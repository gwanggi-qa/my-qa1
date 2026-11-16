import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from flask import Flask

from speech_service import SpeechService, register
from speech_subjects import inventory
from speech_word import APK_SHA256


class InventoryTests(unittest.TestCase):
    def probe(self, packages=('tjp2', 'tjp', 'tcn', 'trip', 'toeic'), build=11, digest=APK_SHA256):
        def read(args):
            if args == ['get-state']:
                return b'device'
            if args[:4] == ['shell', 'pm', 'list', 'packages']:
                return '\n'.join('package:com.weaversmind.' + p for p in packages).encode()
            if args[:3] == ['shell', 'dumpsys', 'package']:
                return ('versionCode=' + str(build)).encode()
            if args[:3] == ['shell', 'pm', 'path']:
                return b'package:/data/app/test/base.apk'
            if args[:2] == ['shell', 'sha256sum']:
                return (digest + '  /data/app/test/base.apk').encode()
            raise AssertionError(args)
        return Mock(side_effect=read)

    def test_only_installed_registered_courses_and_capabilities(self):
        adb = self.probe()
        result = inventory('device-A', adb)
        self.assertEqual(result['device'], 'device-A')
        self.assertEqual(len(result['items']), 5)
        supported = [i for i in result['items'] if i['supported']]
        self.assertEqual([i['id'] for i in supported], ['japanese-premium'])
        self.assertTrue(all(supported[0]['capabilities'].values()))
        self.assertTrue(all(i['support_status'] == 'not_integrated' for i in result['items'][1:]))
        self.assertFalse(any('input' in call.args[0] or 'am' in call.args[0] for call in adb.call_args_list))
        self.assertEqual(inventory('device-B', self.probe(('tjp',)))['items'][0]['id'], 'japanese')
        self.assertEqual(inventory('empty', self.probe(()))['items'], [])

    def test_wrong_build_or_apk_never_supported(self):
        for build, digest in ((12, APK_SHA256), (11, 'a' * 64)):
            item = inventory('a', self.probe(('tjp2',), build, digest))['items'][0]
            self.assertFalse(item['supported'])
            self.assertEqual(item['support_status'], 'unsupported_build')
            self.assertFalse(any(item['capabilities'].values()))

    def test_failure_and_bad_path_fail_closed(self):
        for answer in (b'package:/tmp/file.apk', b'package:/data/app/../base.apk', b'package:/data/app/a/base.apk\npackage:/data/app/b/base.apk'):
            original = self.probe(('tjp2',))
            def read(args):
                return answer if args[:3] == ['shell', 'pm', 'path'] else original(args)
            self.assertEqual(inventory('a', read)['items'][0]['support_status'], 'unavailable')
        with self.assertRaises(ValueError):
            inventory('a', lambda args: b'unauthorized')


class EnvironmentApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.adapter = Mock()
        self.materials = Mock()
        self.service = SpeechService(lambda: Mock(), self.tmp.name, self.adapter, self.materials)
        app = Flask('speech-environment-tests')
        app.testing = True
        with patch('speech_service.SpeechService', return_value=self.service):
            register(app, lambda: Mock(), root=self.tmp.name)
        self.client = app.test_client()

    def test_discovery_is_bound_to_requested_device(self):
        self.adapter.subjects.return_value = {'device': 'A', 'items': []}
        response = self.client.post('/api/speech-verification/subjects', json={'device': 'A'})
        self.assertEqual(response.status_code, 200)
        self.adapter.subjects.assert_called_once_with('A')
        for invalid in (None, '', '../device', ['A']):
            self.assertEqual(self.client.post('/api/speech-verification/subjects', json={'device': invalid}).status_code, 400)

    def test_unsupported_subject_never_reads_or_writes(self):
        for route in ('catalog', 'native', 'recordings/import', 'preview', 'tts', 'run'):
            response = self.client.post('/api/speech-verification/' + route,
                                        json={'device': 'A', 'subject': 'chinese', 'text': 'test'})
            self.assertEqual(response.status_code, 400, (route, response.json))
        self.adapter.inspect.assert_not_called()
        self.materials.catalog.assert_not_called()
        self.materials.generate_tts.assert_not_called()
        self.assertFalse(list(Path(self.tmp.name).rglob('*.json')))

    def test_reset_removes_only_named_preview_and_preserves_files(self):
        first, other = 'a' * 32, 'b' * 32
        self.service.previews = {first: {'ready': True}, other: {'ready': True}}
        self.service.store.save({'id': 'c' * 32, 'status': 'finished', 'results': []})
        evidence = Path(self.tmp.name) / 'retained.png'
        evidence.write_bytes(b'original evidence')
        before = {p: p.read_bytes() for p in Path(self.tmp.name).rglob('*') if p.is_file()}
        response = self.client.post('/api/speech-verification/prepare/reset', json={'preview_id': first})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(self.service.previews), [other])
        self.assertEqual(before, {p: p.read_bytes() for p in Path(self.tmp.name).rglob('*') if p.is_file()})
        self.service.busy = True
        self.assertEqual(self.client.post('/api/speech-verification/prepare/reset', json={'preview_id': other}).status_code, 409)
        self.assertIn(other, self.service.previews)


if __name__ == '__main__':
    unittest.main()
