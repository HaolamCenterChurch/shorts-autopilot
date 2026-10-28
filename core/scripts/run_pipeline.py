#!/usr/bin/env python3
"""extract_segments -> silence_cut -> track_crop -> (make_ass -> render_vertical
-> verify_output) 전체를 묶는 오케스트레이터. --stage a 로 1~5단계, --stage b 로
자막~검증 단계를 실행한다."""
import argparse
import codecs
import json
import os
import subprocess
import sys

import env_discovery

FFMPEG = env_discovery.get_ffmpeg()
WHISPER_BIN = env_discovery.get_whisper_bin()
WHISPER_MODEL = env_discovery.get_whisper_model()
YUNET_MODEL = env_discovery.get_yunet_model()

PYTHON_BIN = env_discovery.get_python()
HERE = os.path.dirname(os.path.abspath(__file__))


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def run_module(script_name, args_list):
    cmd = [PYTHON_BIN, os.path.join(HERE, script_name)] + args_list
    log(f"[run_pipeline] 실행: {script_name} {' '.join(args_list)}")
    subprocess.run(cmd, check=True)


def extract_wav(video_path, wav_path):
    cmd = [FFMPEG, "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000",
           wav_path]
    subprocess.run(cmd, check=True)


def transcribe_words(video_path, workdir, prefix):
    wav_path = os.path.join(workdir, f"{prefix}.wav")
    extract_wav(video_path, wav_path)
    out_prefix = os.path.join(workdir, prefix)
    cmd = [WHISPER_BIN, "-m", WHISPER_MODEL, "-l", "ko", "-oj", "-ojf",
           "--beam-size", "5", "--temperature", "0",
           # ★DTW 를 켜야 실제 발화 시각(t_dtw)이 나온다. flash-attn 이 켜져 있으면
           #   --dtw 를 줘도 조용히 무시되므로 --no-flash-attn 을 반드시 같이 준다.
           "--dtw", "large.v3", "--no-flash-attn",
           "-f", wav_path, "-of", out_prefix]
    log(f"[run_pipeline] whisper 단어 타임스탬프 전사: {prefix}")
    subprocess.run(cmd, check=True)

    # ★whisper.cpp 는 한글 한 글자를 토큰 두 개로 쪼개 반쪽짜리 UTF-8 바이트를 낸다.
    #   errors="ignore" 로 열면 그 반쪽들이 통째로 사라져 전사문에 글자가 빠진다
    #   (2026-09-02 실측: "사춘기" -> "사기", "컴플렉스" -> "스"). 그래서
    #   surrogateescape 로 바이트를 보존한 뒤, 바이트 스트림을 증분 디코딩해
    #   **글자 단위**로 시각을 붙인다. 자막 정렬이 이 스트림 위에서 이뤄진다.
    with open(out_prefix + ".json", "r", encoding="utf-8",
              errors="surrogateescape") as f:
        data = json.load(f)

    # ★whisper 기본 타임스탬프(offsets)는 반올림 heuristic 이라 실제 발화보다 앞선다.
    #   2026-09-05 실측(54초 마스터, 유효토큰 212개): 평균 0.336초·중앙 0.315초 빠르고
    #   24.1%는 0.5초 이상 빨랐다 — 오너가 "자막이 너무 빠르다"고 한 그것이다.
    #   cross-attention DTW 가 낸 t_dtw(센티초)가 실제 발화 시각이므로 그걸 정본으로 쓴다.
    toks = []
    for seg in data.get("transcription", []):
        for tok in seg.get("tokens", []):
            text = tok.get("text", "")
            # [_BEG_], [_TT_105] 같은 특수 토큰 전부 제외 (끝이 "_]" 가 아닌 것도 있다)
            if text.startswith("[_") and text.endswith("]"):
                continue
            offsets = tok.get("offsets", {})
            toks.append({
                "text": text,
                "n0": offsets.get("from", 0) / 1000.0,
                "n1": offsets.get("to", 0) / 1000.0,
                "dtw": tok.get("t_dtw", -1),
            })

    n_dtw = sum(1 for t in toks if t["dtw"] != -1)
    use_dtw = bool(toks) and n_dtw >= len(toks) * 0.5
    if use_dtw:
        log(f"[run_pipeline] t_dtw 적용: {n_dtw}/{len(toks)} 토큰")
        # 시작 시각은 t_dtw, 끝 시각은 다음 토큰의 시작(무간극). 마지막만 offsets 의 끝.
        starts = []
        prev = 0.0
        for t in toks:
            s0 = t["dtw"] / 100.0 if t["dtw"] != -1 else t["n0"]
            if s0 < prev:  # DTW 가 역행하면 직전 값으로 고정한다
                s0 = prev
            starts.append(s0)
            prev = s0
        for i, t in enumerate(toks):
            t["t0"] = starts[i]
            t["t1"] = starts[i + 1] if i + 1 < len(toks) else max(t["n1"], starts[i])
    else:
        log(f"[run_pipeline] ⚠t_dtw 부족({n_dtw}/{len(toks)}) — 기본 타임스탬프로 폴백")
        for t in toks:
            t["t0"], t["t1"] = t["n0"], t["n1"]

    byte_times = []  # (바이트, t0, t1)
    for t in toks:
        for b in t["text"].encode("utf-8", "surrogateescape"):
            byte_times.append((b, t["t0"], t["t1"]))

    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    words = []
    pending_t0 = None
    pending_t1 = None
    for b, t0, t1 in byte_times:
        if pending_t0 is None:
            pending_t0 = t0
        pending_t1 = t1
        piece = decoder.decode(bytes([b]))
        if piece:
            for ch in piece:
                if ch.strip():
                    words.append({"t0": pending_t0, "t1": pending_t1,
                                  "text": ch})
            pending_t0 = None
            pending_t1 = None
    tail = decoder.decode(b"", True)
    for ch in tail:
        if ch.strip():
            words.append({"t0": pending_t1 or 0.0, "t1": pending_t1 or 0.0,
                          "text": ch})

    words_path = os.path.join(workdir, f"{prefix}.words.json")
    with open(words_path, "w", encoding="utf-8") as f:
        json.dump(words, f, ensure_ascii=False, indent=2)
    return words_path, words


