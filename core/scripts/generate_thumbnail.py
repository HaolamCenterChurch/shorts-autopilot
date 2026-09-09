#!/usr/bin/env python3
"""쇼츠 유튜브(9:16) 및 인스타그램(4:5) 썸네일 통합 자동 생성기.

1. 유튜브 쇼츠 (1080x1920):
   - 상단 딤 그라디언트 + 카테고리 뱃지 + 고대비 볼드 2단 훅 타이틀 + 서브 티저 박스
2. 인스타그램 릴스/피드 커버 (1080x1350):
   - 캔바 템플릿 카드(870x1158) 프레임 + 노란 가이드(760x981) 내 안전 영역 타이포그래피
   - 클린본(실제 업로드용) 및 가이드본(안전영역 검토용) 동시 출력
"""
import argparse
import os
import sys
from PIL import Image, ImageDraw, ImageFont

# 폰트 탐색 (어그로체 우선, 시스템 산세리프 폴백)
def get_fonts():
    aggro_b_candidates = [
        "/Users/caleb/Library/Fonts/SB 어그로OTF B.otf",
        "/Library/Fonts/SB 어그로OTF B.otf",
        os.path.expanduser("~/Library/Fonts/SB 어그로OTF B.otf"),
    ]
    aggro_m_candidates = [
        "/Users/caleb/Library/Fonts/SB 어그로OTF M.otf",
        "/Library/Fonts/SB 어그로OTF M.otf",
        os.path.expanduser("~/Library/Fonts/SB 어그로OTF M.otf"),
    ]
    aggro_l_candidates = [
        "/Users/caleb/Library/Fonts/SB 어그로OTF L.otf",
        "/Library/Fonts/SB 어그로OTF L.otf",
        os.path.expanduser("~/Library/Fonts/SB 어그로OTF L.otf"),
    ]
    sys_sans = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
    
    fb = next((p for p in aggro_b_candidates if os.path.exists(p)), sys_sans)
    fm = next((p for p in aggro_m_candidates if os.path.exists(p)), sys_sans)
    fl = next((p for p in aggro_l_candidates if os.path.exists(p)), sys_sans)
    return fb, fm, fl, sys_sans

FONT_B, FONT_M, FONT_L, FONT_SANS = get_fonts()

def draw_text_with_shadow(draw, xy, text, font, fill_color, shadow_color=(0, 0, 0, 240), shadow_offset=4):
    x, y = xy
    for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2), (-2, 2), (2, 2), (0, shadow_offset), (0, shadow_offset + 2)]:
        draw.text((x + dx, y + dy), text, font=font, fill=shadow_color)
    draw.text((x, y), text, font=font, fill=fill_color)

def get_fitted_font(font_path, text, target_size, max_width=940, min_size=52):
    dummy_draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    size = target_size
    while size > min_size:
        font = ImageFont.truetype(font_path, size)
        box = dummy_draw.textbbox((0, 0), text, font=font)
        w = box[2] - box[0]
        if w <= max_width:
            return font
        size -= 2
    return ImageFont.truetype(font_path, min_size)

