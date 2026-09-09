#!/usr/bin/env python3
"""클린 마스터 영상(_master.mov)과 트래킹 정보(_track.json)로부터 자막 없는 순수 9:16 썸네일 후보 프레임을 추출."""
import argparse
import json
import os
import subprocess
import sys

FFMPEG = "/opt/homebrew/bin/ffmpeg"


def extract_frames(master_path, track_path, times, outdir, prefix):
    os.makedirs(outdir, exist_ok=True)
    with open(track_path, "r", encoding="utf-8") as f:
        track = json.load(f)

    crop_w = track["crop_w"]
    fps = track.get("fps", 30)
    xs = track["x"]

    results = []
    for t in times:
        frame_idx = min(len(xs) - 1, max(0, int(round(t * fps))))
        crop_x = xs[frame_idx]
        out_name = f"{prefix}_{int(round(t)):02d}s.jpg"
        out_path = os.path.join(outdir, out_name)

        vf = f"crop={crop_w}:2160:{crop_x}:0,scale=1080:1920:flags=lanczos"
        cmd = [
            FFMPEG, "-y",
            "-ss", f"{t:.3f}",
            "-i", master_path,
            "-vf", vf,
            "-frames:v", "1",
            "-q:v", "2",
            out_path
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        print(f"[extract_clean_frames] 클린 프레임 추출 완료: {out_path} (t={t:.2f}s, crop_x={crop_x})")
        results.append(out_path)
    return results


def main():
    parser = argparse.ArgumentParser(description="자막 없는 클린 마스터 9:16 썸네일 프레임 추출기")
    parser.add_argument("--master", required=True, help="클린 마스터 영상 경로 (_master.mov)")
    parser.add_argument("--track", required=True, help="트래킹 JSON 경로 (_track.json)")
    parser.add_argument("--times", nargs="+", type=float, required=True, help="추출할 타임스탬프 초 목록 (예: 10 22 35)")
    parser.add_argument("--outdir", required=True, help="프레임 저장 디렉토리")
    parser.add_argument("--prefix", required=True, help="파일명 접두사 (예: A_frame)")
    args = parser.parse_args()

    extract_frames(args.master, args.track, args.times, args.outdir, args.prefix)


if __name__ == "__main__":
    main()
