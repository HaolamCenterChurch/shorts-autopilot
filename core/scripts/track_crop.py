#!/usr/bin/env python3
"""클린 마스터(16:9)에서 인물을 추적해 9:16 크롭 궤적을 만든다."""
import argparse
import json
import os
import sys

import cv2
import numpy as np

FPS = 30
DET_W, DET_H = 960, 540
SCALE = 3840 / DET_W  # 원본 3840 스케일로 되돌리는 배율 (=4)
MAX_SPEED_PX_S = 180.0
EMA_ALPHA = 0.08
DEADZONE_RATIO = 0.22
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
    prev_cx, prev_cy = None, None
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
                    # 세로/가로 비율 체크(사람 얼굴은 세로가 긴 편, 헤어 악세서리 등 원형/가로형 오검출 제외)
                    valid_faces = [f for f in faces if f[14] >= 0.55 and (f[3] / f[2]) >= 0.90]
                    if not valid_faces:
                        valid_faces = faces

                    if prev_cx is None:
                        best = max(valid_faces, key=lambda f: f[14])
                    else:
                        def face_cost(f):
                            cx_val = (f[0] + f[2] / 2) * SCALE
                            cy_val = (f[1] + f[3] / 2) * SCALE
                            dist = np.hypot(cx_val - prev_cx, cy_val - prev_cy)
                            return dist - f[14] * 250.0
                        best = min(valid_faces, key=face_cost)

                    x, y, w, h = best[0], best[1], best[2], best[3]
                    cx = (x + w / 2) * SCALE
                    cy = (y + h / 2) * SCALE
                    fw = w * SCALE
                    prev_cx, prev_cy = cx, cy
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


def snap_cuts_to_visual(in_path, cuts, fps=FPS):
    """비디오 스트림을 직접 확인하여 cuts 시점을 실제 픽셀 차이가 발생하는 프레임으로 정밀 동기화."""
    if not os.path.exists(in_path) or not cuts:
        return cuts
    cap = cv2.VideoCapture(in_path)
    if not cap.isOpened():
        return cuts
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    snapped = [0.0]
    for c in cuts:
        if c <= 0.1 or c >= (total_frames - 1) / float(fps) - 0.1:
            continue
        approx_f = int(round(c * fps))
        start_f = max(0, approx_f - 4)
        end_f = min(total_frames - 1, approx_f + 5)
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_f)
        diffs = []
        prev = None
        for f in range(start_f, end_f + 1):
            ok, frame = cap.read()
            if not ok:
                break
            if prev is not None:
                d = float(np.mean(np.abs(frame.astype(float) - prev.astype(float))))
                diffs.append((f, d))
            prev = frame
        if diffs:
            best_f, best_d = max(diffs, key=lambda x: x[1])
            if best_d >= 3.5:
                snapped.append(best_f / float(fps))
            else:
                snapped.append(c)
        else:
            snapped.append(c)
    cap.release()
    return sorted(list(set(snapped)))


