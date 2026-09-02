"""
mirror_server.py
ADB screencap -> Flask + Socket.IO -> browser tablet mirror.
Excel test case upload -> ADB based tablet validation runner.
"""

import base64
import hashlib
import html as html_lib
import importlib.util
import io
import json
import os
import random
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, render_template_string, request, send_file, send_from_directory
from flask_socketio import SocketIO, emit


FPS = 10
QUALITY = 80
ADB_SERIAL = ""
BASE_DIR = Path(__file__).resolve().parent
RECORDINGS_DIR = BASE_DIR / "recordings"
QA_RESULTS_DIR = BASE_DIR / "qa_results"
AI_HISTORY_FILE = QA_RESULTS_DIR / "ai_random_history.jsonl"
AI_TEST_HISTORY_FILE = QA_RESULTS_DIR / "ai_testcase_history.jsonl"
OCR_LANGS = ["ko", "en"]
OCR_MIN_CONFIDENCE = 0.35
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6-luna").strip() or "gpt-5.6-luna"
OPENAI_API_URL = os.environ.get("OPENAI_API_URL", "https://api.openai.com/v1/responses").strip()
AI_REQUEST_TIMEOUT_S = 45
AI_HISTORY_LIMIT = 20
AI_TEST_BATCH_SIZE = 25

app = Flask(__name__)
app.config["SECRET_KEY"] = "tablet-mirror-secret"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

_streaming = False
_mirror_lock = threading.Lock()
_adb_input_lock = threading.Lock()
_test_lock = threading.Lock()
_test_steps: list[dict[str, Any]] = []
_test_metadata: dict[str, Any] = {}
_test_running = False
_test_stop_event = threading.Event()
_random_lock = threading.Lock()
_random_running = False
_random_stop_event = threading.Event()
_random_last_plan: list[dict[str, Any]] = []
_ai_history_lock = threading.Lock()
_record_play_lock = threading.Lock()
_record_play_running = False
_record_play_stop_event = threading.Event()
_ocr_lock = threading.Lock()
_ocr_reader = None
_ocr_status: dict[str, Any] = {
    "available": None,
    "state": "idle",
    "message": "OCR not loaded",
    "languages": OCR_LANGS,
}


HEADER_ALIASES = {
    "step": {"step", "step_no", "no", "order", "seq", "\uc21c\uc11c", "\ubc88\ud638", "index"},
    "action": {"action", "\ub3d9\uc791", "\uc561\uc158", "command", "cmd", "type"},
    "target": {"target", "\ub300\uc0c1", "selector", "locator"},
    "value": {"value", "\uac12", "input", "text"},
    "expected": {"expected", "expect", "\uae30\ub300\uacb0\uacfc", "\uac80\uc99d\uac12", "assert"},
    "x": {"x"},
    "y": {"y"},
    "x1": {"x1", "start_x"},
    "y1": {"y1", "start_y"},
    "x2": {"x2", "end_x"},
    "y2": {"y2", "end_y"},
    "duration_ms": {"duration_ms", "duration", "\uc2dc\uac04", "\uc9c0\uc18d\uc2dc\uac04"},
    "delay_ms": {"delay_ms", "delay", "record_delay_ms"},
    "wait_ms": {"wait_ms", "wait", "\ub300\uae30", "\ub300\uae30\uc2dc\uac04"},
    "screen_width": {"screen_width", "width", "record_width"},
    "screen_height": {"screen_height", "height", "record_height"},
    "description": {"description", "desc", "\uc124\uba85", "\ud14c\uc2a4\ud2b8\ub0b4\uc6a9", "test_case", "case"},
    "enabled": {"enabled", "use", "\uc0ac\uc6a9", "\uc2e4\ud589"},
    "block_reason": {"block_reason", "block reason", "\ucc28\ub2e8\uc0ac\uc720", "\ucc28\ub2e8 \uc0ac\uc720"},
}

SUPPORTED_ACTIONS = {
    "tap",
    "swipe",
    "input",
    "text",
    "keyevent",
    "wait",
    "assert_text",
    "assert_not_text",
    "launch",
    "back",
    "home",
    "screenshot",
    "blocked",
}

ACTION_ALIASES = {
    "start": "wait",
    "delay": "wait",
    "sleep": "wait",
    "pause": "wait",
    "\ub300\uae30": "wait",
    "\ud130\uce58": "tap",
    "\ud074\ub9ad": "tap",
    "\uc785\ub825": "input",
    "\uac80\uc99d": "assert_text",
}


MANUAL_QA_HEADER_ALIASES = {
    "depth1": {"1depth"},
    "depth2": {"2depth"},
    "depth3": {"3depth"},
    "depth4": {"4depth"},
    "depth5": {"5depth"},
    "depth6": {"6depth"},
    "depth7": {"7depth"},
    "check": {"check"},
    "expected": {"expectedresult", "expected"},
    "result": {"result"},
}

MANUAL_QA_ACTION_LABELS = [
    "\uc2dc\uc791\ud558\uae30",
    "\ub2e4\uc74c",
    "\uc774\uc804",
    "\ud648\uc73c\ub85c\uac00\uae30",
    "\ub3cc\uc544\uac00\uae30",
    "\ud655\uc778",
    "\ub2eb\uae30",
    "\ub85c\uadf8\uc778",
    "N5",
    "N4",
    "N3",
    "N2",
    "N1",
    "n5",
    "n4",
    "n3",
    "n2",
    "n1",
]

MANUAL_QA_BLOCK_PATTERNS = [
    "wifi",
    "wi-fi",
    "\uc640\uc774\ud30c\uc774",
    "\ub124\ud2b8\uc6cc\ud06c",
    "\uae30\uae30 \ub124\ube44\uac8c\uc774\uc158",
    "\ud658\uacbd\uc124\uc815",
    "\uc124\uc815 \uc571",
    "\ubc31\uadf8\ub77c\uc6b4\ub4dc",
]

MANUAL_QA_VISUAL_ONLY_PATTERNS = [
    "\ub85c\uace0",
    "\uc2a4\ud50c\ub798\uc2dc",
    "\uc774\ubbf8\uc9c0",
    "\uc544\uc774\ucf58",
    "\ubaa8\uc591",
    "\uadf8\ub798\ud504",
]

WORDP_HEADER_ALIASES = {
    "code": {"코드번호", "code"},
    "subject": {"과목명"},
    "case_no": {"no", "번호"},
    "major": {"대분류"},
    "middle": {"중분류"},
    "feature": {"기능"},
    "precondition": {"전제조건"},
    "item": {"테스트항목", "테스트내용", "testcase", "testitem"},
    "expected": {"기대결과", "expectedresult", "expected"},
    "result": {"검증결과", "검증결과pfntna", "result"},
    "note": {"비고", "오류보고내역"},
}

WORDP_TAP_WORDS = ("버튼", "누른다", "터치", "클릭", "선택", "저장", "취소", "확인")
WORDP_SWIPE_WORDS = ("스크롤", "드래그")
WORDP_INPUT_WORDS = ("입력",)
WORDP_BLOCK_PATTERNS = (
    "강제종료",
    "재실행",
    "기기설정",
    "전원버튼",
    "화면 Off",
    "back키",
    "back버튼",
    "Off-Line",
    "오프라인",
    "폴더",
    "태스크바",
    "반복",
    "Q10~Q15",
    "Q15까지",
    "모든 항목",
)
WORDP_ACTION_LABELS = (
    "프로필 추가",
    "저장",
    "취소",
    "확인",
    "설문제출",
    "설문 제출",
    "설문완료",
    "설정완료",
    "학습 시작",
    "맞춤 커리큘럼으로 학습 시작",
    "다시 학습하기",
    "홈",
    "이전",
    "다음",
    "닫기",
    "시작",
    "건너뛰기",
    "돌아가기",
    "삭제",
)
WORDP_GENERIC_LABELS = {"위치", "곳", "항목", "내용", "영역", "화면"}
@dataclass
class StepResult:
    index: int
    total: int
    status: str
    step: dict[str, Any]
    message: str = ""
    elapsed_ms: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


class BlockedStep(Exception):
    pass


class RandomStopped(Exception):
    pass


def adb(*args: str, timeout_s=None):
    cmd = ["adb"]
    if ADB_SERIAL:
        cmd += ["-s", ADB_SERIAL]
    cmd += list(args)
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout_s)
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        message = f"ADB command timed out after {timeout_s}s".encode("utf-8")
        return b"", message, 124


def check_device():
    out, _, _ = adb("devices", timeout_s=4)
    lines = out.decode(errors="ignore").strip().splitlines()
    return any("\tdevice" in line or line.endswith(" device") for line in lines[1:])


def adb_text(*args: str):
    out, _, rc = adb(*args)
    if rc != 0:
        return ""
    return out.decode("utf-8", errors="ignore").strip()


def get_device_info():
    if not check_device():
        return {"connected": False}

    serial = adb_text("get-serialno")
    manufacturer = adb_text("shell", "getprop", "ro.product.manufacturer")
    model = adb_text("shell", "getprop", "ro.product.model")
    device = adb_text("shell", "getprop", "ro.product.device")
    market_name = adb_text("shell", "getprop", "ro.product.marketname")
    android_version = adb_text("shell", "getprop", "ro.build.version.release")
    sdk = adb_text("shell", "getprop", "ro.build.version.sdk")
    resolution = adb_text("shell", "wm", "size").replace("Physical size:", "").strip()
    density = adb_text("shell", "wm", "density").replace("Physical density:", "").strip()
    battery = adb_text("shell", "dumpsys", "battery")
    level = ""
    status = ""
    for line in battery.splitlines():
        text = line.strip()
        if text.startswith("level:"):
            level = text.split(":", 1)[1].strip()
        elif text.startswith("status:"):
            status = text.split(":", 1)[1].strip()

    return {
        "connected": True,
        "serial": serial,
        "manufacturer": manufacturer,
        "model": model,
        "device": device,
        "market_name": market_name,
        "android": android_version,
        "sdk": sdk,
        "resolution": resolution,
        "density": density,
        "battery_level": level,
        "battery_status": status,
    }


def capture_frame():
    raw, _, rc = adb("exec-out", "screencap", "-p")
    if rc != 0 or len(raw) < 100:
        return None

    try:
        from PIL import Image

        img = Image.open(io.BytesIO(raw))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=QUALITY)
        return "jpeg", base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return "png", base64.b64encode(raw).decode("ascii")


def normalize_header(value: Any):
    raw = str(value or "").strip().lower().replace(" ", "_")
    raw = raw.replace("-", "_")
    for key, aliases in HEADER_ALIASES.items():
        if raw in aliases:
            return key
    return raw


def clean_cell(value: Any):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def is_enabled(value: Any):
    if value in ("", None):
        return True
    return str(value).strip().lower() not in {"n", "no", "false", "0", "skip", "disabled", "off"}


def normalize_action(value: Any):
    action = str(value or "").strip().lower()
    return ACTION_ALIASES.get(action, action)


def normalize_manual_qa_header(value: Any):
    text = str(value or "").strip().lower()
    return re.sub(r"[\s_\-]+", "", text)


def map_manual_qa_headers(row):
    mapped: dict[str, int] = {}
    normalized = [normalize_manual_qa_header(cell) for cell in row]
    for key, aliases in MANUAL_QA_HEADER_ALIASES.items():
        for index, value in enumerate(normalized):
            if value in aliases and key not in mapped:
                mapped[key] = index
    return mapped


def find_manual_qa_sheet(workbook):
    for ws in workbook.worksheets:
        max_scan = min(ws.max_row, 10)
        for row_number, row in enumerate(
            ws.iter_rows(min_row=1, max_row=max_scan, values_only=True),
            start=1,
        ):
            mapped = map_manual_qa_headers(row)
            has_depth = any(f"depth{index}" in mapped for index in range(1, 8))
            if has_depth and "check" in mapped and "expected" in mapped:
                return ws, row_number, mapped
    return None, 0, {}


def clean_manual_text(value: Any):
    return re.sub(r"\s+", " ", str(clean_cell(value) or "")).strip()


def manual_qa_cell(row, header_map, key):
    index = header_map.get(key)
    if index is None or index >= len(row):
        return ""
    return clean_manual_text(row[index])


def manual_qa_description(row_number, check_type, expected, depths):
    depth_text = " > ".join(depth for depth in depths if depth)
    summary = expected or depth_text or check_type
    if len(summary) > 90:
        summary = summary[:87] + "..."
    return f"Excel row {row_number} [{check_type or 'Manual QA'}] {summary}"


def should_block_manual_qa(depths, check_type, expected):
    text = " ".join([*depths, check_type, expected]).lower()
    for pattern in MANUAL_QA_BLOCK_PATTERNS:
        if pattern.lower() in text:
            return f"Manual QA row needs environment/device control: {pattern}"
    return ""


def should_block_visual_manual_qa(depths, expected):
    text = " ".join([*depths, expected]).lower()
    for pattern in MANUAL_QA_VISUAL_ONLY_PATTERNS:
        if pattern.lower() in text:
            return f"Visual-only QA row needs screenshot/OCR/template verification: {pattern}"
    return ""


def strip_action_label(value):
    label = str(value or "").strip()
    label = label.strip("[](){}<>\"' ")
    label = re.sub(r"\s+", " ", label)
    return label.strip()


def extract_manual_action_label(depths, expected):
    for value in reversed(depths):
        label = strip_action_label(value)
        if label in MANUAL_QA_ACTION_LABELS:
            return label

    source = " ".join([*depths, expected])
    bracketed = re.findall(r"[\[\(]([^\]\)]+)[\]\)]", source)
    quoted = re.findall(r'"([^"]+)"', source) + re.findall(r"'([^']+)'", source)
    for value in [*bracketed, *quoted]:
        label = strip_action_label(value)
        if label in MANUAL_QA_ACTION_LABELS:
            return label

    for label in MANUAL_QA_ACTION_LABELS:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(label)}(?![A-Za-z0-9])", source):
            return label
    return ""


def manual_assert_target(expected, depths):
    source = " ".join([expected, *depths])
    quoted = re.findall(r'"([^"]+)"', source) + re.findall(r"'([^']+)'", source)
    for value in quoted:
        label = strip_action_label(value)
        if label:
            return label
    for label in MANUAL_QA_ACTION_LABELS:
        if label in source:
            return label

    cleaned = re.sub(r"\s+", " ", str(expected or "")).strip()
    if len(cleaned) > 40:
        cleaned = cleaned[:40].strip()
    return cleaned or expected

def parse_manual_qa_excel(workbook):
    ws, header_row, header_map = find_manual_qa_sheet(workbook)
    if ws is None:
        return None

    steps: list[dict[str, Any]] = []
    for row_number, row in enumerate(
        ws.iter_rows(min_row=header_row + 1, values_only=True),
        start=header_row + 1,
    ):
        depths = [manual_qa_cell(row, header_map, f"depth{index}") for index in range(1, 8)]
        check_type = manual_qa_cell(row, header_map, "check")
        expected = manual_qa_cell(row, header_map, "expected")
        result = manual_qa_cell(row, header_map, "result")
        if not any([*depths, check_type, expected]):
            continue

        description = manual_qa_description(row_number, check_type, expected, depths)
        common = {
            "row": row_number,
            "step": len(steps) + 1,
            "source": "manual_qa",
            "check_type": check_type,
            "qa_expected": expected,
            "qa_result": result,
            "depths": depths,
            "description": description,
            "value": "",
            "x": "",
            "y": "",
            "x1": "",
            "y1": "",
            "x2": "",
            "y2": "",
            "duration_ms": "",
            "wait_ms": "",
            "delay_ms": "",
            "screen_width": "",
            "screen_height": "",
        }

        block_reason = should_block_manual_qa(depths, check_type, expected)
        if block_reason:
            steps.append(
                {
                    **common,
                    "action": "blocked",
                    "target": "",
                    "expected": expected,
                    "block_reason": block_reason,
                }
            )
            continue

        if "ui" in check_type.lower():
            visual_reason = should_block_visual_manual_qa(depths, expected)
            if visual_reason:
                steps.append(
                    {
                        **common,
                        "action": "blocked",
                        "target": "",
                        "expected": expected,
                        "block_reason": visual_reason,
                    }
                )
                continue

            target = manual_assert_target(expected, depths)
            steps.append(
                {
                    **common,
                    "action": "assert_text",
                    "target": target,
                    "expected": target,
                }
            )
            continue

        if check_type == "Function":
            target = extract_manual_action_label(depths, expected)
            if target:
                steps.append(
                    {
                        **common,
                        "action": "tap",
                        "target": target,
                        "expected": expected,
                    }
                )
            else:
                steps.append(
                    {
                        **common,
                        "action": "blocked",
                        "target": "",
                        "expected": expected,
                        "block_reason": "Manual QA row has no clear clickable label.",
                    }
                )
            continue

        steps.append(
            {
                **common,
                "action": "blocked",
                "target": "",
                "expected": expected,
                "block_reason": f"Unsupported manual QA check type: {check_type or 'blank'}",
            }
        )

    if not steps:
        raise ValueError("Manual QA Excel was detected, but no executable rows were found.")
    return steps


def normalize_wordp_header(value: Any):
    text = str(value or "").strip().lower()
    return re.sub(r"[\s_\-().,/]+", "", text)


def map_wordp_headers(row):
    mapped: dict[str, int] = {}
    normalized = [normalize_wordp_header(cell) for cell in row]
    for key, aliases in WORDP_HEADER_ALIASES.items():
        alias_values = {normalize_wordp_header(alias) for alias in aliases}
        for index, value in enumerate(normalized):
            if value in alias_values and key not in mapped:
                mapped[key] = index
    return mapped


def find_wordp_testcase_sheet(workbook):
    sheets = sorted(workbook.worksheets, key=lambda ws: 0 if ws.title.lower() == "testcase" else 1)
    for ws in sheets:
        max_scan = min(ws.max_row, 10)
        for row_number, row in enumerate(
            ws.iter_rows(min_row=1, max_row=max_scan, values_only=True),
            start=1,
        ):
            mapped = map_wordp_headers(row)
            if {"case_no", "item", "expected"}.issubset(mapped):
                return ws, row_number, mapped
    return None, 0, {}


def wordp_cell(row, header_map, key):
    index = header_map.get(key)
    if index is None or index >= len(row):
        return ""
    return clean_manual_text(row[index])


def wordp_description(case_no, major, middle, feature, item):
    category = " > ".join(part for part in [major, middle, feature] if part)
    summary = re.sub(r"\s+", " ", str(item or "")).strip()
    if len(summary) > 70:
        summary = summary[:67] + "..."
    prefix = str(case_no or "WordP").strip()
    return f"{prefix} [{category or 'WordP TestCase'}] {summary}"


def wordp_text_has_any(text, patterns):
    source = str(text or "").lower()
    return any(str(pattern).lower() in source for pattern in patterns)


def should_block_wordp_case(item, expected, precondition):
    text = " ".join(str(value or "") for value in [item, expected, precondition])
    if str(item or "").strip().lower() in {"p", "n/a", "na", "-"}:
        return "WordP TestCase row has no actionable test item."
    if "해당하는 위치" in str(item or "") or "다른 곳" in str(item or ""):
        return "WordP TestCase has no clear clickable label."
    for pattern in WORDP_BLOCK_PATTERNS:
        if str(pattern).lower() in text.lower():
            return f"WordP TestCase needs manual/environment control: {pattern}"
    return ""


def extract_wordp_input_value(item):
    text = str(item or "")
    quoted = re.findall(r'"([^"]+)"', text) + re.findall(r"'([^']+)'", text)
    for value in quoted:
        value = strip_action_label(value)
        if value:
            return value
    return ""


