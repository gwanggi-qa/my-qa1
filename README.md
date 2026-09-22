# 태블릿 실시간 미러링 솔루션

ADB screencap + Flask WebSocket 기반으로 갤럭시 탭 등 Android 기기 화면을  
PC 브라우저에 실시간으로 표시합니다. Appium 자동화도 함께 제공합니다.

---

## 빠른 시작

1. **USB 디버깅 활성화** — 태블릿 설정 → 개발자 옵션 → USB 디버깅 ON  
2. **USB로 PC에 연결** — "파일 전송(MTP)" 모드 선택  
3. **`setup_and_run.bat` 더블클릭** — 패키지 자동 설치 후 메뉴 표시  
4. `1` 선택 → 브라우저에서 `http://localhost:5000` 열기 → **[미러링 시작]** 클릭

---

## 파일 구성

| 파일 | 설명 |
|------|------|
| `mirror_server.py`      | ADB 캡처 → WebSocket → 브라우저 실시간 스트리밍 |
| `appium_controller.py`  | Appium 연결 / 탭·스와이프·텍스트·매크로 자동화 |
| `setup_and_run.bat`     | Python 패키지 자동 설치 + 실행 메뉴 |
| `README.md`             | 이 문서 |

---

## 사전 요구사항

| 항목 | 설치 방법 |
|------|-----------|
| Python 3.8+ | https://www.python.org/downloads/ |
| Android SDK Platform-Tools (adb) | https://developer.android.com/tools/releases/platform-tools |
| Node.js (Appium 사용 시) | https://nodejs.org/ |
| Appium 서버 (자동화 사용 시) | `npm install -g appium` 후 `appium driver install uiautomator2` |

---

## 설정 변경

### AI Random Run

`Random Test > AI Random Run`은 OpenAI API를 사용해 UIAutomator, OCR, Memory,
업로드된 Test Case와 이전 AI 실행 결과를 바탕으로 실행 순서를 계획합니다.
API 키는 소스 파일에 저장하지 않고 서버 프로세스의 환경변수로 설정합니다.

```powershell
$env:OPENAI_API_KEY="YOUR_API_KEY"
$env:OPENAI_MODEL="gpt-5.6-luna"  # optional
python mirror_server.py
```

키가 없거나 API 호출이 실패하면 동일한 후보를 사용하는 기존 Live Run 방식으로
자동 전환되며, Random 패널과 상단 상태에 `AI: Fallback`이 표시됩니다.
AI 실행 이력은 `qa_results/ai_random_history.jsonl`에 저장되어 다음 계획에 반영됩니다.

### AI TC RUN

`Test Case > AI TC RUN`은 업로드한 Excel 순서를 유지하면서 기존 UIAutomator/OCR
실행을 우선합니다. 실행 대상이 모호하거나 실패한 항목만 현재 화면 스크린샷과
UI 후보를 OpenAI API로 분석해 안전한 후보를 선택합니다.

- 전체 Test Case 흐름 분석 후 25개 단위로 실행 힌트 생성
- 실행 전 화면의 전제조건과 실행 후 기대 문구를 UIAutomator/OCR로 각각 확인
- 클릭 대상과 기대결과 후보를 분리해 기대 문구를 잘못 누르는 동작 방지
- 실패 단계는 현재 화면을 다시 분석하며 최대 3회까지 안전한 후보로 복구
- AI 힌트가 적용된 결과는 `ai-guided`, 복구 성공은 `ai-assisted`로 표시
- Progress와 CSV에 AI 시도 횟수, 선택 이유, 검증 근거 표시
- 결제, 삭제, 탈퇴, 초기화, 앱 제거, 시스템 설정 변경은 `Block(N/A)` 처리
- API 키가 없거나 호출이 실패하면 기존 Test Case 실행으로 자동 전환
- 실행 이력은 `qa_results/ai_testcase_history.jsonl`에 저장

AI TC RUN은 위의 `OPENAI_API_KEY`와 `OPENAI_MODEL` 설정을 AI Random Run과 공유합니다.

