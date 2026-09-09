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

HERE = os.path.dirname(os.path.abspath(__file__))


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def run_module(script_name, args_list):
    cmd = [sys.executable, os.path.join(HERE, script_name)] + args_list
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


def stage_a(src, plan, workdir, plan_path=None):
    slug = plan["slug"]
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
                      ("pan_speed", "--pan-speed")):
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
    if "top_title_outline" in subs:
        ass_args += ["--top-title-outline", str(subs["top_title_outline"])]
    if subs.get("top_title"):
        ass_args += ["--top-title", subs["top_title"]]
        if subs.get("top_title_size"):
            ass_args += ["--top-title-size", str(subs["top_title_size"])]
        if subs.get("top_title_margin"):
            ass_args += ["--top-title-margin", str(subs["top_title_margin"])]
    run_module("make_ass.py", ass_args)

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
