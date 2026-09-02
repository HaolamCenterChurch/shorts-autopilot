#!/usr/bin/env python3
"""클린 마스터(16:9)에서 인물을 추적해 9:16 크롭 궤적을 만든다."""
import argparse
import json
import sys

import cv2
import numpy as np

FPS = 30
DET_W, DET_H = 960, 540
SCALE = 3840 / DET_W  # 원본 3840 스케일로 되돌리는 배율 (=4)
MAX_SPEED_PX_S = 250.0
EMA_ALPHA = 0.12
DEADZONE_RATIO = 0.10
FACE_MARGIN_RATIO = 1.2


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def detect_faces(video_path, model_path, sample_fps):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"영상을 열 수 없다: {video_path}")

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    src_fps = cap.get(cv2.CAP_PROP_FPS) or FPS
    step = max(1, round(src_fps / sample_fps))

    detector = cv2.FaceDetectorYN.create(
        model_path, "", (DET_W, DET_H), 0.5, 0.3, 5000)
    detector.setInputSize((DET_W, DET_H))

    cx_samples = []  # (frame_idx, cx, w) 얼굴 중심/너비, 3840 스케일
    frame_idx = 0
    sampled_count = 0
    detected_count = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if frame_idx % step == 0:
            ok2, frame = cap.retrieve()
            if ok2:
                small = cv2.resize(frame, (DET_W, DET_H))
                _, faces = detector.detect(small)
                sampled_count += 1
                if faces is not None and len(faces) > 0:
                    best = max(faces, key=lambda f: f[2] * f[3])
                    x, y, w, h = best[0], best[1], best[2], best[3]
                    cx = (x + w / 2) * SCALE
                    fw = w * SCALE
                    cx_samples.append((frame_idx, cx, fw))
                    detected_count += 1
                else:
                    cx_samples.append((frame_idx, np.nan, np.nan))
        frame_idx += 1
    cap.release()

    if total_frames <= 0:
        total_frames = frame_idx

    det_rate = (detected_count / sampled_count * 100) if sampled_count else 0.0
    log(f"[track_crop] 총 프레임 {total_frames}, 샘플 {sampled_count}개, "
        f"검출률 {det_rate:.1f}%")
    return total_frames, cx_samples, det_rate


def interp_nan(idxs, vals):
    idxs = np.asarray(idxs, dtype=np.float64)
    vals = np.asarray(vals, dtype=np.float64)
    valid = ~np.isnan(vals)
    if not valid.any():
        return np.full_like(vals, np.nan)
    if valid.all():
        return vals
    out = vals.copy()
    out[~valid] = np.interp(idxs[~valid], idxs[valid], vals[valid])
    return out


def moving_median(arr, window):
    if window < 3:
        return arr.copy()
    if window % 2 == 0:
        window += 1
    n = len(arr)
    half = window // 2
    out = np.empty(n)
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        out[i] = np.median(arr[lo:hi])
    return out


def gaussian_kernel(sigma):
    radius = max(1, int(round(sigma * 3)))
    xs = np.arange(-radius, radius + 1)
    k = np.exp(-(xs ** 2) / (2 * sigma ** 2))
    k /= k.sum()
    return k


def gaussian_smooth(arr, sigma):
    k = gaussian_kernel(sigma)
    radius = len(k) // 2
    padded = np.pad(arr, radius, mode="edge")
    out = np.convolve(padded, k, mode="valid")
    return out


def apply_hard_constraint(cam, target, face_w, crop_w):
    """얼굴 박스(마진 포함)가 크롭 안에 들어오도록 cam 을 최소 이동으로 보정."""
    left_bound = target - FACE_MARGIN_RATIO * face_w
    right_bound = target + FACE_MARGIN_RATIO * face_w
    crop_left = cam - crop_w / 2
    crop_right = cam + crop_w / 2
    if left_bound < crop_left:
        cam -= (crop_left - left_bound)
    crop_left = cam - crop_w / 2
    crop_right = cam + crop_w / 2
    if right_bound > crop_right:
        cam += (right_bound - crop_right)
    return cam


