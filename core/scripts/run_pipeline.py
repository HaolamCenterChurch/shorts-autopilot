#!/usr/bin/env python3
"""extract_segments -> silence_cut -> track_crop -> (make_ass -> render_vertical
-> verify_output) 전체를 묶는 오케스트레이터. --stage a 로 1~5단계, --stage b 로
자막~검증 단계를 실행한다."""
import argparse
import codecs
import json
import os
import subprocess
import sys

FFMPEG = "/opt/homebrew/bin/ffmpeg"
WHISPER_BIN = "/Users/caleb/Desktop/Whisper/whisper.cpp/build/bin/whisper-cli"
WHISPER_MODEL = "/Users/caleb/Desktop/Whisper/whisper.cpp/models/ggml-large-v3.bin"
YUNET_MODEL = "/Users/caleb/Documents/HaolamShorts/2026-08-30_11-44-30/work/yunet.onnx"

HERE = os.path.dirname(os.path.abspath(__file__))


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def run_module(script_name, args_list):
    cmd = [sys.executable, os.path.join(HERE, script_name)] + args_list
    log(f"[run_pipeline] 실행: {script_name} {' '.join(args_list)}")
    subprocess.run(cmd, check=True)


def extract_wav(video_path, wav_path):
    cmd = [FFMPEG, "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000",
           wav_path]
    subprocess.run(cmd, check=True)


def transcribe_words(video_path, workdir, prefix):
    wav_path = os.path.join(workdir, f"{prefix}.wav")
    extract_wav(video_path, wav_path)
    out_prefix = os.path.join(workdir, prefix)
    cmd = [WHISPER_BIN, "-m", WHISPER_MODEL, "-l", "ko", "-oj", "-ojf",
           "--beam-size", "5", "--temperature", "0", "-f", wav_path,
           "-of", out_prefix]
    log(f"[run_pipeline] whisper 단어 타임스탬프 전사: {prefix}")
    subprocess.run(cmd, check=True)

    # ★whisper.cpp 는 한글 한 글자를 토큰 두 개로 쪼개 반쪽짜리 UTF-8 바이트를 낸다.
    #   errors="ignore" 로 열면 그 반쪽들이 통째로 사라져 전사문에 글자가 빠진다
    #   (2026-09-02 실측: "사춘기" -> "사기", "컴플렉스" -> "스"). 그래서
    #   surrogateescape 로 바이트를 보존한 뒤, 바이트 스트림을 증분 디코딩해
    #   **글자 단위**로 시각을 붙인다. 자막 정렬이 이 스트림 위에서 이뤄진다.
    with open(out_prefix + ".json", "r", encoding="utf-8",
              errors="surrogateescape") as f:
        data = json.load(f)

    byte_times = []  # (바이트, t0, t1)
    for seg in data.get("transcription", []):
        for tok in seg.get("tokens", []):
            text = tok.get("text", "")
            # [_BEG_], [_TT_105] 같은 특수 토큰 전부 제외 (끝이 "_]" 가 아닌 것도 있다)
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
                    words.append({"t0": pending_t0, "t1": pending_t1,
                                  "text": ch})
            pending_t0 = None
            pending_t1 = None
    tail = decoder.decode(b"", True)
    for ch in tail:
        if ch.strip():
            words.append({"t0": pending_t1 or 0.0, "t1": pending_t1 or 0.0,
                          "text": ch})

    words_path = os.path.join(workdir, f"{prefix}.words.json")
    with open(words_path, "w", encoding="utf-8") as f:
        json.dump(words, f, ensure_ascii=False, indent=2)
    return words_path, words


def stage_a(src, plan, workdir, plan_path=None):
    slug = plan["slug"]
    segments_str = ";".join(f"{s}-{e}" for s, e in plan["segments"])

    raw_path = os.path.join(workdir, f"{slug}_raw.mov")
    run_module("extract_segments.py", [
        "--src", src, "--segments", segments_str,
        "--out", raw_path, "--workdir", workdir,
    ])

    raw_words_path, _ = transcribe_words(raw_path, workdir, f"{slug}_raw")

    master_path = os.path.join(workdir, f"{slug}_master.mov")
    run_module("silence_cut.py", [
        "--in", raw_path, "--out", master_path,
        "--words", raw_words_path,
        "--min-silence", "0.30", "--pad", "0.12", "--keep-gap", "0.06",
        "--min-gain", "0.40", "--min-keep", "1.20",
        "--workdir", workdir,
    ])

    master_words_path, _ = transcribe_words(master_path, workdir, f"{slug}_master")

    track_json = os.path.join(workdir, f"{slug}_track.json")
    track_cmd = os.path.join(workdir, f"{slug}_track.sendcmd.txt")
    track_args = [
        "--in", master_path, "--out-cmd", track_cmd, "--out-json", track_json,
        "--crop-w", "1216", "--sample-fps", "10", "--model", YUNET_MODEL,
    ]
    if plan_path:
        track_args.extend(["--plan", plan_path])
    run_module("track_crop.py", track_args)

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


def stage_b(plan, workdir, outdir, chunks_path, state=None):
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
    run_module("make_ass.py", [
        "--words", master_words_path, "--chunks", chunks_path,
        "--out", ass_path, "--duration", str(duration),
    ])

    os.makedirs(outdir, exist_ok=True)
    final_path = os.path.join(outdir, f"{slug}.mp4")
    run_module("render_vertical.py", [
        "--in", master_path, "--track", track_json, "--ass", ass_path,
        "--out", final_path,
    ])

    script_path = os.path.join(workdir, f"{slug}_script.txt")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(plan.get("script", ""))

    report_path = os.path.join(outdir, f"{slug}_verify.md")
    run_module("verify_output.py", [
        "--video", final_path, "--script", script_path,
        "--out", report_path, "--workdir", workdir,
    ])

    log(f"[stage b 완료] 최종본={final_path} 리포트={report_path}")


def main():
    ap = argparse.ArgumentParser(description="쇼츠 파이프라인 오케스트레이터")
    ap.add_argument("--src", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--stage", choices=["a", "b"], default="a")
    ap.add_argument("--chunks", default=None)
    args = ap.parse_args()

    with open(args.plan, "r", encoding="utf-8", errors="ignore") as f:
        plan = json.load(f)

    workdir = os.path.join(args.outdir, "_work")
    os.makedirs(workdir, exist_ok=True)

    if args.stage == "a":
        state = stage_a(args.src, plan, workdir, plan_path=args.plan)
        if args.chunks:
            stage_b(plan, workdir, args.outdir, args.chunks, state=state)
        else:
            print(json.dumps({
                "master_words": state["master_words_path"],
                "duration": state["duration"],
            }, ensure_ascii=False))
    else:
        if not args.chunks:
            raise ValueError("--stage b 에는 --chunks 가 필요하다")
        stage_b(plan, workdir, args.outdir, args.chunks)


if __name__ == "__main__":
    main()
