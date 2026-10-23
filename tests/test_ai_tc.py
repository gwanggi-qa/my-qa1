import copy
import io
import json
import hashlib
import subprocess
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from openpyxl import Workbook
from werkzeug.datastructures import FileStorage
import ai_tc
import mirror_server as m


def action(label="Learning"):
    return {"action": "tap", "target": label, "value": "", "direction": "", "wait_ms": 0, "source": label}


def check(text):
    return {"text": text, "source": text, "present": True}


def step():
    return {"action": "tap", "target": "Learning", "test_item": "Learning",
            "expected": "Analysis\nScore", "source": "wordp_testcase", "row": 3, "case_no": "TC-1"}


def hint():
    return {"index": 1, "intent": "Learning report", "preconditions": [],
            "actions": [action()], "checks": [check("Analysis"), check("Score")], "unsupported_reason": ""}


def candidate(label="Learning"):
    return {"candidate_id": "c001", "action": "tap", "label": label,
            "bounds": "[0,10][100,50]", "source": "ui-analysis"}


class AiTcTests(unittest.TestCase):
    def test_unlabeled_clickable_icon_has_real_bounds_and_stable_identity(self):
        xml = '<hierarchy><node class="android.widget.Button" clickable="true" enabled="true" bounds="[936,129][1080,273]"/></hierarchy>'
        with patch.object(m, 'dump_ui_xml', return_value=xml), patch.object(m, 'build_ocr_candidates', return_value=[]):
            candidates, context = m.ai_test_screen_candidates()
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0]['source'], 'ui-icon')
            self.assertTrue(context[0]['visual_identification_required'])
            self.assertEqual((candidates[0]['x'], candidates[0]['y']), (1008, 201))
            self.assertEqual(ai_tc.current_candidate(m, candidates[0]), candidates[0])

    def test_unlabeled_noninteractive_disabled_and_risky_controls_excluded(self):
        xml = '<hierarchy>' + ''.join([
            '<node bounds="[0,0][100,100]"/>',
            '<node clickable="true" enabled="false" bounds="[0,0][100,100]"/>',
            '<node clickable="true" resource-id="delete_account" bounds="[0,0][100,100]"/>',
            '<node clickable="true" bounds="[0,0][0,0]"/>',
            '<node clickable="true" package="com.android.systemui" bounds="[0,0][100,100]"/>'
        ]) + '</hierarchy>'
        with patch.object(m, 'dump_ui_xml', return_value=xml), patch.object(m, 'build_ocr_candidates', return_value=[]):
            self.assertEqual(m.ai_test_screen_candidates()[0], [])

    def test_reference_title_and_purpose_do_not_add_acceptance_checks(self):
        tc = {**step(), 'source': 'narrative_qa', 'title': 'Unrelated document',
              'test_purpose': 'Verify "Other" and unrelated account history'}
        self.assertEqual(ai_tc.validate_hint(hint(), tc), hint())

    def setUp(self):
        m._test_stop_event.clear()
        m._test_ai_context.metrics = {"planner_calls": 0, "responses": 0, "usage": {}}
        self.addCleanup(m._test_stop_event.clear)

    def test_all_expected_clauses_are_required(self):
        value = hint()
        value["checks"].pop()
        with self.assertRaisesRegex(ValueError, "cover"):
            ai_tc.validate_hint(value, step())

    def test_unknown_fields_and_invented_input_rejected(self):
        value = hint()
        value["actions"][0]["x"] = 10
        with self.assertRaises(ValueError):
            ai_tc.validate_hint(value, step())
        value = hint()
        value["actions"][0].update(action="input", value="invented")
        with self.assertRaisesRegex(ValueError, "input"):
            ai_tc.validate_hint(value, step())

    def test_only_one_of_two_checks_visible_fails(self):
        with patch.object(m, "dump_ui_xml", return_value='<hierarchy><node text="Analysis"/></hierarchy>'), \
                patch.object(m, "ocr_screen_texts", return_value=[]):
            with self.assertRaisesRegex(AssertionError, "Score"):
                ai_tc.verify(m, hint()["checks"])

    def test_conjunction_cannot_be_reduced_to_one_label(self):
        tc = {**step(), "expected": "Analysis and Score"}
        value = hint()
        value["checks"] = [{"source": tc["expected"], "text": "Analysis", "present": True}]
        with self.assertRaisesRegex(ValueError, "cover"):
            ai_tc.validate_hint(value, tc)

    def test_no_evidence_cannot_pass_absence_check(self):
        with patch.object(m, "dump_ui_xml", return_value=""), patch.object(m, "ocr_screen_texts", return_value=[]):
            with self.assertRaises(m.BlockedStep):
                ai_tc.verify(m, [{**check("Error"), "present": False}])

    def test_changed_candidate_rejected_before_adb(self):
        with patch.object(m, "ai_test_screen_candidates", return_value=([candidate("Other")], [])), \
                patch.object(m, "adb") as adb:
            with self.assertRaisesRegex(RuntimeError, "changed"):
                ai_tc.execute_action(m, action(), candidate())
        adb.assert_not_called()

    def test_compose_child_uses_clickable_parent_and_dedupes_ocr(self):
        xml = '<hierarchy><node clickable="true" bounds="[0,10][100,50]"><node text="Learning" bounds="[10,15][80,40]"/></node></hierarchy>'
        with patch.object(m, "dump_ui_xml", return_value=xml), \
                patch.object(m, "build_ocr_candidates", return_value=[candidate()]):
            found, _ = m.ai_test_screen_candidates()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["bounds"], "[0,10][100,50]")

    def test_cli_tc_never_calls_api_and_accumulates_usage(self):
        reply = {"summary": "Test", "risks": [], "execution_notes": []}
        with patch.object(m, "AI_PROVIDER", "codex_cli"), \
                patch.object(m, "request_codex_json", return_value=(reply, {"input_tokens": 3})) as cli, \
                patch.object(m, "request_openai_json") as api:
            m.request_test_ai_json("test", {}, ai_tc.OVERVIEW_SCHEMA)
            m.request_test_ai_json("test", {}, ai_tc.OVERVIEW_SCHEMA)
        api.assert_not_called()
        self.assertIs(cli.call_args.args[4], m._test_stop_event)
        self.assertEqual(m._test_ai_context.metrics["usage"]["input_tokens"], 6)

    def test_tc_cli_image_and_final_json_are_separate_from_events(self):
        proc = MagicMock(returncode=0)
        proc.poll.return_value = 0
        proc.communicate.return_value = ('{"type":"turn.completed","usage":{"output_tokens":8}}', "")
        def launch(command, **kwargs):
            self.assertEqual(Path(command[command.index("--image")+1]).read_bytes(), b"png")
            Path(command[command.index("--output-last-message")+1]).write_text(
                '{"summary":"OK","risks":[],"execution_notes":[]}', encoding="utf-8")
            return proc
        with patch.object(m, "codex_chatgpt_status", return_value={"configured": True}), \
                patch.object(m, "codex_executable", return_value="codex.exe"), \
                patch.object(m.subprocess, "Popen", side_effect=launch):
            data, usage = m.request_codex_json("test", {}, {"planner_calls": 0}, ai_tc.OVERVIEW_SCHEMA,
                                              m._test_stop_event, ai_tc.Stopped, b"png")
        self.assertEqual(data["summary"], "OK")
        self.assertEqual(usage["output_tokens"], 8)

    def test_stop_before_action_sends_no_input(self):
        m._test_stop_event.set()
        with patch.object(m, "adb") as adb:
            with self.assertRaises(ai_tc.Stopped):
                ai_tc.execute_action(m, action(), candidate())
        adb.assert_not_called()

    def test_tc_stop_kills_waiting_cli(self):
        proc = MagicMock()
        proc.poll.return_value = None
        def communicate(**kwargs):
            if kwargs.get("input"):
                m._test_stop_event.set()
                raise subprocess.TimeoutExpired("codex", 0.2)
            return "", ""
        proc.communicate.side_effect = communicate
        with patch.object(m, "codex_chatgpt_status", return_value={"configured": True}), \
                patch.object(m, "codex_executable", return_value="codex.exe"), \
                patch.object(m.subprocess, "Popen", return_value=proc):
            with self.assertRaises(ai_tc.Stopped):
                m.request_codex_json("test", {}, {"planner_calls": 0}, ai_tc.OVERVIEW_SCHEMA,
                                     m._test_stop_event, ai_tc.Stopped)
        proc.terminate.assert_called_once()

    def test_expected_mismatch_does_not_trigger_navigation(self):
        tc = {**step(), "_ai_phase": "verify"}
        with patch.object(m, "request_ai_test_recovery") as recovery:
            with self.assertRaises(m.AIRecoveryExhausted):
                m.run_ai_test_recovery(1, tc, "Score missing", {}, {})
        recovery.assert_not_called()

    def test_recovery_navigation_resumes_original_plan(self):
        tc = {**step(), "ai_hint": hint(), "_ai_phase": "prepare"}
        recovery = {"decision": "execute", "action": "tap", "candidate_id": "c001",
                    "candidate": candidate(), "reason": "Navigate to Home", "wait_ms": 0,
                    "snapshot": hashlib.sha256(json.dumps([], sort_keys=True).encode()).hexdigest()}
        with patch.object(m, "request_ai_test_recovery", return_value=recovery), \
                patch.object(m, "ai_test_screen_candidates", return_value=([candidate()], [])), \
                patch.object(ai_tc, "execute_action", return_value="navigated") as execute, \
                patch.object(ai_tc, "execute_plan", return_value="all checks verified") as resume, \
                patch.object(m, "capture_failure_screenshot", return_value="proof.png"), \
                patch.object(m, "emit_test_ai_status"):
            result = m.run_ai_test_recovery(1, tc, "Home missing", {}, {})
        execute.assert_called_once()
        resume.assert_called_once()
        self.assertIn("ai-assisted", result["message"])
        self.assertEqual(tc.get("_ai_cursor", 0), 0)

    def test_recovery_rejects_stale_snapshot_without_input(self):
        tc = {**step(), "ai_hint": hint(), "_ai_phase": "prepare"}
        recovery = {"decision": "execute", "action": "tap", "candidate_id": "c001",
                    "candidate": candidate(), "reason": "navigate", "wait_ms": 0, "snapshot": "old"}
        with patch.object(m, "request_ai_test_recovery", return_value=recovery), \
                patch.object(m, "ai_test_screen_candidates", return_value=([], [])), \
                patch.object(ai_tc, "execute_action") as execute, patch.object(m, "emit_test_ai_status"):
            with self.assertRaises(m.BlockedStep):
                m.run_ai_test_recovery(1, tc, "missing", {}, {})
        execute.assert_not_called()
        self.assertEqual(len(tc["_ai_trace"]), 5)

    def test_ambiguous_block_recoverable_but_environment_is_not(self):
        tc = {**step(), "action": "blocked", "block_reason": "WordP TestCase sentence is ambiguous for automation."}
        self.assertEqual(ai_tc.hard_block_reason(m, tc), "")
        tc["precondition"] = "Wi-Fi OFF"
        self.assertTrue(ai_tc.hard_block_reason(m, tc))

    def test_multipart_plan_records_one_tc_and_runs_in_order(self):
        tc = {**step(), "ai_hint": hint()}
        tc["ai_hint"]["actions"].append(action("Report"))
        with patch.object(ai_tc, "execute_action", return_value="sent") as execute, \
                patch.object(ai_tc, "verify", return_value="Analysis; Score"), patch.object(m, "emit_test_ai_status"):
            ai_tc.execute_plan(m, tc)
        self.assertEqual([call.args[1]["target"] for call in execute.call_args_list], ["Learning", "Report"])
        self.assertEqual(len(tc["_ai_trace"]), 2)
        self.assertEqual(tc["_ai_phase"], "done")

    def test_original_excel_headers_and_fields_preserved(self):
        wb = Workbook()
        wb.active.title = "Learning"
        wb.active.append(["TC ID", "사전조건", "테스트 절차", "기대결과"])
        wb.active.append(["TC-1", "Home", "Learning 버튼 클릭", "Analysis"])
        content = io.BytesIO()
        wb.save(content)
        content.seek(0)
        parsed = m.parse_excel(FileStorage(stream=content, filename="qa.xlsx"))[0]
        self.assertEqual(parsed["case_no"], "TC-1")
        self.assertEqual(parsed["precondition"], "Home")
        self.assertEqual(parsed["test_item"], "Learning 버튼 클릭")
        self.assertEqual(parsed["sheet"], "Learning")

    def test_automation_and_recorder_upload_regression(self):
        for recorder in [False, True]:
            wb = Workbook()
            ws = wb.active
            ws.title = "Automation" if not recorder else "Recording"
            ws.append(["type" if recorder else "Action", "Target", "x", "y", "x1", "y1", "x2", "y2",
                       "delay_ms", "duration_ms", "screen_width", "screen_height"])
            ws.append(["tap", "", 50, 50, "", "", "", "", 20, 350, 1340, 800])
            data = io.BytesIO()
            wb.save(data)
            data.seek(0)
            parsed = m.parse_excel(FileStorage(stream=data, filename="tc.xlsx"))[0]
            self.assertEqual(parsed["action"], "tap")
            self.assertEqual(parsed["delay_ms"], 20)
            self.assertEqual(parsed["source"], "recorder")

    def test_other_automation_rejected_during_tc(self):
        with patch.object(m, "_test_running", True), patch.object(m.threading, "Thread") as thread:
            client = m.app.test_client()
            for url in ["/api/run", "/api/random/run", "/api/recordings/example/play"]:
                response = client.post(url, json={}, base_url="http://localhost:5000",
                                       headers={"Origin": "http://localhost:5000",
                                                "X-QA-CSRF-Token": m.local_access.token})
                self.assertEqual(response.status_code, 409)
                self.assertIn("Another automation", response.json["error"])
                denied = client.post(url, json={}, base_url="http://localhost:5000",
                                     headers={"Origin": "http://localhost:5000"})
                self.assertEqual(denied.status_code, 403)
                self.assertEqual(denied.json["code"], "page_verification_required")
        thread.assert_not_called()

    def run_suite(self, steps, ai=True, overview_error=None):
        with ExitStack() as stack:
            for name, value in [("_test_steps", steps), ("_test_metadata", {}), ("AI_PROVIDER", "codex_cli")]:
                stack.enter_context(patch.object(m, name, value))
            for name in ["validate_recorder_resolution", "append_ai_test_history", "append_test_run_history",
                         "record_linked_retest", "queue_test_failure"]:
                stack.enter_context(patch.object(m, name))
            stack.enter_context(patch.object(m, "check_device", return_value=True))
            stack.enter_context(patch.object(m, "failure_extra", return_value={}))
            stack.enter_context(patch.object(m, "request_ai_test_overview", return_value={}, side_effect=overview_error))
            stack.enter_context(patch.object(m, "request_ai_test_batch_hints", return_value=({1: hint(), 2: hint()}, {})))
            stack.enter_context(patch.object(ai_tc, "execute_action", return_value="sent"))
            stack.enter_context(patch.object(ai_tc, "verify", return_value="All checks"))
            standard = stack.enter_context(patch.object(m, "execute_step", return_value="standard OK"))
            api = stack.enter_context(patch.object(m, "request_openai_json"))
            emit = stack.enter_context(patch.object(m.socketio, "emit"))
            m.run_test_steps(ai_mode=ai)
        done = [c.args[1] for c in emit.call_args_list if c.args[0] == "test_run_finished"][0]
        return done, standard, api

    def test_ambiguous_block_can_complete_using_plan(self):
        tc = {**step(), "action": "blocked", "block_reason": "Sentence is ambiguous"}
        done, standard, _ = self.run_suite([tc])
        self.assertEqual(done["passed"], 1)
        self.assertEqual(done["ai_assisted"], 1)
        standard.assert_not_called()

    def test_cli_error_falls_back_to_standard_not_api(self):
        done, standard, api = self.run_suite([step(), step()], overview_error=RuntimeError("quota reached"))
        self.assertEqual(done["passed"], 2)
        self.assertTrue(done["ai_fallback"])
        self.assertEqual(standard.call_count, 2)
        api.assert_not_called()

    def test_standard_run_unchanged(self):
        done, standard, api = self.run_suite([step()], ai=False)
        self.assertEqual(done["passed"], 1)
        standard.assert_called_once()
        api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
