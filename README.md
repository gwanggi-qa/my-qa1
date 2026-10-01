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

`Random Test > AI Random Run`은 기본적으로 **Codex CLI의 ChatGPT 로그인**을 사용합니다.
UIAutomator, OCR, Memory, 업로드된 Test Case와 이전 AI 실행 결과로 실행 순서를
한 번 계획하고, 실제 입력은 기존 ADB 실행기로 수행합니다. 매 클릭마다 AI가 화면을
다시 판단하는 방식은 아닙니다. `Live Run`은 AI 호출 없이 기존 방식으로 동작합니다.

서버를 실행하는 Windows 사용자 계정에서 먼저 로그인합니다.

```powershell
codex login
codex login status
python mirror_server.py
```

- `Logged in using ChatGPT` 상태여야 합니다. API 키 로그인은 이 경로에서 사용하지 않습니다.
- CLI 실행 파일은 PATH 또는 Codex 앱 설치 폴더에서 찾습니다. 필요한 경우
  `CODEX_CLI_PATH`에 네이티브 `codex.exe`의 전체 경로를 지정합니다.
- 모델은 CLI 기본값을 사용하며 `CODEX_MODEL`로 선택할 수 있습니다.
- 임시 폴더에서 읽기 전용, 도구 비활성 상태로 후보 ID만 반환하게 합니다.
  앱 파일 수정이나 AI가 만든 임의 좌표 실행은 허용하지 않습니다.
- 계획 생성 제한 시간은 120초이며 Stop은 CLI 계획 생성도 중단합니다.
- 로그인/한도/네트워크 오류는 일반 Live Run으로 전환하고 사유를 표시합니다.
  유료 API로 자동 전환하지 않습니다.
- 패널의 마지막 AI 실행 결과에 계획 적용 여부, 호출 시도 수, CLI가 보고한 입출력
  토큰 수를 표시합니다. 이 토큰 수는 Pro 잔여 사용량의 퍼센트가 아닙니다.
- 실행 이력은 `qa_results/ai_random_history.jsonl`에 저장됩니다.
  후보 문구, Excel 요약, 이전 결과가 AI에 전송되므로 민감 데이터는 제외하세요.
- 서버 접근자는 로그인된 계정의 사용량을 소비할 수 있습니다. 신뢰하는 내부 사용자만
  접근하도록 운영하고 공용 인터넷에 그대로 노출하지 마세요.
- AI Random Run API는 기본적으로 localhost에서만 허용합니다. 팀원의 다른 PC에서도
  사용하려면 서버를 시작하기 전에 `RANDOM_AI_ALLOW_REMOTE=1`을 설정하세요. 네트워크의
  다른 사용자가 로그인된 Codex 계정의 사용량을 쓸 수 있으므로 신뢰하는 사내망에서만 켜세요.

기존 OpenAI API 방식이 필요한 경우에만 명시적으로 선택합니다. API 요금은 별도입니다.

```powershell
$env:RANDOM_AI_PROVIDER="openai_api"
$env:OPENAI_API_KEY="YOUR_API_KEY"
$env:OPENAI_MODEL="gpt-5.6-luna"  # optional
python mirror_server.py
```