def extract_wordp_action_label(item, major="", middle="", feature=""):
    text = re.sub(r"\s+", " ", str(item or "")).strip()
    quoted = re.findall(r'"([^"]+)"', text) + re.findall(r"'([^']+)'", text)
    bracketed = re.findall(r"[\[\(]([^\]\)]+)[\]\)]", text)
    for value in [*quoted, *bracketed]:
        label = strip_action_label(value)
        if label and label not in WORDP_GENERIC_LABELS:
            return label

    for label in WORDP_ACTION_LABELS:
        if label in text:
            return label

    patterns = [
        r"([가-힣A-Za-z0-9\s]+?)\s*버튼",
        r"([가-힣A-Za-z0-9\s]+?)(?:을|를)\s*선택",
        r"([가-힣A-Za-z0-9\s]+?)(?:을|를)\s*터치",
        r"([가-힣A-Za-z0-9\s]+?)(?:을|를)\s*클릭",
        r"([가-힣A-Za-z0-9\s]+?)(?:을|를)\s*누른다",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            label = strip_action_label(match.group(1))
            label = re.sub(r"^(다시|해당하는|원하는)\s+", "", label).strip()
            if 1 <= len(label) <= 24 and label not in WORDP_GENERIC_LABELS:
                return label

    for fallback in [feature, middle, major]:
        label = strip_action_label(fallback)
        if label and len(label) <= 24 and label not in WORDP_GENERIC_LABELS:
            return label
    return ""


def wordp_expected_candidates(expected):
    text = re.sub(r"\s+", " ", str(expected or "")).strip()
    if not text:
        return []

    candidates: list[str] = []
    candidates.extend(strip_action_label(value) for value in re.findall(r'"([^"]+)"', text))
    parts = re.split(r"[\n\r\-.]+", text)
    for part in parts:
        part = strip_action_label(part)
        if not part:
            continue
        cleaned = re.sub(r"(화면|메시지|Toast).*$", r"\1", part).strip()
        cleaned = re.sub(r"(으로|로)?\s*(이동|노출|표시|생성|저장|변경).*$", "", cleaned).strip()
        cleaned = re.sub(r"(이|가|은|는|을|를)$", "", cleaned).strip()
        for value in [cleaned, part]:
            value = strip_action_label(value)
            if 2 <= len(value) <= 40 and value not in candidates:
                candidates.append(value)
    return candidates[:6]


def parse_wordp_testcase_excel(workbook):
    ws, header_row, header_map = find_wordp_testcase_sheet(workbook)
    if ws is None:
        return None

    steps: list[dict[str, Any]] = []
    for row_number, row in enumerate(
        ws.iter_rows(min_row=header_row + 1, values_only=True),
        start=header_row + 1,
    ):
        case_no = wordp_cell(row, header_map, "case_no")
        major = wordp_cell(row, header_map, "major")
        middle = wordp_cell(row, header_map, "middle")
        feature = wordp_cell(row, header_map, "feature")
        precondition = wordp_cell(row, header_map, "precondition")
        item = wordp_cell(row, header_map, "item")
        expected = wordp_cell(row, header_map, "expected")
        result = wordp_cell(row, header_map, "result")
        if not any([case_no, major, middle, feature, precondition, item, expected]):
            continue

        assert_candidates = wordp_expected_candidates(expected)
        common = {
            "row": row_number,
            "step": len(steps) + 1,
            "source": "wordp_testcase",
            "case_no": case_no,
            "major": major,
            "middle": middle,
            "feature": feature,
            "precondition": precondition,
            "test_item": item,
            "qa_expected": expected,
            "qa_result": result,
            "description": wordp_description(case_no, major, middle, feature, item),
            "value": "",
            "expected": expected,
            "assert_candidates": assert_candidates,
            "x": "",
            "y": "",
            "x1": "",
            "y1": "",
            "x2": "",
            "y2": "",
            "duration_ms": "",
            "wait_ms": "",
            "delay_ms": "",
            "screen_width": "",
            "screen_height": "",
        }

        block_reason = should_block_wordp_case(item, expected, precondition)
        if block_reason:
            steps.append({**common, "action": "blocked", "target": "", "block_reason": block_reason})
            continue

        if wordp_text_has_any(item, WORDP_SWIPE_WORDS):
            steps.append({**common, "action": "swipe", "target": "", "duration_ms": 450})
            continue

        if wordp_text_has_any(item, WORDP_INPUT_WORDS):
            value = extract_wordp_input_value(item)
            if value:
                steps.append({**common, "action": "input", "target": "", "value": value})
            else:
                steps.append(
                    {
                        **common,
                        "action": "blocked",
                        "target": "",
                        "block_reason": "WordP TestCase input value is not explicit.",
                    }
                )
            continue

        if wordp_text_has_any(item, WORDP_TAP_WORDS):
            target = extract_wordp_action_label(item, major, middle, feature)
            if target:
                steps.append({**common, "action": "tap", "target": target})
            else:
                steps.append(
                    {
                        **common,
                        "action": "blocked",
                        "target": "",
                        "block_reason": "WordP TestCase has no clear clickable label.",
                    }
                )
            continue

        if assert_candidates:
            steps.append({**common, "action": "assert_text", "target": assert_candidates[0]})
            continue

        steps.append(
            {
                **common,
                "action": "blocked",
                "target": "",
                "block_reason": "WordP TestCase sentence is ambiguous for automation.",
            }
        )

    if not steps:
        raise ValueError("WordP TestCase Excel was detected, but no executable rows were found.")
    return steps


def find_automation_sheet(workbook):
    for ws in workbook.worksheets:
        if ws.title.strip().casefold() in {"automation", "\uc790\ub3d9\ud654"}:
            return ws
    return None


def parse_standard_excel_sheet(ws, source="testcase"):
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise ValueError(f"The '{ws.title}' sheet is empty.")

    header_index = next(
        (
            index
            for index, row in enumerate(rows[:10])
            if "action" in {normalize_header(cell) for cell in row}
        ),
        None,
    )
    if header_index is None:
        raise ValueError(f"The '{ws.title}' sheet needs an Action column in its first 10 rows.")

    headers = [normalize_header(cell) for cell in rows[header_index]]
    steps: list[dict[str, Any]] = []
    is_recorder_excel = {
        "action",
        "delay_ms",
        "screen_width",
        "screen_height",
    }.issubset(set(headers)) and {"x", "y", "x1", "y1", "x2", "y2"}.intersection(headers)

    for row_index, row in enumerate(rows[header_index + 1:], start=header_index + 2):
        record = {
            headers[col_index]: clean_cell(row[col_index]) if col_index < len(row) else ""
            for col_index in range(len(headers))
            if headers[col_index]
        }

        if not any(str(v).strip() for v in record.values()):
            continue
        if not is_enabled(record.get("enabled", "")):
            continue

        raw_action = str(record.get("action", "") or "").strip().lower()
        action = normalize_action(raw_action)
        description_text = str(record.get("description", "") or "")
        if raw_action == "start" and re.search(r"tap|click", description_text, re.IGNORECASE):
            action = "tap"
        if not action:
            raise ValueError(f"Row {row_index}: action is required.")
        if action not in SUPPORTED_ACTIONS:
            supported = ", ".join(sorted(SUPPORTED_ACTIONS))
            raise ValueError(f"Row {row_index}: unsupported action '{action}'. Supported: {supported}")

        step = {
            "row": row_index,
            "step": record.get("step", len(steps) + 1),
            "action": action,
            "target": str(record.get("target", "") or "").strip(),
            "value": str(record.get("value", "") or "").strip(),
            "expected": str(record.get("expected", "") or "").strip(),
            "description": description_text.strip(),
            "x": record.get("x", ""),
            "y": record.get("y", ""),
            "x1": record.get("x1", ""),
            "y1": record.get("y1", ""),
            "x2": record.get("x2", ""),
            "y2": record.get("y2", ""),
            "duration_ms": record.get("duration_ms", ""),
            "wait_ms": record.get("wait_ms", ""),
            "delay_ms": record.get("delay_ms", ""),
            "screen_width": record.get("screen_width", ""),
            "screen_height": record.get("screen_height", ""),
            "block_reason": str(record.get("block_reason", "") or "").strip(),
            "source": "recorder" if is_recorder_excel else source,
        }
        if action == "blocked" and not step["block_reason"]:
            step["block_reason"] = step["description"] or "Blocked by Excel automation policy."
        if is_recorder_excel and not step["description"]:
            if action == "tap":
                step["description"] = f"Recorder tap ({step['x']}, {step['y']})"
            elif action == "swipe":
                step["description"] = (
                    f"Recorder swipe ({step['x1']}, {step['y1']}) -> "
                    f"({step['x2']}, {step['y2']})"
                )
        steps.append(step)

    if not steps:
        raise ValueError(f"No executable test steps found in '{ws.title}'.")

    return steps


def parse_excel(file_storage):
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError("openpyxl is not installed. Run setup_and_run.bat again.") from exc

    file_storage.stream.seek(0)
    payload = file_storage.stream.read()
    wb = load_workbook(io.BytesIO(payload), data_only=True)

    automation_sheet = find_automation_sheet(wb)
    if automation_sheet is not None:
        return parse_standard_excel_sheet(automation_sheet, source="automation")

    manual_qa_steps = parse_manual_qa_excel(wb)
    if manual_qa_steps is not None:
        return manual_qa_steps
    wordp_steps = parse_wordp_testcase_excel(wb)
    if wordp_steps is not None:
        return wordp_steps

    return parse_standard_excel_sheet(wb.active)


def int_value(value: Any, default: int | None = None):
    if value in ("", None):
        if default is None:
            raise ValueError("Missing number")
        return default
    return int(float(value))


def parse_point(step):
    if step.get("target"):
        values = [part.strip() for part in step["target"].split(",")]
        if len(values) >= 2:
            return int_value(values[0]), int_value(values[1])
    return int_value(step.get("x")), int_value(step.get("y"))


def looks_like_point(value: Any):
    text = str(value or "").strip()
    return bool(re.fullmatch(r"-?\d+(\.\d+)?\s*,\s*-?\d+(\.\d+)?", text))


def parse_swipe(step):
    if step.get("target"):
        values = [part.strip() for part in step["target"].split(",")]
        if len(values) >= 4:
            return tuple(int_value(value) for value in values[:4])
    return (
        int_value(step.get("x1")),
        int_value(step.get("y1")),
        int_value(step.get("x2")),
        int_value(step.get("y2")),
    )


def parse_resolution_text(value: Any):
    match = re.search(r"(\d+)\s*x\s*(\d+)", str(value or ""))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def step_resolution(step):
    width = step.get("screen_width")
    height = step.get("screen_height")
    if width in ("", None) or height in ("", None):
        return None
    return int_value(width), int_value(height)


def recorder_resolution_from_steps(steps):
    for step in steps:
        if step.get("source") == "recorder":
            resolution = step_resolution(step)
            if resolution:
                return resolution
    return None


def current_device_resolution():
    resolution_text = adb_text("shell", "wm", "size").replace("Physical size:", "").strip()
    return parse_resolution_text(resolution_text)


def current_screen_resolution():
    """Return the actual screencap size so display rotation is reflected."""
    try:
        from PIL import Image

        raw = capture_screen_png()
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
        if width > 0 and height > 0:
            return width, height, "screencap"
    except Exception:
        pass

    fallback = current_device_resolution()
    if fallback:
        return fallback[0], fallback[1], "wm_size"
    return None


def resolution_payload(resolution):
    if not resolution:
        return None
    width, height, source = resolution
    orientation = "landscape" if width > height else "portrait" if height > width else "square"
    return {"width": width, "height": height, "source": source, "orientation": orientation}


def recorder_resolution_status(steps):
    expected = recorder_resolution_from_steps(steps)
    if not expected:
        return None

    current = current_screen_resolution()
    expected_payload = resolution_payload((expected[0], expected[1], "recording"))
    current_payload = resolution_payload(current)
    return {
        "expected": expected_payload,
        "current": current_payload,
        "matches": bool(current and (current[0], current[1]) == expected),
    }


class RecorderResolutionMismatch(RuntimeError):
    def __init__(self, status):
        self.status = status
        expected = status["expected"]
        current = status["current"]
        super().__init__(
            f"Recorder Excel resolution {expected['width']}x{expected['height']} "
            f"does not match current screen {current['width']}x{current['height']}. "
            "Rotate the device to the recorded orientation before running."
        )


def validate_recorder_resolution(steps):
    status = recorder_resolution_status(steps)
    if not status:
        return
    if not status["current"]:
        raise RuntimeError("Could not read current screen resolution for Recorder Excel.")
    if not status["matches"]:
        raise RecorderResolutionMismatch(status)


def apply_recorder_delay(step):
    if step.get("source") != "recorder":
        return 0
    delay_ms = int_value(step.get("delay_ms"), 0)
    if delay_ms > 0:
        time.sleep(delay_ms / 1000)
    return delay_ms


def testcase_source(steps):
    if steps and all(step.get("source") == "recorder" for step in steps):
        return "recorder"
    if steps and all(step.get("source") == "automation" for step in steps):
        return "automation"
    if steps and all(step.get("source") == "manual_qa" for step in steps):
        return "manual_qa"
    if steps and all(step.get("source") == "wordp_testcase" for step in steps):
        return "wordp_testcase"
    return "testcase"


def adb_text_arg(text):
    # ADB input text treats spaces specially.
    return text.replace(" ", "%s")


def dump_ui_xml():
    _, _, dump_rc = adb("shell", "uiautomator", "dump", "/sdcard/window_dump.xml", timeout_s=7)
    if dump_rc != 0:
        return ""
    out, _, rc = adb("exec-out", "cat", "/sdcard/window_dump.xml", timeout_s=4)
    if rc != 0:
        return ""
    return out.decode("utf-8", errors="ignore")


def capture_screen_hash():
    raw, _, rc = adb("exec-out", "screencap", "-p")
    if rc != 0 or len(raw) < 100:
        return ""
    return hashlib.sha1(raw).hexdigest()


def capture_screen_png():
    raw, err, rc = adb("exec-out", "screencap", "-p", timeout_s=8)
    if rc != 0 or len(raw) < 100:
        raise RuntimeError(err.decode(errors="ignore") or "screencap failed")
    return raw


def ocr_status_payload():
    with _ocr_lock:
        data = dict(_ocr_status)
        data["installed"] = importlib.util.find_spec("easyocr") is not None
        data["languages"] = list(OCR_LANGS)
        return data


def ai_status_payload():
    configured = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    return {
        "configured": configured,
        "state": "ready" if configured else "unavailable",
        "model": OPENAI_MODEL,
        "message": (
            f"AI planner ready: {OPENAI_MODEL}"
            if configured
            else "Set OPENAI_API_KEY before starting the server. AI Run will use Live Run fallback until configured."
        ),
    }


def get_ocr_reader():
    global _ocr_reader

    with _ocr_lock:
        if _ocr_reader is not None:
            _ocr_status.update({"available": True, "state": "ready", "message": "OCR ready"})
            return _ocr_reader
        if _ocr_status.get("available") is False:
            return None

        _ocr_status.update({"available": None, "state": "loading", "message": "Loading EasyOCR"})
        try:
            import easyocr

            _ocr_reader = easyocr.Reader(OCR_LANGS, gpu=False, verbose=False)
            _ocr_status.update({"available": True, "state": "ready", "message": "OCR ready"})
            return _ocr_reader
        except Exception as exc:
            _ocr_status.update(
                {
                    "available": False,
                    "state": "unavailable",
                    "message": f"EasyOCR unavailable: {exc}",
                }
            )
            return None


def ocr_screen_texts():
    reader = get_ocr_reader()
    if reader is None:
        return []

    try:
        import numpy as np
        from PIL import Image

        raw = capture_screen_png()
        image = np.array(Image.open(io.BytesIO(raw)).convert("RGB"))
        results = reader.readtext(image, detail=1, paragraph=False)
    except Exception as exc:
        with _ocr_lock:
            _ocr_status.update({"available": False, "state": "unavailable", "message": f"OCR failed: {exc}"})
        return []

    items: list[dict[str, Any]] = []
    for result in results:
        if len(result) < 3:
            continue
        box, text, confidence = result[0], str(result[1] or "").strip(), float(result[2] or 0)
        if not text or confidence < OCR_MIN_CONFIDENCE:
            continue
        try:
            xs = [int(round(point[0])) for point in box]
            ys = [int(round(point[1])) for point in box]
        except Exception:
            continue
        x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
        items.append(
            {
                "text": text,
                "confidence": round(confidence, 3),
                "bounds": [x1, y1, x2, y2],
                "center": {"x": (x1 + x2) // 2, "y": (y1 + y2) // 2},
            }
        )

    with _ocr_lock:
        _ocr_status.update({"available": True, "state": "ready", "message": f"OCR ready: {len(items)} texts"})
    return items


def wait_for_screen_change(before_hash, timeout_s=5.0, stop_event=None):
    if not before_hash:
        deadline = time.time() + 1.0
        while time.time() < deadline:
            if stop_event and stop_event.is_set():
                return False
            time.sleep(min(0.1, max(0, deadline - time.time())))
        return False

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if stop_event and stop_event.is_set():
            return False
        time.sleep(min(0.35, max(0, deadline - time.time())))
        after_hash = capture_screen_hash()
        if after_hash and after_hash != before_hash:
            stable_deadline = time.time() + 1.0
            while time.time() < stable_deadline:
                if stop_event and stop_event.is_set():
                    return True
                time.sleep(min(0.1, max(0, stable_deadline - time.time())))
            return True
    return False


def node_attrs(node_xml):
    return {
        key: html_lib.unescape(value)
        for key, value in re.findall(r'([\w:-]+)="([^"]*)"', node_xml)
    }


def parse_bounds(bounds):
    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not match:
        return None
    x1, y1, x2, y2 = [int(value) for value in match.groups()]
    return x1, y1, x2, y2


def text_candidates_from_step(step):
    candidates: list[str] = []

    for candidate_key in ("assert_candidates", "ai_target_candidates", "ai_expected_candidates"):
        extra_candidates = step.get(candidate_key) or []
        if isinstance(extra_candidates, list):
            candidates.extend(str(value).strip() for value in extra_candidates if str(value or "").strip())

    for key in ("target", "value", "expected"):
        value = str(step.get(key) or "").strip()
        if value and not looks_like_point(value) and not re.fullmatch(r"\d+(\.\d+)?", value):
            candidates.append(value)

    description = str(step.get("description") or "").strip()
    if description:
        quoted = re.findall(r'"([^"]+)"', description) + re.findall(r"'([^']+)'", description)
        bracketed = re.findall(r"[\[\(]([^\]\)]+)[\]\)]", description)
        candidates.extend(part.strip() for part in [*quoted, *bracketed] if part.strip())
        if len(description) <= 40:
            candidates.append(description)

    unique: list[str] = []
    for candidate in candidates:
        candidate = re.sub(r"\s+", " ", candidate).strip()
        if candidate and candidate not in unique:
            unique.append(candidate)
    return unique


def text_value_matches(candidate, value):
    candidate = str(candidate or "").strip()
    value = str(value or "").strip()
    if not candidate or not value:
        return False
    candidate_lower = candidate.lower()
    value_lower = value.lower()
    compact_candidate = re.sub(r"\s+", "", candidate_lower)
    compact_value = re.sub(r"\s+", "", value_lower)
    return candidate_lower in value_lower or compact_candidate in compact_value


def find_ocr_text_center(candidates):
    useful = [str(candidate or "").strip() for candidate in candidates if str(candidate or "").strip()]
    if not useful:
        return None

    for item in ocr_screen_texts():
        text = item.get("text", "")
        for candidate in useful:
            if text_value_matches(candidate, text):
                center = item.get("center") or {}
                return {
                    "x": int(center.get("x", 0)),
                    "y": int(center.get("y", 0)),
                    "text": text,
                    "candidate": candidate,
                    "source": "ocr",
                    "confidence": item.get("confidence", 0),
                }
    return None


def find_ui_text_center(candidates):
    xml = dump_ui_xml()
    if not xml:
        return find_ocr_text_center(candidates)

    nodes = re.findall(r"<node\b[^>]*>", xml)
    for candidate in candidates:
        for node in nodes:
            attrs = node_attrs(node)
            values = [
                attrs.get("text", ""),
                attrs.get("content-desc", ""),
                attrs.get("resource-id", ""),
            ]
            for value in values:
                if text_value_matches(candidate, value):
                    bounds = parse_bounds(attrs.get("bounds", ""))
                    if bounds:
                        x1, y1, x2, y2 = bounds
                        return {
                            "x": (x1 + x2) // 2,
                            "y": (y1 + y2) // 2,
                            "text": value,
                            "candidate": candidate,
                        }
    return find_ocr_text_center(candidates)


def allows_unchanged_after_tap(step, text_hit):
    description = str(step.get("description") or "")
    matched_text = str((text_hit or {}).get("text") or "")
    return any(value in description or matched_text == value for value in ["\\ub2e4\\uc74c", "\\uc774\\uc804", "Next", "Previous"])


def ui_contains(text):
    xml = dump_ui_xml()
    if not xml:
        return False
    return text in xml


def wait_until_ui_contains(text, timeout_s=5.0, should_exist=True):
    deadline = time.time() + timeout_s
    next_ocr_at = 0.0
    while time.time() < deadline:
        found = ui_contains(text)
        if should_exist and not found and time.time() >= next_ocr_at:
            found = find_ocr_text_center([text]) is not None
            next_ocr_at = time.time() + 1.0
        if found == should_exist:
            return True
        time.sleep(0.35)
    return False


def wait_until_any_ui_contains(candidates, timeout_s=5.0):
    deadline = time.time() + timeout_s
    useful = [candidate for candidate in candidates if candidate]
    next_ocr_at = 0.0
    while time.time() < deadline:
        xml = dump_ui_xml()
        if xml:
            compact_xml = xml.replace(" ", "")
            for candidate in useful:
                compact_candidate = candidate.replace(" ", "")
                if candidate in xml or compact_candidate in compact_xml:
                    return candidate
        if time.time() >= next_ocr_at:
            hit = find_ocr_text_center(useful)
            if hit:
                return hit.get("candidate") or hit.get("text") or ""
            next_ocr_at = time.time() + 1.0
        time.sleep(0.35)
    return ""


def qa_results_dir():
    QA_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    return QA_RESULTS_DIR


def capture_failure_screenshot(run_type, index):
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    safe_type = re.sub(r"[^A-Za-z0-9_-]+", "-", str(run_type or "qa")).strip("-") or "qa"
    path = qa_results_dir() / f"{timestamp}-{safe_type}-{index}.png"
    raw, _, rc = adb("exec-out", "screencap", "-p")
    if rc != 0 or len(raw) < 100:
        return ""
    path.write_bytes(raw)
    return str(path)


def qa_result_screenshot_url(path):
    filename = Path(path).name
    if not filename:
        return ""
    return f"/api/results/screenshot/{filename}"


def failure_extra(run_type, index, extra=None):
    payload = dict(extra or {})
    screenshot = capture_failure_screenshot(run_type, index)
    if screenshot:
        payload["screenshot"] = screenshot
        payload["screenshot_file"] = Path(screenshot).name
        payload["screenshot_url"] = qa_result_screenshot_url(screenshot)
    return payload


def emit_progress(result: StepResult):
    payload = {
        "index": result.index,
        "total": result.total,
        "status": result.status,
        "step": result.step,
        "message": result.message,
        "elapsed_ms": result.elapsed_ms,
        "extra": result.extra,
    }
    socketio.emit("test_progress", payload, namespace="/mirror")


ERROR_TEXT_PATTERNS = [
    "FATAL EXCEPTION",
    "ANR in",
    "Force finishing",
    "has stopped",
    "keeps stopping",
    "Application Error",
    "Unfortunately",
    "crash",
    "not responding",
]

def normalize_ui_label(value):
    return re.sub(r"\s+", " ", html_lib.unescape(str(value or ""))).strip()


def bounds_center(bounds):
    parsed = parse_bounds(bounds)
    if not parsed:
        return None
    x1, y1, x2, y2 = parsed
    width = x2 - x1
    height = y2 - y1
    if width <= 0 or height <= 0:
        return None
    return {
        "x": (x1 + x2) // 2,
        "y": (y1 + y2) // 2,
        "width": width,
        "height": height,
        "area": width * height,
        "bounds": bounds,
    }


def ui_node_score(label, attrs, center):
    priority_keywords = ("Unit", "MY", "JLPT", "N1", "N2", "N3", "N4", "N5", "학습", "문법", "독해", "단어", "문제", "퀴즈")
    score = 4
    if label:
        score += 3
    if "Tab" in label:
        score += 2
    if any(keyword.lower() in label.lower() for keyword in priority_keywords):
        score += 3
    if attrs.get("scrollable") == "true":
        score += 2
    if attrs.get("selected") == "true":
        score -= 2
    if attrs.get("enabled") == "false":
        score -= 6
    if center["area"] < 1600:
        score -= 3
    if not label and attrs.get("clickable") == "true":
        score -= 1
    return max(1, score)

    score = 4
    if label:
        score += 3
    if "Tab" in label:
        score += 2
    if re.search(r"Unit|?????|????????????????釉먮폁?????????????????????????ロ깫??????????筌띯뫔??????????????MY", label):
        score += 3
    if attrs.get("scrollable") == "true":
        score += 2
    if attrs.get("selected") == "true":
        score -= 2
    if attrs.get("enabled") == "false":
        score -= 6
    if center["area"] < 1600:
        score -= 3
    if not label and attrs.get("clickable") == "true":
        score -= 1
    return max(1, score)


def analyze_ui_candidates():
    xml = dump_ui_xml()
    if not xml:
        return [], ""

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for index, node in enumerate(re.findall(r"<node\b[^>]*>", xml), start=1):
        attrs = node_attrs(node)
        clickable = attrs.get("clickable") == "true"
        scrollable = attrs.get("scrollable") == "true"
        if not clickable and not scrollable:
            continue

        center = bounds_center(attrs.get("bounds", ""))
        if not center:
            continue

        label = normalize_ui_label(
            attrs.get("content-desc")
            or attrs.get("text")
            or attrs.get("resource-id")
            or ""
        )
        if not label and not scrollable and center["area"] > 300000:
            continue
        key = (attrs.get("class", ""), center["bounds"], label)
        if key in seen:
            continue
        seen.add(key)

        action = "swipe" if scrollable and not clickable else "tap"
        score = ui_node_score(label, attrs, center)
        if action == "swipe":
            step = {
                "action": "swipe",
                "target": label or "scrollable area",
                "description": label or "Scroll visible content",
                "x1": center["x"],
                "y1": min(center["y"] + 180, center["y"] + center["height"] // 3),
                "x2": center["x"],
                "y2": max(center["y"] - 180, center["y"] - center["height"] // 3),
                "duration_ms": 450,
            }
        else:
            step = {
                "action": "tap",
                "target": label or "unlabeled UI",
                "description": label or f"Unlabeled {attrs.get('class', 'view')}",
                "x": center["x"],
                "y": center["y"],
            }

        candidates.append(
            {
                "node_index": index,
                "label": label or "(no label)",
                "class": attrs.get("class", ""),
                "package": attrs.get("package", ""),
                "bounds": center["bounds"],
                "x": center["x"],
                "y": center["y"],
                "action": action,
                "score": score,
                "source": "ui-analysis",
                "selected": attrs.get("selected") == "true",
                "step": step,
            }
        )

    candidates.sort(key=lambda item: (-item["score"], item["y"], item["x"]))
    return candidates, xml


def build_ocr_candidates():
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    for index, item in enumerate(ocr_screen_texts(), start=1):
        label = normalize_ui_label(item.get("text", ""))
        if not label:
            continue
        compact_label = re.sub(r"\s+", "", label.lower())
        if compact_label in seen:
            continue
        seen.add(compact_label)

        bounds = item.get("bounds") or []
        center = item.get("center") or {}
        if len(bounds) != 4 or not center:
            continue
        x1, y1, x2, y2 = [int(value) for value in bounds]
        bounds_text = f"[{x1},{y1}][{x2},{y2}]"
        x, y = int(center["x"]), int(center["y"])
        confidence = float(item.get("confidence") or 0)
        score = 8 + min(4, int(confidence * 4))

        step = {
            "action": "tap",
            "target": label,
            "description": f"OCR text: {label}",
            "x": x,
            "y": y,
        }
        candidates.append(
            {
                "node_index": f"ocr-{index}",
                "label": label,
                "class": "ocr-text",
                "package": "",
                "bounds": bounds_text,
                "x": x,
                "y": y,
                "action": "tap",
                "score": score,
                "source": "ocr-text",
                "selected": False,
                "step": step,
                "confidence": confidence,
            }
        )

    return candidates


def point_in_bounds(x, y, bounds):
    parsed = parse_bounds(bounds)
    if not parsed:
        return False
    x1, y1, x2, y2 = parsed
    return x1 <= x <= x2 and y1 <= y <= y2


def normalize_random_memory_actions(actions):
    if actions in ("", None):
        return []
    if not isinstance(actions, list):
        raise ValueError("memory_actions must be a list.")

    normalized: list[dict[str, Any]] = []
    for index, action in enumerate(actions[:200], start=1):
        action_type = str(action.get("type") or "").strip().lower()
        if action_type not in {"tap", "swipe"}:
            continue

        width = payload_int(action, "screen_width", minimum=1, maximum=10000)
        height = payload_int(action, "screen_height", minimum=1, maximum=10000)
        item = {
            "type": action_type,
            "screen_width": width,
            "screen_height": height,
            "memory_index": payload_int(action, "memory_index", index, minimum=1, maximum=100000),
        }
        if action_type == "tap":
            x, y = validate_input_point(action)
            item.update({"x": x, "y": y})
        else:
            x1, y1 = validate_input_point(action, "x1", "y1")
            x2, y2 = validate_input_point(action, "x2", "y2")
            duration = payload_int(action, "duration_ms", 350, minimum=50, maximum=10000)
            item.update({"x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration_ms": duration})
        normalized.append(item)
    return normalized


def build_memory_candidates(memory_actions, ui_candidates):
    candidates: list[dict[str, Any]] = []
    for index, action in enumerate(memory_actions, start=1):
        if action["type"] == "tap":
            probe_x, probe_y = action["x"], action["y"]
            step = {
                "action": "tap",
                "target": f"memory tap #{action['memory_index']}",
                "description": f"Memory tap #{action['memory_index']}",
                "x": action["x"],
                "y": action["y"],
            }
        else:
            probe_x = (action["x1"] + action["x2"]) // 2
            probe_y = (action["y1"] + action["y2"]) // 2
            step = {
                "action": "swipe",
                "target": f"memory swipe #{action['memory_index']}",
                "description": f"Memory swipe #{action['memory_index']}",
                "x1": action["x1"],
                "y1": action["y1"],
                "x2": action["x2"],
                "y2": action["y2"],
                "duration_ms": action.get("duration_ms", 350),
            }

        matched = None
        for candidate in ui_candidates:
            if point_in_bounds(probe_x, probe_y, candidate.get("bounds", "")):
                matched = candidate
                break

        source = "functional-memory" if matched else "nonfunctional-memory"
        label = matched["label"] if matched else step["target"]
        step["description"] = f"{source}: {label}"
        candidates.append(
            {
                "node_index": f"memory-{index}",
                "label": label,
                "class": matched.get("class", "") if matched else "",
                "package": matched.get("package", "") if matched else "",
                "bounds": matched.get("bounds", "") if matched else "",
                "x": probe_x,
                "y": probe_y,
                "action": action["type"],
                "score": 12 if matched else 9,
                "source": source,
                "selected": False,
                "step": step,
            }
        )
    return candidates


def collect_random_candidates(memory_actions=None):
    candidates, _ = analyze_ui_candidates()
    ocr_candidates = build_ocr_candidates()
    analysis_candidates = [*candidates, *ocr_candidates]
    memory_candidates = build_memory_candidates(memory_actions or [], analysis_candidates)
    combined_candidates = [*memory_candidates, *analysis_candidates]
    for index, candidate in enumerate(combined_candidates, start=1):
        candidate["candidate_id"] = f"c{index:03d}"
    return combined_candidates


def build_random_plan(max_steps=10, memory_actions=None):
    combined_candidates = collect_random_candidates(memory_actions)
    if not combined_candidates:
        return []

    planned: list[dict[str, Any]] = []
    pool = list(combined_candidates)
    for index in range(max_steps):
        if not pool:
            pool = list(combined_candidates)
        weights = [max(1, item["score"]) for item in pool]
        choice = random.choices(pool, weights=weights, k=1)[0]
        pool.remove(choice)
        step = dict(choice["step"])
        source = choice.get("source", "ui-analysis")
        step["description"] = choice["label"] if source == "ui-analysis" else f"{source}: {choice['label']}"
        planned.append(
            {
                "index": index + 1,
                "candidate": choice,
                "step": step,
                "risk": "high" if choice["label"] == "(no label)" else "medium",
                "verify": f"{source} + screen-change + error scan",
            }
        )
    return planned


AI_RISKY_LABELS = (
    "purchase",
    "payment",
    "buy now",
    "delete account",
    "factory reset",
    "uninstall",
    "결제",
    "구매",
    "탈퇴",
    "계정 삭제",
    "초기화",
    "앱 삭제",
)


def ai_candidate_is_safe(candidate):
    if candidate.get("action") not in {"tap", "swipe"}:
        return False
    label = str(candidate.get("label") or "").casefold()
    if any(keyword.casefold() in label for keyword in AI_RISKY_LABELS):
        return False
    if label == "(no label)" and candidate.get("source") not in {
        "functional-memory",
        "nonfunctional-memory",
    }:
        return False
    return True


def load_ai_random_history(limit=AI_HISTORY_LIMIT):
    if not AI_HISTORY_FILE.exists():
        return []
    try:
        lines = AI_HISTORY_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    history: list[dict[str, Any]] = []
    for line in lines[-max(1, limit):]:
        try:
            item = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(item, dict):
            history.append(item)
    return history


def append_ai_random_history(record):
    QA_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _ai_history_lock:
        with AI_HISTORY_FILE.open("a", encoding="utf-8") as handle:
            handle.write(line)


def ai_uploaded_test_context(limit=40):
    items: list[dict[str, str]] = []
    for step in _test_steps[:limit]:
        items.append(
            {
                "action": str(step.get("action") or ""),
                "target": str(step.get("target") or "")[:160],
                "description": str(step.get("description") or "")[:240],
                "expected": str(step.get("expected") or "")[:160],
            }
        )
    return items


def extract_openai_output_text(response_data):
    direct = response_data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    parts: list[str] = []
    for output_item in response_data.get("output") or []:
        if not isinstance(output_item, dict):
            continue
        for content_item in output_item.get("content") or []:
            if not isinstance(content_item, dict):
                continue
            text = content_item.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    return "\n".join(parts).strip()


def parse_openai_json(text):
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("AI response must be a JSON object.")
    return data


def request_openai_json(instructions, context, max_output_tokens=2400, image_png=None):
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    content = [
        {
            "type": "input_text",
            "text": json.dumps(context, ensure_ascii=False, separators=(",", ":")),
        }
    ]
    if image_png:
        encoded = base64.b64encode(image_png).decode("ascii")
        content.append(
            {
                "type": "input_image",
                "image_url": f"data:image/png;base64,{encoded}",
            }
        )

    payload = {
        "model": OPENAI_MODEL,
        "instructions": instructions,
        "input": [{"role": "user", "content": content}],
        "max_output_tokens": max_output_tokens,
    }
    http_request = urllib.request.Request(
        OPENAI_API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(http_request, timeout=AI_REQUEST_TIMEOUT_S) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        message = f"OpenAI API returned HTTP {exc.code}"
        try:
            error_data = json.loads(exc.read().decode("utf-8", errors="ignore"))
            detail = str((error_data.get("error") or {}).get("message") or "").strip()
            if detail:
                message = f"{message}: {detail[:300]}"
        except (TypeError, ValueError):
            pass
        raise RuntimeError(message) from exc
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"OpenAI API request failed: {exc}") from exc

    output_text = extract_openai_output_text(response_data)
    if not output_text:
        raise RuntimeError("OpenAI API returned no JSON output")
    return parse_openai_json(output_text), response_data.get("usage") or {}


def parse_ai_plan_json(text):
    data = parse_openai_json(text)
    if not isinstance(data.get("steps"), list):
        raise ValueError("AI planner response does not contain a steps list.")
    return data


def request_ai_random_plan(context):
    instructions = (
        "You are a cautious Android QA planner. Select only candidate_id values provided by the user. "
        "Plan the requested number of actions, favoring feature coverage, unseen paths, prior failures, "
        "uploaded test-case intent, and memory actions. Never invent coordinates or actions. Avoid purchases, "
        "account deletion, uninstall, factory reset, system settings, permissions, and other destructive flows. "
        "Return only a JSON object with summary and steps. Each step must have candidate_id and a short reason."
    )
    data, usage = request_openai_json(instructions, context, max_output_tokens=2400)
    if not isinstance(data.get("steps"), list):
        raise ValueError("AI planner response does not contain a steps list.")
    return data, usage


def build_ai_random_plan(max_steps=10, memory_actions=None):
    combined_candidates = collect_random_candidates(memory_actions)
    safe_candidates = [candidate for candidate in combined_candidates if ai_candidate_is_safe(candidate)]
    if not safe_candidates:
        raise RuntimeError("No safe UI candidates found for AI planning")

    candidate_context = []
    for candidate in safe_candidates[:100]:
        candidate_context.append(
            {
                "candidate_id": candidate["candidate_id"],
                "label": str(candidate.get("label") or "")[:160],
                "action": candidate.get("action"),
                "source": candidate.get("source"),
                "bounds": candidate.get("bounds"),
                "score": candidate.get("score"),
                "selected": bool(candidate.get("selected")),
            }
        )

    context = {
        "requested_steps": max_steps,
        "candidates": candidate_context,
        "uploaded_test_cases": ai_uploaded_test_context(),
        "recent_runs": load_ai_random_history(),
        "response_schema": {
            "summary": "short planning summary",
            "steps": [{"candidate_id": "c001", "reason": "short reason"}],
        },
    }
    ai_response, usage = request_ai_random_plan(context)
    candidate_map = {candidate["candidate_id"]: candidate for candidate in safe_candidates}
    selected: list[tuple[dict[str, Any], str]] = []
    for item in ai_response.get("steps") or []:
        if not isinstance(item, dict):
            continue
        candidate = candidate_map.get(str(item.get("candidate_id") or ""))
        if not candidate:
            continue
        selected.append((candidate, str(item.get("reason") or "AI selected candidate")[:200]))
        if len(selected) >= max_steps:
            break
    if not selected:
        raise RuntimeError("AI planner did not select any valid candidates")

    planned: list[dict[str, Any]] = []
    for index in range(max_steps):
        candidate, reason = selected[index % len(selected)]
        planned_candidate = dict(candidate)
        planned_candidate["original_source"] = candidate.get("source", "ui-analysis")
        planned_candidate["source"] = "ai-planner"
        planned_candidate["ai_reason"] = reason
        step = dict(candidate["step"])
        step["description"] = f"ai-planner: {candidate['label']}"
        planned.append(
            {
                "index": index + 1,
                "candidate": planned_candidate,
                "step": step,
                "risk": "medium",
                "verify": f"ai-planner ({planned_candidate['original_source']}) + screen-change + error scan",
            }
        )
    return planned, {
        "model": OPENAI_MODEL,
        "summary": str(ai_response.get("summary") or "AI plan created")[:400],
        "candidate_count": len(safe_candidates),
        "usage": usage,
        "fallback": False,
    }


AI_TEST_ALLOWED_ACTIONS = {"tap", "swipe", "assert_text", "wait", "input"}
AI_TEST_RISKY_TEXT = (
    *AI_RISKY_LABELS,
    "delete",
    "reset",
    "system settings",
    "permission",
    "offline",
    "wi-fi",
    "wifi",
    "force stop",
    "power button",
    "삭제",
    "설정 변경",
    "권한",
    "오프라인",
    "강제종료",
    "전원 버튼",
)


def compact_test_step(index, step):
    return {
        "index": index,
        "row": step.get("row", ""),
        "case_no": str(step.get("case_no") or "")[:80],
        "source": str(step.get("source") or "testcase"),
        "action": str(step.get("action") or ""),
        "target": str(step.get("target") or "")[:160],
        "value": str(step.get("value") or "")[:120],
        "expected": str(step.get("expected") or step.get("qa_expected") or "")[:220],
        "description": str(step.get("description") or "")[:280],
        "block_reason": str(step.get("block_reason") or "")[:180],
    }


def ai_test_step_is_risky(step):
    text = " ".join(
        str(step.get(key) or "")
        for key in ("target", "value", "expected", "qa_expected", "description", "block_reason")
    ).casefold()
    return any(keyword.casefold() in text for keyword in AI_TEST_RISKY_TEXT)


def load_ai_test_history(file_hash, limit=AI_HISTORY_LIMIT):
    if not file_hash or not AI_TEST_HISTORY_FILE.exists():
        return []
    try:
        lines = AI_TEST_HISTORY_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    history: list[dict[str, Any]] = []
    for line in reversed(lines):
        try:
            item = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(item, dict) and item.get("file_hash") == file_hash:
            history.append(item)
            if len(history) >= max(1, limit):
                break
    history.reverse()
    return history


def append_ai_test_history(record):
    QA_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _ai_history_lock:
        with AI_TEST_HISTORY_FILE.open("a", encoding="utf-8") as handle:
            handle.write(line)


def compact_ai_test_history(file_hash, limit=5):
    compact: list[dict[str, Any]] = []
    for run in load_ai_test_history(file_hash, limit=limit):
        issues = []
        for result in run.get("results") or []:
            if result.get("status") in {"failed", "blocked"}:
                issues.append(
                    {
                        "index": result.get("index"),
                        "status": result.get("status"),
                        "message": str(result.get("message") or "")[:240],
                    }
                )
            if len(issues) >= 20:
                break
        compact.append(
            {
                "created_at": run.get("created_at"),
                "summary": str(run.get("summary") or "")[:300],
                "passed": run.get("passed", 0),
                "failed": run.get("failed", 0),
                "blocked": run.get("blocked", 0),
                "ai_assisted": run.get("ai_assisted", 0),
                "fallback": bool(run.get("fallback")),
                "issues": issues,
            }
        )
    return compact


def emit_test_ai_status(state, message, **extra):
    payload = {"state": state, "message": message, "model": OPENAI_MODEL, **extra}
    socketio.emit("test_ai_status", payload, namespace="/mirror")


def request_ai_test_overview(steps, metadata):
    step_context = [compact_test_step(index, step) for index, step in enumerate(steps, start=1)]
    context = {
        "file": metadata,
        "total_steps": len(steps),
        "steps": step_context,
        "recent_runs": compact_ai_test_history(str(metadata.get("file_hash") or ""), limit=5),
        "response_schema": {
            "summary": "short test-suite purpose and flow",
            "risks": ["short risk"],
            "execution_notes": ["short note"],
        },
    }
    instructions = (
        "You are an Android QA test-suite analyst. Analyze the uploaded cases without changing their order. "
        "Identify the application flow, prerequisites, and unsafe cases. Do not propose purchases, deletion, "
        "account changes, app removal, system settings, permissions, or device environment changes. "
        "Return only JSON matching response_schema. Keep the summary and lists concise."
    )
    data, usage = request_openai_json(instructions, context, max_output_tokens=1800)
    return {
        "summary": str(data.get("summary") or "AI Test Case analysis ready")[:500],
        "risks": [str(value)[:200] for value in (data.get("risks") or [])[:20]],
        "execution_notes": [str(value)[:200] for value in (data.get("execution_notes") or [])[:20]],
        "usage": usage,
    }


def request_ai_test_batch_hints(steps, start_index, overview, metadata):
    batch_context = [
        compact_test_step(start_index + offset, step)
        for offset, step in enumerate(steps)
    ]
    context = {
        "file": metadata,
        "suite_summary": overview.get("summary", ""),
        "steps": batch_context,
        "response_schema": {
            "hints": [
                {
                    "index": start_index,
                    "intent": "short intent",
                    "target_candidates": ["visible label"],
                    "expected_candidates": ["expected visible text"],
                    "risk": "safe or blocked",
                    "reason": "short reason",
                }
            ]
        },
    }
    instructions = (
        "You are preparing execution hints for Android QA cases. Preserve every Excel index and action. "
        "Extract concise visible labels that UIAutomator or OCR can match. Do not invent coordinates, input values, "
        "or additional actions. Mark destructive, account, payment, deletion, app removal, permission, system-setting, "
        "offline, or device-control cases as blocked. Return only JSON matching response_schema."
    )
    data, usage = request_openai_json(instructions, context, max_output_tokens=4200)
    hints: dict[int, dict[str, Any]] = {}
    valid_indexes = set(range(start_index, start_index + len(steps)))
    for item in data.get("hints") or []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if index not in valid_indexes:
            continue
        hints[index] = {
            "intent": str(item.get("intent") or "")[:200],
            "target_candidates": [str(value).strip()[:100] for value in (item.get("target_candidates") or [])[:8] if str(value or "").strip()],
            "expected_candidates": [str(value).strip()[:100] for value in (item.get("expected_candidates") or [])[:8] if str(value or "").strip()],
            "risk": str(item.get("risk") or "safe").strip().lower(),
            "reason": str(item.get("reason") or "")[:240],
        }
    return hints, usage


def apply_ai_test_hint(step, hint):
    enriched = dict(step)
    if not hint:
        return enriched
    enriched["ai_hint"] = hint
    enriched["ai_target_candidates"] = list(hint.get("target_candidates") or [])
    enriched["ai_expected_candidates"] = list(hint.get("expected_candidates") or [])
    return enriched


def ai_test_screen_candidates():
    candidates = [candidate for candidate in collect_random_candidates([]) if ai_candidate_is_safe(candidate)]
    candidate_context = []
    for candidate in candidates[:100]:
        candidate_context.append(
            {
                "candidate_id": candidate.get("candidate_id"),
                "label": str(candidate.get("label") or "")[:160],
                "action": candidate.get("action"),
                "source": candidate.get("source"),
                "bounds": candidate.get("bounds"),
                "score": candidate.get("score"),
            }
        )
    return candidates[:100], candidate_context


def request_ai_test_recovery(index, step, original_error, overview, metadata):
    candidates, candidate_context = ai_test_screen_candidates()
    if not candidates:
        raise RuntimeError("No safe current-screen candidates found for AI recovery")
    screenshot = capture_screen_png()
    context = {
        "file": metadata,
        "suite_summary": overview.get("summary", ""),
        "failed_step": compact_test_step(index, step),
        "original_error": str(original_error)[:500],
        "screen_candidates": candidate_context,
        "recent_runs": compact_ai_test_history(str(metadata.get("file_hash") or ""), limit=3),
        "response_schema": {
            "decision": "execute, blocked, or fail",
            "action": "tap, swipe, assert_text, wait, or input",
            "candidate_id": "c001 or blank",
            "wait_ms": 1000,
            "reason": "short reason",
        },
    }
    instructions = (
        "You are recovering one failed Android QA step using the attached current screenshot and the supplied "
        "UIAutomator/OCR candidates. Select only an existing candidate_id; never invent coordinates. Input is allowed "
        "only when failed_step.value is non-empty. Return blocked for destructive, payment, deletion, account, app "
        "removal, permission, system-setting, offline, or device-control operations. Otherwise return execute or fail. "
        "Return only JSON matching response_schema."
    )
    data, usage = request_openai_json(
        instructions,
        context,
        max_output_tokens=1400,
        image_png=screenshot,
    )
    decision = str(data.get("decision") or "fail").strip().lower()
    action = str(data.get("action") or "").strip().lower()
    candidate_id = str(data.get("candidate_id") or "").strip()
    candidate_map = {str(candidate.get("candidate_id")): candidate for candidate in candidates}
    candidate = candidate_map.get(candidate_id)
    try:
        wait_ms = max(100, min(int_value(data.get("wait_ms"), 1000), 5000))
    except (TypeError, ValueError):
        wait_ms = 1000
    return {
        "decision": decision,
        "action": action,
        "candidate": candidate,
        "candidate_id": candidate_id,
        "wait_ms": wait_ms,
        "reason": str(data.get("reason") or "AI recovery")[:300],
        "usage": usage,
    }


def ai_test_expected_candidates(step):
    values: list[str] = []
    for key in ("ai_expected_candidates", "assert_candidates"):
        source_values = step.get(key) or []
        if isinstance(source_values, list):
            values.extend(str(value).strip() for value in source_values if str(value or "").strip())
    expected = str(step.get("expected") or step.get("qa_expected") or "").strip()
    if expected and len(expected) <= 120:
        values.append(expected)
    return list(dict.fromkeys(values))[:12]


def execute_ai_test_candidate(candidate):
    action = candidate.get("action")
    before_hash = capture_screen_hash()
    if action == "tap":
        args = ("shell", "input", "tap", str(candidate["x"]), str(candidate["y"]))
        message = f"Tapped '{candidate.get('label', '')}' at ({candidate['x']}, {candidate['y']})"
    elif action == "swipe":
        step = candidate.get("step") or {}
        args = (
            "shell",
            "input",
            "swipe",
            str(step["x1"]),
            str(step["y1"]),
            str(step["x2"]),
            str(step["y2"]),
            str(step.get("duration_ms", 450)),
        )
        message = f"Swiped '{candidate.get('label', '')}'"
    else:
        raise ValueError("AI candidate action is not executable")
    with _adb_input_lock:
        _, err, rc = adb(*args)
    if rc:
        raise RuntimeError(err.decode(errors="ignore") or "AI-assisted ADB input failed")
    changed = wait_for_screen_change(before_hash, timeout_s=4.0, stop_event=_test_stop_event)
    return f"{message}; {'screen changed' if changed else 'input sent'}"


def execute_ai_test_recovery(step, recovery):
    decision = recovery.get("decision")
    reason = recovery.get("reason") or "AI recovery"
    if decision == "blocked":
        raise BlockedStep(f"AI blocked: {reason}")
    if decision != "execute":
        raise RuntimeError(f"AI could not recover this step: {reason}")

    action = recovery.get("action")
    if action not in AI_TEST_ALLOWED_ACTIONS:
        raise RuntimeError(f"AI returned unsupported action: {action or 'blank'}")
    candidate = recovery.get("candidate")

    if action == "assert_text":
        if not candidate:
            raise RuntimeError("AI assert_text did not select a current-screen candidate")
        result = f"AI verified visible text '{candidate.get('label', '')}'"
    elif action in {"tap", "swipe"}:
        if not candidate or candidate.get("action") != action:
            raise RuntimeError("AI selected candidate does not match the requested action")
        result = execute_ai_test_candidate(candidate)
    elif action == "input":
        value = str(step.get("value") or "").strip()
        if not value:
            raise RuntimeError("AI input recovery requires an Excel input value")
        if not candidate or candidate.get("action") != "tap":
            raise RuntimeError("AI input recovery requires a tappable field candidate")
        with _adb_input_lock:
            _, tap_err, tap_rc = adb("shell", "input", "tap", str(candidate["x"]), str(candidate["y"]))
            if tap_rc:
                raise RuntimeError(tap_err.decode(errors="ignore") or "AI input focus tap failed")
            _, input_err, input_rc = adb("shell", "input", "text", adb_text_arg(value))
        if input_rc:
            raise RuntimeError(input_err.decode(errors="ignore") or "AI input failed")
        result = f"AI entered Excel value into '{candidate.get('label', '')}'"
    else:
        deadline = time.time() + (recovery.get("wait_ms", 1000) / 1000)
        while time.time() < deadline:
            if _test_stop_event.is_set():
                raise RuntimeError("Test Case stopped")
            time.sleep(min(0.1, max(0, deadline - time.time())))
        result = execute_step(step)

    expected_candidates = ai_test_expected_candidates(step)
    if action in {"tap", "swipe", "input"} and expected_candidates:
        matched = wait_until_any_ui_contains(expected_candidates, timeout_s=3.0)
        if not matched:
            raise AssertionError("AI action completed but expected text was not found")
        result = f"{result}; found '{matched}'"
    return f"ai-assisted: {reason}; {result}"


def detect_runtime_errors():
    findings: list[str] = []
    xml = dump_ui_xml()
    for pattern in ERROR_TEXT_PATTERNS:
        if pattern in xml:
            findings.append(f"UI text matched: {pattern}")

    out, _, rc = adb("logcat", "-d", "-t", "350")
    if rc == 0:
        log_text = out.decode("utf-8", errors="ignore")
        for pattern in ERROR_TEXT_PATTERNS[:7]:
            if pattern in log_text:
                findings.append(f"logcat matched: {pattern}")

    unique: list[str] = []
    for finding in findings:
        if finding not in unique:
            unique.append(finding)
    return unique


def emit_random_started(total, mode):
    socketio.emit(
        "random_run_started",
        {"total": total, "mode": mode, "started_at": time.strftime("%H:%M:%S")},
        namespace="/mirror",
    )


def emit_random_progress(index, total, status, step, message="", extra=None):
    payload = {
        "index": index,
        "total": total,
        "status": status,
        "step": step,
        "message": message,
        "elapsed_ms": 0,
        "extra": extra or {},
    }
    socketio.emit("random_progress", payload, namespace="/mirror")


def payload_int(payload, key, default=None, minimum=None, maximum=None):
    if key not in payload or payload.get(key) in ("", None):
        if default is None:
            raise ValueError(f"Missing {key}")
        value = default
    else:
        value = int_value(payload.get(key))

    if minimum is not None and value < minimum:
        raise ValueError(f"{key} is below {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{key} is above {maximum}")
    return value


def validate_input_point(payload, x_key="x", y_key="y"):
    width = payload_int(payload, "screen_width", 10000, minimum=1, maximum=10000)
    height = payload_int(payload, "screen_height", 10000, minimum=1, maximum=10000)
    x = payload_int(payload, x_key, minimum=0, maximum=width - 1)
    y = payload_int(payload, y_key, minimum=0, maximum=height - 1)
    return x, y


def run_manual_input(payload):
    input_type = str(payload.get("type") or "").strip().lower()
    if input_type not in {"tap", "swipe", "long_press", "keyevent"}:
        raise ValueError("Unsupported input type.")

    if not check_device():
        raise RuntimeError("No adb device. Check USB connection and USB debugging.")

    if input_type == "tap":
        x, y = validate_input_point(payload)
        args = ("shell", "input", "tap", str(x), str(y))
        message = f"tap ({x}, {y})"
    elif input_type == "swipe":
        x1, y1 = validate_input_point(payload, "x1", "y1")
        x2, y2 = validate_input_point(payload, "x2", "y2")
        duration = payload_int(payload, "duration_ms", 350, minimum=50, maximum=10000)
        args = ("shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration))
        message = f"swipe ({x1}, {y1}) -> ({x2}, {y2})"
    elif input_type == "long_press":
        x, y = validate_input_point(payload)
        duration = payload_int(payload, "duration_ms", 700, minimum=600, maximum=10000)
        args = ("shell", "input", "swipe", str(x), str(y), str(x), str(y), str(duration))
        message = f"long press ({x}, {y})"
    else:
        key = payload_int(payload, "key", minimum=0, maximum=300)
        args = ("shell", "input", "keyevent", str(key))
        message = f"keyevent {key}"

    with _adb_input_lock:
        _, err, rc = adb(*args)
    if rc:
        raise RuntimeError(err.decode(errors="ignore") or "ADB input failed")
    return {"status": "ok", "input": input_type, "message": message}


def recordings_dir():
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    return RECORDINGS_DIR


def safe_recording_id(value):
    recording_id = str(value or "").strip()
    if not re.fullmatch(r"recording-\d{8}-\d{6}(?:-[a-z0-9]+)?", recording_id):
        raise ValueError("Invalid recording id.")
    return recording_id


def recording_path(recording_id):
    return recordings_dir() / f"{safe_recording_id(recording_id)}.json"


def recording_export_path(recording_id):
    return recordings_dir() / f"{safe_recording_id(recording_id)}.xlsx"


def read_recording(recording_id):
    path = recording_path(recording_id)
    if not path.exists():
        raise FileNotFoundError("Recording not found.")
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def list_recordings():
    items = []
    for path in sorted(recordings_dir().glob("recording-*.json"), reverse=True):
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            continue
        items.append(
            {
                "id": data.get("id") or path.stem,
                "name": data.get("name") or path.stem,
                "created_at": data.get("created_at") or "",
                "screen_width": data.get("screen_width") or 0,
                "screen_height": data.get("screen_height") or 0,
                "count": len(data.get("actions") or []),
            }
        )
    return items


def normalize_recording_payload(payload):
    actions = payload.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError("Recording needs at least one action.")

    width = payload_int(payload, "screen_width", minimum=1, maximum=10000)
    height = payload_int(payload, "screen_height", minimum=1, maximum=10000)
    now = datetime.now().strftime("%Y%m%d-%H%M%S")
    recording_id = f"recording-{now}"
    name = str(payload.get("name") or recording_id).strip()[:80] or recording_id

    normalized_actions = []
    for index, action in enumerate(actions, start=1):
        action_type = str(action.get("type") or "").strip().lower()
        if action_type not in {"tap", "swipe"}:
            raise ValueError(f"Unsupported recording action at {index}.")

        common = {
            "type": action_type,
            "delay_ms": payload_int(action, "delay_ms", 0, minimum=0, maximum=600000),
            "screen_width": width,
            "screen_height": height,
        }
        if action_type == "tap":
            x, y = validate_input_point({**action, "screen_width": width, "screen_height": height})
            common.update({"x": x, "y": y})
        else:
            x1, y1 = validate_input_point({**action, "screen_width": width, "screen_height": height}, "x1", "y1")
            x2, y2 = validate_input_point({**action, "screen_width": width, "screen_height": height}, "x2", "y2")
            duration = payload_int(action, "duration_ms", 350, minimum=50, maximum=10000)
            common.update({"x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration_ms": duration})
        normalized_actions.append(common)

    return {
        "id": recording_id,
        "name": name,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "screen_width": width,
        "screen_height": height,
        "actions": normalized_actions,
    }


def write_recording(payload):
    data = normalize_recording_payload(payload)
    path = recording_path(data["id"])
    if path.exists():
        data["id"] = f"{data['id']}-{random.randint(1000, 9999)}"
    path = recording_path(data["id"])
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    return data


def export_recording_excel(recording):
    try:
        from openpyxl import Workbook
    except ImportError as exc:
        raise RuntimeError("openpyxl is not installed. Run setup_and_run.bat again.") from exc

    path = recording_export_path(recording["id"])
    wb = Workbook()
    ws = wb.active
    ws.title = "Recording"
    ws.append(["index", "type", "x", "y", "x1", "y1", "x2", "y2", "delay_ms", "duration_ms", "screen_width", "screen_height"])
    for index, action in enumerate(recording.get("actions") or [], start=1):
        ws.append(
            [
                index,
                action.get("type", ""),
                action.get("x", ""),
                action.get("y", ""),
                action.get("x1", ""),
                action.get("y1", ""),
                action.get("x2", ""),
                action.get("y2", ""),
                action.get("delay_ms", 0),
                action.get("duration_ms", ""),
                action.get("screen_width", recording.get("screen_width", "")),
                action.get("screen_height", recording.get("screen_height", "")),
            ]
        )
    wb.save(path)
    return path


def emit_record_progress(index, total, status, action, message="", extra=None):
    payload = {
        "index": index,
        "total": total,
        "status": status,
        "step": {"action": action.get("type", "record"), "description": "Recorder Playback", "source": "recorder"},
        "message": message,
        "elapsed_ms": 0,
        "extra": extra or {},
    }
    socketio.emit("test_progress", payload, namespace="/mirror")


def sleep_with_stop(delay_ms):
    if delay_ms <= 0:
        return False
    deadline = time.time() + (delay_ms / 1000)
    while time.time() < deadline:
        if _record_play_stop_event.is_set():
            return True
        time.sleep(min(0.1, max(0, deadline - time.time())))
    return _record_play_stop_event.is_set()


def play_recording_steps(recording, repeat_count=1):
    global _record_play_running

    actions = list(recording.get("actions") or [])
    total = len(actions) * repeat_count
    passed = 0
    failed = 0
    stopped = False

    socketio.emit(
        "test_run_started",
        {"total": total, "started_at": time.strftime("%H:%M:%S")},
        namespace="/mirror",
    )

    try:
        if not actions:
            raise RuntimeError("Recording has no actions.")

        for loop_index in range(1, repeat_count + 1):
            for action_index, action in enumerate(actions, start=1):
                progress_index = ((loop_index - 1) * len(actions)) + action_index
                loop_label = f"Loop {loop_index}/{repeat_count}"

                if _record_play_stop_event.is_set():
                    emit_record_progress(
                        progress_index,
                        total,
                        "failed",
                        action,
                        f"{loop_label} - Playback stopped",
                        failure_extra("recorder", progress_index),
                    )
                    failed += 1
                    stopped = True
                    break

                delay_ms = int(action.get("delay_ms") or 0)
                if sleep_with_stop(delay_ms):
                    emit_record_progress(
                        progress_index,
                        total,
                        "failed",
                        action,
                        f"{loop_label} - Playback stopped",
                        failure_extra("recorder", progress_index),
                    )
                    failed += 1
                    stopped = True
                    break

                emit_record_progress(progress_index, total, "running", action, f"{loop_label} - Recorder playback")
                try:
                    payload = dict(action)
                    result = run_manual_input(payload)
                    passed += 1
                    emit_record_progress(
                        progress_index,
                        total,
                        "passed",
                        action,
                        f"{loop_label} - {result.get('message', 'sent')}",
                    )
                except Exception as exc:
                    failed += 1
                    emit_record_progress(
                        progress_index,
                        total,
                        "failed",
                        action,
                        f"{loop_label} - {exc}",
                        failure_extra("recorder", progress_index),
                    )
            if stopped:
                break
    except Exception as exc:
        socketio.emit("test_error", {"msg": str(exc)}, namespace="/mirror")
    finally:
        with _record_play_lock:
            _record_play_running = False
            _record_play_stop_event.clear()
        socketio.emit(
            "test_run_finished",
            {
                "total": total,
                "passed": passed,
                "failed": failed,
                "blocked": 0,
                "finished_at": time.strftime("%H:%M:%S"),
            },
            namespace="/mirror",
        )


def execute_random_action(plan_item):
    step = plan_item["step"]
    before_hash = capture_screen_hash()
    if random_stop_requested():
        raise RandomStopped("Random run stopped")

    if step["action"] == "swipe":
        _, err, rc = adb(
            "shell",
            "input",
            "swipe",
            str(step["x1"]),
            str(step["y1"]),
            str(step["x2"]),
            str(step["y2"]),
            str(step.get("duration_ms", 450)),
        )
        action_message = f"Swiped {step['target']}"
    else:
        _, err, rc = adb("shell", "input", "tap", str(step["x"]), str(step["y"]))
        action_message = f"Tapped {step['target']} at ({step['x']}, {step['y']})"

    if rc:
        raise RuntimeError(err.decode(errors="ignore") or "ADB input failed")

    if random_stop_requested():
        raise RandomStopped("Random run stopped")

    changed = wait_for_screen_change(before_hash, timeout_s=4.0, stop_event=_random_stop_event)
    if random_stop_requested():
        raise RandomStopped("Random run stopped")

    errors = detect_runtime_errors()
    if errors:
        raise AssertionError("; ".join(errors))

    if not changed:
        return f"{action_message}; no visible screen change"
    return f"{action_message}; screen changed"


def random_stop_requested():
    return _random_stop_event.is_set()


def random_sleep_with_stop(delay_ms):
    if delay_ms <= 0:
        return False
    deadline = time.time() + (delay_ms / 1000)
    while time.time() < deadline:
        if random_stop_requested():
            return True
        time.sleep(min(0.1, max(0, deadline - time.time())))
    return random_stop_requested()


def run_random_exploration(max_steps=20, delay_ms=800, dry_run=False, memory_actions=None, ai_mode=False):
    global _random_running, _random_last_plan

    passed = 0
    failed = 0
    stopped = 0
    total = max_steps
    mode = "ai" if ai_mode else ("dry-run" if dry_run else "live")
    run_id = uuid.uuid4().hex
    ai_meta: dict[str, Any] = {
        "model": OPENAI_MODEL,
        "summary": "",
        "fallback": False,
    }
    run_results: list[dict[str, Any]] = []

    try:
        if not check_device():
            raise RuntimeError("No adb device. Check USB connection and USB debugging.")

        emit_random_started(total, mode)
        if ai_mode:
            socketio.emit(
                "random_ai_status",
                {"state": "planning", "message": f"AI planning {total} steps with {OPENAI_MODEL}"},
                namespace="/mirror",
            )
            try:
                plan, ai_meta = build_ai_random_plan(
                    max_steps=max_steps,
                    memory_actions=memory_actions or [],
                )
                socketio.emit(
                    "random_ai_status",
                    {"state": "ready", "message": ai_meta["summary"], "model": OPENAI_MODEL},
                    namespace="/mirror",
                )
            except Exception as exc:
                ai_meta = {
                    "model": OPENAI_MODEL,
                    "summary": str(exc),
                    "fallback": True,
                }
                socketio.emit(
                    "random_ai_status",
                    {
                        "state": "fallback",
                        "message": f"AI unavailable; using Live Run fallback: {exc}",
                        "model": OPENAI_MODEL,
                    },
                    namespace="/mirror",
                )
                plan = build_random_plan(max_steps=max_steps, memory_actions=memory_actions or [])
                for plan_item in plan:
                    candidate = plan_item["candidate"]
                    candidate["original_source"] = candidate.get("source", "ui-analysis")
                    candidate["source"] = "ai-fallback"
                    plan_item["verify"] = "ai-fallback + screen-change + error scan"
        else:
            plan = build_random_plan(max_steps=max_steps, memory_actions=memory_actions or [])
        _random_last_plan = plan

        if not plan:
            raise RuntimeError("No clickable or scrollable UI candidates found.")

        if not dry_run:
            adb("logcat", "-c")

        for plan_item in plan:
            if random_stop_requested():
                break

            index = plan_item["index"]
            step = plan_item["step"]
            emit_random_progress(
                index,
                total,
                "running",
                step,
                f"{mode}: {plan_item['verify']}",
                {"candidate": plan_item["candidate"], "risk": plan_item["risk"]},
            )

            try:
                if random_stop_requested():
                    raise RandomStopped("Random run stopped")

                if dry_run:
                    message = (
                        f"Would {step['action']} '{step['target']}' "
                        f"at {plan_item['candidate']['bounds']}"
                    )
                else:
                    message = execute_random_action(plan_item)
                    if random_sleep_with_stop(delay_ms):
                        raise RandomStopped("Random run stopped")

                passed += 1
                run_results.append(
                    {
                        "index": index,
                        "status": "passed",
                        "action": step.get("action"),
                        "label": plan_item["candidate"].get("label"),
                        "source": plan_item["candidate"].get("source"),
                        "reason": plan_item["candidate"].get("ai_reason", ""),
                    }
                )
                emit_random_progress(
                    index,
                    total,
                    "passed",
                    step,
                    f"{plan_item['candidate'].get('source', 'ui-analysis')}: {message}",
                    {"candidate": plan_item["candidate"], "risk": plan_item["risk"]},
                )
            except RandomStopped as exc:
                stopped += 1
                run_results.append(
                    {
                        "index": index,
                        "status": "stopped",
                        "action": step.get("action"),
                        "label": plan_item["candidate"].get("label"),
                        "source": plan_item["candidate"].get("source"),
                        "message": str(exc)[:300],
                    }
                )
                emit_random_progress(
                    index,
                    total,
                    "stopped",
                    step,
                    f"{plan_item['candidate'].get('source', 'ui-analysis')}: {exc}",
                    {"candidate": plan_item["candidate"], "risk": plan_item["risk"]},
                )
                break
            except Exception as exc:
                failed += 1
                run_results.append(
                    {
                        "index": index,
                        "status": "failed",
                        "action": step.get("action"),
                        "label": plan_item["candidate"].get("label"),
                        "source": plan_item["candidate"].get("source"),
                        "reason": plan_item["candidate"].get("ai_reason", ""),
                        "message": str(exc)[:300],
                    }
                )
                extra = failure_extra(
                    "random",
                    index,
                    {"candidate": plan_item["candidate"], "risk": plan_item["risk"]},
                )
                emit_random_progress(
                    index,
                    total,
                    "failed",
                    step,
                    f"{plan_item['candidate'].get('source', 'ui-analysis')}: {exc}",
                    extra,
                )
    except Exception as exc:
        socketio.emit("random_error", {"msg": str(exc)}, namespace="/mirror")
    finally:
        if ai_mode:
            try:
                append_ai_random_history(
                    {
                        "run_id": run_id,
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                        "model": ai_meta.get("model", OPENAI_MODEL),
                        "summary": ai_meta.get("summary", ""),
                        "fallback": bool(ai_meta.get("fallback")),
                        "requested_steps": total,
                        "passed": passed,
                        "failed": failed,
                        "stopped": stopped,
                        "uploaded_test_cases": len(_test_steps),
                        "results": run_results,
                    }
                )
            except OSError:
                pass
        with _random_lock:
            _random_running = False
            _random_stop_event.clear()
        socketio.emit(
            "random_run_finished",
            {
                "total": total,
                "passed": passed,
                "failed": failed,
                "stopped": stopped,
                "mode": mode,
                "ai_model": ai_meta.get("model") if ai_mode else None,
                "ai_fallback": bool(ai_meta.get("fallback")) if ai_mode else False,
                "finished_at": time.strftime("%H:%M:%S"),
            },
            namespace="/mirror",
        )


def execute_step(step):
    action = step["action"]

    if action == "blocked":
        raise BlockedStep(step.get("block_reason") or "Blocked by safe automation policy.")

    if action == "tap":
        before_hash = capture_screen_hash()
        text_hit = find_ui_text_center(text_candidates_from_step(step))
        if text_hit:
            x, y = text_hit["x"], text_hit["y"]
            source = f"text '{text_hit['text']}'"
        else:
            x, y = parse_point(step)
            source = f"coordinate ({x}, {y})"

        _, err, rc = adb("shell", "input", "tap", str(x), str(y))
        if rc:
            raise RuntimeError(err.decode(errors="ignore") or "tap failed")

        changed = wait_for_screen_change(before_hash, timeout_s=5.0)
        if not changed:
            if step.get("source") == "recorder":
                return f"Tapped {source} at ({x}, {y}); recorder step sent"
            if step.get("source") in {"manual_qa", "wordp_testcase"}:
                matched = wait_until_any_ui_contains(text_candidates_from_step(step), timeout_s=1.5)
                if matched:
                    return f"Tapped {source} at ({x}, {y}); found '{matched}'"
            if text_hit and allows_unchanged_after_tap(step, text_hit):
                return f"Tapped {source} at ({x}, {y}); screen already on target"
            raise AssertionError(f"Screen did not change after tap: {source}")

        if step.get("source") == "wordp_testcase":
            candidates = text_candidates_from_step(step)
            if candidates:
                matched = wait_until_any_ui_contains(candidates, timeout_s=2.0)
                if not matched:
                    raise AssertionError(f"Expected text not found after tap: {', '.join(candidates[:3])}")
                return f"Tapped {source} at ({x}, {y}); found '{matched}'"

        description = str(step.get("description") or "")
        submit_keywords = ["submit", "quiz", "\uc81c\ucd9c", "\ud034\uc988"]
        if any(keyword in description.lower() for keyword in submit_keywords):
            confirm_hit = find_ui_text_center(["\ud655\uc778", "\ud648\uc73c\ub85c\uac00\uae30", "OK", "Ok", "ok"])
            if confirm_hit:
                before_confirm_hash = capture_screen_hash()
                _, err, rc = adb(
                    "shell",
                    "input",
                    "tap",
                    str(confirm_hit["x"]),
                    str(confirm_hit["y"]),
                )
                if rc:
                    raise RuntimeError(err.decode(errors="ignore") or "confirm tap failed")
                wait_for_screen_change(before_confirm_hash, timeout_s=5.0)
                return (
                    f"Tapped {source} at ({x}, {y}); "
                    f"confirmed '{confirm_hit['text']}' at ({confirm_hit['x']}, {confirm_hit['y']})"
                )

        return f"Tapped {source} at ({x}, {y}); screen changed"

    if action == "swipe":
        if step.get("source") == "wordp_testcase" and not any(step.get(key) not in ("", None) for key in ("x1", "y1", "x2", "y2")):
            current = current_device_resolution()
            if not current:
                raise RuntimeError("Could not read current device resolution for WordP swipe.")
            width, height = current
            x1 = x2 = width // 2
            y1 = int(height * 0.70)
            y2 = int(height * 0.35)
        else:
            x1, y1, x2, y2 = parse_swipe(step)
        duration = int_value(step.get("duration_ms"), 400)
        _, err, rc = adb("shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration))
        if rc:
            raise RuntimeError(err.decode(errors="ignore") or "swipe failed")
        return f"Swiped ({x1}, {y1}) -> ({x2}, {y2})"

    if action in {"input", "text"}:
        value = step.get("value") or step.get("target")
        if not value:
            raise ValueError("input action needs value.")
        _, err, rc = adb("shell", "input", "text", adb_text_arg(value))
        if rc:
            raise RuntimeError(err.decode(errors="ignore") or "input failed")
        return f"Typed text: {value}"

    if action == "keyevent":
        key = step.get("value") or step.get("target")
        if not key:
            raise ValueError("keyevent action needs value.")
        _, err, rc = adb("shell", "input", "keyevent", str(key))
        if rc:
            raise RuntimeError(err.decode(errors="ignore") or "keyevent failed")
        return f"Keyevent: {key}"

    if action == "wait":
        raw_wait = step.get("wait_ms") or step.get("value") or step.get("target") or 1000
        wait_ms = int_value(raw_wait, 1000)
        if wait_ms < 50:
            wait_ms *= 1000
        time.sleep(wait_ms / 1000)
        return f"Waited {wait_ms}ms"

    if action == "assert_text":
        expected = step.get("expected") or step.get("value") or step.get("target")
        if not expected:
            raise ValueError("assert_text action needs expected/value/target.")
        if step.get("source") in {"manual_qa", "wordp_testcase"}:
            matched = wait_until_any_ui_contains(text_candidates_from_step(step), timeout_s=5.0)
            if not matched:
                raise AssertionError(f"Text not found: {expected}")
            return f"Found text: {matched}"
        if not wait_until_ui_contains(expected, timeout_s=5.0, should_exist=True):
            raise AssertionError(f"Text not found: {expected}")
        return f"Found text: {expected}"

    if action == "assert_not_text":
        expected = step.get("expected") or step.get("value") or step.get("target")
        if not expected:
            raise ValueError("assert_not_text action needs expected/value/target.")
        if not wait_until_ui_contains(expected, timeout_s=5.0, should_exist=False):
            raise AssertionError(f"Unexpected text found: {expected}")
        return f"Text absent: {expected}"

    if action == "launch":
        package = step.get("target") or step.get("value")
        activity = step.get("value") if step.get("target") else ""
        if not package:
            raise ValueError("launch action needs target package.")
        if activity:
            _, err, rc = adb("shell", "am", "start", "-n", f"{package}/{activity}")
        else:
            _, err, rc = adb(
                "shell",
                "monkey",
                "-p",
                package,
                "-c",
                "android.intent.category.LAUNCHER",
                "1",
            )
        if rc:
            raise RuntimeError(err.decode(errors="ignore") or "launch failed")
        return f"Launched: {package}"

    if action == "back":
        adb("shell", "input", "keyevent", "4")
        return "Pressed Back"

    if action == "home":
        adb("shell", "input", "keyevent", "3")
        return "Pressed Home"

    if action == "screenshot":
        _, err, rc = adb("shell", "screencap", "-p", "/sdcard/testcase-last.png")
        if rc:
            raise RuntimeError(err.decode(errors="ignore") or "screenshot failed")
        return "Saved /sdcard/testcase-last.png"

    raise ValueError(f"Unsupported action: {action}")


def run_test_steps(ai_mode=False):
    global _test_running

    with _test_lock:
        steps = list(_test_steps)
        metadata = dict(_test_metadata)

    total = len(steps)
    passed = 0
    failed = 0
    blocked = 0
    ai_assisted = 0
    ai_fallback = False
    ai_active = ai_mode
    ai_overview: dict[str, Any] = {}
    ai_hints: dict[int, dict[str, Any]] = {}
    run_results: list[dict[str, Any]] = []
    run_id = uuid.uuid4().hex
    mode = "ai" if ai_mode else "standard"

    socketio.emit(
        "test_run_started",
        {"total": total, "mode": mode, "started_at": time.strftime("%H:%M:%S")},
        namespace="/mirror",
    )

    try:
        if not check_device():
            raise RuntimeError("No adb device. Check USB connection and USB debugging.")
        validate_recorder_resolution(steps)

        if ai_mode:
            emit_test_ai_status("analyzing", f"Analyzing {total} Test Case steps")
            try:
                ai_overview = request_ai_test_overview(steps, metadata)
                emit_test_ai_status("executing", ai_overview.get("summary") or "AI Test Case analysis ready")
            except Exception as exc:
                ai_active = False
                ai_fallback = True
                emit_test_ai_status("fallback", f"AI TC fallback to standard execution: {exc}")

        for index, step in enumerate(steps, start=1):
            if _test_stop_event.is_set():
                break

            if ai_active and (index - 1) % AI_TEST_BATCH_SIZE == 0:
                batch = steps[index - 1:index - 1 + AI_TEST_BATCH_SIZE]
                try:
                    batch_hints, _ = request_ai_test_batch_hints(
                        batch,
                        index,
                        ai_overview,
                        metadata,
                    )
                    ai_hints.update(batch_hints)
                    emit_test_ai_status(
                        "executing",
                        f"AI hints ready for steps {index}-{index + len(batch) - 1}",
                        batch_start=index,
                        batch_end=index + len(batch) - 1,
                    )
                except Exception as exc:
                    ai_active = False
                    ai_fallback = True
                    emit_test_ai_status("fallback", f"AI hint generation failed; continuing standard execution: {exc}")
                if _test_stop_event.is_set():
                    break

            active_step = apply_ai_test_hint(step, ai_hints.get(index)) if ai_active else dict(step)
            started = time.time()
            emit_progress(StepResult(index=index, total=total, status="running", step=active_step))

            ai_hint_blocked = str((active_step.get("ai_hint") or {}).get("risk") or "").lower() == "blocked"
            if ai_mode and (ai_test_step_is_risky(active_step) or ai_hint_blocked):
                elapsed_ms = int((time.time() - started) * 1000)
                hint_reason = str((active_step.get("ai_hint") or {}).get("reason") or "").strip()
                reason = f"Blocked by AI TC safety policy{f': {hint_reason}' if hint_reason else '.'}"
                blocked += 1
                run_results.append({"index": index, "status": "blocked", "message": reason, "ai_assisted": False})
                emit_progress(
                    StepResult(
                        index=index,
                        total=total,
                        status="blocked",
                        step=active_step,
                        message=reason,
                        elapsed_ms=elapsed_ms,
                    )
                )
                continue

            try:
                delay_ms = apply_recorder_delay(active_step)
                message = execute_step(active_step)
                if delay_ms:
                    message = f"Waited {delay_ms}ms; {message}"
                elapsed_ms = int((time.time() - started) * 1000)
                passed += 1
                run_results.append({"index": index, "status": "passed", "message": message, "ai_assisted": False})
                emit_progress(
                    StepResult(
                        index=index,
                        total=total,
                        status="passed",
                        step=active_step,
                        message=message,
                        elapsed_ms=elapsed_ms,
                    )
                )
            except BlockedStep as exc:
                original_error = str(exc)
                recovered = False
                recovery_error = ""
                if ai_active:
                    emit_test_ai_status("assisting", f"AI assisting Test Case step {index}", step=index)
                    try:
                        recovery = request_ai_test_recovery(index, active_step, original_error, ai_overview, metadata)
                    except Exception as ai_exc:
                        ai_active = False
                        ai_fallback = True
                        recovery_error = str(ai_exc)
                        emit_test_ai_status("fallback", f"AI recovery unavailable; continuing standard execution: {ai_exc}")
                    else:
                        if _test_stop_event.is_set():
                            break
                        try:
                            message = execute_ai_test_recovery(active_step, recovery)
                            recovered = True
                        except Exception as recovery_exc:
                            recovery_error = str(recovery_exc)

                elapsed_ms = int((time.time() - started) * 1000)
                if recovered:
                    passed += 1
                    ai_assisted += 1
                    run_results.append({"index": index, "status": "passed", "message": message, "ai_assisted": True})
                    emit_progress(
                        StepResult(
                            index=index,
                            total=total,
                            status="passed",
                            step=active_step,
                            message=message,
                            elapsed_ms=elapsed_ms,
                            extra={"ai_assisted": True, "ai_hint": active_step.get("ai_hint", {})},
                        )
                    )
                else:
                    blocked += 1
                    message = original_error if not recovery_error else f"{original_error}; AI: {recovery_error}"
                    run_results.append({"index": index, "status": "blocked", "message": message, "ai_assisted": False})
                    emit_progress(
                        StepResult(
                            index=index,
                            total=total,
                            status="blocked",
                            step=active_step,
                            message=message,
                            elapsed_ms=elapsed_ms,
                        )
                    )
            except Exception as exc:
                original_error = str(exc)
                recovered = False
                recovery_blocked = False
                recovery_error = ""
                if ai_active:
                    emit_test_ai_status("assisting", f"AI assisting Test Case step {index}", step=index)
                    try:
                        recovery = request_ai_test_recovery(index, active_step, original_error, ai_overview, metadata)
                    except Exception as ai_exc:
                        ai_active = False
                        ai_fallback = True
                        recovery_error = str(ai_exc)
                        emit_test_ai_status("fallback", f"AI recovery unavailable; continuing standard execution: {ai_exc}")
                    else:
                        if _test_stop_event.is_set():
                            break
                        try:
                            message = execute_ai_test_recovery(active_step, recovery)
                            recovered = True
                        except BlockedStep as recovery_exc:
                            recovery_blocked = True
                            recovery_error = str(recovery_exc)
                        except Exception as recovery_exc:
                            recovery_error = str(recovery_exc)

                elapsed_ms = int((time.time() - started) * 1000)
                if recovered:
                    passed += 1
                    ai_assisted += 1
                    run_results.append({"index": index, "status": "passed", "message": message, "ai_assisted": True})
                    emit_progress(
                        StepResult(
                            index=index,
                            total=total,
                            status="passed",
                            step=active_step,
                            message=message,
                            elapsed_ms=elapsed_ms,
                            extra={"ai_assisted": True, "ai_hint": active_step.get("ai_hint", {})},
                        )
                    )
                elif recovery_blocked:
                    blocked += 1
                    message = f"{original_error}; {recovery_error}"
                    run_results.append({"index": index, "status": "blocked", "message": message, "ai_assisted": False})
                    emit_progress(
                        StepResult(
                            index=index,
                            total=total,
                            status="blocked",
                            step=active_step,
                            message=message,
                            elapsed_ms=elapsed_ms,
                        )
                    )
                else:
                    failed += 1
                    message = original_error if not recovery_error else f"{original_error}; AI: {recovery_error}"
                    run_results.append({"index": index, "status": "failed", "message": message, "ai_assisted": False})
                    extra = failure_extra("testcase-ai" if ai_mode else "testcase", index)
                    if ai_mode:
                        extra.update({"ai_assisted": False, "ai_hint": active_step.get("ai_hint", {})})
                    emit_progress(
                        StepResult(
                            index=index,
                            total=total,
                            status="failed",
                            step=active_step,
                            message=message,
                            elapsed_ms=elapsed_ms,
                            extra=extra,
                        )
                    )
    except Exception as exc:
        socketio.emit("test_error", {"msg": str(exc)}, namespace="/mirror")
    finally:
        if ai_mode:
            try:
                append_ai_test_history(
                    {
                        "run_id": run_id,
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                        "file_hash": metadata.get("file_hash", ""),
                        "filename": metadata.get("filename", ""),
                        "source": metadata.get("source", ""),
                        "model": OPENAI_MODEL,
                        "summary": ai_overview.get("summary", ""),
                        "fallback": ai_fallback,
                        "total": total,
                        "passed": passed,
                        "failed": failed,
                        "blocked": blocked,
                        "ai_assisted": ai_assisted,
                        "results": run_results,
                    }
                )
            except OSError:
                pass
        _test_running = False
        _test_stop_event.clear()
        if ai_mode:
            emit_test_ai_status(
                "done",
                f"AI TC done: {passed} passed, {failed} failed, {blocked} blocked, {ai_assisted} AI-assisted",
                ai_fallback=ai_fallback,
                ai_assisted=ai_assisted,
            )
        socketio.emit(
            "test_run_finished",
            {
                "total": total,
                "passed": passed,
                "failed": failed,
                "blocked": blocked,
                "mode": mode,
                "ai_fallback": ai_fallback,
                "ai_assisted": ai_assisted,
                "finished_at": time.strftime("%H:%M:%S"),
            },
            namespace="/mirror",
        )


def stream_loop():
    global _streaming

    interval = 1.0 / FPS
    while _streaming:
        started = time.time()
        frame = capture_frame()
        if frame:
            mime, data = frame
            socketio.emit("frame", {"mime": mime, "data": data}, namespace="/mirror")
        else:
            socketio.emit(
                "mirror_error",
                {"msg": "Capture failed. Check USB debugging and adb devices."},
                namespace="/mirror",
            )

        elapsed = time.time() - started
        time.sleep(max(0, interval - elapsed))


HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Tablet Test Browser</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #101114;
      --panel: #181a20;
      --panel-2: #20232b;
      --line: #323640;
      --text: #f4f7fb;
      --muted: #a8b0bd;
      --accent: #2fa36b;
      --danger: #d94b4b;
      --warn: #d59f34;
      --blue: #3b6ea8;
    }

    * { box-sizing: border-box; }

    html,
    body {
      width: 100%;
      height: 100%;
      margin: 0;
      overflow: hidden;
      background: var(--bg);
      color: var(--text);
      font-family: "Segoe UI", Arial, sans-serif;
    }

    .app {
      display: grid;
      grid-template-columns: minmax(320px, 33.333vw) 1fr;
      width: 100vw;
      height: 100vh;
    }

    .workspace,
    .mirror {
      display: flex;
      min-width: 0;
      min-height: 0;
      height: 100%;
      flex-direction: column;
    }

    .workspace {
      border-right: 1px solid var(--line);
      background: var(--panel);
    }

    .mirror {
      background: #050608;
      --progress-height: 240px;
    }

    .workspace-header,
    .mirror-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      min-height: 52px;
      padding: 0 16px;
      border-bottom: 1px solid var(--line);
      background: var(--panel-2);
    }

    .title {
      margin: 0;
      font-size: 15px;
      font-weight: 700;
      letter-spacing: 0;
    }

    .status {
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }

    .workspace-body {
      display: flex;
      flex: 1;
      min-height: 0;
      flex-direction: column;
      gap: 12px;
      padding: 14px;
    }

    .upload-panel,
    .device-panel,
    .case-panel,
    .progress-panel {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #111318;
    }

    .upload-panel {
      display: grid;
      grid-template-columns: 1fr 96px;
      gap: 8px;
      padding: 10px;
    }

    input,
    button {
      font: inherit;
    }

    input {
      width: 100%;
      height: 38px;
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #0c0e13;
      color: var(--text);
      outline: none;
      padding: 0 10px;
    }

    input[type="file"] {
      padding: 7px 8px;
      color: var(--muted);
    }

    button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      height: 38px;
      min-width: 0;
      border: 0;
      border-radius: 6px;
      color: #fff;
      cursor: pointer;
      font-weight: 700;
      line-height: 1;
      overflow: hidden;
      white-space: nowrap;
    }

    button:disabled {
      cursor: not-allowed;
      opacity: .45;
    }

    .btn-start { background: var(--accent); }
    .btn-stop { background: var(--danger); }
    .btn-load,
    .btn-upload { background: var(--blue); }
    .btn-run { background: #5b63c7; }
    .btn-ai-test { background: #355bc1; }
    .btn-random-dry { background: #6b7280; }
    .btn-random-dry.active { background: var(--accent); }
    .btn-random-live { background: #ad6b2d; }
    .btn-random-ai { background: #355bc1; }
    .btn-random-stop { background: #8f3d56; }
    .btn-record { background: #b2554a; }
    .btn-save { background: var(--blue); }
    .btn-play { background: var(--accent); }
    .btn-secondary { background: #6b7280; }

    .ui-icon {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex: 0 0 auto;
      width: 16px;
      height: 16px;
      color: rgba(255, 255, 255, .94);
      font-size: 16px;
      font-weight: 900;
      line-height: 1;
    }

    .ui-icon::before {
      content: "";
      display: block;
    }

    .icon-connect::before { content: "+"; }
    .icon-disconnect::before { content: "x"; }
    .icon-upload::before { content: "^"; }
    .icon-run::before,
    .icon-play::before { content: ">"; }
    .icon-ai-test::before { content: "AI"; font-size: 10px; }
    .icon-dry::before { content: "M"; }
    .icon-live::before { content: ">"; }
    .icon-ai::before { content: "AI"; font-size: 10px; }
    .icon-record::before { content: "o"; }
    .icon-stop::before,
    .icon-stop-play::before { content: "s"; }
    .icon-clear::before { content: "c"; }
    .icon-load::before { content: "L"; }
    .icon-save::before { content: "S"; }
    .icon-export::before { content: "E"; }
    .icon-delete::before { content: "D"; }
    .icon-refresh::before { content: "R"; }
    .icon-tab-case::before { content: "T"; }
    .icon-tab-random::before { content: "?"; }
    .icon-tab-recorder::before { content: "R"; }

    .controls {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }

    .test-actions {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) 90px;
      gap: 8px;
    }

    .test-ai-state {
      min-height: 28px;
      padding: 7px 9px;
      border: 1px solid rgba(255, 255, 255, .08);
      border-radius: 6px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 800;
      background: rgba(255, 255, 255, .025);
    }

    .test-ai-state.active {
      color: #fff;
      border-color: rgba(53, 91, 193, .7);
      background: rgba(53, 91, 193, .22);
    }

    .test-resolution-state {
      min-height: 28px;
      padding: 7px 9px;
      border: 1px solid rgba(255, 255, 255, .08);
      border-radius: 6px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      line-height: 1.4;
      background: rgba(255, 255, 255, .025);
    }

    .test-resolution-state.match {
      color: #a8f2c8;
      border-color: rgba(62, 173, 111, .62);
      background: rgba(62, 173, 111, .16);
    }

    .test-resolution-state.mismatch {
      color: #ffd39a;
      border-color: rgba(217, 164, 65, .72);
      background: rgba(217, 164, 65, .16);
    }

    .tab-bar {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 6px;
    }

    .tab-button {
      height: 44px;
      border: 1px solid #343a46;
      background: #282d37;
      color: var(--muted);
      font-size: 14px;
      box-shadow: inset 0 -1px 0 rgba(0, 0, 0, .3);
    }

    .tab-button.active {
      background: #355bc1;
      color: #fff;
      border-color: #4c70d3;
      box-shadow: inset 0 0 0 1px rgba(255, 255, 255, .08);
      font-size: 16px;
      font-weight: 800;
    }

    .tab-button .ui-icon {
      color: rgba(255, 255, 255, .45);
      font-size: 16px;
    }

    .tab-button.active .ui-icon {
      color: rgba(255, 255, 255, .96);
      font-size: 18px;
    }

    .tab-pane {
      display: none;
      min-height: 0;
      flex: 1;
      flex-direction: column;
      gap: 12px;
    }

    .tab-pane.active {
      display: flex;
    }

    .random-panel {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #111318;
    }

    .random-panel .panel-head {
      grid-column: 1 / -1;
      min-height: 24px;
      padding: 0 0 8px;
      border-bottom: 1px solid var(--line);
    }

    .random-memory-state {
      grid-column: 1 / -1;
      min-height: 26px;
      padding: 6px 8px;
      border: 1px solid rgba(255, 255, 255, .08);
      border-radius: 6px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 800;
      background: rgba(255, 255, 255, .025);
    }

    .random-memory-state.active {
      color: #fff;
      border-color: rgba(47, 163, 107, .5);
      background: rgba(47, 163, 107, .18);
    }

    .random-field {
      min-width: 0;
    }

    .random-field label {
      display: block;
      margin-bottom: 4px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
    }

    .random-actions {
      display: grid;
      grid-column: 1 / -1;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
    }

    .recorder-panel {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #111318;
    }

    .recorder-panel .panel-head,
    .recorder-actions,
    .recording-select-row {
      grid-column: 1 / -1;
    }

    .recorder-actions {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 8px;
    }

    .recording-select-row {
      display: grid;
      grid-template-columns: 1fr 82px 96px;
      gap: 8px;
    }

    select {
      width: 100%;
      height: 38px;
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #0c0e13;
      color: var(--text);
      outline: none;
      padding: 0 10px;
      font: inherit;
    }

    .device-panel {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px 12px;
      padding: 10px;
    }

    .device-panel .panel-head {
      grid-column: 1 / -1;
      min-height: 24px;
      padding: 0 0 8px;
      border-bottom: 1px solid var(--line);
    }

    .device-field {
      min-width: 0;
    }

    .device-label {
      margin-bottom: 3px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
    }

    .device-value {
      min-height: 18px;
      overflow: hidden;
      color: var(--text);
      font-size: 12px;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .case-panel {
      display: flex;
      flex: 1;
      min-height: 0;
      flex-direction: column;
      overflow: hidden;
    }

    .panel-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      min-height: 38px;
      padding: 0 10px;
      border-bottom: 1px solid var(--line);
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }

    .case-list,
    .record-list,
    .progress-list {
      flex: 1;
      min-height: 0;
      overflow-x: hidden;
      overflow-y: auto;
      padding: 8px;
      scrollbar-gutter: stable;
    }

    .case-row,
    .record-row,
    .progress-row {
      display: grid;
      grid-template-columns: 34px 80px 1fr;
      gap: 8px;
      align-items: center;
      min-height: 34px;
      padding: 7px 8px;
      border-bottom: 1px solid rgba(255, 255, 255, .06);
      color: var(--muted);
      font-size: 12px;
    }

    .case-row strong,
    .record-row strong,
    .progress-row strong {
      color: var(--text);
      font-size: 12px;
    }

    .case-row span,
    .record-row span,
    .progress-row span {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .record-row {
      grid-template-columns: 34px 66px 1fr 64px;
    }

    .progress-tabs {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 6px;
      padding: 8px;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 255, 255, .02);
    }

    .progress-head-actions {
      display: inline-flex;
      align-items: center;
      justify-content: flex-end;
      gap: 8px;
      min-width: 0;
    }

    .progress-head-actions #run-summary {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .progress-export {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: auto;
      min-width: 92px;
      height: 28px;
      padding: 0 10px;
      border-radius: 6px;
      background: #345f92;
      color: #fff;
      font-size: 11px;
      font-weight: 800;
      text-decoration: none;
      white-space: nowrap;
    }

    .progress-export[aria-disabled="true"] {
      pointer-events: none;
      opacity: .45;
    }

    .progress-tab {
      height: 32px;
      border: 1px solid #343a46;
      border-radius: 6px;
      background: #242832;
      color: var(--muted);
      font-size: 12px;
      font-weight: 800;
      white-space: nowrap;
    }

    .progress-tab.active {
      background: #355bc1;
      border-color: #4c70d3;
      color: #fff;
    }

    .progress-tab span {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 22px;
      height: 18px;
      margin-left: 5px;
      padding: 0 5px;
      border-radius: 999px;
      background: rgba(255, 255, 255, .14);
      color: inherit;
      font-size: 11px;
    }

    .mirror-status {
      display: flex;
      flex-direction: column;
      align-items: flex-end;
      gap: 3px;
    }

    .empty {
      padding: 14px 10px;
      color: var(--muted);
      font-size: 12px;
    }

    .mirror-stage {
      display: flex;
      flex: 1 1 auto;
      min-height: 0;
      align-items: center;
      justify-content: center;
      overflow: hidden;
      padding: 16px;
      position: relative;
      background: radial-gradient(circle at 50% 35%, #191d24 0, #050608 70%);
    }

    #screen {
      display: block;
      width: auto;
      height: auto;
      box-sizing: border-box;
      max-width: 100%;
      max-height: 100%;
      object-fit: contain;
      object-position: center;
      border: 1px solid #333944;
      background: #000;
      box-shadow: 0 20px 70px rgba(0, 0, 0, .45);
      cursor: crosshair;
      user-select: none;
      -webkit-user-drag: none;
      touch-action: none;
    }

    #screen.portrait {
      max-width: 100%;
      max-height: 100%;
    }

    #screen.landscape {
      max-width: 100%;
      max-height: 100%;
    }

    .touch-ripple {
      position: absolute;
      width: 22px;
      height: 22px;
      margin: -11px 0 0 -11px;
      border: 2px solid rgba(72, 166, 255, .95);
      border-radius: 50%;
      pointer-events: none;
      transform: scale(.65);
      opacity: .9;
      animation: touch-ripple .32s ease-out forwards;
    }

    @keyframes touch-ripple {
      to {
        transform: scale(1.9);
        opacity: 0;
      }
    }

    .progress-panel {
      display: flex;
      flex: 0 0 var(--progress-height);
      height: var(--progress-height);
      min-height: 150px;
      flex-direction: column;
      margin: 0 16px 16px;
      overflow: hidden;
    }

    .resize-handle {
      position: relative;
      flex: 0 0 12px;
      margin: 0 16px;
      cursor: row-resize;
      touch-action: none;
    }

    .resize-handle::before {
      content: "";
      position: absolute;
      left: 0;
      right: 0;
      top: 5px;
      height: 2px;
      border-radius: 2px;
      background: #3a404d;
    }

    .resize-handle:hover::before,
    .resize-handle.dragging::before {
      background: #6f7ea1;
    }

    .progress-row {
      grid-template-columns: 44px 84px 1fr 72px;
    }

    .badge {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      height: 22px;
      border-radius: 999px;
      padding: 0 8px;
      color: #fff;
      font-size: 11px;
      font-weight: 800;
      text-transform: uppercase;
    }

    .badge.waiting { background: #4b5260; }
    .badge.running { background: var(--warn); }
    .badge.passed { background: var(--accent); }
    .badge.failed { background: var(--danger); }
    .badge.blocked { background: #8f6a27; }
    .badge.stopped { background: #6b7280; }

    .result-link {
      color: #8ec5ff;
      font-weight: 800;
      text-decoration: none;
    }

    .result-link:hover {
      color: #c7e3ff;
      text-decoration: underline;
    }

    @media (max-width: 900px) {
      .app {
        grid-template-columns: 1fr;
        grid-template-rows: 40vh 1fr;
      }

      .workspace {
        border-right: 0;
        border-bottom: 1px solid var(--line);
      }

      .progress-panel {
        flex-basis: var(--progress-height);
      }
    }
  </style>
</head>
<body>
  <main class="app">
    <section class="workspace" aria-label="Work area">
      <div class="workspace-header">
        <h1 class="title">Work Area</h1>
        <span class="status" id="status">Waiting</span>
      </div>
      <div class="workspace-body">
        <div class="controls">
          <button class="btn-start" type="button" onclick="startMirror()"><span class="ui-icon icon-connect" aria-hidden="true"></span>기기 연결</button>
          <button class="btn-stop" type="button" onclick="stopMirror()"><span class="ui-icon icon-disconnect" aria-hidden="true"></span>연결 해제</button>
        </div>
        <section class="device-panel" aria-label="Connected device information">
          <div class="panel-head">
            <span>연결된 기기</span>
            <span id="device-state">미연결</span>
          </div>
          <div class="device-field">
            <div class="device-label">모델명</div>
            <div class="device-value" id="device-model">-</div>
          </div>
          <div class="device-field">
            <div class="device-label">해상도</div>
            <div class="device-value" id="device-resolution">-</div>
          </div>
          <div class="device-field">
            <div class="device-label">Android</div>
            <div class="device-value" id="device-android">-</div>
          </div>
          <div class="device-field">
            <div class="device-label">밀도</div>
            <div class="device-value" id="device-density">-</div>
          </div>
          <div class="device-field">
            <div class="device-label">시리얼</div>
            <div class="device-value" id="device-serial">-</div>
          </div>
          <div class="device-field">
            <div class="device-label">배터리</div>
            <div class="device-value" id="device-battery">-</div>
          </div>
        </section>
        <nav class="tab-bar" aria-label="Work area tabs">
          <button class="tab-button active" type="button" data-tab="test-case" onclick="switchTab('test-case')"><span class="ui-icon icon-tab-case" aria-hidden="true"></span>Test Case</button>
          <button class="tab-button" type="button" data-tab="random-test" onclick="switchTab('random-test')"><span class="ui-icon icon-tab-random" aria-hidden="true"></span>Random Test</button>
          <button class="tab-button" type="button" data-tab="recorder" onclick="switchTab('recorder')"><span class="ui-icon icon-tab-recorder" aria-hidden="true"></span>Recorder</button>
        </nav>

        <section class="tab-pane active" id="tab-test-case" aria-label="Test Case tab">
          <div class="upload-panel">
            <input id="excel-file" type="file" accept=".xlsx,.xlsm" aria-label="Excel test case file">
            <button class="btn-upload" type="button" onclick="uploadTestCase()"><span class="ui-icon icon-upload" aria-hidden="true"></span>Upload</button>
          </div>
          <div class="test-actions">
            <button class="btn-run" id="btn-run" type="button" onclick="runTestCase()" disabled><span class="ui-icon icon-run" aria-hidden="true"></span>Run Test Case</button>
            <button class="btn-ai-test" id="btn-ai-test" type="button" onclick="runTestCase(true)" disabled><span class="ui-icon icon-ai-test" aria-hidden="true"></span>AI TC RUN</button>
            <button class="btn-secondary" id="btn-test-stop" type="button" onclick="stopTestCase()" disabled><span class="ui-icon icon-stop" aria-hidden="true"></span>Stop</button>
          </div>
          <div class="test-ai-state" id="test-ai-state">AI TC: Checking</div>
          <div class="test-resolution-state" id="test-resolution-state" hidden></div>
          <section class="case-panel" aria-label="Uploaded test cases">
            <div class="panel-head">
              <span>Uploaded Test Case</span>
              <span id="case-count">0 steps</span>
            </div>
            <div class="case-list" id="case-list">
              <div class="empty">Upload an Excel file to load test steps.</div>
            </div>
          </section>
        </section>

        <section class="tab-pane" id="tab-random-test" aria-label="Random Test tab">
          <section class="random-panel" aria-label="Random UI exploration">
            <div class="panel-head">
              <span>Random UI Explorer</span>
              <span id="random-state">Idle</span>
            </div>
            <div class="random-field">
              <label for="random-steps">Steps</label>
              <input id="random-steps" type="number" min="1" max="200" value="10">
            </div>
            <div class="random-field">
              <label for="random-delay">Delay ms</label>
              <input id="random-delay" type="number" min="0" max="10000" step="100" value="800">
            </div>
            <div class="random-memory-state" id="random-memory-state">Memory: 0 actions</div>
            <div class="random-memory-state" id="ocr-state">OCR: Idle</div>
            <div class="random-memory-state" id="ai-state">AI: Checking</div>
            <div class="random-actions">
              <button class="btn-random-dry" id="btn-random-memory" type="button" onclick="toggleRandomMemory()"><span class="ui-icon icon-dry" aria-hidden="true"></span><span id="random-memory-label">Memory</span></button>
              <button class="btn-random-live" id="btn-random-live" type="button" onclick="runRandom()"><span class="ui-icon icon-live" aria-hidden="true"></span>Live Run</button>
              <button class="btn-random-ai" id="btn-random-ai" type="button" onclick="runRandom(true)"><span class="ui-icon icon-ai" aria-hidden="true"></span>AI Random Run</button>
              <button class="btn-secondary" id="btn-random-memory-clear" type="button" onclick="clearRandomMemory()"><span class="ui-icon icon-clear" aria-hidden="true"></span>Clear</button>
              <button class="btn-random-stop" id="btn-random-stop" type="button" onclick="stopRandom()" disabled><span class="ui-icon icon-stop" aria-hidden="true"></span>Stop</button>
            </div>
          </section>
        </section>

        <section class="tab-pane" id="tab-recorder" aria-label="Recorder tab">
          <section class="recorder-panel" aria-label="Recorder controls">
            <div class="panel-head">
              <span>Recorder</span>
              <span id="record-state">Idle</span>
            </div>
            <div class="recorder-actions">
              <button class="btn-record" id="btn-record-start" type="button" onclick="startRecording()"><span class="ui-icon icon-record" aria-hidden="true"></span>Record</button>
              <button class="btn-secondary" id="btn-record-stop" type="button" onclick="stopRecording()" disabled><span class="ui-icon icon-stop" aria-hidden="true"></span>Stop</button>
              <button class="btn-secondary" id="btn-record-clear" type="button" onclick="clearRecording()"><span class="ui-icon icon-clear" aria-hidden="true"></span>Clear</button>
            </div>
            <div class="recording-select-row">
              <select id="recording-select" aria-label="Saved recordings"></select>
              <select id="record-repeat-count" aria-label="Playback repeat count">
                <option value="1">1회</option>
                <option value="2">2회</option>
                <option value="3">3회</option>
                <option value="4">4회</option>
                <option value="5">5회</option>
                <option value="6">6회</option>
                <option value="7">7회</option>
                <option value="8">8회</option>
                <option value="9">9회</option>
                <option value="10">10회</option>
              </select>
              <button class="btn-secondary" id="btn-record-load" type="button" onclick="loadSelectedRecording()"><span class="ui-icon icon-load" aria-hidden="true"></span>Load</button>
            </div>
            <div class="recorder-actions">
              <button class="btn-save" id="btn-record-save" type="button" onclick="saveRecording()" disabled><span class="ui-icon icon-save" aria-hidden="true"></span>Save JSON</button>
              <button class="btn-play" id="btn-record-play" type="button" onclick="playSelectedRecording()" disabled><span class="ui-icon icon-play" aria-hidden="true"></span>Play</button>
              <button class="btn-secondary" id="btn-record-play-stop" type="button" onclick="stopPlayback()" disabled><span class="ui-icon icon-stop-play" aria-hidden="true"></span>Stop Play</button>
            </div>
            <div class="recorder-actions">
              <button class="btn-secondary" id="btn-record-export" type="button" onclick="exportSelectedRecording()" disabled><span class="ui-icon icon-export" aria-hidden="true"></span>Export Excel</button>
              <button class="btn-stop" id="btn-record-delete" type="button" onclick="deleteSelectedRecording()" disabled><span class="ui-icon icon-delete" aria-hidden="true"></span>Delete</button>
              <button class="btn-secondary" type="button" onclick="refreshRecordings()"><span class="ui-icon icon-refresh" aria-hidden="true"></span>Refresh</button>
            </div>
          </section>
          <section class="case-panel" aria-label="Recorded actions">
            <div class="panel-head">
              <span>Recorded Actions</span>
              <span id="record-count">0 steps</span>
            </div>
            <div class="record-list" id="record-list">
              <div class="empty">Press Record, then click or drag on Tablet Screen.</div>
            </div>
          </section>
        </section>
      </div>
    </section>

    <section class="mirror" aria-label="Tablet mirror">
      <div class="mirror-header">
        <h2 class="title">Tablet Screen</h2>
        <div class="mirror-status">
          <span class="status" id="fps-info">FPS: 0</span>
          <span class="status" id="mouse-info">Mouse: -</span>
        </div>
      </div>
      <div class="mirror-stage">
        <img id="screen" src="" alt="Tablet screen">
      </div>
      <div class="resize-handle" id="mirror-progress-resizer" role="separator" aria-orientation="horizontal" title="Drag to resize test progress"></div>
      <section class="progress-panel" aria-label="Test progress">
        <div class="panel-head">
          <span>Test Progress</span>
          <div class="progress-head-actions">
            <span id="run-summary">Not started</span>
            <button class="progress-export" id="btn-progress-export" type="button" onclick="exportProgressCsv()" disabled>
              Export CSV
            </button>
          </div>
        </div>
        <div class="progress-tabs" role="tablist" aria-label="Progress result filter">
          <button class="progress-tab" type="button" data-progress-filter="passed" role="tab">
            Pass <span id="progress-count-passed">0</span>
          </button>
          <button class="progress-tab active" type="button" data-progress-filter="failed" role="tab">
            Fail <span id="progress-count-failed">0</span>
          </button>
          <button class="progress-tab" type="button" data-progress-filter="blocked" role="tab">
            Block(N/A) <span id="progress-count-blocked">0</span>
          </button>
        </div>
        <div class="progress-list" id="progress-list">
          <div class="empty">The running step sequence will appear here.</div>
        </div>
      </section>
    </section>
  </main>

  <script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
  <script>
    const socket = io('/mirror');
    const screen = document.getElementById('screen');
    const statusLabel = document.getElementById('status');
    const fpsInfo = document.getElementById('fps-info');
    const mouseInfo = document.getElementById('mouse-info');
    const fileInput = document.getElementById('excel-file');
    const runButton = document.getElementById('btn-run');
    const aiRunButton = document.getElementById('btn-ai-test');
    const testStopButton = document.getElementById('btn-test-stop');
    const testAiState = document.getElementById('test-ai-state');
    const testResolutionState = document.getElementById('test-resolution-state');
    const caseList = document.getElementById('case-list');
    const caseCount = document.getElementById('case-count');
    const progressList = document.getElementById('progress-list');
    const runSummary = document.getElementById('run-summary');
    const progressExportButton = document.getElementById('btn-progress-export');
    const progressTabs = Array.from(document.querySelectorAll('.progress-tab'));
    const progressCountEls = {
      passed: document.getElementById('progress-count-passed'),
      failed: document.getElementById('progress-count-failed'),
      blocked: document.getElementById('progress-count-blocked')
    };
    const randomState = document.getElementById('random-state');
    const randomSteps = document.getElementById('random-steps');
    const randomDelay = document.getElementById('random-delay');
    const randomMemoryButton = document.getElementById('btn-random-memory');
    const randomMemoryLabel = document.getElementById('random-memory-label');
    const randomMemoryState = document.getElementById('random-memory-state');
    const ocrState = document.getElementById('ocr-state');
    const aiState = document.getElementById('ai-state');
    const randomMemoryClearButton = document.getElementById('btn-random-memory-clear');
    const randomLiveButton = document.getElementById('btn-random-live');
    const randomAiButton = document.getElementById('btn-random-ai');
    const randomStopButton = document.getElementById('btn-random-stop');
    const recordState = document.getElementById('record-state');
    const recordStartButton = document.getElementById('btn-record-start');
    const recordStopButton = document.getElementById('btn-record-stop');
    const recordClearButton = document.getElementById('btn-record-clear');
    const recordSaveButton = document.getElementById('btn-record-save');
    const recordPlayButton = document.getElementById('btn-record-play');
    const recordPlayStopButton = document.getElementById('btn-record-play-stop');
    const recordExportButton = document.getElementById('btn-record-export');
    const recordDeleteButton = document.getElementById('btn-record-delete');
    const recordLoadButton = document.getElementById('btn-record-load');
    const recordSelect = document.getElementById('recording-select');
    const recordRepeatCount = document.getElementById('record-repeat-count');
    const recordList = document.getElementById('record-list');
    const recordCount = document.getElementById('record-count');
    const deviceState = document.getElementById('device-state');
    const deviceModel = document.getElementById('device-model');
    const deviceResolution = document.getElementById('device-resolution');
    const deviceAndroid = document.getElementById('device-android');
    const deviceDensity = document.getElementById('device-density');
    const deviceSerial = document.getElementById('device-serial');
    const deviceBattery = document.getElementById('device-battery');
    const mirrorPane = document.querySelector('.mirror');
    const mirrorHeader = document.querySelector('.mirror-header');
    const mirrorStage = document.querySelector('.mirror-stage');
    const progressPanel = document.querySelector('.progress-panel');
    const resizeHandle = document.getElementById('mirror-progress-resizer');

    let frameCount = 0;
    let lastFpsTime = Date.now();
    let loadedSteps = [];
    let isTestRunning = false;
    let progressItems = new Map();
    let activeProgressFilter = 'failed';
    let isRandomMemoryActive = false;
    let isRandomRunning = false;
    let randomMemoryActions = [];
    let resizeStartY = 0;
    let resizeStartHeight = 0;
    let isResizingProgress = false;
    let isRecording = false;
    let recordedActions = [];
    let lastRecordedAt = 0;
    let selectedRecordingId = '';
    let isPlayingRecording = false;
    let recordingScreenWidth = 0;
    let recordingScreenHeight = 0;
    const defaultProgressHeight = 240;
    const minProgressHeight = 150;
    const minMirrorStageHeight = 140;
    const tapMoveThreshold = 8;
    const longPressMs = 600;
    let pointerGesture = null;

    socket.on('connect', () => {
      statusLabel.textContent = 'Server connected';
      loadCurrentTestCase();
      refreshDeviceInfo();
      refreshRecordings();
      refreshOcrStatus();
      refreshAiStatus();
    });

    socket.on('disconnect', () => {
      statusLabel.textContent = 'Disconnected';
    });

    socket.on('mirror_error', (data) => {
      statusLabel.textContent = data.msg || 'Mirror error';
      refreshDeviceInfo();
    });

    socket.on('frame', (data) => {
      const mime = data.mime || (data.data.startsWith('/9j/') ? 'jpeg' : 'png');
      screen.src = 'data:image/' + mime + ';base64,' + data.data;
      if (!statusLabel.textContent.startsWith('Running')) {
        statusLabel.textContent = 'Mirroring';
      }

      frameCount += 1;
      const now = Date.now();
      if (now - lastFpsTime >= 1000) {
        fpsInfo.textContent = 'FPS: ' + frameCount;
        frameCount = 0;
        lastFpsTime = now;
      }
    });

    screen.addEventListener('load', () => {
      fitScreenToStage();
      syncRandomControls();
      if (isActiveTab('random-test') && !isRandomRunning) {
        statusLabel.textContent = 'Random Test ready';
      }
    });
    setupScreenInput();
    recordSelect.addEventListener('change', () => {
      selectedRecordingId = recordSelect.value || '';
      updateRecorderControls();
    });
    progressTabs.forEach((tab) => {
      tab.addEventListener('click', () => {
        setProgressFilter(tab.dataset.progressFilter || 'failed');
      });
    });
    setProgressFilter(activeProgressFilter);
    syncRandomControls();
    renderRecordedActions();
    updateRecorderControls();

    socket.on('test_run_started', (data) => {
      const label = data.mode === 'ai' ? 'AI TC RUN' : 'Running test case';
      statusLabel.textContent = label;
      if (data.mode === 'ai') {
        testAiState.textContent = 'AI TC: Analyzing';
        testAiState.classList.add('active');
      }
      runSummary.textContent = `0 / ${data.total}`;
      setTestRunning(true);
      resetProgressState();
    });

    socket.on('test_progress', (data) => {
      upsertProgress(data);
      updateRunSummary(data.index, data.total);
    });

    socket.on('test_error', (data) => {
      statusLabel.textContent = data.msg || 'Test error';
    });

    socket.on('test_ai_status', (data) => {
      const state = data.state || 'unknown';
      const labels = {
        analyzing: 'AI TC: Analyzing',
        executing: 'AI TC: Executing',
        assisting: `AI TC: Assisting${data.step ? ` step ${data.step}` : ''}`,
        fallback: 'AI TC: Fallback',
        done: 'AI TC: Done'
      };
      testAiState.textContent = labels[state] || 'AI TC: Unknown';
      testAiState.title = data.message || '';
      testAiState.classList.toggle('active', ['analyzing', 'executing', 'assisting'].includes(state));
      statusLabel.textContent = data.message || testAiState.textContent;
    });

    socket.on('test_run_finished', (data) => {
      const blocked = data.blocked || 0;
      const assisted = data.ai_assisted || 0;
      const isAi = data.mode === 'ai';
      statusLabel.textContent = isAi
        ? `AI TC Done: ${data.passed} passed, ${data.failed} failed, ${blocked} blocked, ${assisted} AI-assisted`
        : `Done: ${data.passed} passed, ${data.failed} failed, ${blocked} blocked`;
      runSummary.textContent = isAi
        ? `${data.passed} passed / ${data.failed} failed / ${blocked} blocked / ${assisted} AI-assisted`
        : `${data.passed} passed / ${data.failed} failed / ${blocked} blocked`;
      setTestRunning(false);
      isPlayingRecording = false;
      updateRecorderControls();
      refreshAiStatus();
    });

    function randomModeLabel(mode) {
      if (mode === 'ai') return 'AI Random Run';
      if (mode === 'dry-run') return 'Dry Run';
      return 'Live Run';
    }

    socket.on('random_run_started', (data) => {
      const label = randomModeLabel(data.mode);
      statusLabel.textContent = `${label} random explorer`;
      randomState.textContent = label;
      runSummary.textContent = `0 / ${data.total}`;
      resetProgressState();
      setProgressFilter('passed');
      setRandomRunning(true);
      refreshOcrStatus();
    });

    socket.on('random_ai_status', (data) => {
      const state = data.state || 'unknown';
      const model = data.model ? ` (${data.model})` : '';
      aiState.textContent = state === 'planning'
        ? `AI: Planning${model}`
        : state === 'fallback'
          ? 'AI: Fallback'
          : `AI: Ready${model}`;
      aiState.title = data.message || '';
      aiState.classList.toggle('active', state === 'ready' || state === 'planning');
      statusLabel.textContent = data.message || aiState.textContent;
    });

    socket.on('random_progress', (data) => {
      upsertProgress(data);
      updateRunSummary(data.index, data.total);
    });

    socket.on('random_error', (data) => {
      statusLabel.textContent = data.msg || 'Random explorer error';
      randomState.textContent = 'Error';
      setRandomRunning(false);
      refreshOcrStatus();
    });

    socket.on('random_run_finished', (data) => {
      const label = randomModeLabel(data.mode);
      const stopped = data.stopped || 0;
      const done = (data.passed || 0) + (data.failed || 0) + stopped;
      const hadError = randomState.textContent === 'Error' && done === 0;
      statusLabel.textContent = stopped
        ? `${label} stopped: ${data.passed} passed, ${data.failed} failed`
        : hadError
          ? statusLabel.textContent
        : `${label} done: ${data.passed} passed, ${data.failed} failed, 0 blocked`;
      runSummary.textContent = stopped
        ? `${done} / ${data.total} - ${data.passed} passed / ${data.failed} failed / ${stopped} stopped`
        : `${done} / ${data.total} - ${data.passed} passed / ${data.failed} failed / 0 blocked`;
      randomState.textContent = 'Idle';
      setRandomRunning(false);
      refreshOcrStatus();
      refreshAiStatus();
    });

    setupProgressResize();

    function startMirror() {
      socket.emit('start');
      refreshDeviceInfo();
    }

    function stopMirror() {
      socket.emit('stop');
      statusLabel.textContent = 'Stopped';
      fpsInfo.textContent = 'FPS: 0';
      deviceState.textContent = 'Disconnected';
    }

    async function refreshDeviceInfo() {
      try {
        const response = await fetch('/api/device');
        const data = await response.json();
        renderDeviceInfo(data);
      } catch (error) {
        renderDeviceInfo({ connected: false });
      }
    }

    async function refreshOcrStatus() {
      try {
        const response = await fetch('/api/ocr/status');
        const data = await response.json();
        const state = data.state || 'idle';
        let label = 'OCR: Idle';
        if (state === 'ready') {
          label = 'OCR: Ready';
        } else if (state === 'loading') {
          label = 'OCR: Loading';
        } else if (state === 'unavailable' || data.available === false || data.installed === false) {
          label = 'OCR: Unavailable';
        }
        ocrState.textContent = label;
        ocrState.title = data.message || '';
        ocrState.classList.toggle('active', data.available === true || state === 'ready');
      } catch (error) {
        ocrState.textContent = 'OCR: Unknown';
        ocrState.classList.remove('active');
      }
    }

    async function refreshAiStatus() {
      try {
        const response = await fetch('/api/ai/status');
        const data = await response.json();
        aiState.textContent = data.configured
          ? `AI: Ready (${data.model})`
          : 'AI: API key required';
        aiState.title = data.message || '';
        aiState.classList.toggle('active', Boolean(data.configured));
        testAiState.textContent = data.configured
          ? `AI TC: Ready (${data.model})`
          : 'AI TC: API key required';
        testAiState.title = data.message || '';
        testAiState.classList.toggle('active', Boolean(data.configured));
      } catch (error) {
        aiState.textContent = 'AI: Unknown';
        aiState.classList.remove('active');
        testAiState.textContent = 'AI TC: Unknown';
        testAiState.classList.remove('active');
      }
    }

    function renderDeviceInfo(data) {
      if (!data || !data.connected) {
        deviceState.textContent = 'Not connected';
        deviceModel.textContent = '-';
        deviceResolution.textContent = '-';
        deviceAndroid.textContent = '-';
        deviceDensity.textContent = '-';
        deviceSerial.textContent = '-';
        deviceBattery.textContent = '-';
        return;
      }

      const modelName = data.model || data.market_name || data.device || '';
      const modelParts = [data.manufacturer, modelName].filter(Boolean);
      deviceState.textContent = 'Connected';
      deviceModel.textContent = modelParts.length ? modelParts.join(' ') : '-';
      deviceResolution.textContent = data.resolution || '-';
      deviceAndroid.textContent = data.android ? `Android ${data.android} (SDK ${data.sdk || '-'})` : '-';
      deviceDensity.textContent = data.density ? `${data.density} dpi` : '-';
      deviceSerial.textContent = data.serial || '-';
      deviceBattery.textContent = data.battery_level ? `${data.battery_level}%` : '-';
    }

    async function uploadTestCase() {
      if (!fileInput.files.length) {
        statusLabel.textContent = 'Select an Excel file first.';
        return;
      }

      const form = new FormData();
      form.append('file', fileInput.files[0]);
      statusLabel.textContent = 'Uploading test case';

      const response = await fetch('/api/testcase', { method: 'POST', body: form });
      const data = await response.json();

      if (!response.ok) {
        loadedSteps = [];
        renderCases(loadedSteps);
        renderProgress(loadedSteps, 'waiting');
        updateRecorderResolutionState(null);
        setTestRunning(false);
        runSummary.textContent = 'Upload failed';
        statusLabel.textContent = data.error || 'Upload failed';
        return;
      }

      loadedSteps = data.steps || [];
      renderCases(loadedSteps);
      renderProgress(loadedSteps, 'waiting');
      updateRecorderResolutionState(data.recorder_resolution);
      setTestRunning(false);
      statusLabel.textContent = data.source === 'recorder'
        ? `Loaded Recorder Excel: ${loadedSteps.length} steps`
        : data.source === 'automation'
          ? `Loaded Automation Excel: ${loadedSteps.length} steps`
        : data.source === 'manual_qa'
          ? `Loaded Manual QA Excel: ${loadedSteps.length} cases`
          : data.source === 'wordp_testcase'
            ? `Loaded WordP TestCase: ${loadedSteps.length} cases`
          : `Loaded ${loadedSteps.length} steps`;
      runSummary.textContent = 'Ready';
    }

    async function loadCurrentTestCase() {
      const response = await fetch('/api/testcase');
      if (!response.ok) {
        return;
      }
      const data = await response.json();
      loadedSteps = data.steps || [];
      updateRecorderResolutionState(data.recorder_resolution);
      if (loadedSteps.length) {
        renderCases(loadedSteps);
        renderProgress(loadedSteps, 'waiting');
        setTestRunning(false);
        runSummary.textContent = 'Ready';
      }
    }

    function updateRecorderResolutionState(resolution) {
      if (!resolution || !resolution.expected) {
        testResolutionState.hidden = true;
        testResolutionState.textContent = '';
        testResolutionState.classList.remove('match', 'mismatch');
        return;
      }

      const label = (item) => {
        if (!item) return '-';
        const orientation = item.orientation === 'landscape' ? '가로' : item.orientation === 'portrait' ? '세로' : '정사각';
        return `${item.width}×${item.height} (${orientation})`;
      };
      const expected = label(resolution.expected);
      const current = label(resolution.current);
      const matches = Boolean(resolution.matches);
      testResolutionState.hidden = false;
      testResolutionState.classList.toggle('match', matches);
      testResolutionState.classList.toggle('mismatch', !matches);
      testResolutionState.textContent = matches
        ? `Recorder: ${expected} / Current: ${current} — 실행 가능`
        : `Recorder: ${expected} / Current: ${current} — 기기를 ${resolution.expected.orientation === 'landscape' ? '가로' : '세로'}로 전환한 뒤 실행하세요.`;
    }

    async function runTestCase(aiMode = false) {
      if (!loadedSteps.length) {
        statusLabel.textContent = 'Upload a test case first.';
        return;
      }

      setTestRunning(true);
      statusLabel.textContent = aiMode ? 'Preparing AI TC RUN' : 'Preparing Test Case run';
      const response = await fetch('/api/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ai_mode: aiMode })
      });
      const data = await response.json();
      if (!response.ok) {
        if (Object.prototype.hasOwnProperty.call(data, 'recorder_resolution')) {
          updateRecorderResolutionState(data.recorder_resolution);
        }
        statusLabel.textContent = data.error || 'Run failed';
        setTestRunning(false);
      }
    }

    async function stopTestCase() {
      if (!isTestRunning) {
        return;
      }
      statusLabel.textContent = 'Stopping test case';
      testStopButton.disabled = true;
      await fetch('/api/run/stop', { method: 'POST' });
    }

    function setTestRunning(running) {
      isTestRunning = running;
      runButton.disabled = running || loadedSteps.length === 0;
      aiRunButton.disabled = running || loadedSteps.length === 0;
      testStopButton.disabled = !running;
    }

    function toggleRandomMemory() {
      if (!screen.naturalWidth || !screen.naturalHeight) {
        statusLabel.textContent = 'Start mirroring before Memory.';
        return;
      }
      isRandomMemoryActive = !isRandomMemoryActive;
      updateRandomMemoryControls();
      statusLabel.textContent = isRandomMemoryActive
        ? 'Memory ON: remembering taps and swipes'
        : `Memory OFF: ${randomMemoryActions.length} actions remembered`;
    }

    function rememberRandomAction(payload) {
      if (!isRandomMemoryActive || !['tap', 'swipe'].includes(payload.type)) {
        return;
      }
      const action = {
        type: payload.type,
        memory_index: randomMemoryActions.length + 1,
        screen_width: payload.screen_width,
        screen_height: payload.screen_height
      };
      if (payload.type === 'tap') {
        action.x = payload.x;
        action.y = payload.y;
      } else {
        action.x1 = payload.x1;
        action.y1 = payload.y1;
        action.x2 = payload.x2;
        action.y2 = payload.y2;
        action.duration_ms = payload.duration_ms;
      }
      randomMemoryActions.push(action);
      syncRandomControls();
    }

    function clearRandomMemory() {
      randomMemoryActions = [];
      syncRandomControls();
      statusLabel.textContent = isRandomMemoryActive
        ? 'Memory cleared. Continue remembering taps and swipes.'
        : 'Memory cleared.';
    }

    function updateRandomMemoryControls() {
      syncRandomControls();
    }

    function syncRandomControls() {
      randomMemoryLabel.textContent = isRandomMemoryActive ? 'Memory ON' : 'Memory';
      randomMemoryState.textContent = `Memory: ${randomMemoryActions.length} actions`;
      randomMemoryState.classList.toggle('active', isRandomMemoryActive);
      randomMemoryButton.classList.toggle('active', isRandomMemoryActive);
      randomMemoryButton.disabled = isRandomRunning;
      randomLiveButton.disabled = isRandomRunning;
      randomAiButton.disabled = isRandomRunning;
      randomMemoryClearButton.disabled = isRandomRunning || randomMemoryActions.length === 0;
      randomStopButton.disabled = !isRandomRunning;
      randomSteps.disabled = isRandomRunning;
      randomDelay.disabled = isRandomRunning;
    }

    async function runRandom(aiMode = false) {
      const steps = clampNumber(randomSteps.value, 1, 200, 10);
      const delayMs = clampNumber(randomDelay.value, 0, 10000, 800);

      setRandomRunning(true);
      const runLabel = aiMode ? 'AI Random Run' : 'Live Run';
      statusLabel.textContent = `Preparing ${runLabel} with ${randomMemoryActions.length} memory actions`;
      randomState.textContent = aiMode ? 'AI Planning' : 'Live Run';
      try {
        const response = await fetch('/api/random/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            dry_run: false,
            ai_mode: aiMode,
            steps,
            delay_ms: delayMs,
            memory_actions: randomMemoryActions
          })
        });
        const data = await response.json();
        if (!response.ok) {
          statusLabel.textContent = data.error || `${runLabel} failed`;
          randomState.textContent = 'Idle';
          setRandomRunning(false);
        }
      } catch (error) {
        statusLabel.textContent = `${runLabel} failed`;
        randomState.textContent = 'Idle';
        setRandomRunning(false);
      }
    }

    async function stopRandom() {
      if (!isRandomRunning) {
        return;
      }
      randomState.textContent = 'Stopping...';
      statusLabel.textContent = 'Stopping random test';
      randomStopButton.disabled = true;
      try {
        const response = await fetch('/api/random/stop', { method: 'POST' });
        const data = await response.json();
        if (!response.ok) {
          statusLabel.textContent = data.error || 'Stop random failed';
          randomState.textContent = 'Live Run';
          syncRandomControls();
        }
      } catch (error) {
        statusLabel.textContent = 'Stop random failed';
        randomState.textContent = 'Live Run';
        syncRandomControls();
      }
    }

    function setRandomRunning(running) {
      isRandomRunning = running;
      runButton.disabled = running || isTestRunning || loadedSteps.length === 0;
      syncRandomControls();
    }

    function clampNumber(value, min, max, fallback) {
      const parsed = Number(value);
      if (!Number.isFinite(parsed)) {
        return fallback;
      }
      return Math.max(min, Math.min(max, Math.round(parsed)));
    }

    function setupScreenInput() {
      screen.addEventListener('pointermove', (event) => {
        updateMouseInfo(event.clientX, event.clientY);
      });

      screen.addEventListener('pointerleave', () => {
        mouseInfo.textContent = 'Mouse: outside';
      });

      screen.addEventListener('contextmenu', (event) => {
        event.preventDefault();
        sendManualInput({ type: 'keyevent', key: 4 }, 'Back');
      });

      screen.addEventListener('pointerdown', (event) => {
        if (event.button !== 0) {
          return;
        }
        const point = clientToDevicePoint(event.clientX, event.clientY, false);
        if (!point) {
          return;
        }
        pointerGesture = {
          pointerId: event.pointerId,
          startClientX: event.clientX,
          startClientY: event.clientY,
          startPoint: point,
          startedAt: Date.now()
        };
        screen.setPointerCapture(event.pointerId);
        event.preventDefault();
      });

      screen.addEventListener('pointerup', (event) => {
        if (!pointerGesture || pointerGesture.pointerId !== event.pointerId) {
          return;
        }
        const gesture = pointerGesture;
        pointerGesture = null;
        if (screen.hasPointerCapture(event.pointerId)) {
          screen.releasePointerCapture(event.pointerId);
        }

        const endPoint = clientToDevicePoint(event.clientX, event.clientY, true);
        if (!endPoint) {
          return;
        }

        const clientDx = event.clientX - gesture.startClientX;
        const clientDy = event.clientY - gesture.startClientY;
        const distance = Math.hypot(clientDx, clientDy);
        const elapsed = Date.now() - gesture.startedAt;

        if (distance >= tapMoveThreshold) {
          const payload = {
            type: 'swipe',
            x1: gesture.startPoint.x,
            y1: gesture.startPoint.y,
            x2: endPoint.x,
            y2: endPoint.y,
            duration_ms: Math.max(80, Math.min(3000, elapsed)),
            screen_width: screen.naturalWidth,
            screen_height: screen.naturalHeight
          };
          sendManualInput(payload, 'Swipe').then((ok) => {
            if (ok) {
              recordAction(payload);
              rememberRandomAction(payload);
            }
          });
          showTouchRipple(event.clientX, event.clientY);
        } else if (elapsed >= longPressMs) {
          sendManualInput({
            type: 'long_press',
            x: gesture.startPoint.x,
            y: gesture.startPoint.y,
            duration_ms: Math.max(longPressMs, Math.min(3000, elapsed)),
            screen_width: screen.naturalWidth,
            screen_height: screen.naturalHeight
          }, 'Long press');
          showTouchRipple(gesture.startClientX, gesture.startClientY);
        } else {
          const payload = {
            type: 'tap',
            x: gesture.startPoint.x,
            y: gesture.startPoint.y,
            screen_width: screen.naturalWidth,
            screen_height: screen.naturalHeight
          };
          sendManualInput(payload, 'Tap').then((ok) => {
            if (ok) {
              recordAction(payload);
              rememberRandomAction(payload);
            }
          });
          showTouchRipple(gesture.startClientX, gesture.startClientY);
        }
        event.preventDefault();
      });

      screen.addEventListener('pointercancel', (event) => {
        if (pointerGesture && pointerGesture.pointerId === event.pointerId) {
          pointerGesture = null;
        }
      });

      screen.addEventListener('wheel', (event) => {
        const point = clientToDevicePoint(event.clientX, event.clientY, false);
        if (!point) {
          return;
        }
        const distance = Math.max(80, Math.round(screen.naturalHeight * 0.3));
        const half = Math.round(distance / 2);
        const y1 = clampNumber(
          event.deltaY > 0 ? point.y + half : point.y - half,
          0,
          screen.naturalHeight - 1,
          point.y
        );
        const y2 = clampNumber(
          event.deltaY > 0 ? point.y - half : point.y + half,
          0,
          screen.naturalHeight - 1,
          point.y
        );

        sendManualInput({
          type: 'swipe',
          x1: point.x,
          y1,
          x2: point.x,
          y2,
          duration_ms: 350,
          screen_width: screen.naturalWidth,
          screen_height: screen.naturalHeight
        }, 'Wheel swipe');
        showTouchRipple(event.clientX, event.clientY);
        event.preventDefault();
      }, { passive: false });
    }

    function clientToDevicePoint(clientX, clientY, clampToScreen) {
      if (!screen.naturalWidth || !screen.naturalHeight) {
        return null;
      }
      const rect = screen.getBoundingClientRect();
      if (!rect.width || !rect.height) {
        return null;
      }
      let localX = clientX - rect.left;
      let localY = clientY - rect.top;

      if (clampToScreen) {
        localX = Math.max(0, Math.min(localX, rect.width));
        localY = Math.max(0, Math.min(localY, rect.height));
      } else if (localX < 0 || localY < 0 || localX > rect.width || localY > rect.height) {
        return null;
      }

      return {
        x: Math.max(0, Math.min(screen.naturalWidth - 1, Math.round((localX / rect.width) * screen.naturalWidth))),
        y: Math.max(0, Math.min(screen.naturalHeight - 1, Math.round((localY / rect.height) * screen.naturalHeight)))
      };
    }

    async function sendManualInput(payload, label) {
      try {
        const response = await fetch('/api/input', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok) {
          statusLabel.textContent = data.error || `${label} failed`;
          return false;
        }
        statusLabel.textContent = `${label}: ${data.message || 'sent'}`;
        return true;
      } catch (error) {
        statusLabel.textContent = `${label} failed`;
        return false;
      }
    }

    function showTouchRipple(clientX, clientY) {
      const stageRect = mirrorStage.getBoundingClientRect();
      const ripple = document.createElement('span');
      ripple.className = 'touch-ripple';
      ripple.style.left = `${clientX - stageRect.left}px`;
      ripple.style.top = `${clientY - stageRect.top}px`;
      mirrorStage.appendChild(ripple);
      window.setTimeout(() => ripple.remove(), 340);
    }

    function switchTab(tabName) {
      document.querySelectorAll('.tab-button').forEach((button) => {
        button.classList.toggle('active', button.dataset.tab === tabName);
      });
      document.querySelectorAll('.tab-pane').forEach((pane) => {
        pane.classList.toggle('active', pane.id === `tab-${tabName}`);
      });
      if (tabName === 'random-test') {
        syncRandomControls();
        refreshOcrStatus();
        refreshAiStatus();
        if (!isRandomRunning) {
          statusLabel.textContent = screen.naturalWidth && screen.naturalHeight
            ? 'Random Test ready'
            : 'Mirroring required for Memory';
        }
      }
    }

    function isActiveTab(tabName) {
      const activeButton = document.querySelector('.tab-button.active');
      return activeButton && activeButton.dataset.tab === tabName;
    }

    function updateMouseInfo(clientX, clientY) {
      const point = clientToDevicePoint(clientX, clientY, false);
      if (!screen.naturalWidth || !screen.naturalHeight) {
        mouseInfo.textContent = 'Mouse: -';
      } else if (!point) {
        mouseInfo.textContent = 'Mouse: outside';
      } else {
        mouseInfo.textContent = `Mouse: ${point.x}, ${point.y} / ${screen.naturalWidth}x${screen.naturalHeight}`;
      }
    }

    function startRecording() {
      if (!screen.naturalWidth || !screen.naturalHeight) {
        statusLabel.textContent = 'Start mirroring before recording.';
        return;
      }
      isRecording = true;
      recordedActions = [];
      selectedRecordingId = '';
      recordingScreenWidth = screen.naturalWidth;
      recordingScreenHeight = screen.naturalHeight;
      lastRecordedAt = 0;
      recordState.textContent = 'Recording...';
      renderRecordedActions();
      updateRecorderControls();
      statusLabel.textContent = 'Recording clicks and drags';
    }

    function stopRecording() {
      isRecording = false;
      recordState.textContent = recordedActions.length ? 'Ready' : 'Idle';
      updateRecorderControls();
    }

    function clearRecording() {
      isRecording = false;
      recordedActions = [];
      selectedRecordingId = '';
      recordingScreenWidth = 0;
      recordingScreenHeight = 0;
      lastRecordedAt = 0;
      recordState.textContent = 'Idle';
      renderRecordedActions();
      updateRecorderControls();
    }

    function recordAction(payload) {
      if (!isRecording || !['tap', 'swipe'].includes(payload.type)) {
        return;
      }
      const now = Date.now();
      const delayMs = lastRecordedAt ? now - lastRecordedAt : 0;
      lastRecordedAt = now;
      const action = {
        type: payload.type,
        delay_ms: delayMs,
        screen_width: payload.screen_width,
        screen_height: payload.screen_height
      };
      if (payload.type === 'tap') {
        action.x = payload.x;
        action.y = payload.y;
      } else {
        action.x1 = payload.x1;
        action.y1 = payload.y1;
        action.x2 = payload.x2;
        action.y2 = payload.y2;
        action.duration_ms = payload.duration_ms;
      }
      recordedActions.push(action);
      selectedRecordingId = '';
      renderRecordedActions();
      updateRecorderControls();
    }

    function renderRecordedActions() {
      recordCount.textContent = `${recordedActions.length} steps`;
      if (!recordedActions.length) {
        recordList.innerHTML = '<div class="empty">Press Record, then click or drag on Tablet Screen.</div>';
        return;
      }
      recordList.innerHTML = recordedActions.map((action, index) => {
        const coords = action.type === 'tap'
          ? `(${action.x}, ${action.y})`
          : `(${action.x1}, ${action.y1}) -> (${action.x2}, ${action.y2})`;
        const timing = action.type === 'swipe'
          ? `${action.delay_ms}ms / ${action.duration_ms}ms`
          : `${action.delay_ms}ms`;
        return `
          <div class="record-row">
            <strong>${index + 1}</strong>
            <span>${escapeHtml(action.type)}</span>
            <span title="${escapeHtml(coords)}">${escapeHtml(coords)}</span>
            <span title="${escapeHtml(timing)}">${escapeHtml(timing)}</span>
          </div>
        `;
      }).join('');
    }

    function updateRecorderControls() {
      recordStartButton.disabled = isRecording || isPlayingRecording;
      recordStopButton.disabled = !isRecording;
      recordClearButton.disabled = isPlayingRecording || (!recordedActions.length && !isRecording);
      recordSaveButton.disabled = isPlayingRecording || !recordedActions.length;
      recordPlayButton.disabled = isPlayingRecording || !selectedRecordingId;
      recordPlayStopButton.disabled = !isPlayingRecording;
      recordExportButton.disabled = !selectedRecordingId;
      recordDeleteButton.disabled = isPlayingRecording || !selectedRecordingId;
      recordSelect.disabled = isPlayingRecording;
      recordRepeatCount.disabled = isPlayingRecording;
      recordLoadButton.disabled = isPlayingRecording || !selectedRecordingId;
    }

    async function refreshRecordings() {
      try {
        const response = await fetch('/api/recordings');
        const data = await response.json();
        const recordings = data.recordings || [];
        recordSelect.innerHTML = recordings.length
          ? recordings.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} (${item.count})</option>`).join('')
          : '<option value="">No saved recordings</option>';
        if (selectedRecordingId && recordings.some((item) => item.id === selectedRecordingId)) {
          recordSelect.value = selectedRecordingId;
        } else {
          selectedRecordingId = recordSelect.value || '';
        }
        updateRecorderControls();
      } catch (error) {
        recordSelect.innerHTML = '<option value="">Load failed</option>';
      }
    }

    async function saveRecording() {
      if (!recordedActions.length) {
        statusLabel.textContent = 'No recorded actions to save.';
        return;
      }
      const payload = {
        name: `Recording ${new Date().toLocaleString()}`,
        screen_width: recordingScreenWidth || screen.naturalWidth,
        screen_height: recordingScreenHeight || screen.naturalHeight,
        actions: recordedActions
      };
      const response = await fetch('/api/recordings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await response.json();
      if (!response.ok) {
        statusLabel.textContent = data.error || 'Save failed';
        return;
      }
      selectedRecordingId = data.id;
      recordState.textContent = 'Saved';
      statusLabel.textContent = `Saved ${data.name}`;
      await refreshRecordings();
      recordSelect.value = selectedRecordingId;
      updateRecorderControls();
    }

    async function loadSelectedRecording() {
      const id = recordSelect.value;
      if (!id) {
        return;
      }
      const response = await fetch(`/api/recordings/${encodeURIComponent(id)}`);
      const data = await response.json();
      if (!response.ok) {
        statusLabel.textContent = data.error || 'Load failed';
        return;
      }
      selectedRecordingId = data.id;
      recordedActions = data.actions || [];
      recordingScreenWidth = data.screen_width || 0;
      recordingScreenHeight = data.screen_height || 0;
      isRecording = false;
      recordState.textContent = 'Loaded';
      renderRecordedActions();
      updateRecorderControls();
    }

    async function playSelectedRecording() {
      const id = recordSelect.value || selectedRecordingId;
      if (!id || !screen.naturalWidth || !screen.naturalHeight) {
        statusLabel.textContent = 'Select a recording and start mirroring first.';
        return;
      }
      const repeatCount = clampNumber(recordRepeatCount.value, 1, 10, 1);
      isPlayingRecording = true;
      updateRecorderControls();
      const response = await fetch(`/api/recordings/${encodeURIComponent(id)}/play`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          screen_width: screen.naturalWidth,
          screen_height: screen.naturalHeight,
          repeat_count: repeatCount
        })
      });
      const data = await response.json();
      if (!response.ok) {
        statusLabel.textContent = data.error || 'Playback failed';
        isPlayingRecording = false;
        updateRecorderControls();
        return;
      }
      selectedRecordingId = id;
      recordState.textContent = 'Playing...';
      statusLabel.textContent = `Recorder playback started (${repeatCount}x)`;
    }

    async function stopPlayback() {
      await fetch('/api/recordings/play/stop', { method: 'POST' });
      recordState.textContent = 'Stopping...';
    }

    function exportSelectedRecording() {
      const id = recordSelect.value || selectedRecordingId;
      if (!id) {
        return;
      }
      window.location.href = `/api/recordings/${encodeURIComponent(id)}/export`;
    }

    async function deleteSelectedRecording() {
      const id = recordSelect.value || selectedRecordingId;
      if (!id) {
        return;
      }
      const response = await fetch(`/api/recordings/${encodeURIComponent(id)}`, { method: 'DELETE' });
      const data = await response.json();
      if (!response.ok) {
        statusLabel.textContent = data.error || 'Delete failed';
        return;
      }
      if (selectedRecordingId === id) {
        clearRecording();
      }
      await refreshRecordings();
      statusLabel.textContent = 'Recording deleted';
    }

    function renderCases(steps) {
      caseCount.textContent = `${steps.length} steps`;
      if (!steps.length) {
        caseList.innerHTML = '<div class="empty">No test steps found.</div>';
        return;
      }

      caseList.innerHTML = steps.map((step, index) => {
        const label = step.description || step.expected || step.target || step.value || '-';
        return `
          <div class="case-row">
            <strong>${index + 1}</strong>
            <span>${escapeHtml(step.action)}</span>
            <span title="${escapeHtml(label)}">${escapeHtml(label)}</span>
          </div>
        `;
      }).join('');
    }

    function renderProgress(steps, status) {
      resetProgressState();
    }

    function upsertProgress(data) {
      if (isCompletedProgressStatus(data.status)) {
        progressItems.set(String(data.index), data);
        renderProgressList();
      }
      updateProgressTabCounts();
      updateProgressExportButton();
    }

    function resetProgressState() {
      progressItems = new Map();
      updateProgressTabCounts();
      renderProgressList();
      updateProgressExportButton();
    }

    function setProgressFilter(status) {
      activeProgressFilter = status;
      progressTabs.forEach((tab) => {
        const active = tab.dataset.progressFilter === status;
        tab.classList.toggle('active', active);
        tab.setAttribute('aria-selected', active ? 'true' : 'false');
      });
      renderProgressList();
    }

    function isCompletedProgressStatus(status) {
      return ['passed', 'failed', 'blocked', 'stopped'].includes(status);
    }

    function progressCounts() {
      const counts = { passed: 0, failed: 0, blocked: 0 };
      for (const item of progressItems.values()) {
        if (counts[item.status] !== undefined) {
          counts[item.status] += 1;
        }
      }
      return counts;
    }

    function updateProgressTabCounts() {
      const counts = progressCounts();
      for (const [status, element] of Object.entries(progressCountEls)) {
        if (element) {
          element.textContent = counts[status] || 0;
        }
      }
    }

    function updateProgressExportButton() {
      progressExportButton.disabled = progressItems.size === 0;
    }

    function updateRunSummary(index, total) {
      const counts = progressCounts();
      runSummary.textContent = `${index} / ${total} - ${counts.passed} passed / ${counts.failed} failed / ${counts.blocked} blocked`;
    }

    function renderProgressList() {
      const rows = Array.from(progressItems.values())
        .filter((item) => item.status === activeProgressFilter || (activeProgressFilter === 'failed' && item.status === 'stopped'))
        .sort((left, right) => left.index - right.index);

      if (!rows.length) {
        progressList.innerHTML = `<div class="empty">No ${progressFilterLabel(activeProgressFilter)} items yet.</div>`;
        return;
      }

      progressList.innerHTML = rows.map((item) => progressRow(item)).join('');
      const lastRow = progressList.querySelector('.progress-row:last-child');
      if (lastRow) {
        lastRow.scrollIntoView({ block: 'nearest' });
      }
    }

    function progressFilterLabel(status) {
      if (status === 'passed') return 'Pass';
      if (status === 'failed') return 'Fail';
      if (status === 'stopped') return 'Stopped';
      return 'Block(N/A)';
    }

    function progressRow(data) {
      const step = data.step || {};
      const label = step.description || step.expected || step.target || step.value || '-';
      const screenshot = data.extra && data.extra.screenshot ? ` ??screenshot: ${data.extra.screenshot}` : '';
      const message = `${data.message || label}${screenshot}`;
      const screenshotUrl = data.extra && data.extra.screenshot_url ? data.extra.screenshot_url : '';
      const screenshotLink = screenshotUrl
        ? ` <a class="result-link" href="${escapeHtml(screenshotUrl)}" target="_blank" rel="noopener noreferrer">Open</a>`
        : '';
      return `
        <div class="progress-row" id="progress-row-${escapeHtml(data.status)}-${data.index}">
          <strong>${data.index}/${data.total}</strong>
          <span class="badge ${escapeHtml(data.status)}">${escapeHtml(data.status)}</span>
          <span title="${escapeHtml(label)}">${escapeHtml(step.action || '-')} - ${escapeHtml(label)}</span>
          <span title="${escapeHtml(message)}">${escapeHtml(message)}${screenshotLink}</span>
        </div>
      `;
    }

    function exportProgressCsv() {
      const rows = Array.from(progressItems.values()).sort((left, right) => left.index - right.index);
      if (!rows.length) {
        statusLabel.textContent = 'No progress results to export.';
        return;
      }

      const headers = [
        'index',
        'total',
        'status',
        'action',
        'description',
        'message',
        'source',
        'check_type',
        'excel_row',
        'target',
        'expected',
        'elapsed_ms',
        'screenshot',
        'screenshot_url'
      ];
      const csvRows = [headers];
      rows.forEach((item) => {
        const step = item.step || {};
        csvRows.push([
          item.index,
          item.total,
          item.status,
          step.action || '',
          step.description || '',
          item.message || '',
          step.source || '',
          step.check_type || '',
          step.row || '',
          step.target || '',
          step.expected || '',
          item.elapsed_ms || 0,
          item.extra && item.extra.screenshot ? item.extra.screenshot : '',
          item.extra && item.extra.screenshot_url ? item.extra.screenshot_url : ''
        ]);
      });

      const csv = csvRows.map((row) => row.map(csvCell).join(',')).join('\r\n');
      const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      const stamp = new Date().toISOString().replace(/[:.]/g, '-');
      link.href = url;
      link.download = `qa-progress-${stamp}.csv`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      statusLabel.textContent = `Exported ${rows.length} progress results`;
    }

    function csvCell(value) {
      const text = String(value ?? '');
      if (/[",\r\n]/.test(text)) {
        return `"${text.replace(/"/g, '""')}"`;
      }
      return text;
    }

    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>"']/g, (char) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
      }[char]));
    }

    function setupProgressResize() {
      applyProgressHeight(readProgressHeight());

      resizeHandle.addEventListener('pointerdown', (event) => {
        startProgressResize(event.clientY);
        resizeHandle.setPointerCapture(event.pointerId);
        event.preventDefault();
      });

      resizeHandle.addEventListener('pointermove', (event) => {
        moveProgressResize(event.clientY);
      });

      resizeHandle.addEventListener('pointerup', endResize);
      resizeHandle.addEventListener('pointercancel', endResize);
      resizeHandle.addEventListener('mousedown', (event) => {
        startProgressResize(event.clientY);
        event.preventDefault();
      });
      document.addEventListener('mousemove', (event) => moveProgressResize(event.clientY));
      document.addEventListener('mouseup', endResize);
      resizeHandle.addEventListener('touchstart', (event) => {
        const touch = event.touches[0];
        if (touch) {
          startProgressResize(touch.clientY);
        }
        event.preventDefault();
      }, { passive: false });
      document.addEventListener('touchmove', (event) => {
        const touch = event.touches[0];
        if (touch) {
          moveProgressResize(touch.clientY);
        }
      }, { passive: false });
      document.addEventListener('touchend', endResize);
      resizeHandle.addEventListener('dblclick', () => applyProgressHeight(defaultProgressHeight));
      window.addEventListener('resize', () => {
        applyProgressHeight(readProgressHeight());
        fitScreenToStage();
      });
      if ('ResizeObserver' in window) {
        const observer = new ResizeObserver(() => fitScreenToStage());
        observer.observe(mirrorStage);
      }
    }

    function startProgressResize(clientY) {
      if (isResizingProgress) {
        return;
      }
      isResizingProgress = true;
      resizeStartY = clientY;
      resizeStartHeight = readProgressHeight();
      resizeHandle.classList.add('dragging');
      document.body.style.cursor = 'row-resize';
      document.body.style.userSelect = 'none';
    }

    function moveProgressResize(clientY) {
      if (!isResizingProgress) {
        return;
      }
      const nextHeight = resizeStartHeight + (resizeStartY - clientY);
      applyProgressHeight(nextHeight);
    }

    function endResize(event = {}) {
      if (event.pointerId !== undefined && resizeHandle.hasPointerCapture(event.pointerId)) {
        resizeHandle.releasePointerCapture(event.pointerId);
      }
      isResizingProgress = false;
      resizeHandle.classList.remove('dragging');
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    }

    function readProgressHeight() {
      return progressPanel.getBoundingClientRect().height || defaultProgressHeight;
    }

    function applyProgressHeight(nextHeight) {
      const paneHeight = mirrorPane.getBoundingClientRect().height;
      const headerHeight = mirrorHeader.getBoundingClientRect().height;
      const handleHeight = resizeHandle.getBoundingClientRect().height;
      const bottomMargin = 16;
      const availableHeight = Math.max(0, paneHeight - headerHeight - handleHeight - bottomMargin);
      const responsiveMinProgress = Math.min(minProgressHeight, Math.max(96, availableHeight * 0.45));
      const maxProgressHeight = Math.max(
        responsiveMinProgress,
        Math.floor(availableHeight - minMirrorStageHeight)
      );
      const clampedHeight = Math.max(responsiveMinProgress, Math.min(nextHeight, maxProgressHeight));

      mirrorPane.style.setProperty('--progress-height', `${Math.round(clampedHeight)}px`);
      mirrorStage.style.minHeight = '0px';
      fitScreenToStage();
    }

    function fitScreenToStage() {
      if (!screen.naturalWidth || !screen.naturalHeight) {
        return;
      }

      const stageRect = mirrorStage.getBoundingClientRect();
      const style = getComputedStyle(mirrorStage);
      const px = (value) => Number(String(value).replace('px', '')) || 0;
      const paddingX = px(style.paddingLeft) + px(style.paddingRight);
      const paddingY = px(style.paddingTop) + px(style.paddingBottom);
      const availableWidth = Math.max(0, stageRect.width - paddingX - 2);
      const availableHeight = Math.max(0, stageRect.height - paddingY - 2);
      if (!availableWidth || !availableHeight) {
        return;
      }

      const scale = Math.min(
        availableWidth / screen.naturalWidth,
        availableHeight / screen.naturalHeight
      );
      const fittedWidth = Math.max(1, Math.floor(screen.naturalWidth * scale));
      const fittedHeight = Math.max(1, Math.floor(screen.naturalHeight * scale));

      screen.style.width = `${fittedWidth}px`;
      screen.style.height = `${fittedHeight}px`;
      screen.classList.toggle('portrait', screen.naturalHeight > screen.naturalWidth);
      screen.classList.toggle('landscape', screen.naturalWidth >= screen.naturalHeight);
    }
  </script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/favicon.ico")