def find_shot_boundaries(in_path, cuts_path, plan_path, total_duration):
    """cuts.json 및 plan.json 기반으로 마스터 영상의 모든 컷(샷) 경계를 추출한다."""
    master_cuts = [0.0]

    # 1. plan.json 의 원본 세그먼트 경계 추출
    raw_cuts = []
    if plan_path and os.path.exists(plan_path):
        try:
            with open(plan_path, "r", encoding="utf-8") as f:
                pdata = json.load(f)
            raw_cum = 0.0
            for s, e in pdata.get("segments", [])[:-1]:
                raw_cum += (e - s)
                raw_cuts.append(raw_cum)
        except Exception as err:
            log(f"[track_crop] plan.json 읽기 실패: {err}")

    # 2. cuts.json (무음 제거 컷 구간) 반영
    if cuts_path and os.path.exists(cuts_path):
        try:
            with open(cuts_path, "r", encoding="utf-8") as f:
                cdata = json.load(f)
            master_t = 0.0
            for k0, k1 in cdata.get("keeps", []):
                dur = k1 - k0
                for rc in raw_cuts:
                    if k0 < rc < k1:
                        split_master = master_t + (rc - k0)
                        master_cuts.append(split_master)
                master_t += dur
                master_cuts.append(master_t)
        except Exception as err:
            log(f"[track_crop] cuts.json 읽기 실패: {err}")

    # 비디오 실제 픽셀 변화 프레임으로 컷 경계 정밀 스냅
    master_cuts = snap_cuts_to_visual(in_path, master_cuts, FPS)

    master_cuts.append(total_duration)
    master_cuts = sorted(list(set(master_cuts)))

    shots = []
    for i in range(len(master_cuts) - 1):
        c0, c1 = master_cuts[i], master_cuts[i + 1]
        if c1 - c0 <= 0.15:
            if shots:
                shots[-1] = (shots[-1][0], c1)
            else:
                shots.append((c0, c1))
        else:
            shots.append((c0, c1))

    if not shots:
        shots = [(0.0, total_duration)]
    return shots


def build_cut_aware_expr(cut_infos, crop_w):
    """컷 경계를 인지하는 최종 ffmpeg crop expression 생성.

    1. 정적 샷(Static Shot): 프레임 내내 고정 좌표 유지
    2. 이동 샷(Walking Shot): 샷 내부에서만 순방향 S-커브로 이동 (절대 컷 경계를 넘지 않음)
    3. 컷 전환점: 컷 프레임에서 정확하게 다음 샷 구도로 순시 전환 (자연스러운 점프 컷)
    """
    if not cut_infos:
        return f"{1920 - crop_w / 2:.1f}", [(0.0, 1920 - crop_w / 2)]

    first = cut_infos[0]
    cur_x = first[3] if first[0] == "static" else first[3][0][1]
    expr = f"{cur_x:.1f}"
    all_points = [(0.0, cur_x)]

    for c_type, t0, t1, data in cut_infos:
        if c_type == "static":
            dx = data - cur_x
            if abs(dx) >= 1.0 and t0 > 0:
                cut_frame = int(round(t0 * FPS))
                safe_t = (cut_frame - 0.5) / float(FPS)
                expr += f"+({dx:.1f})*gte(t\\,{safe_t:.4f})"
                cur_x = data
            all_points.append((t0, cur_x))
        elif c_type == "walk":
            start_x = data[0][1]
            dx_cut = start_x - cur_x
            if abs(dx_cut) >= 1.0 and t0 > 0:
                cut_frame = int(round(t0 * FPS))
                safe_t = (cut_frame - 0.5) / float(FPS)
                expr += f"+({dx_cut:.1f})*gte(t\\,{safe_t:.4f})"
                cur_x = start_x
            all_points.append((t0, cur_x))

            for j in range(1, len(data)):
                pt_t0, pt_x0 = data[j - 1]
                pt_t1, pt_x1 = data[j]
                dt = pt_t1 - pt_t0
                dx = pt_x1 - pt_x0
                if abs(dx) >= 1.0 and dt > 0:
                    # 등속도 선형 이동: dx/dt = 일정 (가속/감속 없는 완벽한 등속 글라이드)
                    expr += f"+({dx:.1f})*clip((t-{pt_t0:.4f})/{dt:.4f}\\,0\\,1)"
                    cur_x += dx
                    all_points.append((pt_t1, cur_x))

    return expr, all_points


