#!/usr/bin/env python3
"""완성본 게이트 검사기 — 텍스트가 아니라 '시간'과 '움직임'을 잰다.

2026-09-05 사고의 교훈: 기존 게이트(check_chunks 글자수 일치, verify_output 텍스트
유사도, track_crop 검출률)는 전부 통과했는데 자막이 4.46초 떠 있었고 카메라는 얼어
있었다. 결함의 단위가 '초'와 '픽셀'인데 재는 단위가 '글자'와 '검출률'이었기 때문이다.
이 스크립트가 그 빈 자리를 메운다.

검사 항목
  G-A 첫 자막 리드   첫 자막이 뜨고 실제 말이 시작될 때까지의 대기 시간
  G-B 블록 정렬      블록마다 자막이 뜬 뒤 말이 시작될 때까지의 대기 시간 분포
  G-C 표시 시간      블록이 화면에 머무는 시간 (읽을 수 있는가)
  G-D 카메라 모션    크롭 좌표가 실제로 움직이는 프레임 비율 (동결 탐지)
  G-E 산출 규칙      디렉토리·슬러그·썸네일 4종·유사도
  G-F 렌더 일치      완성본 MP4 길이가 지금 ASS 와 맞는가 (묵은 렌더 탐지)

사용법
  {venv}/python3 check_output.py --outdir "{영상폴더}/v2/out" --slug "A_한글제목"
  경로를 직접 주려면 --ass/--wav/--track/--mp4/--verify 로 덮어쓴다.

종료 코드 0=통과, 1=실패. 실패 항목은 ❌ 로 표시된다.
"""
import argparse
import json
import os
import re
import sys
import wave

import numpy as np

# ── 합격 기준 (2026-09-06 실측 보정) ────────────────────────────────
# 근거: 2026-09-05 3편 실측. C안(동결·자막 4.46초 선행)은 떨어지고
#       A안·B안은 붙도록 맞췄다. 완화할 때는 반드시 실측을 다시 붙일 것.
TH_HEAD_LEAD = 0.30        # G-A 첫 자막 리드 상한(초)
TH_BLOCK_WAIT_MAX = 1.00   # G-B 블록 대기 최대치 상한(초)
TH_BLOCK_WAIT_RATIO = 0.15 # G-B 0.5초 넘게 기다리는 블록 비율 상한
TH_MIN_DUR = 0.40          # G-C 블록 최단 표시 시간 하한(초)
TH_SHORT_RATIO = 0.25      # G-C 1.0초 미만 블록 비율 상한
TH_MOVE_FAIL = 0.10        # G-D 이동 프레임 비율 하한 (미만이면 실패)
TH_MOVE_WARN = 0.20        # G-D 이 아래면 경고
MOVE_PX_PER_SEC = 5.0      # G-D '움직였다'로 볼 최소 속도
TH_SIMILARITY = 95.0       # G-E 재전사 유사도 하한(%)
# G-F: 완성본 = 마스터 + 앞 인트로(0.7s 정지 + 0.25s 페이드) + 뒤 여유. 오차 허용치.
INTRO_MIN, INTRO_MAX = 0.0, 1.60
# 근거(2026-09-06): generate_thumbnail.py 는 `_work/{slug}_raw.mp4` 백업이 남아 있으면
# 새로 렌더한 mp4 대신 그 백업으로 인트로를 붙인다. 그래서 재렌더가 통째로 버려지고도
# ASS·words 기반 게이트는 전부 통과했다. 실제 파일을 재는 항목이 반드시 필요하다.

THUMBS = ["_thumb_youtube.jpg", "_thumb_instagram.jpg",
          "_thumb_instagram_guide.jpg", "_thumb.jpg"]


def voiced_track(wav_path):
    """10ms 홉 RMS 로 발화/무음 트랙을 만든다. silence_cut.py 와 같은 임계값 규칙."""
    with wave.open(wav_path) as w:
        sr = w.getframerate()
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    a = a.astype(np.float32) / 32768.0
    hop, win = int(sr * 0.010), int(sr * 0.025)
    m = (len(a) - win) // hop
    if m <= 0:
        raise ValueError(f"오디오가 너무 짧다: {wav_path}")
    rms = np.sqrt(np.array([np.mean(a[i * hop:i * hop + win] ** 2) for i in range(m)])) + 1e-9
    db = 20 * np.log10(rms)
    floor, speech = np.percentile(db, 5), np.percentile(db, 85)
    thr = min(max(floor + 6, speech - 18), speech - 12)
    return db > thr


