#!/usr/bin/env python3
"""클린 마스터 -> 9:16 + 자막 하드번인 최종본을 만든다."""
import argparse
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from app.paths import get_fonts_dirs

FFMPEG = os.environ.get("SHORTS_FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.environ.get("SHORTS_FFPROBE_BIN") or shutil.which("ffprobe") or "ffprobe"
FONTS_DIR = os.environ.get("SHORTS_FONTS_DIR") or (get_fonts_dirs() or [""])[0]


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def main():
    ap = argparse.ArgumentParser(description="9:16 세로 자막 하드번인 렌더")
    ap.add_argument("--in", dest="in_path", required=True)
    ap.add_argument("--track", required=True)
    ap.add_argument("--ass", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-subs", action="store_true")
    args = ap.parse_args()

    with open(args.track, "r", encoding="utf-8") as f:
        track = json.load(f)
    crop_w = track["crop_w"]
    expr = track["expr"]

    # ★반드시 이름 있는 옵션(w=/h=/x=/y=)으로 쓴다. crop=1216:2160:x='..':0 처럼
    #   위치 인자와 이름 인자를 섞으면 ffmpeg 가 "No option name near '0'" 로 죽는다.
    filter_str = (f"crop=w={crop_w}:h=2160:x='{expr}':y=0,"
                  "scale=1080:1920:flags=lanczos")

    ass_abspath = os.path.abspath(args.ass)
    ass_dir = os.path.dirname(ass_abspath)
    ass_name = os.path.basename(ass_abspath)

    cwd = None
    if not args.no_subs:
        filter_str += f",ass='{ass_name}':fontsdir='{FONTS_DIR}'"
        cwd = ass_dir

    in_abspath = os.path.abspath(args.in_path)
    out_abspath = os.path.abspath(args.out)

    cmd = [
        FFMPEG, "-y", "-i", in_abspath, "-vf", filter_str,
        "-c:v", "h264_videotoolbox", "-b:v", "16M", "-maxrate", "20M",
        "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        out_abspath,
    ]
    log(f"[render_vertical] 렌더 중... (자막 {'끔' if args.no_subs else '켬'})")
    subprocess.run(cmd, check=True, cwd=cwd)

    probe_cmd = [
        FFPROBE, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-show_entries", "format=duration",
        "-of", "json", out_abspath,
    ]
    proc = subprocess.run(probe_cmd, check=True, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True)
    info = json.loads(proc.stdout)
    stream = info["streams"][0]
    width, height = stream["width"], stream["height"]
    duration = float(info["format"]["duration"])
    log(f"[완료] {out_abspath} {width}x{height}, {duration:.2f}s")

    if (width, height) != (1080, 1920):
        raise RuntimeError(
            f"출력 해상도가 1080x1920 이 아니다: {width}x{height}")


if __name__ == "__main__":
    main()
