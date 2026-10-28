#!/usr/bin/env python3
"""렌더에 들어가는 ASS 의 모든 스타일·모든 행을 플랫폼 안전폭으로 잰다.

설교·인사이트·찬양 쇼츠 공용. 줄바꿈이 끝난 최종 ASS 를 재야 화면과 같다(P3).
인라인 \\fs 크기와 \\N 행을 반영한다. 안전폭 정본은 safe_area.py.
종료코드: 0 통과 / 1 초과 있음.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from safe_area import safe_width, text_width, PLATFORM_SAFE_X0, PLATFORM_SAFE_X1  # noqa: E402


def load_styles(path):
    styles = {}
    for line in open(path, encoding="utf-8"):
        if line.startswith("Style:"):
            p = [x.strip() for x in line[6:].split(",")]
            try:
                styles[p[0]] = (int(float(p[2])), int(p[-4]))
            except (ValueError, IndexError):
                pass
    return styles


def check(path):
    styles = load_styles(path)
    rows, over = 0, []
    for line in open(path, encoding="utf-8"):
        if not line.startswith("Dialogue:"):
            continue
        p = line.split(",", 9)
        style, text = p[3].strip(), p[9].strip()
        size, mlr = styles.get(style, (90, 0))
        # 대사 줄 자체의 MarginL/R 이 0 이 아니면 그 값이 스타일보다 우선
        try:
            dl, dr = int(p[5]), int(p[6])
            if dl or dr:
                mlr = max(dl, dr)
        except ValueError:
            pass
        limit = safe_width(mlr)
        cur = size
        for seg in re.split(r"\\N", text):
            m = re.findall(r"\\fs(\d+)", seg)
            if m:
                cur = int(m[-1])
            w = text_width(seg, cur)
            if not w:
                continue
            rows += 1
            if w > limit:
                over.append((w, limit, style, re.sub(r"\{[^}]*\}", "", seg).strip()))
    return rows, over


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ass", nargs="+")
    args = ap.parse_args()
    bad = 0
    for path in args.ass:
        rows, over = check(path)
        name = os.path.basename(path)
        if over:
            bad += 1
            print(f"❌ {name}: {rows}행 중 {len(over)}행이 안전폭 초과")
            for w, lim, st, t in sorted(over, reverse=True):
                print(f'   {w}px > {lim}px  [{st}] "{t}"')
        else:
            print(f"✅ {name}: {rows}행 모두 안전폭 이내")
    print(f"(플랫폼 안전영역 x {PLATFORM_SAFE_X0}~{PLATFORM_SAFE_X1}, 정본 safe_area.py)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
