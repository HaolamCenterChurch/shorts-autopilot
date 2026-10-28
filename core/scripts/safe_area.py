"""세로 영상(1080x1920) 자막 가로 안전폭 — 모든 스킬의 단일 정본.

플랫폼 UI(좌우 버튼·프로필) 와 기기별 크롭 때문에 캔버스 끝까지 쓰면 잘린다.
유튜브 쇼츠 · 인스타 릴스 · 틱톡 공통 안전영역은 가로 x 90 ~ 990 이다.
안전폭 = min(캔버스 - MarginLR x 2, 플랫폼 안전폭). 스타일 여백이 더 넓으면 그쪽을 따른다.
이 값을 다른 파일·문서에 다시 적지 않는다 — 여기서 import 한다.
"""
import re

CANVAS_W = 1080
PLATFORM_SAFE_X0, PLATFORM_SAFE_X1 = 90, 990
PLATFORM_SAFE_W = PLATFORM_SAFE_X1 - PLATFORM_SAFE_X0   # 900
FONT_PATH = "/System/Library/Fonts/AppleSDGothicNeo.ttc"

_fonts = {}


def safe_width(margin_lr=0):
    return min(CANVAS_W - 2 * int(margin_lr), PLATFORM_SAFE_W)


def text_width(text, size):
    """스타일 태그를 벗긴 한 행의 실제 픽셀 폭(폰트 메트릭)."""
    clean = re.sub(r"\{[^}]*\}", "", text).strip()
    if not clean:
        return 0
    try:
        from PIL import ImageFont
        if size not in _fonts:
            _fonts[size] = ImageFont.truetype(FONT_PATH, size)
        b = _fonts[size].getbbox(clean)
        return b[2] - b[0]
    except Exception:
        k = size / 90.0
        return int(sum((78 if "\uac00" <= ch <= "\ud7a3" else 25 if ch.isspace() else 48) * k
                       for ch in clean))
