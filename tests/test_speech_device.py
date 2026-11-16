"""Persistent offline adapter regressions. Device/process/network calls are mocked."""

import copy
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

import speech_device as speech


PACKAGE = speech.PACKAGE
SERIAL = "offline-serial"
APK = "/data/app/~~1TfV4cYeHOwsauDKTAID-Q==/com.weaversmind.tjp2-0KvW5O0WyF4hc8WUvJYvLQ==/base.apk"
SCOPE = dict(subject="japanese-premium", level=2, week=1, stage=1, session=2)


def xml_node(rid, **attrs):
    from xml.etree.ElementTree import Element, tostring
    values = dict(package=PACKAGE, **{"resource-id": PACKAGE + ":id/" + rid,
        "enabled": "true", "clickable": "true", "bounds": "[10,20][110,120]",
        "text": "", "content-desc": ""})
    values.update(attrs)
    return tostring(Element("node", values), encoding="unicode")


def hierarchy(*nodes):
    return ("<hierarchy>" + "".join(nodes) + "</hierarchy>").encode()


START_XML = hierarchy(xml_node("ivFBSpeakIcon"))
RESULT_XML = hierarchy(xml_node("llFBCenter", clickable="false"))


class FakeAdapter(speech.DeviceAdapter):
    def __init__(self, root):
        super().__init__(types.SimpleNamespace(_adb_input_lock=threading.Lock()), root)
        self.calls = []
        self.forwards = [["FOREIGN", "tcp:7777", "jdwp:8"]]
        self.start_xml = START_XML
        self.result_xml = RESULT_XML
        self.apk = APK
        self.apk_hash = "a" * 64
        self.activity = "topResumedActivity=ActivityRecord{28ba810 u0 " + PACKAGE + "/.MessageBoxActivity} t71}"
        self.build = 11
        self.grade = "Good"
        self.ocr_data = None
        self.started = False

    def _adb(self, serial, args, deadline, stop=None, timeout=10):
        if serial != SERIAL:
            raise AssertionError("Device serial was not preserved")
        self.calls.append(list(args))
        if args == ["get-state"]:
            return b"device"
        if args == ["shell", "dumpsys", "package", PACKAGE]:
            return ("Package [" + PACKAGE + "] versionCode=" + str(self.build)).encode()
        if args == ["shell", "pm", "path", PACKAGE]:
            return ("package:" + self.apk).encode()
        if args == ["shell", "sha256sum", self.apk]:
            return (self.apk_hash + "  " + self.apk).encode()
        if args == ["shell", "dumpsys", "activity", "activities"]:
            return self.activity.encode()
        if args == ["shell", "pidof", PACKAGE]:
            return b"123"
        if args == ["forward", "tcp:0", "jdwp:123"]:
            self.forwards.append([serial, "tcp:4567", "jdwp:123"])
            return b"4567"
        if args == ["forward", "--list"]:
            return "\n".join(" ".join(row) for row in self.forwards).encode()
        if args == ["forward", "--remove", "tcp:4567"]:
            self.forwards = [row for row in self.forwards if row[1] != "tcp:4567"]
            return b""
        if args == ["exec-out", "uiautomator", "dump", "/dev/tty"]:
            return self.result_xml if self.started else self.start_xml
        if args[:3] == ["shell", "input", "tap"]:
            if not self._input_lock.locked():
                raise AssertionError("Input dispatched without host lock")
            self.started = True
            return b""
        if args == ["exec-out", "screencap", "-p"]:
            return b"\x89PNG\r\n\x1a\nMOCK"
        raise AssertionError("Unexpected ADB call: " + repr(args))

    def _java(self, *args):
        return ["MOCK_JAVA"]

    def _observe_grade(self, tree, folder, deadline, stop, sequence_index=None):
        if self.ocr_data is not None:
            (folder / "ocr.log").write_text(json.dumps(self.ocr_data), encoding="utf-8")
        return self.grade, None if self.grade else "local_ocr_grade_inconclusive"


