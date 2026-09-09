---
name: shorts-autopilot
description: 16:9 원본 설교 영상 하나만으로 편집 프로그램을 전혀 열지 않고 9:16 세로 쇼츠 완성본 MP4 와 유튜브/인스타 썸네일을 만든다. 전사 → OREO 내용 재구성(A/B/C 3안) → 무음 제거 + 간투사·반복 발화 컷 → 컷 인지형 등속 인물 추적 → 한글 단독 대형 자막 + 상단 고정 제목 하드번인 + 줌인/미니멀 버블팝 SFX → 포토제닉 프레임 선별 & 2단 훅(Hook) 썸네일(유튜브 9:16 + 인스타 4:5 안전영역 가이드)까지 전부 자동. "쇼츠 자동으로 만들어" / "원본에서 쇼츠까지 한 번에" / "편집 없이 쇼츠" 로 발동한다.
---

# 🎬 shorts-autopilot — 설교 쇼츠 완전 무인 자동화 제작 정본 가이드

> **대상**: Claude, Antigravity 등 본 파이프라인을 구동하는 모든 AI 에이전트  
> **철학**: NLE 편집기를 일절 열지 않고, 16:9 원본 영상에서 9:16 완성본 MP4 및 4종 썸네일 세트를 100% 전자동으로 완성한다.  
> **절대 준수**: 명시된 **8대 금기**와 **Gate 1~8 하드 게이트**는 선택 사항이 아닌 절대 통과 조건이다.
>
> ⚠ **아래 증상이 하나라도 보이면 즉시 `references/postmortems.md` 를 읽는다**:  
> `⚠꼬리 부족` · 무음 5% 초과 · 컷 직후 인물 이탈 · G-D 급락 · G-F 1.6s 초과 · check_chunks 가 옛 자막인데 ✅ 통과

---

## 🚨 AI 에이전트 8대 금기 사항 (Strict Negative Constraints)

1. **디렉토리/파일명 임의 생성 금지 ❌**:
   * 모든 최종 산출물은 `{영상폴더}/v2/out/` 에, 중간 작업물은 `{영상폴더}/v2/out/_work/` 에 격리한다.
   * 비디오 파일명은 반드시 `{ID}_{한글제목_공백은_언더바}.mp4` (예: `A_다_후원한다더니_29만_원이_왔어요.mp4`)로 명명한다.
2. **Whisper Naive Offset 단독 사용 금지 ❌**:
   * 기본 offset은 0.3~0.5초 앞서는 오차가 있으므로, 반드시 `--dtw large.v3 --no-flash-attn` 기반 센티초 단위 `t_dtw` 정밀 타임스탬프를 적용한다.
3. **`check_chunks.py` 기계 검증 생략 금지 ❌**:
   * `check_chunks.py` 실행 결과 터미널에 `✅ 일치`가 뜨지 않으면 Stage B 렌더링으로 절대 넘어갈 수 없다.
4. **자막 표준 사양 임의 변경 금지 ❌** (2026-09-08 정본):
   * **본문**: 한글 단독 90pt(`&HFFFFFF&`), 테두리 1.5, 그림자 0, MarginV 360, MarginLR 30, 영문 병기 없음(`no_en: true`).
   * **상단 제목**: 인물 머리 위 고정 96pt(마진 185, 테두리 0, 완성된 한 문장)를 영상 내내 노출한다.
5. **회중 리액션(웃음소리/박수) 절단 금지 ❌**:
   * 펀치라인 직후 회중 웃음/박수 구간(2~3초, -34~-41dB)은 무음 감지기 삭제를 방지하기 위해 `plan.json`의 `"preserve": [[start_sec, end_sec]]`에 원본 절대시간으로 명시한다.
   * ★`preserve`는 반드시 `segments` 범위 안에 있어야 한다 (벗어나면 변환이 비어 로그 없이 통째로 무시됨).
6. **화면 동결 및 컷 직후 인물 이탈 금지 ❌**:
   * 인물 추적은 480 px/s 등속 글라이드로 부드럽게 패닝한다.
   * 컷이 바뀐 첫 프레임부터 인물이 화면 중앙에 있어야 한다 (스무딩·보간은 샷 경계 안에서만 적용).
7. **자막 번인 영상에서 썸네일 프레임 추출 금지 ❌**:
   * 썸네일 배경은 반드시 자막이 0% 들어간 클린 마스터(`_work/{slug}_master.mov`)에서 `extract_clean_frames.py`로 추출한다.
