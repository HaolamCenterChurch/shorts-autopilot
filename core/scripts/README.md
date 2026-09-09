# shorts_v2 — 설교 쇼츠 완전 자동화 파이프라인 (Cross-Platform)

16:9 원본 영상과 기획안(`plan.json`)만으로, 편집 프로그램을 열지 않고
9:16 세로 쇼츠 완성본 MP4(자막 하드번인 포함) 및 썸네일 4종 세트를 자동 생성합니다.

---

## 🛠️ 오픈소스 기술 스택 & 단계별 프로그램 역할

본 파이프라인은 상용 유료 솔루션이나 특정 개인 환경에 종속되지 않는 **순수 오픈소스 생태계**로 구축되어 있습니다.

| 단계 | 파이프라인 작업 | 담당 오픈소스 도구 / 모델 | 역할 및 기술적 이유 |
|---|---|---|---|
| **1. DTW 전사** | 음성 전사 및 단어 단위 타임스탬프 추출 | **`Whisper.cpp`** (`whisper-cli`)<br>+ `ggml-large-v3.bin` | C/C++ 네이티브 고속 추론, Metal/CUDA GPU 가속, 토큰별 단어(words) 정확한 타임스탬프 추출 |
| **2. 무음/호흡 컷** | 발화 사이 공백 및 호흡 구간 정밀 제거 | **`FFmpeg`** (`silencedetect` 필터)<br>+ `Python` 구간 연산 | 데시벨(-30dB~-26dB) 기반 무음 탐지, 0.15s 패딩 및 현장 리액션 보존 필터링 |
| **3. 인물 추적** | 16:9 영상 내 화자 얼굴 추적 및 9:16 크롭 | **`OpenCV DNN`** (`cv2.FaceDetectorYN`)<br>+ `yunet.onnx` 모델 | 가벼운 실시간 얼굴 검출, 480px/s 등속 글라이드 및 컷 경계 0.5f 스냅 연산 |
| **4. 자막 생성** | 한글 단독 90pt 볼드 + 상단 고정 제목 | **`Python`** + **`ASS`** (Advanced SubStation Alpha) | 어절 단위 12~16자 1줄 컷, 골드(#F9D342) 키워드 하이라이트, 상단 96pt 제목 고정 |
| **5. 자막 검증** | 청크와 원본 발화 100% 일치 기계 감사 | **`Python`** (`check_chunks.py`) | 자막 텍스트와 전사 단어 목록의 어절·순서 불일치를 사전 차단 (Gate 4) |
| **6. 영상 렌더링** | 크롭, 줌인, 스티커, 자막 하드번인 | **`FFmpeg`** + **`libass`** 라이브러리 | `filter_complex` 기반 단일 패스 렌더링. OS별 하드웨어 가속 자동 선택 |
| **7. 오디오 믹싱** | 대사 + 45ms 미니멀 버블팝 SFX | **`FFmpeg`** (`amix`, `afade`)<br>+ **`NumPy`** 신호 합성 폴백 | 음성과 효과음 1.0:0.45 믹싱, 샘플 부재 시 900→1800Hz 사인파 자동 합성 |
| **8. 품질 검증** | 최종 MP4 오디오 재전사 일치율 검사 | **`Whisper.cpp`** + `difflib.SequenceMatcher` | 완성본 오디오를 재전사하여 대본과 95% 이상 일치하는지 최종 판정 (Gate 8) |
| **9. 썸네일 제작** | 유튜브 9:16 + 인스타 4:5 안전영역 썸네일 | **`Pillow`** (PIL) | 클린 마스터 프레임 기반 2단 훅 타이포그래피 및 안전영역 가이드 합성 |

---

## 🌐 크로스플랫폼 동적 환경 탐색 (`env_discovery.py`)

특정 개인 머신(`/Users/...`) 경로 하드코딩이 완전히 제거되었으며, 다음 우선순위로 도구와 모델을 동적으로 탐색합니다:

1. **환경변수 오버라이드**: `FFMPEG_BIN`, `WHISPER_BIN`, `WHISPER_MODEL`, `YUNET_MODEL`
2. **로컬 프로젝트 디렉토리**: `./models/` 또는 스크립트 인근 경로
3. **시스템 PATH & 패키지 매니저**: macOS Homebrew(`/opt/homebrew/bin`), Linux(`/usr/bin`, `/usr/local/bin`), Windows
4. **글로벌 캐시 경로**: `~/.cache/whisper.cpp/`, `~/.cache/shorts_autopilot/` 등

### OS별 자동 최적화
- **비디오 인코더**:
  - macOS (Apple Silicon): `h264_videotoolbox` (초고속 하드웨어 인코딩)
  - NVIDIA GPU 환경: `h264_nvenc`
  - 범용 Linux / CPU: `libx264` (-crf 18, -preset fast)
- **폰트 폴백**:
  - 1순위: SB 어그로체 / Pretendard
  - 2순위: 시스템 기본 고딕 (macOS: Apple SD Gothic Neo, Linux: NanumGothic/NotoSans, Windows: 맑은 고딕)

---

## 📦 필수 도구 및 모델 준비

### 1. 시스템 도구
```bash
# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt-get update && sudo apt-get install -y ffmpeg libass-dev

# Python 가상환경
python3 -m venv .venv
source .venv/bin/activate
pip install numpy opencv-python Pillow
```

### 2. Whisper.cpp 빌드
```bash
git clone https://github.com/ggerganov/whisper.cpp.git
cd whisper.cpp
cmake -B build
cmake --build build --config Release
# 대형 모델 다운로드
bash ./models/download-ggml-model.sh large-v3
```

### 3. YuNet 얼굴 검출 ONNX 모델
```bash
mkdir -p models
curl -L -o models/yunet.onnx https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
```

---

## 🚀 파이프라인 실행

```bash
# Stage A: 구간 추출 + 무음 제거 + DTW 전사 + 인물 추적
python3 run_pipeline.py --src input.mp4 --plan plan.json --outdir out --stage a

# Stage B: 자막 하드번인 + 줌인/SFX + 재전사 검증
python3 run_pipeline.py --src input.mp4 --plan plan.json --outdir out --stage b --chunks out/_work/chunks.json

# 썸네일 프레임 추출 및 썸네일 4종 생성
python3 extract_clean_frames.py --master out/_work/master.mov --track out/_work/track.json --times 10 20 30 --outdir out/thumbs
python3 generate_thumbnail.py --mode both --frame out/thumbs/frame_20s.jpg --badge "인사이트" --l1 "첫 번째 줄" --l2 "두 번째 줄" --sub "서브 설명문" --out-yt out/thumb_yt.jpg --out-insta out/thumb_insta.jpg --out-guide out/thumb_guide.jpg
```
