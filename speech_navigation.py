"""Fail-closed, semantic UIXML navigation for Japanese Premium build 11.

Host contract (the existing mirror_server interface):
    host.ADB_SERIAL == device serial
    host.dump_ui_xml() -> fresh UIAutomator XML
    host.adb(*args, timeout_s=...) -> (stdout, stderr, returncode)
stop is a threading.Event or a zero-argument cancellation callable.

prepare(item, mode='selected'|'unit'), next(item), retry(item), skip(item)
return {ready, reason, actions, unit_completed, ...}. A ready result may be an
ARMING HANDOFF, not an assertion that the engine is ready. It includes
ready_for='armed_start' or 'armed_transition', requires_armed_start=True,
start_selector, and target_verified=False. The attempt adapter must arm first,
re-read XML, resolve that selector in the stated scope, and independently
verify the actual engine target. Never tap a saved coordinate from actions.

restart_session(item, mode='selected') clears cached navigation continuity and
uses the same bounded safe-exit/menu route as prepare; it never resets progress.

next_intro_selector(raw, scope) is a pure, no-input helper for first-word
and first-core sentence1 introductions. Pass start_selector['intro_scope'] from prepare.
It returns a fresh selector, None for known playback/first-speaking boundaries,
or raises NavigationBlocked for unsupported/mismatched screens. The adapter
must remain armed, enforce its deadline/action bound, and revalidate selectors
under its input lock. No method opens a hidden video skip control.

Only menu selection/scrolling, safe lesson-cover entry, observed exit
confirmation, and result acknowledgement are performed here.
Start, next, skip, retry, play, and microphone controls can auto-record and
are returned to the armed adapter, never pressed by this module.

unit means the entire (subject, level, week, stage), NOT just one session.
complete_unit() never promotes a session result or exhausted item list to
whole-stage completion. Current artifacts do not establish a semantic
whole-stage completion indicator, so that claim remains explicitly blocked.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager, nullcontext
import re
import subprocess
import time
import xml.etree.ElementTree as ET

from speech_materials import BUILD, PACKAGE, SUBJECT, device_serial

PREFIX = PACKAGE + ":id/"
SESSION_LABELS = {
    1: "\ub2e8\uc5b4", 2: "\ubb38\uc7a51", 3: "\ubb38\uc7a52", 4: "\ud68c\ud654",
}
SESSION_ALIASES = {
    "word": 1, "sentence1": 2, "sentence2": 3, "conversation": 4,
    **{label: number for number, label in SESSION_LABELS.items()},
}
BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]\Z")
EXIT_MESSAGE = "\ud559\uc2b5\uc744 \uc911\ub2e8\ud558\uace0 \ub098\uac00\uc2dc\uaca0\uc2b5\ub2c8\uae4c?"
CONFIRM = "\ud655\uc778"
CANCEL = "\ucde8\uc18c"
COMPLETE = "\ud559\uc2b5 \uc644\ub8cc"
SKIP_LECTURE = "\uc815\ub9d0 \uac15\uc758\ub97c \uac74\ub108\ub6f0\uc2dc\uaca0\uc5b4\uc694?"
SKIP_LECTURE_BODY = (
    "\ub3d9\uc601\uc0c1 \uac15\uc758\ub97c \uac74\ub108\ub6f0\uba74\n"
    "\ub2e4\uc74c \uacfc\uc815\uc758 \ub0b4\uc6a9\uc774 \uc5b4\ub835\uac8c \ub290\uaef4\uc9c8 \uc218 \uc788\uc5b4\uc694.\n"
    "\uadf8\ub798\ub3c4 \uac15\uc758\ub97c \uac74\ub108\ub6f0\uc2dc\uaca0\uc5b4\uc694?"
)
NOTE_GUIDE = "\ud559\uc2b5\ud55c \uac15\uc758 \ub0b4\uc6a9\uc744 \uc815\ub9ac\ud574\ubcf4\uc138\uc694."
GRAMMAR_GUIDE = "\ud559\uc2b5\ud560 \ubb38\uc7a5\uc758 \uc911\uc694\ud55c \uc5b4\ubc95\uc744 \uc0b4\ud3b4\ubcf4\uc138\uc694."
FIRST_CORE_GUIDE = "\ud575\uc2ec \ubb38\uc7a5\uc744 \uc775\ud788\uace0 \uc9c1\uc811 \ub9d0\ud574 \ubcf4\uc138\uc694."


class NavigationBlocked(RuntimeError):
    pass


class NavigationCancelled(NavigationBlocked):
    pass


def _rid(node):
    value = node.get("resource-id", "")
    return value[len(PREFIX):] if value.startswith(PREFIX) else ""


def _bounds(node):
    match = BOUNDS.fullmatch(node.get("bounds", ""))
    if not match:
        raise NavigationBlocked("Missing semantic node bounds")
    left, top, right, bottom = map(int, match.groups())
    if right <= left or bottom <= top:
        raise NavigationBlocked("Semantic node is offscreen or has empty bounds")
    return left, top, right, bottom


class Screen:
    def __init__(self, xml):
        if isinstance(xml, bytes):
            xml = xml.decode("utf-8")
        if not isinstance(xml, str) or not xml.strip() or len(xml) > 4 * 1024 * 1024:
            raise NavigationBlocked("Missing or oversized UI XML")
        if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
            raise NavigationBlocked("Unexpected XML declarations")
        try:
            self.root = ET.fromstring(xml)
        except ET.ParseError as exc:
            raise NavigationBlocked("Malformed UI XML") from exc
        self.nodes = list(self.root.iter("node"))
        self.parents = {child: parent for parent in self.root.iter() for child in parent}
        start, end = xml.find("<hierarchy"), xml.rfind("</hierarchy>")
        if start < 0 or end < start:
            raise NavigationBlocked("Missing hierarchy root")
        hierarchy = xml[start:end + len("</hierarchy>")]
        self.sha256 = hashlib.sha256(hierarchy.encode("utf-8")).hexdigest()
        if not self.nodes or self.nodes[0].get("package") != PACKAGE:
            raise NavigationBlocked("Wrong foreground subject/package")
        for node in self.nodes:
            if (node.get("package") not in {PACKAGE, "", None} and
                    node.get("clickable") == "true" and node.get("enabled") == "true"):
                raise NavigationBlocked("Foreign/system overlay requires manual attention")

    def find(self, rid, text=None, scope=None):
        nodes = (scope.iter("node") if scope is not None else self.nodes)
        return [n for n in nodes if _rid(n) == rid and
                (text is None or n.get("text") == text) and n.get("visible-to-user") != "false"]

    def one(self, rid, text=None, scope=None):
        nodes = self.find(rid, text, scope)
        if len(nodes) != 1:
            raise NavigationBlocked(f"Expected exactly one {rid}; found {len(nodes)}")
        return nodes[0]

    def ancestor(self, node, rid):
        while node in self.parents:
            node = self.parents[node]
            if _rid(node) == rid:
                return node
        raise NavigationBlocked(f"Missing semantic ancestor {rid}")

    def stage(self, number):
        scroller = self.one("listStagePath")
        header = self.one("tvStageNum", f"Stage {number}", scope=scroller)
        node = header
        # A clipped sibling can expose controls without its header. Never borrow
        # those controls by climbing into the ListView or its outer containers.
        while self.parents.get(node) is not scroller:
            if node not in self.parents:
                raise NavigationBlocked("Stage header is not inside a list row")
            node = self.parents[node]
        if node is header or len(self.find("tvStageNum", scope=node)) != 1:
            raise NavigationBlocked("Stage header does not identify a unique list row")
        return node

    def selected_week(self):
        # Saved production XML exposes tvEpTitleNum only on the active week.
        nodes = self.find("tvEpTitleNum")
        if len(nodes) != 1:
            raise NavigationBlocked("Active Week identity is not uniquely exposed")
        match = re.fullmatch(r"Week (\d+)", nodes[0].get("text", ""))
        if not match:
            raise NavigationBlocked("Unknown active Week label")
        return int(match[1])

    def level(self):
        match = re.fullmatch(r"Level (\d+)", self.one("tvLevelTitle").get("text", ""))
        if not match:
            raise NavigationBlocked("Unknown Level label")
        return int(match[1])


def _first_core_intro_scope(scope):
    if not isinstance(scope, dict) or scope.get("subject") != SUBJECT:
        raise NavigationBlocked("First-core intro requires the verified subject/unit/item scope")
    if scope.get("package", PACKAGE) != PACKAGE or scope.get("build", BUILD) != BUILD:
        raise NavigationBlocked("Intro package/build mismatch")
    for key in ("level", "week", "stage", "session", "word_id", "order"):
        if not re.fullmatch(r"[1-9]\d*", str(scope.get(key, ""))):
            raise NavigationBlocked("First-core intro scope is incomplete")
    if (int(scope["level"]) < 2 or int(scope["session"]) != 2 or int(scope["order"]) != 1
            or scope.get("kind") != "sentence_core"):
        raise NavigationBlocked("Intro navigation supports sentence1 first core only; no unselected recognition skipping")
    target = scope.get("target")
    if not isinstance(target, str) or not target.endswith(";") or any(c in target for c in "\0\r\n"):
        raise NavigationBlocked("First-core intro requires the exact runtime target")
    return {
        "subject": SUBJECT, "package": PACKAGE, "build": BUILD,
        **{key: int(scope[key]) for key in ("level", "week", "stage", "session", "word_id", "order")},
        "kind": "sentence_core", "target": target,
    }


def _exact_intro_text(container, text):
    matches = [n for n in container.iter("node") if n.get("text") == text
               and n.get("package") == PACKAGE and n.get("visible-to-user") != "false"]
    if len(matches) != 1:
        raise NavigationBlocked("Intro guide/confirmation text is missing or ambiguous")
    _bounds(matches[0])


def _word_intro_scope(scope):
    if (not isinstance(scope, dict) or scope.get('subject') != SUBJECT
            or scope.get('package', PACKAGE) != PACKAGE or scope.get('build', BUILD) != BUILD
            or scope.get('kind') != 'word'):
        raise NavigationBlocked('Word intro subject/build mismatch')
    for key in ('level', 'week', 'stage', 'session', 'word_id', 'order'):
        if not re.fullmatch(r'[1-9]\d*', str(scope.get(key, ''))):
            raise NavigationBlocked('Word intro scope is incomplete')
    if int(scope['session']) != 1 or int(scope['order']) != 1 or int(scope['level']) < 2:
        raise NavigationBlocked('Word intro requires first selected word')
    target = scope.get('target')
    if not isinstance(target, str) or not target.endswith(';') or any(c in target for c in '\0\r\n'):
        raise NavigationBlocked('Word intro requires exact runtime target')
    return {**{key: int(scope[key]) for key in ('level', 'week', 'stage', 'session', 'word_id', 'order')},
            'subject': SUBJECT, 'package': PACKAGE, 'build': BUILD, 'kind': 'word', 'target': target}


def _word_today_control(view):
    if any(view.find(rid) for rid in ('layoutMessageBox', 'layoutSingleQuiz', 'tvConvGuide',
                                     'rlLecturePopup', 'flStagePopupOverlay')):
        raise NavigationBlocked('Unexpected word cover overlay')
    container = view.one('flTodayLessonCoverOverlay')
    view.one('tvTodayLessonTitle', '\uc624\ub298\uc758 \ud559\uc2b5 \ub2e8\uc5b4', scope=container)
    _exact_intro_text(view.one('gvTodayLessonGuide', scope=container), '\uc624\ub298 \ud559\uc2b5\ud560 \ub2e8\uc5b4\ub97c \ud655\uc778\ud558\uc138\uc694.')
    view.one('layoutWord', scope=view.one('cvStudyCard'))
    return view.one('btnTodayLessonNext', '\ub2e4\uc74c', scope=container)


def _word_recall_control(view):
    if any(view.find(rid) for rid in ('layoutMessageBox', 'layoutSingleQuiz', 'tvConvGuide',
                                     'rlLecturePopup', 'flTodayLessonCoverOverlay')):
        raise NavigationBlocked('Unexpected word recall overlay')
    container = view.one('flStagePopupOverlay')
    view.one('tvStagePopupTitle', '\ub2e8\uc5b4 \ub5a0\uc62c\ub9ac\uae30', scope=container)
    view.one('tvStagePopupDesc', '\ub73b\uacfc \uadf8\ub9bc\uc744 \ubcf4\uace0 \ub2e8\uc5b4\ub97c \ub5a0\uc62c\ub824 \ubcf4\uc138\uc694.', scope=container)
    view.one('layoutWord', scope=view.one('cvStudyCard'))
    return view.one('btnStagePopupStart', '\uc2dc\uc791', scope=container)


def _word_intro_selector(raw, scope):
    scope = _word_intro_scope(scope)
    view = Screen(raw)
    if any(view.find(rid) for rid in ('layoutMessageBox', 'layoutSingleQuiz', 'tvConvGuide', 'rlLecturePopup')):
        raise NavigationBlocked('Unexpected word intro overlay')
    if view.find('flTodayLessonCoverOverlay'):
        control = _word_today_control(view)
        phase = 'word_today_cover'
    elif view.find('flStagePopupOverlay'):
        control = _word_recall_control(view)
        phase = 'word_recall_start'
    elif view.find('layoutWord'):
        # The first listening pass advances itself. Never press next to skip it.
        view.one('layoutWord', scope=view.one('cvStudyCard'))
        return None
    else:
        raise NavigationBlocked('Unknown word intro screen')
    if view.one(_rid(control)) is not control or control.get('enabled') != 'true' or control.get('clickable') != 'true':
        raise NavigationBlocked('Word intro control is not unique and enabled')
    _bounds(control)
    return dict(resource_id=control.get('resource-id'), text=control.get('text', ''),
                content_desc=control.get('content-desc', ''),
                scope={key: scope[key] for key in ('subject', 'level', 'week', 'stage', 'session')},
                intro_scope=scope, intro_phase=phase, screen_sha256=view.sha256,
                screen_hash_format='hierarchy', requires_armed_start=True, target_verified=False,
                expected_target=scope['target'])


def next_intro_selector(raw, scope):
    """Return a semantic selector without reading or operating the device.