8. **묵은 `_raw.mp4` 위에 재렌더 금지 ❌**:
   * 재렌더 전 반드시 `rm -f {영상폴더}/v2/out/_work/{slug}_raw.mp4`를 실행한다. (Gate 8 G-F 길이차로 사후 검출)

---

## 🛠️ 오픈소스 기술 스택 & 단계별 프로그램 역할

본 파이프라인은 상용 솔루션이나 특정 개인 환경에 종속되지 않는 **순수 오픈소스 생태계**로 동작합니다.

| 단계 | 공정 | 오픈소스 도구 / 모델 | 세부 역할 및 기술적 이유 |
|---|---|---|---|
| **1** | **DTW 전사** | **`Whisper.cpp`** (`whisper-cli`)<br>+ `ggml-large-v3.bin` | C/C++ 네이티브 Metal/CUDA 가속. 토큰별 단어(words) 센티초 단위 정밀 타임스탬프(`t_dtw`) 추출 |
| **2** | **무음/호흡 컷** | **`FFmpeg`** (`silencedetect`)<br>+ `Python` | -30dB~-26dB 기준 발화 간격 무음 탐지, 0.15s 안전 패딩 및 현장 회중 리액션 보존(`preserve`) |
| **3** | **인물 추적** | **`OpenCV DNN`** (`cv2.FaceDetectorYN`)<br>+ `yunet.onnx` 모델 | 가벼운 실시간 얼굴 검출. 480px/s 등속 글라이드 패닝 및 컷 경계 0.5f 스냅 9:16 크롭 좌표 계산 |
| **4** | **자막 렌더** | **`Python`** + **`ASS`** 포맷<br>+ **`FFmpeg`** (`libass`) | 90pt 한글 단독 1줄(12~16자) + 96pt 상단 고정 제목. OS별 폰트 폴백(어그로체/Pretendard/시스템고딕) |
| **5** | **자막 기계감사**| **`Python`** (`check_chunks.py`) | 자막 청크 텍스트와 전사 단어 목록의 어절·순서 불일치를 기계적으로 전수 대조 (Gate 3 Hard Gate) |
| **6** | **영상 인코딩** | **`FFmpeg`** (하드웨어 가속) | macOS `h264_videotoolbox`, NVIDIA `h264_nvenc`, 범용 Linux/CPU `libx264` 자동 선택 |
| **7** | **오디오 믹싱** | **`FFmpeg`** (`amix`, `afade`)<br>+ **`NumPy`** 신호 합성 | 대사 100% + 45ms 미니멀 버블팝 SFX 45% 배합. 샘플 부재 시 900→1800Hz 사인파 자동 합성 폴백 |
| **8** | **품질 교차검증**| **`Whisper.cpp`** + `difflib` | 완성 MP4 오디오를 재전사하여 대본과 95.0% 이상 일치하는지 자동 판정 (Gate 4 & Gate 8) |
| **9** | **썸네일 합성** | **`Pillow`** (PIL) | 클린 마스터 9:16 프레임 기반 2단 훅 타이포 및 인스타 4:5 안전영역 가이드 동시 출력 |

---

## 🌐 크로스플랫폼 동적 도구 탐색 (`env_discovery.py`)

파이프라인 실행 시 `env_discovery.py`가 아래 우선순위로 도구와 모델을 동적으로 자동 감지합니다:
1. **환경변수**: `WHISPER_BIN`, `WHISPER_MODEL`, `YUNET_MODEL`, `FFMPEG_BIN`, `FFPROBE_BIN`
2. **로컬 프로젝트**: `./models/` (예: `models/yunet.onnx`, `models/ggml-large-v3.bin`)
3. **시스템 PATH & 패키지 매니저**: Homebrew(`/opt/homebrew/bin`), APT(`/usr/bin`), `~/.local/bin`
4. **글로벌 캐시 디렉토리**: `~/.cache/whisper.cpp/`, `~/.cache/shorts_autopilot/`

*실행 명령어 예시의 `python3`는 OpenCV(cv2), NumPy, Pillow가 설치된 가상환경 파이썬을 가리키며, 스크립트 경로는 프로젝트 기준 상대경로(`scripts/shorts_v2/`)로 표기합니다.*

---

## 📋 표준 플랜 템플릿 (`plans/plan_{ID}.json`)