def extend_segment_tails(src, segments, max_ext=0.6):
    """★구간 끝에서 말꼬리를 자르지 않는다 (오너 2026-10-03 "말이 끝나지 않았는데 자꾸 넘긴다").
    손으로 잡은 구간 끝이 '~요/~다'가 작게(-42dB) 이어지는 중간에 걸리는 일이 반복됐다
    (2026-10-02 B안 2691.10s: 끝 뒤 0.1~0.35s 에 '요'가 남아 있었다).
    끝 뒤를 원본에서 읽어, 소리가 '발화-28dB' 아래로 0.15초 이상 내려간 지점까지 끝을 민다.
    다음 말 첫소리(발화-12dB 이상)를 만나면 그 직전에서 멈추고, 최대 max_ext 초까지만 민다.
    바로 뒤(1초 안)에 다른 구간이 이어지는 끝은 cut_fillers 가 일부러 자른 이음매라 건드리지 않는다."""
    import numpy as np
    out = []
    starts = [s for s, _ in segments]
    for s, e in segments:
        if any(e <= ns < e + 1.0 for ns in starts):
            out.append([s, e]); continue
        look0 = max(s, e - 2.0)
        cmd = [FFMPEG, "-v", "error", "-ss", f"{look0:.3f}", "-t", f"{e - look0 + max_ext + 0.2:.3f}",
               "-i", src, "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"]
        x = np.frombuffer(subprocess.run(cmd, capture_output=True).stdout, np.float32)
        hop = 160
        n = len(x) // hop
        if n < 20:
            out.append([s, e]); continue
        db = np.array([20 * np.log10(np.sqrt(np.mean(x[i * hop:(i + 1) * hop] ** 2)) + 1e-9) for i in range(n)])
        k0 = int(round((e - look0) / 0.01))
        ref = np.percentile(db[:k0], 90) if k0 > 10 else db.max()
        release, onset = ref - 28.0, ref - 12.0
        quiet, new_e, last_quiet_start = 0, None, None
        for k in range(k0, min(n, k0 + int(max_ext / 0.01))):
            if db[k] >= onset:                       # 다음 말이 시작됐다 → 그 앞 조용한 곳까지만
                if last_quiet_start is not None:
                    new_e = look0 + last_quiet_start * 0.01 + 0.05
                break
            if db[k] < release:
                if quiet == 0:
                    last_quiet_start = k
                quiet += 1
            else:
                quiet = 0
            if quiet >= 15:                          # 0.15초 조용 = 말꼬리가 끝났다
                new_e = look0 + last_quiet_start * 0.01 + 0.05
                break
        if last_quiet_start == k0 and new_e is not None:
            new_e = e                                # 이미 조용한 곳에서 끝난다
        if new_e is None:
            log(f"[stage a] ⚠구간 끝 {e:.2f}s 뒤 {max_ext}s 안에 말꼬리가 끝나지 않는다 — 플랜 끝을 확인하라")
            new_e = e
        new_e = max(e, round(new_e, 3))
        if new_e > e + 0.02:
            log(f"[stage a] 말꼬리 연장 {e:.2f} → {new_e:.2f}s (+{new_e - e:.2f})")
        out.append([s, new_e])
    return out


