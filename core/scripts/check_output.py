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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from safe_area import safe_width  # noqa: E402  안전폭 정본

# 본문 자막 MarginV 정본. 2026-09-19 박스 자막 레이아웃(상단 400·하단 380 밴드, 자막은 영상 아래쪽 위)으로 360→440
BODY_MARGIN_V = 440

# ── 합격 기준 (2026-09-06 실측 보정) ────────────────────────────────
# 근거: 2026-09-05 3편 실측. C안(동결·자막 4.46초 선행)은 떨어지고
#       A안·B안은 붙도록 맞췄다. 완화할 때는 반드시 실측을 다시 붙일 것.
TH_HEAD_LEAD = 0.30        # G-A 첫 자막 리드 상한(초)
TH_BLOCK_WAIT_MAX = 1.00   # G-B 블록 대기 최대치 상한(초)
TH_BLOCK_WAIT_RATIO = 0.15 # G-B 0.5초 넘게 기다리는 블록 비율 상한
TH_MIN_DUR = 0.40          # G-C 블록 최소 표시 시간 (초, 숏폼 빠른 발화 반영)
TH_SHORT_RATIO = 0.35      # G-C 1.0초 미만 블록 비율 상한 (단독 1줄 숏폼 빠른 템포 반영)
TH_MOVE_FAIL = 0.05        # G-D 이동 프레임 비율 하한 (미만이면 실패)
TH_MOVE_WARN = 0.20        # G-D 이 아래면 경고
MOVE_PX_PER_SEC = 5.0      # G-D '움직였다'로 볼 최소 속도
TH_SIMILARITY = 95.0       # G-E 재전사 유사도 하한(%)
# G-F: 완성본 = 마스터 + 앞 인트로(0.7s 정지 + 0.25s 페이드) + 뒤 여유. 오차 허용치.
INTRO_MIN, INTRO_MAX = 0.0, 1.60
GAP_HOP = 0.020        # 무음 판정 RMS 창(초). cut_fillers --gaps 와 공유
SILENCE_DB = -42.0     # 무음 판정 절대 임계(dBFS). Gate 2 silencedetect 와 동일
TH_MAX_GAP = 0.35      # 발화 중 허용되는 단일 무음 갭 상한(초)
TH_CUT_EDGE_DB = 24.0  # G-I: 무음 컷으로 지운 쪽 첫·끝 0.1초가 발화(p90)보다 이만큼은 작아야 한다
TH_GAP_RATIO = 5.0     # 발화 중 무음 총합 상한(%)
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
    """(시작, 끝, 표시문자열, 스타일, 원본텍스트) 목록. 스타일 태그는 벗긴다."""
    out = []
    with open(ass_path, encoding="utf-8") as f:
        for line in f:
            if not line.startswith("Dialogue:"):
                continue
            p = line.split(",", 9)

            def sec(t):
                h, m, s = t.split(":")
                return int(h) * 3600 + int(m) * 60 + float(s)

            raw_txt = p[9].strip()
            style = p[3].strip()
            text = re.sub(r"\{[^}]*\}", "", raw_txt).replace("\\N", " ").strip()
            # ★글자가 없는 블록은 화면에 아무것도 안 뜨는 '꼬리 자리표'다.
            #   말이 끝난 뒤 페이드로 사라지는 구간을 덮으려고 make_ass 가 만든다.
            #   읽을 게 없으니 표시시간·대기시간 검사 대상이 아니다(2026-09-06).
            if not text:
                continue
            out.append((sec(p[1]), sec(p[2]), text, style, raw_txt))
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