def favicon():
    return Response(status=204)


@app.route("/api/device")
def device_info():
    return jsonify(get_device_info())


@app.route("/api/ocr/status")
def ocr_status():
    return jsonify(ocr_status_payload())


@app.route("/api/ai/status")
def ai_status():
    return jsonify(ai_status_payload())


@app.route("/api/results/screenshot/<path:filename>")
def result_screenshot(filename):
    safe_name = Path(filename).name
    if safe_name != filename or not re.fullmatch(r"[A-Za-z0-9_.-]+\.png", safe_name):
        return jsonify({"error": "Invalid screenshot filename."}), 400

    path = qa_results_dir() / safe_name
    if not path.exists():
        return jsonify({"error": "Screenshot not found."}), 404

    return send_from_directory(qa_results_dir(), safe_name, mimetype="image/png")


@app.route("/api/testcase", methods=["GET", "POST"])
def upload_testcase():
    global _test_steps, _test_metadata

    if request.method == "GET":
        with _test_lock:
            steps = list(_test_steps)
            metadata = dict(_test_metadata)
        return jsonify(
            {
                "steps": steps,
                "count": len(steps),
                "source": testcase_source(steps),
                "metadata": metadata,
                "recorder_resolution": recorder_resolution_status(steps),
            }
        )

    file_storage = request.files.get("file")
    if not file_storage:
        return jsonify({"error": "No file uploaded."}), 400

    filename = file_storage.filename or ""
    if not re.search(r"\.xls[xm]$", filename, re.IGNORECASE):
        return jsonify({"error": "Upload an .xlsx or .xlsm file."}), 400

    try:
        file_storage.stream.seek(0)
        file_payload = file_storage.stream.read()
        file_storage.stream.seek(0)
        steps = parse_excel(file_storage)
    except Exception as exc:
        with _test_lock:
            _test_steps = []
            _test_metadata = {}
        return jsonify({"error": str(exc)}), 400

    source = testcase_source(steps)
    metadata = {
        "filename": filename,
        "file_hash": hashlib.sha256(file_payload).hexdigest(),
        "source": source,
        "count": len(steps),
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
    }
    with _test_lock:
        _test_steps = steps
        _test_metadata = metadata

    return jsonify(
        {
            "steps": steps,
            "count": len(steps),
            "source": source,
            "metadata": metadata,
            "recorder_resolution": recorder_resolution_status(steps),
        }
    )


