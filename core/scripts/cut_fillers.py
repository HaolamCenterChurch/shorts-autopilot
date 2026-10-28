#!/usr/bin/env python3
"""반복 발화·말더듬 구간을 원본 절대시간으로 역매핑해 plan.segments 에서 잘라낸다.

Stage A 가 끝난 뒤(= master.words.json 이 있는 상태) 쓴다.
  1) --list   master 전사 글자 스트림을 색인과 함께 출력한다 (반복·더듬 눈으로 찾기)
  2) --find   부분 문자열의 글자 색인 구간을 찾아준다
  3) --cut    글자 색인 구간(i:j)들을 master 시간 ➔ raw ➔ src 로 역매핑해
              plan.segments 를 쪼개고 plan 파일을 갱신한다
  4) --cut-sec master 기준 초 구간(a:b). 전사문에 안 적히는 소리(조각 발성·들숨·더듬은
              첫 음절)는 글자 색인이 없으므로 시간으로 자른다(P2)
  5) --gaps   master.wav 진폭에서 발화 중 무음 갭(G-H 상한 초과)과 첫 발화 앞 잡음을
              자동으로 찾아 컷 목록에 넣는다. preserve 구간과 겹치는 갭은 건드리지 않는다

★ 잘라낸 뒤 0.35초 미만의 조각 세그먼트가 남으면 Whisper DTW 가 죽는다
  (WHISPER_ASSERT: filter_width < a->ne[2], SIGABRT). 조각이 생기면 거부하고 알려준다.

예)
  python3 scripts/shorts_v2/cut_fillers.py --work "{영상폴더}/v2/out/_work" --slug "{slug}" --list
  python3 scripts/shorts_v2/cut_fillers.py --work ... --slug ... --find 그래서제가 그런글을
  python3 scripts/shorts_v2/cut_fillers.py --work ... --slug ... \
      --plan "{영상폴더}/plans/plan_F.json" --cut 36:44 54:61 207:209
"""
import argparse
import json
import os
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_output import SILENCE_DB, TH_MAX_GAP, GAP_HOP  # noqa: E402  G-H 기준과 같은 잣대(P4)

MIN_FRAG = 0.35   # 이보다 짧은 조각 세그먼트는 만들지 않는다 (whisper DTW 크래시 방지)
MIN_KEEP = 0.12   # 이보다 짧은 잔여물은 조용히 버린다
GAP_LEAVE = 0.08  # 무음 갭을 자를 때 양쪽에 남기는 자연스러운 숨 (초)
HEAD_DB = -40.0   # 첫 발화 앞에서 이보다 큰 덩어리는 잡음으로 본다
HEAD_LEAD = 0.06  # 첫 단어 앞에 남길 여유 (초)