scope must be the unchanged intro_scope issued by a verified prepare handoff,
carried through the SAME armed attempt. Note/grammar/popups do not expose unit
labels themselves, so this pure helper cannot re-establish lost continuity.
The caller must check stop/deadline before every input and stop navigation on
the first recognition frame. Returned selectors always leave target_verified
false: only the attempt's runtime target guard can verify it.

Evidence: japanese-retest-20261106 s1-video-start/bar/skip-dialog/skipped,
s1-sentence1-flow-001/002 and fix83-001..005. No artifact is read at runtime.
An already-open exact lecture popup can be confirmed; opening it from the
custom video player is unsupported. None at that player means WAIT ONLY within
the caller's existing deadline, never infer completion or tap its surface.
"""
    if isinstance(scope, dict) and scope.get('kind') == 'word':
        return _word_intro_selector(raw, scope)
    scope = _first_core_intro_scope(scope)
    view = Screen(raw)
    if view.find("layoutMessageBox"):
        raise NavigationBlocked("Generic dialog is not a verified lecture-skip popup")
    markers = {
        "today_cover": bool(view.find("flTodayLessonCoverOverlay")),
        "lecture_cover": bool(view.find("flLectureCoverOverlay")),
        "lecture_skip_confirm": bool(view.find("rlLecturePopup") or view.find("layoutLecturePopup")),
        "lecture_note": bool(view.find("btnCtrlReplayLecture")),
        "grammar": bool(view.find("layoutSentenceDesc")),
        "lecture_playback": bool(view.find("layoutSentenceLecture")),
        "first_speaking": bool(view.find("layoutSingleQuiz")),
    }
    active = [name for name, visible in markers.items() if visible]
    if len(active) != 1:
        raise NavigationBlocked("Unknown or overlapping intro screen")
    phase = active[0]
    if phase == "today_cover":
        container = view.one("flTodayLessonCoverOverlay")
        view.one("tvTodayLessonTitle", "\uc624\ub298\uc758 \ud559\uc2b5", scope=container)
        control = view.one("btnTodayLessonNext", "\ub2e4\uc74c", scope=container)
    elif phase == "lecture_cover":
        container = view.one("flLectureCoverOverlay")
        for rid, text in (
            ("tvLectureCoverLevel", f"Level {scope['level']}"),
            ("tvLectureCoverChapter", f"Week {scope['week']}"),
            ("tvLectureCoverStage", f"Stage {scope['stage']}"),
            ("tvLectureCoverTodayLabel", "\ubb38\uc7a51 \uc624\ub298\uc758 \uac15\uc758"),
        ):
            view.one(rid, text, scope=container)
        control = container
    elif phase == "lecture_skip_confirm":
        overlay = view.one("rlLecturePopup")
        container = view.one("layoutLecturePopup", scope=overlay)
        _exact_intro_text(container, SKIP_LECTURE)
        _exact_intro_text(container, SKIP_LECTURE_BODY)
        view.one("btn_sel2", CANCEL, scope=container)
        control = view.one("btn_sel1", "\uac74\ub108\ub6f0\uae30", scope=container)
    elif phase == "lecture_note":
        view.one("lytContent")
        view.one("imgContent")
        _exact_intro_text(view.one("txtGuideMsg"), NOTE_GUIDE)
        controls = view.one("rlBottomControlBar")
        view.one("btnCtrlReplayLecture", "\uac15\uc758 \ub2e4\uc2dc\ubcf4\uae30", scope=controls)
        control = view.one("btnCtrlNext", "\ub2e4\uc74c", scope=controls)
    elif phase == "grammar":
        container = view.one("layoutSentenceDesc")
        view.one("cvDescCard", scope=container)
        view.one("txtDescText", scope=container)
        _exact_intro_text(view.one("txtGuideMsg"), GRAMMAR_GUIDE)
        controls = view.one("rlBottomControlBar")
        view.one("ivCtrlPrev", scope=controls)
        control = view.one("ivCtrlNext", scope=controls)
    elif phase == "lecture_playback":
        container = view.one("layoutSentenceLecture")
        view.one("layoutLecture", scope=container)
        view.one("mvLecture", scope=container)
        if any(n.get("clickable") == "true" for n in container.iter("node")):
            raise NavigationBlocked("Unverified lecture-player controls; no semantic skip mapping")
        return None
    else:
        view.one("tvSingleQuizGuide", FIRST_CORE_GUIDE)
        if not re.fullmatch(r"1 / [1-9]\d* \ubb38\uc7a5", view.one("tvSingleQuizPage").get("text", "")):
            raise NavigationBlocked("Not the first speaking page; unselected recognition skipping is unsupported")
        return None
    if view.one(_rid(control)) is not control:
        raise NavigationBlocked("Intro control is not globally unique")
    if control.get("enabled") != "true" or control.get("clickable") != "true":
        raise NavigationBlocked("Intro control is not enabled and clickable")
    _bounds(control)
    return {
        "resource_id": control.get("resource-id"), "text": control.get("text", ""),
        "content_desc": control.get("content-desc", ""),
        "scope": {key: scope[key] for key in ("subject", "level", "week", "stage", "session")},
        "intro_scope": dict(scope), "intro_phase": phase,
        "screen_sha256": view.sha256, "screen_hash_format": "hierarchy",
        "requires_armed_start": True, "target_verified": False,
        "expected_target": scope["target"],
    }


class Navigator:
    MAX_ACTIONS = 20
    MAX_SECONDS = 90
    MIN_SCROLL_PIXELS = 16

    def __init__(self, host, device, stop):
        self.host = host
        self.device = device_serial(device)
        self.stop = stop
        self._scope = None
        self._unit = None
        self._item = None
        self._mode = "selected"
        self._actions = []
        self._deadline = 0.0
        self._completed_sessions = set()
        self._last_handoff = None
        self._word_observation = None

    def record_attempt(self, item, attempt):
        """Keep only a completed, cleaned-up word observation for one next handoff."""
        self._word_observation = None
        if (item.get("kind") != "word" or attempt.get("status") != "completed"
                or any(attempt.get(k) is not True for k in (
                    "cleanup_ok", "signature_verified", "target_verified", "full_input"))):
            return
        evidence = attempt.get("outcome_evidence") or {}
        runtime = attempt.get("runtime") or {}
        if (attempt.get("app_outcome") not in {"word_correct", "word_wrong"}
                or runtime.get("text") != item.get("target") or runtime.get("slot") != 0
                or evidence.get("app_outcome") != attempt["app_outcome"]
                or evidence.get("source") != "installed_apk_word_icon_pixels"
                or not re.fullmatch(r"[a-f0-9]{64}", evidence.get("screen_sha256", ""))):
            return
        self._word_observation = (self._identity(item), str(item["word_id"]), evidence["screen_sha256"], attempt["app_outcome"])

    def _check(self):
        stopped = self.stop() if callable(self.stop) else self.stop.is_set() if self.stop is not None else False
        if stopped:
            raise NavigationCancelled("Cancelled")
        if time.monotonic() > self._deadline:
            raise NavigationBlocked("Navigation time limit reached")
        if getattr(self.host, "ADB_SERIAL", None) != self.device:
            raise NavigationBlocked("Host ADB serial does not match the requested device")

    def _begin(self):
        self._actions = []
        self._deadline = time.monotonic() + self.MAX_SECONDS
        self._check()
        data = self._adb("shell", "dumpsys", "package", PACKAGE)
        if isinstance(data, bytes):
            data = data.decode("utf-8", "replace")
        if set(re.findall(r"\bversionCode=(\d+)\b", data)) != {str(BUILD)}:
            raise NavigationBlocked("Expected Japanese Premium build 11")

    def _adb(self, *args):
        self._check()
        stdout, stderr, code = self.host.adb(*args, timeout_s=8)
        self._check()
        if code:
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", "replace")
            raise NavigationBlocked("ADB operation failed: " + str(stderr)[:300])
        return stdout

    def _read(self):
        self._check()
        xml = self.host.dump_ui_xml()
        self._check()
        return Screen(xml)

    def _result(self, ready=False, reason="", **extra):
        return {
            "ready": ready, "reason": reason, "actions": list(self._actions),
            "unit_completed": False, "target_verified": False, **extra,
        }

    def _blocked(self, exc):
        self._last_handoff = None
        self._word_observation = None
        # A failed/unknown observation breaks the continuity used for identity.
        self._scope = None
        return self._result(reason=str(exc), stopped=isinstance(exc, NavigationCancelled))

    @staticmethod
    def _identity(item):
        if not isinstance(item, dict) or item.get("subject", SUBJECT) != SUBJECT:
            raise NavigationBlocked("Wrong or missing subject")
        if item.get("package", PACKAGE) != PACKAGE or item.get("build", BUILD) != BUILD:
            raise NavigationBlocked("Item package/build mismatch")
        try:
            values = [item[key] for key in ("level", "week", "stage", "word_id")]
            if any(not re.fullmatch(r"[1-9]\d*", str(value)) for value in values):
                raise ValueError("Non-integral identity")
            *numbers, word = map(int, values)
            numbers = tuple(numbers)
            session = SESSION_ALIASES.get(item["session"], item["session"])
            if not re.fullmatch(r"[1-4]", str(session)):
                raise ValueError("Non-integral session")
            session = int(session)
        except (KeyError, TypeError, ValueError):
            raise NavigationBlocked("Complete level/week/stage/session/word identity is required")
        if any(n <= 0 for n in numbers) or word <= 0 or session not in SESSION_LABELS:
            raise NavigationBlocked("Unsupported unit/session identity")
        if numbers[0] == 1:
            raise NavigationBlocked("Level-1 session selectors are not established")
        if not isinstance(item.get("text"), str) or not item["text"]:
            raise NavigationBlocked("Missing exact display text")
        if not isinstance(item.get("target"), str) or not item["target"].endswith(";"):
            raise NavigationBlocked("Missing exact recognition target including terminator")
        if item.get("navigation_available") is False:
            raise NavigationBlocked(item.get("navigation_reason") or "Item is not navigable")
        label = item.get("session_label")
        if label is not None and label != SESSION_LABELS[session]:
            raise NavigationBlocked("Session ordinal/label mismatch")
        kind = item.get("kind")
        expected = {1: {"word"}, 2: {"sentence", "sentence_core", "sentence_variation"},
                    3: {"sentence", "sentence_core", "sentence_variation"}, 4: {"conversation"}}
        if kind not in expected[session]:
            raise NavigationBlocked("Session/kind mismatch")
        return (SUBJECT, *numbers, session)

    def _selector(self, node, screen):
        return {
            "resource_id": node.get("resource-id"), "text": node.get("text", ""),
            "content_desc": node.get("content-desc", ""),
            "scope": {
                "subject": self._scope[0], "level": self._scope[1], "week": self._scope[2],
                "stage": self._scope[3], "session": self._scope[4],
            } if self._scope else None,
            "screen_sha256": screen.sha256,
            "screen_hash_format": "hierarchy",
        }

    @contextmanager
    def _input_guard(self):
        lock = getattr(self.host, "_adb_input_lock", None)
        if lock is None or not hasattr(lock, "acquire"):
            with lock or nullcontext():
                yield
            return
        while True:
            self._check()
            if lock.acquire(timeout=0.1):
                break
        try:
            self._check()
            yield
        finally:
            lock.release()

    def _input(self, screen, node, action, command):
        with self._input_guard():
            self._check()
            if len(self._actions) >= self.MAX_ACTIONS:
                raise NavigationBlocked("Navigation input limit reached")
            if node.get("enabled") != "true":
                raise NavigationBlocked("Semantic control is disabled")
            if self._read().sha256 != screen.sha256:
                raise NavigationBlocked("Screen changed before input; no input sent")
            self._check()
            record = {"action": action, "selector": self._selector(node, screen), "dispatched": True,
                      "command": list(command)}
            if action == "scroll":
                record["visible_stages"] = [
                    {"text": n.get("text"), "bounds": n.get("bounds")}
                    for n in screen.find("tvStageNum", scope=node)
                ]
            self._actions.append(record)
            _, error, code = self.host.adb("shell", "input", *command, timeout_s=8)
            record["sent"] = code == 0
            self._check()
            if code:
                if isinstance(error, bytes):
                    error = error.decode("utf-8", "replace")
                raise NavigationBlocked("Input failed: " + str(error)[:300])

    def _tap(self, screen, node):
        if node.get("clickable") != "true":
            raise NavigationBlocked("Semantic control is not clickable")
        left, top, right, bottom = _bounds(node)
        self._input(screen, node, "tap", ("tap", str((left + right) // 2), str((top + bottom) // 2)))

    def _scroll_stage(self, screen, stage):
        candidates = [
            n for n in screen.nodes if n.get("scrollable") == "true" and
            screen.find("tvStageNum", scope=n)
        ]
        if len(candidates) != 1:
            raise NavigationBlocked("No unique production stage scroller")
        scroller = candidates[0]
        values = []
        for node in screen.find("tvStageNum", scope=scroller):
            match = re.fullmatch(r"Stage (\d+)", node.get("text", ""))
            if match:
                values.append(int(match[1]))
        if not values:
            raise NavigationBlocked("Visible stage range is unknown")
        left, top, right, bottom = _bounds(scroller)
        x = (left + right) // 2
        high, low = top + (bottom - top) // 5, bottom - (bottom - top) // 5
        start, end = (high, low) if stage < min(values) else (low, high)
        headers = screen.find("tvStageNum", f"Stage {stage}", scope=scroller)
        if len(headers) == 1:
            # Keep the target header visible while bringing its controls into view.
            # A full-height swipe can overshoot a partially visible stage forever.
            header_top = _bounds(headers[0])[1]
            distance = header_top - (top + (bottom - top) // 20)
            if abs(distance) < self.MIN_SCROLL_PIXELS:
                raise NavigationBlocked("Stage alignment cannot make safe progress; requested session controls are not visible")
            if distance > 0:
                end = low - min(low - high, distance)
                start = low
            elif distance < 0:
                start, end = high, high + min(low - high, -distance)
        if abs(end - start) < self.MIN_SCROLL_PIXELS:
            raise NavigationBlocked("Stage scroll viewport is too small for safe progress")
        self._input(screen, scroller, "scroll", ("swipe", str(x), str(start), str(x), str(end), "800"))

    def _handoff(self, screen, node, item, ready_for, reason, target_verified=False):
        self._check()
        if node.get("enabled") != "true" or node.get("clickable") != "true":
            raise NavigationBlocked("Start/transition control is not enabled and clickable")
        _bounds(node)
        if self._read().sha256 != screen.sha256:
            raise NavigationBlocked("Screen changed during arming handoff")
        selector = self._selector(node, screen)
        try:
            validate = _word_intro_scope if item.get('kind') == 'word' else _first_core_intro_scope
            selector["intro_scope"] = validate({**item, **selector["scope"]})
        except NavigationBlocked:
            pass
        self._last_handoff = selector
        return self._result(
            ready=True, reason=reason, ready_for=ready_for,
            requires_armed_start=True, start_selector=selector,
            expected_target=item["target"], word_id=item["word_id"],
            target_verified=target_verified, identity_verified=True,
        )

    def _menu(self, screen, item, scope):
        _, level, week, stage, session = scope
        seen = set()
        while True:
            self._check()
            if screen.sha256 in seen:
                raise NavigationBlocked("Menu navigation made no progress")
            seen.add(screen.sha256)
            actual_level = screen.level()
            if actual_level != level:
                self._scope = None
                self._tap(screen, screen.one("cvRightArrow" if actual_level < level else "cvLeftArrow"))
                screen = self._read()
                continue
            if screen.selected_week() != week:
                week_nodes = screen.find("tvEpTitleNum", f"Week {week}")
                if len(week_nodes) == 1:
                    row = screen.ancestor(week_nodes[0], "llEpisodeRow")
                else:
                    # Inactive rows hide the number; only a DB-provided exact
                    # title uniquely scoped to this level is acceptable.
                    title = item.get("week_title")
                    if not title:
                        raise NavigationBlocked("Inactive Week has no accessible number or DB title")
                    row = screen.ancestor(screen.one("tvEpTitleSubject", title), "llEpisodeRow")
                self._scope = None
                self._tap(screen, row)
                screen = self._read()
                if screen.level() != level or screen.selected_week() != week:
                    raise NavigationBlocked("Week selection did not confirm exact Level/Week")
                continue
            stage_nodes = screen.find("tvStageNum", f"Stage {stage}")
            if not stage_nodes:
                self._scroll_stage(screen, stage)
                screen = self._read()
                continue
            stage_root = screen.stage(stage)
            self._scope = scope
            starts = screen.find(f"flStart{session}", scope=stage_root)
            if len(starts) == 1:
                if session in (2, 3, 4):
                    # Artifact navigation consistently enters Today's Lesson /
                    # Today's Situation before the recording-capable Next.
                    captions = [n.get("text") for n in starts[0].iter("node") if n.get("text")]
                    if captions != ["\ud559\uc2b5 \uc2dc\uc791"]:
                        raise NavigationBlocked("Unrecognized lesson start/resume state")
                    self._tap(screen, starts[0])
                    cover = self._read()
                    cover.one("flTodayLessonCoverOverlay")
                    expected_title = "\uc624\ub298\uc758 \uc0c1\ud669" if session == 4 else "\uc624\ub298\uc758 \ud559\uc2b5"
                    cover.one("tvTodayLessonTitle", expected_title)
                    return self._handoff(
                        cover, cover.one("btnTodayLessonNext"), item, "armed_start",
                        "Verified unit/session lesson cover; arm before Next and verify the runtime target",
                    )
                return self._handoff(
                    screen, starts[0], item, "armed_start",
                    "Exact unit/session selected; adapter must arm before starting and verify engine target",
                )
            if (not screen.find(f"tvNode{session}Label", scope=stage_root) or
                    not screen.find(f"flNode{session}Circle", scope=stage_root)):
                self._scroll_stage(screen, stage)
                screen = self._read()
                continue
            label = screen.one(f"tvNode{session}Label", scope=stage_root)
            if label.get("text") != SESSION_LABELS[session]:
                raise NavigationBlocked("Stage session label mismatch")
            self._tap(screen, screen.one(f"flNode{session}Circle", scope=stage_root))
            screen = self._read()
            if not screen.find("tvLevelTitle"):
                raise NavigationBlocked("Unexpected screen after session selection")
            if screen.level() != level or screen.selected_week() != week:
                raise NavigationBlocked("Unit identity changed after session selection")

    def _require_context(self, scope):
        if self._scope != scope:
            raise NavigationBlocked("Screen does not expose exact unit identity; navigate from the unit menu first")

    def _return_to_menu(self, screen):
        """Only observed exit confirmation and known learning screens are safe."""
        self._scope = None
        self._last_handoff = None
        self._word_observation = None
        seen = set()
        known = ("tvConvGuide", "layoutSingleQuiz", "layoutPattern9",
                 "flTodayLessonCoverOverlay", "layoutSentenceDesc", "layoutSentenceLecture")
        for _ in range(6):
            if screen.find("tvLevelTitle"):
                screen.level()
                screen.selected_week()
                return screen
            if screen.sha256 in seen:
                raise NavigationBlocked("Return-to-menu made no progress")
            seen.add(screen.sha256)
            if screen.find("layoutMessageBox"):
                screen.one("popup_content", EXIT_MESSAGE)
                screen.one("btn_sel2", CANCEL)
                self._tap(screen, screen.one("btn_sel1", CONFIRM))
            elif screen.find("tvSlotName") and screen.find("tvScoreText"):
                buttons = [n for rid in ("btnConfirm", "btnResultConfirm")
                           for n in screen.find(rid, COMPLETE)]
                if len(buttons) != 1:
                    raise NavigationBlocked("Unknown result acknowledgement")
                # This acknowledges the visible result, not whole-unit success.
                self._tap(screen, buttons[0])
            else:
                if screen.find('layoutWord') and screen.find('flTodayLessonCoverOverlay'):
                    _word_today_control(screen)
                    self._input(screen, screen.one('flTodayLessonCoverOverlay'), 'back', ('keyevent', '4'))
                    screen = self._read()
                    continue
                if screen.find('flStagePopupOverlay'):
                    # A previous attempt can leave this prompt open. Exit, never
                    # start recording before unit identity and the adapter are ready.
                    popup = screen.one('flStagePopupOverlay')
                    if screen.find('tvStagePopupTitle', '\ub2e8\uc5b4 \ud034\uc988', scope=popup):
                        screen.one('tvStagePopupDesc', '\ubc30\uc6b4 \ub2e8\uc5b4\ub97c \ud034\uc988\ub85c \ubcf5\uc2b5\ud574 \ubcf4\uc138\uc694!', scope=popup)
                        screen.one('layoutWord', scope=screen.one('cvStudyCard'))
                        screen.one('btnStagePopupStart', '\uc2dc\uc791', scope=popup)
                        if any(screen.find(rid) for rid in ('layoutSingleQuiz', 'tvConvGuide', 'rlLecturePopup')):
                            raise NavigationBlocked('Unexpected word quiz overlay')
                    else:
                        _word_recall_control(screen)
                    self._input(screen, screen.one('flStagePopupOverlay'), 'back', ('keyevent', '4'))
                    screen = self._read()
                    continue
                if screen.find("layoutWord"):
                    from speech_word import word_frame
                    word_frame(ET.tostring(screen.root, encoding="unicode"))
                    self._input(screen, screen.one("layoutWord"), "back", ("keyevent", "4"))
                    screen = self._read()
                    continue
                marker = next((screen.find(rid)[0] for rid in known if screen.find(rid)), None)
                if marker is None:
                    raise NavigationBlocked("Unknown screen; safe return-to-menu is unavailable")
                self._input(screen, marker, "back", ("keyevent", "4"))
            screen = self._read()
        raise NavigationBlocked("Return-to-menu action limit reached")

    @staticmethod
    def _grade_next(screen):
        if screen.find("tvConvGuide") and screen.find("ivTalkGrade"):
            if any(screen.find(rid) for rid in ("ivTalkListen", "ivTalkSpeak", "pbTalkProgress")):
                return None
            buttons = screen.find("btnConvNext", "\ub2e4\uc74c")
            return buttons[0] if len(buttons) == 1 else None
        if screen.find("layoutSingleQuiz"):
            grades = {"Perfect", "Excellent", "Good", "Bad", "Fail"}
            if any(n.get("text") in grades or n.get("content-desc") in grades for n in screen.nodes):
                buttons = screen.find("ivCtrlNext")
                return buttons[0] if len(buttons) == 1 else None
        return None

    def _sequential(self, screen, previous, item, scope):
        if self._scope != scope or previous is None:
            return None
        try:
            consecutive = (self._identity(previous) == scope and
                           int(item["order"]) == int(previous["order"]) + 1 and
                           str(item["word_id"]) != str(previous["word_id"]))
        except (KeyError, TypeError, ValueError):
            return None
        if not consecutive:
            return None
        if scope[4] == 1 and item.get("kind") == previous.get("kind") == "word":
            from speech_word import word_frame
            expected = (scope, str(previous["word_id"]))
            if not self._word_observation or self._word_observation[:2] != expected:
                raise NavigationBlocked("Word transition requires an unchanged verified previous result; no blind next/play")
            word_frame(ET.tostring(screen.root, encoding="unicode"))
            meaning = previous.get("meaning")
            if not meaning or screen.one("txtPinyin").get("text", "").strip() != meaning.strip():
                raise NavigationBlocked("Previous word meaning changed before next handoff")
            result = self._handoff(screen, screen.one("ivCtrlNext"), item, "armed_transition",
                                   "Verified word feedback-to-next; new runtime target must match before PCM")
            result["previous_word_id"] = previous["word_id"]
            # Revalidate the current semantic frame and feedback pixels at dispatch;
            # transient UI attributes need not match the earlier captured XML bytewise.
            result["previous_result_screen_sha256"] = self._word_observation[2]
            result["start_selector"]["word_transition"] = dict(
                previous_word_id=str(previous["word_id"]), meaning=meaning.strip(),
                app_outcome=self._word_observation[3])
            self._word_observation = None
            return result
        button = self._grade_next(screen)
        if button is None:
            return None
        result = self._handoff(
            screen, button, item, "armed_transition",
            "Sequential same-session grade-to-next handoff; adapter must verify the NEW runtime target",
        )
        result["previous_word_id"] = previous["word_id"]
        return result

    @staticmethod
    def _target_node(screen, item):
        # tvKeySentenceText is a reference heading, NOT the current target.
        if screen.find("layoutWord"):
            raise NavigationBlocked("Word target/play state is hidden; only a verified result-to-next handoff is supported")
        elif screen.find("layoutSingleQuiz"):
            nodes = screen.find("fvSingleQuizText")
        elif screen.find("tvConvGuide"):
            nodes = []
            for icon in screen.find("ivTalkSpeak"):
                try:
                    bubble = screen.ancestor(icon, "cvTalkBubble")
                    nodes.extend(screen.find("tvTalkText", scope=bubble))
                except NavigationBlocked:
                    pass
        elif screen.find("layoutPattern9"):
            nodes = screen.find("fvFBText")
        else:
            raise NavigationBlocked("Unsupported speaking screen")
        if len(nodes) != 1:
            raise NavigationBlocked("Current speaking target is not uniquely exposed")
        value = nodes[0].get("text") or nodes[0].get("content-desc", "")
        if not value:
            raise NavigationBlocked("Speaking target is hidden in a custom view; UIXML cannot verify it")
        if value not in {item["text"], item["target"], item["target"].removesuffix(";")}:
            raise NavigationBlocked("Current speaking target does not exactly match the selected item")
        return nodes[0]

    def prepare(self, item, mode="selected"):
        try:
            self._begin()
            if mode not in {"selected", "unit"}:
                raise NavigationBlocked("Mode must be selected or unit")
            scope = self._identity(item)
            if mode == "unit" and self._unit is not None and self._unit != scope[:4]:
                raise NavigationBlocked("Whole-unit navigation cannot cross the selected Stage")
            previous = self._item
            self._mode = mode
            if self._unit != scope[:4]:
                self._completed_sessions.clear()
            self._unit = scope[:4]
            screen = self._read()
            sequential = self._sequential(screen, previous, item, scope)
            self._item = dict(item)
            if sequential is not None:
                return sequential
            if screen.find("tvLevelTitle"):
                return self._menu(screen, item, scope)
            if self._scope != scope:
                screen = self._return_to_menu(screen)
                return self._menu(screen, item, scope)
            self._require_context(scope)
            if screen.find("flTodayLessonCoverOverlay"):
                return self._handoff(
                    screen, screen.one("btnTodayLessonNext"), item, "armed_transition",
                    "Known lesson cover; subsequent screens may auto-record and need armed navigation",
                )
            if any(screen.find(rid) for rid in ("layoutSentenceDesc", "btnCtrlReplayLecture",
                                               "rlLecturePopup", "flLectureCoverOverlay")):
                intro = next_intro_selector(
                    ET.tostring(screen.root, encoding="unicode"), {**item, "subject": SUBJECT})
                result = self._handoff(
                    screen, screen.one(intro["resource_id"].removeprefix(PREFIX)), item, "armed_transition",
                    "Verified first-core intro; adapter must arm before input and verify the runtime target",
                )
                result["start_selector"]["intro_phase"] = intro["intro_phase"]
                return result
            if screen.find("layoutWord"):
                # Custom word/page views do not expose identity or play/pause state.
                # Re-enter only from a verified menu; never skip unselected speech.
                screen = self._return_to_menu(screen)
                return self._menu(screen, item, scope)
            self._target_node(screen, item)
            # Bare play/pause icons in saved XML do not expose their current
            # action. Do not infer pause/recording state from their existence.
            controls = [n for rid in ("ivCtrlPlay", "ivConvCtrlPlay") for n in screen.find(rid)
                        if n.get("content-desc") in {"Play", "\uc7ac\uc0dd", "\ub2e4\uc2dc \ub9d0\ud558\uae30"}]
            if len(controls) != 1:
                raise NavigationBlocked("Target matches but paused/start state is not semantically exposed")
            return self._handoff(screen, controls[0], item, "armed_start",
                                 "Exact target at an explicit paused/start control; arm before input",
                                 target_verified=True)
        except (NavigationBlocked, OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
            return self._blocked(exc)

    def _transition(self, item, operation):
        try:
            self._begin()
            scope = self._identity(item)
            self._require_context(scope)
            screen = self._read()
            if operation == "next":
                button = self._grade_next(screen)
                if button is not None:
                    return self._handoff(
                        screen, button, item, "armed_transition",
                        "Completed speaking page; next target still requires runtime verification",
                    )
            if operation == "skip":
                if not screen.find("rlLecturePopup"):
                    raise NavigationBlocked("Only an already-open verified first-core lecture skip is supported")
                intro = next_intro_selector(
                    ET.tostring(screen.root, encoding="unicode"), {**item, "subject": SUBJECT})
                return self._handoff(
                    screen, screen.one(intro["resource_id"].removeprefix(PREFIX)), item, "armed_transition",
                    "Explicit lecture skip only; not whole-unit completion",
                )
            self._target_node(screen, item)
            if operation == "retry":
                candidates = [n for rid in ("ivCtrlPlay", "ivConvCtrlPlay") for n in screen.find(rid)
                              if n.get("content-desc") in {"Retry", "\ub2e4\uc2dc \ub9d0\ud558\uae30"}]
            elif operation == "next":
                candidates = [n for rid in ("ivCtrlNext", "btnConvNext") for n in screen.find(rid)]
            if len(candidates) != 1:
                raise NavigationBlocked(f"No unambiguous safe {operation} control")
            return self._handoff(screen, candidates[0], item, "armed_transition",
                                 f"{operation} can auto-record; input is delegated to the armed adapter",
                                 target_verified=True)
        except (NavigationBlocked, OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
            return self._blocked(exc)

    def next(self, item):
        """Prepare the next transition FROM item; never press auto-start controls."""
        return self._transition(item, "next")

    def retry(self, item):
        """Hand off an explicit retry for the same target; never reset progress."""
        return self._transition(item, "retry")

    def restart_session(self, item, mode="selected"):
        """Restart via existing safe exit/menu navigation, never app progress reset."""
        self._scope = None
        self._item = None
        self._last_handoff = None
        self._word_observation = None
        return self.prepare(item, mode)

    def skip(self, item):
        return self._transition(item, "skip")

    def complete_unit(self):
        """Acknowledge a proven session result, without claiming Stage completion."""
        try:
            self._begin()
            if self._scope is None or self._item is None:
                raise NavigationBlocked("No verified unit/session context")
            screen = self._read()
            label = screen.one("tvSlotName").get("text")
            if label != SESSION_LABELS[self._scope[4]]:
                raise NavigationBlocked("Result belongs to a different or unsupported session")
            if not screen.find("tvScoreText") or not screen.find("tvItemText", self._item["text"]):
                raise NavigationBlocked("Result screen lacks selected-item/session evidence")
            buttons = [n for rid in ("btnConfirm", "btnResultConfirm") for n in screen.find(
                rid, "\ud559\uc2b5 \uc644\ub8cc")]
            if len(buttons) != 1:
                raise NavigationBlocked("No unique session completion acknowledgement")
            completed_scope = self._scope
            self._tap(screen, buttons[0])
            after = self._read()
            if after.level() != completed_scope[1] or after.selected_week() != completed_scope[2]:
                raise NavigationBlocked("Session completion did not return to the selected unit")
            after.stage(completed_scope[3])
            self._completed_sessions.add(completed_scope[4])
            self._scope = None
            self._last_handoff = None
            return self._result(
                reason="Session result acknowledged; whole-Stage completion has no verified UIXML indicator",
                session_completed=True, session=completed_scope[4],
                completed_sessions=sorted(self._completed_sessions),
                completion_scope="session", unsupported="whole_stage_completion",
            )
        except (NavigationBlocked, OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
            return self._blocked(exc)
