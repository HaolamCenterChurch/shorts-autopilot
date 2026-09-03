#!/usr/bin/env python3
"""중간본에서 무음/호흡 구간을 잘라 '클린 마스터'를 만든다. 말끝 잘림 금지."""
import argparse
import json
import os
import shutil
import subprocess
import sys

import numpy as np

FFMPEG = os.environ.get("SHORTS_FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
FPS = 30
SR = 16000

ENCODE_ARGS = [
    "-c:v", "h264_videotoolbox", "-b:v", "60M", "-pix_fmt", "yuv420p",
    "-r", str(FPS), "-vsync", "cfr",
    "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "1",
]


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def snap(sec: float) -> float:
    return round(sec * FPS) / FPS


def read_pcm(in_path):
    cmd = [FFMPEG, "-y", "-i", in_path, "-vn", "-ac", "1", "-ar", str(SR),
           "-f", "s16le", "-"]
    log("[silence_cut] PCM 추출 중...")
    proc = subprocess.run(cmd, check=True, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL)
    audio = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float64)
    return audio


def frame_dbfs(audio, hop, win):
    n_frames = max(0, (len(audio) - win) // hop + 1)
    if n_frames <= 0:
        return np.array([]), np.array([])
    idx = np.arange(n_frames)[:, None] * hop + np.arange(win)[None, :]
    frames = audio[idx]
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    db = 20 * np.log10(rms / 32767.0 + 1e-9)
    times = np.arange(n_frames) * hop / SR
    return db, times


def find_silence_runs(db, times, threshold, min_silence):
    below = db < threshold
    runs = []
    i = 0
    n = len(below)
    while i < n:
        if below[i]:
            j = i
            while j < n and below[j]:
                j += 1
            t_start = times[i]
            t_end = times[j - 1] + (times[1] - times[0] if len(times) > 1 else 0.01)
            if t_end - t_start >= min_silence:
                runs.append((t_start, t_end))
            i = j
        else:
            i += 1
    return runs


def build_delete_regions(runs, duration, pad, keep_gap, words, word_guard=False,
                         min_gain=0.40):
    deletes = []
    skipped_gain = 0
    for idx, (s, e) in enumerate(runs):
        is_leading = (idx == 0 and s <= 0.05)
        is_trailing = (idx == len(runs) - 1 and e >= duration - 0.05)
        if is_leading and not is_trailing:
            a, b = 0.0, max(0.0, e - pad)
            if b > a:
                deletes.append((a, b))
        elif is_trailing and not is_leading:
            a, b = min(s + pad, duration), duration
            if b > a:
                deletes.append((a, b))
        elif is_leading and is_trailing:
            a, b = s + pad, e - pad
            if b > a:
                deletes.append((a, b))
        else:
            a, b = s + pad, e - pad
            if b - a <= keep_gap:
                continue
            b -= keep_gap
            # ★이득이 없는 컷은 만들지 않는다 (2026-09-03 실측).
            #   pad·keep_gap 를 빼고 나면 0.02초만 지워지는 삭제 구간이 생기는데,
            #   그래도 화면은 한 번 튄다. 오너가 "음성도 없는데 잠깐 지나가는 컷"
            #   이라고 지적한 것이 전부 이것이었다. 실측(2026-09-02 3편):
            #   0.25초 미만만 지우는 컷이 14개 중 8개.
            if b - a < min_gain:
                skipped_gain += 1
                continue
            deletes.append((a, b))
    if skipped_gain:
        log(f"[silence_cut] 이득 {min_gain:.2f}s 미만이라 컷하지 않음: {skipped_gain}개")

    # ★단어 가드는 기본으로 쓰지 않는다.
    #   whisper 토큰 타임스탬프는 앞 토큰의 끝 = 뒤 토큰의 시작으로 타임라인을 빈틈없이
    #   덮기 때문에, 겹침만 보면 실제 무음 구간까지 전부 취소된다(2026-09-02 실측:
    #   삭제후보 6개 → 통과 0개). 무음 판정은 RMS 가 정본이고, 말끝 보호는 pad 가 한다.
    #   --word-guard 를 켰을 때만, 삭제 구간이 한 단어 안에 0.15초 넘게 파고드는 경우만
    #   취소한다.
    if words and word_guard:
        filtered = []
        for a, b in deletes:
            overlap = 0.0
            for w in words:
                overlap = max(overlap, min(b, w["t1"]) - max(a, w["t0"]))
            if overlap <= 0.15:
                filtered.append((a, b))
        deletes = filtered

    return deletes


def enforce_min_keep(deletes, duration, min_keep):
    """컷과 컷 사이에 min_keep 보다 짧은 조각이 남지 않게 한다.

    ★짧은 조각은 "갑자기 컷이 바뀌고 잠깐 지나가는 컷"으로 보인다(오너 지적).
    조각이 너무 짧으면 그 조각을 만든 두 삭제 구간 중 **덜 지우는 쪽을 취소**해
    앞뒤를 도로 이어 붙인다. 이득이 큰 컷은 살아남는다.
    """
    deletes = sorted(deletes)
    dropped = 0
    while len(deletes) > 1:
        target = None
        for i in range(len(deletes) - 1):
            if deletes[i + 1][0] - deletes[i][1] < min_keep:
                li = deletes[i][1] - deletes[i][0]
                lj = deletes[i + 1][1] - deletes[i + 1][0]
                target = i if li <= lj else i + 1
                break
        if target is None:
            break
        deletes.pop(target)
        dropped += 1
    # 머리·꼬리에 남는 조각도 같은 기준으로 본다(머리/꼬리 무음 삭제 자체는 건드리지 않는다).
    if deletes and 0.01 < deletes[0][0] < min_keep:
        deletes.pop(0)
        dropped += 1
    if deletes and 0.01 < duration - deletes[-1][1] < min_keep:
        deletes.pop()
        dropped += 1
    if dropped:
        log(f"[silence_cut] 조각이 {min_keep:.2f}s 미만이라 취소한 컷: {dropped}개")
    return deletes

def deletes_to_keeps(deletes, duration):
    deletes = sorted(deletes)
    keeps = []
    cursor = 0.0
    for a, b in deletes:
        a = max(a, cursor)
        if a > cursor:
            keeps.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < duration:
        keeps.append((cursor, duration))
    keeps = [(snap(a), snap(b)) for a, b in keeps if b - a > 0]
    keeps = [(a, b) for a, b in keeps if b > a]
    return keeps


def render_batch(in_path, keeps, workdir, batch_idx):
    n = len(keeps)
    filters = []
    labels = []
    for i, (a, b) in enumerate(keeps):
        dur = b - a
        fade_out_st = max(0.0, dur - 0.005)
        filters.append(
            f"[0:v]trim=start={a:.4f}:end={b:.4f},setpts=PTS-STARTPTS[v{i}]")
        filters.append(
            f"[0:a]atrim=start={a:.4f}:end={b:.4f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:st=0:d=0.005,afade=t=out:st={fade_out_st:.4f}:d=0.005[a{i}]")
        labels.append(f"[v{i}][a{i}]")
    filters.append(f"{''.join(labels)}concat=n={n}:v=1:a=1[outv][outa]")
    filter_complex = ";".join(filters)

    out_path = os.path.join(workdir, f"_silence_batch_{batch_idx:03d}.mov")
    cmd = [FFMPEG, "-y", "-i", in_path, "-filter_complex", filter_complex,
           "-map", "[outv]", "-map", "[outa]"] + ENCODE_ARGS + [out_path]
    log(f"[silence_cut] 배치 {batch_idx} 렌더 중 ({n}개 조각)...")
    subprocess.run(cmd, check=True)
    return out_path


def concat_batches(batch_paths, out_path, workdir):
    if len(batch_paths) == 1:
        os.replace(batch_paths[0], out_path)
        return
    list_path = os.path.join(workdir, "_silence_concat_list.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for p in batch_paths:
            escaped = p.replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")
    cmd = [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", list_path]
    cmd += ENCODE_ARGS + [out_path]
    log(f"[silence_cut] {len(batch_paths)}개 배치 최종 합치는 중...")
    subprocess.run(cmd, check=True)
    for p in batch_paths:
        if os.path.exists(p):
            os.remove(p)


def main():
    ap = argparse.ArgumentParser(description="무음/호흡 구간 제거")
    ap.add_argument("--in", dest="in_path", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--words", default=None)
    ap.add_argument("--min-silence", type=float, default=0.30)
    ap.add_argument("--word-guard", action="store_true",
                    help="단어 타임스탬프로 삭제를 취소한다(기본 꺼짐)")
    ap.add_argument("--pad", type=float, default=0.12)
    ap.add_argument("--keep-gap", type=float, default=0.06)
    ap.add_argument("--min-gain", type=float, default=0.40,
                    help="이만큼도 못 지우는 무음은 컷하지 않는다(화면만 튄다)")
    ap.add_argument("--min-keep", type=float, default=1.20,
                    help="컷 사이 조각이 이보다 짧으면 덜 지우는 쪽 컷을 취소한다")
    ap.add_argument("--workdir", required=True)
    args = ap.parse_args()

    os.makedirs(args.workdir, exist_ok=True)

    words = None
    if args.words and os.path.exists(args.words):
        with open(args.words, "r", encoding="utf-8", errors="ignore") as f:
            words = json.load(f)

    audio = read_pcm(args.in_path)
    duration = len(audio) / SR
    hop = int(0.01 * SR)
    win = int(0.03 * SR)
    db, times = frame_dbfs(audio, hop, win)
    if len(db) == 0:
        raise RuntimeError("오디오가 너무 짧다")

    floor_db = np.percentile(db, 10)
    speech_db = np.percentile(db, 90)
    # ★발화 레벨 기준으로 잡는다. 플로어 기준(floor+8)만 쓰면 조용한 스튜디오 녹음에서
    #   임계가 -47dB 까지 내려가 호흡 구간을 하나도 못 잡았다(2026-09-02 실측).
    threshold = min(max(floor_db + 6.0, speech_db - 18.0), speech_db - 12.0)
    log(f"[silence_cut] floor={floor_db:.1f}dB speech={speech_db:.1f}dB "
        f"threshold={threshold:.1f}dB")

    runs = find_silence_runs(db, times, threshold, args.min_silence)
    log(f"[silence_cut] 무음 후보 {len(runs)}개")

    deletes = build_delete_regions(runs, duration, args.pad, args.keep_gap,
                                   words, args.word_guard, args.min_gain)
    deletes = enforce_min_keep(deletes, duration, args.min_keep)
    log(f"[silence_cut] 실제 삭제 구간 {len(deletes)}개")

    keeps = deletes_to_keeps(deletes, duration)
    log(f"[silence_cut] 남는 구간 {len(keeps)}개")

    batch_paths = []
    for i in range(0, len(keeps), 20):
        chunk = keeps[i:i + 20]
        batch_paths.append(render_batch(args.in_path, chunk, args.workdir,
                                         i // 20))

    concat_batches(batch_paths, args.out, args.workdir)

    total_keep = sum(b - a for a, b in keeps)
    removed = duration - total_keep
    cuts_path = args.out + ".cuts.json"
    with open(cuts_path, "w", encoding="utf-8") as f:
        json.dump({"keeps": [[a, b] for a, b in keeps],
                   "duration": total_keep, "removed": removed}, f,
                  ensure_ascii=False, indent=2)
    log(f"[완료] {args.out} (길이 {total_keep:.3f}s, 제거 {removed:.3f}s) / "
        f"cuts: {cuts_path}")


if __name__ == "__main__":
    main()