def find_gaps(work, slug, words, min_gap=None):
    """master.wav 에서 자를 구간(master 초) 목록. 발화 중 갭 + 첫 발화 앞 잡음."""
    with wave.open(os.path.join(work, f"{slug}_master.wav")) as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    if ch > 1:
        a = a.reshape(-1, ch).mean(1)
    hop = int(sr * GAP_HOP)   # G-H 와 같은 창 크기 — 잣대가 다르면 경계값에서 판정이 엇갈린다(P4)
    f = a[:len(a) // hop * hop].reshape(-1, hop)
    db = 20 * np.log10(np.sqrt((f ** 2).mean(1)) + 1e-9)
    quiet = db < SILENCE_DB
    n = len(quiet)
    cuts = []
    t_first = words[0]["t0"] if words else 0.0
    t_last = words[-1]["t1"] if words else n * 0.010
    # 첫 발화 앞: 잡음 덩어리가 있으면 0 ~ (첫 단어 - 여유) 를 통째로 자른다
    head_end = int(max(0.0, t_first - HEAD_LEAD) / GAP_HOP)
    if head_end > 0 and (db[:head_end] > HEAD_DB).any():
        cuts.append((0.0, head_end * GAP_HOP, "첫 발화 앞 잡음"))
    i = 0
    while i < n:
        if not quiet[i]:
            i += 1
            continue
        j = i
        while j < n and quiet[j]:
            j += 1
        g0, g1 = i * GAP_HOP, j * GAP_HOP
        if g0 > t_first and g1 < t_last and (g1 - g0) > (TH_MAX_GAP if min_gap is None else min_gap):
            cuts.append((g0 + GAP_LEAVE, g1 - GAP_LEAVE, f"무음 {g1 - g0:.2f}s"))
        i = j
    return cuts


def load(work, slug):
    base = os.path.join(work, slug)
    words = json.load(open(f"{base}_master.words.json", encoding="utf-8"))
    keeps = json.load(open(f"{base}_master.mov.cuts.json", encoding="utf-8"))["keeps"]
    segmap = json.load(open(f"{base}_raw.mov.segmap.json", encoding="utf-8"))["segments"]
    return words, keeps, segmap


def m2r(t, keeps):
    """master(무음 제거본) 시간 ➔ raw(구간 이어붙인 것) 시간"""
    acc = 0.0
    for a, b in keeps:
        if t < acc + (b - a) - 1e-9:
            return a + (t - acc)
        acc += b - a
    return keeps[-1][1]


def r2s(t, segmap):
    """raw 시간 ➔ src(원본 영상 절대시간)"""
    for g in segmap:
        if g["dst_start"] - 1e-9 <= t <= g["dst_end"] + 1e-9:
            return g["src_start"] + (t - g["dst_start"])
    return None


def stutter_suspects(txt, max_len=8, max_between=4):
    """2026-09-17: '용암불이 막 용암이' 같은 재시작이 --list 에 그대로 보였는데 통독에서 놓쳤다.
    같은 글자열(2글자 이상)이 끝난 뒤 max_between 글자 안에 다시 시작하면 후보로 낸다. 판정은 사람이 한다."""
    out = []
    for i in range(len(txt)):
        for n in range(max_len, 1, -1):
            rep = txt[i:i + n]
            if len(rep) < n or not rep.isalnum():
                continue
            k = txt.find(rep, i + n, i + n + max_between + n)
            if k >= 0:
                if not out or i >= out[-1][1]:
                    out.append((i, k, rep))
                break
    return out


def main():
    ap = argparse.ArgumentParser(description="반복·말더듬 구간 컷")
    ap.add_argument("--work", required=True, help="{영상폴더}/v2/out/_work")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--plan", help="--cut 에 필요한 plan_{ID}.json 경로")
    ap.add_argument("--list", action="store_true", help="글자 스트림을 색인과 함께 출력")
    ap.add_argument("--find", nargs="*", default=[], help="부분 문자열의 색인 구간 찾기")
    ap.add_argument("--cut", nargs="*", default=[], help="잘라낼 글자 색인 구간 i:j")
    ap.add_argument("--cut-sec", nargs="*", default=[], help="잘라낼 master 초 구간 a:b")
    ap.add_argument("--gaps", action="store_true", help="G-H 초과 무음 갭과 첫 발화 앞 잡음을 자동 컷")
    ap.add_argument("--gaps-min", type=float, default=None,
                    help="--gaps 가 자를 최소 갭 길이(초). 기본은 G-H 단일 상한. "
                         "개별 갭은 통과했는데 총합이 넘칠 때만 낮춘다(P1)")
    ap.add_argument("--dry-run", action="store_true", help="plan 파일을 쓰지 않고 결과만 출력")
    args = ap.parse_args()

    words, keeps, segmap = load(args.work, args.slug)
    txt = "".join(w["text"] for w in words)

    if args.list:
        for k in range(0, len(txt), 40):
            print(f"{k:5d} {txt[k:k + 40]}")
        sus = stutter_suspects(txt)
        print(f"\n더듬·재시작 의심 {len(sus)}곳 (같은 글자열이 4글자 안에 다시 나옴 — 귀로 확인 후 --cut/--cut-sec):")
        for i, j, rep in sus:
            print(f"  {i}:{j}  …{txt[max(0, i - 8):i]}[{txt[i:j]}]{txt[j:j + 8]}…  (반복 '{rep}')")
        return

    if args.find:
        for pat in args.find:
            i = 0
            hit = 0
            while True:
                j = txt.find(pat, i)
                if j < 0:
                    break
                hit += 1
                print(f"{pat!r} -> {j}:{j + len(pat)}"
                      f"   …{txt[max(0, j - 14):j]}[{pat}]{txt[j + len(pat):j + len(pat) + 14]}…")
                i = j + 1
            if not hit:
                print(f"{pat!r} -> 없음")
        return

    if not (args.cut or args.cut_sec or args.gaps):
        ap.error("--list, --find, --cut, --cut-sec, --gaps 중 하나는 있어야 한다")
    if not args.plan:
        ap.error("컷에는 --plan 이 필요하다")

    kill = []
    plan_pres = json.load(open(args.plan, encoding="utf-8")).get("preserve", [])
    sec_specs = []
    for spec in args.cut_sec:
        a, b = (float(x) for x in spec.split(":"))
        sec_specs.append((a, b, "지정"))
    if args.gaps:
        sec_specs.extend(find_gaps(args.work, args.slug, words, args.gaps_min))
    for m0, m1, why in sorted(sec_specs):
        if m1 - m0 < 0.02:
            continue
        s0, s1 = r2s(m2r(m0, keeps), segmap), r2s(m2r(m1, keeps), segmap)
        if s0 is None or s1 is None or s1 <= s0:
            print(f"  ⚠ 매핑 실패 master {m0:.2f}~{m1:.2f} — 건너뛴다")
            continue
        if any(not (s1 <= pa or s0 >= pb) for pa, pb in plan_pres):
            print(f"  · 보존 master {m0:.2f}~{m1:.2f} ({why}) — preserve 구간과 겹친다")
            continue
        # 서로 다른 세그먼트 경계를 가로지르면 src 에서 엉뚱한 긴 구간이 된다
        if s1 - s0 > (m1 - m0) + 0.05:
            print(f"  · 보존 master {m0:.2f}~{m1:.2f} ({why}) — 세그먼트 이음매를 가로지른다")
            continue
        kill.append((s0, s1))
        print(f"  컷 [{why}]  master {m0:.2f}~{m1:.2f} → src {s0:.3f}~{s1:.3f} ({s1 - s0:.2f}s)")

    for spec in args.cut:
        i, j = (int(x) for x in spec.split(":"))
        m0 = words[i]["t0"]
        # 다음 단어 직전까지 잘라 붙어 있는 뜸까지 함께 없앤다
        m1 = max(words[j - 1]["t1"], words[j]["t0"] - 0.03) if j < len(words) else words[j - 1]["t1"]
        s0, s1 = r2s(m2r(m0, keeps), segmap), r2s(m2r(m1, keeps), segmap)
        if s0 is None or s1 is None or s1 <= s0:
            print(f"  ⚠ 매핑 실패 {spec} — 건너뛴다")
            continue
        kill.append((s0 - 0.01, s1 - 0.03))
        print(f"  컷 {txt[i:j]!r}  master {m0:.2f}~{m1:.2f} → src {s0:.3f}~{s1:.3f} ({s1 - s0:.2f}s)")

    plan = json.load(open(args.plan, encoding="utf-8"))
    out = []
    for a, b in plan["segments"]:
        cur = [(a, b)]
        for k0, k1 in kill:
            nxt = []
            for x, y in cur:
                if k1 <= x or k0 >= y:
                    nxt.append((x, y))
                    continue
                if k0 > x:
                    nxt.append((x, min(k0, y)))
                if k1 < y:
                    nxt.append((max(k1, x), y))
            cur = nxt
        out.extend(cur)

    frag = [(a, b) for a, b in out if MIN_KEEP < b - a < MIN_FRAG]
    if frag:
        print("\n❌ 0.35초 미만 조각 세그먼트가 생긴다 — 이대로 Stage A 를 돌리면 whisper DTW 가 죽는다:")
        for a, b in frag:
            print(f"   {a:.3f}~{b:.3f} ({b - a:.2f}s)")
        print("   컷 구간을 넓혀 조각을 흡수하거나, 그 컷을 포기한다. plan 은 건드리지 않았다.")
        raise SystemExit(1)

    out = [(round(a, 3), round(b, 3)) for a, b in out if b - a > MIN_KEEP]
    pres = [[a, b] for a, b in plan.get("preserve", [])
            if any(s <= a and b <= e for s, e in out)]
    total = sum(b - a for a, b in out)
    print(f"\n  → 세그먼트 {len(out)}개, 총 {total:.1f}s, preserve {len(pres)}개")
    if args.dry_run:
        print("  (dry-run: plan 파일을 쓰지 않았다)")
        return
    plan["segments"] = [[a, b] for a, b in out]
    plan["preserve"] = pres
    with open(args.plan, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    print(f"  {args.plan} 갱신 — Stage A 부터 다시 돌리고, chunks 도 새 단어 스트림으로 다시 쓴다")


if __name__ == "__main__":
    main()