def main():
    ap = argparse.ArgumentParser(description="인물 추적 9:16 크롭 궤적 생성 (컷 인지형 순방향 트래킹)")
    ap.add_argument("--in", dest="in_path", required=True)
    ap.add_argument("--out-cmd", required=True)
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--crop-w", type=int, default=1216)
    ap.add_argument("--sample-fps", type=float, default=10)
    ap.add_argument("--model", required=True)
    ap.add_argument("--cuts", default=None, help="cuts.json 경로")
    ap.add_argument("--plan", default=None, help="plan.json 경로")
    args = ap.parse_args()

    crop_w = args.crop_w
    total_frames, samples, det_rate = detect_faces(
        args.in_path, args.model, args.sample_fps)
    total_dur = total_frames / float(FPS)

    cuts_path = args.cuts or (args.in_path + ".cuts.json")
    plan_path = args.plan

    if not samples:
        log("[track_crop] 검출 샘플 없음 -> 화면 중앙 고정")
        x_frame = np.full(total_frames, np.clip(1920 - crop_w / 2, 0, 3840 - crop_w))
        x_frame = (np.round(x_frame / 2) * 2).astype(int)
        expr = f"{x_frame[0]:.1f}"
        save_outputs(args, total_frames, x_frame, expr, [(0.0, float(x_frame[0]))], crop_w, det_rate)
        return

    idxs = [s[0] for s in samples]
    cx_vals = [s[1] for s in samples]
    w_vals = [s[2] for s in samples]

    if all(np.isnan(v) for v in cx_vals):
        log("[track_crop] 전부 미검출 -> 화면 중앙 고정")
        x_frame = np.full(total_frames, np.clip(1920 - crop_w / 2, 0, 3840 - crop_w))
        x_frame = (np.round(x_frame / 2) * 2).astype(int)
        expr = f"{x_frame[0]:.1f}"
        save_outputs(args, total_frames, x_frame, expr, [(0.0, float(x_frame[0]))], crop_w, det_rate)
        return

    cx_interp = interp_nan(idxs, cx_vals)
    w_interp = interp_nan(idxs, w_vals)
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

    # 컷 경계 탐색
    shots = find_shot_boundaries(args.in_path, cuts_path, plan_path, total_dur)
    log(f"[track_crop] 총 {len(shots)}개 컷(샷) 분할 프레이밍 시작")

    cut_infos = []
    last_static_x = None

    for c0, c1 in shots:
        dur = c1 - c0
        f0 = int(round(c0 * FPS))
        f1 = min(int(round(c1 * FPS)), total_frames)
        if f0 >= f1:
            continue

        clip_cx = face_cx_full[f0:f1]
        clip_w = face_w_full[f0:f1]
        spread = float(np.max(clip_cx) - np.min(clip_cx))

        # 정적 샷(발화 중심 제자리) vs 이동 샷(실제 칠판 이동)
        if dur < 3.5 or spread <= 280.0:
            med_cx = float(np.median(clip_cx))
            cam_x = np.clip(med_cx - crop_w / 2.0, 0, 3840 - crop_w)
            cam_x = round(cam_x / 2.0) * 2.0
            # 이전 정적 샷과 위치 차이가 적으면(140px 미만) 동일 위치로 락(Lock)하여 호흡 컷 시 불필요한 미세 점프 방지
            if last_static_x is not None and abs(cam_x - last_static_x) < 140.0:
                cam_x = last_static_x
            last_static_x = cam_x
            cut_infos.append(("static", c0, c1, cam_x))
        else:
            n_clip = f1 - f0
            # 이동 샷: 등속도(Constant Speed) 트래킹
            # 이동할 때는 일정 속도(PAN_SPEED = 200.0 px/s)로 직선 등속 이동(Linear Glide)
            # 가속/감속이나 속도 급변 없이 일정한 속도로만 편안하게 이동한다.
            PAN_SPEED = 480.0  # px / sec (민첩한 등속 패닝 속도: 걷는 속도에 맞춰 신속하게 도달)
            step = PAN_SPEED / FPS
            deadzone = 0.10 * crop_w  # ~120px (인물이 항상 중앙 10% 내에 잘 머물도록 좁힌 데드존)
            cam = np.clip(clip_cx[0] - crop_w / 2.0, 0, 3840 - crop_w)
            out = np.empty(n_clip)
            for cf in range(n_clip):
                target = np.clip(clip_cx[cf] - crop_w / 2.0, 0, 3840 - crop_w)
                diff = target - cam
                if abs(diff) > deadzone:
                    direction = 1.0 if diff > 0 else -1.0
                    cam += direction * step
                    if (direction > 0 and cam > target) or (direction < 0 and cam < target):
                        cam = target
                out[cf] = cam

            # 선형 등속 단순화
            t_arr = np.arange(n_clip) / float(FPS) + c0

            def rdp_linear(i0, i1, err=4.0):
                if i1 <= i0 + 1:
                    return [i0, i1]
                dt = t_arr[i1] - t_arr[i0]
                interp = out[i0] if dt <= 0 else out[i0] + (out[i1] - out[i0]) * (t_arr[i0:i1+1] - t_arr[i0]) / dt
                diff = np.abs(out[i0:i1+1] - interp)
                mid = i0 + int(np.argmax(diff))
                if diff[mid - i0] > err:
                    return rdp_linear(i0, mid, err)[:-1] + rdp_linear(mid, i1, err)
                return [i0, i1]

            idxs = rdp_linear(0, n_clip - 1, err=4.0)
            raw_pts = [(float(t_arr[idx]), float(out[idx])) for idx in idxs]

            # 50px 미만의 미세 흔들림은 이전 정지 좌표로 스냅 (불필요한 미세 이동 방지)
            cleaned = [raw_pts[0]]
            for pt in raw_pts[1:]:
                if abs(pt[1] - cleaned[-1][1]) < 50.0:
                    cleaned.append((pt[0], cleaned[-1][1]))
                else:
                    cleaned.append(pt)

            # 연속 정지 구간 병합
            merged = [cleaned[0]]
            for pt in cleaned[1:]:
                if abs(pt[1] - merged[-1][1]) < 1.0:
                    merged[-1] = (pt[0], merged[-1][1])
                else:
                    merged.append(pt)

            cut_infos.append(("walk", c0, c1, merged))
            last_static_x = float(merged[-1][1])

    expr, points = build_cut_aware_expr(cut_infos, crop_w)

    # JSON 호환을 위해 프레임별 x 좌표 생성
    x_frame = np.full(total_frames, -1, dtype=int)
    for c_type, t0, t1, data in cut_infos:
        f0 = int(round(t0 * FPS))
        f1 = min(int(round(t1 * FPS)), total_frames)
        if f0 >= f1:
            continue
        if c_type == "static":
            x_frame[f0:f1] = int(round(data))
        elif c_type == "walk":
            t_arr = np.arange(f0, f1) / float(FPS)
            pts = data
            vals = np.full(f1 - f0, pts[0][1], dtype=np.float64)
            for j in range(1, len(pts)):
                pt_t0, pt_x0 = pts[j - 1]
                pt_t1, pt_x1 = pts[j]
                dt = pt_t1 - pt_t0
                dx = pt_x1 - pt_x0
                if abs(dx) >= 1.0 and dt > 0:
                    u = np.clip((t_arr - pt_t0) / dt, 0.0, 1.0)
                    vals += dx * u
            x_frame[f0:f1] = np.clip(np.round(vals), 0, 3840 - crop_w).astype(int)

    # 미할당 프레임(경계 라운딩 등) 전방 채우기
    default_x = int(round(cut_infos[0][3] if cut_infos[0][0] == "static" else cut_infos[0][3][0][1])) if cut_infos else 1920 - crop_w // 2
    for f in range(total_frames):
        if x_frame[f] < 0:
            x_frame[f] = x_frame[f - 1] if f > 0 else default_x

    log(f"[track_crop] 컷 인지형 프레이밍 완료: 총 {len(cut_infos)}개 샷, 키프레임 {len(points)}개")
    save_outputs(args, total_frames, x_frame, expr, points, crop_w, det_rate)


def save_outputs(args, total_frames, x_frame, expr, points, crop_w, det_rate):
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
        f"키프레임={len(points)}개")


if __name__ == "__main__":
    main()
