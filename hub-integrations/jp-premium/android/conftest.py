import json
import os
from pathlib import Path

import pytest

from premium_check import Device, Unobservable, inspect_case


def pytest_addoption(parser):
    parser.addoption("--device-serial")
    parser.addoption("--device-webview", action="store_true")
    parser.addoption("--allow-state-changing", action="store_true")


@pytest.fixture
def check(request):
    def run(tid):
        serial = request.config.getoption("--device-serial")
        if not serial or not request.config.getoption("--allow-state-changing"):
            pytest.skip("허브에서 기기를 선택하고 학습 진도 변경 가능성을 승인하세요.")
        cases = json.loads(Path(__file__).with_name("cases.json").read_text(encoding="utf-8"))
        case = cases[tid]
        dev = Device(serial)
        try:
            version = dev.version()
            request.node.user_properties.extend([("tc_id", tid), ("version_code", version),
                                                ("content_id", case["content_id"])])
            inspect_case(dev, case)
        except Unobservable as exc:
            pytest.skip(str(exc))
        except AssertionError:
            dest = os.environ.get("AUTOMATION_EVIDENCE_DIR")
            if dest and dev.last_capture:
                Path(dest).mkdir(parents=True, exist_ok=True)
                (Path(dest) / (tid + ".png")).write_bytes(dev.last_capture)
            raise
    return run