```json
{
  "id": "A",
  "title": "한 달 후원금이 29만 원이었습니다",
  "slug": "A_다_후원한다더니_29만_원이_왔어요",
  "segments": [
    [1803.14, 1810.29], [1810.86, 1811.70], [1832.84, 1834.78], [1836.24, 1848.26],
    [1850.34, 1855.19], [1855.71, 1879.10], [1956.69, 1960.28], [1966.56, 1970.88],
    [1994.86, 1995.63], [1995.73, 2000.64], [2002.46, 2006.54], [2017.72, 2031.35]
  ],
  "preserve": [
    [1875.0, 1879.1],
    [2030.55, 2031.35]
  ],
  "silence": {
    "min_silence": "0.25", "pad": "0.09", "keep_gap": "0.06",
    "min_gain": "0.25", "min_keep": "0.90"
  },
  "subs": {
    "no_en": true, "font_size": 90, "outline": 1.5, "shadow": 0,
    "margin_v": 360, "margin_lr": 30, "last_tail": 0.30,
    "top_title": "한 달 후원금이\\N{\\c&H00E5FF&}29만 원{\\c&HFFFFFF&}이었습니다",
    "top_title_size": 96, "top_title_margin": 185, "top_title_outline": 0,
    "band_top": 420, "band_bottom": 500, "band_crop_y": 250, "zoom_out": 1.35
  },
  "script": "실제 완성본 영상에서 들릴 정본 한국어 대본 전문"
}
```

### ⚠️ 필수 키 누락 시 발생하는 장애 (절대 생략 금지)
| 누락 키 | 발생 장애 (조용한 실패) |
|---|---|
| `subs` 전체 | 구형 68pt 영/한 2단 자막(Outline 4.0, MarginV 470)으로 자동 회귀 |
| `subs.last_tail` | 마지막 자막이 영상 끝까지 늘어나 뒤쪽 페이드아웃 자리 소멸 (`⚠꼬리 부족`) |
| `subs.band_top` | `band_bottom`, `band_crop_y`, `zoom_out` 설정이 통째로 무시됨 |
| `silence` 전체 | 무음 기본값으로 돌아가 무음 비율이 9%대로 급증 (표준은 5% 이하) |
| `preserve` 범위 이탈 | 세그먼트와 겹치지 않으면 로그 없이 무시되어 회중 웃음/꼬리가 짤려나감 |

* **제목 3자 구분**: `title`은 문서용 메타데이터, `top_title`은 화면 96pt 표시용 문장, **`slug`는 파일명 정체성이므로 한 번 정하면 절대 바꾸지 않는다** (중간물·썸네일 불일치 방지).
* **선두 즉시 발화 규칙**: `segments[0][0]`는 반드시 파형 기준 **첫 음절 발화 시작 직전 0.05초**로 바짝 당겨 설정한다 (선두 정적 원천 차단).
* **꼬리 페이드아웃 자리 확보**: 마지막 세그먼트는 `[..., 발화끝 + 1.2s]`로 잡고, `preserve`에 `[발화끝, 발화끝 + 1.2s]`를 명시한다.
* **카메라 미세 조정 (`track`, 선택 사항)**: 카메라가 산만하거나 구도가 튈 때만 `plan.json`에 `track` 블록을 추가한다 (없으면 480 px/s 기본값):

| 키 | 기본값 | 뜻 | 언제 만지나 |
|---|---|---|---|
| `pan_speed` | 480 | 패닝 속도(px/s) | 카메라가 산만하면 낮춘다 (G-D 대응 실측 110) |
| `static_spread` | 150 | 정적 판정 이동폭(px) | 미세하게 계속 흔들리면 올린다 / G-D 이동비율이 모자라면 낮춘다 (실측 50) |
| `static_lock` | 60 | 이전 샷과 같은 구도로 락 거는 허용 오차(px) | 컷마다 구도가 튀면 올린다 / G-D 이동비율이 모자라면 낮춘다 (실측 10) |
| `static_mindur` | 1.8 | 이 길이(초) 미만인 샷은 무조건 고정 | 짧은 샷이 자꾸 움직이면 올린다 (조정 전례 없음) |

---

## 🔄 전 공정 8단계 워크플로우 및 실행 가이드

### [Phase 1] 기획 & 플랜 작성 (Gate 1)
1. **서사 구조**: 3~5초 내 도발/웃음/호기심 훅 ➔ 본질적 이유(R) ➔ 구체적 장면(E) ➔ **타협 없는 복음적 결단 및 선포(O)** 로 종결 (단순 상황 설명/개념 정의 종결 절대 금지).
2. **세그먼트 추출 & preserve 지정**: 회중 웃음 구간(실제 웃음만 2~3초 좁게) 및 마지막 세그먼트 페이드 꼬리를 `preserve`에 원본 절대시간으로 지정.
* 🛑 **Gate 1 통과 기준**: 완결된 OREO 선포 서사 + preserve 명시 + 선두 발화 직전 컷팅.

