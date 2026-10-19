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
- AI Random Run은 로컬과 `local_access_config.py`에 명시된 내부망에서 허용합니다.
  접속한 다른 PC도 서버에 로그인된 Codex 계정의 사용량을 공유합니다.
  `RANDOM_AI_ALLOW_REMOTE` 환경 변수로 허용 네트워크 범위를 넓힐 수 없습니다.

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
CLI AI TC 실행도 공통 내부망 허용 범위를 따릅니다. `AI_ALLOW_REMOTE` 환경 변수로
허용 범위를 넓힐 수 없습니다. 동시에 TC/Random/Recorder를 실행할 수 없습니다.
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

## 결과 Excel 사본 다운로드

TC ID와 검증결과 열이 있는 원본 Excel을 업로드하고 TC 실행을 마친 뒤,
Test Progress의 **결과 Excel 다운로드**를 누릅니다. 실행 중에는 다운로드할 수 없습니다.
중단된 실행도 이미 완료된 TC와 미실행 TC를 구분한 사본을 받을 수 있습니다.

- 원본 파일은 변경하지 않습니다. 업로드한 XLSX/XLSM 패키지의 해당 시트·행·TC ID를
  연결하여 검증결과 셀만 기록하고, 그림·서식·병합·다른 시트의 과거 결과값은 보존합니다.
- 이번 실행의 `passed`는 `Pass`, `failed`는 `F`, `blocked`·중단·미실행은 `NT`입니다.
  명시적인 적용 제외 결과만 `NA`이며, 검증 불가를 적용 제외로 바꾸지 않습니다.
  상세 사유와 원래 상태는 기존 실행 JSONL 이력에 남습니다.
- 알려진 검증결과 열을 참조하는 지원 형태의 `COUNTIF`/`COUNTIFS` 성공 집계는
  정확한 `"P"`와 `"Pass"` 조건의 합으로 변경합니다. 기존 소문자 `p`도 Excel의
  대소문자 미구분 집계에 포함되므로 별도 합산하지 않습니다. `P*`는 사용하지 않습니다.
  이미 두 값을 집계하는 수식은 다시 확장하지 않습니다. 공유/배열 수식은 경고와 함께 유지합니다.
- 예: `COUNTIFS(TestCase!$D:$D,$R$2,TestCase!$K:$K,P4)` →
  `COUNTIFS(TestCase!$D:$D,$R$2,TestCase!$K:$K,"P")+COUNTIFS(TestCase!$D:$D,$R$2,TestCase!$K:$K,"Pass")`.
- F/NT/NA 집계와 과목·프로젝트 필터는 그대로 둡니다. 기초영어 보고서의 `History`에
  남아 있는 다른 과목명·코드 필터는 별도로 확인해야 합니다. Pass 호환성 변경만으로
  필터 불일치가 해결되지는 않습니다. Excel에서 파일을 열 때 전체 재계산하도록 표시하며,
  서버가 수식을 계산하거나 집계 캐시값을 확정하는 것은 아닙니다.
- 원본 바이트와 최근 실행 스냅샷은 서버 메모리에만 보관합니다. 새 업로드·새 TC 실행은
  이전 다운로드를 만료시키며, 서버 재시작 후에는 다시 업로드해야 합니다. 원본 크기 제한은
  20 MiB입니다. 미리보기와 실패한 업로드는 이전 원본/실행을 교체하지 않습니다.
- 다운로드는 이 PC의 localhost에서만 가능합니다. 원본 ID·결과 열이 없거나 모호한 양식,
  중복 대상 행, 병합되거나 수식이 있는 결과 셀, 서명된 패키지는 다운로드를 차단합니다.
  기존 Automation/Recorder 실행과 CSV 내보내기는 유지됩니다.
- 이 기능은 **기존 실행기의 판정을 사본에 기록**합니다. 실행기의 false-Pass 가능성이나
  실제 기기 검증의 신뢰성을 새로 보증하지 않습니다. 과거 `P` 값은 이번 실행 성공 근거가 아닙니다.

코드 변경 후에는 서버를 재시작하고 페이지를 새로고침해야 새 버튼과 기능을 사용할 수 있습니다.
회귀 테스트는 합성 workbook과 mock 실행으로 검증하며 실제 태블릿·AI·Redmine을 호출하지 않습니다.

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
# 선택 TC 로컬 검토 실행

