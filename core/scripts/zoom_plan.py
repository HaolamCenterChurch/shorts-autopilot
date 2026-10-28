#!/usr/bin/env python3
"""하이라이트 자막(hl) → 줌 구간 계획 (effects.json 의 zooms).

오너 규칙 (2026-09-15, 테슈바 32일차 A편 피드백):
  1. 줌인했으면 그 발화(문장)가 끝나는 자막 경계까지 유지한다. 그 전에 장면 이음매(컷)가
     오면 그 컷에서 뺀다. 발화 중간에 빼면 어지럽다.
  2. 강조에 강조가 이어지면(줌 유지 중 새 문장의 hl) 한 단계 더 들어간다 (1.14 → 1.28).
  3. 줌을 켜고 빼고 단계를 바꾸는 순간은 가까운 이음매 프레임에 맞춘다 —
     같은 장면을 이어 붙인 점프컷이 줌 변화에 가려져 튀어 보이지 않는다.
     가까운 이음매가 없으면 ±0.35초 안의 무음 컷 점프에 맞춘다 — 강조 문장 앞 뜸을
     무음 제거가 잘라낸 점프 직후에 줌이 또 켜지면 두 번 연달아 튄다(B편 15.33→15.50 실측).
  4. 줌이 빠졌다가 BRIDGE 초 안에 다시 들어가면 빼지 않고 잇는다.

출력: [[t0, t1, scale], ...] — 시간순, 서로 겹치지 않는 조각. 경계는 프레임 단위로 맞춘다.
미리보기: python3 zoom_plan.py --workdir {_work} --slug {slug} --chunks chunks_X.json
"""
import argparse
import json
import os
import sys

FPS = 30
LEVELS = {1: 1.14, 2: 1.28}
MAX_HOLD = 4.0            # hl 블록 끝에서 문장 끝을 찾아 늘리는 최대 길이(초)
MIN_PIECE = 0.8           # 줌 시작 후 이보다 일찍 오는 컷에서는 빼지 않는다
BRIDGE = 0.8              # 줌이 빠진 뒤 이 안에 다시 들어가면 잇는다
# (앞, 뒤) 허용 초 — 이음매를 먼저 찾고, 없으면 무음 컷 점프
SNAP_START = (1.0, 0.25)
SNAP_END = (0.3, 0.6)
SNAP_STEP = (0.4, 0.4)
JUMP_START = (0.35, 0.15)
JUMP_END = (0.1, 0.35)     # 줌 끝은 무음 컷 쪽으로 앞당기지 않는다 — 말 끝나기 전에 빠지면 안 된다
JUMP_STEP = (0.3, 0.3)
MIN_LEN = 0.4             # 스냅 후 이보다 짧아진 조각은 버린다
SENTENCE_END = set("다요죠까니냐야네마?!.")
EPS = 1e-3


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def ends_sentence(ko):
    t = (ko or "").strip().rstrip("\"'”’)」")
    if not t or t[-1] not in SENTENCE_END:
        return False
    # "사랑하니까" 는 이어지는 말, "됩니까·하십니까" 는 물음 — 같은 '까' 로 끝나 갈라 본다
    if t.endswith("니까") and not t.endswith(("습니까", "ㅂ니까", "십니까", "입니까", "됩니까", "합니까", "갑니까", "옵니까", "봅니까")):
        return False
    return True


def plan_zooms(chunks, spans, cuts, duration, jumps=None):
    """chunks: [{ko, hl}], spans: chunk 별 (start, end) 또는 (None, None),
    cuts: 마스터 타임라인의 장면 이음매 시각, jumps: 무음 컷 점프 시각, duration: 마스터 길이."""
    n = len(chunks)
    cuts = sorted(c for c in cuts if 0.1 < c < duration - 0.1)
    jumps = sorted(j for j in (jumps or []) if 0.1 < j < duration - 0.1
                   and all(abs(j - c) > 2.5 / FPS for c in cuts))

    def start_of(i):
        return spans[i][0] if spans[i] and spans[i][0] is not None else None

    def block_end(i):
        # 자막 블록은 다음 블록이 뜰 때까지 화면에 남는다 — 줌도 그 경계에서 뺀다
        for k in range(i + 1, n):
            s = start_of(k)
            if s is not None:
                return s
        e = spans[i][1] if spans[i] and spans[i][1] is not None else duration
        return min(duration, e + 0.3)

    pieces = []  # [t0, t1, level]
    for i, ch in enumerate(chunks):
        if not ch.get("hl") or start_of(i) is None:
            continue
        start = start_of(i)
        hl_end = block_end(i)
        j = i
        while (not ends_sentence(chunks[j].get("ko")) and j + 1 < n
               and start_of(j + 1) is not None
               and block_end(j + 1) - hl_end <= MAX_HOLD):
            j += 1
        end = block_end(j)
        for c in cuts:
            if start + MIN_PIECE <= c < end:
                end = c
                break
        new_sentence = i == 0 or ends_sentence(chunks[i - 1].get("ko"))
        if pieces and start <= pieces[-1][1] + BRIDGE:
            last = pieces[-1]
            if new_sentence and last[2] == 1 and start - last[0] >= MIN_PIECE:
                prev_end = last[1]
                last[1] = start
                pieces.append([start, max(end, prev_end), 2])
            else:
                last[1] = max(last[1], end)
        else:
            pieces.append([start, end, 1])

    def nearest(t, win, lo, hi, pts):
        best = None
        for c in pts:
            if t - win[0] - EPS <= c <= t + win[1] + EPS and lo < c < hi:
                if best is None or abs(c - t) < abs(best - t):
                    best = c
        return best

    def snap(t, cut_win, jump_win, lo, hi):
        c = nearest(t, cut_win, lo, hi, cuts)
        return c if c is not None else nearest(t, jump_win, lo, hi, jumps)

    for k, p in enumerate(pieces):
        prev = pieces[k - 1] if k > 0 else None
        if prev is not None and abs(prev[1] - p[0]) < 1e-6:
            c = snap(p[0], SNAP_STEP, JUMP_STEP, prev[0] + MIN_LEN, p[1] - MIN_LEN)
            if c is not None:
                prev[1] = p[0] = c
        else:
            c = snap(p[0], SNAP_START, JUMP_START, prev[1] if prev else 0.0, p[1] - MIN_LEN)
            if c is not None:
                p[0] = c
    for k, p in enumerate(pieces):
        nxt = pieces[k + 1] if k + 1 < len(pieces) else None
        if nxt is not None and abs(nxt[0] - p[1]) < 1e-6:
            continue
        c = snap(p[1], SNAP_END, JUMP_END, p[0] + MIN_LEN, nxt[0] if nxt else duration)
        if c is not None:
            p[1] = c

    out = []
    for t0, t1, lv in pieces:
        t0, t1 = round(t0 * FPS) / FPS, round(t1 * FPS) / FPS
        if t1 - t0 >= MIN_LEN:
            out.append([round(t0, 4), round(t1, 4), LEVELS[lv]])
    return out


