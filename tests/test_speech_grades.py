"""Offline badge matching tests using synthetic PNGs and in-memory APK ZIPs."""

import hashlib
import io
import json
from pathlib import Path
import random
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
from PIL import Image

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import speech_device as speech


GRADES = ('Perfect', 'Excellent', 'Good', 'Bad', 'Fail')
SIZE = (32, 24)
LEFT, RIGHT = 24, 160


class OfflineCase(unittest.TestCase):
    def setUp(self):
        for target in ('subprocess.Popen', 'subprocess.run', 'socket.socket',
                       'socket.create_connection', 'socket.getaddrinfo',
                       'speech_device._command', 'speech_device.DeviceAdapter._adb'):
            guard = patch(target, side_effect=AssertionError('External operation forbidden: ' + target))
            guard.start()
            self.addCleanup(guard.stop)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.templates = {}
        for seed, grade in enumerate(GRADES):
            rng = random.Random(seed)
            badge = Image.new('RGBA', SIZE)
            badge.putdata([tuple(rng.randrange(32, 241) for _ in range(3)) + (255,)
                           for _ in range(SIZE[0] * SIZE[1])])
            path = self.root / (grade + '.png')
            badge.save(path)
            self.templates[grade] = path

    def capture(self, placements=()):
        image = Image.new('RGB', (256, 256), (12, 14, 18))
        for grade, x, y in placements:
            with Image.open(self.templates[grade]) as badge:
                image.paste(badge, (x, y), badge)
        path = self.root / 'capture.png'
        image.save(path)
        return path

    @staticmethod
    def box(x, y=120):
        return [x, y, x + SIZE[0], y + SIZE[1]]

    def assert_blocked(self, result, reason):
        self.assertIsNone(result['grade'], result)
        self.assertEqual(result['reason'], reason)
        self.assertNotIn('source', result)


class TemplateGradeTests(OfflineCase):
    def test_exact_pixels_match_each_grade_without_numeric_score(self):
        for grade in GRADES:
            with self.subTest(grade=grade):
                result = speech._template_grade(self.capture([(grade, LEFT, 120)]),
                                               self.templates, self.box(LEFT), None, 0)
                self.assertEqual(result['grade'], grade)
                self.assertIsNone(result['reason'])
                self.assertLess(result['distance'], .04)
                self.assertEqual(result['roi'], self.box(LEFT))
                self.assertEqual(result['source'], 'installed_apk_badge_pixels')
                self.assertIsNone(result['app_score'])
                self.assertEqual(result['app_score_reason'], 'ui_numeric_score_not_observed')

    def test_latest_alternating_column_selects_current_grade(self):
        for current, previous in ((RIGHT, LEFT), (LEFT, RIGHT)):
            with self.subTest(current=current):
                path = self.capture([('Bad', previous, 32), ('Excellent', current, 120)])
                result = speech._template_grade(path, self.templates, self.box(current),
                                               self.box(previous, 32), 1)
                self.assertEqual(result['grade'], 'Excellent')
                self.assertEqual(len(result['matches']), 2)
                self.assertEqual(result['roi'], self.box(current))

    def test_stale_prior_or_latest_wrong_column_is_rejected(self):
        for placements in ([('Perfect', LEFT, 120)],
                           [('Good', RIGHT, 32), ('Perfect', LEFT, 120)]):
            with self.subTest(placements=placements):
                result = speech._template_grade(self.capture(placements), self.templates,
                                               self.box(RIGHT), self.box(LEFT, 32), 1)
                self.assert_blocked(result, 'grade_template_current_turn_unverified')

    def test_same_column_or_missing_previous_turn_is_ambiguous(self):
        path = self.capture([('Bad', LEFT, 32), ('Perfect', LEFT, 120)])
        for previous in (None, self.box(LEFT, 32), self.box(LEFT + 10, 32)):
            with self.subTest(previous=previous):
                result = speech._template_grade(path, self.templates, self.box(LEFT), previous, 1)
                self.assert_blocked(result, 'grade_template_turn_not_distinguishable')

    def test_blank_and_unrelated_pixels_have_no_match(self):
        for patterned in (False, True):
            with self.subTest(patterned=patterned):
                path = self.capture()
                if patterned:
                    rng = random.Random(777)
                    image = Image.new('RGB', (256, 256))
                    image.putdata([tuple(rng.randrange(256) for _ in range(3)) for _ in range(256 * 256)])
                    image.save(path)
                result = speech._template_grade(path, self.templates, self.box(LEFT), None, 0)
                self.assert_blocked(result, 'grade_template_count_unverified')

    def test_conflicting_grade_templates_are_rejected(self):
        templates = {'Perfect': self.templates['Perfect'], 'Fail': self.templates['Perfect']}
        result = speech._template_grade(self.capture([('Perfect', LEFT, 120)]),
                                       templates, self.box(LEFT), None, 0)
        self.assert_blocked(result, 'grade_template_ambiguous')

    def test_extra_badges_cannot_be_assigned_to_first_turn(self):
        path = self.capture([('Good', LEFT, 32), ('Perfect', RIGHT, 120)])
        result = speech._template_grade(path, self.templates, self.box(RIGHT), None, 0)
        self.assert_blocked(result, 'grade_template_count_unverified')

    def test_missing_and_invalid_dimensions_are_rejected(self):
        path = self.capture([('Perfect', LEFT, 120)])
        for box in (None, []):
            with self.subTest(box=box):
                self.assert_blocked(speech._template_grade(path, self.templates, box, None, 0),
                                    'grade_template_turn_not_distinguishable')
        for box in ([24, 20, 24, 44], [56, 20, 24, 44], [24, 44, 56, 20],
                    [24, 20, 43, 44], [24, 20, 56, 39], [0, 0, 301, 24], [0, 0, 32, 301]):
            with self.subTest(box=box):
                self.assert_blocked(speech._template_grade(path, self.templates, box, None, 0),
                                    'grade_template_size_unverified')

    def test_distance_at_threshold_is_not_an_exact_match(self):
        path = self.capture([('Perfect', LEFT, 120)])
        response = np.ones((233, 225), dtype=np.float64)
        response[120, LEFT] = .04
        with patch('cv2.matchTemplate', return_value=response):
            result = speech._template_grade(path, {'Perfect': self.templates['Perfect']},
                                           self.box(LEFT), None, 0)
        self.assert_blocked(result, 'grade_template_count_unverified')