def make_youtube_thumbnail(bg_image_path, badge_text, title_line1, title_line2, teaser_text, out_path, zoom=1.0, fs_l1=0, fs_l2=0):
    im = Image.open(bg_image_path).convert("RGBA")
    if im.size != (1080, 1920):
        im = im.resize((1080, 1920), Image.LANCZOS)

    if zoom < 0.999:
        w, h = im.size
        nw, nh = int(w * zoom), int(h * zoom)
        scaled = im.resize((nw, nh), Image.LANCZOS)
        bg_color = im.getpixel((w // 2, 50))
        canvas = Image.new("RGBA", (w, h), bg_color)
        canvas.paste(scaled, ((w - nw) // 2, h - nh))
        im = canvas

    # 상/하단 그라디언트
    grad = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    grad_draw = ImageDraw.Draw(grad)
    for y in range(800):
        alpha = int(230 * ((800 - y) / 800) ** 1.3)
        grad_draw.line([(0, y), (1080, y)], fill=(0, 0, 0, alpha))
    for y in range(1600, 1920):
        alpha = int(140 * ((y - 1600) / 320) ** 1.4)
        grad_draw.line([(0, y), (1080, y)], fill=(0, 0, 0, alpha))

    im = Image.alpha_composite(im, grad)
    draw = ImageDraw.Draw(im)

    has_l2 = bool(title_line2 and title_line2.strip())
    has_sub = bool(teaser_text and teaser_text.strip())

    font_badge = ImageFont.truetype(FONT_B, 34)

    # 뱃지 Y 좌표: 2줄 확대 모드 시 Y=240, 기본 Y=270
    by0 = 240 if (has_l2 and not has_sub) else 260 if not has_l2 else 270

    if badge_text:
        b_bbox = draw.textbbox((0, 0), badge_text, font=font_badge)
        bw, bh = b_bbox[2] - b_bbox[0], b_bbox[3] - b_bbox[1]
        bx0 = (1080 - bw) // 2 - 26
        draw.rounded_rectangle([bx0, by0, bx0 + bw + 52, by0 + bh + 22], radius=16, fill=(15, 15, 20, 220), outline=(255, 232, 67, 240), width=2)
        draw.text((bx0 + 26, by0 + 9), badge_text, font=font_badge, fill=(255, 232, 67, 255))

    if not has_l2:
        # 단일행 훅 극대화 모드
        target_size = fs_l1 if fs_l1 > 0 else 104
        font_l1 = get_fitted_font(FONT_B, title_line1, target_size=target_size, max_width=960, min_size=50)
        l1_box = draw.textbbox((0, 0), title_line1, font=font_l1)
        l1_w = l1_box[2] - l1_box[0]
        y_l1 = 400
        draw_text_with_shadow(draw, ((1080 - l1_w) // 2, y_l1), title_line1, font=font_l1, fill_color=(255, 232, 67, 255), shadow_offset=7)
    else:
        # 2단 훅 모드 (서브 티저 유무에 따라 글자 크기 최적화)
        if not has_sub:
            # 티저 없는 2단 대형 훅
            target_l1 = fs_l1 if fs_l1 > 0 else 118
            target_l2 = fs_l2 if fs_l2 > 0 else 140
            font_l1 = get_fitted_font(FONT_B, title_line1, target_size=target_l1, max_width=940, min_size=54)
            font_l2 = get_fitted_font(FONT_B, title_line2, target_size=target_l2, max_width=940, min_size=60)
            y_l1 = 335
            y_l2 = 475
        else:
            # 티저 포함 표준 2단 훅
            target_l1 = fs_l1 if fs_l1 > 0 else 82
            target_l2 = fs_l2 if fs_l2 > 0 else 96
            font_l1 = get_fitted_font(FONT_B, title_line1, target_size=target_l1, max_width=940, min_size=54)
            font_l2 = get_fitted_font(FONT_B, title_line2, target_size=target_l2, max_width=940, min_size=60)
            y_l1 = 365
            y_l2 = 475

        l1_box = draw.textbbox((0, 0), title_line1, font=font_l1)
        l1_w = l1_box[2] - l1_box[0]
        draw_text_with_shadow(draw, ((1080 - l1_w) // 2, y_l1), title_line1, font=font_l1, fill_color=(255, 255, 255, 255), shadow_offset=7)

        l2_box = draw.textbbox((0, 0), title_line2, font=font_l2)
        l2_w = l2_box[2] - l2_box[0]
        draw_text_with_shadow(draw, ((1080 - l2_w) // 2, y_l2), title_line2, font=font_l2, fill_color=(255, 232, 67, 255), shadow_offset=7)

    # 서브 티저 박스
    if has_sub:
        font_sub = get_fitted_font(FONT_SANS, teaser_text, target_size=38, max_width=940, min_size=26)
        s_bbox = draw.textbbox((0, 0), teaser_text, font=font_sub)
        sw, sh = s_bbox[2] - s_bbox[0], s_bbox[3] - s_bbox[1]
        sx0, sy0 = (1080 - sw) // 2 - 24, 605
        draw.rounded_rectangle([sx0, sy0, sx0 + sw + 48, sy0 + sh + 18], radius=12, fill=(0, 0, 0, 180))
        draw.text(((1080 - sw) // 2, 612), teaser_text, font=font_sub, fill=(235, 240, 250, 255))

    im.convert("RGB").save(out_path, "JPEG", quality=96)
    print(f"[generate_thumbnail] 유튜브 썸네일 저장: {out_path}")

def make_instagram_thumbnail(bg_image_path, badge_text, title_line1, title_line2, teaser_text, out_clean_path, out_guide_path=None, zoom=1.0, fs_l1=0, fs_l2=0):
    canvas = Image.new("RGB", (1080, 1350), (255, 255, 255))
    pw, ph = 870, 1158
    px, py = 105, 42
    gx0, gy0, gx1, gy1 = 161, 164, 921, 1145 # 노란 가이드 규격 (760 x 981)

    frame = Image.open(bg_image_path).convert("RGB")
    if zoom < 0.999:
        fw, fh = frame.size
        nw, nh = int(fw * zoom), int(fh * zoom)
        scaled_f = frame.resize((nw, nh), Image.LANCZOS)
        bg_col = frame.getpixel((fw // 2, 50))
        new_frame = Image.new("RGB", (fw, fh), bg_col)
        new_frame.paste(scaled_f, ((fw - nw) // 2, fh - nh))
        frame = new_frame

    fw, fh = frame.size
    crop_w = int(fh * (pw / ph))
    off_x = (fw - crop_w) // 2
    rf_crop = frame.crop((off_x, 0, off_x + crop_w, fh)).resize((pw, ph), Image.LANCZOS)

    # 상단 딤 그라디언트
    grad = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(grad)
    for y in range(500):
        alpha = int(220 * ((500 - y) / 500) ** 1.3)
        gdraw.line([(0, y), (pw, y)], fill=(0, 0, 0, alpha))
    photo_img = Image.alpha_composite(rf_crop.convert("RGBA"), grad).convert("RGB")
    canvas.paste(photo_img, (px, py))

    draw = ImageDraw.Draw(canvas)
    has_l2 = bool(title_line2 and title_line2.strip())
    has_sub = bool(teaser_text and teaser_text.strip())

    font_badge = ImageFont.truetype(FONT_B, 26)
    cx = 540

    if badge_text:
        b_box = draw.textbbox((0, 0), badge_text, font=font_badge)
        bw, bh = b_box[2] - b_box[0], b_box[3] - b_box[1]
        bx0, by0 = cx - bw // 2 - 16, gy0 + 20
        draw.rounded_rectangle([bx0, by0, bx0 + bw + 32, by0 + bh + 14], radius=10, fill=(15, 15, 20, 220), outline=(255, 232, 67, 240), width=2)
        draw.text((bx0 + 16, by0 + 6), badge_text, font=font_badge, fill=(255, 232, 67, 255))

    if not has_l2:
        # 단일행 훅 인스타 규격
        target_size = int(fs_l1 * 0.72) if fs_l1 > 0 else 76
        font_l1 = get_fitted_font(FONT_B, title_line1, target_size=target_size, max_width=740, min_size=40)
        l1_box = draw.textbbox((0, 0), title_line1, font=font_l1)
        draw_text_with_shadow(draw, (cx - (l1_box[2] - l1_box[0]) // 2, gy0 + 105), title_line1, font=font_l1, fill_color=(255, 232, 67, 255), shadow_offset=5)
    else:
        if not has_sub:
            # 티저 없는 2단 대형 훅
            target_l1 = int(fs_l1 * 0.68) if fs_l1 > 0 else 80
            target_l2 = int(fs_l2 * 0.68) if fs_l2 > 0 else 96
            font_l1 = get_fitted_font(FONT_B, title_line1, target_size=target_l1, max_width=740, min_size=40)
            font_l2 = get_fitted_font(FONT_B, title_line2, target_size=target_l2, max_width=740, min_size=40)
            y_l1 = gy0 + 75
            y_l2 = gy0 + 170
        else:
            font_l1 = get_fitted_font(FONT_B, title_line1, target_size=58, max_width=740, min_size=36)
            font_l2 = get_fitted_font(FONT_B, title_line2, target_size=68, max_width=740, min_size=40)
            y_l1 = gy0 + 80
            y_l2 = gy0 + 155

        l1_box = draw.textbbox((0, 0), title_line1, font=font_l1)
        draw_text_with_shadow(draw, (cx - (l1_box[2] - l1_box[0]) // 2, y_l1), title_line1, font=font_l1, fill_color=(255, 255, 255, 255), shadow_offset=5)

        l2_box = draw.textbbox((0, 0), title_line2, font=font_l2)
        draw_text_with_shadow(draw, (cx - (l2_box[2] - l2_box[0]) // 2, y_l2), title_line2, font=font_l2, fill_color=(255, 232, 67, 255), shadow_offset=5)

    if has_sub:
        font_sub = get_fitted_font(FONT_SANS, teaser_text, target_size=28, max_width=740, min_size=20)
        s_box = draw.textbbox((0, 0), teaser_text, font=font_sub)
        sw, sh = s_box[2] - s_box[0], s_box[3] - s_box[1]
        sx0, sy0 = cx - sw // 2 - 14, gy0 + 240
        draw.rounded_rectangle([sx0, sy0, sx0 + sw + 28, sy0 + sh + 12], radius=8, fill=(0, 0, 0, 180))
        draw.text((cx - sw // 2, gy0 + 245), teaser_text, font=font_sub, fill=(235, 240, 250, 255))

    canvas.save(out_clean_path, "JPEG", quality=96)
    print(f"[generate_thumbnail] 인스타 클린 커버 저장: {out_clean_path}")

    if out_guide_path:
        canvas_guide = canvas.copy()
        draw_g = ImageDraw.Draw(canvas_guide)
        draw_g.rectangle([gx0, gy0, gx1, gy1], outline=(255, 220, 0), width=4)
        canvas_guide.save(out_guide_path, "JPEG", quality=96)
        print(f"[generate_thumbnail] 인스타 가이드 커버 저장: {out_guide_path}")

FADE_OUT_D = 0.40   # 끝 페이드아웃 길이(초)
TAIL_HOLD = 0.15    # 말이 끝나고 페이드가 시작되기까지 쉬는 시간(초)


def last_visible_sub_end(ass_path):
    """ASS 에서 마지막으로 '글자가 보이는' 자막 블록의 끝 시각(초). 없으면 None."""
    import re as _re
    last = None
    try:
        for line in open(ass_path, encoding="utf-8"):
            if not line.startswith("Dialogue"):
                continue
            parts = line.split(",", 9)
            body = _re.sub(r"\{[^}]*\}", "", parts[9]).replace("\\N", "").strip()
            if not body:
                continue                      # ko 가 빈 블록(자막 없는 꼬리)은 건너뛴다
            h, m, sec = parts[2].split(":")
            last = int(h) * 3600 + int(m) * 60 + float(sec)
    except Exception as exc:  # noqa: BLE001
        print(f"[generate_thumbnail] \u26a0ASS 읽기 실패({exc})")
        return None
    return last


def last_speech_end(video_path):
    """영상 안에서 마지막 발화가 끝나는 시각(초). 실패하면 None."""
    import subprocess
    import tempfile
    import wave

    import numpy as np

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    try:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", video_path,
                        "-vn", "-ac", "1", "-ar", "16000", tmp.name], check=True)
        with wave.open(tmp.name) as w:
            sr = w.getframerate()
            a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        a = a.astype(np.float32) / 32768.0
        hop, win = int(sr * 0.010), int(sr * 0.025)
        m = (len(a) - win) // hop
        if m <= 0:
            return None
        rms = np.sqrt(np.array([np.mean(a[i * hop:i * hop + win] ** 2)
                                for i in range(m)])) + 1e-9
        db = 20 * np.log10(rms)
        # check_output.voiced_track 과 같은 임계값 규칙을 쓴다
        floor, speech = np.percentile(db, 5), np.percentile(db, 85)
        thr = min(max(floor + 6, speech - 18), speech - 12)
        voiced = np.flatnonzero(db > thr)
        if not len(voiced):
            return None
        return float(voiced[-1] * 0.010 + 0.025)
    except Exception as exc:  # noqa: BLE001
        print(f"[generate_thumbnail] ⚠발화 끝 검출 실패({exc}) — 꼬리 패딩 생략")
        return None
    finally:
        os.unlink(tmp.name)


def attach_thumbnail_to_video(thumb_path, video_path, out_path=None, ass_path=None,
                              sub_end=None, fade_d=None, no_intro=False):
    """영상 첫머리에 무음 썸네일(0.7s) -> 블랙 페이드아웃(0.25s) -> 본 영상 페이드인(0.25s) 결합.
    
    1. 0.00s ~ 0.70s: 100% 정지 썸네일 (완전 디지털 무음) -> 플랫폼 0초 첫 프레임 썸네일 인식
    2. 0.70s ~ 0.95s: 썸네일 블랙으로 페이드 아웃 (0.25초)
    3. 0.95s: 암전(Dip to black)
    4. 0.95s ~ 1.20s: 본 영상 블랙에서 페이드 인 (0.25초) + 오디오 50ms 마이크로 페이드인 시작
    """
    import subprocess
    import shutil
    if out_path is None:
        vdir = os.path.dirname(os.path.abspath(video_path))
        work_dir = os.path.join(vdir, "_work")
        base = os.path.basename(video_path)
        raw_in_work = os.path.join(work_dir, base.replace(".mp4", "_raw.mp4"))
        raw_in_cur = video_path.replace(".mp4", "_raw.mp4")

        # 만약 이미 _raw 백업이 있으면 원본 깨끗한 영상을 소스로 사용
        if os.path.exists(raw_in_work):
            src_video = raw_in_work
        elif os.path.exists(raw_in_cur):
            if os.path.exists(work_dir):
                shutil.move(raw_in_cur, raw_in_work)
                src_video = raw_in_work
            else:
                src_video = raw_in_cur
        else:
            if os.path.exists(work_dir):
                shutil.copyfile(video_path, raw_in_work)
                src_video = raw_in_work
            else:
                shutil.copyfile(video_path, raw_in_cur)
                src_video = raw_in_cur

        target_out = video_path + ".tmp.mp4"
        final_dest = video_path
    else:
        src_video = video_path
        target_out = out_path
        final_dest = out_path

    # 본 영상 길이 측정하여 종료 0.4초 전부터 앞뒤 페이드(블랙 및 오디오) 적용
    probe_cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", src_video
    ]
    res = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
    src_dur = float(res.stdout.strip())

    # ★끝 페이드는 마지막 말이 끝난 뒤에 시작해야 한다(오너 지시 2026-09-06).
    #   그 여유는 plan 의 마지막 구간을 뒤로 늘려서 "원본 뒤를 살려" 확보한다.
    #   여기서 정지 프레임으로 늘리면 말이 끊긴 채 얼어붙어 더 어색해진다.
    # 마지막 자막이 끝난 뒤 TAIL_HOLD 만큼 쉬고 페이드아웃한다. 그만큼의 여유는
    # plan 의 마지막 구간을 원본에서 더 가져와("뒤를 살려") 확보해 둔 상태여야 한다.
    fade_len = FADE_OUT_D if fade_d is None else float(fade_d)
    fade_out_st = max(0.0, src_dur - fade_len)
    if sub_end is None and ass_path:
        sub_end = last_visible_sub_end(ass_path)
    if sub_end is not None:
        want = sub_end
        if want + fade_len <= src_dur + 1e-6:
            fade_out_st = want
            print(f"[generate_thumbnail] 마지막 자막 끝 {sub_end:.2f}s "
                  f"→ 페이드 {fade_out_st:.2f}~{fade_out_st + fade_len:.2f}s "
                  f"(길이 {fade_len:.2f}s, 원본 꼬리 {src_dur - (fade_out_st + fade_len):.2f}s 잘라냄)")
        else:
            print(f"[generate_thumbnail] \u26a0꼬리 부족: 자막 끝 {sub_end:.2f}s / 영상 {src_dur:.2f}s "
                  f"— {want + fade_len - src_dur:.2f}s 더 필요하다. plan 마지막 구간을 늘려라")
    trim_v = f",trim=end={fade_out_st + fade_len:.3f},setpts=PTS-STARTPTS"
    trim_a = f",atrim=end={fade_out_st + fade_len:.3f},asetpts=PTS-STARTPTS"
    tail_v = ""
    tail_a = ""

    if no_intro:
        # 앞 썸네일 정지컷 없이 본 영상만 — 시작 페이드인 + 끝 페이드아웃
        cmd = [
            "ffmpeg", "-y", "-i", src_video,
            "-filter_complex",
            f"[0:v]fps=30,settb=1/30,format=yuv420p{tail_v}{trim_v},"
            f"fade=t=in:st=0.0:d=0.25,fade=t=out:st={fade_out_st:.3f}:d={fade_len:.3f}[vout];"
            f"[0:a]aresample=48000{tail_a}{trim_a},"
            f"afade=t=in:st=0.0:d=0.08,afade=t=out:st={fade_out_st:.3f}:d={fade_len:.3f}[aout]",
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
            target_out,
        ]
        subprocess.run(cmd, check=True)
        if out_path is None:
            os.replace(target_out, final_dest)
        print(f"[generate_thumbnail] 썸네일 인트로 없이 앞뒤 페이드만 적용 완료: {final_dest}")
        return

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-t", "0.95", "-i", thumb_path,
        "-f", "lavfi", "-t", "0.95", "-i", "anullsrc=r=48000:cl=mono",
        "-i", src_video,
        "-filter_complex",
        f"[0:v]fps=30,settb=1/30,format=yuv420p,fade=t=out:st=0.7:d=0.25[v0];"
        f"[2:v]fps=30,settb=1/30,format=yuv420p{tail_v}{trim_v},"
        f"fade=t=in:st=0.0:d=0.25,fade=t=out:st={fade_out_st:.3f}:d={fade_len:.3f}[v1];"
        f"[2:a]aresample=48000{tail_a}{trim_a},"
        f"afade=t=in:st=0.0:d=0.08,afade=t=out:st={fade_out_st:.3f}:d={fade_len:.3f}[a1];"
        f"[v0][1:a][v1][a1]concat=n=2:v=1:a=1[vout][aout]",
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        target_out
    ]
    subprocess.run(cmd, check=True)
    if out_path is None:
        os.replace(target_out, final_dest)
    print(f"[generate_thumbnail] 앞뒤 페이드(시작 페이드인 + 끝 0.4s 블랙/오디오 페이드아웃) 인트로 영상 결합 완료: {final_dest}")

def main():
    parser = argparse.ArgumentParser(description="유튜브 & 인스타그램 썸네일 통합 생성기 (A안 안전영역 기본)")
    parser.add_argument("--mode", choices=["youtube", "instagram", "both"], default="youtube")
    parser.add_argument("--frame", required=True, help="썸네일 배경 프레임 이미지 경로")
    parser.add_argument("--badge", default="하올람 말씀 인사이트", help="상단 카테고리 뱃지 문구")
    parser.add_argument("--l1", required=True, help="메인 타이틀 1행 (화이트 또는 단일 훅)")
    parser.add_argument("--l2", default="", help="메인 타이틀 2행 (골드 강조, 단일 훅일 경우 생략 가능)")
    parser.add_argument("--sub", default="", help="하단 서브 티저 문구 (생략 시 타이틀 극대화 모드)")
    parser.add_argument("--fs-l1", type=int, default=0, help="타이틀 1행 폰트 크기 (기본 0: 자동 최적화)")
    parser.add_argument("--fs-l2", type=int, default=0, help="타이틀 2행 폰트 크기 (기본 0: 자동 최적화)")
    parser.add_argument("--out-yt", default="thumb_youtube.jpg", help="유튜브 썸네일 출력 경로")
    parser.add_argument("--out-insta", default=None, help="인스타 클린 커버 출력 경로 (미지정 시 생성 생략)")
    parser.add_argument("--out-guide", default=None, help="인스타 가이드 커버 출력 경로")
    parser.add_argument("--video", default=None, help="썸네일 인트로를 첫머리에 결합할 완성본 영상 경로 (옵션)")
    parser.add_argument("--no-intro", action="store_true",
                        help="앞 썸네일 정지컷 없이 페이드인/아웃만 적용한다")
    parser.add_argument("--ass", default=None, help="자막 ASS 경로 — 마지막 자막이 끝난 뒤에 페이드아웃을 시작한다")
    parser.add_argument("--zoom", type=float, default=1.0, help="인물 줌아웃 배율 (예: 0.86 — 타이틀 상단 여백 확보)")
    args = parser.parse_args()

    if args.mode in ["youtube", "both"]:
        make_youtube_thumbnail(args.frame, args.badge, args.l1, args.l2, args.sub, args.out_yt, zoom=args.zoom, fs_l1=args.fs_l1, fs_l2=args.fs_l2)
    if args.mode in ["instagram", "both"] and args.out_insta:
        make_instagram_thumbnail(args.frame, args.badge, args.l1, args.l2, args.sub, args.out_insta, args.out_guide, zoom=args.zoom, fs_l1=args.fs_l1, fs_l2=args.fs_l2)
    if args.video and os.path.exists(args.video):
        attach_thumbnail_to_video(args.out_yt, args.video, ass_path=args.ass,
                                  no_intro=args.no_intro)

if __name__ == "__main__":
    main()