### [Phase 2] Stage A 실행 (컷 추출 + 무음 제거 + DTW 전사 + 인물 추적) (Gate 2)
```bash
python3 scripts/shorts_v2/run_pipeline.py \
  --src "{원본_영상_경로.mp4}" --plan "{영상폴더}/plans/plan_{ID}.json" \
  --outdir "{영상폴더}/v2/out" --stage a
```
* **간투사·반복 발화·뜸 제거 필수 절차**:  
  `silence_cut.py`는 유음 간투사("어", "그", 반복 발화)를 못 잡으므로 반드시 검출기를 실행한다 (`MIN_VOICE = 0.30` 기준):
  ```bash
  python3 scripts/shorts_v2/find_filler.py "{영상폴더}/v2/out/_work/{slug}"
  ```
  검출된 구간은 아래 파이썬 스니펫으로 master 시간 ➔ raw ➔ src(원본 절대시간)로 역매핑하여 `plan.segments`를 쪼개어 삭제한 뒤 Stage A를 재실행한다:
  ```python
  import json
  base = "{영상폴더}/v2/out/_work/{slug}"
  cuts = json.load(open(f"{base}_master.mov.cuts.json"))["keeps"]
  segs = json.load(open(f"{base}_raw.mov.segmap.json"))["segments"]
  def m2r(t):
      acc = 0.0
      for a, b in cuts:
          if t < acc + (b - a) - 1e-9: return a + (t - acc)
          acc += b - a
      return cuts[-1][1]
  def r2s(t):
      for g in segs:
          if g["dst_start"] - 1e-9 <= t <= g["dst_end"] + 1e-9:
              return g["src_start"] + (t - g["dst_start"])
  ```
* 🛑 **Gate 2 통과 기준**: `_master.mov` 생성 + DTW 정렬 90% 이상 + 얼굴 검출률 >= 95% + `ffmpeg -i ... -af silencedetect=noise=-42dB:d=0.20` 측정 시 무음 총합 <= 5% + 간투사 0건.

### [Phase 3] 자막 청크(`chunks_{ID}.json`) 작성 및 기계 검증 (Gate 3 Hard Gate)
1. `_work/{slug}_master.words.json` 단어 스트림을 읽고 10~16자 호흡 단위로 분할.
   * `ko`: 화면 표시 한글 자막 (숫자는 `1000개`, `29만 원` 등 아라비아 숫자)
   * `en`: 항상 빈 문자열 `""`
   * `hl`: 핵심 펀치라인 5~8개 블록에 `true` (골드 옐로우 하이라이트)
   * `stt`: 아라비아 숫자나 괄호 설명 태그(`{\\fs62\\c&HC8C8C8&}(설명){\\fs90\\c&HFFFFFF&}`) 적용 시 실제 발화 음성 텍스트를 기입하여 검증과 화면 표시를 분리.
   * ⚠ Whisper 재전사가 흔들려 불일치가 나면 `stt`를 억지로 넣지 말고 **오히려 지워서** `ko`와 맞춘다.
2. **`check_chunks.py` 기계 검증 (단독 실행 필수)**:
```bash
python3 scripts/shorts_v2/check_chunks.py \
  --words "{영상폴더}/v2/out/_work/{slug}_master.words.json" \
  --chunks "{영상폴더}/v2/out/_work/chunks_{ID}.json"
```
* 🛑 **Gate 3 통과 기준**: 터미널에 반드시 **`✅ 일치 (NNN자, 블록 NN개)`** 가 출력되어야 함. (1자라도 불일치 시 Stage B 진행 금지. 단, "⚠ N번 블록이 김" 경고는 ASS 태그 길이 때문이므로 무시 가능)

### [Phase 4] Stage B 렌더링 & 음성 전사 대조 검증 (Gate 4)
* 묵은 렌더 방지: `rm -f "{영상폴더}/v2/out/_work/{slug}_raw.mp4"` 선행 실행.
```bash
python3 scripts/shorts_v2/run_pipeline.py \
  --src "{원본_영상_경로.mp4}" --plan "{영상폴더}/plans/plan_{ID}.json" \
  --outdir "{영상폴더}/v2/out" --stage b --chunks "{영상폴더}/v2/out/_work/chunks_{ID}.json"
```
* 🛑 **Gate 4 통과 기준**: `{slug}.mp4` 생성 + `verify_output.py` 대본 유사도 **>= 95.0%**.

