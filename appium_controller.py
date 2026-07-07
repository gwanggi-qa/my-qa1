"""
appium_controller.py
Appium 연결 + 탭 / 스와이프 / 텍스트 입력 / 앱 실행 자동화 도우미
"""

import time
import sys

try:
    from appium import webdriver
    from appium.options import AppiumOptions
    from appium.webdriver.common.appiumby import AppiumBy
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException, WebDriverException
except ImportError:
    print("[오류] Appium 클라이언트가 없습니다. setup_and_run.bat 을 먼저 실행하세요.")
    sys.exit(1)


# ── 설정 ──────────────────────────────────────────────────────────────────────
APPIUM_URL  = "http://127.0.0.1:4723"
DEVICE_NAME = "Galaxy Tab"       # 아무 이름이나 가능
PLATFORM    = "Android"
ADB_SERIAL  = ""                 # 기기 여러 개면 "emulator-5554" 입력
# ─────────────────────────────────────────────────────────────────────────────


def build_options(app_package: str = None, app_activity: str = None) -> AppiumOptions:
    opts = AppiumOptions()
    opts.platform_name          = PLATFORM
    opts.device_name            = DEVICE_NAME
    opts.automation_name        = "UiAutomator2"
    opts.no_reset               = True
    opts.new_command_timeout    = 300

    if ADB_SERIAL:
        opts.udid = ADB_SERIAL
    if app_package:
        opts.app_package  = app_package
        opts.app_activity = app_activity or ""
    else:
        # 현재 화면 그대로 제어 (앱 강제 실행 없음)
        opts.set_capability("appium:ensureWebviewsHavePages", True)

    return opts


class TabletController:
    """태블릿 자동화 컨트롤러"""

    def __init__(self, app_package: str = None, app_activity: str = None):
        print(f"[Appium] {APPIUM_URL} 에 연결 중…")
        opts = build_options(app_package, app_activity)
        self.driver = webdriver.Remote(APPIUM_URL, options=opts)
        self.wait   = WebDriverWait(self.driver, 10)
        print("[Appium] 연결 성공 ✅")

    # ── 기본 제스처 ──────────────────────────────────────────────────────────

    def tap(self, x: int, y: int):
        """좌표 탭"""
        self.driver.execute_script("mobile: clickGesture", {"x": x, "y": y})
        print(f"[tap] ({x}, {y})")

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 400):
        """두 좌표 사이 스와이프"""
        self.driver.execute_script("mobile: swipeGesture", {
            "startX": x1, "startY": y1,
            "endX":   x2, "endY":   y2,
            "speed":  duration_ms
        })
        print(f"[swipe] ({x1},{y1}) → ({x2},{y2})")

    def scroll_down(self, steps: int = 3):
        """화면 아래로 스크롤"""
        size = self.driver.get_window_size()
        cx = size["width"] // 2
        top    = int(size["height"] * 0.75)
        bottom = int(size["height"] * 0.25)
        for _ in range(steps):
            self.swipe(cx, top, cx, bottom)
            time.sleep(0.3)

    def scroll_up(self, steps: int = 3):
        """화면 위로 스크롤"""
        size = self.driver.get_window_size()
        cx     = size["width"] // 2
        top    = int(size["height"] * 0.25)
        bottom = int(size["height"] * 0.75)
        for _ in range(steps):
            self.swipe(cx, top, cx, bottom)
            time.sleep(0.3)

    def long_press(self, x: int, y: int, duration_ms: int = 1500):
        """좌표 롱프레스"""
        self.driver.execute_script("mobile: longClickGesture", {
            "x": x, "y": y, "duration": duration_ms
        })
        print(f"[long_press] ({x}, {y}) {duration_ms}ms")

    # ── 요소 찾기 / 상호작용 ─────────────────────────────────────────────────

    def find(self, by: str, value: str, timeout: int = 10):
        """요소 대기 후 반환. by: 'id' | 'xpath' | 'text' | 'desc'"""
        by_map = {
            "id":    AppiumBy.ID,
            "xpath": AppiumBy.XPATH,
            "text":  AppiumBy.ANDROID_UIAUTOMATOR,
            "desc":  AppiumBy.ACCESSIBILITY_ID,
        }
        locator = by_map.get(by, AppiumBy.XPATH)
        if by == "text":
            value = f'new UiSelector().text("{value}")'
        return WebDriverWait(self.driver, timeout).until(
            EC.presence_of_element_located((locator, value))
        )

    def click_text(self, text: str):
        """화면에 보이는 텍스트 클릭"""
        el = self.find("text", text)
        el.click()
        print(f"[click_text] '{text}'")

    def type_text(self, text: str):
        """현재 포커스된 입력창에 텍스트 입력"""
        self.driver.execute_script("mobile: type", {"text": text})
        print(f"[type_text] '{text}'")

    def press_key(self, keycode: int):
        """Android 키코드 전송 (3=홈, 4=뒤로, 187=앱전환)"""
        self.driver.press_keycode(keycode)

    def home(self):
        self.press_key(3)

    def back(self):
        self.press_key(4)

    # ── 스크린샷 ─────────────────────────────────────────────────────────────

    def screenshot(self, path: str = "screenshot.png"):
        self.driver.save_screenshot(path)
        print(f"[screenshot] 저장 → {path}")
        return path

    # ── 앱 관리 ──────────────────────────────────────────────────────────────

    def launch_app(self, package: str, activity: str = ""):
        self.driver.execute_script("mobile: startActivity", {
            "intent": f"{package}/{activity}" if activity else package
        })
        print(f"[launch_app] {package}")

    def close(self):
        self.driver.quit()
        print("[Appium] 드라이버 종료")


# ── 사용 예시 ─────────────────────────────────────────────────────────────────

def example_basic():
    """연결 테스트 + 홈 화면 스크린샷"""
    ctrl = TabletController()
    try:
        ctrl.home()
        time.sleep(1)
        ctrl.screenshot("home.png")
        print("기본 테스트 완료 — home.png 저장됨")
    finally:
        ctrl.close()


def example_youtube():
    """YouTube 열고 검색창 탭"""
    ctrl = TabletController(
        app_package  = "com.google.android.youtube",
        app_activity = "com.google.android.youtube.HomeActivity"
    )
    try:
        time.sleep(3)
        ctrl.click_text("검색")
        time.sleep(1)
        ctrl.type_text("파이썬 강의")
        ctrl.screenshot("youtube_search.png")
    finally:
        ctrl.close()


def example_macro():
    """반복 매크로 예시 (10회 스크롤 후 스크린샷)"""
    ctrl = TabletController()
    try:
        for i in range(10):
            ctrl.scroll_down(1)
            time.sleep(0.5)
        ctrl.screenshot(f"macro_result.png")
        print("매크로 완료")
    finally:
        ctrl.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="태블릿 Appium 컨트롤러")
    parser.add_argument("--demo", choices=["basic", "youtube", "macro"],
                        default="basic", help="실행할 예시 선택")
    args = parser.parse_args()

    demos = {"basic": example_basic, "youtube": example_youtube, "macro": example_macro}
    demos[args.demo]()
