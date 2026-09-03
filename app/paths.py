"""번들 및 개발 공용 경로 및 바이너리 탐색 유틸리티.

PATH·시스템 표준 경로만으로는 whisper.cpp를 소스 빌드해 임의 폴더(예:
~/Desktop/Whisper/whisper.cpp)에 두는 흔한 설치 방식을 못 찾는다. SHORTS_WHISPER_BIN/
SHORTS_WHISPER_MODEL 환경변수 오버라이드 + 홈 디렉토리 얕은 글롭 탐색으로 보완한다.
"""
import glob
import os
import shutil
import sys

if getattr(sys, "frozen", False) or "RESOURCEPATH" in os.environ:
    BASE_DIR = os.environ.get("RESOURCEPATH") or os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

APP_DIR = os.path.join(BASE_DIR, "app")
CORE_DIR = os.path.join(BASE_DIR, "core")
PROMPTS_DIR = os.path.join(BASE_DIR, "prompts")
UI_HTML = os.path.join(APP_DIR, "ui", "index.html")
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

# whisper.cpp 는 brew 대신 소스 빌드로 임의 폴더에 두는 경우가 흔하다(예: ~/Desktop/Whisper/
# whisper.cpp, ~/whisper.cpp). PATH에 안 걸려도 찾도록 홈 디렉토리 얕은 글롭 탐색으로 보완한다.
_WHISPER_BIN_GLOBS = [
    "~/whisper.cpp/build/bin/whisper-cli",
    "~/*/whisper.cpp/build/bin/whisper-cli",
    "~/*/*/whisper.cpp/build/bin/whisper-cli",
]
_WHISPER_MODEL_GLOBS = [
    "~/whisper.cpp/models/ggml-large-v3.bin",
    "~/*/whisper.cpp/models/ggml-large-v3.bin",
    "~/*/*/whisper.cpp/models/ggml-large-v3.bin",
]

def augmented_path_str() -> str:
    return os.pathsep.join(_EXTRA_PATHS + [os.environ.get("PATH", "")])

def _glob_first_existing(patterns: list[str], min_size: int = 0) -> str | None:
    for pattern in patterns:
        for hit in sorted(glob.glob(os.path.expanduser(pattern))):
            if os.path.isfile(hit) and os.path.getsize(hit) > min_size:
                return hit
    return None

def find_binary(name: str) -> str | None:
    """환경변수 오버라이드 → PATH/추가 경로 → 홈 디렉토리 whisper.cpp 글롭 순으로 탐색."""
    if name in ("whisper-cli", "whisper-cpp", "whisper"):
        env_bin = os.environ.get("SHORTS_WHISPER_BIN")
        if env_bin and os.path.isfile(env_bin):
            return env_bin
    found = shutil.which(name, path=augmented_path_str())
    if found:
        return found
    if name in ("whisper-cli", "whisper-cpp", "whisper"):
        return _glob_first_existing(_WHISPER_BIN_GLOBS)
    return None

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
    """Whisper ggml 모델 경로 검색. 환경변수 오버라이드 → 번들/캐시 경로 → 홈 디렉토리 글롭."""
    env_model = os.environ.get("SHORTS_WHISPER_MODEL")
    if env_model and os.path.isfile(env_model):
        return env_model
    candidates = [
        os.path.join(MODELS_DIR, "ggml-large-v3.bin"),
        os.path.join(BASE_DIR, "ggml-large-v3.bin"),
        os.path.expanduser("~/.cache/whisper/ggml-large-v3.bin"),
        os.path.expanduser("~/.cache/whisper.cpp/ggml-large-v3.bin"),
    ]
    for c in candidates:
        if os.path.isfile(c) and os.path.getsize(c) > 1000:
            return c
    return _glob_first_existing(_WHISPER_MODEL_GLOBS, min_size=1000)
