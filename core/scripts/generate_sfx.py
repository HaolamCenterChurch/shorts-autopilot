#!/usr/bin/env python3
"""사용자 선택 효과음(01_미니멀_버블팝_초미세.wav) 적용 SFX 생성기.

선택된 45ms 초미세 미니멀 버블팝 샘플을 핵심 하이라이트 및 줌인 타이밍에 배치합니다.
샘플 파일이 없는 환경에서도 무중단 동작을 위해 동일 주파수 스윕(900Hz->1800Hz) 자동 합성 폴백을 내장합니다.
"""
import argparse
import os
import sys
import wave
import numpy as np

SR = 48000

def get_pop_sample():
    # 1. 로컬 assets/sfx 디렉토리 우선 탐색
    script_dir = os.path.dirname(os.path.abspath(__file__))
    local_asset = os.path.join(script_dir, "assets", "sfx", "01_미니멀_버블팝_초미세.wav")
    system_sample = "/Users/caleb/Documents/HaolamShorts/sfx_audition/01_미니멀_버블팝_초미세.wav"
    
    sample_path = local_asset if os.path.exists(local_asset) else (system_sample if os.path.exists(system_sample) else None)
    
    if sample_path and os.path.exists(sample_path):
        try:
            with wave.open(sample_path, "r") as wf:
                n_f = wf.getnframes()
                raw = wf.readframes(n_f)
                return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0
        except Exception:
            pass
            
    # 2. 파일이 없을 경우 동일 스펙 procedural synthesis 폴백 (45ms 초미세 버블팝)
    dur = 0.045
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    # 900Hz -> 1800Hz 지수 상승 스윕
    freq = np.geomspace(900, 1800, len(t))
    phase = 2 * np.pi * np.cumsum(freq) / SR
    env = np.exp(-t * 90.0) # 빠른 감쇄
    sig = np.sin(phase) * env * 0.45
    return sig.astype(np.float32)

def build_sfx_track(ass_file, duration, out_wav, zoom_times=None):
    total_samples = int(np.ceil(duration * SR))
    track = np.zeros(total_samples, dtype=np.float32)
    pop = get_pop_sample()
    pop_len = len(pop)

    placed_times = []
    # 1. 줌인 시작 시점에 버블팝
    if zoom_times:
        for zt in zoom_times:
            idx = int(zt * SR)
            if 0 <= idx < total_samples:
                end = min(idx + pop_len, total_samples)
                track[idx:end] += pop[:end - idx]
                placed_times.append(zt)

    # 2. 골드 하이라이트 자막 시작 시점에 버블팝 (이미 줌인 시점에 배치된 것은 중복 방지)
    if ass_file and os.path.exists(ass_file):
        with open(ass_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("Dialogue:"):
                    parts = line.split(",", 9)
                    if len(parts) >= 10:
                        t_str = parts[1].strip()
                        h, m, s = t_str.split(":")
                        start_sec = int(h) * 3600 + int(m) * 60 + float(s)
                        text = parts[9]
                        idx = int(start_sec * SR)
                        if 0 <= idx < total_samples:
                            if "00E5FF" in text:
                                if not any(abs(start_sec - pt) < 0.4 for pt in placed_times):
                                    end = min(idx + pop_len, total_samples)
                                    track[idx:end] += pop[:end - idx]
                                    placed_times.append(start_sec)

    int_samples = (np.clip(track, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(out_wav, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(int_samples.tobytes())

    print(f"[generate_sfx] 01_미니멀_버블팝 SFX 생성 완료: {out_wav} (총 {len(placed_times)}곳 배치)")

def main():
    ap = argparse.ArgumentParser(description="미니멀 버블팝 효과음 트랙 생성")
    ap.add_argument("--ass", default=None)
    ap.add_argument("--duration", type=float, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--zoom-times", nargs="*", type=float, default=[])
    args = ap.parse_args()

    build_sfx_track(args.ass, args.duration, args.out, args.zoom_times)

if __name__ == "__main__":
    main()