class FakeProbe:
    def __init__(self, args, *, stdout, stderr, report=None, stop=None, forced=False):
        if args[0] != "MOCK_JAVA":
            raise AssertionError("Real process launch attempted")
        self.root = Path(args[3])
        self.pid = 424242
        self.returncode = None
        self.forced = forced
        self.report = dict(completed=True, target_verified=True, full_input=True, engine_state=1,
                           scores={"count": 2, "word": [60, 70], "word_average": 65},
                           cleanup=dict(detached=True, requests_removed=True, recording_released=True, errors=[]))
        if report:
            self.report.update(copy.deepcopy(report))
        if stop:
            stop.set()
        else:
            (self.root / "armed.json").write_text(json.dumps(dict(signature_verified=True, runtime={"slot": 0})))

    def finish(self, completed):
        value = dict(self.report)
        value["completed"] = value["completed"] and completed
        (self.root / "result.json").write_text(json.dumps(value))
        self.returncode = 0 if value["completed"] else 2

    def poll(self):
        if self.returncode is not None:
            return self.returncode
        if (self.root / "stop.signal").exists() and not self.forced:
            self.finish(False)
        elif (self.root / "capture.signal").exists():
            self.finish(True)
        elif (self.root / "start.signal").exists():
            (self.root / "observation.json").write_text("{}")
        return self.returncode

    def wait(self, timeout=None):
        self.poll()
        if self.returncode is None:
            raise subprocess.TimeoutExpired("MOCK_JAVA", timeout)
        return self.returncode

    def kill(self):
        self.returncode = -9


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.adapter = FakeAdapter(self.root)
        self.pcm = self.root / "input.pcm"
        self.pcm.write_bytes(b"\x01\x00" * 900)
        self.stop = threading.Event()
        self.report = {}
        self.forced = False
        self.stop_on_launch = False
        self.addCleanup(patch.stopall)
        patch.object(speech.subprocess, "Popen", side_effect=AssertionError("Unmocked process launch")).start()
        patch.object(speech, "_command", side_effect=AssertionError("Unmocked external command")).start()
        patch.object(speech, "_process_identity", return_value="birth-token").start()
        patch.object(speech, "_launch", side_effect=self.launch).start()

    def launch(self, args, **kwargs):
        return FakeProbe(args, **kwargs, report=self.report, forced=self.forced,
                         stop=self.stop if self.stop_on_launch else None)

    def attempt(self, target="exact;", serial=SERIAL):
        return self.adapter.run_attempt(serial, target, self.pcm, self.root / "evidence", self.stop)

    def lease(self, **extra):
        value = self.adapter._claim_lease(SERIAL, self.root / "old-attempt")
        value.update(state="cleanup_incomplete", helper={"pid": 424242, "birth": "birth-token"},
                     helper_launch_started=True, forward_allocation_started=True,
                     forward=[SERIAL, "tcp:4567", "jdwp:123"])
        value.update(extra)
        self.adapter._save_lease(SERIAL, value)
        return value

    def test_success_preserves_foreign_forward_and_relative_evidence(self):
        result = self.attempt()
        self.assertEqual(result["status"], "completed", result)
        self.assertTrue(result["target_verified"] and result["full_input"] and result["cleanup_ok"])
        self.assertEqual(result["app_grade"], "Good")
        self.assertEqual(self.adapter.forwards, [["FOREIGN", "tcp:7777", "jdwp:8"]])
        self.assertTrue(all(not Path(p).is_absolute() and ".." not in Path(p).parts for p in result["files"]))
        self.assertTrue(all((self.root / "evidence" / p).is_file() for p in result["files"]))
        self.assertFalse(self.adapter._lease_path(SERIAL).exists())

    def test_second_attempt_does_not_overwrite_first_evidence(self):
        first = self.attempt()
        saved = (self.root / "evidence" / first["log"]).read_bytes()
        self.adapter.started = False
        second = self.attempt()
        self.assertNotEqual(first["log"], second["log"])
        self.assertEqual((self.root / "evidence" / first["log"]).read_bytes(), saved)

    def test_state_one_is_not_a_grade(self):
        self.adapter.grade = None
        result = self.attempt()
        self.assertEqual(result["engine_state"], 1)
        self.assertIsNone(result["app_grade"])
        self.assertEqual(result["status"], "inconclusive")

    def test_word_outcome_completes_without_fabricated_grade_or_score(self):
        with patch.object(self.adapter, '_word_templates', return_value={'mock': b'pixels'}), \
                patch.object(self.adapter, '_observe_word', return_value=dict(app_outcome='word_correct', reason=None)):
            result = self.attempt(dict(text='exact;', scope={'session': 1}))
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(result['app_outcome'], 'word_correct')
        self.assertIsNone(result['app_grade'])
        self.assertIsNone(result['app_score'])

    def test_word_outcome_cannot_override_incomplete_input(self):
        self.report['full_input'] = False
        with patch.object(self.adapter, '_word_templates', return_value={'mock': b'pixels'}), \
                patch.object(self.adapter, '_observe_word', return_value=dict(app_outcome='word_correct', reason=None)):
            result = self.attempt(dict(text='exact;', scope={'session': 1}))
        self.assertEqual(result['status'], 'blocked', result)
        self.assertIsNone(result['app_outcome'])

    def test_incomplete_or_unverified_report_cannot_complete(self):
        for key in ("target_verified", "full_input"):
            with self.subTest(key=key):
                self.adapter.started = False
                self.report = {key: False}
                result = self.attempt()
                self.assertEqual(result["status"], "blocked")
                self.assertIsNone(result["app_grade"])

    def test_cleanup_failure_is_not_completed(self):
        self.report = {"cleanup": dict(detached=False, requests_removed=True, recording_released=True)}
        result = self.attempt()
        self.assertFalse(result["cleanup_ok"])
        self.assertNotEqual(result["status"], "completed")
        self.assertTrue(self.adapter._lease_path(SERIAL).exists())

    def test_numeric_ui_score_is_not_internal_average(self):
        result = self.attempt()
        self.assertEqual(result["scores"]["word_average"], 65)
        self.assertIsNone(result["app_score"])
        self.assertTrue(result["app_score_reason"])

    def test_proven_numeric_score_is_separate(self):
        self.adapter.ocr_data = dict(app_score=94, score_source="captured_grade_roi_ocr")
        result = self.attempt()
        self.assertEqual(result["app_score"], 94)
        self.assertEqual(result["scores"]["word_average"], 65)

    def test_invalid_grade_never_completes(self):
        self.adapter.grade = "PASS"
        result = self.attempt()
        self.assertIsNone(result["app_grade"])
        self.assertEqual(result["status"], "inconclusive")

    def test_invalid_targets_do_not_contact_device(self):
        for target in (None, "", "a\nb", 7, {"text": "a;", "slot": True},
                       {"text": "a;", "actualWordID": 123}, {"text": "a;", "timeout_s": None},
                       {"text": "a;", "timeout_s": float("nan")}, {"text": "a;", "timeout_s": 301}):
            with self.subTest(target=target):
                result = self.attempt(target)
                self.assertFalse(result["target_verified"])
                self.assertEqual(self.adapter.calls, [])

    def test_invalid_serials_do_not_contact_device(self):
        for serial in (None, "", "-bad", "a;cmd", "a b"):
            with self.subTest(serial=serial):
                self.assertFalse(self.attempt(serial=serial)["target_verified"])
                self.assertEqual(self.adapter.calls, [])

    def test_invalid_pcm_does_not_contact_device(self):
        for data in (b"", b"x", b"RIFFabcd", b"OggSabcd", b"\0" * 480002):
            with self.subTest(length=len(data)):
                self.pcm.write_bytes(data)
                result = self.attempt()
                self.assertFalse(result["full_input"])
                self.assertEqual(self.adapter.calls, [])

    def test_unknown_control_never_taps(self):
        self.adapter.start_xml = hierarchy(xml_node("unknown"))
        result = self.attempt()
        self.assertIn("known_start_control", result["reason"])
        self.assertFalse(any(row[:3] == ["shell", "input", "tap"] for row in self.adapter.calls))

    def test_build_and_apk_hash_bind_attempt(self):
        self.assertTrue(self.adapter.inspect(SERIAL)["supported"])
        self.adapter.apk_hash = "b" * 64
        self.assertEqual(self.attempt()["reason"], "apk_changed_since_inspect")

    def test_android_path_activity_variants_and_no_jdwp_stream(self):
        for marker in ("topResumedActivity=", "topResumedActivity =", "mResumedActivity:"):
            self.adapter.activity = marker + "ActivityRecord{28ba810 u0 " + PACKAGE + "/.MessageBoxActivity} t71}"
            self.assertTrue(self.adapter.inspect(SERIAL)["supported"])
        self.assertNotIn(["jdwp"], self.adapter.calls)

    def test_unsafe_apk_paths_and_build_are_blocked(self):
        for path in ("/data/app/../base.apk", "/sdcard/x/base.apk", "/data/app/a;cmd/base.apk"):
            self.adapter.apk = path
            self.assertFalse(self.adapter.inspect(SERIAL)["supported"])
        self.adapter.apk = APK
        self.adapter.build = 12
        self.assertEqual(self.adapter.inspect(SERIAL)["reason"], "unsupported_build")

    def test_stop_before_work(self):
        self.stop.set()
        self.assertTrue(self.attempt()["stopped"])
        self.assertEqual(self.adapter.calls, [])

    def test_cooperative_stop_cleans_and_releases_lease(self):
        self.stop_on_launch = True
        result = self.attempt()
        self.assertTrue(result["stopped"] and result["cleanup_ok"], result)
        self.assertFalse(self.adapter._lease_path(SERIAL).exists())

    def test_forced_child_termination_retains_lease(self):
        self.stop_on_launch = self.forced = True
        result = self.attempt()
        self.assertTrue(result["stopped"])
        self.assertFalse(result["cleanup_ok"])
        self.assertTrue(result["cleanup"]["forced_termination"])
        self.assertTrue(self.adapter._lease_path(SERIAL).exists())

    def test_forward_ownership_change_never_removes_foreign_mapping(self):
        self.adapter.forwards = [["FOREIGN", "tcp:4567", "jdwp:99"]]
        with self.assertRaisesRegex(speech._Blocked, "ownership_changed"):
            self.adapter._remove_forward((SERIAL, "tcp:4567", "jdwp:123"))
        self.assertFalse(any("--remove" in row for row in self.adapter.calls))

    def test_cleanup_clear_allows_next_attempt(self):
        self.lease()
        with patch.object(speech, "_process_identity", return_value=None):
            result = self.adapter.check_cleanup(SERIAL)
        self.assertTrue(result["cleanup_ok"], result)
        self.assertFalse(self.adapter._lease_path(SERIAL).exists())
        self.assertEqual(self.attempt()["status"], "completed")

    def test_live_helper_retains_lease(self):
        self.lease()
        result = self.adapter.check_cleanup(SERIAL)
        self.assertFalse(result["cleanup_ok"])
        self.assertIn("helper_still_alive", result["reason"])
        self.assertTrue(self.adapter._lease_path(SERIAL).exists())

    def test_live_active_owner_retains_lease(self):
        self.lease(state="active")
        self.assertIn("owner_still_alive", self.adapter.check_cleanup(SERIAL)["reason"])

    def test_remaining_owned_forward_retains_lease(self):
        self.lease()
        self.adapter.forwards.append([SERIAL, "tcp:4567", "jdwp:123"])
        with patch.object(speech, "_process_identity", return_value=None):
            result = self.adapter.check_cleanup(SERIAL)
        self.assertFalse(result["cleanup_ok"])
        self.assertTrue(self.adapter._lease_path(SERIAL).exists())
        self.assertFalse(any("--remove" in row for row in self.adapter.calls))

    def test_foreign_reuse_of_port_is_left_alone(self):
        self.lease()
        self.adapter.forwards = [["FOREIGN", "tcp:4567", "jdwp:99"]]
        with patch.object(speech, "_process_identity", return_value=None):
            self.assertTrue(self.adapter.check_cleanup(SERIAL)["cleanup_ok"])
        self.assertEqual(self.adapter.forwards, [["FOREIGN", "tcp:4567", "jdwp:99"]])

    def test_pid_reuse_is_not_owned_helper(self):
        self.lease()
        with patch.object(speech, "_process_identity", return_value="different-birth"):
            self.assertTrue(self.adapter.check_cleanup(SERIAL)["cleanup_ok"])

    def test_unknown_launch_or_forward_allocation_is_retained(self):
        for extra, reason in (({"helper": None}, "helper_launch_unconfirmed"),
                              ({"forward": None}, "forward_allocation_unconfirmed")):
            with self.subTest(reason=reason):
                lease = self.lease(**extra)
                with patch.object(speech, "_process_identity", return_value=None):
                    result = self.adapter.check_cleanup(SERIAL)
                self.assertEqual(result["reason"], reason)
                self.adapter._release_lease(SERIAL, lease)

    def test_cleanup_read_failures_retain_lease(self):
        self.lease()
        with patch.object(speech, "_process_identity", side_effect=OSError("access denied")):
            self.assertFalse(self.adapter.check_cleanup(SERIAL)["cleanup_ok"])
        with patch.object(speech, "_process_identity", return_value=None), patch.object(self.adapter, "_adb", side_effect=TimeoutError("offline")):
            self.assertFalse(self.adapter.check_cleanup(SERIAL)["cleanup_ok"])
        self.assertTrue(self.adapter._lease_path(SERIAL).exists())

    def test_changed_lease_is_not_erased(self):
        self.lease()
        original = self.adapter._adb
        def replace_during_read(*args, **kwargs):
            changed = json.loads(self.adapter._lease_path(SERIAL).read_text())
            changed["attempt"] = "new-owner-attempt"
            self.adapter._lease_path(SERIAL).write_text(json.dumps(changed))
            return original(*args, **kwargs)
        with patch.object(speech, "_process_identity", return_value=None), patch.object(self.adapter, "_adb", side_effect=replace_during_read):
            self.assertEqual(self.adapter.check_cleanup(SERIAL)["reason"], "lease_changed_during_cleanup_check")
        self.assertTrue(self.adapter._lease_path(SERIAL).exists())

    def test_foreign_lease_cannot_be_saved_or_released(self):
        original = self.lease()
        other = dict(original, attempt="foreign-attempt")
        self.adapter._lease_path(SERIAL).write_text(json.dumps(other))
        with self.assertRaises(speech._Blocked):
            self.adapter._save_lease(SERIAL, original)
        with self.assertRaises(speech._Blocked):
            self.adapter._release_lease(SERIAL, original)

    def test_malformed_lease_is_retained(self):
        self.lease()
        self.adapter._lease_path(SERIAL).write_text("{")
        self.assertFalse(self.adapter.check_cleanup(SERIAL)["cleanup_ok"])
        self.assertTrue(self.adapter._lease_path(SERIAL).exists())

    def test_cleanup_during_attempt_is_blocked(self):
        with speech._LOCK:
            speech._BUSY.add(SERIAL)
        try:
            self.assertEqual(self.adapter.check_cleanup(SERIAL)["reason"], "device_adapter_busy")
        finally:
            speech._BUSY.discard(SERIAL)