def cut_edge_check(work, slug):
    """G-I: 무음 컷으로 지운 쪽 첫·끝 0.1초에 말소리가 남아 있었는지 raw(무음 컷 전) 진폭으로 잰다.
    오너 2026-10-03 "말이 끝나지 않았는데 자꾸 넘긴다". 재전사 유사도·G-H 는 작게 흐려진
    '~요/~다' 가 잘려 나간 것을 못 본다(P2). 반환: (기준 dB, 걸린 곳 목록)"""
    with open(os.path.join(work, f"{slug}_master.mov.cuts.json"), encoding="utf-8") as fh:
        keeps = json.load(fh)["keeps"]
    with wave.open(os.path.join(work, f"{slug}_raw.wav")) as w:
        sr = w.getframerate()
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    h = int(sr * 0.01)
    db = 20 * np.log10(np.sqrt((a[:len(a) // h * h].reshape(-1, h) ** 2).mean(1)) + 1e-9)
    # G-H 가 무음으로 세는 크기(SILENCE_DB) 이하는 잘라도 말끝 잘림이 아니다 — 두 게이트가 서로 반박하지 않게(P4)
    lim = max(np.percentile(db, 90) - TH_CUT_EDGE_DB, SILENCE_DB)
    bad = []
    for (_, a1), (b0, _) in zip(keeps, keeps[1:]):
        for t0, t1, kind in ((a1, a1 + 0.10, "말끝"), (b0 - 0.10, b0, "첫소리")):
            seg = db[int(t0 / 0.01):int(t1 / 0.01)]
            if len(seg) and seg.max() > lim:
                bad.append(f"{kind} raw {t0:.2f}s({seg.max():.0f}dB)")
    return lim, bad


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
    waits = np.array([wait_after(voiced, s) for s, *_ in blocks[1:]]) if len(blocks) > 1 else np.array([0.0])
    over = float(np.mean(waits > 0.5))
    rep.add("G-B", "블록 대기 최대", f"{waits.max():.2f}s (중앙 {np.median(waits):.2f}s)",
            f"<= {TH_BLOCK_WAIT_MAX:.2f}s", waits.max() <= TH_BLOCK_WAIT_MAX)
    rep.add("G-B", "0.5초 초과 비율", f"{100*over:.0f}% ({int((waits>0.5).sum())}/{len(waits)})",
            f"<= {100*TH_BLOCK_WAIT_RATIO:.0f}%", over <= TH_BLOCK_WAIT_RATIO)

    # ── G-C 표시 시간 ─────────────────────────────────────────────
    durs = np.array([e - s for s, e, *_ in blocks])
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

        ok_slug = bool(re.match(r"^[A-Z](?:-[0-9]+)?_[0-9A-Za-z가-힣_]+$", slug)) and re.search(r"[가-힣]", slug)
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

    # P3: 화면에 그려지는 스타일은 하나도 빼지 않고 잰다.
    #   스타일마다 폰트 크기·MarginLR 가 다르므로 안전폭도 다르다.
    #   여기서 특정 스타일을 빼면 그 스타일은 영구 사각지대가 된다.
    title_size, title_lr = 96, 50
    body_size, body_lr = 90, 30
    if os.path.exists(ass):
        with open(ass, encoding="utf-8") as f:
            for l in f:
                if l.startswith("Style: TopTitle,"):
                    pr = l.split(",")
                    try:
                        title_size = int(float(pr[2]))
                        title_lr = int(pr[-4].strip())
                    except Exception:
                        pass
                elif l.startswith("Style: Main,"):
                    pr = l.split(",")
                    try:
                        body_size = int(float(pr[2]))
                        body_lr = int(pr[-4].strip())
                    except Exception:
                        pass
    title_limit = safe_width(title_lr)
    body_limit = safe_width(body_lr)
    try:
        from PIL import ImageFont as _IF
        font_title = _IF.truetype("/System/Library/Fonts/AppleSDGothicNeo.ttc", title_size)
        font_measurer = _IF.truetype("/System/Library/Fonts/AppleSDGothicNeo.ttc", body_size)
    except Exception:
        font_title = None
        font_measurer = None

    def _width(cl, fnt, size):
        if fnt is not None:
            b = fnt.getbbox(cl)
            return b[2] - b[0]
        k = size / 90.0
        return int(sum((78 if "\uac00" <= ch <= "\ud7a3" else 25 if ch.isspace() else 48) * k
                       for ch in cl))

    max_title_w = 0
    title_violations = []
    for _, _, _, style, raw_t in blocks:
        if style != "TopTitle":
            continue
        for line in raw_t.replace("\\N", "\n").split("\n"):
            cl = re.sub(r"\{[^}]*\}", "", line).strip()
            if not cl:
                continue
            w = _width(cl, font_title, title_size)
            max_title_w = max(max_title_w, w)
            if w > title_limit:
                title_violations.append((cl, len(cl), w))

    max_sub_w = 0
    layout_violations = []
    two_line_violations = []
    for _, _, _, style, raw_t in blocks:
        if style != "Main":
            continue
        if "\\N" in raw_t or "\n" in raw_t:
            two_line_violations.append(raw_t)
        lines = raw_t.replace("\\N", "\n").split("\n")
        for line in lines:
            cl = re.sub(r"\{[^}]*\}", "", line).strip()
            if not cl:
                continue
            if font_measurer:
                bbox = font_measurer.getbbox(cl)
                w = bbox[2] - bbox[0]
            else:
                w = sum(78 if "\uac00" <= ch <= "\ud7a3" else 25 if ch.isspace() else 48 for ch in cl)
            if w > max_sub_w:
                max_sub_w = w
            if w > body_limit:
                layout_violations.append((cl, len(cl), w))

    # MarginV 체크 (Format: ..., Alignment, MarginL, MarginR, MarginV, Encoding)
    ass_margin_v = None
    if os.path.exists(ass):
        with open(ass, encoding="utf-8") as f:
            for l in f:
                if l.startswith("Style: Main,"):
                    parts = l.split(",")
                    if len(parts) >= 23:
                        try:
                            ass_margin_v = int(parts[-2].strip())
                        except Exception:
                            pass
                    break

    ok_g = (len(layout_violations) == 0) and (len(two_line_violations) == 0) \
        and (len(title_violations) == 0) and (ass_margin_v == BODY_MARGIN_V or ass_margin_v is None)
    val_str = f"본문 최대 {max_sub_w}px / 제목 최대 {max_title_w}px"
    if title_violations:
        val_str += (f" (❌ 제목 {len(title_violations)}건 초과: "
                    f"\"{title_violations[0][0][:10]}…\" {title_violations[0][2]}px "
                    f"> {title_limit}px)")
    if two_line_violations:
        val_str += f" (❌ 2줄 자막 {len(two_line_violations)}건 검출)"
    if layout_violations:
        val_str += f" (❌ {len(layout_violations)}건 규격 초과: \"{layout_violations[0][0][:8]}…\" {layout_violations[0][2]}px)"
    if ass_margin_v is not None and ass_margin_v != BODY_MARGIN_V:
        val_str += f" (❌ MarginV={ass_margin_v} != {BODY_MARGIN_V})"
    rep.add("G-G", "자막 레이아웃 한계", val_str,
            f"본문 <= {body_limit}px 단독 1줄(2줄금지) MarginV={BODY_MARGIN_V} / 제목 각 행 <= {title_limit}px", ok_g)

    # ── G-H 무음 갭 검사 ──────────────────────────────────────────
    # P1: 집계 상한(총합 %)만 걸면 긴 구멍 하나가 전체 길이에 희석돼 통과한다.
    #   단일 항목 상한(가장 긴 갭)을 같이 걸어야 잡힌다.
    #   voiced_track 의 적응형 임계는 음절 사이 미세 공백까지 무음으로 세어
    #   총합이 과대평가되므로, 여기서는 Gate 2 와 같은 절대 dBFS 기준으로 잰다.
    gaps = []
    span0 = blocks[0][0] if blocks else 0.0
    span1 = blocks[-1][1] if blocks else float("inf")
    try:
        with wave.open(wav) as _w:
            _sr = _w.getframerate()
            _a = np.frombuffer(_w.readframes(_w.getnframes()), dtype=np.int16)
        _a = _a.astype(np.float32) / 32768.0
        _hop = int(_sr * GAP_HOP)
        _f = _a[:len(_a) // _hop * _hop].reshape(-1, _hop)
        _db = 20 * np.log10(np.sqrt((_f ** 2).mean(1)) + 1e-9)
        _quiet = _db < SILENCE_DB
        _n = len(_quiet)
        _i = 0
        while _i < _n:
            if not _quiet[_i]:
                _i += 1
                continue
            _j = _i
            while _j < _n and _quiet[_j]:
                _j += 1
            # 발화 구간(첫 자막 시작 ~ 마지막 자막 끝) 밖은 제외한다. 머리는 G-A 가,
            # 꼬리는 preserve(회중 반응)·페이드 자리라 금기 5 가 다룬다. cut_fillers --gaps 와 같은 범위.
            _g0, _g1 = _i * GAP_HOP, _j * GAP_HOP
            if _g0 > span0 and _g1 < span1 and (_g1 - _g0) >= 0.20:
                gaps.append((_g1 - _g0, _g0))
            _i = _j
        total_len = _n * GAP_HOP
    except Exception as e:
        total_len = 0.0
        rep.add("G-H", "발화 중 무음 갭", f"측정 실패 ({e})", "측정 가능", False)
    if total_len:
        max_gap, max_at = max(gaps, default=(0.0, 0.0))
        gap_ratio = sum(g for g, _ in gaps) / total_len * 100.0
        over = [(g, t) for g, t in gaps if g > TH_MAX_GAP]
        ok_h = (len(over) == 0) and (gap_ratio <= TH_GAP_RATIO)
        val_h = f"최대 {max_gap:.2f}s @{max_at:.1f}s, 총합 {gap_ratio:.1f}%"
        if over:
            val_h += f" (❌ {TH_MAX_GAP:.2f}s 초과 {len(over)}곳: " + \
                     ", ".join(f"{t:.1f}s({g:.2f}s)" for g, t in over[:4]) + ")"
        rep.add("G-H", "발화 중 무음 갭", val_h,
                f"개별 <= {TH_MAX_GAP:.2f}s, 총합 <= {TH_GAP_RATIO:.0f}% (-42dBFS 기준)", ok_h)

    # ── G-I 무음 컷 말끝·첫소리 잘림 ─────────────────────────────────
    try:
        _lim, _bad = cut_edge_check(os.path.join(outdir, "_work"), slug)
        rep.add("G-I", "무음 컷 말끝 잘림", "0곳" if not _bad else f"{len(_bad)}곳: " + ", ".join(_bad[:4]),
                f"지운 쪽 첫·끝 0.1s <= 발화-{TH_CUT_EDGE_DB:.0f}dB ({_lim:.0f}dB)", not _bad)
    except FileNotFoundError as e:
        rep.add("G-I", "무음 컷 말끝 잘림", f"측정 불가 ({os.path.basename(e.filename)} 없음)",
                "raw.wav·cuts.json 필요", False)

    rep.render(slug)
    if rep.failed:
        print("\n❌ 게이트 불합격 — 위 ❌ 항목을 고치기 전에는 오너에게 가져가지 않는다.")
        return 1
    print("\n✅ 게이트 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
