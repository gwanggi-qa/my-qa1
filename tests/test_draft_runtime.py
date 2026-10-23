import io
import json
import subprocess
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import mirror_server as m
from ai_request_errors import AIRequestError, DRAFT_TIMEOUT_S, ERRORS
from tc_drafts import DraftStopped


class DraftRuntimeTests(unittest.TestCase):
    def cli(self, proc, times, timeout=600):
        with patch.object(m, 'codex_chatgpt_status', return_value={'configured': True}), \
                patch.object(m, 'codex_executable', return_value='codex.exe'), \
                patch.object(m.subprocess, 'Popen', return_value=proc), \
                patch.object(m.time, 'monotonic', side_effect=times):
            return m.request_codex_json('synthetic', {}, {'planner_calls': 0}, {},
                                        threading.Event(), DraftStopped, timeout_s=timeout)

    def test_draft_timeout_does_not_change_defaults(self):
        self.assertEqual(m.CODEX_REQUEST_TIMEOUT_S, 120)
        self.assertEqual(m.AI_REQUEST_TIMEOUT_S, 45)
        with patch.object(m, 'AI_PROVIDER', 'codex_cli'), \
                patch.object(m, 'request_codex_json', return_value=({}, {})) as cli, \
                patch.object(m, 'request_openai_json') as api:
            m.request_draft_ai_json('', {}, {}, {}, threading.Event(), DraftStopped)
        self.assertEqual(cli.call_args.kwargs['timeout_s'], 600)
        api.assert_not_called()

    def test_cli_waits_past_120_and_terminates_at_600(self):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.communicate.side_effect = [subprocess.TimeoutExpired('codex', .2), ('', '')]
        with self.assertRaises(AIRequestError) as error:
            self.cli(proc, [0, 121, 600])
        self.assertEqual(error.exception.code, 'timeout')
        self.assertEqual(proc.communicate.call_count, 2)
        proc.terminate.assert_called_once()

    def test_cli_default_still_times_out_at_120(self):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.communicate.return_value = ('', '')
        with self.assertRaisesRegex(RuntimeError, '120s'):
            self.cli(proc, [0, 120], timeout=None)
        proc.terminate.assert_called_once()

    def test_safe_cli_categories(self):
        for diagnostic, code in [('usage limit private-key', 'usage_limit'),
                                 ('401 private-key', 'authentication'),
                                 ('error sending request private-key', 'connection'),
                                 ('unknown private-key', 'cli_execution')]:
            proc = MagicMock(returncode=1)
            proc.poll.return_value = 1
            proc.communicate.return_value = ('', diagnostic)
            with self.subTest(code=code), self.assertRaises(AIRequestError) as error:
                self.cli(proc, [0, 1, 1])
            self.assertEqual(error.exception.code, code)
            self.assertNotIn('private-key', str(error.exception))

    def test_cli_success_after_120_and_late_response_rejected(self):
        for returned_at in (200, 601):
            proc = MagicMock(returncode=0)
            proc.poll.return_value = 0
            proc.communicate.return_value = ('{"type":"turn.completed","usage":{"output_tokens":2}}', '')
            def launch(command, **kwargs):
                Path(command[command.index('--output-last-message')+1]).write_text('{"ok":true}', encoding='utf-8')
                return proc
            with self.subTest(returned_at=returned_at), \
                    patch.object(m, 'codex_chatgpt_status', return_value={'configured': True}), \
                    patch.object(m, 'codex_executable', return_value='codex.exe'), \
                    patch.object(m.subprocess, 'Popen', side_effect=launch), \
                    patch.object(m.time, 'monotonic', side_effect=[0, 121, returned_at]):
                args = ('synthetic', {}, {'planner_calls': 0}, {}, threading.Event(), DraftStopped)
                if returned_at < 600:
                    value, usage = m.request_codex_json(*args, timeout_s=600)
                    self.assertTrue(value['ok'])
                    self.assertEqual(usage['output_tokens'], 2)
                else:
                    with self.assertRaises(AIRequestError) as caught:
                        m.request_codex_json(*args, timeout_s=600)
                    self.assertEqual(caught.exception.code, 'timeout')

    def test_malformed_json_is_classified_by_draft_service(self):
        from test_tc_drafts import DraftTests
        fixture = DraftTests()
        fixture.setUp()
        try:
            def malformed(*args):
                return m.parse_openai_json('not JSON private material'), {}
            fixture.service.runner = malformed
            batch = fixture.generate()
            self.assertEqual(batch['error_code'], 'invalid_response')
            self.assertNotIn('private material', batch['error'])
        finally:
            fixture.doCleanups()

    def test_api_timeout_and_http_categories(self):
        for status, code in [(401, 'authentication'), (403, 'authentication'), (429, 'usage_limit'),
                             (504, 'timeout'), (503, 'connection'), (400, 'unknown')]:
            error = urllib.error.HTTPError('url', status, 'secret', {}, io.BytesIO(b'secret'))
            with self.subTest(status=status), patch.dict(m.os.environ, {'OPENAI_API_KEY': 'synthetic'}), \
                    patch.object(m.urllib.request, 'urlopen', side_effect=error) as call:
                with self.assertRaises(AIRequestError) as caught:
                    m.request_openai_json('', {}, timeout_s=600)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(call.call_args.kwargs['timeout'], 600)
                self.assertNotIn('secret', str(caught.exception))

    def test_api_draft_stop_discards_late_response(self):
        entered, release, cancel = threading.Event(), threading.Event(), threading.Event()
        def delayed(*args, **kwargs):
            entered.set()
            release.wait(3)
            return {'late': True}, {}
        outcome = []
        def run():
            try:
                m.request_draft_ai_json('', {}, {'planner_calls': 0}, {}, cancel, DraftStopped)
            except DraftStopped:
                outcome.append('stopped')
        with patch.object(m, 'AI_PROVIDER', 'openai_api'), patch.object(m, 'request_openai_json', side_effect=delayed):
            worker = threading.Thread(target=run)
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                cancel.set()
                worker.join(2)
                self.assertEqual(outcome, ['stopped'])
            finally:
                release.set()
                worker.join(3)

    def test_api_draft_wall_clock_timeout(self):
        release = threading.Event()
        def delayed(*args, **kwargs):
            release.wait(3)
            return {}, {}
        with patch.object(m, 'AI_PROVIDER', 'openai_api'), \
                patch.object(m, 'request_openai_json', side_effect=delayed), \
                patch.object(m.time, 'monotonic', side_effect=[0, 600]):
            try:
                with self.assertRaises(AIRequestError) as error:
                    m.request_draft_ai_json('', {}, {'planner_calls': 0}, {}, threading.Event(), DraftStopped)
                self.assertEqual(error.exception.code, 'timeout')
            finally:
                release.set()