원본 Excel의 TC 번호를 명시하고 계획의 원문 절차·전제조건·기대조건을 검토한 뒤 승인합니다.
현재 이 경로는 도움말 열기, 환경설정 열기, 제품정보 탭 선택의 세 조회 절차만 지원합니다.
다른 절차 및 위험 동작은 Block이며 일반 `/api/run`으로 Excel 전체를 우회 실행할 수 없습니다.
AI 텍스트 계획은 선택한 원문만 사용하며 좌표·추가 절차·변경된 원문을 허용하지 않습니다.
실제 입력은 별도로 캡처한 로컬 정지 화면의 대상과 전제조건을 확인해야 한 번씩 실행됩니다.
입력 전 화면이 달라졌거나 중단·재업로드·계획 변조가 감지되면 거부합니다. 입력 결과 불명확 시 자동 재시도하지 않습니다.
준비용 뒤로는 TC 동작 전에 명시적 화면 확인 후 최대 두 번 허용하며 TC 절차로 인정하지 않습니다.
판정은 `operator-reviewed-local`로 기록합니다. 자동 AI 판정이 아니며 모든 기대조건의 화면 근거가 필요합니다.
결과 사본에는 선택한 TC만 반영하고 미선택 결과 셀은 원본 그대로 유지합니다.
이번 제한 경로는 기존 일반 AI 판정/Redmine 등의 알려진 문제를 포괄적으로 수정한 것이 아닙니다.

## 2026-10-07: 출처를 구분하는 단계 실행과 결과 화면

상단 **대시보드** 탭을 엽니다. 현재 TC·단계, 실행 이력,
판정/출처 필터, 원문·절차·기대·실제·단계별 근거·중단/미수행 사유를 확인할 수 있습니다.
자동 기기 실행, 수동 관찰, 소프트웨어 테스트, 과거 출처 불명을 구분합니다.
과거 출처 불명 Pass는 원문 기록을 보존하면서 **검증 미확인**으로 표시합니다.

- 공통 저장 형식은 schema_version=2입니다. 원문 시트·행·ID·파일 해시·업로드 ID,
  실행 ID, 앱/기기 정보(확인 불가 시 미기록), 단계별 시각·오류·이미지 근거를 기록합니다.
  기존 JSONL을 덮어쓰지 않으며 새 형식으로 저장할 때 원래 데이터도 compatibility_record에 보존합니다.
- 기존 무검토 Run/AI Run은 TC를 직접 실행하지 않습니다. **검토된 매핑 실행** 또는
  기존 운영자 단계별 검토 실행을 사용합니다. Random/Recorder API를 TC 검증 경로로 사용하지 마세요.
- 자동 경로는 최초 버전에서 도움말 열기·환경설정 열기·제품정보 선택의 읽기 전용 절차만
  허용합니다. TC마다 실제 앱에서 확인한 매핑이 필요하며, 설치된 실제 앱 매핑을 제공하는 것은 아닙니다.
  Type만으로 자동화 가능성을 확정하거나 AI가 만든 셀렉터를 자동 승인하지 않습니다.
- 각 원문 절차는 한 단계와 정확히 대응하고, 매 단계에 사전조건·후속 화면 앵커·기대조건이
  있어야 합니다. 누락/재정렬/원문 버전 불일치/미검토 위험 동작은 입력 전에 거부합니다.
  여러 절차는 줄바꿈으로 구분해야 합니다. 지원하지 않는 절차는 추정 실행하지 않습니다.
- 입력 직전에 대상 픽셀, 의미 기반 대상, 주변 계층 및 팝업 구조를 다시 검사합니다.
  다른 영역의 픽셀 애니메이션은 허용하지만 대상/구조 변화는 거부합니다. 임계값을 낮춘
  이미지 유사도 판정이 아닙니다. 수동 검토 경로도 대상의 UI 계층이 필요하며 같은 이미지라도 승인 대상 밖 좌표를 거부합니다.
- 연결/캡처/매핑 오류는 Block, 유효한 후속 화면에서 관찰한 기대 불일치는 Fail입니다.
  Stop은 대기 중인 후속 입력을 막고 입력 임계구역이 끝난 뒤 응답합니다. 이미 전달된 입력은
  취소할 수 없습니다. 불확실한 입력은 재시도하지 않으며 같은 실행 객체도 재실행할 수 없습니다.
