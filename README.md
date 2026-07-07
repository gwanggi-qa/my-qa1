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
