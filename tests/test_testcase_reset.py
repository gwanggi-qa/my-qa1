import unittest
from unittest.mock import patch
import mirror_server as m


class ResetTests(unittest.TestCase):
    def test_reset_clears_references_without_touching_files(self):
        with patch.multiple(m, _test_running=False, _random_running=False,
                            _record_play_running=False, _test_steps=[{'step': 1}],
                            _test_metadata={'upload_id': 'old'}, _test_workbook=object(),
                            _last_workbook_run=object(), _reviewed_plan=object(),
                            _verified_run=object()), patch.dict(m.tc_workflow_service, current=object()), \
                patch.object(m.socketio, 'emit') as emit, \
                m.app.test_request_context('/api/testcase', method='DELETE'):
            before = set(m.QA_RESULTS_DIR.rglob('*'))
            response = m.upload_testcase()
            self.assertEqual(response.json['count'], 0)
            self.assertEqual(m._test_metadata, {})
            self.assertIsNone(m.tc_workflow_service['current'])
            self.assertIsNone(m._last_workbook_run)
            self.assertEqual(before, set(m.QA_RESULTS_DIR.rglob('*')))
            emit.assert_called_once_with('testcase_changed', {}, namespace='/mirror')

    def test_busy_reset_and_upload_are_rejected(self):
        for flag in ('_test_running', '_random_running', '_record_play_running'):
            for method in ('DELETE', 'POST'):
                with patch.multiple(m, _test_running=False, _random_running=False,
                                    _record_play_running=False), patch.object(m, flag, True), \
                        m.app.test_request_context('/api/testcase', method=method):
                    original = m._test_steps
                    response, status = m.upload_testcase()
                    self.assertEqual(status, 409)
                    self.assertIs(m._test_steps, original)
