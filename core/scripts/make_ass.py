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
Style: Main,Apple SD Gothic Neo,68,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,4.0,2.0,2,60,60,470,1

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


def build_dialogue_text(en, ko, hl):
    ko = ko or ""
    en = en or ""
    if hl:
        ko_tag = r"{\fs68\c&H00E5FF&\fscx112\fscy112}"
    else:
        ko_tag = r"{\fs68\c&HFFFFFF&}"
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

    lines = [ASS_HEADER]
    for chunk, s, e in zip(chunks, starts, ends):
        if e <= s:
            e = s + 0.01
        text = build_dialogue_text(chunk.get("en"), chunk.get("ko", ""),
                                    chunk.get("hl", False))
        lines.append(
            f"Dialogue: 0,{fmt_time(s)},{fmt_time(e)},Main,,0,0,0,,{text}\n")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("".join(lines))

    log(f"[완료] {args.out} 블록 {len(chunks)}개, 마지막 종료={ends[-1]:.3f}s")


if __name__ == "__main__":
    main()