class SelectorTests(unittest.TestCase):
    def selector(self, raw, rid="btnTodayLessonNext"):
        return dict(resource_id=PACKAGE + ":id/" + rid, text="", content_desc="",
                    scope=SCOPE, screen_sha256=hashlib.sha256(raw).hexdigest())

    def test_handoff_hash_accepts_only_xml_envelope_difference(self):
        raw = hierarchy(xml_node("flTodayLessonCoverOverlay", clickable="false"), xml_node("btnTodayLessonNext"))
        selector = self.selector(raw)
        self.assertEqual(speech._semantic_control(b'<?xml version="1.0"?>' + raw, selector, SCOPE)["x"], 60)
        with self.assertRaisesRegex(speech._Blocked, "screen_changed"):
            speech._semantic_control(raw.replace(b"[10,20]", b"[11,20]"), selector, SCOPE)

    def test_selector_scope_package_and_action_guards(self):
        raw = hierarchy(xml_node("flTodayLessonCoverOverlay", clickable="false"), xml_node("btnTodayLessonNext"))
        selector = self.selector(raw)
        with self.assertRaisesRegex(speech._Blocked, "scope_mismatch"):
            speech._semantic_control(raw, selector, dict(SCOPE, stage=2))
        with self.assertRaises(speech._Blocked):
            speech._semantic_control(raw, dict(selector, resource_id="android:id/button1"), SCOPE)
        with self.assertRaises(speech._Blocked):
            speech._xml(hierarchy(xml_node("x", package="android")))
        with self.assertRaises(speech._Blocked):
            speech._xml(b'<!DOCTYPE hierarchy><hierarchy/>')

    def test_ambiguous_or_disabled_controls_block(self):
        for raw in (hierarchy(xml_node("ivFBSpeakIcon"), xml_node("ivFBSpeakIcon")),
                    hierarchy(xml_node("ivFBSpeakIcon", enabled="false")),
                    hierarchy(xml_node("ivFBSpeakIcon", bounds="[0,0][0,0]"))):
            with self.assertRaises(speech._Blocked):
                speech._start_control(speech._xml(raw))