### Redmine 실패 이슈 검토

Test Case 실행 중 `Fail`이 발생하면 스크린샷, 기대결과, 실제 오류, 재현 경로와
기기/앱 정보를 `qa_results/redmine_failures.json`에 검토 대기 항목으로 저장합니다.
`Test Case > Fail Review`에서 내용을 보완한 뒤에만 이슈를 만들거나 기존 Redmine
이슈 번호를 연결합니다. 자동으로 이슈를 발행하지는 않습니다.

Redmine 이슈 생성 기능은 서버를 실행하는 PowerShell에서 다음 환경변수를 설정한 뒤
서버를 다시 시작해야 합니다. API 키는 브라우저나 결과 파일에 표시되지 않습니다.

```powershell
$env:REDMINE_URL="https://redmine.example.com"
$env:REDMINE_API_KEY="YOUR_REDMINE_API_KEY"
$env:REDMINE_PROJECT_ID="project-identifier"
$env:REDMINE_TRACKER_ID="1"          # optional
$env:REDMINE_DEFAULT_PRIORITY_ID="2" # optional
$env:REDMINE_ASSIGNEE_ID="12"        # optional
python mirror_server.py
```

실패 항목에 이슈를 연결한 뒤 같은 Excel을 다시 실행하면, 해당 Test Case의 최신
Pass/Fail 결과가 연결된 검토 항목의 re-test 기록에 자동으로 추가됩니다.

### 미러링 FPS / 화질 — `mirror_server.py` 상단

```python
FPS     = 10   # 높을수록 부드럽지만 CPU 증가 (권장 5~15)
QUALITY = 80   # JPEG 품질 1~100 (낮출수록 빠름)
ADB_SERIAL = ""  # 기기 여러 개면 시리얼 번호 입력 (adb devices 로 확인)
```

### Appium 기기 정보 — `appium_controller.py` 상단

```python
APPIUM_URL  = "http://127.0.0.1:4723"
DEVICE_NAME = "Galaxy Tab"
ADB_SERIAL  = ""   # adb devices 에 표시된 시리얼 번호
```

---

## 문제 해결

### `adb devices` 에 기기가 안 보임
- USB 케이블 교체 (데이터 전송 지원 케이블 필요)  
- 태블릿 화면의 "USB 디버깅 허용?" 팝업 → **허용** 클릭  
- `adb kill-server && adb start-server` 실행 후 재확인  

### 브라우저에 화면이 안 뜸
- 태블릿 화면이 꺼져 있으면 켜주세요  
- 일부 기기는 `screencap` 권한이 필요 — 화면 잠금 해제 상태에서 시도  
- `mirror_server.py` 의 `FPS` 를 낮춰보세요  

### Appium 연결 오류
1. 별도 터미널에서 `appium` 실행 확인 (기본 포트 4723)  
2. `appium driver list --installed` 에 `uiautomator2` 있는지 확인  
3. 없으면 `appium driver install uiautomator2`  

### `pip install` 실패
```
python -m pip install --upgrade pip
python -m pip install flask flask-socketio pillow Appium-Python-Client selenium
```

---

## Appium 자동화 API 예시

```python
from appium_controller import TabletController

ctrl = TabletController()

ctrl.tap(540, 960)           # 좌표 탭
ctrl.swipe(540,1500, 540,500) # 위로 스와이프
ctrl.scroll_down(3)           # 3회 스크롤 다운
ctrl.click_text("설정")       # 텍스트로 클릭
ctrl.type_text("검색어")      # 텍스트 입력
ctrl.home()                   # 홈 버튼
ctrl.back()                   # 뒤로 가기
ctrl.screenshot("out.png")    # 스크린샷 저장
ctrl.close()
```

---

## 아키텍처

```
갤럭시탭 (USB)
  │
  │  adb exec-out screencap -p
  ▼
mirror_server.py (Flask + SocketIO :5000)
  │
  │  WebSocket "frame" 이벤트 (base64 JPEG)
  ▼
브라우저 localhost:5000
  └── <img> 태그 실시간 갱신
```
