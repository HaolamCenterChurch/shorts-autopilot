"""번들 및 개발 공용 경로 및 바이너리 탐색 유틸리티.

하드코딩된 절대경로를 금지하며 shutil.which 및 시스템 표준 경로 탐색을 사용합니다.
"""
import os
import shutil
import sys

if getattr(sys, "frozen", False) or "RESOURCEPATH" in os.environ:
    BASE_DIR = os.environ.get("RESOURCEPATH") or os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ui/ 는 app 패키지 밖의 최상위 폴더(app/ui 아님). "app" 패키지 안에 두면 py2app 이
# site-packages.zip 안에 동명의 빈 app 패키지를 만들어 진짜 app 패키지를 가린다.
UI_HTML = os.path.join(BASE_DIR, "ui", "index.html")

APP_DIR = os.path.join(BASE_DIR, "app")
CORE_DIR = os.path.join(BASE_DIR, "core")
PROMPTS_DIR = os.path.join(BASE_DIR, "prompts")
MODELS_DIR = os.path.join(BASE_DIR, "models")

# 기본 출력 폴더
DEFAULT_OUTPUT_ROOT = os.path.expanduser("~/Movies/ShortsAutopilot")

_EXTRA_PATHS = [
    os.path.expanduser("~/.local/bin"),
    os.path.expanduser("~/bin"),
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
]

def augmented_path_str() -> str:
    return os.pathsep.join(_EXTRA_PATHS + [os.environ.get("PATH", "")])

def find_binary(name: str) -> str | None:
    """PATH 및 추가 경로에서 실행 파일 위치를 탐색."""
    return shutil.which(name, path=augmented_path_str())

def get_yunet_model_path() -> str | None:
    """프로젝트 models/ 또는 시스템 기본 위치에서 YuNet 모델 검색."""
    candidates = [
        os.path.join(MODELS_DIR, "yunet.onnx"),
        os.path.join(BASE_DIR, "yunet.onnx"),
        os.path.expanduser("~/.cache/shorts_autopilot/yunet.onnx"),
    ]
    for c in candidates:
        if os.path.isfile(c) and os.path.getsize(c) > 1000:
            return c
    return None

def get_fonts_dirs() -> list[str]:
    """OS별 시스템/사용자 폰트 폴더 후보 목록(존재하는 것만)."""
    if sys.platform == "darwin":
        candidates = [
            os.path.expanduser("~/Library/Fonts"),
            "/Library/Fonts",
            "/System/Library/Fonts",
        ]
    elif sys.platform.startswith("linux"):
        candidates = [
            os.path.expanduser("~/.fonts"),
            os.path.expanduser("~/.local/share/fonts"),
            "/usr/share/fonts",
            "/usr/local/share/fonts",
        ]
    else:
        candidates = [os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")]
    return [c for c in candidates if os.path.isdir(c)]

def get_whisper_model_path() -> str | None:
    """Whisper ggml 모델 경로 검색."""
    candidates = [
        os.path.join(MODELS_DIR, "ggml-large-v3.bin"),
        os.path.join(BASE_DIR, "ggml-large-v3.bin"),
        os.path.expanduser("~/.cache/whisper/ggml-large-v3.bin"),
        os.path.expanduser("~/.cache/whisper.cpp/ggml-large-v3.bin"),
    ]
    for c in candidates:
        if os.path.isfile(c) and os.path.getsize(c) > 1000:
            return c
    return None
