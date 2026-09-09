#!/usr/bin/env python3
"""편집된 타임라인 기준으로 영/한 2단 자막 ASS 파일을 만든다.

★폰트는 "Apple SD Gothic Neo"(Bold) 를 쓴다. PretendardVariable.ttf 는 패밀리명이
  "Pretendard Variable" 이고 가변 폰트라 libass 가 굵기 축에 못 닿아 항상 Regular 로
  그려진다 — 쇼츠 자막으로는 너무 얇다(2026-09-02 실측 비교).

★PlayRes 는 최종 출력 해상도(1080x1920)와 같아야 한다. 보고서의 4K(2160x3840)
  수치를 그대로 쓰면 libass 가 절반으로 줄여 그려서 글씨가 절반 크기가 된다
  (2026-09-02 실측).
"""
import argparse
import json
import re
import sys

ASS_HEADER = """[Script Info]
Title: shorts_v2 subtitles
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,Apple SD Gothic Neo,{FS},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,{OUTLINE},{SHADOW},2,{MLR},{MLR},{MV},1{TOPSTYLE}{BADGESTYLE}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def normalize(text):
    """한글/영숫자만 남긴 정규화 문자열."""
    return re.sub(r"[^0-9A-Za-z가-힣]", "", text or "")


def fmt_time(sec):
    if sec < 0:
        sec = 0.0
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    cs = int(round((sec - int(sec)) * 100))
    if cs >= 100:
        cs -= 100
        s += 1
        if s >= 60:
            s -= 60
            m += 1
            if m >= 60:
                m -= 60
                h += 1
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def match_chunks_to_words(words, chunks):
    """길이 기반 소비로 chunk 별 시작/끝을 단어 타임스탬프에서 구한다."""
    norm_words = [normalize(w.get("text", "")) for w in words]
    w_idx = 0
    n_words = len(words)
    results = []
    for chunk in chunks:
        # ★"stt" 가 있으면 그걸로 길이를 잰다. 화면에 띄우는 ko 는 STT 오인식을
        #   교정한 문장이라 길이가 달라질 수 있고, 그러면 정렬이 밀린다.
        target_len = len(normalize(chunk.get("stt") or chunk.get("ko", "")))
        first_idx = w_idx
        # ★선행 구두점 토큰(정규화하면 빈 문자열)을 블록 시작 시각으로 쓰지 않는다.
        #   whisper 는 회중 웃음처럼 발화가 없는 구간을 "." 토큰 하나로 길게 잡는다.
        #   그걸 시작으로 삼으면 자막이 실제 발화보다 먼저 떠서 펀치라인을 미리 까버린다
        #   (2026-09-06 실측: D안 "서로 고아라서 그래요" 가 발화 1.78초 전에 떴다).
        while first_idx < n_words and not norm_words[first_idx]:
            first_idx += 1
        consumed = 0
        last_idx = w_idx
        if target_len == 0:
            # 빈 대사: 다음 단어 하나만 소비하지 않고 인접 시각으로 처리
            results.append((None, None))
            continue
        while w_idx < n_words and consumed < target_len:
            consumed += len(norm_words[w_idx])
            last_idx = w_idx
            w_idx += 1
        if first_idx >= n_words:
            # 단어가 모자라면 마지막 단어를 재사용
            first_idx = last_idx = n_words - 1
        start = words[first_idx]["t0"] if n_words else 0.0
        end = words[last_idx]["t1"] if n_words else 0.0
        results.append((start, end))
    return results


def build_dialogue_text(en, ko, hl, no_en=False, fs=68):
    ko = ko or ""
    en = "" if no_en else (en or "")
    if hl:
        # 하이라이트 자막: 골드 컬러(&H00E5FF&) + 팝인 바운스 애니메이션 (118% -> 100%)
        ko_tag = (r"{\t(0,80,\fscx118\fscy118)\t(80,160,\fscx100\fscy100)\fs"
                  + str(fs) + r"\c&H00E5FF&}")
    else:
        ko_tag = r"{\fs" + str(fs) + r"\c&HFFFFFF&}"
    if en.strip():
        en_part = r"{\fs42\c&HF0F4F8&}" + en + r"\N"
    else:
        en_part = ""
    return f"{en_part}{ko_tag}{ko}"


def main():
    ap = argparse.ArgumentParser(description="영/한 2단 ASS 자막 생성")
    ap.add_argument("--words", required=True)
    ap.add_argument("--chunks", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--duration", type=float, required=True)
    ap.add_argument("--last-tail", type=float, default=None,
                    help="마지막 블록을 (마지막 단어 끝 + 이 초)에서 끊는다. "
                         "미지정이면 종전대로 영상 끝까지 늘린다.")
    ap.add_argument("--no-en", action="store_true",
                    help="영문 자막을 렌더하지 않는다 (한글 1단)")
    ap.add_argument("--font-size", type=int, default=68,
                    help="한글 자막 폰트 크기 (기본 68)")
    ap.add_argument("--margin-v", type=int, default=470,
                    help="자막 하단 여백 px (기본 470)")
    ap.add_argument("--badge", default=None,
                    help="상단 밴드에 얹을 작은 뱃지 문구 (옵션)")
    ap.add_argument("--badge-margin", type=int, default=205,
                    help="뱃지의 화면 위쪽 여백 px (기본 205)")
    ap.add_argument("--margin-lr", type=int, default=60,
                    help="자막 좌우 여백 px (기본 60)")
    ap.add_argument("--outline", type=float, default=4.0,
                    help="자막 테두리(스트로크) 두께. 0 이면 테두리 없음 (기본 4.0)")
    ap.add_argument("--top-title-outline", type=float, default=6.0,
                    help="상단 제목 테두리 두께. 0 이면 테두리 없음 (기본 6.0)")
    ap.add_argument("--shadow", type=float, default=2.0,
                    help="자막 그림자 크기. 0 이면 그림자 없음 (기본 2.0)")
    ap.add_argument("--top-title", default=None,
                    help="화면 상단(인물 머리 위)에 영상 내내 고정 표시할 제목. \\N 으로 줄바꿈")
    ap.add_argument("--top-title-size", type=int, default=84,
                    help="상단 제목 폰트 크기 (기본 84)")
    ap.add_argument("--top-title-margin", type=int, default=110,
                    help="상단 제목의 화면 위쪽 여백 px (기본 110)")
    args = ap.parse_args()

    with open(args.words, "r", encoding="utf-8", errors="ignore") as f:
        words = json.load(f)
    with open(args.chunks, "r", encoding="utf-8", errors="ignore") as f:
        chunks = json.load(f)

    if not chunks:
        raise ValueError("chunks 가 비어 있다")

    spans = match_chunks_to_words(words, chunks)

    # None 스팬(빈 ko)을 이웃 값으로 보정
    for i, (s, e) in enumerate(spans):
        if s is None:
            prev_e = spans[i - 1][1] if i > 0 and spans[i - 1][1] is not None else 0.0
            spans[i] = (prev_e, prev_e)

    starts = [s for s, _ in spans]
    ends = [e for _, e in spans]

    # 무간극 타이밍: end[i] = start[i+1], 마지막 end = duration, 첫 start = 0
    starts[0] = 0.0
    for i in range(len(spans) - 1):
        ends[i] = starts[i + 1]
    ends[-1] = args.duration
    if args.last_tail is not None:
        # 마지막 자막이 영상 끝까지 늘어나면 뒤에 페이드아웃 자리가 안 남는다.
        last_word_end = 0.0
        for _w in reversed(words):          # 구두점 토큰은 건너뛴다
            # 영상 길이를 넘는 t1 은 DTW 가 꼬리 무음까지 물고 늘어진 것이라 믿지 않는다.
            if normalize(_w.get("text", "")) and _w["t1"] <= args.duration + 1e-6:
                last_word_end = _w["t1"]
                break
        ends[-1] = min(args.duration, max(starts[-1] + 0.40, last_word_end + args.last_tail))

    top_style = ""
    if args.top_title:
        top_style = (
            "\nStyle: TopTitle,Apple SD Gothic Neo,%d,&H00FFFFFF,&H000000FF,"
            "&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,%.1f,0.0,8,50,50,%d,1"
            % (args.top_title_size, args.top_title_outline, args.top_title_margin))

    badge_style = ""
    if args.badge:
        badge_style = (
            "\nStyle: Badge,Apple SD Gothic Neo,36,&H0043E8FF,&H000000FF,"
            "&H00141414,&H00141414,-1,0,0,0,100,100,0,0,3,8.0,0.0,8,50,50,%d,1"
            % args.badge_margin)

    header = ASS_HEADER.replace("{BADGESTYLE}", badge_style) \
                       .replace("{MV}", str(args.margin_v)) \
                       .replace("{FS}", str(args.font_size)) \
                       .replace("{MLR}", str(args.margin_lr)) \
                       .replace("{SHADOW}", f"{args.shadow:.1f}") \
                       .replace("{OUTLINE}", f"{args.outline:.1f}") \
                       .replace("{TOPSTYLE}", top_style)
    lines = [header]
    if args.badge:
        lines.append(
            f"Dialogue: 0,{fmt_time(0.0)},{fmt_time(args.duration)},Badge,,0,0,0,,"
            f"{args.badge}\n")
    if args.top_title:
        lines.append(
            f"Dialogue: 0,{fmt_time(0.0)},{fmt_time(args.duration)},TopTitle,,0,0,0,,"
            f"{args.top_title}\n")
    for chunk, s, e in zip(chunks, starts, ends):
        if e <= s:
            e = s + 0.01
        text = build_dialogue_text(chunk.get("en"), chunk.get("ko", ""),
                                    chunk.get("hl", False), no_en=args.no_en,
                                    fs=args.font_size)
        lines.append(
            f"Dialogue: 0,{fmt_time(s)},{fmt_time(e)},Main,,0,0,0,,{text}\n")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("".join(lines))

    log(f"[완료] {args.out} 블록 {len(chunks)}개, 마지막 종료={ends[-1]:.3f}s")


if __name__ == "__main__":
    main()
