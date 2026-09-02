# 🎬 Shorts Autopilot Desktop App

16:9 가로 강연/발표 영상 하나로 편집 프로그램 없이 **9:16 세로 완성본 쇼츠 MP4**를 자동 생성하는 pywebview 기반 데스크톱 애플리케이션입니다.

---

## 🌟 주요 특징

1. **자동화 파이프라인 (Auto Pipeline)**
   - Whisper 기반 단어/글자 단위 정밀 타임스탬프 전사 (한글 토큰 보존 증분 디코딩).
   - **무음 자동 제거 (Silence Cut)**: 0.3초 이상의 불필요한 무음을 자동 감지 및 컷하여 밀도 높은 쇼츠 생성.
   - **인물 추적 9:16 크롭 (Face Tracking)**: YuNet ONNX 모델을 통한 고속 얼굴 추적 및 코리도 기반 부드러운 카메라 이동.
   - **영/한 2단 자막 하드번인**: 10~16자 호흡 단위 분절, 영문 번역, 클라이맥스 강조 스타일 적용.
   - **완성본 재전사 대조 검증**: 렌더링된 MP4 오디오를 재전사하여 대본과의 정합성을 평가.

2. **OREO 구조 3안 기획 (A/B/C)**
   - O(결론/훅) → R(이유) → E(예시) → O(재강조) 구조로 영상을 자동 재배열 및 기획.

3. **전 단계 상시 AI 수정 패널**
   - 고정된 단계별 승인에 구애받지 않고, 화면 우측의 상시 AI 어시스턴트를 통해 언제든 자연어로 산출물(기획안, 자막, 대본)을 실시간 수정 가능.

4. **시스템 진단 도구 (Doctor)**
   - FFmpeg, Whisper CLI, Python 라이브러리, YuNet 모델 설치 상태를 점검하고 환경별 맞춤 가이드 및 자동 설치 제공.

---

## 📦 설치 및 요구사항

### 1. 시스템 필수 도구
- **FFmpeg**: 동영상 및 오디오 추출/필터링
  - macOS: `brew install ffmpeg`
  - Ubuntu/Debian: `sudo apt install ffmpeg`
- **Whisper CLI**: 음성 전사
  - macOS: `brew install whisper-cpp` 또는 `whisper.cpp` 빌드
- **Whisper 모델(ggml-large-v3)**:
  ```bash
  mkdir -p models
  curl -L -o models/ggml-large-v3.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin
  ```
- **YuNet 모델**:
  ```bash
  mkdir -p models
  curl -L -o models/yunet.onnx https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
  ```
- 위 도구·모델 상태는 앱 실행 후 **시스템 진단(Doctor)** 화면에서 한 번에 확인할 수 있습니다.

### 2. 파이썬 환경 설정
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 🚀 실행 방법

개발 중 빠른 실행:
```bash
./run_dev.command
# 또는
python3 -m app.main
```

macOS 독립 실행형 앱(.app)으로 빌드:
```bash
.venv/bin/pip install py2app
.venv/bin/python setup.py py2app
./build_dmg.sh   # dmg/background.png 준비 시 드래그 설치용 DMG까지 생성
```

---

## 📂 프로젝트 구조

```text
shorts-autopilot-app/
├── app/
│   ├── main.py            # pywebview 진입점 및 JS-Python 브리지 API
│   ├── orchestrator.py    # OREO 기획, 자막, 렌더링 파이프라인 오케스트레이션
│   ├── ai_adapter.py      # BYO-CLI (Claude, Gemini, Antigravity 등) 및 카나리 검증
│   ├── doctor.py          # 시스템 도구 및 라이브러리 환경 진단
│   ├── paths.py           # 경로 해석 및 shutil.which 바이너리 검색
│   └── ui/
│       └── index.html     # 반응형 데스크톱 UI & 상시 AI 수정 패널
├── core/
│   ├── pipeline.py        # 결정적 파이프라인 오케스트레이션(전사·구간추출·무음제거·인물추적·자막·렌더·검증)
│   └── scripts/           # 검증된 8개 실행 스크립트(extract_segments·silence_cut·track_crop·make_ass·render_vertical·verify_output·check_chunks)
├── prompts/
│   ├── plan_oreo.md       # OREO 구조 기획 프롬프트 (범용 강연 예시)
│   ├── revise_plan.md     # 기획안 수정 프롬프트
│   ├── chunk_subtitle.md  # 2단 자막 분절 및 번역 프롬프트
│   └── verify_prompt.md   # 완성본 대조 검증 프롬프트
├── setup.py               # py2app 빌드 설정
├── build_dmg.sh           # 드래그 설치형 DMG 생성 스크립트
├── run_dev.command        # 개발용 더블클릭 실행기
├── requirements.txt
├── LICENSE                # MIT License
├── .gitignore
└── README.md
```

## ⚠️ 현재 상태
- 파이프라인 오케스트레이션(`core/pipeline.py`)과 8개 실행 스크립트는 이식·검증 완료.
- `check_chunks.py`(자막-원문 일치 검증)는 아직 오케스트레이션에 연결 전 — 자막 확정 전 수동/후속 연동 필요.
- 아이콘(`assets/AppIcon.icns`)·DMG 배경(`dmg/background.png`)은 준비되지 않음 — `.app` 빌드는 되지만 DMG는 배경 이미지를 넣은 뒤 가능.

---

## 📄 라이선스
MIT License
