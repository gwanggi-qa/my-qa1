"""Local, operator-reviewed inspection runs. No autonomous execution or recovery.

The model may propose labels only. Approval freezes the original TC/plan. Each
input additionally needs a fresh local screenshot and an operator-selected point.
Verdicts are explicitly operator-reviewed, never advertised as AI verification.
"""
import copy
import hashlib
import io
import json
import re
import threading
import time
import uuid
from datetime import datetime

from PIL import Image
import ai_tc
import result_workbook
import ui_guard
import execution_results


RISK = re.compile(r"삭제|초기화|탈퇴|로그아웃|결제|구매|저장|변경|업데이트|학습시작|학습 시작|권한|clear|delete|reset|logout|purchase|payment|save|install|permission", re.I)
TARGETS = ("도움말", "환경설정", "제품정보")
PROCEDURES = {
    "메인화면에서도움말버튼을클릭한다.": "도움말",
    "메인화면에서환경설정버튼을클릭한다.": "환경설정",
    "제품정보탭을선택한다.": "제품정보",
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def case_key(step):
    return {"sheet": step.get("sheet"), "row": step.get("row"), "case_no": str(step.get("case_no") or "")}


def selected_rows(steps, selection):
    if not isinstance(selection, list) or not 1 <= len(selection) <= 20:
        raise ValueError("원본 시트·행·TC ID로 1~20개 TC를 명시적으로 선택하세요.")
    keys = [case_key(step) for step in steps]
    if any(not isinstance(item, dict) or set(item) != {"sheet", "row", "case_no"} for item in selection):
        raise ValueError("선택 TC 키가 올바르지 않습니다.")
    indexes = []
    for item in selection:
        matches = [i+1 for i, key in enumerate(keys) if key == item]
        if len(matches) != 1 or matches[0] in indexes:
            raise ValueError("선택한 TC가 없거나 중복·모호합니다.")
        indexes.append(matches[0])
    return indexes


def make_case(index, step, proposed=None):
    source = str(step.get("test_item") or step.get("description") or "")
    expected = ai_tc.expected_text(step)
    precondition = str(step.get("precondition") or "")
    targets = [label for label in TARGETS if label in source]
    reason = ""
    if RISK.search(source + " " + precondition):
        reason = "원문에 계정·데이터·설정 변경 또는 위험 동작이 포함되어 있습니다."
    elif len(targets) != 1 or not expected or PROCEDURES.get(re.sub(r"\s+", "", source)) != targets[0]:
        reason = "현재 검토 실행은 도움말·환경설정 열기·제품정보 표시 검사만 지원합니다."
    actions = []
    target = targets[0] if len(targets) == 1 else ""
    if not reason:
        actions = [{"action": "tap", "target": target, "source": source}]
    if proposed is not None and not reason:
        # Only this complete, single navigation procedure is supported. No hidden
        # coordinates, extra inputs, waits, recovery, or model-authored verdicts.
        if not isinstance(proposed, list) or len(proposed) != 1:
            reason = "AI 계획의 누락/추가 절차는 검토 실행에서 허용하지 않습니다."
        else:
            action = proposed[0]
            if (not isinstance(action, dict) or set(action) != {"action", "target", "source"}
                    or action != actions[0] or RISK.search(action.get("target", ""))):
                reason = "AI 계획이 원문·허용 대상과 일치하지 않아 차단했습니다."
    if reason:
        actions = []
    requirements = [{"id": "whole", "source": expected}]
    facets = {"환경설정": ("진행속도", "음성인식난이도", "튜토리얼"),
              "제품정보": ("버전명", "업데이트", "저작권")}.get(target, ())
    for label in facets:
        if label in expected:
            requirements.append({"id": f"condition-{len(requirements)}", "source": label})
    return {"index": index, **case_key(step), "procedure": source, "precondition": precondition,
            "start_state": "환경설정 화면" if target == "제품정보" else "메인 화면",
            "expected": expected, "requirements": requirements, "actions": actions, "blocked_reason": reason}


class ReviewedRun:
    def __init__(self, host, template, metadata, steps, selection, proposals=None):
        self.host = host
        self.template = template
        self.metadata = copy.deepcopy(metadata)
        self.steps = copy.deepcopy(steps)
        self.selected = selected_rows(steps, selection)
        self.cases = [make_case(i, steps[i-1], None if proposals is None else proposals.get(i, [])) for i in self.selected]
        self.plan_id = uuid.uuid4().hex
        self.plan_digest = digest(self.cases)
        self.run_id = uuid.uuid4().hex
        self.approved = False
        self.finished = False
        self.stopped = False
        self.position = 0
        self.action_cursor = 0
        self.observation = None
        self.trace = []
        self.preparations = []
        self.results = []
        self.input_error = ""
        self.lock = threading.RLock()

    def guard(self, active=False):
        if self.host._test_workbook is not self.template or self.host._test_metadata.get("upload_id") != self.metadata.get("upload_id"):
            raise ValueError("원본 업로드가 교체되어 계획이 만료되었습니다.")
        if digest(self.cases) != self.plan_digest:
            raise ValueError("검토한 계획이 변경되었습니다. 실행을 차단합니다.")
        if active and (not self.approved or self.finished or self.host._test_stop_event.is_set()):
            raise ValueError("승인되지 않았거나 종료/중단된 실행입니다.")

    def public(self):
        return {"plan_id": self.plan_id, "plan_digest": self.plan_digest, "run_id": self.run_id,
                "file_hash": self.template.file_hash, "cases": copy.deepcopy(self.cases),
                "approved": self.approved, "finished": self.finished, "position": self.position,
                "action_cursor": self.action_cursor, "results": copy.deepcopy(self.results),
                "verification_method": "operator-reviewed-local", "input_error": self.input_error}

    def approve(self, payload):
        with self.lock:
            self.guard()
            if self.approved or self.finished or payload != {"plan_digest": self.plan_digest, "reviewed": True}:
                raise ValueError("원문과 전체 계획을 확인한 뒤 정확한 계획 해시를 승인하세요.")
            self.approved = True
            self.host._test_running = True
            self.host._test_stop_event.clear()
            self.host._last_workbook_run = None
            self.host.socketio.emit("test_run_started", {"total": len(self.cases), "mode": "reviewed",
                "run_id": self.run_id, "file_hash": self.template.file_hash}, namespace="/mirror")
            return self.public()

    def observe(self):
        with self.lock:
            self.guard(active=True)
            raw = self.host.capture_screen_png()
            with Image.open(io.BytesIO(raw)) as image:
                width, height = image.size
            xml = ""
            if hasattr(self.host, "dump_ui_xml"):
                try:
                    xml = self.host.dump_ui_xml()
                except Exception:
                    pass  # Observation may be viewed; execution requires a valid target hierarchy.
            token = uuid.uuid4().hex
            path = self.host.QA_RESULTS_DIR / f"reviewed-{self.run_id}-{token}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            self.observation = {"_png": raw, "_xml": xml, "token": token, "sha256": hashlib.sha256(raw).hexdigest(),
                "width": width, "height": height, "position": self.position, "cursor": self.action_cursor,
                "monotonic": time.monotonic(), "screenshot": str(path), "screenshot_file": path.name,
                "screenshot_url": self.host.qa_result_screenshot_url(path)}
            return {k: v for k, v in self.observation.items() if k != "monotonic" and not k.startswith("_")}

    def _observation(self, token):
        value = self.observation
        if (not value or value["token"] != token or value["position"] != self.position
                or value["cursor"] != self.action_cursor or time.monotonic() - value["monotonic"] > 180):
            raise ValueError("현재 단계의 새 화면을 먼저 관찰하세요.")
        return value

    def execute(self, payload):
        with self.lock:
            self.guard(active=True)
            required = {"observation_token", "x", "y", "target_confirmed", "prerequisites_confirmed", "prerequisite_evidence"}
            if set(payload) != required or payload["target_confirmed"] is not True or payload["prerequisites_confirmed"] is not True:
                raise ValueError("화면의 대상과 시작 상태를 명시적으로 확인해야 합니다.")
            if not str(payload["prerequisite_evidence"]).strip():
                raise ValueError("시작 상태·전제조건의 관찰 근거가 필요합니다.")
            case = self.cases[self.position]
            if case["blocked_reason"] or self.input_error or self.action_cursor >= len(case["actions"]):
                raise ValueError("차단됐거나 더 이상 승인된 동작이 없습니다.")
            action = case["actions"][self.action_cursor]
            if action["action"] != "tap" or action["target"] not in TARGETS or RISK.search(action["source"]):
                raise ValueError("승인된 안전한 화면 이동 동작이 아닙니다.")
            observation = self._observation(payload["observation_token"])
            x, y = payload["x"], payload["y"]
            if type(x) is not int or type(y) is not int or not (0 <= x < observation["width"] and 0 <= y < observation["height"] * .92):
                raise ValueError("현재 앱 화면 안의 좌표만 허용합니다.")
            with self.host._adb_input_lock:
                self.guard(active=True)
                current = self.host.capture_screen_png()
                selector = {"text": action["target"]}
                if not observation.get("_xml") or not hasattr(self.host, "dump_ui_xml"):
                    raise ValueError("승인 대상을 확인할 UI 근거가 없습니다. 입력을 차단합니다.")
                fresh_xml = self.host.dump_ui_xml()
                ui_guard.unchanged(observation["_xml"], observation["_png"], fresh_xml, current, selector)
                box = ui_guard.bounds(ui_guard.select(fresh_xml, selector))
                if not (box[0] <= x < box[2] and box[1] <= y < box[3]):
                    raise ValueError("검토 좌표가 확인된 대상 요소 밖에 있습니다.")
                # Consume before input: an uncertain ADB outcome may never be retried blindly.
                self.observation = None
                self.action_cursor += 1
                item = {**action, "x": x, "y": y, "before": observation["screenshot_file"],
                        "before_sha256": observation["sha256"], "prerequisite_evidence": payload["prerequisite_evidence"],
                        "status": "attempted", "started_at": execution_results.now(),
                        "preconditions_verified": True, "expectations_verified": False}
                self.trace.append(item)
                try:
                    self.guard(active=True)
                    _, _, rc = self.host.adb("shell", "input", "tap", str(x), str(y), timeout_s=8)
                    if rc:
                        raise RuntimeError("ADB input outcome could not be confirmed")
                    item["status"] = "sent"
                    item["finished_at"] = execution_results.now()
                except Exception:
                    self.input_error = "입력 결과가 불확실합니다. 재실행하지 말고 Block으로 기록하세요."
                    raise
            return self.public()

    def prepare_back(self, payload):
        """Explicitly reviewed popup dismissal only; never credits a TC action."""
        with self.lock:
            self.guard(active=True)
            if (set(payload) != {"observation_token", "safe_dismissal_confirmed", "evidence"}
                    or payload["safe_dismissal_confirmed"] is not True
                    or not str(payload["evidence"]).strip()
                    or self.action_cursor or self.input_error
                    or sum(p["position"] == self.position for p in self.preparations) >= 2):
                raise ValueError("Only two explicitly reviewed Back preparations before a TC are supported.")
            observation = self._observation(payload["observation_token"])
            with self.host._adb_input_lock:
                self.guard(active=True)
                if hashlib.sha256(self.host.capture_screen_png()).hexdigest() != observation["sha256"]:
                    raise ValueError("Screen changed; capture and review again.")
                self.observation = None
                item = {"position": self.position, "action": "preparation_back", "evidence": payload["evidence"],
                        "before": observation["screenshot_file"], "before_sha256": observation["sha256"], "status": "attempted"}
                self.preparations.append(item)
                try:
                    self.guard(active=True)
                    _, _, rc = self.host.adb("shell", "input", "keyevent", "4", timeout_s=8)
                    if rc:
                        raise RuntimeError("Back outcome uncertain")
                    item["status"] = "sent"
                except Exception:
                    self.input_error = "Preparation outcome uncertain; no automatic retry."
                    raise
            return self.public()

    def verdict(self, payload):
        with self.lock:
            self.guard(active=True)
            if set(payload) != {"observation_token", "findings", "reason"}:
                raise ValueError("판정 요청 필드가 올바르지 않습니다.")
            case = self.cases[self.position]
            observation = self._observation(payload["observation_token"])
            findings = payload["findings"]
            expected_ids = {item["id"] for item in case["requirements"]}
            if (not isinstance(findings, list) or len(findings) != len(expected_ids)
                    or {f.get("id") for f in findings if isinstance(f, dict)} != expected_ids):
                raise ValueError("모든 원문 기대조건에 대한 판정이 필요합니다.")
            for finding in findings:
                if (set(finding) != {"id", "status", "evidence"} or finding["status"] not in {"met", "unmet", "unknown"}
                        or not str(finding["evidence"]).strip()):
                    raise ValueError("조건별 met/unmet/unknown과 실제 관찰 설명이 필요합니다.")
            procedures_done = self.action_cursor == len(case["actions"]) and bool(case["actions"]) and all(t["status"] == "sent" for t in self.trace)
            statuses = {f["status"] for f in findings}
            if case["blocked_reason"] or self.input_error or not procedures_done:
                status = "blocked"
            else:
                status = "failed" if "unmet" in statuses else "blocked" if "unknown" in statuses else "passed"
            reason = str(payload["reason"]).strip()
            if status == "blocked" and not reason:
                raise ValueError("미검증 사유를 기록하세요.")
            for item in self.trace:
                item["input_status"] = item["status"]
                item["expected"] = copy.deepcopy(findings)
                item["actual"] = reason or "; ".join(f["evidence"] for f in findings)
                item["expectations_verified"] = all(f["status"] == "met" for f in findings)
                item["evidence"] = [{"file": item["before"]},
                                    {"file": observation["screenshot_file"], "url": observation["screenshot_url"]}]
                item["verification_finished_at"] = execution_results.now()
            result = {"index": case["index"], "status": status, "tc": case_key(case),
                      "message": reason or "; ".join(f["evidence"] for f in findings),
                      "verification_method": "operator-reviewed-local", "findings": copy.deepcopy(findings),
                      "procedure": case["procedure"], "expected": case["expected"],
                      "actions": copy.deepcopy(self.trace), "screenshot": observation["screenshot"],
                      "screenshot_file": observation["screenshot_file"], "screenshot_sha256": observation["sha256"]}
            self.results.append(result)
            self.host.socketio.emit("test_progress", {"index": self.position+1, "total": len(self.cases),
                "status": status, "step": self.steps[case["index"]-1], "message": "운영자 화면 검증: " + result["message"],
                "extra": {"screenshot_url": observation["screenshot_url"], "verification_method": "operator-reviewed-local"}}, namespace="/mirror")
            self.position += 1
            self.action_cursor = 0
            self.observation = None
            self.trace = []
            self.input_error = ""
            if self.position == len(self.cases):
                self.finish()
            return self.public()

    def finish(self, stopped=False):
        with self.lock:
            if self.finished:
                return
            self.guard()
            completed = {r["index"] for r in self.results}
            for position, case in enumerate(self.cases):
                if case["index"] not in completed:
                    self.results.append({"index": case["index"], "status": "stopped" if position == self.position else "not_run",
                        "tc": case_key(case), "message": "검토 실행 중단: 판정 미완료", "verification_method": "operator-reviewed-local"})
            snapshot = result_workbook.finish_run(self.run_id, self.template, self.steps, self.results, selected_indices=self.selected)
            self.host.append_test_run_history({"run_id": self.run_id, "created_at": datetime.now().isoformat(timespec="seconds"),
                "file_hash": self.template.file_hash, "filename": self.template.filename, "mode": "reviewed",
                "upload_id": self.metadata.get("upload_id"), "steps": self.steps,
                "verification_method": "operator-reviewed-local", "plan_digest": self.plan_digest, "plan": self.cases,
                "selected_indices": self.selected, "results": self.results, "preparations": self.preparations, "stopped": stopped})
            self.host._last_workbook_run = snapshot
            self.finished = True
            self.stopped = bool(stopped)
            self.host._test_running = False
            self.host._test_stop_event.clear()
            counts = {s: sum(r["status"] == s for r in self.results) for s in ("passed", "failed", "blocked")}
            self.host.socketio.emit("test_run_finished", {"run_id": self.run_id, "total": len(self.cases), "mode": "reviewed",
                **counts, "stopped": stopped, "workbook_export": self.host.workbook_export_info(snapshot)}, namespace="/mirror")