def conversation_xml(count, next_enabled=True, last_graded=True):
    bubbles = []
    for i in range(count):
        y = 80 + 120 * i
        text = xml_node("tvTalkText", text="", clickable="false", bounds="[30,%d][200,%d]" % (y + 50, y + 100))
        mean = xml_node("tvTalkMean", text="meaning-%d" % i, bounds="[30,%d][200,%d]" % (y + 10, y + 40))
        grade = xml_node("ivTalkGrade", clickable="false", bounds="[300,%d][400,%d]" % (y + 20, y + 100))
        if i == count - 1 and not last_graded:
            grade = ""
        bubbles.append('<node package="' + PACKAGE + '" resource-id="' + PACKAGE + ':id/cvTalkBubble" '
                       'bounds="[10,%d][500,%d]">' % (y, y + 110) + text + mean + grade + '</node>')
    area = '<node package="' + PACKAGE + '" resource-id="' + PACKAGE + ':id/svConvTalk" bounds="[0,30][1340,656]">'
    return hierarchy(xml_node("tvConvGuide", clickable="false"), area + "".join(bubbles) + '</node>',
                     xml_node("btnConvNext", text="\ub2e4\uc74c", enabled="true" if next_enabled else "false"))


class FakeSequenceProbe(FakeProbe):
    def __init__(self, args, adapter, reports, **kwargs):
        super().__init__(args, **kwargs)
        self.adapter = adapter
        self.index = 0
        self.roots = [Path(base64.b64decode(line.split("\t")[3]).decode())
                      for line in Path(args[4]).read_text().splitlines()]
        texts = [base64.b64decode(line.split("\t")[0]).decode() for line in Path(args[4]).read_text().splitlines()]
        self.reports = [dict(self.report, capture_valid=True, sequence_index=i, runtime=dict(text=text, slot=0))
                        for i, text in enumerate(texts)]
        for index, extra in reports.items():
            self.reports[index].update(extra)

    def finish(self, completed):
        value = dict(completed=completed, sequence_reports=self.reports, cleanup=self.report["cleanup"])
        (self.root / "result.json").write_text(json.dumps(value))
        self.returncode = 0 if completed else 2

    def poll(self):
        if self.returncode is not None:
            return self.returncode
        if (self.root / "stop.signal").exists():
            self.finish(False)
            return self.returncode
        if (self.root / "sequence-end.signal").exists():
            self.finish(True)
            return self.returncode
        if not (self.root / "start.signal").exists():
            return None
        if self.index < 4 and (self.roots[self.index] / "capture.signal").exists():
            self.reports[self.index].setdefault("png_sha256", (self.roots[self.index] / "png.signal").read_text())
            (self.roots[self.index] / "trial.json").write_text(json.dumps(self.reports[self.index]))
            self.index += 1
        if self.index < 4:
            self.adapter.result_xml = conversation_xml(self.index + 1)
            (self.roots[self.index] / "observation.json").write_text("{}")
        return None