def first_speech(voiced, min_run=10):
    """0.1초(100ms) 이상 연속으로 소리가 이어지는 첫 지점(초). 산발적 잡음(10~20ms) 배제 및 단음절(100~150ms) 감지."""
    cnt = 0
    for i, v in enumerate(voiced):
        cnt = cnt + 1 if v else 0
        if cnt >= min_run:
            return (i - min_run + 1) * 0.010
    return None


def parse_ass(ass_path):
    """(시작, 끝, 표시문자열) 목록. 스타일 태그는 벗긴다."""
    out = []
    with open(ass_path, encoding="utf-8") as f:
        for line in f:
            if not line.startswith("Dialogue:"):
                continue
            p = line.split(",", 9)

            def sec(t):
                h, m, s = t.split(":")
                return int(h) * 3600 + int(m) * 60 + float(s)

            text = re.sub(r"\{[^}]*\}", "", p[9]).replace("\\N", " ").strip()
            # ★글자가 없는 블록은 화면에 아무것도 안 뜨는 '꼬리 자리표'다.
            #   말이 끝난 뒤 페이드로 사라지는 구간을 덮으려고 make_ass 가 만든다.
            #   읽을 게 없으니 표시시간·대기시간 검사 대상이 아니다(2026-09-06).
            if not text:
                continue
            out.append((sec(p[1]), sec(p[2]), text))
    return out


def wait_after(voiced, t, cap=1.5):
    """t 초에 자막이 떴을 때 실제 말이 시작될 때까지 기다리는 시간(초)."""
    i = int(round(t / 0.010))
    if i >= len(voiced):
        return 0.0
    if voiced[i]:
        return 0.0
    j, lim = i, int(cap / 0.010)
    while j < len(voiced) and not voiced[j] and (j - i) < lim:
        j += 1
    return (j - i) * 0.010


class Report:
    def __init__(self):
        self.rows = []
        self.failed = False

    def add(self, gate, name, value, criterion, ok, warn=False):
        mark = "✅" if ok else ("⚠️" if warn else "❌")
        if not ok and not warn:
            self.failed = True
        self.rows.append((mark, gate, name, value, criterion))

    def render(self, title):
        print(f"\n=== {title} ===")
        w = max(len(r[2]) for r in self.rows)
        for mark, gate, name, value, crit in self.rows:
            print(f"  {mark} [{gate}] {name:<{w}}  {value:<22} 기준: {crit}")


