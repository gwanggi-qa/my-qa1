"""Explicitly consented, read-only Appium session smoke check."""
import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time
import urllib.request
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app_analysis import tool_paths


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--session', action='store_true')
    parser.add_argument('--serial', required=True)
    args = parser.parse_args()
    if not args.session:
        parser.error('--session requires explicit consent for Appium helper provisioning')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://localhost:5000/', timeout=10) as response:
        html = response.read().decode('utf-8')
    token = re.search(r'name="qa-csrf-token" content="([^"]+)"', html)[1]
    with opener.open(urllib.request.Request('http://localhost:5000/api/runtime/status',
                    headers={'X-QA-CSRF-Token': token}), timeout=10) as response:
        runtime = json.load(response)
    if any(runtime[name] for name in ('test_running', 'random_running', 'record_play_running')):
        raise RuntimeError('An existing automation is running; no session was opened.')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 4725))
    paths = tool_paths()
    env = os.environ.copy()
    env['JAVA_HOME'] = str(paths['java_home'])
    env['ANDROID_HOME'] = str(Path(os.environ['LOCALAPPDATA']) / 'Android/Sdk')
    appium = Path(os.environ['APPDATA']) / 'npm/node_modules/appium/build/lib/main.js'
    log_path = ROOT / 'qa_results/appium-environment-check.log'
    driver = None
    with log_path.open('wb') as log:
        process = subprocess.Popen([paths['node'], str(appium), '--address', '127.0.0.1',
                                    '--port', '4725', '--log-level', 'warn'],
                                   env=env, stdout=log, stderr=log,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            for _ in range(90):
                if process.poll() is not None:
                    raise RuntimeError('Appium server exited; inspect local diagnostic log.')
                try:
                    with opener.open('http://127.0.0.1:4725/status', timeout=2) as response:
                        if json.load(response)['value']['ready']:
                            break
                except (OSError, ValueError):
                    time.sleep(1)
            else:
                raise RuntimeError('Appium server startup timed out.')
            from appium import webdriver
            from appium.options.android import UiAutomator2Options
            options = UiAutomator2Options().load_capabilities({
                'platformName': 'Android', 'appium:automationName': 'UiAutomator2',
                'appium:udid': args.serial, 'appium:noReset': True,
                'appium:autoLaunch': False, 'appium:skipDeviceInitialization': True,
                'appium:dontStopAppOnReset': True, 'appium:newCommandTimeout': 60,
                'appium:adbExecTimeout': 30000, 'appium:uiautomator2ServerInstallTimeout': 60000,
            })
            driver = webdriver.Remote('http://127.0.0.1:4725', options=options)
            report = {'session_created': True, 'window_size': driver.get_window_size(),
                      'page_source_bytes': len(driver.page_source.encode()),
                      'screenshot_bytes': len(driver.get_screenshot_as_png()),
                      'device_input_performed': False, 'apk_under_test_installed': False}
            (ROOT / 'qa_results/appium-environment-check.json').write_text(
                json.dumps(report, indent=2), encoding='utf-8')
            print(json.dumps(report))
        finally:
            try:
                if driver is not None:
                    driver.quit()
            finally:
                from app_analysis import stop_process
                stop_process(process)


if __name__ == '__main__':
    main()
