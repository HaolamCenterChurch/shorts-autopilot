#!/bin/bash
# 드래그 설치형 DMG 생성기 — 쇼츠 오토파일럿
# 창을 열면 [앱] → [Applications 바로가기] 레이아웃 + 안내 배경이 보인다.
# 사용: ./build_dmg.sh   (먼저 dist/...app 이 빌드돼 있어야 함: .venv/bin/python setup.py py2app)
set -euo pipefail
cd "$(dirname "$0")"

# 앱 이름엔 버전을 넣지 않는다(업데이트 시 .app 이름 불변 → 교체 설치).
# 버전은 DMG/볼륨명에만 표기. 새 버전마다 VERSION 만 올리면 된다.
APP_NAME="쇼츠 오토파일럿"
VERSION="v0.1.0"
DMG_NAME="${APP_NAME} ${VERSION}"
APP="dist/${APP_NAME}.app"
VOL="$DMG_NAME"
BG="dmg/background.png"
OUT="dist/${DMG_NAME}.dmg"
TMP_DMG="dist/.tmp_${DMG_NAME}.dmg"
STAGE="dist/.stage"

[ -d "$APP" ] || { echo "❌ $APP 없음 — 먼저 .venv/bin/python setup.py py2app"; exit 1; }
[ -f "$BG" ] || { echo "❌ $BG 없음 — dmg/background.png 를 준비해라(선택: python3 dmg/make_bg.py 같은 생성기)"; exit 1; }

echo "▶ 스테이징 준비"
rm -rf "$STAGE" "$TMP_DMG" "$OUT"
mkdir -p "$STAGE/.background"
cp -R "$APP" "$STAGE/"
cp "$BG" "$STAGE/.background/background.png"
ln -s /Applications "$STAGE/Applications"

# 용량 = 내용물 + 350MB 여유 (작은 파일 수천 개의 블록 오버헤드 + .DS_Store 공간)
SIZE_MB=$(( $(du -sm "$STAGE" | cut -f1) + 350 ))
echo "▶ RW 디스크 이미지 생성 (${SIZE_MB}MB)"
hdiutil create -srcfolder "$STAGE" -volname "$VOL" -fs HFS+ \
  -format UDRW -size "${SIZE_MB}m" "$TMP_DMG" >/dev/null

echo "▶ 마운트"
DEV=$(hdiutil attach -readwrite -noverify -noautoopen "$TMP_DMG" | egrep '^/dev/' | head -1 | awk '{print $1}')
MOUNT="/Volumes/$VOL"
sleep 2

echo "▶ Finder 레이아웃 적용"
osascript <<OSA || echo "⚠ Finder 자동화 권한이 없으면 레이아웃이 안 잡힐 수 있음 (DMG 자체는 정상)"
tell application "Finder"
  tell disk "$VOL"
    open
    set current view of container window to icon view
    set toolbar visible of container window to false
    set statusbar visible of container window to false
    set the bounds of container window to {180, 110, 940, 570}
    set vo to the icon view options of container window
    set arrangement of vo to not arranged
    set icon size of vo to 110
    set text size of vo to 13
    set background picture of vo to file ".background:background.png"
    set position of item "${APP_NAME}.app" of container window to {200, 230}
    set position of item "Applications" of container window to {560, 230}
    update without registering applications
    delay 1
    close
  end tell
end tell
OSA

sync
echo "▶ 언마운트"
hdiutil detach "$DEV" >/dev/null || hdiutil detach "$DEV" -force >/dev/null

echo "▶ 압축본(UDZO)으로 변환"
hdiutil convert "$TMP_DMG" -format UDZO -imagekey zlib-level=9 -o "$OUT" >/dev/null
rm -f "$TMP_DMG"
rm -rf "$STAGE"

echo "✅ 완료: $OUT"
ls -lh "$OUT"
