#!/bin/bash
# 개발용 실행기 — 더블클릭하면 앱 창이 뜬다 (배포 .app 빌드 전 테스트용)
cd "$(dirname "$0")"
exec .venv/bin/python -m app.main
