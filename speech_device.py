"""Local build-11 PCM injection. No TTS, playback, or remote API.

Public contract:
    DeviceAdapter(host, root)  # host._adb_input_lock guards input/capture
    devices() -> list[dict]   # explicitly selected local ADB devices
    inspect(serial, *, runtime_snapshot=False) -> dict
    check_cleanup(serial) -> {cleanup_ok, reason}
    run_attempt(serial, target, pcm_path, evidence_dir, stop_event) -> dict

target is the EXACT HciControl.recogText string (including punctuation), or a
mapping with text (or target), optional slot, actualWordID and timeout_s.
Navigator handoffs accept start_selector, scope and word_id (navigation metadata,
NOT an independently observed runtime ID). A handoff defers the exact text/slot
guard to the first input frame. At most two known intro transitions are allowed.
actualWordID is deliberately unsupported until a task-bound source is proven;
requesting it blocks, rather than confusing one of the APK's several word IDs.
Raw input must already be signed PCM16LE, mono, 16000 Hz, 0 < duration <= 15s.
Raw PCM has no header: its rate/channel contract cannot be detected from bytes.
root resolves relative caller paths. Every operation creates a unique evidence
subdirectory, never overwriting a previous trial. Returned files are relative
to evidence_dir. Only PNG/XML/log evidence is listed there; no audio is played.
The 90s default attempt deadline includes preflight/compile/capture. Cleanup has
a separate bounded grace period. No device work happens at import/construction.
"""

from collections.abc import Mapping
from contextlib import contextmanager
import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import zipfile
import xml.etree.ElementTree as ET


PACKAGE = "com.weaversmind.tjp2"
BUILD = 11
_LOCK = threading.Lock()
_LEASE_LOCK = threading.Lock()
_BUSY = set()
# Deliberately excludes stage navigation, decorative Speak dots, and random
# coordinates. These controls must be clickable in the CURRENT hierarchy.
_START_IDS = {PACKAGE + ":id/" + x for x in (
    "ivTalkSpeak", "ivFBSpeakIcon", "btnStudyStart", "ivStudyStart",
)}
_GRADES = {"Perfect", "Excellent", "Good", "Bad", "Fail"}


class _Blocked(RuntimeError):
    pass


class _Stopped(RuntimeError):
    pass


def _check(deadline, stop):
    if stop is not None and stop.is_set():
        raise _Stopped("stopped")
    if time.monotonic() >= deadline:
        raise TimeoutError("attempt_timeout")


def _launch(args, **kwargs):
    return subprocess.Popen(args, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), **kwargs)


