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


def normalize(t):
    return re.sub(r"[^0-9A-Za-z가-힣]", "", t or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--words", required=True)
    ap.add_argument("--chunks", required=True)
    args = ap.parse_args()

    words = json.load(open(args.words, encoding="utf-8"))
    chunks = json.load(open(args.chunks, encoding="utf-8"))

    stream = normalize("".join(w["text"] for w in words))
    joined = normalize("".join(c.get("stt") or c.get("ko", "") for c in chunks))

    if stream == joined:
        lens = [len(normalize(c.get("ko", ""))) for c in chunks]
        print(f"✅ 일치 ({len(stream)}자, 블록 {len(chunks)}개, "
              f"블록길이 {min(lens)}~{max(lens)}자)")
        long_blocks = [(i, c["ko"]) for i, c in enumerate(chunks)
                       if len(normalize(c.get("ko", ""))) > 18]
        for i, t in long_blocks:
            print(f"  ⚠ {i}번 블록이 김: {t}")
        return 0

    print(f"❌ 불일치 — 스트림 {len(stream)}자 vs 청크 {len(joined)}자")
    sm = difflib.SequenceMatcher(None, stream, joined)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        print(f"  [{tag}] whisper:{stream[max(0,i1-12):i2+12]!r}")
        print(f"        청크   :{joined[max(0,j1-12):j2+12]!r}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
