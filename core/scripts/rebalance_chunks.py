#!/usr/bin/env python3
"""G-C(블록 표시 시간) 불합격을 청크 경계 재배치로 고친다. 글자·표기는 바꾸지 않는다.

짧게 스쳐 가는 자막은 두 가지로 생긴다 — 무음을 잘라 말이 촘촘해졌거나, 폭 때문에 나눈 블록이 짧거나.
이웃과 그냥 합치면 한 행 안전폭을 넘기 쉬우므로(P3), 연속된 2~3 블록의 어절을 모아
단어 경계에서 다시 나눈다. 모든 행은 safe_area 안전폭 이내, 경계 시각은 master.words.json 기준.
stt 가 ko 와 다른 블록은 표기-발화 대응이 깨질 수 있어 건드리지 않는다.
★ 이 도구는 시간만 보고 끊으므로 의미 단위를 모른다. 결과(→ 로 찍힌 재배치)를 반드시 읽고
  명사구·호흡이 끊긴 줄은 손으로 고친 뒤 check_chunks 를 다시 돌린다. 기본은 --dry-run 으로 먼저 본다.

  python3 scripts/shorts_v2/rebalance_chunks.py --words {slug}_master.words.json --chunks chunks_{ID}.json [--dry-run]
"""
import argparse
import itertools
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from safe_area import safe_width, text_width  # noqa: E402

MIN_SHOW = 0.45     # G-C 최단 0.40s 에 여유
SHORT = 1.0         # G-C "1초 미만"
TARGET_RATIO = 0.33  # G-C 35% 에 여유


def norm(t):
    return re.sub(r"[^0-9A-Za-z가-힣]", "", t or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--words", required=True)
    ap.add_argument("--chunks", required=True)
    ap.add_argument("--font-size", type=int, default=90)
    ap.add_argument("--margin-lr", type=int, default=30)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    lim = safe_width(a.margin_lr)

    times = []
    for w in json.load(open(a.words, encoding="utf-8")):
        times.extend([w["t0"]] * len(norm(w["text"])))
    end_t = json.load(open(a.words, encoding="utf-8"))[-1]["t1"]
    ch = json.load(open(a.chunks, encoding="utf-8"))

    def stream_len(c):
        return len(norm(c.get("stt") or c["ko"]))

    def durations(blocks):
        st, k = [], 0
        for c in blocks:
            st.append(times[min(k, len(times) - 1)])
            k += stream_len(c)
        return [e - s for s, e in zip(st, st[1:] + [end_t])], st

    def score(blocks):
        d, _ = durations(blocks)
        return (sum(x < SHORT for x in d) / len(d), -min(d))

    def editable(c):
        return "stt" not in c or norm(c["stt"]) == norm(c["ko"])

    def resplit(group, n_out, start_char):
        """group 블록들의 어절을 n_out 개로 다시 나눈 후보 중 최단 표시가 가장 긴 것."""
        words = " ".join(c["ko"] for c in group).split()
        hl = any(c.get("hl") for c in group)
        best = None
        for cuts in itertools.combinations(range(1, len(words)), n_out - 1):
            parts = [" ".join(words[i:j]) for i, j in zip((0,) + cuts, cuts + (len(words),))]
            if any(text_width(p, a.font_size) > lim for p in parts):
                continue
            k, st = start_char, []
            for p in parts:
                st.append(times[min(k, len(times) - 1)])
                k += len(norm(p))
            nxt = times[k] if k < len(times) else end_t
            dur = [e - s for s, e in zip(st, st[1:] + [nxt])]
            key = (sum(x < SHORT for x in dur), -min(dur))
            if best is None or key < best[0]:
                best = (key, [{"ko": p, "hl": hl} for p in parts])
        return best

    changed = 0
    for _ in range(200):
        d, _st = durations(ch)
        ratio = sum(x < SHORT for x in d) / len(d)
        # 비율 목표에 닿으면 멈춘다. 최단 표시를 쫓아 계속 쪼개면 의미 단위가 무너진다.
        if ratio <= TARGET_RATIO:
            break
        base = score(ch)
        best = None
        offs = [0]
        for c in ch:
            offs.append(offs[-1] + stream_len(c))
        for size, n_out in ((2, 2), (3, 2), (3, 3)):
            for k in range(len(ch) - size + 1):
                grp = ch[k:k + size]
                if not all(editable(c) for c in grp):
                    continue
                if not any(x < SHORT for x in d[k:k + size]):
                    continue
                r = resplit(grp, n_out, offs[k])
                if not r:
                    continue
                trial = ch[:k] + r[1] + ch[k + size:]
                sc = score(trial)
                if sc < base and (best is None or sc < best[0]):
                    best = (sc, k, size, r[1])
        if not best:
            break
        _, k, size, new = best
        print(f"   {' | '.join(c['ko'] for c in ch[k:k + size])}  →  {' | '.join(c['ko'] for c in new)}")
        ch = ch[:k] + new + ch[k + size:]
        changed += 1

    d, _ = durations(ch)
    ratio = sum(x < SHORT for x in d) / len(d)
    ok = ratio <= TARGET_RATIO + 0.02 and min(d) >= 0.40
    for i, x in enumerate(d):
        if x < MIN_SHOW:
            print(f"   ⚠ [{i}번] {x:.2f}s \"{ch[i]['ko']}\" — 자동 처리 안 함. 의미 단위로 이웃과 직접 묶는다")
    print(f"{'✅' if ok else '❌'} 재배치 {changed}회 — 블록 {len(ch)}, 최단 {min(d):.2f}s, "
          f"1초 미만 {ratio * 100:.0f}% (목표 최단 >= {MIN_SHOW}s, 비율 <= {TARGET_RATIO * 100:.0f}%)")
    if not a.dry_run and changed:
        json.dump(ch, open(a.chunks, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
