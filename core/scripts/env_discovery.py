#!/usr/bin/env python3
"""환경별 도구, 모델, 폰트 동적 탐색 유틸리티.

절대 하드코딩된 개인 디렉토리(/Users/caleb/...)를 쓰지 않고,
1) 환경변수 오버라이드
2) 로컬 models/ 폴더
3) 시스템 PATH 및 패키지 매니저(Homebrew, APT 등)
4) 표준 캐시 디렉토리(~/.cache/...)
순으로 안전하게 자동 감지합니다.
"""
import glob
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(HERE))

def find_binary(name: str, env_key: str = None, extra_globs: list[str] = None) -> str:
    """바이너리 실행 파일 경로 탐색."""
    if env_key and os.environ.get(env_key):
        cand = os.environ.get(env_key)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand

    # PATH 탐색
    p = shutil.which(name)
    if p:
        return p

    # OS별 표준 경로
    standard_paths = [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        os.path.expanduser("~/.local/bin"),
        os.path.expanduser("~/bin"),
    ]
    for pref in standard_paths:
        cand = os.path.join(pref, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand

    # 홈 디렉토리 빌드 경로 글롭
    if extra_globs:
        for g in extra_globs:
            for hit in sorted(glob.glob(os.path.expanduser(g))):
                if os.path.isfile(hit) and os.access(hit, os.X_OK):
                    return hit

    raise FileNotFoundError(
        f"필수 실행 도구 '{name}'을(를) 찾을 수 없습니다. "
        f"PATH에 추가하거나 환경변수 {env_key or name.upper()}을(를) 설정하세요."
    )


def find_file(name: str, env_key: str, search_globs: list[str], min_size: int = 1000) -> str:
    """모델 파일 등 정적 에셋 경로 탐색."""
    if env_key and os.environ.get(env_key):
        cand = os.environ.get(env_key)
        if os.path.isfile(cand) and os.path.getsize(cand) > min_size:
            return cand

    # 프로젝트 models/ 및 상대 경로
    local_candidates = [
        os.path.join(HERE, "models", name),
        os.path.join(HERE, name),
        os.path.join(PROJECT_ROOT, "models", name),
        os.path.join(os.getcwd(), "models", name),
        os.path.join(os.getcwd(), name),
    ]
    for cand in local_candidates:
        if os.path.isfile(cand) and os.path.getsize(cand) > min_size:
            return cand

    for g in search_globs:
        for hit in sorted(glob.glob(os.path.expanduser(g))):
            if os.path.isfile(hit) and os.path.getsize(hit) > min_size:
                return hit

    raise FileNotFoundError(
        f"필수 에셋/모델 '{name}'을(를) 찾을 수 없습니다. "
        f"models/{name} 에 배치하거나 환경변수 {env_key}을(를) 지정하세요."
    )


# 1. FFmpeg & FFprobe
def get_ffmpeg() -> str:
    return find_binary("ffmpeg", "FFMPEG_BIN")

def get_ffprobe() -> str:
    return find_binary("ffprobe", "FFPROBE_BIN")

# 2. Whisper CLI
def get_whisper_bin() -> str:
    return find_binary(
        "whisper-cli",
        "WHISPER_BIN",
        extra_globs=[
            "~/whisper.cpp/build/bin/whisper-cli",
            "~/*/whisper.cpp/build/bin/whisper-cli",
            "~/*/*/whisper.cpp/build/bin/whisper-cli",
            "~/whisper.cpp/whisper-cli",
        ]
    )

# 3. Whisper Large-v3 Model
def get_whisper_model() -> str:
    return find_file(
        "ggml-large-v3.bin",
        "WHISPER_MODEL",
        search_globs=[
            "~/.cache/whisper.cpp/ggml-large-v3.bin",
            "~/.cache/whisper/ggml-large-v3.bin",
            "~/whisper.cpp/models/ggml-large-v3.bin",
            "~/*/whisper.cpp/models/ggml-large-v3.bin",
            "~/*/*/whisper.cpp/models/ggml-large-v3.bin",
        ],
        min_size=100000000 # 100MB 이상
    )

# 4. YuNet ONNX Face Detection Model
def get_yunet_model() -> str:
    return find_file(
        "yunet.onnx",
        "YUNET_MODEL",
        search_globs=[
            "~/.cache/shorts_autopilot/yunet.onnx",
            "~/.cache/opencv/yunet.onnx",
            "~/models/yunet.onnx",
            "~/**/yunet.onnx",
        ],
        min_size=10000 # 10KB 이상
    )

# 5. Fonts Directory & Fallback Fonts
def get_fonts_dirs() -> list[str]:
    """시스템 및 사용자 폰트 디렉토리 목록."""
    candidates = []
    if sys.platform == "darwin":
        candidates = [
            os.path.expanduser("~/Library/Fonts"),
            "/Library/Fonts",
            "/System/Library/Fonts",
            "/System/Library/Fonts/Supplemental",
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


def find_font(style: str = "bold") -> str:
    """지정된 스타일(bold, medium, regular)에 부합하는 폰트 절대경로 탐색."""
    font_dirs = get_fonts_dirs()
    
    # 1순위: SB 어그로체
    target_names = {
        "bold": ["SB 어그로OTF B.otf", "SB 어그로 B.ttf", "Pretendard-Black.otf", "Pretendard-Bold.otf"],
        "medium": ["SB 어그로OTF M.otf", "SB 어그로 M.ttf", "Pretendard-SemiBold.otf", "Pretendard-Medium.otf"],
        "regular": ["SB 어그로OTF L.otf", "SB 어그로 L.ttf", "Pretendard-Regular.otf", "AppleSDGothicNeo.ttc"],
    }.get(style, ["Pretendard-Bold.otf", "AppleSDGothicNeo.ttc"])

    for d in font_dirs:
        for name in target_names:
            p = os.path.join(d, name)
            if os.path.isfile(p):
                return p

    # 2순위: 시스템 한글 기본 폰트
    system_fallbacks = [
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "C:\\Windows\\Fonts\\malgunbd.ttf",
        "C:\\Windows\\Fonts\\malgun.ttf",
    ]
    for p in system_fallbacks:
        if os.path.isfile(p):
            return p

    # 3순위: 디렉토리 내 아무 otf/ttf
    for d in font_dirs:
        for f in glob.glob(os.path.join(d, "*.[ot]tf")):
            return f
    return "sans-serif"


def get_stickers_dir() -> str:
    """스티커 에셋 디렉토리."""
    d = os.path.join(HERE, "assets", "stickers")
    if os.path.isdir(d):
        return d
    return ""