def _raw_to_master(raw_times, keeps):
    mapped = []
    for rc in raw_times:
        if not keeps:
            mapped.append(rc)
            continue
        acc = 0.0
        for k0, k1 in keeps:
            if rc < k0:
                mapped.append(acc)
                break
            if rc <= k1:
                mapped.append(acc + rc - k0)
                break
            acc += k1 - k0
        else:
            mapped.append(acc)
    return mapped


def _snap(master_path, times):
    try:
        from track_crop import snap_cuts_to_visual
        times = snap_cuts_to_visual(master_path, times)
    except Exception as err:
        log(f"[zoom_plan] 화면 전환 프레임 맞춤 실패 — 계산 시각 그대로 사용: {err}")
    return sorted(c for c in times if c > 0.1)


def _keeps(master_path):
    cuts_path = master_path + ".cuts.json"
    if not os.path.exists(cuts_path):
        return None
    with open(cuts_path, "r", encoding="utf-8") as f:
        return json.load(f).get("keeps")


def master_shot_cuts(workdir, slug, master_path, plan=None):
    """세그먼트 이음매를 마스터(무음 제거 후) 시각으로 옮기고 실제 화면 전환 프레임에 맞춘다."""
    raw_cuts = []
    segmap_path = os.path.join(workdir, f"{slug}_raw.mov.segmap.json")
    if os.path.exists(segmap_path):
        with open(segmap_path, "r", encoding="utf-8") as f:
            raw_cuts = [s["dst_start"] for s in json.load(f)["segments"][1:]]
    elif plan:
        acc = 0.0
        for s, e in plan.get("segments", [])[:-1]:
            acc += e - s
            raw_cuts.append(acc)
    return _snap(master_path, _raw_to_master(raw_cuts, _keeps(master_path)))


def master_jump_points(master_path):
    """무음 제거로 이어 붙인 자리(같은 장면 안의 작은 점프)를 마스터 시각·실제 프레임으로."""
    keeps = _keeps(master_path)
    if not keeps:
        return []
    pts, acc = [], 0.0
    for (a0, a1), _ in zip(keeps, keeps[1:]):
        acc += a1 - a0
        pts.append(acc)
    return _snap(master_path, pts)


def main():
    ap = argparse.ArgumentParser(description="hl 자막 기반 줌 계획 미리보기")
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--chunks", required=True)
    args = ap.parse_args()
    from make_ass import match_chunks_to_words
    master = os.path.join(args.workdir, f"{args.slug}_master.mov")
    with open(os.path.join(args.workdir, f"{args.slug}_master.words.json"), encoding="utf-8") as f:
        words = json.load(f)
    with open(args.chunks, encoding="utf-8") as f:
        chunks = json.load(f)
    with open(master + ".cuts.json", encoding="utf-8") as f:
        duration = json.load(f)["duration"]
    spans = match_chunks_to_words(words, chunks)
    cuts = master_shot_cuts(args.workdir, args.slug, master)
    jumps = master_jump_points(master)
    print("이음매:", [round(c, 3) for c in cuts])
    print("무음컷:", [round(c, 3) for c in jumps])
    for ch, (s, e) in zip(chunks, spans):
        if ch.get("hl") and s is not None:
            print(f"  hl {s:6.2f}-{e:6.2f} {ch['ko']}")
    for z in plan_zooms(chunks, spans, cuts, duration, jumps):
        print(f"줌 {z[0]:6.2f}~{z[1]:6.2f} ×{z[2]}")


if __name__ == "__main__":
    main()
