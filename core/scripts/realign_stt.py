#!/usr/bin/env python3
"""컷 뒤 Stage A 재전사로 글자 스트림이 흔들렸을 때 chunks 의 stt 만 새 스트림에 맞춘다.

오디오를 조금만 잘라도 Whisper 는 같은 말을 다르게 받아쓴다(띄어쓰기·어미·영문 표기·짧은 말 누락).
화면 표기(ko)는 그대로 두고 검증용 stt 만 새 전사 위치로 옮기면 check_chunks 스트림이 다시 맞는다.
옛 청크 경계를 difflib 로 새 스트림에 사상한다. 새 전사가 하나도 대응하지 않는 블록이 생기면
쓰지 않고 목록만 알린다 — 그 블록의 말이 실제로 잘렸는지(또는 간투사인지) 오디오로 확인할 신호다(P2).

  python3 scripts/shorts_v2/realign_stt.py \
      --words "{영상폴더}/v2/out/_work/{slug}_master.words.json" \
      --chunks "{영상폴더}/v2/out/_work/chunks_{ID}.json" [--dry-run]
"""
import argparse
import difflib
import json
import re
import sys


def norm(t):
    return re.sub(r"[^0-9A-Za-z가-힣]", "", re.sub(r"\\N", "", t or ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--words", required=True)
    ap.add_argument("--chunks", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    new = norm("".join(w["text"] for w in json.load(open(a.words, encoding="utf-8"))))
    ch = json.load(open(a.chunks, encoding="utf-8"))
    parts = [norm(c.get("stt") or c.get("ko")) for c in ch]
    old = "".join(parts)
    ops = difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes()

    def mp(x):
        for tag, i1, i2, j1, j2 in ops:
            if i1 <= x < i2 or (x == i2 and tag != "equal" and i1 == i2):
                if tag == "equal":
                    return j1 + (x - i1)
                return j1 + round((x - i1) * (j2 - j1) / max(1, i2 - i1))
        return len(new)

    bnd = [0]
    for p in parts:
        bnd.append(bnd[-1] + len(p))
    nb = [0] + [mp(b) for b in bnd[1:-1]] + [len(new)]
    for k in range(1, len(nb)):
        nb[k] = max(nb[k], nb[k - 1])

    empty = [(k, c["ko"]) for k, c in enumerate(ch) if not new[nb[k]:nb[k + 1]]]
    if empty:
        print("❌ 새 전사가 대응하지 않는 블록 — 쓰지 않았다. 오디오에서 그 말이 남아 있는지 확인한다:")
        for k, ko in empty:
            print(f"   [{k}번] {ko}")
        return 1

    changed = 0
    for k, c in enumerate(ch):
        seg = new[nb[k]:nb[k + 1]]
        if seg == norm(c["ko"]):
            c.pop("stt", None)
        elif c.get("stt") != seg:
            print(f"   [{k:3d}] ko={c['ko']!r}  stt → {seg!r}")
            c["stt"] = seg
            changed += 1
    assert "".join(norm(c.get("stt") or c["ko"]) for c in ch) == new
    if a.dry_run:
        print(f"(dry-run) stt 갱신 대상 {changed}블록")
        return 0
    json.dump(ch, open(a.chunks, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"✅ stt 재정렬 {changed}블록, 스트림 {len(new)}자 일치")
    return 0


if __name__ == "__main__":
    sys.exit(main())