def compute_cam_trajectory(face_cx, face_w, crop_w):
    n = len(face_cx)
    cam_arr = np.empty(n)
    cam = float(face_cx[0]) if n > 0 else 1920.0
    max_step = MAX_SPEED_PX_S / FPS
    for t in range(n):
        target = face_cx[t]
        if abs(target - cam) >= DEADZONE_RATIO * crop_w:
            desired = cam + (target - cam) * EMA_ALPHA
            step = np.clip(desired - cam, -max_step, max_step)
            cam += step
        cam = apply_hard_constraint(cam, target, face_w[t], crop_w)
        cam_arr[t] = cam
    return cam_arr


def enforce_corridor(x_desired, face_cx, face_w, crop_w, max_step,
                     frame_w=3840):
    """얼굴이 절대 크롭 밖으로 못 나가면서도 카메라가 끊기지 않게 움직이는 궤적.

    ★스무딩 뒤에 제약을 프레임마다 '즉시 보정'으로 걸면, 인물이 갑자기 움직인
      프레임에서 크롭이 60px 씩 순간이동한다(2026-09-02 실측: t=45.8s 에서 +62px).
      그래서 프레임별 허용구간 [lo,hi] 를 만든 뒤 **뒤에서 앞으로 전파**해
      "지금부터 천천히 움직여도 제때 도착하는" 구간으로 좁힌다. 그 안에서만
      속도 제한을 걸어 따라가므로 제약 위반도, 순간이동도 없다.
    """
    n = len(x_desired)
    margin = FACE_MARGIN_RATIO * np.asarray(face_w, dtype=np.float64)
    face_cx = np.asarray(face_cx, dtype=np.float64)
    x_max_abs = float(frame_w - crop_w)

    lo = np.maximum(0.0, face_cx + margin - crop_w)   # x 는 이것보다 커야 한다
    hi = np.minimum(x_max_abs, face_cx - margin)      # x 는 이것보다 작아야 한다

    # 마진이 너무 커서 구간이 뒤집히면 얼굴 중앙 정렬로 타협한다
    mid = np.clip(face_cx - crop_w / 2.0, 0.0, x_max_abs)
    bad = lo > hi
    lo[bad] = mid[bad]
    hi[bad] = mid[bad]

    for t in range(n - 2, -1, -1):
        lo[t] = max(lo[t], lo[t + 1] - max_step)
        hi[t] = min(hi[t], hi[t + 1] + max_step)
        if lo[t] > hi[t]:
            hi[t] = lo[t]

    out = np.empty(n)
    prev = float(np.clip(x_desired[0], lo[0], hi[0]))
    out[0] = prev
    for t in range(1, n):
        cand = float(np.clip(x_desired[t], prev - max_step, prev + max_step))
        cand = float(np.clip(cand, lo[t], hi[t]))
        out[t] = cand
        prev = cand
    return out


def quantize_points(x, max_points, fps=FPS):
    """변화점이 max_points 이하가 될 때까지 x 를 계단으로 양자화한다.

    변화량이 항상 양자화 스텝과 같아지므로, 예전처럼 '가장 작은 변화를 지운다'
    방식과 달리 큰 점프가 생기지 않는다.
    """
    step = 2
    while True:
        xq = np.round(np.asarray(x, dtype=np.float64) / step) * step
        n_changes = 1 + int(np.count_nonzero(np.diff(xq)))
        if n_changes <= max_points or step >= 64:
            return xq.astype(int), step
        step += 2


def build_expr(x_frame, max_points=400):
    n = len(x_frame)
    points = [(0.0, float(x_frame[0]))]
    for i in range(1, n):
        if x_frame[i] != x_frame[i - 1]:
            points.append((i / FPS, float(x_frame[i])))

    while len(points) > max_points:
        # 값 변화가 가장 작은 지점을 병합(제거)해서 개수를 줄인다
        min_idx, min_delta = 1, None
        for i in range(1, len(points)):
            delta = abs(points[i][1] - points[i - 1][1])
            if min_delta is None or delta < min_delta:
                min_delta = delta
                min_idx = i
        del points[min_idx]

    expr = f"{points[0][1]:.1f}"
    max_jump = 0.0
    for i in range(1, len(points)):
        prev_v = points[i - 1][1]
        v = points[i][1]
        t = points[i][0]
        max_jump = max(max_jump, abs(v - prev_v))
        expr += f"+({v - prev_v:.1f})*gte(t\\,{t:.4f})"
    if max_jump > 24:
        log(f"[track_crop] ⚠ 크롭이 한 번에 {max_jump:.0f}px 튄다 — 확인 필요")
    return expr, points