기본 경로로 돌아가려면 `RANDOM_AI_PROVIDER=codex_cli`로 설정하고 서버를 재시작합니다.
CLI 자동화 방식은 [공식 비대화형 실행 문서](https://learn.chatgpt.com/docs/non-interactive-mode)를 참고하세요.

### AI TC RUN

`Test Case > AI TC RUN`은 전체 Excel을 처음부터 원래 순서대로 재실행합니다.
기본 `AI_PROVIDER=codex_cli`는 서버를 실행하는 Windows 사용자의 `codex login`
ChatGPT 인증을 사용하며, API 키를 상속하거나 유료 API로 자동 전환하지 않습니다.
구독 사용량 한도가 적용됩니다. `codex --version`, `codex login status`로 준비 상태를 확인합니다.

- 전체 Test Case 흐름 분석 후 10개 단위로 계획 생성 (`AI_TEST_BATCH_SIZE`: 10~20)
- 사전조건/전제조건, 테스트 절차/테스트 항목, 기대결과와 원본 TC ID·시트·행 보존
- 복합 절차를 하위 동작으로 수행하고 모든 기대 문구를 함께 확인
- 실행 전 화면의 전제조건과 실행 후 기대 문구를 UIAutomator/OCR로 각각 확인
- 클릭 대상과 기대결과 후보를 분리해 기대 문구를 잘못 누르는 동작 방지
- 대상 탐색 실패·모호한 문장 Block은 최신 화면 후보로 최대 5개 추가 동작까지 복구
- 기대결과 불일치는 Fail로 보존하며, 다른 화면으로 이동해 결함을 숨기지 않음
- 없는 데이터 생성·환경 변경·음성·시각 전용 검증은 준비 사유와 함께 Block
- AI 힌트가 적용된 결과는 `ai-guided`, 복구 성공은 `ai-assisted`로 표시
- Progress와 CSV에 AI 시도 횟수, 선택 이유, 검증 근거 표시
- 결제, 삭제, 탈퇴, 초기화, 앱 제거, 시스템 설정 변경은 `Block(N/A)` 처리
- CLI 인증 실패·한도 초과·호출 실패 시 `AI Fallback`을 표시하고 일반 TC 실행으로 전환
- 실행 이력은 `qa_results/ai_testcase_history.jsonl`에 저장

API 방식은 `AI_PROVIDER=openai_api`를 명시하고 `OPENAI_API_KEY`, `OPENAI_MODEL`을 설정한 경우에만
사용합니다. CLI 모델은 `CODEX_MODEL`, 호출 제한시간은 현재 120초입니다.
`RANDOM_AI_PROVIDER`는 Random 전용 우선 설정이며 없으면 `AI_PROVIDER`를 따릅니다.
CLI AI TC 실행은 기본적으로 localhost에서만 가능하며 신뢰하는 LAN에 허용할 때만
`AI_ALLOW_REMOTE=1`을 설정합니다. 동시에 TC/Random/Recorder를 실행할 수 없습니다.
화면에는 실제 provider, 요청/응답/복구 횟수와 CLI가 반환한 토큰 사용량이 표시됩니다.
Stop은 CLI 프로세스와 후속 입력을 중단합니다. 이미 기기에 전달된 ADB 입력은 취소할 수 없습니다.

### Redmine 실패 이슈 검토

Test Case 실행 중 `Fail`이 발생하면 스크린샷, 기대결과, 실제 오류, 재현 경로와
기기/앱 정보를 `qa_results/redmine_failures.json`에 검토 대기 항목으로 저장합니다.
`Test Case > Fail Review`에서 내용을 보완한 뒤에만 이슈를 만들거나 기존 Redmine
이슈 번호를 연결합니다. 자동으로 이슈를 발행하지는 않습니다.

Redmine 이슈 생성 기능은 서버를 실행하는 PowerShell에서 다음 환경변수를 설정한 뒤
서버를 다시 시작해야 합니다. API 키는 브라우저나 결과 파일에 표시되지 않습니다.
`REDMINE_PROJECT_ID`는 기본 프로젝트를 미리 선택하는 선택값이며, 비워 두면
`Fail Review > Test connection` 후 검토 화면에서 프로젝트를 선택할 수 있습니다.

```powershell
$env:REDMINE_URL="https://redmine.example.com"
$env:REDMINE_API_KEY="YOUR_REDMINE_API_KEY"
$env:REDMINE_PROJECT_ID="project-identifier" # optional default project
$env:REDMINE_TRACKER_ID="1"          # optional
$env:REDMINE_DEFAULT_PRIORITY_ID="2" # optional
$env:REDMINE_ASSIGNEE_ID="12"        # optional
python mirror_server.py
```

`Test connection`은 현재 API 키의 인증, 프로젝트 접근 권한과 접근 가능한 프로젝트
목록을 확인합니다. 검토 화면에서는 프로젝트, 유형, 우선순위, 담당자를 Redmine
선택 목록으로 고른 뒤 발행합니다.

동일 Excel/Test Case/동작·대상 조합에 열린 Redmine 이슈가 있으면 `Create issue`는
새 이슈 대신 최신 실패 증거와 캡처를 해당 이슈에 코멘트로 추가합니다. 연결된 항목을
다시 실행해 Pass가 되면 `QA re-test PASSED` 코멘트를 비동기로 추가합니다. 이슈 상태는
자동으로 변경하거나 종료하지 않습니다.

네트워크 오류로 이슈 생성 결과를 확정할 수 없으면 자동 재시도를 막습니다. Redmine에서
생성 여부를 확인한 뒤 검토 화면의 `Link existing`으로 연결해야 중복 이슈를 방지할 수
있습니다.

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

### 사람용 QA Excel 업로드와 AI TC RUN

- `통합ID`, `테스트목적`, `사전조건`, `테스트절차`, `기대결과`가 있는 문서는 원본 그대로 업로드할 수 있습니다. `Type`은 테스트 분류로 읽습니다.
- 처음 보는 양식은 **Excel 열 매핑**에서 시트와 헤더행을 선택하고 열의 의미를 지정한 뒤, **미리보기 → 매핑 적용**으로 등록합니다. 미리보기와 업로드 실패는 기존 TC 목록을 지우지 않습니다.
- `자동화가능=FALSE`도 AI가 다시 판단합니다. `Enabled=N` 등 명시적 실행 제외는 계속 적용합니다. 셀의 줄바꿈과 원본 행 번호를 보존하며, 병합 범위 밖의 빈 조건을 임의로 채우지 않습니다.
- **AI TC RUN**은 목적·사전조건·절차·기대결과를 함께 분석합니다. 일부 문구 확인만으로 전체 TC를 Pass 처리하지 않습니다. 계산 기준/학습 데이터나 화면 구조 검증 수단이 부족하면 이유와 함께 Block으로 남깁니다.
- **Run Test Case**에서 AI 해석이 필요한 문장형 TC는 `AI 분석 대기` 사유로 Block 처리됩니다. 기존 Automation/Recorder/Manual QA/WordP 형식도 유지됩니다.

개발 검증: `python -X utf8 -m unittest discover -s tests -v`

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

## 화면 표시 검증

- `Run Test Case`와 `AI TC RUN` 모두 화면 구조/시각 조건이 있는 TC에서 설정된 AI provider를 사용합니다. 일반 Run도 이 경우 AI 사용량이 발생합니다.
- 문구 존재와 별도로 위치, 카드 내부 포함 관계, 그래프 종류/개수, 축 표시, 색상/선택 상태를 현재 캡처로 검사합니다. 표시 관련 단어만으로 사전 차단하거나 자동 Pass 처리하지 않습니다.
- 모든 필수 조건이 확인되면 Pass, 명확한 불일치는 Fail, 잘림/가림/근거 부족/AI 오류는 Block입니다. AI 보조 판정이므로 Progress의 캡처 링크와 조건별 근거를 검토하세요.
- 현재 캡처와 UI/OCR 관찰을 사용합니다. 계산 정확성에는 원본 데이터가, 디자인 일치에는 기준 이미지가, 변화/애니메이션에는 시점별 근거가 필요합니다. 제공되지 않은 자료를 추정하여 통과시키지 않습니다.
- 일반 Run은 사람용 TC의 미실행 클릭·이동을 대신 수행하지 않습니다. 현재 화면에서 가능한 검사만 수행하고, 절차 실행이 필요하면 AI TC RUN을 사용하세요.
- Codex CLI 실패 시 유료 API로 자동 전환하지 않습니다. 실패 항목은 Block으로 남기고 다음 TC를 진행합니다. Recorder/Random 실행 정책은 변경하지 않습니다.
- 캡처와 조건별 결과는 `qa_results/`와 기존 테스트 실행 이력에 저장됩니다. 원본 Excel은 변경하지 않습니다.

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