class SequenceTests(unittest.TestCase):
    def test_word_sequence_preserves_two_input_phases_per_selected_word(self):
        targets = copy.deepcopy(self.targets)
        for index, target in enumerate(targets):
            target.update(kind='word', order=index+1, db_sha256='a'*64)
            target['scope']['session'] = 1
        targets[0]['start_selector']['scope']['session'] = 1
        shared = dict(cleanup_ok=True, cleanup={}, stopped=False, sequence_reports=[])
        with patch.object(self.adapter, '_operate', return_value=shared) as operate:
            results = self.adapter.run_sequence(SERIAL, targets, self.stop)
        plan = operate.call_args.kwargs['sequence']
        self.assertEqual([p['word_phase'] for p in plan], [1,2]*4)
        self.assertEqual([p['text'] for p in plan], [t['text'] for t in targets for _ in (1,2)])
        self.assertEqual(len(results), 4)
        self.assertTrue(all(r['status'] == 'blocked' and len(r['word_phases']) == 2 for r in results))

    def test_word_phase_aggregation_never_hides_missing_or_wrong_phase(self):
        good = dict(status='completed', target_verified=True, full_input=True, cleanup_ok=True, signature_verified=True,
                    app_outcome='word_correct', files=['a.png'])
        fold = speech.DeviceAdapter._word_phase_results
        result = fold([dict(good), dict(good, files=['b.png'])])[0]
        self.assertEqual(result['app_outcome'], 'word_correct')
        self.assertEqual(result['files'], ['a.png', 'b.png'])
        result = fold([dict(good, app_outcome='word_wrong'), dict(good)])[0]
        self.assertEqual(result['app_outcome'], 'word_wrong')
        for change in ({'status':'blocked'}, {'full_input':False}, {'target_verified':False},
                       {'cleanup_ok':False}, {'signature_verified':False}, {'app_outcome':None}):
            for phases in ([dict(good, **change), good], [good, dict(good, **change)]):
                result = fold(phases)[0]
                self.assertEqual(result['status'], 'blocked')
                self.assertIsNone(result['app_outcome'])
        self.assertEqual(fold([good])[0]['status'], 'blocked')

    def test_word_sequence_requires_four_ordered_items_and_runtime_slot_zero(self):
        targets = copy.deepcopy(self.targets)
        for i, target in enumerate(targets):
            target.update(kind='word', order=i+1, db_sha256='a'*64)
            target['scope']['session'] = 1
        targets[0]['start_selector']['scope']['session'] = 1
        self.assertEqual([p['slot'] for p in self.adapter._sequence_plan(targets)], [0]*4)
        for change in ({'slot': 1}, {'kind': 'sentence'}, {'order': 3}, {'db_sha256': None}):
            bad = copy.deepcopy(targets)
            bad[0].update(change)
            with self.assertRaises(speech._Blocked):
                self.adapter._sequence_plan(bad)
        with self.assertRaises(speech._Blocked):
            self.adapter._sequence_plan(targets[:3])

    def launch(self, args, **kwargs):
        self.launches += 1
        return FakeSequenceProbe(args, self.adapter, self.sequence_reports, **kwargs)

    def setUp(self):
        AdapterTests.setUp(self)
        self.adapter._grade_templates = lambda *args: None
        self.launches = 0
        self.sequence_reports = {}
        scope = dict(SCOPE, session=4)
        self.adapter.start_xml = hierarchy(xml_node("ivConvCtrlPlay", **{"content-desc": "Play"}))
        selector = dict(resource_id=PACKAGE + ":id/ivConvCtrlPlay", text="", content_desc="Play", scope=scope,
                        screen_sha256=hashlib.sha256(self.adapter.start_xml).hexdigest())
        self.targets = [dict(text="line-%d;" % i, word_id=i + 1, pcm_path=str(self.pcm),
                             meaning="meaning-%d" % i,
                             evidence_dir=str(self.root / ("item-%d" % i)),
                             scope={k: scope[k] for k in ("level", "week", "stage", "session")}) for i in range(4)]
        self.targets[0]["start_selector"] = selector

    def test_sequence_single_attach_four_relative_results(self):
        results = self.adapter.run_sequence(SERIAL, self.targets, self.stop)
        self.assertEqual(len(results), 4)
        self.assertEqual(self.launches, 1)
        self.assertEqual(sum(row[:3] == ["shell", "input", "tap"] for row in self.adapter.calls), 1)
        for index, result in enumerate(results):
            self.assertEqual(result["status"], "completed", result)
            self.assertEqual(result["sequence_index"], index)
            self.assertFalse(result["retry_available"])
            self.assertTrue(result["cleanup_ok"])
            base = Path(self.targets[index]["evidence_dir"])
            self.assertTrue(all((base / p).is_file() for p in result["files"]))

    def test_sequence_capture_race_only_invalidates_affected_grade(self):
        self.sequence_reports[1] = {"capture_valid": False}
        results = self.adapter.run_sequence(SERIAL, self.targets, self.stop)
        self.assertIsNone(results[1]["app_grade"])
        self.assertEqual(results[1]["status"], "inconclusive")
        self.assertTrue(results[1]["full_input"])
        self.assertEqual([r["app_grade"] for r in results], ["Good", None, "Good", "Good"])

    def test_sequence_partial_target_cannot_complete(self):
        self.sequence_reports[2] = {"completed": False, "full_input": False, "reason": "incomplete_pcm_input"}
        results = self.adapter.run_sequence(SERIAL, self.targets, self.stop)
        self.assertEqual(results[2]["status"], "blocked")
        self.assertIsNone(results[2]["app_grade"])

    def test_sequence_does_not_expand_subset(self):
        results = self.adapter.run_sequence(SERIAL, self.targets[:2], self.stop)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r["status"] == "blocked" for r in results))
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(self.launches, 0)

    def test_sequence_rejects_duplicate_scope_order_and_late_starts(self):
        variants = []
        duplicate = copy.deepcopy(self.targets); duplicate[2]["word_id"] = duplicate[0]["word_id"]; variants.append(duplicate)
        other = copy.deepcopy(self.targets); other[2]["scope"]["stage"] = 99; variants.append(other)
        late = copy.deepcopy(self.targets); late[2]["start_selector"] = late[0]["start_selector"]; variants.append(late)
        order = copy.deepcopy(self.targets)
        for item, number in zip(order, (1, 3, 2, 4)):
            item["order"] = number
        variants.append(order)
        for targets in variants:
            with self.subTest(targets=targets):
                self.assertTrue(all(r["status"] == "blocked" for r in self.adapter.run_sequence(SERIAL, targets, self.stop)))
        self.assertEqual(self.adapter.calls, [])

    def test_quiescent_clipped_bubbles_terminal_without_clicking_next(self):
        self.assertTrue(self.adapter._sequence_terminal(speech._xml(conversation_xml(4))))
        self.assertTrue(self.adapter._sequence_terminal(speech._xml(conversation_xml(2))))
        self.assertFalse(self.adapter._sequence_terminal(speech._xml(conversation_xml(2, last_graded=False))))
        self.assertFalse(self.adapter._sequence_terminal(speech._xml(conversation_xml(4, next_enabled=False))))

    def test_current_bubble_roi_among_four_grades(self):
        raw = json.dumps(dict(grade="Excellent", reason=None)).encode()
        with patch.object(speech, "_command", return_value=raw) as command:
            grade, reason = speech.DeviceAdapter._observe_grade(self.adapter, speech._xml(conversation_xml(4)),
                                                               self.root, time.monotonic() + 5, None, sequence_index=3)
        self.assertEqual(grade, "Excellent")
        self.assertEqual(json.loads(command.call_args.args[0][-1]), [300, 460, 400, 540])
        grade, reason = speech.DeviceAdapter._observe_grade(self.adapter, speech._xml(conversation_xml(4, last_graded=False)),
                                                           self.root, time.monotonic() + 5, None, sequence_index=3)
        self.assertIsNone(grade)

    def test_newest_empty_text_view_grade_accepts_clipped_history(self):
        raw = json.dumps(dict(grade="Perfect", reason=None)).encode()
        with patch.object(speech, "_command", return_value=raw) as command:
            grade, _ = speech.DeviceAdapter._observe_grade(self.adapter, speech._xml(conversation_xml(2)),
                                                          self.root, time.monotonic() + 5, None, sequence_index=3)
        self.assertEqual(grade, "Perfect")
        self.assertEqual(json.loads(command.call_args.args[0][-1]), [300, 220, 400, 300])

    def test_sequence_wrong_checkpoint_never_observes_grade(self):
        self.sequence_reports[1] = dict(sequence_index=0)
        self.sequence_reports[2] = dict(runtime=dict(text="wrong;", slot=0))
        results = self.adapter.run_sequence(SERIAL, self.targets, self.stop)
        self.assertEqual([r["app_grade"] for r in results], ["Good", None, None, "Good"])

    def test_png_checkpoint_hash_required_when_xml_raced(self):
        self.sequence_reports[0] = dict(capture_valid=False, png_capture_valid=True)
        self.sequence_reports[1] = dict(capture_valid=False, png_capture_valid=True, png_sha256="wrong")
        self.sequence_reports[2] = dict(capture_valid=False, png_capture_valid=False)
        with patch.object(self.adapter, "_observe_sequence_png", return_value=("Perfect", None)) as observe:
            results = self.adapter.run_sequence(SERIAL, self.targets, self.stop)
        self.assertEqual([r["app_grade"] for r in results], ["Perfect", None, None, "Good"])
        observe.assert_called_once()

    def test_png_signal_written_before_xml(self):
        self.adapter.started = True
        original = self.adapter._adb
        def adb(serial, args, *rest):
            if args == ["exec-out", "uiautomator", "dump", "/dev/tty"]:
                self.assertTrue((self.root / "png.signal").exists())
                self.assertFalse((self.root / "capture.signal").exists())
            return original(serial, args, *rest)
        with patch.object(self.adapter, "_adb", side_effect=adb):
            self.adapter._capture(SERIAL, self.root, time.monotonic() + 5, None)
        self.assertEqual((self.root / "png.signal").read_text(), hashlib.sha256((self.root / "capture.png").read_bytes()).hexdigest())

    def test_png_anchor_template_is_geometry_only(self):
        raw = json.dumps(dict(grade="Perfect", reason=None)).encode()
        with patch.object(speech, "_command", return_value=raw) as command:
            grade, _ = self.adapter._observe_sequence_png(speech._xml(conversation_xml(2, last_graded=False)),
                        self.root, {"meaning": "meaning-0"}, ["meaning-%d" % i for i in range(4)], time.monotonic() + 5, None)
        self.assertEqual(grade, "Perfect")
        self.assertEqual(command.call_args.args[0][2], "--local-conversation-ocr")
        self.assertEqual(json.loads(command.call_args.args[0][-1])["mean"], [30, 90, 200, 120])


