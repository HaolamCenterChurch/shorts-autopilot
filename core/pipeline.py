#!/usr/bin/env python3
"""결정적 파이프라인 오케스트레이션.

core/scripts/ 에 이식된 검증된 실행 스크립트를 subprocess로 호출한다.
- extract_segments.py
- silence_cut.py (무음 제거 - 필수 활성화)
- track_crop.py (인물 추적 9:16)
- make_ass.py (2단 자막 생성)
- render_vertical.py (세로 MP4 렌더링)
- verify_output.py (완성본 재전사 대조 검증)

check_chunks.py(자막-원문 일치 검증)는 AI 자막 생성 직후 orchestrator 단계에서
호출해야 한다 — 아직 연결 전(TODO).
"""
import codecs
import json
import os
import re
import subprocess
import sys
import threading

from app.paths import (
    find_binary,
    get_yunet_model_path,
    get_whisper_model_path,
    get_fonts_dirs,
    augmented_path_str,
)

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")
_PROGRESS_RE = re.compile(r"progress\s*=\s*(\d+(?:\.\d+)?)\s*%")


def _child_env() -> dict:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PATH"] = augmented_path_str()

    ffmpeg_bin = find_binary("ffmpeg")
    if ffmpeg_bin:
        env["SHORTS_FFMPEG_BIN"] = ffmpeg_bin

    ffprobe_bin = find_binary("ffprobe")
    if ffprobe_bin:
        env["SHORTS_FFPROBE_BIN"] = ffprobe_bin

    whisper_bin = (
        find_binary("whisper-cli")
        or find_binary("whisper-cpp")
        or find_binary("whisper")
    )
    if whisper_bin:
        env["SHORTS_WHISPER_BIN"] = whisper_bin

    whisper_model = get_whisper_model_path()
    if whisper_model:
        env["SHORTS_WHISPER_MODEL"] = whisper_model

    fonts_dirs = get_fonts_dirs()
    if fonts_dirs:
        env["SHORTS_FONTS_DIR"] = fonts_dirs[0]
    else:
        env["SHORTS_FONTS_DIR"] = ""

    return env


def log(msg: str):
    print(msg, file=sys.stderr, flush=True)


def run_module(script_name: str, args_list: list[str]):
    cmd = [sys.executable, os.path.join(SCRIPTS_DIR, script_name)] + args_list
    log(f"[pipeline] 실행: {script_name} {' '.join(args_list)}")
    subprocess.run(cmd, check=True, env=_child_env())

def extract_wav(video_path: str, wav_path: str, progress_cb=None, cancel_event: threading.Event | None = None):
    """영상에서 16kHz 모노 WAV 추출."""
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("취소됨")
    ffmpeg_bin = find_binary("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError("ffmpeg 바이너리를 찾을 수 없습니다.")
    cmd = [ffmpeg_bin, "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000", wav_path]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_child_env())
    try:
        while proc.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                raise RuntimeError("취소됨")
            try:
                proc.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass
        if proc.returncode != 0:
            _, stderr_data = proc.communicate()
            raise subprocess.CalledProcessError(proc.returncode, cmd, stderr=stderr_data)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except Exception:
                proc.kill()