- 결과 Excel은 기존 원본 보존 기능을 사용하며 선택 TC만 반영합니다. 미선택 행·다른 시트·
  기존 서식/이미지는 유지합니다. 원본에 결과 열이 중복된 경우 기존 명시적 열 매핑이 필요합니다.
- 새 엔진은 AI나 Redmine을 호출하지 않습니다. 운영 서버 재시작·배포와 실제 기기 수락 검증은
  별도 운영 단계입니다. 코드 적용만으로 실행 중인 서버가 바뀌지는 않습니다.

매핑 준비: **업로드 원문·버전 확인**에서 source_digest와 TC key를 확인합니다.
실제 화면 검토 뒤 아래 필드를 가진 JSON을 입력하고 검토 확인란을 선택합니다.

- 루트: upload_id, file_hash, selection(TC key 목록), mappings(동일 순서 매핑 목록)
- 매핑: source_digest, review={confirmed:true,evidence:실기기 확인 근거}, stages
- 단계: source(원문 절차 한 줄 그대로), action="tap", selector, preconditions, checks, screen
- 조건: source(원문 조건 그대로), selector, present(true/false)
- selector/screen: text 또는 content-desc 또는 resource-id를 포함한 정확한 의미 기반 매핑.
  앱별 package/class로 범위를 좁힐 수 있습니다. 좌표나 인스턴스 순서로 매핑하지 않습니다.
  원문 기대조건/사전조건 전체가 표현되지 않으면 실행을 준비하지 마세요.
  이 UI는 매핑 전문가용 첫 단계이며, 일반 사용자용 매핑 편집기는 포함하지 않습니다.

안전 회귀: `python -B -X utf8 tests/run_isolated.py`
실제 하위 프로세스·기기·외부 연결을 금지하고 임시 이력 경로를 사용합니다.
`tests/test_execution_phase1.py`의 6개 대표 명세는 합성 UI/XML fixture이며
실제 태블릿 테스트 결과가 아닙니다. 앱별 운영 매핑으로 복사하지 마세요.


자동 기대조건의 첫 버전은 명시적인 라벨의 표시/부재만 판정합니다. 예: 「도움말이 출력된다.」.
여러 라벨·개수·계산·그래프·비교를 한 라벨 검사로 대체하는 매핑은 거부합니다.
복합 조건은 모든 원문 절에 각각 검증기가 필요하며 현재 지원 범위 밖이면 Block/매핑 거부로 남깁니다.

## 2026-10-08: 실행 화면과 대시보드 탭

기본 **테스트 실행** 탭은 왼쪽 TC/실행 제어와 오른쪽 태블릿 미러링을 표시합니다.
선택 TC 검토 패널은 왼쪽 TC 영역에서 펼칠 수 있습니다. **대시보드** 탭에서 결과 이력과
판정/출처 필터, 상세 근거를 확인합니다. 두 탭의 공통 상단에서 연결·실행 상태와 중단 버튼을 확인합니다.
탭 전환은 화면 표시만 바꾸며 TC·Random·Recorder를 시작/중단하거나 연결을 끊지 않습니다.
업로드/입력 내용과 필터를 유지합니다. 좁은 화면에서는 실행 제어 위·미러링 아래로 배치합니다.


## 2026-10-08: 실제 결과 대시보드 및 입력·파일 안전성 보완

대시보드는 기존 schema_version=2 결과 API만 읽어 실행 이력·판정·출처·검색·원문·단계별 예상/실제·저장된 이미지 근거를 표시합니다.
시안의 예시 실행·합성 이미지·가공된 검증 절차는 포함하지 않습니다. 기록이 없거나 불러오지 못한 경우 해당 상태를 표시합니다.
Pass(초록), Fail(빨강), Block(골드), 미실행(회색) 집계와 중단·검증 미확인·과거 출처 불명을 구분합니다.
실행의 종료와 전체 Pass는 별개입니다. 기존 테스트 실행 탭·미러링·공통 중단·검토 매핑 도구는 유지합니다.