class OcrTests(unittest.TestCase):
    def observe(self, rows):
        reader = Mock()
        reader.readtext.return_value = rows
        constructor = Mock(return_value=reader)
        image = Mock(width=100, height=100)
        image.convert.return_value = image
        image.crop.return_value.resize.return_value = "crop"
        modules = {"easyocr": types.SimpleNamespace(Reader=constructor),
                   "numpy": types.SimpleNamespace(array=lambda value: value),
                   "PIL": types.SimpleNamespace(Image=types.SimpleNamespace(open=lambda path: image))}
        with patch.dict("sys.modules", modules), patch.object(speech.sys, "addaudithook"):
            result = speech._local_grade_ocr("mock.png", [0, 0, 100, 100])
        self.assertFalse(constructor.call_args.kwargs["download_enabled"])
        return result

    def test_exact_confident_grade_and_percentage(self):
        result = self.observe([([], "Excellent", .97), ([], "94%", .95)])
        self.assertEqual(result["grade"], "Excellent")
        self.assertEqual(result["app_score"], 94)

    def test_low_confidence_ambiguity_and_non_grade_tokens(self):
        for rows in ([([], "Perfect", .2)], [([], "Perfect", .99), ([], "Fail", .99)],
                     [([], "Perfectly", .99)]):
            self.assertIsNone(self.observe(rows)["grade"])

    def test_numeric_score_requires_percentage_not_engine_number(self):
        for rows in ([([], "65", .99)], [([], "94%", .2)], [([], "94%", .99), ([], "67%", .99)]):
            self.assertIsNone(self.observe(rows)["app_score"])


