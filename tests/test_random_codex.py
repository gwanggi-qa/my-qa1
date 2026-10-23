import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import mirror_server as m


def candidate(number=1, label="Lessons"):
    return {
        "candidate_id": f"c{number:03d}", "action": "tap", "label": label,
        "source": "ui-analysis", "bounds": [10, 10, 80, 80], "score": 5,
        "step": {"action": "tap", "target": label, "x": 40, "y": 40},
    }


def response(ids):
    return {"summary": "Cover safe controls", "steps": [
        {"candidate_id": value, "reason": "Cover feature"} for value in ids
    ]}


class RandomCodexTests(unittest.TestCase):
    def setUp(self):
        m._random_stop_event.clear()
        m._codex_status_cache.clear()
        self.addCleanup(m._random_stop_event.clear)
        self.addCleanup(m._codex_status_cache.clear)

    def test_login_requires_chatgpt_not_api_key(self):
        for output, accepted in [("Logged in using ChatGPT", True),
                                 ("Logged in using an API key", False), ("Not logged in", False)]:
            with self.subTest(output=output), patch.object(m, "codex_executable", return_value="codex.exe"), \
                    patch.object(m.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", output)):
                self.assertEqual(m.codex_chatgpt_status(force=True)["configured"], accepted)

    def test_random_status_does_not_change_testcase_api_status(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}), \
                patch.object(m, "AI_PROVIDER", "openai_api"), \
                patch.object(m, "RANDOM_AI_PROVIDER", "codex_cli"), \
                patch.object(m, "codex_chatgpt_status", return_value={"configured": True, "state": "ready"}):
            data = m.app.test_client().get("/api/ai/status").get_json()
        self.assertFalse(data["configured"])
        self.assertTrue(data["random"]["configured"])
        self.assertEqual(data["random"]["provider"], "codex_cli")

    def test_codex_remote_access_uses_allowlist_even_with_legacy_flags(self):
        for flag in ("", "1"):
            with patch.dict(os.environ, {"RANDOM_AI_ALLOW_REMOTE": flag, "AI_ALLOW_REMOTE": flag}):
                self.assertFalse(m.random_ai_remote_access_allowed("192.168.1.20"))
                self.assertTrue(m.random_ai_remote_access_allowed("192.168.11.20"))
                self.assertTrue(m.random_ai_remote_access_allowed("127.0.0.1"))

    def test_remote_ai_run_is_rejected_before_device_work(self):
        with patch.dict(os.environ, {"RANDOM_AI_ALLOW_REMOTE": ""}), \
                patch.object(m, "check_device") as device:
            result = m.app.test_client().post(
                "/api/random/run", json={"ai_mode": True}, environ_base={"REMOTE_ADDR": "192.168.1.20"}
            )
        self.assertEqual(result.status_code, 403)
        device.assert_not_called()

    def test_cli_does_not_inherit_api_credentials(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "hidden", "CODEX_API_KEY": "hidden", "CODEX_ACCESS_TOKEN": "hidden"}):
            env = m.codex_process_environment()
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("CODEX_API_KEY", env)
        self.assertNotIn("CODEX_ACCESS_TOKEN", env)

    def cli_call(self, behavior="success"):
        context = {"requested_steps": 2, "candidates": [candidate()]}
        metrics = {"planner_calls": 0}
        process = MagicMock()
        process.returncode = 0
        process.poll.return_value = 0

        def launch(command, **kwargs):
            self.assertIn("--no-daemon", command)
            self.assertIn("read-only", command)
            self.assertIn("--ignore-user-config", command)
            self.assertIn("shell_tool", command)
            self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)
            self.assertFalse(kwargs.get("shell", False))
            schema = json.loads(Path(command[command.index("--output-schema") + 1]).read_text())
            self.assertEqual(schema["properties"]["steps"]["minItems"], 2)
            outfile = Path(command[command.index("--output-last-message") + 1])
            outfile.write_text(json.dumps(response(["c001", "c001"])), encoding="utf-8")
            return process

        if behavior == "success":
            process.communicate.return_value = (json.dumps({"type": "turn.completed", "usage": {
                "input_tokens": 120, "cached_input_tokens": 40, "output_tokens": 25,
            }}), "")
        elif behavior == "stop":
            process.poll.return_value = None
            def interrupt(**kwargs):
                if kwargs.get("input"):
                    m._random_stop_event.set()
                    raise subprocess.TimeoutExpired("codex", 0.2)
                return "", ""
            process.communicate.side_effect = interrupt
        elif behavior == "timeout":
            process.poll.return_value = None
            process.communicate.return_value = ("", "")
        elif behavior == "quota":
            process.returncode = 1
            process.communicate.return_value = ("", "usage_limit reached: private diagnostic")
        with patch.object(m, "codex_chatgpt_status", return_value={"configured": True}), \
                patch.object(m, "codex_executable", return_value="codex.exe"), \
                patch.object(m.subprocess, "Popen", side_effect=launch), \
                patch.object(m, "CODEX_REQUEST_TIMEOUT_S", -1 if behavior == "timeout" else 120):
            if behavior in {"stop", "timeout", "quota"}:
                error = m.RandomStopped if behavior == "stop" else RuntimeError
                with self.assertRaises(error) as caught:
                    m.request_codex_random_json("Plan only", context, metrics)
                self.assertNotIn("private diagnostic", str(caught.exception))
                if behavior != "quota":
                    process.terminate.assert_called_once()
                return
            data, usage = m.request_codex_random_json("Plan only", context, metrics)
        self.assertEqual(len(data["steps"]), 2)
        self.assertEqual(usage["output_tokens"], 25)
        self.assertEqual(metrics["planner_calls"], 1)
        self.assertTrue(metrics["response_received"])

    def test_cli_success_parses_usage_and_plan(self):
        self.cli_call()

    def test_stop_terminates_cli(self):
        self.cli_call("stop")

    def test_timeout_terminates_cli(self):
        self.cli_call("timeout")

    def test_quota_failure_is_sanitized(self):
        self.cli_call("quota")

    def test_openai_provider_is_explicit_and_still_supported(self):
        metrics = {"planner_calls": 0}
        with patch.object(m, "RANDOM_AI_PROVIDER", "openai_api"), \
                patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), \
                patch.object(m, "request_openai_json", return_value=(response(["c001"]), {"input_tokens": 2})) as api, \
                patch.object(m, "request_codex_random_json") as cli:
            m.request_ai_random_plan({}, metrics)
        api.assert_called_once()
        cli.assert_not_called()
        self.assertTrue(metrics["response_received"])

    def test_no_implicit_paid_api_fallback(self):
        with patch.object(m, "RANDOM_AI_PROVIDER", "codex_cli"), \
                patch.object(m, "codex_chatgpt_status", return_value={"configured": False, "message": "Login required"}), \
                patch.object(m, "request_openai_json") as api:
            with self.assertRaisesRegex(RuntimeError, "Login required"):
                m.request_ai_random_plan({}, {"planner_calls": 0})
        api.assert_not_called()

    def test_plan_rejects_unknown_unsafe_or_unsent_candidates(self):
        candidates = [candidate(i) for i in range(1, 102)] + [candidate(102, "payment")]
        for returned_id in ["invented", "c101", "c102"]:
            with self.subTest(returned_id=returned_id), \
                    patch.object(m, "collect_random_candidates", return_value=candidates), \
                    patch.object(m, "load_ai_random_history", return_value=[]), \
                    patch.object(m, "request_ai_random_plan", return_value=(response([returned_id]), {})):
                with self.assertRaisesRegex(ValueError, "outside"):
                    m.build_ai_random_plan(max_steps=1)

    def test_plan_rejects_coordinates_and_wrong_step_count(self):
        bad = response(["c001"])
        bad["steps"][0]["x"] = 42
        for data in [bad, response([]), response(["c001", "c001"])]:
            with self.subTest(data=data), patch.object(m, "collect_random_candidates", return_value=[candidate()]), \
                    patch.object(m, "load_ai_random_history", return_value=[]), \
                    patch.object(m, "request_ai_random_plan", return_value=(data, {})):
                with self.assertRaises(ValueError):
                    m.build_ai_random_plan(max_steps=1)

    def test_valid_plan_uses_original_adb_coordinates(self):
        with patch.object(m, "collect_random_candidates", return_value=[candidate()]), \
                patch.object(m, "load_ai_random_history", return_value=[]), \
                patch.object(m, "request_ai_random_plan", return_value=(response(["c001", "c001"]), {})):
            plan, meta = m.build_ai_random_plan(max_steps=2)
        self.assertEqual(len(plan), 2)
        self.assertEqual(plan[1]["step"]["x"], 40)
        self.assertEqual(plan[0]["candidate"]["source"], "ai-planner")
        self.assertFalse(meta["fallback"])

    def test_history_context_is_compact_and_keeps_failures_first(self):
        items = [{"status": "passed", "label": f"step {index}"} for index in range(12)]
        items.extend([{"status": "failed", "label": "error", "message": "screen mismatch"}])
        with patch.object(m, "load_ai_random_history", return_value=[{
            "results": items, "summary": "A" * 500, "created_at": "2026-09-23T12:00:00"
        }]):
            history = m.compact_ai_random_history(limit=1, max_results=5)
        self.assertEqual(len(history[0]["results"]), 5)
        self.assertEqual(history[0]["results"][0]["status"], "failed")
        self.assertEqual(len(history[0]["summary"]), 200)

    def run_mocked(self, plan_error=None, action_error=False):
        item = candidate()
        plan = [{"index": i, "candidate": dict(item), "step": dict(item["step"]),
                 "risk": "medium", "verify": "mock"} for i in range(1, 4)]
        meta = {"model": "Codex default", "provider": "codex_cli", "summary": "Mock plan",
                "fallback": False, "response_received": True, "planner_calls": 1,
                "usage": {"input_tokens": 12, "output_tokens": 4}}
        with patch.object(m, "check_device", return_value=True), \
                patch.object(m, "build_ai_random_plan", side_effect=plan_error, return_value=(plan, meta)), \
                patch.object(m, "build_random_plan", return_value=plan) as fallback, \
                patch.object(m, "adb") as adb, \
                patch.object(m, "execute_random_action", side_effect=[RuntimeError("step failed"), "ok", "ok"] if action_error else None, return_value="ok") as action, \
                patch.object(m, "failure_extra", return_value={}), \
                patch.object(m, "append_ai_random_history") as history, \
                patch.object(m.socketio, "emit") as emit:
            m.run_random_exploration(max_steps=3, delay_ms=0, ai_mode=True)
        finished = [call.args[1] for call in emit.call_args_list if call.args[0] == "random_run_finished"][0]
        return finished, history, fallback, action, adb

    def test_planning_stop_never_falls_back_or_taps(self):
        finished, _, fallback, action, adb = self.run_mocked(m.RandomStopped("stopped"))
        self.assertEqual(finished["stopped"], 1)
        self.assertFalse(finished["ai_fallback"])
        fallback.assert_not_called()
        action.assert_not_called()
        adb.assert_not_called()

    def test_action_failure_continues_and_reports_actual_usage(self):
        finished, history, _, action, _ = self.run_mocked(action_error=True)
        self.assertEqual((finished["passed"], finished["failed"]), (2, 1))
        self.assertEqual(action.call_count, 3)
        self.assertEqual(finished["ai"]["usage"]["input_tokens"], 12)
        self.assertTrue(history.call_args.args[0]["plan_ready"])

    def test_fallback_reason_is_preserved(self):
        finished, history, fallback, action, _ = self.run_mocked(RuntimeError("Codex login unavailable"))
        self.assertTrue(finished["ai_fallback"])
        self.assertIn("login unavailable", finished["ai"]["summary"])
        self.assertTrue(fallback.call_args.kwargs["safe_only"])
        self.assertEqual(action.call_count, 3)
        self.assertEqual(history.call_args.args[0]["planner_calls"], 0)

    def test_fallback_filters_risky_candidates_and_preserves_count(self):
        with patch.object(m, "collect_random_candidates", return_value=[candidate(), candidate(2, "payment")]):
            plan = m.build_random_plan(max_steps=10, safe_only=True)
        self.assertEqual(len(plan), 10)
        self.assertTrue(all(item["candidate"]["candidate_id"] == "c001" for item in plan))


if __name__ == "__main__":
    unittest.main()