- 수동 입력은 TC/Random/Recorder가 실행 중이면 거부합니다. Recorder/Random도 입력 락 안에서 취소를 확인하며 중단 응답 전에 진행 중 입력이 끝나기를 기다립니다. 이미 전달된 입력을 되돌리지는 않습니다.
- 검토된 좌표 입력은 매번 UI 계층으로 승인 대상의 영역과 현재 화면을 검사합니다. 같은 이미지라도 다른 버튼의 좌표나 UI 근거가 없는 입력은 거부합니다.
- Excel은 파싱 전에 원본 20 MiB·압축 해제 합계 100 MiB·ZIP 항목 2,048개·시트 64개·행 50,000·열 1,024·전체 셀 영역 500,000 한도를 검사합니다. 경로가 포함된 파일명·비정상 ZIP 경로·DTD도 거부합니다. 미리보기에도 같은 제한을 적용합니다.
- CSV의 새 문자열은 수식 시작 문자를 데이터로 처리하고, 새 Recorder XLSX 문자열 셀도 수식으로 만들지 않습니다. 원본 기반 결과 Excel의 정당한 수식·서식·이미지 보존 정책은 유지합니다.
- 이 변경에는 인증·Origin/CSRF·접속 정책·자격정보·OS/방화벽 변경이 포함되지 않습니다. 해당 정책은 별도 승인 대기이며 기존 로컬 접속 제한을 완화하지 않았습니다.

검증은 `python -B -X utf8 tests/run_isolated.py`를 사용합니다. 실제 TC 실행·기기 입력·외부 AI/Redmine 전송은 별도 승인 없이 수행하지 않습니다.


## 사내 네트워크 접속 보호 (2026-10-19)

기본 실행은 `local_access_config.py`의 `BIND_ADDRESS = "0.0.0.0"`, `PORT = 5000`을 사용합니다. 로컬 주소는 `http://localhost:5000`, `http://127.0.0.1:5000`이며 다른 PC의 접속 주소는 `http://192.168.11.217:5000`입니다. 실제 접속 IP가 로컬 또는 `ALLOWED_NETWORKS = ("192.168.11.0/24",)`에 포함되어야 합니다. Wi-Fi 이름이 아닌 IP 범위로 제한하므로 같은 내부망의 유선 PC도 허용합니다. 지정되지 않은 Host, Origin, 프록시 전달 헤더는 거부합니다.

Windows 방화벽은 사설 프로필, 서버 Python 프로그램, TCP 5000, 로컬 주소 `192.168.11.217`, 원격 주소 `192.168.11.0/24`에 한정해 허용합니다. 공용 프로필이나 공유기 외부 포트는 개방하지 않습니다. 서버 IP가 바뀌면 `BROWSER_HOSTNAMES`와 방화벽의 로컬 주소를 명시적으로 갱신한 뒤 재시작하세요. 네트워크 범위를 자동으로 확장하지 않습니다. 게스트 Wi-Fi나 기기 간 통신 차단이 적용된 망은 별도 관리자 확인이 필요합니다.

페이지를 열 때 발급되는 메모리상의 검증값으로 API 요청과 Socket.IO 연결/제어를 검증합니다. 값은 재시작할 때 바뀌므로 기존 탭을 새로고침하세요. 장기 토큰, 계정 또는 권한 관리 기능은 아닙니다. 허용된 내부망의 사용자는 기기 조작, 업로드, 현재 지원되는 테스트 실행, AI, 결과 및 기존 검토 후 이슈 발행 기능을 함께 사용합니다. 기기와 AI 사용량은 서버 PC 것을 공유하며 기존 실행 잠금과 검토 요건을 유지합니다.

상태 변경 API는 같은 출처의 Origin, 검증 헤더, JSON 객체가 필요합니다. Excel 업로드는 기존 multipart 형식을 유지합니다. 화면의 fetch 공통 처리가 검증 헤더를 붙이고, 빈 POST/DELETE 요청을 JSON 객체로 만듭니다. 이미지와 다운로드에는 세션 동안만 유지되는 HttpOnly/SameSite=Strict 페이지 쿠키를 사용하며, 쿠키만으로 상태 변경은 허용하지 않습니다. 검증값 없이 API를 직접 호출하면 403입니다.

격리 회귀 검증: `python -B tests/run_isolated.py`. 실제 기기/외부 접속을 차단하고 임시 저장소를 사용합니다. Socket.IO 화면은 Origin을 보내는 WebSocket 전송을 사용합니다. localhost 주소/포트 설정은 요청의 Host나 프록시 헤더에서 추론하지 않습니다.