def stage_a(src, plan, workdir, plan_path=None):
    slug = plan["slug"]
    plan["segments"] = extend_segment_tails(src, plan["segments"])
    segments_str = ";".join(f"{s}-{e}" for s, e in plan["segments"])

    raw_path = os.path.join(workdir, f"{slug}_raw.mov")
    run_module("extract_segments.py", [
        "--src", src, "--segments", segments_str,
        "--out", raw_path, "--workdir", workdir,
    ])

    raw_words_path, _ = transcribe_words(raw_path, workdir, f"{slug}_raw")

    master_path = os.path.join(workdir, f"{slug}_master.mov")
    # plan 의 "silence" 로만 덮어쓴다. 키가 없으면 아래 기본값 = 종전과 동일.
    sil = plan.get("silence") or {}
    silence_args = [
        "--in", raw_path, "--out", master_path,
        "--words", raw_words_path,
        "--min-silence", str(sil.get("min_silence", "0.30")),
        "--pad", str(sil.get("pad", "0.12")),
        "--keep-gap", str(sil.get("keep_gap", "0.06")),
        "--min-gain", str(sil.get("min_gain", "0.40")),
        "--min-keep", str(sil.get("min_keep", "1.20")),
        "--workdir", workdir,
    ]
    if "preserve" in plan and plan["preserve"]:
        # plan 의 preserve 는 원본(src) 절대시간이고, silence_cut 이 보는 것은
        # 세그먼트를 이어붙인 raw 시간축이다. 변환하지 않으면 통째로 무시된다.
        acc = 0.0
        pres = []
        for ss, se in plan["segments"]:
            for ps, pe in plan["preserve"]:
                lo, hi = max(ps, ss), min(pe, se)
                if hi > lo:
                    pres.append((acc + (lo - ss), acc + (hi - ss)))
            acc += se - ss
        if pres:
            pres_str = ";".join(f"{a:.3f}-{b:.3f}" for a, b in pres)
            log(f"[run_pipeline] preserve src→raw 변환: {pres_str}")
            silence_args.extend(["--preserve", pres_str])
    run_module("silence_cut.py", silence_args)

    master_words_path, _ = transcribe_words(master_path, workdir, f"{slug}_master")

    track_json = os.path.join(workdir, f"{slug}_track.json")
    track_cmd = os.path.join(workdir, f"{slug}_track.sendcmd.txt")
    track_args = [
        "--in", master_path, "--out-cmd", track_cmd, "--out-json", track_json,
        "--crop-w", "1216", "--sample-fps", "10", "--model", YUNET_MODEL,
    ]
    if plan_path:
        track_args.extend(["--plan", plan_path])
    # plan 의 "track" 으로 정적 락 임계값을 회차별로 조정한다(없으면 표준값 그대로).
    tr = plan.get("track") or {}
    for key, flag in (("static_spread", "--static-spread"),
                      ("static_mindur", "--static-mindur"),
                      ("static_lock", "--static-lock"),
                      ("pan_speed", "--pan-speed"),
                      ("carry_ratio", "--carry-ratio"),
                      ("fixed_cx", "--fixed-cx"),
                      ("fixed_x", "--fixed-x")):
        if key in tr:
            track_args.extend([flag, str(tr[key])])
    run_module("track_crop.py", track_args)

    with open(master_path + ".cuts.json", "r", encoding="utf-8") as f:
        cuts = json.load(f)
    duration = cuts["duration"]

    log(f"[stage a 완료] master_words={master_words_path} duration={duration:.3f}s")
    return {
        "master_path": master_path,
        "master_words_path": master_words_path,
        "track_json": track_json,
        "duration": duration,
    }


