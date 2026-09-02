"""시스템 의존성 검사 및 설치 가이드 (ffmpeg, whisper, python libs, yunet)."""
import os
import platform
import subprocess
import sys

from app.paths import find_binary, get_yunet_model_path, get_whisper_model_path, MODELS_DIR

YUNET_URL = (
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx"
)

def check_python_packages():
    missing = []
    try:
        import cv2
    except ImportError:
        missing.append("opencv-python (또는 opencv-python-headless)")
    try:
        import numpy
    except ImportError:
        missing.append("numpy")
    try:
        import webview
    except ImportError:
        missing.append("pywebview")
    return missing

def diagnose() -> dict:
    os_name = platform.system()
    ffmpeg_bin = find_binary("ffmpeg")
    ffprobe_bin = find_binary("ffprobe")
    whisper_bin = find_binary("whisper-cli") or find_binary("whisper") or find_binary("whisper-cpp")
    
    missing_pkgs = check_python_packages()
    yunet_path = get_yunet_model_path()
    whisper_model = get_whisper_model_path()

    items = []
    
    # 1. FFmpeg
    items.append({
        "name": "FFmpeg",
        "ok": bool(ffmpeg_bin and ffprobe_bin),
        "detail": ffmpeg_bin or "미설치 (동영상/오디오 처리 필수)",
        "guide": (
            "brew install ffmpeg" if os_name == "Darwin"
            else "sudo apt update && sudo apt install -y ffmpeg" if os_name == "Linux"
            else "https://ffmpeg.org/download.html 에서 다운로드 후 PATH 등록"
        )
    })

    # 2. Whisper CLI
    items.append({
        "name": "Whisper CLI",
        "ok": bool(whisper_bin),
        "detail": whisper_bin or "미설치 (음성 전사/타임스탬프 추출 필수)",
        "guide": (
            "brew install whisper-cpp 또는 whisper.cpp 빌드" if os_name == "Darwin"
            else "whisper.cpp 저장소 클론 후 make 빌드"
        )
    })

    # 3. YuNet 얼굴 인식 모델
    items.append({
        "name": "YuNet Face Model",
        "ok": bool(yunet_path),
        "detail": yunet_path or "미발견 (세로 9:16 인물 추적 크롭용)",
        "guide": f"curl -L -o models/yunet.onnx {YUNET_URL}"
    })

    # 4. Whisper 모델
    items.append({
        "name": "Whisper Model (ggml-large-v3)",
        "ok": bool(whisper_model),
        "detail": whisper_model or "미발견 (models/ggml-large-v3.bin)",
        "guide": "whisper.cpp models/download-ggml-model.sh large-v3 다운로드"
    })

    # 5. 파이썬 라이브러리
    items.append({
        "name": "Python Packages",
        "ok": len(missing_pkgs) == 0,
        "detail": "모두 설치됨" if not missing_pkgs else f"누락: {', '.join(missing_pkgs)}",
        "guide": f"{sys.executable} -m pip install -r requirements.txt"
    })

    all_ok = all(it["ok"] for it in items)
    return {
        "all_ok": all_ok,
        "os": os_name,
        "items": items
    }

def auto_fix() -> dict:
    """가능한 의존성을 자동 다운로드 및 설치 시도."""
    results = []
    
    # 1. models 디렉토리 및 YuNet 모델 다운로드
    yunet_path = get_yunet_model_path()
    if not yunet_path:
        os.makedirs(MODELS_DIR, exist_ok=True)
        target = os.path.join(MODELS_DIR, "yunet.onnx")
        try:
            cmd = ["curl", "-L", "-o", target, YUNET_URL]
            subprocess.run(cmd, check=True, capture_output=True)
            if os.path.isfile(target) and os.path.getsize(target) > 1000:
                results.append("YuNet 모델 다운로드 성공")
            else:
                results.append("YuNet 모델 다운로드 실패 (LFS 포인터 확인 필요)")
        except Exception as e:
            results.append(f"YuNet 다운로드 에러: {e}")

    # 2. brew 자동 설치 시도 (macOS)
    if os_name == "Darwin":
        ffmpeg_bin = find_binary("ffmpeg")
        if not ffmpeg_bin:
            brew_bin = find_binary("brew")
            if brew_bin:
                try:
                    subprocess.run([brew_bin, "install", "ffmpeg"], check=True, capture_output=True)
                    results.append("Homebrew로 FFmpeg 설치 완료")
                except Exception as e:
                    results.append(f"Homebrew FFmpeg 설치 실패: {e}")

    # 3. pip 패키지 설치 시도
    missing_pkgs = check_python_packages()
    if missing_pkgs:
        try:
            cmd = [sys.executable, "-m", "pip", "install", "pywebview", "opencv-python-headless", "numpy"]
            subprocess.run(cmd, check=True, capture_output=True)
            results.append("Python 필수 패키지 설치 완료")
        except Exception as e:
            results.append(f"pip 설치 에러: {e}")

    return {"results": results, "status": diagnose()}

if __name__ == "__main__":
    import json
    import argparse
    parser = argparse.ArgumentParser(description="Shorts Autopilot 의존성 검사기")
    parser.add_argument("--auto", action="store_true", help="가능한 의존성 자동 설치 시도")
    args = parser.parse_args()

    if args.auto:
        res = auto_fix()
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        diag = diagnose()
        print(json.dumps(diag, ensure_ascii=False, indent=2))
