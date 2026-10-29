import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from flask import Flask
import app_analysis as a
from local_access import LocalAccessPolicy, HEADER


def apk(entries=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, content in (entries or {'AndroidManifest.xml': b'fixture',
                                         'classes.dex': b'fixture'}).items():
            archive.writestr(name, content)
    output.seek(0)
    return output


def payload(extra='', manifest=None):
    xml = manifest or ('<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
                      'package="qa.example"><application><activity android:name=".Main" '
                      'android:exported="false"/><meta-data android:name="api_key" '
                      'android:value="super-secret"/></application></manifest>')
    return {'normalized_result': {'package_name': 'qa.example', 'version_name': '1.0',
        'manifest': {'status': 'complete', 'text': xml}, 'permissions': ['android.permission.INTERNET'],
        'class_count': 2, 'resource_count': 3, 'raw': extra,
        'engine': {'name': 'jadx-headless-mcp', 'version': '0.7.1', 'artifact_sha256': a.JADX_HASH}}}


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = a.AnalysisStore(self.root)

    def test_upload_hash_dedup_and_safe_filename(self):
        build, duplicate = self.store.register(apk(), '../../a.apk')
        self.assertFalse(duplicate)
        self.assertEqual(build['filename'], 'a.apk')
        self.assertEqual(a.digest(self.store.path(build['sha256'])), build['sha256'])
        again, duplicate = self.store.register(apk(), 'b.apk')
        self.assertTrue(duplicate)
        self.assertEqual(again['sha256'], build['sha256'])
        self.assertEqual(len(self.store.list()), 1)

    def test_invalid_files_leave_no_uploads(self):
        for stream, name in [(io.BytesIO(b'not zip'), 'a.apk'), (apk(), 'a.aab'),
                             (apk({'other': b'no manifest'}), 'a.apk'),
                             (apk({'AndroidManifest.xml': b'x', '../outside': b'x'}), 'a.apk'),
                             (apk({'AndroidManifest.xml': b'x', 'child.apk': b'x'}), 'a.apk')]:
            with self.subTest(name=name):
                with self.assertRaises(a.AnalysisError):
                    self.store.register(stream, name)
        self.assertEqual(self.store.list(), [])
        self.assertEqual(list(self.root.glob('*.upload')), [])

    def test_size_limit(self):
        with patch.object(a, 'MAX_APK', 5):
            with self.assertRaises(a.AnalysisError) as caught:
                self.store.register(apk(), 'a.apk')
        self.assertEqual(caught.exception.status, 413)

    def test_storage_limit(self):
        with patch.object(a, 'MAX_STORAGE', 1):
            with self.assertRaises(a.AnalysisError) as caught:
                self.store.register(apk(), 'a.apk')
        self.assertEqual(caught.exception.status, 413)
        self.assertEqual(self.store.list(), [])

    def test_duplicate_zip_members_rejected(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('AndroidManifest.xml', b'x')
            archive.writestr('androidmanifest.xml', b'x')
        stream.seek(0)
        with self.assertRaises(a.AnalysisError):
            self.store.register(stream, 'duplicate.apk')

    def test_newer_schema_is_not_overwritten(self):
        with self.store.connect() as db:
            db.execute('PRAGMA user_version=9')
        with self.assertRaises(a.AnalysisError):
            a.AnalysisStore(self.root).list()

    def test_output_allowlist(self):
        result = a.sanitize_result(payload('raw-sensitive'), 'a'*64)
        text = json.dumps(result)
        self.assertNotIn('super-secret', text)
        self.assertNotIn('api_key', text)
        self.assertNotIn('raw-sensitive', text)
        self.assertEqual(result['components'][0]['name'], '.Main')
        self.assertEqual(result['signature_verification'], 'not_performed')
        self.assertFalse(result['ai_used'])
        self.assertEqual(result['evidence_kind'], 'static_declaration')

    def test_split_entity_invalid_results_rejected(self):
        for value in [None, {}, {'normalized_result': []},
                      payload(manifest='<manifest package="qa.example" split="config.en"/>'),
                      payload(manifest='<!DOCTYPE a [<!ENTITY x "x">]><manifest/>'),
                      payload(manifest='<manifest package="other.app"/>')]:
            with self.subTest(value=type(value)):
                with self.assertRaises(a.AnalysisError):
                    a.sanitize_result(value, 'a'*64)

    def test_bad_id(self):
        for key in ['../x', 'A'*64, 'a'*63]:
            with self.assertRaises(a.AnalysisError):
                self.store.get(key)

    def test_job_success_and_failure_are_not_test_pass(self):
        for error in [False, True]:
            def runner(path, sha, cancel):
                if error:
                    raise ValueError('secret should not leak')
                return a.sanitize_result(payload(), sha)
            service = a.AnalysisService(self.root / str(error), runner)
            build, _ = service.store.register(apk(), 'a.apk')
            with patch.object(a, 'environment', return_value={'ready': True}):
                service.start(build['sha256'])
            service.worker.join(5)
            self.assertFalse(service.worker.is_alive())
            job = service.store.get(build['sha256'])['job']
            self.assertEqual(job['status'], 'failed' if error else 'completed')
            self.assertNotIn('secret', json.dumps(job))
            self.assertIsNone(service.active_id)

    def test_stop_busy_and_recovery(self):
        entered = threading.Event()
        def runner(path, sha, cancel):
            entered.set()
            cancel.wait(5)
            return a.sanitize_result(payload(), sha)
        service = a.AnalysisService(self.root, runner)
        build, _ = service.store.register(apk(), 'a.apk')
        with patch.object(a, 'environment', return_value={'ready': True}):
            first = service.start(build['sha256'])
            self.assertTrue(entered.wait(2))
            with self.assertRaises(a.AnalysisError) as caught:
                service.start(build['sha256'])
            self.assertEqual(caught.exception.status, 409)
            service.stop(first['job']['id'])
        service.worker.join(5)
        self.assertEqual(service.store.get(build['sha256'])['job']['status'], 'stopped')

    def test_missing_tools_does_not_create_job(self):
        service = a.AnalysisService(self.root)
        build, _ = service.store.register(apk(), 'a.apk')
        with patch.object(a, 'environment', return_value={'ready': False}):
            with self.assertRaises(a.AnalysisError) as caught:
                service.start(build['sha256'])
        self.assertEqual(caught.exception.status, 503)
        self.assertIsNone(service.store.get(build['sha256'])['job'])

    def test_restart_marks_abandoned_job(self):
        build, _ = self.store.register(apk(), 'a.apk')
        with self.store.connect() as db:
            db.execute('INSERT INTO jobs(id,build_sha256,status,started_at) VALUES (?,?,?,?)',
                       ('a'*32, build['sha256'], 'running', a.now()))
        restarted = a.AnalysisStore(self.root)
        self.assertEqual(restarted.get(build['sha256'])['job']['status'], 'interrupted')


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Flask('analysis-test')
        self.policy = LocalAccessPolicy('127.0.0.1', 5000, ['localhost', '127.0.0.1'])
        self.policy.install(self.app)
        a.register(self.app, self.policy, self.temp.name)
        self.client = self.app.test_client()
        self.headers = {HEADER: self.policy.token, 'Origin': 'http://localhost:5000'}

    def call(self, path, **kwargs):
        return self.client.open(path, base_url='http://localhost:5000', **kwargs)

    def test_page_and_upload(self):
        response = self.call('/app-analysis')
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.policy.token, response.get_data(as_text=True))
        response = self.call('/api/app-analysis/builds', method='POST', headers=self.headers,
                             data={'file': (apk(), 'sample.apk')})
        self.assertEqual(response.status_code, 201)
        sha = response.json['build']['sha256']
        detail = self.call('/api/app-analysis/builds/'+sha, headers=self.headers)
        self.assertEqual(detail.json['build']['filename'], 'sample.apk')

    def test_request_guards(self):
        for headers in [{}, {HEADER: self.policy.token, 'Origin': 'http://evil.example'},
                        {**self.headers, 'X-Forwarded-For': '127.0.0.1'}]:
            r = self.call('/api/app-analysis/builds', method='POST', headers=headers,
                          data={'file': (apk(), 'sample.apk')})
            self.assertEqual(r.status_code, 403)
        r = self.call('/api/app-analysis/builds', method='POST', headers=self.headers, json={})
        self.assertEqual(r.status_code, 415)

    def test_delete_build_and_register_fresh(self):
        store = self.app.extensions['app_analysis'].store
        build, _ = store.register(apk(), 'delete-fixture.apk')
        sha = build['sha256']
        evidence = Path(self.temp.name) / 'execution-evidence.txt'
        evidence.write_text('preserved')
        with store.connect() as db:
            db.execute('INSERT INTO jobs(id,build_sha256,status,started_at) VALUES (?,?,?,?)', ('fixture',sha,'running',a.now()))
        url = '/api/app-analysis/builds/'+sha
        self.assertEqual(self.call(url, method='DELETE', headers=self.headers,json={'confirm':True}).status_code,409)
        with store.connect() as db:
            db.execute("UPDATE jobs SET status='completed' WHERE id='fixture'")
        self.assertEqual(self.call(url, method='DELETE', headers=self.headers,json={'confirm':True}).status_code,200)
        self.assertFalse(store.path(sha).exists())
        self.assertEqual(evidence.read_text(),'preserved')
        fresh, duplicate = store.register(apk(), 'fresh.apk')
        self.assertFalse(duplicate)
        self.assertIsNone(fresh['job'])

    def test_arbitrary_parameters_rejected(self):
        r = self.call('/api/app-analysis/builds/'+'a'*64+'/analyze', method='POST',
                      headers=self.headers, json={'path':'C:/secret'})
        self.assertEqual(r.status_code, 400)
        r = self.call('/api/app-analysis/jobs/'+'a'*32+'/stop', method='POST',
                      headers=self.headers, json={})
        self.assertEqual(r.status_code, 404)


class RunnerTests(unittest.TestCase):
    def invoke(self, process, clock=None):
        paths = {'rea': Path('rea.mjs'), 'jadx': Path('jadx.jar'),
                 'java_home': Path('jdk'), 'node': 'node'}
        with patch.object(a, 'tool_paths', return_value=paths), \
             patch.object(a, 'environment', return_value={'ready': True}), \
             patch.object(a, 'digest', side_effect=lambda p: a.JADX_HASH if p == paths['jadx'] else 'a'*64), \
             patch.object(a.subprocess, 'Popen', return_value=process) as popen, \
             patch.object(a, 'stop_process') as stop:
            if clock:
                with patch.object(a.time, 'monotonic', side_effect=clock):
                    result = a.rea_analyze(Path('a.apk'), 'a'*64, threading.Event())
            else:
                result = a.rea_analyze(Path('a.apk'), 'a'*64, threading.Event())
            self.assertEqual(popen.call_args.args[0][2], 'inspect-android-package')
            self.assertNotIn('OPENAI_API_KEY', popen.call_args.kwargs['env'])
            stop.assert_called_once_with(process)
            return result

    @staticmethod
    def process(data, returncode=0):
        class Fake:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def poll(self):
                return self.returncode
        p = Fake()
        p.returncode, p.pid = returncode, 123
        p.stdout, p.stderr = io.BytesIO(data), io.BytesIO(b'private stderr')
        return p

    def test_success_no_key_or_raw_output_leak(self):
        with patch.dict(a.os.environ, {'OPENAI_API_KEY': 'do-not-forward'}):
            result = self.invoke(self.process(json.dumps(payload()).encode()))
        self.assertNotIn('super-secret', json.dumps(result))

    def test_invalid_json_and_nonzero_are_errors(self):
        for p in [self.process(b'not json'), self.process(b'secret failure', 1)]:
            with self.assertRaises(a.AnalysisError) as caught:
                self.invoke(p)
            self.assertNotIn('secret failure', str(caught.exception))

    def test_output_limit(self):
        with patch.object(a, 'MAX_OUTPUT', 8):
            with self.assertRaises(a.AnalysisError):
                self.invoke(self.process(b'x' * 100))

    def test_timeout(self):
        with self.assertRaises(a.AnalysisError) as caught:
            self.invoke(self.process(b'', None), clock=[0, 181])
        self.assertEqual(caught.exception.status, 504)

    def test_cancel_before_launch(self):
        cancel = threading.Event()
        cancel.set()
        with patch.object(a.subprocess, 'Popen') as popen:
            with self.assertRaises(a.AnalysisError):
                a.rea_analyze(Path('a.apk'), 'a'*64, cancel)
            popen.assert_not_called()


if __name__ == '__main__':
    unittest.main()
