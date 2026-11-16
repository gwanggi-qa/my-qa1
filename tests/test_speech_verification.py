import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask

from speech_verification import Store, conclude, pcm_audio, policy, verdict, workbook
from speech_service import SpeechService, register


class EvidenceTests(unittest.TestCase):
    def test_history_read_uses_same_lock_as_atomic_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            with patch.object(store, '_history', side_effect=lambda: store.lock._is_owned()):
                self.assertTrue(store.history())

    def test_input_record_list_only_exposes_bounded_recognition_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            source = {'engine_fields': {'recogState': 1, 'recMs': 200, 'mode': 'm' * 100,
                                        'private_raw': 'not-for-list'}, 'txt_base64': 'raw-log'}
            asset = store.add_asset(b'\1\0' * 800, 'record.pcm', {
                'sample_rate': 16000, 'channels': 1, 'sample_width': 2,
                'kind': 'device_recording', 'source': source})
            listed = store.assets()[0]
            self.assertEqual(listed['input_record'], {'recogState': '1', 'recMs': '200', 'mode': 'm' * 80})
            self.assertNotIn('engine_fields', listed['source'])
            self.assertNotIn('txt_base64', listed['source'])
            self.assertNotIn('app_grade', listed)
            self.assertEqual(store.asset(asset['id'])[0]['source'], source)

    def test_full_input_and_real_grade_required(self):
        expected = policy({'approved': True, 'expected_grades': ['Perfect']})
        a = {'status': 'completed', 'signature_verified': True, 'target_verified': True, 'full_input': True, 'app_grade': 'Perfect', 'files': ['screen.png']}
        self.assertEqual(verdict(a, expected)[0], 'passed')
        for field in ('status', 'signature_verified', 'target_verified', 'full_input', 'app_grade'):
            self.assertEqual(verdict({**a, field: None}, expected)[0], 'blocked')
        self.assertEqual(verdict({**a, 'files': ['input.wav', 'probe.log']}, expected)[0], 'blocked')

    def test_state_one_alone_is_not_pass(self):
        self.assertEqual(verdict({'engine_state': 1}, policy({}))[0], 'blocked')

    def test_review_without_approved_policy(self):
        a = {'status': 'completed', 'signature_verified': True, 'target_verified': True, 'full_input': True, 'app_grade': 'Excellent', 'files': ['s.png']}
        self.assertEqual(verdict(a, policy({}))[0], 'unverified')

    def test_bad_is_not_semantic_rejection(self):
        a = {'status': 'completed', 'signature_verified': True, 'target_verified': True, 'full_input': True, 'app_grade': 'Bad', 'files': ['s.png']}
        self.assertEqual(verdict(a, policy({'approved': True, 'expect_reject': True}))[0], 'failed')
        self.assertEqual(verdict({**a, 'app_grade': 'Fail'}, policy({'approved': True, 'expect_reject': True}))[0], 'passed')

    def test_retry_never_hides_first_failure(self):
        a = {'status': 'completed', 'signature_verified': True, 'target_verified': True, 'full_input': True, 'app_grade': 'Bad', 'files': ['s.png']}
        b = {**a, 'app_grade': 'Perfect'}
        expected = policy({'approved': True, 'expected_grades': ['Perfect']})
        self.assertEqual(conclude([a, b], expected)[0], 'unverified')
        self.assertEqual(conclude([a, {**b, 'full_input': False}], expected)[0], 'blocked')

    def test_pcm_no_silent_transform_and_hash_tamper(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(d)
            data = b'\x01\x00' * 16000
            a = s.add_asset(data, 'human.pcm', {'sample_rate': 16000, 'channels': 1, 'sample_width': 2})
            record, path = s.asset(a['id'])
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(record['transforms'], [])
            path.write_bytes(b'\0\0')
            with self.assertRaises(ValueError):
                s.asset(a['id'])
            with self.assertRaises(ValueError):
                pcm_audio(data, '.pcm', {})
            with self.assertRaises(ValueError):
                pcm_audio(data, '.pcm', {'sample_rate': 44100, 'channels': 1, 'sample_width': 2})

    def test_truncated_wav_is_not_silently_shortened(self):
        import wave
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as audio:
            audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            audio.writeframes(b'\1\0'*1600)
        with self.assertRaises(ValueError):
            pcm_audio(buffer.getvalue()[:-200], '.wav', {})

    def test_concurrent_same_asset_import_deduplicates(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            def add(_):
                return store.add_asset(b'\1\0'*800, 'same.pcm', {'sample_rate':16000,'channels':1,'sample_width':2})['id']
            with ThreadPoolExecutor(max_workers=4) as pool:
                ids = list(pool.map(add, range(4)))
            self.assertEqual(len(set(ids)), 1)

    def test_recover_and_path_traversal(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(d)
            run = {'id': 'a'*32, 'status': 'running', 'started_at': '2026'}
            s.save(run)
            s.recover()
            self.assertEqual(s.get(run['id'])['status'], 'interrupted')
            with self.assertRaises(ValueError):
                s.file(run['id'], '../../../mirror_server.py')

    def test_excel_formulas_and_null_scores(self):
        from openpyxl import load_workbook
        run = {'id': 'a'*32, 'status': 'finished', 'results': [{'word_id': '1', 'text': '=1+1', 'status': 'unverified',
                'reason': 'review', 'attempts': [{'app_grade': 'Perfect', 'scores': {'raw': None}, 'files': []}]}]}
        run['results'].extend([
            {'word_id': '2', 'text': 'scored', 'status': 'unverified', 'attempts': [
                {'app_grade': 'Bad', 'app_score': 42.5}, {'app_grade': 'Fail', 'app_score': 0}]},
            {'word_id': '3', 'text': 'latest missing', 'status': 'unverified', 'attempts': [
                {'app_score': 99}, {'app_grade': 'Perfect', 'scores': {'raw': 123}}]},
            {'word_id': '4', 'text': 'no attempts', 'status': 'not_run', 'attempts': []},
            {'word_id': '5', 'text': 'explicit null', 'status': 'unverified', 'attempts': [
                {'app_score': None}]},
        ])
        wb = load_workbook(workbook(run))
        self.assertEqual(len(wb.sheetnames), 5)
        self.assertEqual(wb['문장별 결과']['B2'].data_type, 's')
        self.assertEqual(wb['내부 점수']['D2'].value, 'null')
        cases, attempts = wb['문장별 결과'], wb['입력 시도']
        self.assertEqual(cases.max_column, 11)
        self.assertEqual(attempts.max_column, 9)
        self.assertEqual(cases['J1'].value, '화면 점수')
        self.assertEqual(attempts['H1'].value, '화면 점수')
        self.assertEqual([cases.cell(row, 10).value for row in range(2, 7)],
                         [None, 0, None, None, None])
        self.assertEqual([attempts.cell(row, 8).value for row in range(2, 8)],
                         [None, 42.5, 0, 99, None, None])
        self.assertEqual(cases['J3'].data_type, 'n')
        self.assertEqual(attempts['H3'].data_type, 'n')

    def test_word_outcome_export_keeps_grade_and_score_empty(self):
        from openpyxl import load_workbook
        run = {'id': 'b'*32, 'status': 'finished', 'results': [
            {'word_id': '20001', 'text': 'word', 'status': 'passed', 'attempts': [
                {'app_grade': None, 'app_score': None, 'app_outcome': 'word_correct'}]}]}
        wb = load_workbook(workbook(run))
        self.assertEqual(wb['문장별 결과']['K2'].value, 'word_correct')
        self.assertEqual(wb['입력 시도']['I2'].value, 'word_correct')
        self.assertIsNone(wb['문장별 결과']['D2'].value)
        self.assertIsNone(wb['문장별 결과']['J2'].value)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.host = SimpleNamespace(_automation_start_lock=threading.Lock(), _test_running=False, _random_running=False,
             _record_play_running=False, _speech_running=False, append_test_run_history=Mock())
        self.device = {'serial': 'serial', 'build': 11, 'package': 'com.weaversmind.tjp2', 'supported': True}
        self.items = [{'word_id': '10', 'text': 'sample', 'target': 'sample', 'level': 2, 'week': 1, 'stage': 3}]
        self.materials = Mock()
        self.materials.catalog.return_value = {'items': self.items, 'db_sha256': 'hash'}
        self.adapter = Mock()
        self.adapter.inspect.return_value = self.device
        self.nav = Mock()
        self.nav.prepare.return_value = {'ready': True, 'actions': []}
        self.nav.retry.return_value = {'ready': True, 'actions': []}
        self.nav.complete_unit.return_value = {'unit_completed': True}
        self.service = SpeechService(lambda: self.host, self.tmp.name, self.adapter, self.materials, lambda *a: self.nav)
        self.asset = self.service.store.add_asset(b'\1\0'*800, 'x.pcm', {'sample_rate': 16000, 'channels': 1, 'sample_width': 2})

    def preview(self, **kwargs):
        return self.service.preview({'device': 'serial', 'items': [{'word_id': '10', 'asset_id': self.asset['id'],
                                    'approved': True, 'expected_grades': ['Perfect']}], **kwargs})

    def stop_client(self):
        app = Flask('offline-speech-stop-test')
        app.testing = True
        with patch('speech_service.SpeechService', return_value=self.service):
            register(app, lambda: self.host, root=self.tmp.name)
        return app.test_client()

    def test_prepare_missing_input_and_duplicate_id(self):
        p = self.service.preview({'device': 'serial', 'items': [{'word_id': '10'}]})
        self.assertFalse(p['ready'])
        with self.assertRaises(ValueError):
            self.service.preview({'device': 'serial', 'items': [{'word_id': '10'}, {'word_id': '10'}]})

    def test_device_lock_and_changed_build(self):
        p = self.preview()
        self.host._random_running = True
        with self.assertRaises(RuntimeError):
            self.service.start({'preview_id': p['id'], 'approved': True})
        self.host._random_running = False
        self.adapter.inspect.return_value = {**self.device, 'build': 12}
        with self.assertRaises(ValueError):
            self.service.start({'preview_id': p['id'], 'approved': True})

    def test_whole_unit_requires_all_items(self):
        self.items.append({'word_id': '11', 'text': 'another', 'level': 2, 'week': 1, 'stage': 3})
        self.assertFalse(self.preview(mode='unit', level=2, week=1, stage=3)['ready'])

    def test_unknown_navigation_and_duplicate_catalog_never_ready(self):
        self.items[0]['navigation_available'] = False
        self.items[0]['navigation_reason'] = 'unmapped session'
        self.assertFalse(self.preview()['ready'])
        self.items.append(dict(self.items[0], stage=4))
        with self.assertRaises(ValueError):
            self.preview()

    def test_restart_holds_device_until_cleanup_checked(self):
        self.service.store.save({'id':'b'*32,'status':'running','started_at':'2026', 'device':self.device})
        self.service.recover()
        self.assertTrue(self.service.cleanup_required)
        self.assertTrue(self.host._speech_running)

    def test_multiple_resumes_preserve_original_evidence_owner(self):
        p = self.preview()
        with patch('speech_service.threading.Thread'):
            run = self.service.start({'preview_id':p['id'], 'approved':True})
        run['status'] = 'stopped'
        run['results'][0].update(status='unverified', inherited_from='c'*32)
        run['results'].append(dict(run['results'][0], word_id='11', status='not_run'))
        self.service.store.save(run)
        self.service.busy = self.host._speech_running = False
        with patch('speech_service.threading.Thread'):
            resumed = self.service.start({'run_id':run['id'], 'approved':True}, resume=True)
        self.assertEqual(resumed['results'][0]['inherited_from'], 'c'*32)

    def test_stop_during_preflight_is_not_cleared(self):
        p = self.preview()
        self.adapter.inspect.side_effect = lambda device: (self.service.stop_event.set() or self.device)
        with patch('speech_service.threading.Thread') as worker:
            with self.assertRaises(RuntimeError):
                self.service.start({'preview_id':p['id'], 'approved':True})
            worker.assert_not_called()
        self.assertFalse(self.service.busy)
        self.assertFalse(self.host._speech_running)

    def test_late_stop_during_history_publication_preserves_finished_run(self):
        preview = self.preview()
        self.adapter.run_attempt.return_value = {
            'status': 'completed', 'signature_verified': True, 'target_verified': True,
            'full_input': True, 'app_grade': 'Perfect', 'files': ['grade.png'], 'cleanup_ok': True,
        }
        with patch('speech_service.threading.Thread'):
            run = self.service.start({'preview_id': preview['id'], 'approved': True})
        client = self.stop_client()
        publishing, release = threading.Event(), threading.Event()
        errors = []

        def publish(dashboard):
            self.assertEqual(dashboard['state'], 'finished')
            publishing.set()
            if not release.wait(5):
                raise AssertionError('History publication was not released')

        def execute():
            try:
                self.service.execute(run)
            except Exception as exc:
                errors.append(exc)

        self.host.append_test_run_history.side_effect = publish
        worker = threading.Thread(target=execute, daemon=True)
        worker.start()
        try:
            self.assertTrue(publishing.wait(5), errors)
            self.assertTrue(self.service.busy)
            self.assertTrue(self.host._speech_running)
            with patch.object(self.service.store, 'save', wraps=self.service.store.save) as save:
                response = client.post('/api/speech-verification/stop', json={})
                self.assertEqual(response.json['status'], 'finished')
                save.assert_not_called()
            self.assertEqual(self.service.current['status'], 'finished')
            self.assertEqual(self.service.store.get(run['id'])['status'], 'finished')
            self.assertTrue(self.service.busy)
            self.assertTrue(self.host._speech_running)
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertFalse(errors, errors)
        self.assertFalse(self.service.busy)
        self.assertFalse(self.host._speech_running)
        self.assertEqual(run['status'], 'finished')
        self.assertEqual(self.service.store.get(run['id'])['status'], 'finished')

    def test_stop_preserves_all_terminal_statuses_while_busy(self):
        client = self.stop_client()
        for status in ('finished', 'blocked', 'stopped', 'interrupted'):
            with self.subTest(status=status):
                self.service.busy = self.host._speech_running = True
                self.service.current = {'id': 'a'*32, 'status': status}
                with patch.object(self.service.store, 'save') as save:
                    response = client.post('/api/speech-verification/stop', json={})
                    save.assert_not_called()
                self.assertEqual(response.json['status'], status)
                self.assertEqual(self.service.current['status'], status)
                self.assertTrue(self.service.busy)
                self.assertTrue(self.host._speech_running)

    def test_stop_still_marks_active_runs_and_reports_idle(self):
        client = self.stop_client()
        for status in ('running', 'stopping'):
            with self.subTest(status=status):
                self.service.stop_event.clear()
                self.service.busy = True
                self.service.current = {'id': 'a'*32, 'status': status}
                response = client.post('/api/speech-verification/stop', json={})
                self.assertEqual(response.json['status'], 'stopping')
                self.assertTrue(self.service.stop_event.is_set())
                self.assertEqual(self.service.current['status'], 'stopping')
                self.assertEqual(self.service.store.get('a'*32)['status'], 'stopping')
        self.service.busy = False
        self.service.current['status'] = 'finished'
        with patch.object(self.service.store, 'save') as save:
            self.assertEqual(client.post('/api/speech-verification/stop', json={}).json['status'], 'idle')
            save.assert_not_called()
        self.assertEqual(self.service.current['status'], 'finished')

    def test_save_failure_releases_execution_lock(self):
        p = self.preview()
        with patch.object(self.service.store, 'save', side_effect=OSError('full')):
            with self.assertRaises(OSError):
                self.service.start({'preview_id':p['id'], 'approved':True})
        self.assertFalse(self.service.busy)
        self.assertFalse(self.host._speech_running)
        with patch('speech_service.threading.Thread'):
            run = self.service.start({'preview_id':p['id'], 'approved':True})
        self.service.stop_event.set()
        with patch.object(self.service.store, 'save', side_effect=OSError('full')):
            self.service.execute(run)
        self.assertFalse(self.service.busy)
        self.assertFalse(self.host._speech_running)

    def test_conversation_requires_all_four_and_preserves_retry(self):
        self.items[:] = [dict(self.items[0], word_id=str(n), order=n, session=4) for n in range(1,5)]
        payload = {'device':'serial', 'items':[{'word_id':str(n), 'asset_id':self.asset['id'],
                                             'approved':True,'expected_grades':['Perfect']} for n in range(1,5)]}
        self.assertFalse(self.service.preview({**payload, 'items':payload['items'][:3]})['ready'])
        p = self.service.preview(payload)
        self.assertTrue(p['ready'])
        observation = {'status':'completed','signature_verified':True,'cleanup_ok':True,'full_input':True,
                       'target_verified':True,'app_grade':'Perfect','files':['grade.png']}
        self.adapter.run_sequence.side_effect = [[dict(observation, app_grade='Bad') for _ in range(4)],
                                                [dict(observation) for _ in range(4)]]
        self.nav.restart_session.return_value = {'ready':True,'actions':[]}
        with patch('speech_service.threading.Thread'):
            run = self.service.start({'preview_id':p['id'],'approved':True})
        self.service.execute(run)
        self.assertEqual(self.adapter.run_sequence.call_count, 2)
        self.adapter.run_attempt.assert_not_called()
        self.assertEqual([r['status'] for r in run['results']], ['unverified']*4)
        self.assertTrue(all(len(r['attempts'])==2 for r in run['results']))

    def test_expected_rejection_pass_does_not_retry(self):
        actual = {'status':'completed','signature_verified':True,'cleanup_ok':True,'full_input':True,
                  'target_verified':True,'app_grade':'Fail','files':['grade.png']}
        self.assertFalse(self.service.retry_needed(actual, policy({'approved':True,'expect_reject':True})))

    def test_four_words_share_one_connection_and_keep_per_word_evidence(self):
        self.items[:] = [dict(self.items[0], word_id=str(n), order=n, session=1, kind='word', target='word%d;' % n)
                        for n in range(1, 5)]
        payload = {'device': 'serial', 'items': [dict(word_id=str(n), asset_id=self.asset['id'],
                     approved=True, expected_outcomes=['word_correct']) for n in range(1, 5)]}
        p = self.service.preview(payload)
        self.assertTrue(p['ready'])
        self.adapter.run_sequence.return_value = [dict(status='completed', signature_verified=True,
            cleanup_ok=True, full_input=True, target_verified=True, app_outcome='word_correct', app_grade=None,
            outcome_evidence={'app_outcome':'word_correct', 'source':'installed_apk_word_icon_pixels', 'screen_sha256':'a'*64}, files=['capture.png'])
            for _ in range(4)]
        with patch('speech_service.threading.Thread'):
            run = self.service.start({'preview_id':p['id'], 'approved':True})
        self.service.execute(run)
        self.adapter.run_sequence.assert_called_once()
        self.adapter.run_attempt.assert_not_called()
        self.assertEqual([r['status'] for r in run['results']], ['passed'] * 4)
        self.assertEqual([t['kind'] for t in self.adapter.run_sequence.call_args.args[1]], ['word'] * 4)
        self.assertEqual(len({r['attempts'][0]['files'][0] for r in run['results']}), 4)

    def test_retry_once_and_cleanup_failure_locks(self):
        p = self.preview()
        a = {'status': 'completed', 'signature_verified': True, 'target_verified': True, 'full_input': True, 'app_grade': 'Bad', 'files': ['screen.png'], 'cleanup_ok': True}
        self.adapter.run_attempt.return_value = a
        with patch('speech_service.threading.Thread'):
            run = self.service.start({'preview_id': p['id'], 'approved': True})
        self.service.execute(run)
        self.assertEqual(self.adapter.run_attempt.call_count, 2)
        self.assertEqual(self.service.current['results'][0]['status'], 'failed')
        self.assertFalse(self.host._speech_running)
        self.adapter.run_attempt.return_value = {**a, 'cleanup_ok': False}
        with patch('speech_service.threading.Thread'):
            run = self.service.start({'preview_id': p['id'], 'approved': True})
        self.service.execute(run)
        self.assertTrue(self.host._speech_running)
        self.assertTrue(self.service.cleanup_required)

    def test_stop_before_device_input_no_pass(self):
        p = self.preview()
        with patch('speech_service.threading.Thread'):
            run = self.service.start({'preview_id': p['id'], 'approved': True})
        self.service.stop_event.set()
        self.service.execute(run)
        self.adapter.run_attempt.assert_not_called()
        self.assertEqual(self.service.current['status'], 'stopped')
        self.assertEqual(self.service.current['results'][0]['status'], 'not_run')

    def test_resume_rechecks_db_and_preserves_original(self):
        p = self.preview()
        with patch('speech_service.threading.Thread'):
            run = self.service.start({'preview_id': p['id'], 'approved': True})
        self.service.stop_event.set()
        self.service.execute(run)
        self.materials.catalog.return_value = {'items': self.items, 'db_sha256': 'changed'}
        with self.assertRaises(ValueError):
            self.service.start({'run_id': run['id'], 'approved': True}, resume=True)
        self.assertEqual(self.service.store.get(run['id'])['status'], 'stopped')


class AccessTests(unittest.TestCase):
    def test_new_routes_keep_origin_token_guards(self):
        import mirror_server as m
        from flask.testing import FlaskClient
        client = FlaskClient(m.app)
        for path, method in [('/api/speech-verification/assets', 'GET'), ('/api/speech-verification/run', 'POST'),
                             ('/api/speech-verification/stop', 'POST')]:
            self.assertEqual(client.open(path, method=method, base_url='http://localhost:5000').status_code, 403)
        headers = {'X-QA-CSRF-Token': m.local_access.token, 'Origin': 'http://elsewhere.test'}
        self.assertEqual(client.post('/api/speech-verification/stop', base_url='http://localhost:5000', headers=headers, json={}).status_code, 403)

    def test_input_and_automation_blocked_during_speech(self):
        import mirror_server as m
        from flask.testing import FlaskClient
        client = FlaskClient(m.app)
        headers = {'X-QA-CSRF-Token': m.local_access.token, 'Origin': 'http://localhost:5000'}
        with patch.object(m, '_speech_running', True), patch.object(m, 'adb') as adb:
            for path in ('/api/input', '/api/run', '/api/random/run', '/api/tc-workflow/prepare'):
                self.assertEqual(client.post(path, base_url='http://localhost:5000', headers=headers, json={}).status_code, 409)
            with self.assertRaises(RuntimeError):
                m.run_manual_input({'type': 'tap'})
            adb.assert_not_called()

    def test_pcm_upload_requires_multipart_and_token(self):
        import mirror_server as m
        from flask.testing import FlaskClient
        client = FlaskClient(m.app)
        headers = {'X-QA-CSRF-Token': m.local_access.token, 'Origin': 'http://localhost:5000'}
        self.assertEqual(client.post('/api/speech-verification/assets', base_url='http://localhost:5000', headers=headers, json={}).status_code, 415)
        with tempfile.TemporaryDirectory() as d, patch.object(m.speech_verification_service, 'store', Store(d)):
            response = client.post('/api/speech-verification/assets', base_url='http://localhost:5000', headers=headers,
                         data={'file': (io.BytesIO(b'\0\0'*800), 'sample.pcm'), 'sample_rate': '16000', 'channels': '1', 'sample_width': '2'})
            self.assertEqual(response.status_code, 200, response.json)
            self.assertEqual(response.json['samples'], 800)


if __name__ == '__main__':
    unittest.main()
