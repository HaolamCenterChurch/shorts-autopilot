#!/usr/bin/env python3
"""플랜의 구간들을 원본 4K 영상에서 뽑아 이어붙인 중간본을 만든다."""
import argparse
import json
import os
import shutil
import subprocess
import sys

FFMPEG = os.environ.get("SHORTS_FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
FPS = 30

ENCODE_ARGS = [
    "-c:v", "h264_videotoolbox", "-b:v", "60M", "-pix_fmt", "yuv420p",
    "-r", str(FPS), "-vsync", "cfr",
    "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "1",
]


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def snap_dur(sec: float) -> float:
    """30fps 프레임 격자에 스냅한 길이(초)."""
    frames = round(sec * FPS)
    return frames / FPS


def parse_segments(spec: str):
    segs = []
    for part in spec.split(";"):
        part = part.strip()
        if not part:
            continue
        in_s, out_s = part.split("-")
        segs.append((float(in_s), float(out_s)))
    return segs


def extract_one(src, start, dur, out_path):
    """빠른 seek(2초 앞) 후 정확 seek 로 한 구간을 뽑는다."""
    afade_out_st = max(0.0, dur - 0.005)
    # ★asetpts 로 타임스탬프를 0 으로 리셋한 뒤 afade 를 건다.
    #   (2026-09-02 실측: 출력쪽 -ss 를 쓰면 PTS 가 0 에서 시작하지 않아
    #    afade=t=out 이 클립 한가운데서 터져 소리가 33dB 죽었다.)
    af = ("asetpts=PTS-STARTPTS,"
          f"afade=t=in:st=0:d=0.005,afade=t=out:st={afade_out_st:.4f}:d=0.005")
    # 입력쪽 -ss 만 쓴다 — ffmpeg 는 키프레임까지 seek 후 디코드해서 정확히 맞춘다.
    cmd = [FFMPEG, "-y", "-ss", f"{start:.4f}", "-i", src, "-t", f"{dur:.4f}"]
    cmd += ENCODE_ARGS + ["-af", af, out_path]
    log(f"[extract] {os.path.basename(out_path)}: start={start:.3f} dur={dur:.3f}")
    subprocess.run(cmd, check=True)


def concat_reencode(part_paths, out_path, workdir):
    list_path = os.path.join(workdir, "_concat_list.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for p in part_paths:
            escaped = p.replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")
    cmd = [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", list_path]
    cmd += ENCODE_ARGS + [out_path]
    log(f"[concat] {len(part_paths)}개 구간 -> {out_path}")
    subprocess.run(cmd, check=True)


def main():
    ap = argparse.ArgumentParser(description="구간 추출 후 이어붙이기")
    ap.add_argument("--src", required=True)
    ap.add_argument("--segments", required=True, help="in-out;in-out;...")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workdir", required=True)
    args = ap.parse_args()

    os.makedirs(args.workdir, exist_ok=True)
    segs = parse_segments(args.segments)
    if not segs:
        raise ValueError("segments 가 비어 있다")

    part_paths = []
    segmap = []
    dst_cursor = 0.0
    for i, (s_in, s_out) in enumerate(segs):
        dur = snap_dur(s_out - s_in)
        part_path = os.path.join(args.workdir, f"seg_{i:03d}.mov")
        extract_one(args.src, s_in, dur, part_path)
        part_paths.append(part_path)
        dst_start = dst_cursor
        dst_end = dst_cursor + dur
        segmap.append({
            "src_start": s_in, "src_end": s_out,
            "dst_start": dst_start, "dst_end": dst_end,
        })
        dst_cursor = dst_end

    concat_reencode(part_paths, args.out, args.workdir)

    segmap_path = args.out + ".segmap.json"
    with open(segmap_path, "w", encoding="utf-8") as f:
        json.dump({"segments": segmap, "duration": dst_cursor}, f,
                   ensure_ascii=False, indent=2)
    log(f"[완료] {args.out} (길이 {dst_cursor:.3f}s) / segmap: {segmap_path}")


if __name__ == "__main__":
    main()