@app.route("/api/run", methods=["POST"])
def run_testcase():
    global _test_running

    payload = request.get_json(silent=True) or {}
    ai_mode = bool(payload.get("ai_mode", False))
    with _test_lock:
        if not _test_steps:
            return jsonify({"error": "Upload a test case first."}), 400
        if _test_running:
            return jsonify({"error": "A test case is already running."}), 409
        steps = list(_test_steps)

    if not check_device():
        return jsonify({"error": "No adb device. Check USB connection and USB debugging."}), 409

    try:
        validate_recorder_resolution(steps)
    except RecorderResolutionMismatch as exc:
        return jsonify(
            {
                "error": str(exc),
                "code": "recorder_resolution_mismatch",
                "recorder_resolution": exc.status,
            }
        ), 409
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 409

    with _test_lock:
        if _test_running:
            return jsonify({"error": "A test case is already running."}), 409
        _test_running = True
        _test_stop_event.clear()

    threading.Thread(target=run_test_steps, kwargs={"ai_mode": ai_mode}, daemon=True).start()
    return jsonify({"status": "started", "mode": "ai" if ai_mode else "standard"})


@app.route("/api/run/stop", methods=["POST"])
def stop_testcase():
    _test_stop_event.set()
    return jsonify({"status": "stopping"})