### [Phase 5] 클린 마스터에서 9:16 썸네일 프레임 추출 (Gate 5)
```bash
python3 scripts/shorts_v2/extract_clean_frames.py \
  --master "{영상폴더}/v2/out/_work/{slug}_master.mov" \
  --track "{영상폴더}/v2/out/_work/{slug}_track.json" \
  --times 10 18 24 35 48 --outdir "{영상폴더}/v2/out/thumbnails_preview" --prefix "{ID}_frame"
```
* 🛑 **Gate 5 통과 기준**: 본문 자막 간섭 0% + 인물 표정 및 손동작이 가장 포토제닉한 프레임 1종 선정.

### [Phase 6] 썸네일 4종 생성 & 앞뒤 시네마틱 페이드 결합 (Gate 6)
```bash
python3 scripts/shorts_v2/generate_thumbnail.py \
  --mode both --frame "{선정프레임_경로}.jpg" --badge "하올람 말씀 인사이트" \
  --l1 "{타이틀 1행}" --l2 "{타이틀 2행(골드)}" --sub "{서브 티저}" --zoom 0.86 \
  --out-yt "{영상폴더}/v2/out/{slug}_thumb_youtube.jpg" \
  --out-insta "{영상폴더}/v2/out/{slug}_thumb_instagram.jpg" \
  --out-guide "{영상폴더}/v2/out/{slug}_thumb_instagram_guide.jpg" \
  --video "{영상폴더}/v2/out/{slug}.mp4" --no-intro --ass "{영상폴더}/v2/out/_work/{slug}.ass"

cp "{영상폴더}/v2/out/{slug}_thumb_youtube.jpg" "{영상폴더}/v2/out/{slug}_thumb.jpg"
```
* 🛑 **Gate 6 통과 기준**: 썸네일 4종 완비 + 인스타 안전영역 안착(노란 가이드 `[161, 164, 921, 1145]`) + 앞 정지컷 없이 0초부터 페이드인(0.25s) + 자막 끝 후 0.4초 암전 페이드아웃(`⚠꼬리 부족` 경고 없음).

### [Phase 7] 결과 보고서 작성 및 폴더 오픈 (Gate 7)
* `{영상폴더}/v2/out/00_읽어보세요.md` 에 안별 길이, 유사도, OREO 서사 기록 후 `open "{영상폴더}/v2/out"` 실행.

### [Phase 8] 시간·모션 하드 게이트 검증 (Gate 8 Hard Gate)
```bash
python3 scripts/shorts_v2/check_output.py \
  --outdir "{영상폴더}/v2/out" --slug "{slug}"
```
* 🛑 **Gate 8 통과 기준 (❌ 0건 필수, G-D 10~20%는 ⚠️ 경고 통과이나 권장 20% 이상)**:
  * **G-A**: 첫 자막 리드 `<= 0.30초` (첫 발화 시작 직전 밀착)
  * **G-B**: 블록 대기 최대 `<= 1.00초`, 0.5초 초과 블록 `<= 15%`
  * **G-C**: 블록 최단 표시 `>= 0.40초`, 1초 미만 `<= 25%`
  * **G-D**: 카메라 이동 비율 `>= 10%` (정지 발화 구간은 컷별 중앙 프레이밍 확인 시 예외 인정)
  * **G-E**: 디렉토리, 슬러그, 썸네일 4종 완비, 유사도 `>= 95.0%`
  * **G-F**: MP4-ASS 길이차 `0.0 ~ 1.6초` (정상 범위: +0.4s 안팎)

---

## 🎨 화면 레이아웃 & 디자인 스펙 요약

* **9:16 레이아웃**: 상단 검은 밴드 420px (고정 제목) / 중앙 영상 1080x1000 / 하단 검은 밴드 500px (본문 자막)
* **화각 크롭**: `subs.zoom_out: 1.35` (4K 1642x1520 크롭 ➔ 1080x1000 스케일, `band_crop_y: 250`)
* **본문 자막**: 90pt, 순백색(`&HFFFFFF&`), 테두리 1.5, 그림자 0, MarginV 360, MarginLR 30 (한 줄 15자 한계)
* **상단 고정 제목**: 96pt, 테두리 0, 마진 185, 완성된 한 문장(`\\N`으로 2행 분할, 골드 강조 `{\\c&H00E5FF&}`)
* **인물 추적**: 480 px/s 등속 글라이드 패닝 (`PAN_SPEED = 480`)
* **썸네일 줌**: `--zoom 0.86` 하단 앵커링 스케일링으로 인물 얼굴과 타이틀 간 50px 안전 버퍼 확보
