"""Bounded, evidence-based execution of AI-authored TC plans.

The model proposes labels and current-screen candidate IDs. Only the host
executes inputs; original Excel text remains the authority for test intent.
"""
import json
import re
import narrative_excel


class Stopped(Exception):
    pass


def obj(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


STR = {"type": "string"}
STRINGS = {"type": "array", "items": STR}
CHECK = obj({"source": STR, "text": STR, "present": {"type": "boolean"}})
ACTION = obj({"action": {"type": "string", "enum": ["tap", "swipe", "input", "wait", "back"]},
              "target": STR, "value": STR, "direction": STR,
              "wait_ms": {"type": "integer"}, "source": STR})
HINT = obj({"index": {"type": "integer"}, "intent": STR,
            "preconditions": {"type": "array", "items": CHECK},
            "actions": {"type": "array", "items": ACTION, "maxItems": 20},
            "checks": {"type": "array", "items": CHECK}, "unsupported_reason": STR})
BATCH_SCHEMA = obj({"hints": {"type": "array", "items": HINT}})
OVERVIEW_SCHEMA = obj({"summary": STR, "risks": STRINGS, "execution_notes": STRINGS})
RECOVERY_SCHEMA = obj({"decision": {"type": "string", "enum": ["execute", "blocked", "fail"]},
                       "action": {"type": "string", "enum": ["tap", "swipe", "input", "wait", "back"]},
                       "candidate_id": STR, "reason": STR, "wait_ms": {"type": "integer"}})


def validate(data, schema):
    """Validate the small JSON-schema subset shared with both model providers."""
    kind = schema["type"]
    matches = {"object": isinstance(data, dict), "array": isinstance(data, list),
               "string": isinstance(data, str), "boolean": type(data) is bool,
               "integer": type(data) is int}
    if not matches[kind]:
        raise ValueError(f"AI response needs {kind}")
    if "enum" in schema and data not in schema["enum"]:
        raise ValueError("AI response contains unsupported value")
    if kind == "object":
        if set(data) != set(schema["properties"]):
            raise ValueError("AI response has missing or unexpected fields")
        for key, value in data.items():
            validate(value, schema["properties"][key])
    elif kind == "array":
        if len(data) > schema.get("maxItems", 1000):
            raise ValueError("AI response has too many items")
        for value in data:
            validate(value, schema["items"])


def compact(text):
    return re.sub(r"\s+", "", str(text or "")).casefold()


def expected_text(step):
    return str(step.get("qa_expected") or step.get("expected") or
               (step.get("target") if step.get("action") in {"assert_text", "assert_not_text"} else "") or "")


def allows_verification_scroll(step):
    return (step.get("source") == "narrative_qa" and bool(step.get("required_labels"))
            and not step.get("verification_gap") and bool(re.search(r"스크롤|scroll", expected_text(step), re.I)))


def validate_hint(hint, step):
    validate(hint, HINT)
    source = " ".join(str(step.get(key) or "") for key in
                      ("test_item", "description", "target", "value", "precondition", "test_purpose"))
    expected = expected_text(step)
    narrative = step.get("source") == "narrative_qa"
    purpose = str(step.get("test_purpose") or "") if narrative else ""
    for action in hint["actions"]:
        action_source = source + (" " + expected if narrative and action["action"] in {"swipe", "wait"} else "")
        if not action["source"] or compact(action["source"]) not in compact(action_source):
            raise ValueError("AI action is not grounded in the Excel procedure")
        if action["action"] == "input" and (
            not action["value"] or action["value"] not in source
        ):
            raise ValueError("AI invented an input value")
        if action["action"] == "swipe" and action["direction"] not in {"up", "down", "left", "right"}:
            raise ValueError("AI swipe needs a direction")
        if action["action"] in {"tap", "input"} and not action["target"].strip():
            raise ValueError("AI action needs a label")
        if not 0 <= action["wait_ms"] <= 5000:
            raise ValueError("AI wait is outside the allowed range")
    visual = narrative_excel.needs_visual_verification(step)
    if visual and not hint["unsupported_reason"] and step.get("action") in {"tap", "swipe", "input", "back"} and not hint["actions"]:
        raise ValueError("AI omitted the original procedure")
    for checks, original in [(hint["checks"], expected + "\n" + purpose),
                              (hint["preconditions"], str(step.get("precondition") or ""))]:
        for check in checks:
            if not check["source"] or compact(check["source"]) not in compact(original):
                raise ValueError("AI check is not grounded in the Excel text")
            if not check["text"] or compact(check["text"]) not in compact(check["source"]):
                raise ValueError("AI invented verification text")
    # Every expected clause must be represented. Missing coverage never becomes Pass.
    if not hint["unsupported_reason"] and not visual:
        clauses = [compact(s) for s in re.split(r"[\n;]+|\s+및\s+|\s+그리고\s+|\s+and\s+", expected) if s.strip()]
        coverage = [compact(c["source"]) for c in hint["checks"]]
        labels_required = step.get("required_labels", []) if narrative else []
        expanded_reference = bool(labels_required) and all(
            any(compact(c["text"]) == compact(label) and c["present"] and
                compact(c["source"]) == compact(purpose) for c in hint["checks"])
            for label in labels_required
        )
        if labels_required and not expanded_reference:
            raise ValueError("AI omitted mandatory labels from the test purpose")
        reference_only = bool(re.fullmatch(r"\d+종\s*모두\s*존재(?:\([^)]*\))?[.!]?", expected.strip()))
        if not clauses or (not (expanded_reference and reference_only) and any(c not in coverage for c in clauses)):
            raise ValueError("AI checks do not cover the original expected result")
        if purpose and not expanded_reference:
            purpose_clauses = [compact(s) for s in re.split(r"[\n;]+|\s+및\s+|\s+그리고\s+|\s+and\s+", purpose) if s.strip()]
            if any(c not in coverage for c in purpose_clauses):
                raise ValueError("AI checks do not cover the original test purpose")
        labels = re.findall(r'["\u201c]([^"\u201d]+)["\u201d]', expected + "\n" + purpose)
        for label in labels:
            if not any(compact(label) in compact(c["text"]) for c in hint["checks"]):
                raise ValueError("AI omitted an explicitly quoted expected label")
        for c in hint["checks"]:
            if re.search(r"not displayed|not visible|사라|표시되지|노출되지|없어", c["source"], re.I) and c["present"]:
                raise ValueError("AI reversed an absence condition")
        if step.get("action") in {"tap", "swipe", "input", "back"} and not hint["actions"]:
            raise ValueError("AI omitted the original procedure")
    return hint


def hard_block_reason(m, step):
    source = " ".join(str(step.get(k) or "") for k in
                      ("precondition", "test_item", "description", "block_reason", "value", "expected", "qa_expected", "test_purpose"))
    if m.ai_test_step_is_risky(step):
        return "This TC requires a restricted account/environment operation."
    if step.get("verification_gap") and not narrative_excel.needs_visual_verification(step):
        return step["verification_gap"]
    if re.search(r"음성|녹음|마이크|데이터\s*동기화|voice|microphone", source, re.I):
        return "This TC needs data, audio or visual verification that is not supported."
    if step.get("action") == "blocked":
        if step.get("source") == "manual_qa" and step.get("visual_inspection"):
            return ""
        if step.get("source") == "narrative_qa" and step.get("ai_pending"):
            return ""
        reason = str(step.get("block_reason") or "")
        recoverable = re.search(r"ambiguous|no clear clickable|no actionable|sentence|모호|대상.*불명확", reason, re.I)
        if not recoverable:
            return reason or "Excel explicitly blocks this TC."
    return ""


def check_stop(m):
    if m._test_stop_event.is_set():
        raise Stopped("Test Case stopped")


def observe(m, checks):
    check_stop(m)
    xml = m.dump_ui_xml()
    values = []
    if xml:
        try:
            for node in m.ET.fromstring(xml).iter("node"):
                values.extend([node.get("text", ""), node.get("content-desc", "")])
        except m.ET.ParseError:
            pass
    def visible(text):
        return any(m.text_value_matches(text, value) for value in values if value)
    # An absent-text assertion requires a valid UI observation, never a capture failure.
    need_ocr = any(not visible(c["text"]) for c in checks)
    if need_ocr:
        values.extend(item["text"] for item in m.ocr_screen_texts())
    check_stop(m)
    if not any(values):
        raise m.BlockedStep("No readable UI/OCR evidence to verify this TC")
    return [c for c in checks if visible(c["text"]) != c["present"]]


def verify(m, checks):
    if not checks:
        raise m.BlockedStep("No supported expected-result checks for this TC")
    missing = observe(m, checks)
    if missing:
        if m._test_stop_event.wait(0.5):
            raise Stopped("Test Case stopped")
        missing = observe(m, checks)
    if missing:
        raise AssertionError("Expected conditions not satisfied: " + "; ".join(
            ("visible: " if c["present"] else "absent: ") + c["text"] for c in missing))
    return "; ".join(("visible: " if c["present"] else "absent: ") + c["text"] for c in checks)


def candidate_signature(candidate):
    return (candidate.get("label"), candidate.get("action"),
            json.dumps(candidate.get("bounds"), sort_keys=True), candidate.get("source"))


def current_candidate(m, candidate):
    check_stop(m)
    fresh, _ = m.ai_test_screen_candidates()
    found = [c for c in fresh if candidate_signature(c) == candidate_signature(candidate)]
    if len(found) != 1:
        raise RuntimeError("Screen candidate changed or is ambiguous; observe again")
    return found[0]


def execute_action(m, action, candidate=None):
    check_stop(m)
    kind = action["action"]
    if kind in {"tap", "input", "swipe"}:
        if candidate is None:
            candidates, _ = m.ai_test_screen_candidates()
            matches = [c for c in candidates if c.get("action") == ("tap" if kind == "input" else kind)
                       and m.text_value_matches(action["target"], c.get("label", ""))]
            if len(matches) != 1:
                raise RuntimeError("Target missing or ambiguous: " + action["target"])
            candidate = matches[0]
        candidate = current_candidate(m, candidate)
        if candidate.get("action") != ("tap" if kind == "input" else kind):
            raise ValueError("Candidate does not support requested action")
        if kind == "swipe":
            bounds = candidate.get("bounds")
            if isinstance(bounds, str):
                bounds = m.parse_bounds(bounds)
            if not bounds or len(bounds) != 4:
                raise RuntimeError("Swipe candidate has no usable bounds")
            x1, y1, x2, y2 = bounds
            x, y = (x1+x2)//2, (y1+y2)//2
            dx, dy = int((x2-x1)*0.3), int((y2-y1)*0.3)
            points = {"up": (x,y+dy,x,y-dy), "down": (x,y-dy,x,y+dy),
                      "left": (x+dx,y,x-dx,y), "right": (x-dx,y,x+dx,y)}
            args = ("shell", "input", "swipe", *map(str, points[action.get("direction", "up")]), "450")
        else:
            center = m.bounds_center(candidate.get("bounds", ""))
            if not center:
                raise RuntimeError("Tap candidate has no usable bounds")
            args = ("shell", "input", "tap", str(center["x"]), str(center["y"]))
    elif kind == "back":
        args = ("shell", "input", "keyevent", "4")
    elif kind == "wait":
        if m._test_stop_event.wait(action.get("wait_ms", 500) / 1000):
            raise Stopped("Test Case stopped")
        return "Waited for screen"
    else:
        raise ValueError("Unsupported planned action")
    with m._adb_input_lock:
        check_stop(m)
        _, error, rc = m.adb(*args, timeout_s=8)
        if rc:
            raise RuntimeError(error.decode(errors="replace") or "ADB input failed")
        if kind == "input":
            check_stop(m)
            _, error, rc = m.adb("shell", "input", "text", m.adb_text_arg(action["value"]), timeout_s=8)
            if rc:
                raise RuntimeError(error.decode(errors="replace") or "ADB text input failed")
    if m._test_stop_event.wait(0.35):
        raise Stopped("Test Case stopped")
    return f"{kind}: {(candidate or {}).get('label', '')}"


def execute_plan(m, step):
    hint = step.get("ai_hint") or {}
    if not hint:
        raise m.BlockedStep("AI plan unavailable for this TC")
    reason = hard_block_reason(m, step) or hint.get("unsupported_reason")
    if reason:
        raise m.BlockedStep(reason)
    if not step.get("_ai_prepared"):
        step["_ai_phase"] = "prepare"
        if hint["preconditions"] and observe(m, hint["preconditions"]):
            raise RuntimeError("Visible preconditions are not satisfied")
        step["_ai_prepared"] = True
    step["_ai_phase"] = "execute"
    actions = hint["actions"]
    for pos in range(step.get("_ai_cursor", 0), len(actions)):
        action = actions[pos]
        m.emit_test_ai_status("executing", f"TC {step.get('case_no') or step.get('row')}: {pos+1}/{len(actions)} {action['action']}")
        message = execute_action(m, action)
        step.setdefault("_ai_trace", []).append({"phase": "execute", "action": action["action"],
                                                  "reason": message, "status": "executed"})
        step["_ai_cursor"] = pos+1
    step["_ai_phase"] = "verify"
    if narrative_excel.needs_visual_verification(step):
        result = m.verify_visual_step(step)
    else:
        result = verify(m, hint["checks"])
    step["_ai_phase"] = "done"
    return "All expected conditions verified: " + result
