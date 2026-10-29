"""Appium input adapter for approved workflows; no app reset or implicit installation."""
import json
import os
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit
import ui_guard


class AppiumSession:
    def __init__(self, serial):
        from appium import webdriver
        from appium.options.android import UiAutomator2Options
        url = os.environ.get('QA_PREPARED_APPIUM_URL', 'http://127.0.0.1:4725')
        parsed = urlsplit(url)
        if parsed.scheme != 'http' or parsed.hostname not in {'localhost', '127.0.0.1'}:
            raise ValueError('Prepared Appium must use a local server')
        opts = UiAutomator2Options().load_capabilities({
            'platformName': 'Android', 'appium:automationName': 'UiAutomator2',
            'appium:udid': serial, 'appium:deviceName': serial,
            'appium:noReset': True, 'appium:autoLaunch': False,
            'appium:newCommandTimeout': 300})
        self.driver = webdriver.Remote(url, options=opts)

    def close(self):
        self.driver.quit()

    def xml(self):
        root = ET.fromstring(self.driver.page_source)
        for node in root.iter():
            if node.tag != 'hierarchy':
                node.tag = 'node'
        return ET.tostring(root, encoding='unicode')

    def tap(self, target):
        from appium.webdriver.common.appiumby import AppiumBy
        for attribute, method in [('text', 'text'), ('content-desc', 'description')]:
            selector = {attribute: target}
            try:
                node = ui_guard.select(self.xml(), selector, optional=True)
            except ValueError:
                raise ValueError('Appium target is ambiguous: ' + target)
            if node is None:
                continue
            if node.get('enabled') == 'false':
                raise ValueError('Appium target is disabled')
            query = 'new UiSelector().' + method + '(' + json.dumps(target, ensure_ascii=False) + ')'
            elements = self.driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, query)
            if len(elements) != 1 or not elements[0].is_displayed():
                raise ValueError('Appium target changed before input')
            elements[0].click()
            return
        raise ValueError('Appium target not found: ' + target)

    def back(self):
        self.driver.back()

    def swipe(self, direction):
        size = self.driver.get_window_size()
        self.driver.execute_script('mobile: swipeGesture', {
            'left': int(size['width']*.1), 'top': int(size['height']*.1),
            'width': int(size['width']*.8), 'height': int(size['height']*.8),
            'direction': direction, 'percent': .5})