@app.route("/api/input", methods=["POST"])
def manual_input():
    payload = request.get_json(silent=True) or {}
    try:
        result = run_manual_input(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify(result)


@app.route("/api/recordings", methods=["GET", "POST"])
def recordings_api():
    if request.method == "GET":
        return jsonify({"recordings": list_recordings()})

    payload = request.get_json(silent=True) or {}
    try:
        data = write_recording(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify(data)


@app.route("/api/recordings/<recording_id>", methods=["GET", "DELETE"])
def recording_detail(recording_id):
    try:
        if request.method == "GET":
            return jsonify(read_recording(recording_id))

        path = recording_path(recording_id)
        if not path.exists():
            return jsonify({"error": "Recording not found."}), 404
        path.unlink()
        export_path = recording_export_path(recording_id)
        warning = None
        if export_path.exists():
            try:
                export_path.unlink()
            except PermissionError:
                warning = "Export file is still in use and will remain until it can be deleted."
        response = {"status": "deleted"}
        if warning:
            response["warning"] = warning
        return jsonify(response)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404


@app.route("/api/recordings/<recording_id>/export")
def recording_export(recording_id):
    try:
        recording = read_recording(recording_id)
        path = export_recording_excel(recording)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500
    return send_file(path, as_attachment=True, download_name=path.name)


@app.route("/api/recordings/<recording_id>/play", methods=["POST"])
def recording_play(recording_id):
    global _record_play_running

    payload = request.get_json(silent=True) or {}
    try:
        recording = read_recording(recording_id)
        current_width = payload_int(payload, "screen_width", minimum=1, maximum=10000)
        current_height = payload_int(payload, "screen_height", minimum=1, maximum=10000)
        repeat_count = payload_int(payload, "repeat_count", 1, minimum=1, maximum=10)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404

    if current_width != int(recording.get("screen_width") or 0) or current_height != int(recording.get("screen_height") or 0):
        return jsonify({"error": "Current screen resolution does not match this recording."}), 409

    with _record_play_lock:
        if _record_play_running:
            return jsonify({"error": "Recorder playback is already running."}), 409
        _record_play_running = True
        _record_play_stop_event.clear()

    threading.Thread(target=play_recording_steps, args=(recording, repeat_count), daemon=True).start()
    return jsonify({"status": "started", "id": recording["id"], "repeat_count": repeat_count})


@app.route("/api/recordings/play/stop", methods=["POST"])
def recording_play_stop():
    _record_play_stop_event.set()
    return jsonify({"status": "stopping"})


@app.route("/api/random/plan", methods=["GET"])
def random_plan():
    max_steps = int_value(request.args.get("steps"), 10)
    max_steps = max(1, min(max_steps, 50))
    plan = build_random_plan(max_steps=max_steps)
    return jsonify({"steps": [item["step"] for item in plan], "plan": plan, "count": len(plan)})


@app.route("/api/random/run", methods=["POST"])
def random_run():
    global _random_running

    payload = request.get_json(silent=True) or {}
    max_steps = int_value(payload.get("steps"), 20)
    delay_ms = int_value(payload.get("delay_ms"), 800)
    dry_run = bool(payload.get("dry_run", False))
    ai_mode = bool(payload.get("ai_mode", False))
    try:
        memory_actions = normalize_random_memory_actions(payload.get("memory_actions") or [])
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    max_steps = max(1, min(max_steps, 200))
    delay_ms = max(0, min(delay_ms, 10000))

    with _random_lock:
        if _random_running:
            return jsonify({"error": "Random UI exploration is already running."}), 409
        _random_running = True
        _random_stop_event.clear()

    threading.Thread(
        target=run_random_exploration,
        kwargs={
            "max_steps": max_steps,
            "delay_ms": delay_ms,
            "dry_run": dry_run,
            "memory_actions": memory_actions,
            "ai_mode": ai_mode,
        },
        daemon=True,
    ).start()
    return jsonify({
        "status": "started",
        "dry_run": dry_run,
        "ai_mode": ai_mode,
        "steps": max_steps,
        "memory_actions": len(memory_actions),
    })


@app.route("/api/random/stop", methods=["POST"])
def random_stop():
    _random_stop_event.set()
    return jsonify({"status": "stopping"})


@socketio.on("start", namespace="/mirror")
def on_start(data=None):
    global _streaming

    with _mirror_lock:
        if _streaming:
            return

        if not check_device():
            emit(
                "mirror_error",
                {"msg": "No adb device. Check USB connection and USB debugging."},
            )
            return

        _streaming = True
        threading.Thread(target=stream_loop, daemon=True).start()


@socketio.on("stop", namespace="/mirror")
def on_stop(data=None):
    global _streaming
    _streaming = False


if __name__ == "__main__":
    print("=" * 55)
    print("Tablet test browser server")
    print("Open http://localhost:5000")
    print("=" * 55)
    socketio.run(
        app,
        host="0.0.0.0",
        port=5000,
        debug=False,
        allow_unsafe_werkzeug=True,
    )
