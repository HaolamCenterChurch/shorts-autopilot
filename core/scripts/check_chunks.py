#!/usr/bin/env python3
"""chunks.json 이 words.json 의 글자 스트림을 빠짐없이 덮는지 기계로 확인한다.

자막이 밀리는 사고는 거의 전부 여기서 잡힌다 — chunk 들의 정규화 텍스트를 이어붙인
것이 whisper 글자 스트림과 정확히 같아야 한다.
"""
import argparse
import json
import re
import sys
import difflib
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from safe_area import safe_width  # noqa: E402  안전폭 정본


def normalize(t):
    t = re.sub(r"\\N", "", t or "")
    return re.sub(r"[^0-9A-Za-z가-힣]", "", t or "")


def get_font(size=90):
    try:
        from PIL import ImageFont
        return ImageFont.truetype("/System/Library/Fonts/AppleSDGothicNeo.ttc", size)
    except Exception:
        return None


def measure_line_width(line, font=None, font_size=90):
    clean = re.sub(r"\{[^}]*\}", "", line).strip()
    if not clean:
        return 0, 0
    if font is not None:
        bbox = font.getbbox(clean)
        return len(clean), (bbox[2] - bbox[0])
    w = 0
    for ch in clean:
        if "\uac00" <= ch <= "\ud7a3":
            w += 78 * (font_size / 90.0)
        elif ch.isspace():
            w += 25 * (font_size / 90.0)
        else:
            w += 48 * (font_size / 90.0)
    return len(clean), int(w)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--words", required=True)
    ap.add_argument("--chunks", required=True)
    ap.add_argument("--font-size", type=int, default=90, help="한글 자막 폰트 크기 (기본 90)")
    ap.add_argument("--max-width", type=int, default=None,
                    help="행 최대 폭(px). 생략하면 safe_area.safe_width(--margin-lr)")
    ap.add_argument("--margin-lr", type=int, default=30, help="본문 스타일 MarginL/R (기본 30)")
    ap.add_argument("--max-lines", type=int, default=1,
                    help="블록당 허용 행 수 (기본 1 = 단독 1줄 원칙). "
                         "중앙 2행 자막을 쓰는 스킬은 2 를 넘긴다")
    ap.add_argument("--plan", default=None,
                    help="plan_{ID}.json. 주면 subs.top_title 각 행의 폭까지 검사한다 "
                         "(제목은 본문과 폰트·여백이 달라 따로 잰다)")
    args = ap.parse_args()
    if args.max_width is None:
        args.max_width = safe_width(args.margin_lr)

    words = json.load(open(args.words, encoding="utf-8"))
    chunks = json.load(open(args.chunks, encoding="utf-8"))

    stream = normalize("".join(w["text"] for w in words))
    joined = normalize("".join(c.get("stt") or c.get("ko", "") for c in chunks))

    has_error = False

    # 1. 텍스트 스트림 일치 검증
    if stream != joined:
        print(f"❌ 글자 스트림 불일치 — whisper {len(stream)}자 vs 청크 {len(joined)}자")
        sm = difflib.SequenceMatcher(None, stream, joined)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            print(f"  [{tag}] whisper:{stream[max(0,i1-12):i2+12]!r}")
            print(f"        청크   :{joined[max(0,j1-12):j2+12]!r}")
        has_error = True
    else:
        lens = [len(normalize(c.get("ko", ""))) for c in chunks]
        print(f"✅ 스트림 일치 ({len(stream)}자, 블록 {len(chunks)}개, "
              f"블록길이 {min(lens)}~{max(lens)}자)")

    # 2. 자막 레이아웃 검증 — 행 수 제한과 가로 폭
    #   P3: 행이 여럿이면 행마다 따로 잰다. 이어 붙여 재면 실제보다 넓게 나온다.
    #   허용 행 수는 스킬마다 다르므로 --max-lines 로 받는다(기본 1 = 단독 1줄 원칙).
    font = get_font(args.font_size)
    two_line_violations = []
    layout_overflows = []

    for i, c in enumerate(chunks):
        ko = c.get("ko", "")
        lines = [l for l in re.split(r"\\N|\n", ko)]
        if len(lines) > args.max_lines:
            two_line_violations.append((i, ko, len(lines)))

        for line in lines:
            line_clean = re.sub(r"\{[^}]*\}", "", line).strip()
            if not line_clean:
                continue
            char_count, width = measure_line_width(line_clean, font=font,
                                                   font_size=args.font_size)
            if width > args.max_width:
                layout_overflows.append((i, line_clean, char_count, width))

    if two_line_violations:
        print(f"\n❌ [자막 행 수 초과] 총 {len(two_line_violations)}개 블록이 "
              f"허용 행 수({args.max_lines}행)를 넘었다:")
        for idx, text, n in two_line_violations:
            print(f'  - [{idx}번] {n}행  "{text}"')
        print("  👉 조치: 줄바꿈으로 늘리지 말고 청크를 나눠 순차 노출한다.")
        has_error = True

    if layout_overflows:
        print(f"\n❌ [자막 가로 폭 초과] 총 {len(layout_overflows)}개 행이 "
              f"안전영역({args.max_width}px)을 초과함:")
        for idx, text, count, px in layout_overflows:
            print(f'  - [{idx}번] "{text}" -> {count}자, {px}px (한계: {args.max_width}px)')
        print("  👉 조치: 청크를 더 짧은 호흡 단위로 분할한다.")
        has_error = True

    if not two_line_violations and not layout_overflows:
        print(f"✅ 자막 레이아웃 통과 (최대 {args.max_lines}행, 모든 행 <= {args.max_width}px)")

    # ── 상단 제목(TopTitle) 폭 검사 ────────────────────────────────
    #   P3: 화면에 뜨는 요소는 전부 잰다. 제목은 본문과 폰트·여백이 달라
    #   안전폭도 다르므로 본문 기준을 그대로 적용하면 안 된다.
    if args.plan:
        plan = json.load(open(args.plan, encoding="utf-8"))
        subs = plan.get("subs", {}) or {}
        title = subs.get("top_title") or plan.get("title") or ""
        t_size = int(subs.get("top_title_size", 96))
        t_lr = int(subs.get("top_title_margin_lr", 50))
        t_limit = safe_width(t_lr)
        t_font = get_font(t_size)
        bad = []
        for line in re.split(r"\\N|\n", title):
            n, w = measure_line_width(line, font=t_font, font_size=t_size)
            if w > t_limit:
                bad.append((re.sub(r"\{[^}]*\}", "", line).strip(), n, w))
        if bad:
            has_error = True
            print(f"\n❌ [상단 제목 가로 폭 초과] {len(bad)}행이 안전폭 {t_limit}px 를 넘었다 "
                  f"({t_size}pt, MarginLR {t_lr}):")
            for text, n, w in bad:
                print(f'  - "{text}" -> {n}자, {w}px (한계: {t_limit}px)')
            print("  → 제목을 짧게 줄이거나 \\N 위치를 옮겨 각 행을 안전폭 안에 넣는다.")
        else:
            print(f"✅ 상단 제목 레이아웃 통과 (모든 행 <= {t_limit}px)")

    return 1 if has_error else 0


if __name__ == "__main__":
    sys.exit(main())

