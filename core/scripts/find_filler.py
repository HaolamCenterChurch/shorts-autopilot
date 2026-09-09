#!/usr/bin/env python3
"""전사에 안 잡혔는데 소리는 있는 구간(간투사·말더듬)을 찾는다.

words.json 의 토큰 사이 갭 중, 파형 에너지가 살아있는 곳 = 글자로 안 옮겨진 발화.
master 시간과 원본(src) 시간을 함께 찍어준다.
"""
import json, sys, wave, array, math

base = sys.argv[1]                      # .../_work/{slug}
GAP_MIN   = float(sys.argv[2]) if len(sys.argv) > 2 else 0.22   # 이 이상 벌어진 곳만
VOICE_DB  = -42.0                       # 이보다 크면 "소리 있음"
MIN_VOICE = 0.30                        # A안(오너 승인본) 최대 오탐 0.24s → 0.30 이 기준선

words = json.load(open(base + "_master.words.json"))
cuts  = json.load(open(base + "_master.mov.cuts.json"))["keeps"]
segs  = json.load(open(base + "_raw.mov.segmap.json"))["segments"]

def m2r(t):
    acc = 0.0
    for a, b in cuts:
        if t < acc + (b - a) - 1e-9: return a + (t - acc)
        acc += b - a
    return cuts[-1][1]

def r2s(t):
    for g in segs:
        if g["dst_start"] - 1e-9 <= t <= g["dst_end"] + 1e-9:
            return g["src_start"] + (t - g["dst_start"])
    return None

w = wave.open(base + "_master.wav")
sr = w.getframerate(); n = w.getnframes()
w.setpos(0)
pcm = array.array("h", w.readframes(n))
HOP = int(0.02 * sr)

def frames_db(t0, t1):
    i0, i1 = max(0, int(t0 * sr)), min(len(pcm), int(t1 * sr))
    out = []
    for i in range(i0, i1 - HOP, HOP):
        fr = pcm[i:i + HOP]
        rms = math.sqrt(sum(x * x for x in fr) / len(fr) + 1e-9)
        out.append((i / sr, 20 * math.log10(rms / 32768 + 1e-12)))
    return out

# 실제 글자가 있는 토큰만 (구두점 제외)
toks = [x for x in words if any(c.isalnum() for c in x["text"])]

hits = []
for a, b in zip(toks, toks[1:]):
    gap = b["t0"] - a["t1"]
    if gap < GAP_MIN: continue
    fr = frames_db(a["t1"], b["t0"])
    if not fr: continue
    # 갭 안에서 연속으로 소리가 나는 최장 구간
    run = best = 0.0; bs = None; cs = None
    for t, db in fr:
        if db > VOICE_DB:
            if cs is None: cs = t
            run += 0.02
            if run > best: best, bs = run, cs
        else:
            run = 0.0; cs = None
    if best >= MIN_VOICE:
        hits.append((a["t1"], b["t0"], best, bs, a["text"], b["text"]))

print(f"토큰 {len(toks)}개 · 갭 {GAP_MIN}s 이상 검사 → 소리 있는 갭 {len(hits)}건\n")
if not hits:
    print("  간투사 후보 없음 ✅")
for t0, t1, dur, vs, pa, pb in hits:
    s0, s1 = r2s(m2r(t0)), r2s(m2r(t1))
    print(f"  master {t0:7.2f}~{t1:7.2f} (갭 {t1-t0:.2f}s, 소리 {dur:.2f}s)")
    print(f"    앞말 …{pa!r}  뒷말 {pb!r}…")
    if s0 and s1: print(f"    src   {s0:9.3f} ~ {s1:9.3f}")
