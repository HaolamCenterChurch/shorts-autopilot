#!/usr/bin/env python3
"""최종본 소리를 다시 받아적어 대본과 맞는지 검증한다."""
import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from app.paths import get_whisper_model_path

FFMPEG = os.environ.get("SHORTS_FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
WHISPER_BIN = (os.environ.get("SHORTS_WHISPER_BIN") or shutil.which("whisper-cli")
               or shutil.which("whisper-cpp") or shutil.which("whisper") or "whisper-cli")
WHISPER_MODEL = os.environ.get("SHORTS_WHISPER_MODEL") or get_whisper_model_path() or ""


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def normalize(text):
    return re.sub(r"[^0-9A-Za-z가-힣]", "", text or "")


def extract_wav(video_path, workdir):
    wav_path = os.path.join(workdir, "_verify.wav")
    cmd = [FFMPEG, "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000",
           wav_path]
    log("[verify_output] wav 추출 중...")
    subprocess.run(cmd, check=True)
    return wav_path


def run_whisper(wav_path, workdir):
    prefix = os.path.join(workdir, "_verify")
    cmd = [WHISPER_BIN, "-m", WHISPER_MODEL, "-l", "ko", "-oj", "-ojf",
           "--beam-size", "5", "--temperature", "0", "-f", wav_path,
           "-of", prefix]
    log("[verify_output] whisper 전사 중...")
    subprocess.run(cmd, check=True)
    json_path = prefix + ".json"
    with open(json_path, "r", encoding="utf-8", errors="ignore") as f:
        data = json.load(f)
    segments = data.get("transcription", [])
    text = "".join(seg.get("text", "") for seg in segments)
    return text.strip()


def diff_report(a_norm, b_norm, a_label, b_label, context=20):
    sm = difflib.SequenceMatcher(None, a_norm, b_norm)
    lines = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        a_ctx_start = max(0, i1 - context)
        a_ctx_end = min(len(a_norm), i2 + context)
        b_ctx_start = max(0, j1 - context)
        b_ctx_end = min(len(b_norm), j2 + context)
        lines.append(
            f"- **{tag}** {a_label}[{i1}:{i2}]=`{a_norm[i1:i2]}` "
            f"{b_label}[{j1}:{j2}]=`{b_norm[j1:j2]}`\n"
            f"  - 문맥({a_label}): ...{a_norm[a_ctx_start:a_ctx_end]}...\n"
            f"  - 문맥({b_label}): ...{b_norm[b_ctx_start:b_ctx_end]}..."
        )
    return lines


def main():
    ap = argparse.ArgumentParser(description="최종본 대본 일치 검증")
    ap.add_argument("--video", required=True)
    ap.add_argument("--script", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workdir", required=True)
    args = ap.parse_args()

    os.makedirs(args.workdir, exist_ok=True)

    with open(args.script, "r", encoding="utf-8", errors="ignore") as f:
        script_text = f.read().strip()

    wav_path = extract_wav(args.video, args.workdir)
    transcript = run_whisper(wav_path, args.workdir)

    norm_transcript = normalize(transcript)
    norm_script = normalize(script_text)

    ratio = difflib.SequenceMatcher(None, norm_transcript, norm_script).ratio()
    log(f"[verify_output] 유사도 {ratio*100:.2f}%")

    diff_lines = diff_report(norm_transcript, norm_script, "전사", "대본")

    report = [
        "# 검증 리포트\n",
        f"\n유사도: **{ratio*100:.2f}%**\n",
        "\n## 전사문 전문\n",
        f"\n{transcript}\n",
        "\n## 대본 전문\n",
        f"\n{script_text}\n",
        "\n## 차이 나는 구간\n",
        "\n" + ("\n".join(diff_lines) if diff_lines else "(차이 없음)") + "\n",
    ]
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("".join(report))

    log(f"[완료] 리포트: {args.out}")

    if ratio < 0.90:
        log(f"[실패] 유사도 {ratio*100:.2f}% < 90%")
        sys.exit(1)


if __name__ == "__main__":
    main()