def main():
    ap = argparse.ArgumentParser(description="인물 추적 9:16 크롭 궤적 생성")
    ap.add_argument("--in", dest="in_path", required=True)
    ap.add_argument("--out-cmd", required=True)
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--crop-w", type=int, default=1216)
    ap.add_argument("--sample-fps", type=float, default=10)
    ap.add_argument("--model", required=True)
    args = ap.parse_args()

    crop_w = args.crop_w
    total_frames, samples, det_rate = detect_faces(
        args.in_path, args.model, args.sample_fps)

    if not samples:
        log("[track_crop] 검출 샘플 없음 -> 화면 중앙 고정")
        x_frame = np.full(total_frames, np.clip(1920 - crop_w / 2, 0,
                                                  3840 - crop_w))
        x_frame = (np.round(x_frame / 2) * 2).astype(int)
        save_outputs(args, total_frames, x_frame, crop_w, det_rate)
        return

    idxs = [s[0] for s in samples]
    cx_vals = [s[1] for s in samples]
    w_vals = [s[2] for s in samples]

    if all(np.isnan(v) for v in cx_vals):
        log("[track_crop] 전부 미검출 -> 화면 중앙 고정")
        x_frame = np.full(total_frames, np.clip(1920 - crop_w / 2, 0,
                                                  3840 - crop_w))
        x_frame = (np.round(x_frame / 2) * 2).astype(int)
        save_outputs(args, total_frames, x_frame, crop_w, det_rate)
        return

    cx_interp = interp_nan(idxs, cx_vals)
    w_interp = interp_nan(idxs, w_vals)
    # 결측 폭은 대체값(중앙값)으로 채운다
    if np.isnan(w_interp).any():
        fill_w = np.nanmedian(w_interp) if not np.all(np.isnan(w_interp)) else crop_w * 0.3
        w_interp = np.where(np.isnan(w_interp), fill_w, w_interp)

    med_window = max(1, round(1.5 * args.sample_fps))
    cx_med = moving_median(cx_interp, med_window)
    w_med = moving_median(w_interp, med_window)

    sample_times = np.array(idxs, dtype=np.float64)
    frame_times = np.arange(total_frames, dtype=np.float64)
    face_cx_full = np.interp(frame_times, sample_times, cx_med)
    face_w_full = np.interp(frame_times, sample_times, w_med)

    cam_arr = compute_cam_trajectory(face_cx_full, face_w_full, crop_w)
    x_full = np.clip(cam_arr - crop_w / 2, 0, 3840 - crop_w)

    sigma = (0.7 * FPS) / 3.0
    x_smooth = gaussian_smooth(x_full, sigma)

    # 스무딩된 궤적을 '실현 가능 구간' 안에서 속도 제한으로 따라간다.
    max_step = MAX_SPEED_PX_S / FPS
    x_final = enforce_corridor(x_smooth, face_cx_full, face_w_full, crop_w,
                               max_step)
    x_frame, q_step = quantize_points(x_final, 400)
    x_frame = np.clip(x_frame, 0, int((3840 - crop_w) // 2 * 2))
    log(f"[track_crop] 양자화 스텝 {q_step}px")

    save_outputs(args, total_frames, x_frame, crop_w, det_rate)


def save_outputs(args, total_frames, x_frame, crop_w, det_rate):
    expr, points = build_expr(x_frame)

    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump({
            "fps": FPS,
            "crop_w": crop_w,
            "x": [int(v) for v in x_frame],
            "expr": expr,
        }, f, ensure_ascii=False)

    with open(args.out_cmd, "w", encoding="utf-8") as f:
        for t, v in points:
            f.write(f"{t:.4f} crop x {v:.0f};\n")

    move_total = float(np.sum(np.abs(np.diff(x_frame)))) if len(x_frame) > 1 else 0.0
    log(f"[완료] 검출률={det_rate:.1f}% x_min={int(x_frame.min())} "
        f"x_max={int(x_frame.max())} 이동총량={move_total:.0f}px "
        f"변화점={len(points)}개")


if __name__ == "__main__":
    main()