def main():
    ap = argparse.ArgumentParser(description="완성본 시간·모션 게이트 검사기")
    ap.add_argument("--outdir", required=True, help="산출 디렉토리 ({영상폴더}/v2/out)")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--ass", default=None)
    ap.add_argument("--wav", default=None)
    ap.add_argument("--track", default=None)
    ap.add_argument("--mp4", default=None)
    ap.add_argument("--verify", default=None)
    ap.add_argument("--skip-naming", action="store_true",
                    help="G-E 디렉토리·파일명 검사를 건너뛴다(옛 산출물 점검용)")
    args = ap.parse_args()

    slug, outdir = args.slug, args.outdir
    work = os.path.join(outdir, "_work")
    ass = args.ass or os.path.join(work, f"{slug}.ass")
    wav = args.wav or os.path.join(work, f"{slug}_master.wav")
    track = args.track or os.path.join(work, f"{slug}_track.json")
    mp4 = args.mp4 or os.path.join(outdir, f"{slug}.mp4")
    verify = args.verify or os.path.join(outdir, f"{slug}_verify.md")

    for label, path in [("ASS", ass), ("마스터 WAV", wav), ("트랙 JSON", track)]:
        if not os.path.exists(path):
            print(f"❌ {label} 를 찾을 수 없다: {path}")
            return 2

    rep = Report()
    voiced = voiced_track(wav)
    blocks = parse_ass(ass)

    # ── G-A 첫 자막 리드 ───────────────────────────────────────────
    fs = first_speech(voiced)
    head_lead = (fs - blocks[0][0]) if (fs is not None and blocks) else 0.0
    head_lead = max(0.0, head_lead)
    rep.add("G-A", "첫 자막 리드",
            f"{head_lead:.2f}s (첫 발화 {fs:.2f}s)",
            f"<= {TH_HEAD_LEAD:.2f}s", head_lead <= TH_HEAD_LEAD)

    # ── G-B 블록 정렬 ─────────────────────────────────────────────
    waits = np.array([wait_after(voiced, s) for s, _, _ in blocks[1:]]) if len(blocks) > 1 else np.array([0.0])
    over = float(np.mean(waits > 0.5))
    rep.add("G-B", "블록 대기 최대", f"{waits.max():.2f}s (중앙 {np.median(waits):.2f}s)",
            f"<= {TH_BLOCK_WAIT_MAX:.2f}s", waits.max() <= TH_BLOCK_WAIT_MAX)
    rep.add("G-B", "0.5초 초과 비율", f"{100*over:.0f}% ({int((waits>0.5).sum())}/{len(waits)})",
            f"<= {100*TH_BLOCK_WAIT_RATIO:.0f}%", over <= TH_BLOCK_WAIT_RATIO)

    # ── G-C 표시 시간 ─────────────────────────────────────────────
    durs = np.array([e - s for s, e, _ in blocks])
    short = float(np.mean(durs < 1.0))
    worst = min(blocks, key=lambda b: b[1] - b[0])
    rep.add("G-C", "블록 최단 표시", f"{durs.min():.2f}s ({worst[2][:14]})",
            f">= {TH_MIN_DUR:.2f}s", durs.min() >= TH_MIN_DUR)
    rep.add("G-C", "1초 미만 비율", f"{100*short:.0f}% ({int((durs<1.0).sum())}/{len(durs)})",
            f"<= {100*TH_SHORT_RATIO:.0f}%", short <= TH_SHORT_RATIO)

    # ── G-D 카메라 모션 ───────────────────────────────────────────
    tj = json.load(open(track, encoding="utf-8"))
    xs, fps = tj["x"], tj.get("fps", 30)
    speed = np.abs(np.diff(np.asarray(xs, dtype=float))) * fps
    ratio = float(np.mean(speed > MOVE_PX_PER_SEC)) if len(speed) else 0.0
    span = (max(xs) - min(xs)) if xs else 0
    ok_d, warn_d = ratio >= TH_MOVE_FAIL, (TH_MOVE_FAIL <= ratio < TH_MOVE_WARN)
    rep.add("G-D", "카메라 이동 프레임", f"{100*ratio:.0f}% (이동폭 {span}px)",
            f">= {100*TH_MOVE_FAIL:.0f}% (권장 {100*TH_MOVE_WARN:.0f}%)",
            ok_d and not warn_d, warn=warn_d)

    # ── G-E 산출 규칙 ─────────────────────────────────────────────
    if not args.skip_naming:
        ok_dir = os.path.normpath(outdir).endswith(os.path.join("v2", "out"))
        rep.add("G-E", "출력 디렉토리", outdir[-28:], "…/v2/out 으로 끝날 것", ok_dir)

        ok_slug = bool(re.match(r"^[A-Z]_[0-9A-Za-z가-힣_]+$", slug)) and re.search(r"[가-힣]", slug)
        rep.add("G-E", "슬러그 형식", slug[:22], "{ID}_{한글제목}", bool(ok_slug))

        missing = [t for t in THUMBS if not os.path.exists(os.path.join(outdir, slug + t))]
        rep.add("G-E", "썸네일 4종", f"{4-len(missing)}/4" + (f" 없음:{missing[0]}" if missing else ""),
                "4종 모두", not missing)

        rep.add("G-E", "완성본 MP4", os.path.basename(mp4)[:22] if os.path.exists(mp4) else "없음",
                "존재", os.path.exists(mp4))

        sim = None
        if os.path.exists(verify):
            m = re.search(r"유사도[:\s*]*\**\s*([0-9.]+)\s*%", open(verify, encoding="utf-8").read())
            if m:
                sim = float(m.group(1))
        # ── G-F 렌더 일치 ─────────────────────────────────────────
        if os.path.exists(mp4) and blocks:
            import subprocess
            dur = float(subprocess.run(
                ["/opt/homebrew/bin/ffprobe", "-v", "error", "-show_entries",
                 "format=duration", "-of", "csv=p=0", mp4],
                capture_output=True, text=True).stdout.strip())
            gap = dur - blocks[-1][1]
            rep.add("G-F", "MP4-ASS 길이차", f"{gap:+.2f}s (mp4 {dur:.2f}s)",
                    f"{INTRO_MIN:.1f}~{INTRO_MAX:.1f}s", INTRO_MIN <= gap <= INTRO_MAX)

        rep.add("G-E", "재전사 유사도", f"{sim:.2f}%" if sim is not None else "리포트 없음",
                f">= {TH_SIMILARITY}%", sim is not None and sim >= TH_SIMILARITY)

    rep.render(slug)
    if rep.failed:
        print("\n❌ 게이트 불합격 — 위 ❌ 항목을 고치기 전에는 오너에게 가져가지 않는다.")
        return 1
    print("\n✅ 게이트 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
