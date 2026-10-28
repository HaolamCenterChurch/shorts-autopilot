#!/usr/bin/env python3
"""클린 마스터 -> 9:16 + 자막 + 줌인 + 스티커 + SFX 하드번인 최종본 렌더."""
import argparse
import json
import os
import subprocess
import sys
from env_discovery import get_ffmpeg, get_ffprobe, get_fonts_dirs, get_stickers_dir

FFMPEG = get_ffmpeg()
FFPROBE = get_ffprobe()
_fonts_dirs = get_fonts_dirs()
FONTS_DIR = _fonts_dirs[0] if _fonts_dirs else ""
STICKERS_DIR = get_stickers_dir()


def get_video_encoder():
    """OS 및 FFmpeg 빌드에 따른 최적의 H.264 인코더 자동 선택."""
    try:
        res = subprocess.run([FFMPEG, "-encoders"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if "h264_videotoolbox" in res.stdout and sys.platform == "darwin":
            return "h264_videotoolbox", ["-b:v", "16M", "-maxrate", "20M"]
        elif "h264_nvenc" in res.stdout:
            return "h264_nvenc", ["-cq", "19", "-preset", "p4"]
    except Exception:
        pass
    return "libx264", ["-crf", "18", "-preset", "fast"]


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def main():
    ap = argparse.ArgumentParser(description="9:16 세로 자막 + 줌인 + 스티커 + SFX 하드번인 렌더")
    ap.add_argument("--in", dest="in_path", required=True)
    ap.add_argument("--track", required=True)
    ap.add_argument("--ass", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sfx", default=None, help="합성된 SFX 오디오 파일 경로")
    ap.add_argument("--effects", default=None, help="줌인 및 스티커 설정 JSON 파일")
    ap.add_argument("--no-subs", action="store_true")
    ap.add_argument("--band-top", type=int, default=0,
                    help="상단에 만들 검정 밴드 높이 px. 0 이면 기존처럼 9:16 꽉 채운다")
    ap.add_argument("--zoom-out", type=float, default=1.0,
                    help="크롭 폭을 이 배율로 넓혀 화면을 줌아웃한다 (1.0 = 그대로)")
    ap.add_argument("--zoom-anchor-y", type=float, default=0.5,
                    help="줌인 세로 기준점 (0=위 끝 고정, 0.5=가운데). 헤드룸을 줄였으면 0 — 가운데면 정수리가 잘린다 (2026-09-30)")
    ap.add_argument("--band-bottom", type=int, default=0,
                    help="하단에 만들 검정 밴드 높이 px (자막을 여기에 내릴 때)")
    ap.add_argument("--band-crop-y", type=int, default=None,
                    help="밴드 사용 시 원본에서 세로 크롭을 시작할 y (기본: 중앙보다 위)")
    args = ap.parse_args()

    with open(args.track, "r", encoding="utf-8") as f:
        track = json.load(f)
    crop_w = track["crop_w"]
    expr = track["expr"]

    ass_abspath = os.path.abspath(args.ass)
    ass_dir = os.path.dirname(ass_abspath)
    ass_name = os.path.basename(ass_abspath)
    in_abspath = os.path.abspath(args.in_path)
    out_abspath = os.path.abspath(args.out)

    # 이펙트(줌인 및 스티커) 로드
    effects_data = {}
    if args.effects and os.path.exists(args.effects):
        with open(args.effects, "r", encoding="utf-8") as f:
            effects_data = json.load(f)
    else:
        auto_fx = os.path.splitext(ass_abspath)[0] + "_effects.json"
        if os.path.exists(auto_fx):
            with open(auto_fx, "r", encoding="utf-8") as f:
                effects_data = json.load(f)

    zoom_times = effects_data.get("zooms", [])
    stickers = effects_data.get("stickers", [])

    # FFmpeg 입력 구성
    inputs = ["-i", in_abspath]
    input_idx = 1

    sfx_idx = None
    if args.sfx and os.path.exists(args.sfx):
        inputs.extend(["-i", os.path.abspath(args.sfx)])
        sfx_idx = input_idx
        input_idx += 1
    else:
        auto_sfx = os.path.splitext(ass_abspath)[0] + "_sfx.wav"
        if os.path.exists(auto_sfx):
            inputs.extend(["-i", auto_sfx])
            sfx_idx = input_idx
            input_idx += 1

    sticker_inputs = []
    for st in stickers:
        st_name = st.get("name")
        st_path = st.get("path") or (os.path.join(STICKERS_DIR, f"{st_name}.png") if st_name else None)
        if st_path and os.path.exists(st_path):
            inputs.extend(["-i", os.path.abspath(st_path)])
            sticker_inputs.append((input_idx, st))
            input_idx += 1
        else:
            log(f"[경고] 스티커 파일을 찾을 수 없음: {st_path}")

    # Filter Complex 구성
    filter_chains = []

    # 1. 크롭 및 1080x1920 스케일
    if args.zoom_out and args.zoom_out != 1.0:
        # 크롭 폭을 넓혀 더 넓은 화각을 담는다. 중심을 유지하도록 x 를 절반만큼 당기고
        # 프레임 밖으로 나가지 않게 표현식 안에서 clip 한다.
        new_w = int(round(crop_w * args.zoom_out / 2.0) * 2)
        new_w = min(new_w, 3840)
        off = (new_w - crop_w) / 2.0
        expr = f"2*trunc(max(0\\,min({3840 - new_w}\\,({expr})-{off:.1f}))/2)"
        crop_w = new_w

    if args.band_top > 0:
        # 상단 밴드: 영상 영역을 1080x(1920-band) 로 만들고 그 아래로 내린다.
        # 원본에서 세로를 덜 잡아 크롭하므로 인물이 더 크게 잡힌다(레터박스 아님).
        vid_h = 1920 - args.band_top - args.band_bottom
        crop_h = int(round(crop_w * vid_h / 1080.0))
        crop_h = min(crop_h, 2160)
        cy = args.band_crop_y if args.band_crop_y is not None else max(0, (2160 - crop_h) // 3)
        cy = max(0, min(cy, 2160 - crop_h))
        filter_chains.append(
            f"[0:v]crop=w={crop_w}:h={crop_h}:x='{expr}':y={cy},"
            f"scale=1080:{vid_h}:flags=lanczos[v_crop]")
        cur_v = "v_crop"

        # 2. 줌인 (Punch-in Zoom: 중앙 영상 영역만 1.14배 확대, 블랙 밴드는 고정)
        if zoom_times:
            # [[t0,t1]] 또는 [[t0,t1,배율]] (zoom_plan.py). 경계를 프레임 중간값에 맞춰
            # 이음매 프레임에서 정확히 바뀌게 한다 — 한 프레임 어긋나면 그 자체가 튀어 보인다 (2026-09-15)
            terms = [f"{(z[2] if len(z) > 2 else 1.14) - 1:.3f}"
                     f"*gte(it,{(round(z[0] * 30) - 0.5) / 30:.4f})"
                     f"*lt(it,{(round(z[1] * 30) - 0.5) / 30:.4f})" for z in zoom_times]
            zoom_expr = f"min(1.3,1+{'+'.join(terms)})"
            filter_chains.append(
                f"[{cur_v}]zoompan=z='{zoom_expr}':d=1:x='iw/2-(iw/zoom/2)':y='(ih-ih/zoom)*{args.zoom_anchor_y:.3f}':s=1080x{vid_h}:fps=30[v_zoomed]"
            )
            cur_v = "v_zoomed"

        # 상하단 밴드 패딩
        filter_chains.append(f"[{cur_v}]pad=1080:1920:0:{args.band_top}:black[v_base]")
        cur_v = "v_base"
    else:
        filter_chains.append(f"[0:v]crop=w={crop_w}:h=2160:x='{expr}':y=0,scale=1080:1920:flags=lanczos[v_base]")
        cur_v = "v_base"
        if zoom_times:
            # [[t0,t1]] 또는 [[t0,t1,배율]] (zoom_plan.py). 경계를 프레임 중간값에 맞춰
            # 이음매 프레임에서 정확히 바뀌게 한다 — 한 프레임 어긋나면 그 자체가 튀어 보인다 (2026-09-15)
            terms = [f"{(z[2] if len(z) > 2 else 1.14) - 1:.3f}"
                     f"*gte(it,{(round(z[0] * 30) - 0.5) / 30:.4f})"
                     f"*lt(it,{(round(z[1] * 30) - 0.5) / 30:.4f})" for z in zoom_times]
            zoom_expr = f"min(1.3,1+{'+'.join(terms)})"
            filter_chains.append(
                f"[{cur_v}]zoompan=z='{zoom_expr}':d=1:x='iw/2-(iw/zoom/2)':y='(ih-ih/zoom)*{args.zoom_anchor_y:.3f}':s=1080x1920:fps=30[v_zoomed]"
            )
            cur_v = "v_zoomed"

    # 3. 스티커 오버레이
    for i, (s_idx, s_info) in enumerate(sticker_inputs):
        t0 = s_info["t0"]
        t1 = s_info["t1"]
        x_expr = s_info.get("x", "(W-w)/2")
        y_expr = s_info.get("y", 1260)
        next_v = f"v_stk_{i}"
        filter_chains.append(
            f"[{cur_v}][{s_idx}:v]overlay=x={x_expr}:y={y_expr}:enable='between(t,{t0:.2f},{t1:.2f})'[{next_v}]"
        )
        cur_v = next_v

    # 4. ASS 자막 하드번인
    if not args.no_subs:
        filter_chains.append(f"[{cur_v}]ass='{ass_name}':fontsdir='{FONTS_DIR}'[v_out]")
    else:
        filter_chains.append(f"[{cur_v}]null[v_out]")

    # 5. 오디오 믹싱 (대사 100% + 감동 앰비언트 SFX 45%로 포근하고 자연스럽게 배합)
    if sfx_idx is not None:
        filter_chains.append(
            f"[0:a][{sfx_idx}:a]amix=inputs=2:duration=first:dropout_transition=0:weights=1.0 0.45[a_out]"
        )
        audio_map = ["[a_out]"]
    else:
        audio_map = ["0:a"]

    filter_complex_str = ";".join(filter_chains)

    enc_name, enc_opts = get_video_encoder()
    cmd = [
        FFMPEG, "-y", *inputs,
        "-filter_complex", filter_complex_str,
        "-map", "[v_out]", "-map", *audio_map,
        "-c:v", enc_name, *enc_opts,
        "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        out_abspath,
    ]
    log(f"[render_vertical] 렌더 중... (줌인 {len(zoom_times)}곳, 스티커 {len(sticker_inputs)}개, SFX {'켬' if sfx_idx else '끔'})")
    subprocess.run(cmd, check=True, cwd=ass_dir)

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


if __name__ == "__main__":
    main()