def stage_b(plan, workdir, outdir, chunks_path, state=None):
    slug = plan["slug"]
    master_path = os.path.join(workdir, f"{slug}_master.mov")
    master_words_path = os.path.join(workdir, f"{slug}_master.words.json")
    track_json = os.path.join(workdir, f"{slug}_track.json")

    if state:
        master_path = state["master_path"]
        master_words_path = state["master_words_path"]
        track_json = state["track_json"]
        duration = state["duration"]
    else:
        with open(master_path + ".cuts.json", "r", encoding="utf-8") as f:
            duration = json.load(f)["duration"]

    ass_path = os.path.join(workdir, f"{slug}.ass")
    ass_args = [
        "--words", master_words_path, "--chunks", chunks_path,
        "--out", ass_path, "--duration", str(duration),
    ]
    # plan 의 "subs" 로 자막 스타일을 회차별로 조정한다(없으면 표준 2단 자막 그대로).
    subs = plan.get("subs") or {}
    if subs.get("no_en"):
        ass_args.append("--no-en")
    if "shadow" in subs:
        ass_args += ["--shadow", str(subs["shadow"])]
    if "font_size" in subs:
        ass_args += ["--font-size", str(subs["font_size"])]
    if "margin_v" in subs:
        ass_args += ["--margin-v", str(subs["margin_v"])]
    if subs.get("badge"):
        ass_args += ["--badge", subs["badge"]]
        if subs.get("badge_margin"):
            ass_args += ["--badge-margin", str(subs["badge_margin"])]
    if "last_tail" in subs:
        ass_args += ["--last-tail", str(subs["last_tail"])]
    if "margin_lr" in subs:
        ass_args += ["--margin-lr", str(subs["margin_lr"])]
    if "outline" in subs:
        ass_args += ["--outline", str(subs["outline"])]
    if subs.get("box"):
        ass_args.append("--box")
        if "box_pad" in subs:
            ass_args += ["--box-pad", str(subs["box_pad"])]
    if "top_title_outline" in subs:
        ass_args += ["--top-title-outline", str(subs["top_title_outline"])]
    if subs.get("top_title"):
        ass_args += ["--top-title", subs["top_title"]]
        if subs.get("top_title_size"):
            ass_args += ["--top-title-size", str(subs["top_title_size"])]
        if subs.get("top_title_margin"):
            ass_args += ["--top-title-margin", str(subs["top_title_margin"])]
    run_module("make_ass.py", ass_args)

    # plan 의 effects 또는 chunks 의 hl(하이라이트) 구간 기반 줌인 effects.json 자동 생성
    # ★매 렌더마다 새로 만든다 (2026-09-15: 파일이 있으면 건너뛰던 탓에 Stage A 재실행 후에도
    #  옛 타임라인의 줌 시각이 남아 A·B·E 줌이 발화 중간에 켜지고 빠졌다). 수동 지정은 plan.effects.zooms.
    effects_path = os.path.join(workdir, f"{slug}_effects.json")
    if True:
        from make_ass import match_chunks_to_words
        import zoom_plan
        with open(chunks_path, "r", encoding="utf-8") as f:
            chunks = json.load(f)
        with open(master_words_path, "r", encoding="utf-8") as f:
            mwords = json.load(f)
        matched = match_chunks_to_words(mwords, chunks)
        explicit_zooms = (plan.get("effects") or {}).get("zooms")
        if explicit_zooms is not None:
            zooms = explicit_zooms
        else:
            # 문장 끝까지 유지 · 이음매에서 전환 · 강조가 이어지면 2단 — 규칙은 zoom_plan.py docstring
            shot_cuts = zoom_plan.master_shot_cuts(workdir, slug, master_path, plan)
            jump_points = zoom_plan.master_jump_points(master_path)
            zooms = zoom_plan.plan_zooms(chunks, matched, shot_cuts, duration, jump_points)
        with open(effects_path, "w", encoding="utf-8") as f:
            json.dump({
                "zooms": zooms,
                "stickers": (plan.get("effects") or {}).get("stickers", [])
            }, f, ensure_ascii=False, indent=2)
        log(f"[run_pipeline] 줌인 effects.json 자동 생성 완료: 줌인 {len(zooms)}곳 -> {effects_path}")

    os.makedirs(outdir, exist_ok=True)
    final_path = os.path.join(outdir, f"{slug}.mp4")
    rv_args = ["--in", master_path, "--track", track_json, "--ass", ass_path,
               "--out", final_path]
    if subs.get("band_top"):
        rv_args += ["--band-top", str(subs["band_top"])]
        if subs.get("zoom_out"):
            rv_args += ["--zoom-out", str(subs["zoom_out"])]
        if subs.get("band_bottom"):
            rv_args += ["--band-bottom", str(subs["band_bottom"])]
        if subs.get("band_crop_y") is not None:
            rv_args += ["--band-crop-y", str(subs["band_crop_y"])]
        if "zoom_anchor_y" in subs:
            rv_args += ["--zoom-anchor-y", str(subs["zoom_anchor_y"])]
    run_module("render_vertical.py", rv_args)

    script_path = os.path.join(workdir, f"{slug}_script.txt")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(plan.get("script", ""))

    report_path = os.path.join(outdir, f"{slug}_verify.md")
    run_module("verify_output.py", [
        "--video", final_path, "--script", script_path,
        "--out", report_path, "--workdir", workdir,
    ])

    log(f"[stage b 완료] 최종본={final_path} 리포트={report_path}")


def main():
    ap = argparse.ArgumentParser(description="쇼츠 파이프라인 오케스트레이터")
    ap.add_argument("--src", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--stage", choices=["a", "b"], default="a")
    ap.add_argument("--chunks", default=None)
    args = ap.parse_args()

    with open(args.plan, "r", encoding="utf-8", errors="ignore") as f:
        plan = json.load(f)

    workdir = os.path.join(args.outdir, "_work")
    os.makedirs(workdir, exist_ok=True)

    if args.stage == "a":
        state = stage_a(args.src, plan, workdir, plan_path=args.plan)
        if args.chunks:
            stage_b(plan, workdir, args.outdir, args.chunks, state=state)
        else:
            print(json.dumps({
                "master_words": state["master_words_path"],
                "duration": state["duration"],
            }, ensure_ascii=False))
    else:
        if not args.chunks:
            raise ValueError("--stage b 에는 --chunks 가 필요하다")
        stage_b(plan, workdir, args.outdir, args.chunks)


if __name__ == "__main__":
    main()