def transcribe_words(
    video_path: str,
    workdir: str,
    prefix: str,
    progress_cb=None,
    cancel_event: threading.Event | None = None,
):
    """Whisper를 실행하여 단어/글자 단위 타임스탬프 전사 (surrogateescape 디코딩)."""
    whisper_bin = find_binary("whisper-cli") or find_binary("whisper") or find_binary("whisper-cpp")
    whisper_model = get_whisper_model_path()
    
    if not whisper_bin or not whisper_model:
        raise RuntimeError(f"Whisper 실행기({whisper_bin}) 또는 모델({whisper_model})이 준비되지 않았습니다.")

    wav_path = os.path.join(workdir, f"{prefix}.wav")
    extract_wav(video_path, wav_path, progress_cb=progress_cb, cancel_event=cancel_event)
    out_prefix = os.path.join(workdir, prefix)

    cmd = [
        whisper_bin, "-m", whisper_model, "-l", "ko", "-oj", "-ojf",
        "--beam-size", "5", "--temperature", "0", "-pp", "-f", wav_path,
        "-of", out_prefix
    ]

    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("취소됨")

    proc = subprocess.Popen(
        cmd,
        env=_child_env(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                raise RuntimeError("취소됨")

            line = proc.stderr.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue

            m = _PROGRESS_RE.search(line)
            if m and progress_cb:
                try:
                    progress_cb(float(m.group(1)))
                except Exception:
                    pass

        return_code = proc.wait()
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("취소됨")
        if return_code != 0:
            raise subprocess.CalledProcessError(return_code, cmd)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except Exception:
                proc.kill()

    json_file = out_prefix + ".json"
    if not os.path.exists(json_file):
        raise FileNotFoundError(f"Whisper 전사 출력 파일이 없습니다: {json_file}")

    with open(json_file, "r", encoding="utf-8", errors="surrogateescape") as f:
        data = json.load(f)

    byte_times = []
    for seg in data.get("transcription", []):
        for tok in seg.get("tokens", []):
            text = tok.get("text", "")
            if text.startswith("[_") and text.endswith("]"):
                continue
            offsets = tok.get("offsets", {})
            t0 = offsets.get("from", 0) / 1000.0
            t1 = offsets.get("to", 0) / 1000.0
            for b in text.encode("utf-8", "surrogateescape"):
                byte_times.append((b, t0, t1))

    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    words = []
    pending_t0 = None
    pending_t1 = None
    for b, t0, t1 in byte_times:
        if pending_t0 is None:
            pending_t0 = t0
        pending_t1 = t1
        piece = decoder.decode(bytes([b]))
        if piece:
            for ch in piece:
                if ch.strip():
                    words.append({"t0": pending_t0, "t1": pending_t1, "text": ch})
            pending_t0 = None
            pending_t1 = None
    tail = decoder.decode(b"", True)
    for ch in tail:
        if ch.strip():
            words.append({"t0": pending_t1 or 0.0, "t1": pending_t1 or 0.0, "text": ch})

    words_path = os.path.join(workdir, f"{prefix}.words.json")
    with open(words_path, "w", encoding="utf-8") as f:
        json.dump(words, f, ensure_ascii=False, indent=2)
    return words_path, words

def transcribe_video_full(
    video_path: str,
    workdir: str,
    force: bool = False,
    progress_cb=None,
    cancel_event: threading.Event | None = None,
) -> dict:
    """전체 원본 영상 1차 전사 (캐시 확인)."""
    os.makedirs(workdir, exist_ok=True)
    base = os.path.splitext(os.path.basename(video_path))[0]
    out_prefix = os.path.join(workdir, f"{base}_full")
    json_path = out_prefix + ".json"
    words_path = out_prefix + ".words.json"
    wav_path = out_prefix + ".wav"

    cached = os.path.exists(json_path) and os.path.exists(words_path) and not force
    if not cached:
        words_path, words = transcribe_words(
            video_path,
            workdir,
            f"{base}_full",
            progress_cb=progress_cb,
            cancel_event=cancel_event,
        )
    else:
        if progress_cb:
            try:
                progress_cb(100.0)
            except Exception:
                pass
        with open(words_path, "r", encoding="utf-8", errors="ignore") as f:
            words = json.load(f)

    # 전사문 조합
    full_text = "".join(w.get("text", "") for w in words)
    return {
        "json": json_path,
        "wav": wav_path,
        "words": words,
        "text": full_text,
        "cached": cached
    }

# =========================================================================
# 스크립트 실행 함수군 (scripts/shorts_v2/ 모듈 연동)
# =========================================================================

def run_extract_segments(src: str, segments_str: str, out_path: str, workdir: str):
    run_module("extract_segments.py", [
        "--src", src, "--segments", segments_str,
        "--out", out_path, "--workdir", workdir,
    ])


def run_silence_cut(raw_path: str, master_path: str, words_path: str, workdir: str):
    run_module("silence_cut.py", [
        "--in", raw_path, "--out", master_path,
        "--words", words_path,
        "--min-silence", "0.30", "--pad", "0.14", "--keep-gap", "0.14",
        "--workdir", workdir,
    ])


def run_track_crop(master_path: str, track_cmd: str, track_json: str, yunet_model: str):
    run_module("track_crop.py", [
        "--in", master_path, "--out-cmd", track_cmd, "--out-json", track_json,
        "--crop-w", "1216", "--sample-fps", "10", "--model", yunet_model,
    ])


def run_make_ass(master_words_path: str, chunks_path: str, ass_path: str, duration: float):
    run_module("make_ass.py", [
        "--words", master_words_path, "--chunks", chunks_path,
        "--out", ass_path, "--duration", str(duration),
    ])


def run_render_vertical(master_path: str, track_json: str, ass_path: str, final_path: str):
    run_module("render_vertical.py", [
        "--in", master_path, "--track", track_json, "--ass", ass_path,
        "--out", final_path,
    ])


def run_verify_output(final_path: str, script_path: str, report_path: str, workdir: str):
    run_module("verify_output.py", [
        "--video", final_path, "--script", script_path,
        "--out", report_path, "--workdir", workdir,
    ])


def run_stage_a(src: str, plan: dict, workdir: str) -> dict:
    slug = plan["slug"]
    segments_str = ";".join(f"{s}-{e}" for s, e in plan["segments"])

    raw_path = os.path.join(workdir, f"{slug}_raw.mov")
    run_extract_segments(src, segments_str, raw_path, workdir)

    raw_words_path, _ = transcribe_words(raw_path, workdir, f"{slug}_raw")

    master_path = os.path.join(workdir, f"{slug}_master.mov")
    run_silence_cut(raw_path, master_path, raw_words_path, workdir)

    master_words_path, _ = transcribe_words(master_path, workdir, f"{slug}_master")

    yunet_model = get_yunet_model_path()
    if not yunet_model:
        raise RuntimeError("YuNet 모델 파일을 찾을 수 없습니다.")

    track_json = os.path.join(workdir, f"{slug}_track.json")
    track_cmd = os.path.join(workdir, f"{slug}_track.sendcmd.txt")
    run_track_crop(master_path, track_cmd, track_json, yunet_model)

    with open(master_path + ".cuts.json", "r", encoding="utf-8") as f:
        cuts = json.load(f)
    duration = cuts["duration"]

    log(f"[stage a 완료] master_words={master_words_path} duration={duration:.3f}s")
    return {
        "master_path": master_path,
        "master_words_path": master_words_path,
        "track_json": track_json,
        "duration": duration,
    }


def run_stage_b(plan: dict, workdir: str, outdir: str, chunks_path: str, state: dict | None = None):
    slug = plan["slug"]
    master_path = os.path.join(workdir, f"{slug}_master.mov")
    master_words_path = os.path.join(workdir, f"{slug}_master.words.json")
    track_json = os.path.join(workdir, f"{slug}_track.json")

    if state:
        master_path = state["master_path"]
        master_words_path = state["master_words_path"]
        track_json = state["track_json"]
        duration = state["duration"]
    else:
        with open(master_path + ".cuts.json", "r", encoding="utf-8") as f:
            duration = json.load(f)["duration"]

    ass_path = os.path.join(workdir, f"{slug}.ass")
    run_make_ass(master_words_path, chunks_path, ass_path, duration)

    os.makedirs(outdir, exist_ok=True)
    final_path = os.path.join(outdir, f"{slug}.mp4")
    run_render_vertical(master_path, track_json, ass_path, final_path)

    script_path = os.path.join(workdir, f"{slug}_script.txt")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(plan.get("script", ""))

    report_path = os.path.join(outdir, f"{slug}_verify.md")
    run_verify_output(final_path, script_path, report_path, workdir)

    log(f"[stage b 완료] 최종본={final_path} 리포트={report_path}")