def _command(args, deadline, stop=None, timeout=10):
    """Poll communicate so a stop can interrupt even a hung adb subprocess."""
    _check(deadline, stop)
    end = min(deadline, time.monotonic() + timeout)
    process = _launch(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        while True:
            _check(end, stop)
            try:
                out, err = process.communicate(timeout=min(.1, max(.001, end - time.monotonic())))
                break
            except subprocess.TimeoutExpired:
                pass
        if process.returncode:
            raise _Blocked("command_failed: " + err.decode("utf-8", "replace")[-600:].strip())
        return out
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()


def _xml(raw):
    text = raw.decode("utf-8", "strict") if isinstance(raw, bytes) else raw
    if len(text) > 4 * 1024 * 1024 or "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise _Blocked("unsafe_ui_xml")
    begin, end = text.find("<hierarchy"), text.rfind("</hierarchy>")
    if begin < 0 or end < begin:
        raise _Blocked("ui_xml_unavailable")
    tree = ET.fromstring(text[begin:end + len("</hierarchy>")])
    nodes = list(tree.iter("node"))
    if not nodes or nodes[0].get("package") != PACKAGE:
        raise _Blocked("wrong_foreground_package")
    if any(n.get("package") not in (PACKAGE, "", None) and n.get("clickable") == "true"
           and n.get("enabled") == "true" for n in nodes):
        raise _Blocked("foreign_overlay")
    return tree


def _xml_text(raw):
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    start = text.find("<?xml")
    if start < 0:
        start = text.find("<hierarchy")
    return text[start:text.rfind("</hierarchy>") + len("</hierarchy>")]


def _semantic_control(raw, selector, scope):
    tree = _xml(raw)
    if not isinstance(selector, Mapping) or not isinstance(selector.get("scope"), Mapping):
        raise _Blocked("scoped_start_selector_required")
    stated = selector["scope"]
    if stated.get("subject") != "japanese-premium":
        raise _Blocked("selector_subject_mismatch")
    for key in ("level", "week", "stage", "session"):
        if type(stated.get(key)) is not int or stated[key] <= 0:
            raise _Blocked("selector_scope_incomplete")
        if key in scope and scope[key] != stated[key]:
            raise _Blocked("selector_scope_mismatch")
    if stated["session"] not in (1, 2, 3, 4):
        raise _Blocked("unsupported_session")
    xml_text = _xml_text(raw)
    hashes = {hashlib.sha256(value.encode("utf-8")).hexdigest()
              for value in (xml_text, xml_text[xml_text.find("<hierarchy"):])}
    if selector.get("screen_sha256") not in hashes:
        raise _Blocked("selector_screen_changed")
    rid = selector.get("resource_id", "")
    short = rid.removeprefix(PACKAGE + ":id/")
    allowed = {"flStart" + str(stated["session"]), "btnTodayLessonNext", "ivCtrlPlay", "ivConvCtrlPlay",
               "ivCtrlNext", "btnConvNext"} | {s.split("/", 1)[1] for s in _START_IDS}
    if selector.get("intro_scope") is not None and selector.get("intro_phase") is not None:
        from speech_navigation import next_intro_selector
        verified = next_intro_selector(xml_text[xml_text.find("<hierarchy"):], selector["intro_scope"])
        if not verified or any(verified.get(k) != selector.get(k) for k in ("resource_id", "text", "content_desc", "scope", "intro_phase")):
            raise _Blocked("intro_selector_not_verified")
        allowed.add(short)
    if not rid.startswith(PACKAGE + ":id/") or short not in allowed:
        raise _Blocked("unsupported_armed_transition")
    if short in ("ivCtrlPlay", "ivConvCtrlPlay") and selector.get("content_desc") not in (
            "Play", "Retry", "\uc7ac\uc0dd", "\ub2e4\uc2dc \ub9d0\ud558\uae30"):
        raise _Blocked("ambiguous_play_control")
    nodes = list(tree.iter("node"))
    if short.startswith("flStart"):
        for label, wanted in (("tvLevelTitle", "Level " + str(stated["level"])),
                              ("tvEpTitleNum", "Week " + str(stated["week"]))):
            found = [n for n in nodes if n.get("resource-id") == PACKAGE + ":id/" + label]
            if len(found) != 1 or found[0].get("text") != wanted:
                raise _Blocked("menu_scope_mismatch")
        parents = {child: parent for parent in tree.iter() for child in parent}
        stages = [n for n in nodes if n.get("resource-id") == PACKAGE + ":id/tvStageNum"
                  and n.get("text") == "Stage " + str(stated["stage"])]
        if len(stages) != 1:
            raise _Blocked("stage_not_unique")
        container = stages[0]
        while container in parents:
            container = parents[container]
            stage_nodes = [n for n in container.iter("node") if n.get("resource-id") == PACKAGE + ":id/tvStageNum"]
            if len(stage_nodes) != 1:
                raise _Blocked("stage_selector_not_scoped")
            if any(n.get("resource-id") == rid for n in container.iter("node")):
                nodes = list(container.iter("node"))
                break
    if short == "ivCtrlNext" and any(n.get("resource-id") == PACKAGE + ":id/layoutWord" for n in nodes):
        from speech_word import word_frame, OUTCOMES
        transition = selector.get("word_transition") or {}
        if stated["session"] != 1 or transition.get("app_outcome") not in OUTCOMES:
            raise _Blocked("word_next_requires_observed_feedback")
        view = word_frame(xml_text[xml_text.find("<hierarchy"):])
        if not transition.get("meaning") or view.one("txtPinyin").get("text", "").strip() != transition["meaning"]:
            raise _Blocked("word_next_meaning_changed")
    if short == "btnTodayLessonNext" and not any(n.get("resource-id") == PACKAGE + ":id/flTodayLessonCoverOverlay" for n in nodes):
        raise _Blocked("intro_scope_missing")
    candidates = [n for n in nodes if n.get("resource-id") == rid and n.get("package") == PACKAGE
                  and n.get("text", "") == selector.get("text", "")
                  and n.get("content-desc", "") == selector.get("content_desc", "")
                  and n.get("enabled") == "true" and n.get("clickable") == "true"
                  and n.get("visible-to-user", "true") == "true"]
    if len(candidates) != 1:
        raise _Blocked("semantic_selector_not_unique")
    m = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", candidates[0].get("bounds", ""))
    if not m:
        raise _Blocked("selector_bounds_missing")
    x1, y1, x2, y2 = map(int, m.groups())
    if x2 <= x1 or y2 <= y1:
        raise _Blocked("selector_offscreen")
    return dict(resource_id=rid, bounds=candidates[0].get("bounds"), x=(x1 + x2) // 2, y=(y1 + y2) // 2)


def _process_identity(pid):
    """Return a process birth token, None if absent; errors remain fail-closed."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:
                return None
            raise _Blocked("cannot_verify_process_identity")
        try:
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                raise _Blocked("cannot_read_process_status")
            if code.value != 259:
                return None
            times = [wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                raise _Blocked("cannot_read_process_birth")
            return str(times[0].dwHighDateTime << 32 | times[0].dwLowDateTime)
        finally:
            kernel.CloseHandle(handle)
    try:
        return Path("/proc", str(pid), "stat").read_text().rsplit(")", 1)[1].split()[19]
    except FileNotFoundError:
        return None


def _start_control(tree):
    matches = []
    for node in tree.iter("node"):
        a = node.attrib
        if (a.get("package") != PACKAGE or a.get("resource-id") not in _START_IDS
                or a.get("enabled") != "true" or a.get("clickable") != "true"
                or a.get("visible-to-user", "true") != "true"):
            continue
        m = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", a.get("bounds", ""))
        if not m:
            continue
        x1, y1, x2, y2 = map(int, m.groups())
        if x2 > x1 and y2 > y1:
            matches.append(dict(resource_id=a["resource-id"], bounds=a["bounds"],
                                x=(x1 + x2) // 2, y=(y1 + y2) // 2))
    if len(matches) != 1:
        raise _Blocked("known_start_control_missing_or_ambiguous")
    return matches[0]


def _grade(tree):
    found = set()
    for node in tree.iter("node"):
        if node.get("package") != PACKAGE or node.get("visible-to-user", "true") != "true":
            continue
        if node.get("bounds") == "[0,0][0,0]":
            continue
        for key in ("text", "content-desc"):
            text = node.get(key, "").strip()
            if text in _GRADES:
                found.add(text)
    return next(iter(found)) if len(found) == 1 else None


def _ui_bounds(node):
    match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.get("bounds", ""))
    if match and node.get("visible-to-user", "true") == "true" and node.get("package") == PACKAGE:
        box = list(map(int, match.groups()))
        if box[0] < box[2] and box[1] < box[3]:
            return box
    return None


def _bubble_grade(tree):
    # Select the newest visible bubble FIRST. An older grade must not fill in
    # for a newer bubble still waiting for speech/feedback.
    bubbles = [n for n in tree.iter("node") if n.get("resource-id") == PACKAGE + ":id/cvTalkBubble"
               and n.get("visible-to-user", "true") == "true" and n.get("package") == PACKAGE]
    if not bubbles:
        return None, "current_conversation_bubble_missing"
    bubble = bubbles[-1]
    box = _ui_bounds(bubble)
    texts = [n for n in bubble.iter("node") if n.get("resource-id") == PACKAGE + ":id/tvTalkText"]
    grades = [n for n in bubble.iter("node") if n.get("resource-id") == PACKAGE + ":id/ivTalkGrade"]
    if not box or len(texts) != 1 or len(grades) != 1:
        return None, "current_conversation_bubble_not_graded"
    for node in (texts[0], grades[0]):
        child = _ui_bounds(node)
        if not child or not (box[0] <= child[0] < child[2] <= box[2]
                             and box[1] <= child[1] < child[3] <= box[3]):
            return None, "current_conversation_bubble_bounds_invalid"
    return grades[0], None


def _meaning_grade(tree, meaning):
    if not isinstance(meaning, str) or not meaning:
        return None
    found = []
    for bubble in tree.iter("node"):
        if bubble.get("resource-id") != PACKAGE + ":id/cvTalkBubble":
            continue
        if any(n.get("resource-id") == PACKAGE + ":id/tvTalkMean" and n.get("text") == meaning
               for n in bubble.iter("node")):
            grade, _ = _bubble_grade(bubble)
            if grade is not None:
                found.append(_ui_bounds(grade))
    return found[0] if len(found) == 1 else None


def _template_grade(path, templates, box, previous_box, index):
    """Match APK badge pixels, not engine score thresholds or OCR guesses.

    The PNG must already have a target-bound probe checkpoint. In a scrolling
    dialogue the latest badge must be in the current speaker's observed column,
    different from the previous speaker. Same-column turns need another oracle.
    """
    import cv2
    import numpy as np
    from PIL import Image
    if not box or (index and (not previous_box or abs(previous_box[0] - box[0]) < 2 * (box[2]-box[0]))):
        return dict(grade=None, reason="grade_template_turn_not_distinguishable")
    image = np.array(Image.open(path).convert("RGB"))
    width, height = box[2] - box[0], box[3] - box[1]
    if not 20 <= width <= 300 or not 20 <= height <= 300:
        return dict(grade=None, reason="grade_template_size_unverified")
    candidates = []
    for grade, source in templates.items():
        reference = Image.open(source).convert("RGBA").resize((width, height), Image.Resampling.BILINEAR)
        rgba = np.array(reference)
        response = cv2.matchTemplate(image, rgba[:, :, :3], cv2.TM_SQDIFF_NORMED, mask=rgba[:, :, 3])
        response = np.nan_to_num(response, nan=1., posinf=1., neginf=1.)
        for _ in range(8):
            score, _, point, _ = cv2.minMaxLoc(response)
            if score >= .04:
                break
            x, y = point
            candidates.append(dict(grade=grade, distance=score, roi=[x, y, x+width, y+height]))
            response[max(0,y-height//2):y+height//2+1, max(0,x-width//2):x+width//2+1] = 1.
    selected = []
    for candidate in sorted(candidates, key=lambda x:x['distance']):
        close = [m for m in selected if abs(m['roi'][0]-candidate['roi'][0]) < width//2
                 and abs(m['roi'][1]-candidate['roi'][1]) < height//2]
        if close:
            if close[0]['grade'] != candidate['grade']:
                return dict(grade=None, reason="grade_template_ambiguous")
        else:
            selected.append(candidate)
    if not selected or len(selected) > index+1:
        return dict(grade=None, reason="grade_template_count_unverified")
    selected.sort(key=lambda x:x['roi'][1])
    current = selected[-1]
    if abs(current['roi'][0]-box[0]) > 6 or (len(selected)>1 and current['roi'][1]-selected[-2]['roi'][1] < height//2):
        return dict(grade=None, reason="grade_template_current_turn_unverified")
    return dict(**current, reason=None, matches=selected, source="installed_apk_badge_pixels",
                app_score=None, app_score_reason="ui_numeric_score_not_observed", score_source="captured_apk_template")


class DeviceAdapter:
    def __init__(self, host, root):
        self.host = host
        self.root = Path(root).resolve()
        self._input_lock = getattr(host, "_adb_input_lock", threading.Lock())
        self._preview_hashes = {}

    def _word_templates(self, serial, apk_hash, deadline, stop):
        from speech_word import APK_SHA256
        if apk_hash != APK_SHA256:
            raise _Blocked("word_feedback_apk_not_verified")
        raw = self._adb(serial, ["shell", "pm", "path", PACKAGE], deadline, stop).decode().strip()
        if not re.fullmatch(r"package:/data/app/[-A-Za-z0-9_./=+~]+/base\.apk", raw) or ".." in raw:
            raise _Blocked("word_reference_apk_path_unverified")
        data = self._adb(serial, ["exec-out", "cat", raw[8:]], deadline, stop, timeout=30)
        if hashlib.sha256(data).hexdigest() != apk_hash:
            raise _Blocked("word_reference_apk_hash_mismatch")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            templates = {}
            for state in ("correct", "wrong", "ready", "speak"):
                name = "res/drawable-nodpi-v4/icon_study_word_" + state + ".png"
                if archive.getinfo(name).file_size > 524288:
                    raise _Blocked("word_reference_image_too_large")
                templates["word_" + state] = archive.read(name)
        return templates

    def _observe_word(self, tree, folder, report, templates):
        from speech_word import observe_word, observe_word_png
        from speech_navigation import NavigationBlocked
        png = (folder / "capture.png").read_bytes()
        if (report.get("png_capture_valid") is not True
                or report.get("png_sha256") != hashlib.sha256(png).hexdigest()):
            return dict(app_outcome=None, reason="word_capture_checkpoint_unverified")
        if report.get('capture_valid') is True:
            raw = _xml_text((folder / "result.xml").read_bytes())
            try:
                observation = observe_word(raw[raw.find("<hierarchy"):], png, templates)
            except NavigationBlocked:
                # A terminal prompt may appear after the PNG while HCI retains
                # the same word. Do not use that later XML to judge the image.
                observation = observe_word_png(png, templates)
        else:
            observation = observe_word_png(png, templates)
        (folder / "word-observation.json").write_text(json.dumps(observation), encoding="utf-8")
        return observation

    def _grade_templates(self, serial, apk_hash, deadline, stop):
        if not re.fullmatch(r"[a-f0-9]{64}", apk_hash or ""):
            return None
        folder = self.root / ".speech-grade-templates" / apk_hash
        manifest = self._read_json(folder / "manifest.json")
        if manifest and all((folder / (g + ".png")).is_file() and
                            hashlib.sha256((folder / (g + ".png")).read_bytes()).hexdigest() == manifest.get(g)
                            for g in _GRADES):
            return {g: folder / (g + ".png") for g in _GRADES}
        raw = self._adb(serial, ["shell", "pm", "path", PACKAGE], deadline, stop).decode().strip()
        if not re.fullmatch(r"package:/data/app/[-A-Za-z0-9_./=+~]+/base\.apk", raw) or ".." in raw:
            raise _Blocked("grade_reference_apk_path_unverified")
        data = self._adb(serial, ["exec-out", "cat", raw[8:]], deadline, stop, timeout=30)
        if hashlib.sha256(data).hexdigest() != apk_hash:
            raise _Blocked("grade_reference_apk_hash_mismatch")
        folder.mkdir(parents=True, exist_ok=True)
        manifest = {}
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for grade in _GRADES:
                name = "res/drawable-nodpi-v4/icon_recognition_" + grade.lower() + ".png"
                if archive.getinfo(name).file_size > 524288:
                    raise _Blocked("grade_reference_image_too_large")
                png = archive.read(name)
                (folder / (grade + ".png")).write_bytes(png)
                manifest[grade] = hashlib.sha256(png).hexdigest()
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return {g: folder / (g + ".png") for g in _GRADES}

    def _path(self, path):
        path = Path(path)
        return (path if path.is_absolute() else self.root / path).resolve()

    @staticmethod
    def _serial(device):
        serial = device.get("serial") if isinstance(device, Mapping) else device
        if not isinstance(serial, str) or serial.startswith("-") or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", serial):
            raise _Blocked("explicit_device_serial_required")
        return serial

    @staticmethod
    def _tool(name):
        if name in ("java", "javac"):
            for variable in ("SPEECH_JAVA_HOME", "JAVA_HOME"):
                home = os.environ.get(variable)
                if home:
                    path = Path(home) / "bin" / (name + (".exe" if os.name == "nt" else ""))
                    if not path.is_file():
                        raise _Blocked(variable + "_missing_" + name)
                    return str(path)
        path = shutil.which(name)
        if not path:
            raise _Blocked(name + "_not_on_PATH")
        return path

    def _adb(self, serial, args, deadline, stop=None, timeout=10):
        # Explicit loopback server avoids inherited remote ADB server settings.
        return _command([self._tool("adb"), "-H", "127.0.0.1", "-P", "5037",
                         "-s", serial, *args], deadline, stop, timeout)

    def devices(self):
        out = _command([self._tool("adb"), "-H", "127.0.0.1", "-P", "5037", "devices"],
                       time.monotonic() + 10).decode("utf-8", "replace")
        return [dict(serial=p[0], state=p[1]) for line in out.splitlines()
                if len(p := line.split()) == 2 and p[1] in ("device", "offline", "unauthorized")]

    def subjects(self, device):
        from speech_subjects import inventory
        serial = self._serial(device)
        deadline = time.monotonic() + 30
        return inventory(serial, lambda args: self._adb(serial, args, deadline))

    def _preflight(self, serial, deadline, stop=None):
        result = dict(serial=serial, package=PACKAGE, build=None, supported=False,
                      signature_verified=False, reason=None)
        if self._adb(serial, ["get-state"], deadline, stop).strip() != b"device":
            raise _Blocked("device_not_ready")
        dump = self._adb(serial, ["shell", "dumpsys", "package", PACKAGE], deadline, stop).decode("utf-8", "replace")
        if not re.search(r"Package \[" + re.escape(PACKAGE) + r"\]", dump):
            raise _Blocked("supported_package_not_installed")
        codes = set(re.findall(r"\bversionCode=(\d+)\b", dump))
        result["build"] = int(next(iter(codes))) if len(codes) == 1 else None
        if result["build"] != BUILD:
            result["reason"] = "unsupported_build"
            return result
        paths = self._adb(serial, ["shell", "pm", "path", PACKAGE], deadline, stop).decode().splitlines()
        if len(paths) != 1 or not paths[0].startswith("package:"):
            raise _Blocked("single_base_apk_required")
        apk = paths[0][8:]
        if not re.fullmatch(r"/data/app/[-A-Za-z0-9_./=+~]+/base\.apk", apk) or ".." in apk.split("/"):
            raise _Blocked("untrusted_apk_path")
        digest = self._adb(serial, ["shell", "sha256sum", apk], deadline, stop).decode().split()
        if len(digest) != 2 or not re.fullmatch(r"[a-fA-F0-9]{64}", digest[0]) or digest[1] != apk:
            raise _Blocked("apk_hash_unavailable")
        result["apk_sha256"] = digest[0].lower()
        focus = self._adb(serial, ["shell", "dumpsys", "activity", "activities"], deadline, stop).decode("utf-8", "replace")
        resumed = re.findall(r"(?:mResumedActivity|topResumedActivity)\s*[:=][^\n]*?\s([\w.]+)/[\w.$]+", focus)
        if not resumed or set(resumed) != {PACKAGE}:
            result["reason"] = "supported_app_not_foreground"
            return result
        pid = self._adb(serial, ["shell", "pidof", PACKAGE], deadline, stop).decode().split()
        if len(pid) != 1 or not pid[0].isdigit():
            result["reason"] = "unique_app_process_required"
            return result
        result["pid"] = int(pid[0])
        # `adb jdwp` is a streaming tracker on some ADB versions. SocketAttach
        # with a finite timeout is the authoritative debuggability check.
        result["debuggable"] = None
        result["supported"] = True
        result["signature_check"] = "required_on_attach"
        return result

    def inspect(self, device, *, runtime_snapshot=False):
        """No attach by default. Snapshot verifies hook signatures, then detaches.

        supported means build/process preflight only unless signature_verified is
        true. Unloaded classes or an active recording block the attach operation.
        """
        serial = None
        try:
            serial = self._serial(device)
            info = self._preflight(serial, time.monotonic() + 30)
            if info.get("apk_sha256"):
                self._preview_hashes[serial] = info["apk_sha256"]
            if runtime_snapshot and info["supported"]:
                snap = self._operate(serial, None, None, self.root / "speech-inspect", None, info)
                info.update(runtime=snap.get("runtime"), signature_verified=snap.get("signature_verified", False),
                            cleanup_ok=snap["cleanup_ok"], reason=snap["reason"], files=snap["files"],
                            evidence_dir=str(self.root / "speech-inspect"))
                info["supported"] = snap["status"] == "completed" and snap["cleanup_ok"]
            return info
        except Exception as exc:
            return dict(serial=serial, package=PACKAGE, build=None, supported=False,
                        signature_verified=False, reason=str(exc))

    def _lease_path(self, serial):
        return self.root / ".speech-adapter-leases" / (hashlib.sha256(serial.encode()).hexdigest() + ".json")

    @contextmanager
    def _lease_guard(self, serial):
        # Keep the sidecar inode stable: deleting a lock file can split waiters
        # across different locks. The OS releases the byte lock after a crash.
        if not _LEASE_LOCK.acquire(timeout=5):
            raise _Blocked("lease_busy")
        stream = None
        acquired = False
        try:
            path = self._lease_path(serial).with_suffix(".lock")
            path.parent.mkdir(parents=True, exist_ok=True)
            stream = path.open("a+b")
            if not path.stat().st_size:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                raise _Blocked("lease_busy") from exc
            yield
        finally:
            if stream is not None:
                try:
                    if acquired:
                        stream.seek(0)
                        if os.name == "nt":
                            import msvcrt
                            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                finally:
                    stream.close()
            _LEASE_LOCK.release()

    @staticmethod
    def _lease_process(entry):
        if (not isinstance(entry, dict) or type(entry.get("pid")) is not int or entry["pid"] <= 0
                or "birth" not in entry or (entry["birth"] is not None and not isinstance(entry["birth"], str))):
            raise _Blocked("invalid_lease_process_identity")
        current = _process_identity(entry["pid"])
        return current is not None and (entry["birth"] is None or current == entry["birth"])

    def check_cleanup(self, device):
        """Clear a proven resource-free lease; never kill or remove a forward."""
        try:
            serial = self._serial(device)
            with _LOCK:
                if serial in _BUSY:
                    raise _Blocked("device_adapter_busy")
            with self._lease_guard(serial):
                path = self._lease_path(serial)
                lease = self._read_json(path)
                if lease is None:
                    return dict(cleanup_ok=True, reason="no_owned_lease")
                if not isinstance(lease, dict) or lease.get("serial") != serial:
                    raise _Blocked("lease_serial_mismatch")
                if lease.get("state") not in ("active", "cleanup_complete", "cleanup_incomplete"):
                    raise _Blocked("invalid_lease_state")
                if self._lease_process(lease.get("owner")) and lease["state"] == "active":
                    raise _Blocked("owned_owner_still_alive")
                if lease.get("helper") is not None:
                    if self._lease_process(lease["helper"]):
                        raise _Blocked("owned_helper_still_alive")
                elif lease.get("helper_launch_started"):
                    raise _Blocked("helper_launch_unconfirmed")
                owned = lease.get("forward")
                if owned is not None:
                    if (not isinstance(owned, list) or len(owned) != 3 or owned[0] != serial
                            or not isinstance(owned[1], str) or not re.fullmatch(r"tcp:[1-9]\d{0,4}", owned[1])
                            or not isinstance(owned[2], str) or not re.fullmatch(r"jdwp:[1-9]\d*", owned[2])):
                        raise _Blocked("invalid_owned_forward")
                    rows = [line.split() for line in self._adb(
                        serial, ["forward", "--list"], time.monotonic() + 5).decode().splitlines() if line.strip()]
                    if any(len(row) != 3 for row in rows):
                        raise _Blocked("forward_list_unverifiable")
                    if owned in rows:
                        raise _Blocked("owned_forward_still_present")
                elif lease.get("forward_allocation_started"):
                    raise _Blocked("forward_allocation_unconfirmed")
                if self._read_json(path) != lease:
                    raise _Blocked("lease_changed_during_cleanup_check")
                path.unlink()
                return dict(cleanup_ok=True, reason="owned_resources_absent_lease_cleared", lease_retained=False)
        except Exception as exc:
            return dict(cleanup_ok=False, reason=str(exc))

    def _claim_lease(self, serial, folder):
        path = self._lease_path(serial)
        path.parent.mkdir(parents=True, exist_ok=True)
        lease = dict(serial=serial, state="active", attempt=str(folder),
                     owner=dict(pid=os.getpid(), birth=_process_identity(os.getpid())))
        with self._lease_guard(serial):
            try:
                with path.open("x", encoding="utf-8") as stream:
                    json.dump(lease, stream)
            except FileExistsError:
                raise _Blocked("owned_lease_exists_check_cleanup_and_review")
        return lease

    @staticmethod
    def _same_lease(current, lease):
        return (isinstance(current, dict) and current.get("serial") == lease.get("serial")
                and current.get("owner") == lease.get("owner") and current.get("attempt") == lease.get("attempt"))

    def _save_lease(self, serial, lease):
        with self._lease_guard(serial):
            path = self._lease_path(serial)
            if not self._same_lease(self._read_json(path), lease):
                raise _Blocked("lease_ownership_changed")
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(lease), encoding="utf-8")
            temp.replace(path)

    def _release_lease(self, serial, lease):
        with self._lease_guard(serial):
            path = self._lease_path(serial)
            if self._read_json(path) != lease:
                raise _Blocked("lease_ownership_changed")
            path.unlink()

    def _acquire_input(self, deadline, stop):
        while not self._input_lock.acquire(timeout=.1):
            _check(deadline, stop)

    def _start(self, serial, selector, scope, fallback, folder, deadline, stop, index=0, word_templates=None):
        self._acquire_input(deadline, stop)
        try:
            _check(deadline, stop)
            if index and (folder / "recording.json").exists():
                raise _Blocked("intro_navigation_after_recording")
            raw = self._adb(serial, ["exec-out", "uiautomator", "dump", "/dev/tty"], deadline, stop)
            (folder / ("start-%d.xml" % index)).write_bytes(raw)
            control = _semantic_control(raw, selector, scope) if selector else _start_control(_xml(raw))
            if not selector and (control != fallback or _grade(_xml(raw))):
                raise _Blocked("start_control_changed")
            if selector and selector.get("word_transition"):
                from speech_word import observe_word
                if not word_templates:
                    raise _Blocked("word_transition_templates_missing")
                png = self._adb(serial, ["exec-out", "screencap", "-p"], deadline, stop)
                (folder / "word-transition.png").write_bytes(png)
                xml_text = _xml_text(raw)
                observed = observe_word(xml_text[xml_text.find("<hierarchy"):], png, word_templates)
                if observed.get("app_outcome") != selector["word_transition"]["app_outcome"]:
                    raise _Blocked("word_feedback_changed_before_input")
            _check(deadline, stop)
            if index and (folder / "recording.json").exists():
                raise _Blocked("intro_navigation_after_recording")
            (folder / "start.signal").touch()
            self._adb(serial, ["shell", "input", "tap", str(control["x"]), str(control["y"])], deadline, stop)
            return control
        finally:
            self._input_lock.release()

    def _intro(self, serial, selector, scope, folder, deadline, stop, seen):
        raw = self._adb(serial, ["exec-out", "uiautomator", "dump", "/dev/tty"], deadline, stop)
        tree = _xml(raw)
        digest = hashlib.sha256(_xml_text(raw).encode("utf-8")).hexdigest()
        if (folder / "recording.json").exists() or digest in seen:
            return False
        if selector.get("intro_scope") is not None:
            from speech_navigation import next_intro_selector
            text = _xml_text(raw)
            intro = next_intro_selector(text[text.find("<hierarchy"):], selector["intro_scope"])
            if intro is None:
                return False
            if len(seen) >= 7:
                raise _Blocked("intro_step_limit")
            self._start(serial, intro, scope, None, folder, deadline, stop, len(seen))
            seen.add(digest)
            return True
        if not any(n.get("resource-id") == PACKAGE + ":id/flTodayLessonCoverOverlay" for n in tree.iter("node")):
            return False
        nodes = [n for n in tree.iter("node") if n.get("resource-id") == PACKAGE + ":id/btnTodayLessonNext"]
        if len(nodes) != 1:
            raise _Blocked("known_intro_next_not_unique")
        if len(seen) >= 3:
            raise _Blocked("intro_step_limit")
        intro = dict(resource_id=nodes[0].get("resource-id"), text=nodes[0].get("text", ""),
                     content_desc=nodes[0].get("content-desc", ""), scope=selector["scope"], screen_sha256=digest)
        self._start(serial, intro, scope, None, folder, deadline, stop, len(seen))
        seen.add(digest)
        return True

    def _capture(self, serial, folder, deadline, stop):
        self._acquire_input(deadline, stop)
        try:
            # One PNG at the probe's observation boundary. Never recapture later
            # to manufacture a grade for an already completed recording.
            png = self._adb(serial, ["exec-out", "screencap", "-p"], deadline, stop)
            if not png.startswith(b"\x89PNG\r\n\x1a\n"):
                raise _Blocked("capture_invalid_png")
            (folder / "capture.png").write_bytes(png)
            # The probe validates this checkpoint before the slower XML dump.
            # XML validity remains separate; its coordinates cannot be borrowed.
            (folder / "png.signal").write_text(hashlib.sha256(png).hexdigest(), encoding="ascii")
            xml = self._adb(serial, ["exec-out", "uiautomator", "dump", "/dev/tty"], deadline, stop)
            (folder / "result.xml").write_bytes(xml)
            (folder / "capture.signal").touch()
            return _xml(xml)
        finally:
            self._input_lock.release()

    def _observe_grade(self, tree, folder, deadline, stop, sequence_index=None):
        # Only dedicated grade graphics / current blank-fill feedback. Never a
        # full-screen OCR search, previous-results list, or score-to-grade map.
        ids = {PACKAGE + ":id/ivTalkGrade", PACKAGE + ":id/llFBCenter"}
        nodes = [n for n in tree.iter("node") if n.get("resource-id") in ids and n.get("package") == PACKAGE
                 and n.get("visible-to-user", "true") == "true"]
        if sequence_index is not None:
            selected, reason = _bubble_grade(tree)
            if selected is None:
                return None, reason
            nodes = [selected]
        if len(nodes) != 1:
            return None, "grade_roi_missing_or_ambiguous"
        bounds = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", nodes[0].get("bounds", ""))
        if not bounds:
            return None, "grade_roi_bounds_missing"
        roi = list(map(int, bounds.groups()))
        out = _command([sys.executable, str(Path(__file__).resolve()), "--local-grade-ocr",
                        str(folder / "capture.png"), json.dumps(roi)], deadline, stop, 20)
        (folder / "ocr.log").write_bytes(out)
        data = json.loads(out.decode("utf-8"))
        return data.get("grade"), data.get("reason")

    def _observe_sequence_png(self, tree, folder, target, meanings, deadline, stop):
        meaning = target.get("meaning")
        if not isinstance(meaning, str) or not meaning.strip():
            return None, "exact_korean_meaning_required_for_png_anchor"
        templates = []
        for bubble in tree.iter("node"):
            if bubble.get("resource-id") != PACKAGE + ":id/cvTalkBubble" or not _ui_bounds(bubble):
                continue
            labels = [n for n in bubble.iter("node") if n.get("resource-id") == PACKAGE + ":id/tvTalkMean"
                      and n.get("text") == meaning and _ui_bounds(n)]
            grade, _ = _bubble_grade(bubble)
            if len(labels) == 1 and grade is not None:
                templates.append(dict(mean=_ui_bounds(labels[0]), grade=_ui_bounds(grade)))
        areas = [_ui_bounds(n) for n in tree.iter("node") if n.get("resource-id") == PACKAGE + ":id/svConvTalk"]
        if len(templates) != 1 or len(areas) != 1 or areas[0] is None:
            return None, "meaning_layout_template_missing_or_ambiguous"
        spec = dict(meaning=meaning, meanings=meanings, area=areas[0], **templates[0])
        out = _command([sys.executable, str(Path(__file__).resolve()), "--local-conversation-ocr",
                        str(folder / "capture.png"), json.dumps(spec, ensure_ascii=True)], deadline, stop, 35)
        (folder / "ocr.log").write_bytes(out)
        data = json.loads(out.decode("utf-8"))
        return data.get("grade"), data.get("reason")

    @staticmethod
    def _target(target):
        spec = dict(target) if isinstance(target, Mapping) else {"text": target}
        text = spec.get("text", spec.get("target"))
        if not isinstance(text, str) or not text or len(text) > 4096 or any(c in text for c in "\x00\r\n"):
            raise _Blocked("exact_runtime_target_required")
        slot = spec.get("slot")
        if slot is not None and (type(slot) is not int or slot < 0):
            raise _Blocked("invalid_slot")
        if spec.get("actualWordID") is not None:
            raise _Blocked("actual_word_id_unavailable")
        try:
            timeout = float(spec.get("timeout_s", 90))
        except (TypeError, ValueError) as exc:
            raise _Blocked("timeout_s_must_be_5_to_300") from exc
        if not math.isfinite(timeout) or not 5 <= timeout <= 300:
            raise _Blocked("timeout_s_must_be_5_to_300")
        return text, slot, timeout

    def run_attempt(self, device, target, pcm_path, evidence_dir, stop_event):
        return self._operate(device, target, pcm_path, evidence_dir, stop_event)

    def _sequence_plan(self, targets):
        if not isinstance(targets, list) or len(targets) != 4 or any(not isinstance(t, Mapping) for t in targets):
            raise _Blocked("complete_four_item_conversation_required")
        selector = targets[0].get("start_selector")
        if not isinstance(selector, Mapping):
            raise _Blocked("first_start_selector_required")
        scopes = []
        prepared = []
        for index, target in enumerate(targets):
            text, slot, timeout = self._target(target)
            scope = target.get("scope")
            if not isinstance(scope, Mapping) or any(type(scope.get(k)) is not int or scope[k] <= 0
                                                    for k in ("level", "week", "stage", "session")):
                raise _Blocked("complete_sequence_scope_required")
            word_sequence = scope["session"] == 1 and target.get('kind') == 'word'
            if (scope["session"] != 4 and not word_sequence) or scope.get("subject", "japanese-premium") != "japanese-premium":
                raise _Blocked("conversation_scope_required")
            if word_sequence:
                if slot not in (None, 0) or target.get('order') != index + 1 or not target.get('db_sha256'):
                    raise _Blocked('word_sequence_identity_required')
                slot = 0
            scopes.append(tuple(scope[k] for k in ("level", "week", "stage", "session")))
            if index and target.get("start_selector") is not None:
                raise _Blocked("only_first_item_may_start_sequence")
            word = str(target.get("word_id", ""))
            if not word.isdigit() or int(word) <= 0:
                raise _Blocked("sequence_word_id_required")
            if not target.get("pcm_path") or not target.get("evidence_dir"):
                raise _Blocked("sequence_pcm_and_evidence_required")
            pcm = self._path(target["pcm_path"])
            with pcm.open("rb") as stream:
                raw = stream.read(480001)
            if not 0 < len(raw) <= 480000 or len(raw) % 2 or raw.startswith((b"RIFF", b"RF64", b"OggS", b"fLaC")):
                raise _Blocked("invalid_sequence_pcm")
            prepared.append(dict(target=dict(target), text=text, slot=slot, timeout=timeout, pcm=pcm,
                                 sha256=hashlib.sha256(raw).hexdigest(), samples=len(raw) // 2,
                                 base=self._path(target["evidence_dir"])))
        if len(set(scopes)) != 1 or len({str(t["word_id"]) for t in targets}) != 4:
            raise _Blocked("sequence_scope_or_word_ids_mismatch")
        if any("order" in t for t in targets) and [t.get("order") for t in targets] != [1, 2, 3, 4]:
            raise _Blocked("sequence_order_mismatch")
        if any("db_sha256" in t for t in targets):
            hashes = {t.get("db_sha256") for t in targets}
            if len(hashes) != 1 or not re.fullmatch(r"[a-fA-F0-9]{64}", next(iter(hashes)) or ""):
                raise _Blocked("sequence_database_fingerprint_mismatch")
        return prepared

    @staticmethod
    def _sequence_terminal(tree, word=False):
        if word:
            from speech_word import word_frame
            from speech_navigation import Screen
            try:
                raw = ET.tostring(tree, encoding='unicode')
                view = Screen(raw)
                if view.find('flStagePopupOverlay'):
                    popup = view.one('flStagePopupOverlay')
                    view.one('tvStagePopupTitle', '\ub2e8\uc5b4 \ud034\uc988', scope=popup)
                    view.one('tvStagePopupDesc', '\ubc30\uc6b4 \ub2e8\uc5b4\ub97c \ud034\uc988\ub85c \ubcf5\uc2b5\ud574 \ubcf4\uc138\uc694!', scope=popup)
                    view.one('btnStagePopupStart', '\uc2dc\uc791', scope=popup)
                    view.one('layoutWord', scope=view.one('cvStudyCard'))
                    return not any(view.find(rid) for rid in ('layoutMessageBox', 'layoutSingleQuiz', 'tvConvGuide', 'rlLecturePopup'))
                view = word_frame(raw)
                view.one('ivStudyWordSpeakIcon', scope=view.one('llSpeak', scope=view.one('cvStudyCard')))
                return True
            except ValueError:
                return False
            except RuntimeError:
                return False
        nodes = list(tree.iter("node"))
        labels = [n.get("text") for n in nodes if n.get("resource-id") == PACKAGE + ":id/tvSlotName"]
        buttons = [n for n in nodes if n.get("resource-id") == PACKAGE + ":id/btnResultConfirm"
                   and n.get("enabled") == "true" and n.get("clickable") == "true"]
        result_page = (labels == ["\ud68c\ud654"] and len(buttons) == 1
                       and any(n.get("resource-id") == PACKAGE + ":id/tvScoreText" for n in nodes))
        next_controls = [n for n in nodes if n.get("resource-id") == PACKAGE + ":id/btnConvNext"
                         and n.get("enabled") == "true" and n.get("clickable") == "true"
                         and (n.get("text") == "\ub2e4\uc74c" or n.get("content-desc") in ("\ub2e4\uc74c", "Next"))]
        quiescent_four = (len(next_controls) == 1 and _ui_bounds(next_controls[0]) is not None
                         and _bubble_grade(tree)[0] is not None
                         and any(n.get("resource-id") == PACKAGE + ":id/tvConvGuide" for n in nodes))
        return result_page or quiescent_four

    def run_sequence(self, device, targets, stop_event):
        """Inject exactly four service-validated ordered conversation or word items.

        One attachment; no per-line navigation or automatic retry. Results are
        final only after shared cleanup. Capture races remain inconclusive.
        The helper stays armed until a known terminal session UI is observed;
        an unexpected next recording is canceled, not filled with ambient PCM.
        No callback: callers can show a sequence-running phase until return.
        """
        started = time.monotonic()
        try:
            plan = self._sequence_plan(targets)
        except Exception as exc:
            rows = targets if isinstance(targets, list) else [targets]
            return [dict(status="blocked", target=t, target_verified=False, full_input=False,
                         cleanup_ok=self.check_cleanup(device)["cleanup_ok"], stopped=False,
                         app_grade=None, app_score=None, app_score_reason="attempt_not_verified",
                         engine_state=None, scores={}, files=[], reason=str(exc)) for t in rows]
        word_sequence = all(p['target'].get('kind') == 'word' and p['target']['scope']['session'] == 1 for p in plan)
        if word_sequence:
            # Build 11 speaks each word in STEP_STAGE2 and STEP_STAGE3 before advancing.
            plan = [dict(p, word_phase=phase) for p in plan for phase in (1, 2)]
        deadline = started + min(300, sum(p["timeout"] for p in plan) + 20)
        first = plan[0]
        shared = self._operate(device, first["target"], first["pcm"], first["base"], stop_event, sequence=plan)
        reports = shared.get("sequence_reports", [])
        results = []
        for index, item in enumerate(plan):
            report = reports[index] if index < len(reports) and isinstance(reports[index], dict) else {}
            result = dict(status="blocked", target=item["target"], target_verified=False, full_input=False,
                          cleanup_ok=shared["cleanup_ok"], cleanup=shared["cleanup"], stopped=shared["stopped"],
                          app_grade=None, app_score=None, app_score_reason="ui_numeric_score_not_observed",
                          engine_state=None, scores={}, states=[], files=[], reason=shared.get("reason"),
                          input=dict(sha256=item["sha256"], samples=item["samples"]),
                          retry_available=False, retry_reason="whole_conversation_replays_all_four_items",
                          sequence_index=index, signature_verified=False,
                          identity=dict(word_id=item["target"]["word_id"], runtime_word_id_verified=False,
                                        source="service_validated_order_plus_runtime_text_and_slot"))
            if word_sequence:
                result['word_phase'] = item['word_phase']
            self._merge_report(result, report)
            result["stopped"] = bool(result["stopped"] or shared["stopped"])
            folder = item.get("folder")
            try:
                if folder:
                    source_log = first["folder"] / "probe.log"
                    if index and source_log.is_file():
                        shutil.copyfile(source_log, folder / "probe.log")
                    result["log"] = (folder / "probe.log").relative_to(item["base"]).as_posix()
                    if (folder / "capture.png").is_file():
                        result["capture"] = (folder / "capture.png").relative_to(item["base"]).as_posix()
                if result["stopped"]:
                    result.update(status="stopped", reason="sequence_stopped")
                elif not result["cleanup_ok"]:
                    result.update(status="blocked", reason="sequence_cleanup_incomplete")
                elif not report.get("completed") or not result["target_verified"] or not result["full_input"]:
                    result.update(status="blocked", reason=report.get("reason") or shared.get("reason") or "sequence_item_not_completed")
                elif (type(report.get("sequence_index")) is not int or report["sequence_index"] != index
                      or report.get("runtime", {}).get("text") != item["text"]
                      or (item["slot"] is not None and report.get("runtime", {}).get("slot") != item["slot"])):
                    result.update(status="inconclusive", reason="sequence_capture_checkpoint_mismatch")
                elif item.get("capture_tree") is None:
                    result.update(status="inconclusive", reason="capture_missing_or_crossed_next_recording")
                else:
                    png_verified = (report.get("png_capture_valid") is True and report.get("png_sha256")
                                    == hashlib.sha256((folder / "capture.png").read_bytes()).hexdigest())
                    if item.get('word_templates'):
                        observation = self._observe_word(item['capture_tree'], folder, report, item['word_templates'])
                        result['app_outcome'] = observation.get('app_outcome')
                        result['outcome_evidence'] = observation
                        result.update(status='completed' if result['app_outcome'] else 'inconclusive',
                                      reason=observation.get('reason'))
                        # Word outcomes are check/cross icons, never named grades.
                        grade, reason = None, result['reason']
                    elif item.get("grade_templates") and png_verified:
                        previous = plan[index-1] if index else None
                        previous_box = (_meaning_grade(previous["capture_tree"], previous["target"].get("meaning"))
                                        if previous and previous.get("capture_tree") is not None else None)
                        observation = _template_grade(folder / "capture.png", item["grade_templates"],
                                                      _meaning_grade(item["capture_tree"], item["target"].get("meaning")),
                                                      previous_box, index)
                        (folder / "ocr.log").write_text(json.dumps(observation), encoding="utf-8")
                        grade, reason = observation.get("grade"), observation.get("reason")
                    elif report.get("capture_valid") is True:
                        grade, reason = self._observe_grade(item["capture_tree"], folder, deadline, stop_event, sequence_index=index)
                    elif png_verified:
                        grade, reason = self._observe_sequence_png(item["capture_tree"], folder, item["target"],
                                                                  [p["target"].get("meaning") for p in plan], deadline, stop_event)
                    else:
                        grade, reason = None, "capture_missing_or_crossed_next_recording"
                    result["app_grade"] = grade if grade in _GRADES else None
                    result.update(status="completed" if result["app_grade"] or result.get('app_outcome') else "inconclusive",
                                  reason=reason or (None if result["app_grade"] or result.get('app_outcome') else "ui_grade_inconclusive"))
                    self._numeric_evidence(result, folder)
            except _Stopped:
                result.update(status="stopped", stopped=True, reason="sequence_stopped")
            except Exception as exc:
                result.update(status="inconclusive", app_grade=None, reason="grade_observation_unavailable: " + str(exc))
            if folder:
                result["files"] = sorted(p.relative_to(item["base"]).as_posix() for p in folder.iterdir()
                                         if p.suffix in (".xml", ".png", ".log"))
                path = folder / "attempt.log"
                relative = path.relative_to(item["base"]).as_posix()
                if relative not in result["files"]:
                    result["files"].append(relative)
                try:
                    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                except OSError as exc:
                    result.update(status="failed", app_grade=None, reason="evidence_write_failed: " + str(exc))
            results.append(result)
        return self._word_phase_results(results) if word_sequence else results

    @staticmethod
    def _word_phase_results(results):
        combined = []
        for index in range(0, len(results), 2):
            phases = results[index:index + 2]
            incomplete = [p for p in phases if p.get('status') != 'completed'
                          or not p.get('target_verified') or not p.get('full_input')
                          or p.get('signature_verified') is not True
                          or not p.get('cleanup_ok') or p.get('app_outcome') not in {'word_correct', 'word_wrong'}]
            selected = (incomplete[0] if incomplete else
                        next((p for p in phases if p['app_outcome'] == 'word_wrong'), phases[-1]))
            result = dict(selected, word_phases=phases,
                          files=sorted({f for p in phases for f in p.get('files', [])}))
            if len(phases) != 2 or incomplete:
                result.update(status='blocked', app_outcome=None, reason=selected.get('reason') or 'word_phase_incomplete')
            combined.append(result)
        return combined

    @staticmethod
    def _numeric_evidence(result, folder):
        if not (folder / "ocr.log").is_file():
            return
        numeric = DeviceAdapter._read_json(folder / "ocr.log")
        score = numeric.get("app_score") if isinstance(numeric, dict) else None
        if type(score) is int and 0 <= score <= 100 and numeric.get("score_source") == "captured_grade_roi_ocr":
            result.update(app_score=score, app_score_reason=None)
        elif isinstance(numeric, dict):
            result["app_score_reason"] = numeric.get("app_score_reason") or "ui_numeric_score_not_observed"

    def _java(self, folder, deadline, stop):
        javac, java = self._tool("javac"), self._tool("java")
        classes = folder / "classes"
        classes.mkdir()
        source = Path(__file__).resolve().parent / "tools" / "speech" / "SpeechProbe.java"
        tools_jar = Path(javac).resolve().parent.parent / "lib" / "tools.jar"
        cp = str(classes) + (os.pathsep + str(tools_jar) if tools_jar.is_file() else "")
        _command([javac, "-encoding", "UTF-8", "-cp", cp, "-d", str(classes), str(source)], deadline, stop, 30)
        return [java, "-Dfile.encoding=UTF-8", "-cp", cp, "SpeechProbe"]

    def _forward(self, serial, pid, deadline, stop):
        port = self._adb(serial, ["forward", "tcp:0", "jdwp:" + str(pid)], deadline, stop).decode().strip()
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            raise _Blocked("adb_did_not_return_allocated_port")
        return (serial, "tcp:" + port, "jdwp:" + str(pid))

    def _remove_forward(self, owned):
        end = time.monotonic() + 4
        serial, local, remote = owned
        rows = self._adb(serial, ["forward", "--list"], end, timeout=2).decode().splitlines()
        matches = [line.split() for line in rows if len(line.split()) == 3 and line.split()[1] == local]
        if not matches:
            return True
        if matches != [list(owned)]:
            raise _Blocked("forward_ownership_changed_not_removed")
        self._adb(serial, ["forward", "--remove", local], end, timeout=2)
        rows = self._adb(serial, ["forward", "--list"], end, timeout=2).decode().splitlines()
        return list(owned) not in [line.split() for line in rows]

    @staticmethod
    def _read_json(path):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    def _operate(self, device, target, pcm_path, evidence_dir, stop, preflight=None, sequence=None):
        result = dict(status="blocked", target_verified=False, full_input=False, cleanup_ok=False,
                      app_grade=None, app_outcome=None, engine_state=None, scores={}, files=[], reason=None, stopped=False,
                      app_score=None, app_score_reason="ui_numeric_score_not_observed",
                      target=target, states=[], capture=None, log=None, runtime=None,
                      signature_verified=False, cleanup={}, input={})
        proc = console = folder = owned = lease = None
        forward_attempted = False
        serial = None
        locked = False
        report = None
        captured_tree = None
        try:
            snapshot = preflight is not None
            spec = dict(target) if isinstance(target, Mapping) else {}
            selector, scope = spec.get("start_selector"), spec.get("scope") or {}
            if selector and selector.get("intro_scope") is not None and selector["intro_scope"].get("target") != spec.get("text", spec.get("target")):
                raise _Blocked("intro_target_mismatch")
            if not sequence and selector and selector.get("scope", {}).get("session") == 4:
                raise _Blocked("conversation_requires_sequence")
            text, slot, seconds = ("", None, 45) if snapshot else self._target(target)
            if not snapshot and scope.get("session") == 1:
                if slot not in (None, 0):
                    raise _Blocked("word_session_requires_runtime_slot_zero")
                slot = 0
            if sequence:
                seconds = min(300, sum(p["timeout"] for p in sequence) + 20)
            deadline = time.monotonic() + seconds
            _check(deadline, stop)
            serial = self._serial(device)
            with _LOCK:
                if serial in _BUSY:
                    raise _Blocked("device_adapter_busy")
                _BUSY.add(serial)
                locked = True
            base = self._path(evidence_dir)
            base.mkdir(parents=True, exist_ok=True)
            folder = Path(tempfile.mkdtemp(prefix="speech-", dir=base))
            lease = self._claim_lease(serial, folder)
            if sequence:
                lines = []
                for index, item in enumerate(sequence):
                    item["base"].mkdir(parents=True, exist_ok=True)
                    item["folder"] = folder if index == 0 else Path(tempfile.mkdtemp(prefix="speech-", dir=item["base"]))
                    encode = lambda value: base64.b64encode(str(value).encode("utf-8")).decode("ascii")
                    lines.append("\t".join([encode(item["text"]), encode(item["pcm"]), item["sha256"],
                                            encode(item["folder"]), str(item["slot"] if item["slot"] is not None else -1),
                                            str(int(item["timeout"] * 1000))]))
                (folder / "sequence.tsv").write_text("\n".join(lines), encoding="utf-8")
            if not snapshot:
                pcm = self._path(pcm_path)
                size = pcm.stat().st_size
                if not 0 < size <= 480000 or size % 2:
                    raise _Blocked("expected_raw_pcm16le_mono_16000hz_max_15s")
                raw = pcm.read_bytes()
                if raw.startswith((b"RIFF", b"RF64", b"OggS", b"fLaC")):
                    raise _Blocked("raw_pcm_required_not_audio_container")
                result["input"] = dict(samples=len(raw) // 2, sha256=hashlib.sha256(raw).hexdigest(),
                                       sample_rate=16000, channels=1, format="s16le")
            else:
                pcm = folder / "unused.pcm"
            result["device"] = preflight or self._preflight(serial, deadline, stop)
            if not result["device"]["supported"]:
                raise _Blocked(result["device"]["reason"])
            preview_hash = spec.get("apk_sha256", self._preview_hashes.get(serial))
            if preview_hash and preview_hash != result["device"].get("apk_sha256"):
                raise _Blocked("apk_changed_since_inspect")
            word_templates = None
            if not snapshot and scope.get("session") == 1:
                word_templates = self._word_templates(serial, result["device"].get("apk_sha256"), deadline, stop)
            if sequence:
                templates = None if word_templates else self._grade_templates(serial, result["device"].get("apk_sha256"), deadline, stop)
                for item in sequence:
                    item["grade_templates"] = templates
                    item['word_templates'] = word_templates
            result["identity"] = dict(word_id=spec.get("word_id"), source="navigator_scope_plus_runtime_text_and_slot",
                                      runtime_word_id_verified=False, scope=scope)
            lease["apk_sha256"] = result["device"].get("apk_sha256")
            self._save_lease(serial, lease)
            java = self._java(folder, deadline, stop)
            if not snapshot:
                # Compiler startup can take seconds; never choose coordinates before it.
                before = self._adb(serial, ["exec-out", "uiautomator", "dump", "/dev/tty"], deadline, stop)
                (folder / "before.xml").write_bytes(before)
                if not sequence and any(n.get("resource-id") == PACKAGE + ":id/tvConvGuide" for n in _xml(before).iter("node")):
                    raise _Blocked("conversation_requires_sequence")
                control = _semantic_control(before, selector, scope) if selector else _start_control(_xml(before))
                if not selector and _grade(_xml(before)):
                    raise _Blocked("previous_grade_visible")
            _check(deadline, stop)
            forward_attempted = True
            lease["forward_allocation_started"] = True
            self._save_lease(serial, lease)
            owned = self._forward(serial, result["device"]["pid"], deadline, stop)
            lease["forward"] = list(owned)
            self._save_lease(serial, lease)
            (folder / "target.txt").write_text(text, encoding="utf-8")
            remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
            console = (folder / "probe.log").open("wb")
            lease["helper_launch_started"] = True
            self._save_lease(serial, lease)
            proc = _launch(java + ["sequence" if sequence else "snapshot" if snapshot else "transition" if selector else "inject", owned[1][4:], str(folder),
                                  str(folder / "sequence.tsv" if sequence else pcm), str(slot if slot is not None else -1), str(remaining_ms),
                                  result["input"].get("sha256", "-")], stdout=console, stderr=subprocess.STDOUT)
            lease["helper"] = dict(pid=proc.pid, birth=_process_identity(proc.pid))
            self._save_lease(serial, lease)
            while proc.poll() is None and not (folder / "armed.json").exists():
                _check(deadline, stop)
                time.sleep(.03)
            armed = self._read_json(folder / "armed.json")
            if armed:
                result["runtime"] = armed.get("runtime")
                result["signature_verified"] = armed.get("signature_verified") is True
            if not snapshot and armed and proc.poll() is None:
                current = self._preflight(serial, deadline, stop)
                if (not current["supported"] or current.get("pid") != result["device"]["pid"]
                        or current.get("apk_sha256") != result["device"].get("apk_sha256")):
                    raise _Blocked("app_changed_after_attach")
                result["start_control"] = self._start(serial, selector, scope, control, folder, deadline, stop,
                                                     word_templates=word_templates)
            seen = {selector["screen_sha256"]} if selector else set()
            next_intro = time.monotonic() + .2
            intro_deadline = min(deadline, time.monotonic() + 12)
            if selector and selector.get("intro_scope"):
                intro_deadline = deadline
            next_terminal = time.monotonic()
            while proc.poll() is None:
                _check(deadline, stop)
                if sequence:
                    for item in sequence:
                        trial_root = item["folder"]
                        if not item.get("capture_done") and (trial_root / "observation.json").exists():
                            if not (trial_root / "trial.json").exists():
                                item["capture_tree"] = self._capture(serial, trial_root, deadline, stop)
                            item["capture_done"] = True
                    last = sequence[-1]
                    if last.get("capture_done") and time.monotonic() >= next_terminal:
                        if last.get("capture_tree") is not None and self._sequence_terminal(last["capture_tree"], word=bool(word_templates)):
                            raw = (last["folder"] / "result.xml").read_bytes()
                        else:
                            raw = self._adb(serial, ["exec-out", "uiautomator", "dump", "/dev/tty"], deadline, stop)
                        if self._sequence_terminal(_xml(raw), word=bool(word_templates)):
                            (last["folder"] / "terminal.xml").write_bytes(raw)
                            (folder / "sequence-end.signal").touch()
                        next_terminal = time.monotonic() + .5
                    elif (selector and not (folder / "recording.json").exists()
                          and time.monotonic() >= next_intro and time.monotonic() < intro_deadline):
                        self._intro(serial, selector, scope, folder, deadline, stop, seen)
                        next_intro = time.monotonic() + .5
                elif not snapshot and captured_tree is None and (folder / "observation.json").exists():
                    captured_tree = self._capture(serial, folder, deadline, stop)
                    result["capture"] = (folder / "capture.png").relative_to(base).as_posix()
                elif (selector and not (folder / "recording.json").exists()
                      and time.monotonic() >= next_intro and time.monotonic() < intro_deadline):
                    self._intro(serial, selector, scope, folder, deadline, stop, seen)
                    next_intro = time.monotonic() + .5
                time.sleep(.03)
            report = self._read_json(folder / "result.json")
            if report is None:
                raise _Blocked("probe_exited_without_result")
            self._merge_report(result, report)
            if not snapshot and not sequence and report.get("completed"):
                if captured_tree is None:
                    raise _Blocked("observation_capture_missing")
                if word_templates is not None:
                    observation = self._observe_word(captured_tree, folder, report, word_templates)
                    result["outcome_evidence"] = observation
                    result["app_outcome"] = observation.get("app_outcome")
                    observed_grade, observation_reason = None, observation.get("reason")
                else:
                    observed_grade, observation_reason = self._observe_grade(captured_tree, folder, deadline, stop)
                result["app_grade"] = observed_grade if observed_grade in _GRADES else None
                result["reason"] = observation_reason
                if result["app_grade"] is None and result["app_outcome"] is None:
                    result["status"] = "inconclusive"
                    result["reason"] = result["reason"] or "ui_grade_inconclusive"
                elif result["target_verified"] is not True or result["full_input"] is not True:
                    result.update(status="blocked", reason="attempt_verification_incomplete")
                self._numeric_evidence(result, folder)
            if proc.returncode and result["status"] == "completed":
                result.update(status="failed", reason="probe_exit_code_" + str(proc.returncode))
        except _Stopped:
            result.update(status="stopped", stopped=True, reason="stopped")
        except TimeoutError:
            result.update(status="timeout", reason="attempt_timeout")
        except Exception as exc:
            result.update(status="blocked" if isinstance(exc, _Blocked) else "failed", reason=str(exc))
        finally:
            forced = False
            cleanup_errors = []
            if proc is not None:
                if proc.poll() is None:
                    try:
                        (folder / "stop.signal").touch()
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        forced = True
                        proc.kill()
                        proc.wait(timeout=2)
                    except Exception as exc:
                        cleanup_errors.append(str(exc))
                        proc.kill()
                        proc.wait(timeout=2)
                try:
                    report = self._read_json(folder / "result.json") or report
                    if report:
                        self._merge_report(result, report, preserve_status=True)
                except Exception as exc:
                    cleanup_errors.append(str(exc))
            if console is not None:
                console.close()
            removed = not forward_attempted
            if forward_attempted and owned is None:
                cleanup_errors.append("forward_allocation_unconfirmed; no unidentified forward removed")
            if owned is not None:
                try:
                    removed = self._remove_forward(owned)
                except Exception as exc:
                    cleanup_errors.append(str(exc))
            jc = report.get("cleanup", {}) if report else {}
            cleanup_errors.extend(jc.get("errors", []))
            detached = proc is None or jc.get("detached") is True
            requests_removed = proc is None or jc.get("requests_removed") is True
            recording_released = proc is None or jc.get("recording_released") is True
            result["cleanup"] = dict(detached=detached, requests_removed=requests_removed,
                                     recording_released=recording_released, forward_removed=removed,
                                     forced_termination=forced, errors=cleanup_errors)
            result["cleanup_ok"] = bool(detached and requests_removed and recording_released and removed
                                        and not forced and not cleanup_errors)
            if lease is not None:
                try:
                    lease["state"] = "cleanup_complete" if result["cleanup_ok"] else "cleanup_incomplete"
                    self._save_lease(serial, lease)
                    if result["cleanup_ok"]:
                        self._release_lease(serial, lease)
                except Exception as exc:
                    result["cleanup_ok"] = False
                    cleanup_errors.append("lease_cleanup: " + str(exc))
            elif serial is not None and self._lease_path(serial).exists():
                result["cleanup_ok"] = False
            if result["status"] not in ("completed", "inconclusive"):
                result["app_grade"] = None
                result["app_outcome"] = None
                result.update(app_score=None, app_score_reason="attempt_not_verified")
            if not result["cleanup_ok"] and result["status"] == "completed":
                result.update(status="failed", reason="cleanup_incomplete")
            if folder is not None:
                result["log"] = (folder / "probe.log").relative_to(base).as_posix() if console else None
                result["files"] = sorted(p.relative_to(base).as_posix() for p in folder.iterdir()
                                         if p.suffix in (".xml", ".png", ".log"))
                result["files"].append((folder / "attempt.log").relative_to(base).as_posix())
                try:
                    (folder / "attempt.log").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                except OSError as exc:
                    result.update(status="failed", reason="evidence_write_failed: " + str(exc))
            if locked:
                with _LOCK:
                    _BUSY.discard(serial)
        return result

    @staticmethod
    def _merge_report(result, report, preserve_status=False):
        for key in ("target_verified", "full_input", "engine_state", "scores", "states", "runtime", "signature_verified", "sequence_reports"):
            if key in report:
                result[key] = report[key]
        if not preserve_status:
            result["status"] = "completed" if report.get("completed") else "failed"
            result["reason"] = report.get("reason")
            result["stopped"] = report.get("stopped", False)
        result["input"].update(report.get("input", {}))


def _meaning_key(value):
    if not isinstance(value, str):
        return ""
    return "".join(c for c in unicodedata.normalize("NFKC", value)
                   if not c.isspace() and not unicodedata.category(c).startswith("P"))


def _conversation_roi(im, reader, np, spec):
    meaning = _meaning_key(spec.get("meaning"))
    approved = [_meaning_key(value) for value in spec.get("meanings", [])]
    if not meaning or len(approved) != 4 or any(not value for value in approved) or len(set(approved)) != 4:
        raise _Blocked("unique_exact_korean_meanings_required")
    area, mean, grade = (spec[key] for key in ("area", "mean", "grade"))
    for box in (area, mean, grade):
        if len(box) != 4 or not (0 <= box[0] < box[2] <= im.width and 0 <= box[1] < box[3] <= im.height):
            raise _Blocked("invalid_conversation_layout")
    rows = reader.readtext(np.array(im.crop(area)), detail=1, paragraph=False)
    candidates = []
    for points, text, _ in rows:
        # Low-confidence full-frame text is a locator only, never evidence.
        if not re.search(r"[\uac00-\ud7a3]", text):
            continue
        xs, ys = [p[0] + area[0] for p in points], [p[1] + area[1] for p in points]
        box = [max(area[0], int(min(xs)) - 6), max(area[1], int(min(ys)) - 5),
               min(area[2], int(max(xs)) + 6), min(area[3], int(max(ys)) + 5)]
        if box[2] > box[0] and box[3] > box[1]:
            candidates.append(box)
    if not candidates or len(candidates) > 12:
        raise _Blocked("meaning_locator_missing_or_ambiguous")
    anchors = []
    evidence = []
    for box in candidates:
        crop = im.crop(box).resize(((box[2] - box[0]) * 3, (box[3] - box[1]) * 3))
        refined = reader.readtext(np.array(crop), detail=1, paragraph=False)
        refined = sorted(refined, key=lambda row: (min(p[1] for p in row[0]), min(p[0] for p in row[0])))
        text = "".join(row[1] for row in refined)
        confidence = min((float(row[2]) for row in refined), default=0)
        evidence.append(dict(text=text, confidence=confidence, roi=box))
        key = _meaning_key(text)
        if key in approved and confidence >= .85:
            anchors.append((key, box))
    matches = [box for key, box in anchors if key == meaning]
    if len(matches) != 1:
        raise _Blocked("exact_png_meaning_missing_or_ambiguous")
    anchor = matches[0]
    if any(box[1] > anchor[1] for key, box in anchors if key != meaning):
        raise _Blocked("newer_conversation_bubble_in_png")
    # Re-anchor layout offsets on THIS PNG. Late XML positions are never used
    # directly; the Korean TextView and detected glyph centers may differ.
    dx = anchor[0] + 6 - mean[0]
    dy = (anchor[1] + anchor[3] - mean[1] - mean[3]) / 2
    if abs(dx) > 20:
        raise _Blocked("meaning_column_does_not_match_layout")
    pad = 16
    roi = [int(grade[0] + dx - pad), int(grade[1] + dy - pad),
           int(grade[2] + dx + pad), int(grade[3] + dy + pad)]
    if not (area[0] <= roi[0] < roi[2] <= area[2] and area[1] <= roi[1] < roi[3] <= area[3]):
        raise _Blocked("anchored_grade_roi_outside_conversation")
    return roi, dict(meaning=spec["meaning"], anchor=anchor, observations=evidence,
                     source="same_png_exact_korean_meaning", layout_source="late_xml_geometry_only")


def _local_grade_ocr(path, roi, conversation=False):
    """Isolated, deadline-bounded local OCR; missing weights never download."""
    try:
        import contextlib
        import io
        # Block network even if an optional dependency changes its defaults.
        def deny_network(event, args):
            if event in ("socket.connect", "socket.connect_ex", "socket.getaddrinfo"):
                raise RuntimeError("OCR network disabled")
        sys.addaudithook(deny_network)
        with contextlib.redirect_stdout(io.StringIO()):
            import easyocr
            import numpy as np
            from PIL import Image
            im = Image.open(path).convert("RGB")
            reader = easyocr.Reader(["ko", "en"], gpu=False, verbose=False, download_enabled=False)
            anchor = None
            if conversation:
                roi, anchor = _conversation_roi(im, reader, np, roi)
            x1, y1, x2, y2 = roi
            if not (0 <= x1 < x2 <= im.width and 0 <= y1 < y2 <= im.height):
                return dict(grade=None, reason="invalid_grade_roi")
            crop = im.crop(roi).resize(((x2 - x1) * 3, (y2 - y1) * 3))
            rows = reader.readtext(np.array(crop), detail=1, paragraph=False)
        found, numeric, evidence = set(), set(), []
        for _, text, confidence in rows:
            value = next((g for g in _GRADES if g.casefold() == text.strip().casefold()), None)
            evidence.append(dict(text=text, confidence=float(confidence)))
            if value and float(confidence) >= .85:
                found.add(value)
            number = re.fullmatch(r"(100|[1-9]?\d)\s*%", text.strip())
            if number and float(confidence) >= .85:
                numeric.add(int(number[1]))
        return dict(grade=next(iter(found)) if len(found) == 1 else None,
                    reason=None if len(found) == 1 else "local_ocr_grade_inconclusive", roi=roi, observations=evidence,
                    app_score=next(iter(numeric)) if len(numeric) == 1 else None,
                    app_score_reason=None if len(numeric) == 1 else "ui_numeric_score_not_observed_or_ambiguous",
                    score_source="captured_grade_roi_ocr", meaning_anchor=anchor)
    except Exception as exc:
        return dict(grade=None, reason="local_ocr_unavailable: " + str(exc))


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] not in ("--local-grade-ocr", "--local-conversation-ocr"):
        raise SystemExit("Only local grade/conversation OCR CAPTURE SPEC_JSON is supported")
    print(json.dumps(_local_grade_ocr(sys.argv[2], json.loads(sys.argv[3]),
                                    conversation=sys.argv[1] == "--local-conversation-ocr"), ensure_ascii=True))