class CommandTests(unittest.TestCase):
    def test_timeout_kills_only_spawned_process(self):
        child = Mock()
        child.poll.return_value = None
        child.communicate.side_effect = [subprocess.TimeoutExpired("MOCK", .1), (b"", b"")]
        with patch.object(speech, "_launch", return_value=child), patch.object(speech.time, "monotonic", side_effect=[0, 0, .1, .2, 2]):
            with self.assertRaises(TimeoutError):
                speech._command(["MOCK"], 1)
        child.kill.assert_called_once()

    def test_java_home_precedes_path(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            (home / "bin").mkdir()
            path = home / "bin" / ("java.exe" if os.name == "nt" else "java")
            path.touch()
            with patch.dict(os.environ, {"SPEECH_JAVA_HOME": str(home), "JAVA_HOME": "invalid"}):
                self.assertEqual(speech.DeviceAdapter._tool("java"), str(path))


@unittest.skipUnless(
    __name__ == "__main__" and os.environ.get("SPEECH_RUN_JAVA_CONTRACT_TESTS") == "1",
    "Local Java subprocess checks require standalone execution and SPEECH_RUN_JAVA_CONTRACT_TESTS=1",
)
class JdiArgumentTests(unittest.TestCase):
    def test_named_and_positional_bindings_with_jdi_proxies(self):
        """Run only local JVM proxies; no VM attach, ADB, or application calls."""
        try:
            javac, java = speech.DeviceAdapter._tool("javac"), speech.DeviceAdapter._tool("java")
        except speech._Blocked as exc:
            self.skipTest("Local JDK unavailable: " + str(exc))
        source = r'''
import com.sun.jdi.*;
import java.lang.reflect.Proxy;
import java.util.*;

public class SpeechArgumentCheck {
    interface Call { Object call(String name, Object[] args) throws Throwable; }
    @SuppressWarnings("unchecked") static <T> T mock(Class<T> type, Call call) {
        return (T) Proxy.newProxyInstance(type.getClassLoader(), new Class<?>[]{type}, (p,m,a) -> {
            if (m.getName().equals("equals")) return p == a[0];
            if (m.getName().equals("hashCode")) return System.identityHashCode(p);
            return call.call(m.getName(), a == null ? new Object[0] : a);
        });
    }
    static int checks;
    static void check(boolean ok, String reason) {
        if (!ok) throw new AssertionError(reason);
        checks++;
    }
    static class Fixture {
        String owner=SpeechProbe.TASK, signature=SpeechProbe.INPUT, methodName="recognitionFrame";
        String localType="[S", arrayType="[S", wrapperType="Lcom/selvy/sr/EduJPN/SelvySTT_pWrapper;";
        boolean missing, absent, namedNull, wrongArray, positional35, getValue35, isStatic;
        long frameIndex=0;
        int length=800, count=2, argumentReads, namedReads, writes, flagReads;
        ArrayReference buffer;
        StackFrame frame() {
            ReferenceType type=mock(ReferenceType.class,(m,a) -> m.equals("name") ? owner : null);
            ReferenceType array=mock(ReferenceType.class,(m,a) -> m.equals("signature") ? arrayType : null);
            buffer=mock(ArrayReference.class,(m,a) -> {
                if(m.equals("referenceType")) return array;
                if(m.equals("length")) return length;
                if(m.startsWith("set")) {writes++; throw new AssertionError("debuggee write during binding");}
                return null;
            });
            ReferenceType wrapper=mock(ReferenceType.class,(m,a) -> m.equals("signature") ? wrapperType : null);
            ObjectReference object=mock(ObjectReference.class,(m,a) -> m.equals("referenceType") ? wrapper : null);
            Method method=mock(Method.class,(m,a) -> {
                if(m.equals("name")) return methodName;
                if(m.equals("signature")) return signature;
                if(m.equals("declaringType")) return type;
                if(m.equals("isStatic")) return isStatic;
                if(m.equals("location")) return mock(Location.class,(n,b) -> n.equals("codeIndex") ? 0L : null);
                return null;
            });
            Location location=mock(Location.class,(m,a) -> m.equals("method") ? method : m.equals("codeIndex") ? frameIndex : null);
            LocalVariable local=mock(LocalVariable.class,(m,a) -> {
                if(m.equals("isArgument")) {flagReads++; return false;}
                if(m.equals("signature")) return localType;
                return null;
            });
            return mock(StackFrame.class,(m,a) -> {
                if(m.equals("location")) return location;
                if(m.equals("visibleVariableByName")) {
                    if(absent) throw new AbsentInformationException("no locals");
                    return missing ? null : local;
                }
                if(m.equals("getValue")) {
                    namedReads++;
                    if(getValue35) throw new InternalException("JDWP 35",35);
                    return namedNull ? null : wrongArray ? object : buffer;
                }
                if(m.equals("getArgumentValues")) {
                    argumentReads++;
                    if(positional35) throw new InternalException("JDWP 35",35);
                    return count==2 ? Arrays.<Value>asList(wrongArray ? object : buffer,object) : Collections.<Value>singletonList(buffer);
                }
                if(m.startsWith("set")) {writes++; throw new AssertionError("debuggee write during binding");}
                throw new AssertionError("Unexpected frame operation " + m);
            });
        }
    }
    static void accepted(Fixture f, boolean positional) throws Exception {
        StackFrame frame=f.frame();
        check(SpeechProbe.argument(frame,"buf")==f.buffer,"exact buffer returned");
        check(f.argumentReads==(positional?1:0) && f.namedReads==(positional?0:1),"correct read path");
        check(f.flagReads==0 && f.writes==0,"metadata flag unused; no debuggee writes");
    }
    static void rejected(Fixture f, String reason, boolean positional) throws Exception {
        StackFrame frame=f.frame();
        try {SpeechProbe.argument(frame,"buf");throw new AssertionError("binding unexpectedly accepted");}
        catch(IllegalStateException ex) {check(ex.getMessage().equals(reason),ex.getMessage());}
        check(f.argumentReads==(positional?1:0) && f.writes==0,"no unsafe fallback or write");
    }
    public static void main(String[] args) throws Exception {
        accepted(new Fixture(),false); // isArgument() would be false on this valid named local.
        Fixture f=new Fixture();f.missing=true;accepted(f,true);
        f=new Fixture();f.absent=true;accepted(f,true);
        f=new Fixture();f.signature="([SI)I";rejected(f,"unsupported_argument_binding",false);
        f=new Fixture();f.owner="other.Task";rejected(f,"unsupported_argument_binding",false);
        f=new Fixture();f.methodName="otherMethod";rejected(f,"unsupported_argument_binding",false);
        f=new Fixture();f.isStatic=true;rejected(f,"unsupported_argument_binding",false);
        f=new Fixture();f.frameIndex=1;rejected(f,"argument_not_at_method_entry",false);
        f=new Fixture();f.localType="[B";rejected(f,"argument_signature_mismatch_buf",false);
        f=new Fixture();f.namedNull=true;rejected(f,"argument_not_array_buf",false);
        f=new Fixture();f.wrongArray=true;rejected(f,"argument_not_array_buf",false);
        f=new Fixture();f.arrayType="[I";rejected(f,"argument_shape_mismatch_buf",false);
        f=new Fixture();f.length=799;rejected(f,"argument_shape_mismatch_buf",false);
        f=new Fixture();f.missing=true;f.count=1;rejected(f,"argument_count_mismatch",true);
        f=new Fixture();f.missing=true;f.wrapperType="Lwrong/Wrapper;";rejected(f,"argument_wrapper_mismatch",true);
        f=new Fixture();f.missing=true;f.wrongArray=true;rejected(f,"argument_not_array_buf",true);
        f=new Fixture();f.missing=true;f.length=801;rejected(f,"argument_shape_mismatch_buf",true);
        f=new Fixture();f.missing=true;f.positional35=true;rejected(f,"argument_values_unavailable_buf_jdwp_35",true);
        f=new Fixture();f.getValue35=true;
        try {SpeechProbe.argument(f.frame(),"buf");throw new AssertionError("named read error hidden");}
        catch(InternalException expected) {check(f.argumentReads==0 && f.writes==0,"named read error stays fail-closed");}
        f=new Fixture();
        try {SpeechProbe.argument(f.frame(),"unknown");throw new AssertionError("unknown argument accepted");}
        catch(IllegalStateException expected) {check(f.namedReads==0 && f.argumentReads==0,"unknown name unread");}
        System.out.println("JDI_BINDING_CHECKS="+checks);
    }
}
'''
        with tempfile.TemporaryDirectory(prefix="speech-argument-test-") as root:
            folder = Path(root)
            fixture = folder / "SpeechArgumentCheck.java"
            fixture.write_text(source, encoding="utf-8")
            tools_jar = Path(javac).resolve().parent.parent / "lib" / "tools.jar"
            classpath = str(folder) + (os.pathsep + str(tools_jar) if tools_jar.is_file() else "")
            probe = Path(speech.__file__).resolve().parent / "tools" / "speech" / "SpeechProbe.java"
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            compiled = subprocess.run([javac, "-encoding", "UTF-8", "-cp", classpath, "-d", str(folder),
                                       str(probe), str(fixture)], capture_output=True, timeout=30, creationflags=flags)
            self.assertEqual(compiled.returncode, 0, compiled.stderr.decode("utf-8", "replace"))
            checked = subprocess.run([java, "-cp", classpath, "SpeechArgumentCheck"],
                                     capture_output=True, timeout=30, creationflags=flags)
            self.assertEqual(checked.returncode, 0, checked.stderr.decode("utf-8", "replace"))
            self.assertIn(b"JDI_BINDING_CHECKS=41", checked.stdout)


if __name__ == "__main__":
    unittest.main()