class GradeTemplateCacheTests(OfflineCase):
    def setUp(self):
        super().setUp()
        self.adapter = speech.DeviceAdapter(SimpleNamespace(_adb_input_lock=threading.Lock()), self.root)
        self.apk_path = b'package:/data/app/synthetic/base.apk\n'

    def apk(self, overrides=None):
        overrides = overrides or {}
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for grade in GRADES:
                name = 'res/drawable-nodpi-v4/icon_recognition_' + grade.lower() + '.png'
                archive.writestr(name, overrides.get(grade, self.templates[grade].read_bytes()))
        return output.getvalue()

    def load(self, data, digest=None):
        digest = digest or hashlib.sha256(data).hexdigest()
        with patch.object(self.adapter, '_adb', side_effect=[self.apk_path, data]) as adb:
            result = self.adapter._grade_templates('offline', digest, 0, None)
            self.assertEqual(adb.call_count, 2)
            self.assertEqual(adb.call_args_list[0].args[1], ['shell', 'pm', 'path', speech.PACKAGE])
            self.assertEqual(adb.call_args_list[1].args[1], ['exec-out', 'cat', '/data/app/synthetic/base.apk'])
        return result

    def test_verified_zip_and_cache_hit_preserve_exact_png_bytes(self):
        data = self.apk()
        digest = hashlib.sha256(data).hexdigest()
        result = self.load(data)
        self.assertEqual(set(result), set(GRADES))
        for grade, path in result.items():
            self.assertEqual(path.read_bytes(), self.templates[grade].read_bytes())
        with patch.object(self.adapter, '_adb', side_effect=AssertionError('Cache hit accessed device')):
            self.assertEqual(self.adapter._grade_templates('offline', digest, 0, None), result)

    def test_apk_hash_mismatch_never_publishes_manifest(self):
        data = self.apk()
        with self.assertRaisesRegex(speech._Blocked, 'grade_reference_apk_hash_mismatch'):
            self.load(data, '0' * 64)
        self.assertFalse(list(self.root.glob('.speech-grade-templates/*/manifest.json')))

    def test_invalid_apk_hash_does_not_access_device(self):
        for digest in (None, '', 'ABCDEF' * 10, '../invalid', 'g' * 64):
            with self.subTest(digest=digest):
                self.assertIsNone(self.adapter._grade_templates('offline', digest, 0, None))

    def test_zip_limit_uses_uncompressed_member_size(self):
        data = self.apk({'Perfect': b'x' * 524289})
        self.assertLess(len(data), 524288)
        with self.assertRaisesRegex(speech._Blocked, 'grade_reference_image_too_large'):
            self.load(data)
        self.assertFalse(list(self.root.glob('.speech-grade-templates/*/manifest.json')))

    def test_cache_png_or_manifest_tamper_requires_verified_reload(self):
        data = self.apk()
        result = self.load(data)
        for damage in ('png', 'manifest'):
            with self.subTest(damage=damage):
                folder = result['Good'].parent
                if damage == 'png':
                    result['Good'].write_bytes(b'tampered')
                else:
                    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
                    manifest['Good'] = '0' * 64
                    (folder / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
                restored = self.load(data)
                self.assertEqual(restored['Good'].read_bytes(), self.templates['Good'].read_bytes())


if __name__ == '__main__':
    unittest.main()
