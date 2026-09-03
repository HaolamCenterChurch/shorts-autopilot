"""py2app 빌드 설정 — 쇼츠 오토파일럿.

빌드:  .venv/bin/python setup.py py2app
결과:  dist/쇼츠 오토파일럿.app  (터미널 없이 실행)

milestone 1: 의존성 번들 없이 .app 실행 검증 (도구는 시스템 PATH·models/ 폴백 경로 사용).
milestone 2: vendor/ (ffmpeg·whisper·model) 를 DATA_FILES에 추가해 자급자족.
"""
import glob
from setuptools import setup

APP = ["app/main.py"]

DATA_FILES = [
    ("prompts", glob.glob("prompts/*.md")),
    # ui/ 는 "app" 패키지 밖에 둬야 한다: app/ 안에 두면 py2app 이 site-packages.zip
    # 안에 동명의 빈 app/__init__.pyc 를 만들어 진짜 app 패키지(app/paths.py 등)를
    # sys.path 우선순위로 가려버려 ModuleNotFoundError: app.paths 가 난다.
    ("ui", ["ui/index.html"]),
]

OPTIONS = {
    "argv_emulation": False,
    "packages": ["webview", "app", "core"],
    "iconfile": "assets/app_icon.icns",
    "plist": {
        "CFBundleName": "쇼츠 오토파일럿",
        "CFBundleDisplayName": "쇼츠 오토파일럿",
        "CFBundleIdentifier": "com.shortsautopilot.app",
        # 버전은 앱 이름이 아니라 여기에만 (업데이트 시 .app 이름이 안 바뀌어 교체 설치됨)
        "CFBundleShortVersionString": "0.1.0",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
    },
}

setup(
    name="쇼츠 오토파일럿",
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
