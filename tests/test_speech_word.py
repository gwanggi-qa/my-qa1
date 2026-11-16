"""Offline word feedback and navigation regressions; no device or network IO."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw

import speech_device as device
import speech_navigation as nav
from speech_verification import policy, verdict, conclude
from speech_word import observe_word, observe_word_png, word_frame


def node(rid, children=(), **attrs):
    e = ET.Element('node', {'package': nav.PACKAGE, 'resource-id': nav.PREFIX + rid,
                           'enabled': 'true', 'clickable': 'false', 'bounds': '[0,0][180,180]',
                           'text': '', 'content-desc': '', **attrs})
    e.extend(children)
    return e


def frame(meaning='meaning', extra=()):
    root = ET.Element('hierarchy')
    root.append(node('root', [
        node('cvStudyCard', [node('layoutWord'), node('txtPinyin', text=meaning),
                            node('llSpeak', [node('ivStudyWordSpeakIcon', bounds='[20,30][58,68]')])]),
        node('rlBottomControlBar', [node('ivCtrlNext', clickable='true')]), *extra]))
    return ET.tostring(root, encoding='unicode')


def png(image):
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    return stream.getvalue()


def templates():
    result = {}
    for state, color in [('correct', '#14c858'), ('wrong', '#ef3434'), ('ready', '#3647ef'), ('speak', '#eac713')]:
        im = Image.new('RGBA', (38, 38), color)
        ImageDraw.Draw(im).line((4, 20, 16, 30, 32, 6), fill='white', width=4)
        result['word_' + state] = png(im)
    return result


def capture(data):
    im = Image.new('RGB', (180, 180), 'white')
    im.paste(Image.open(io.BytesIO(data)), (20, 30))
    return png(im)


def attempt(outcome='word_correct'):
    return dict(status='completed', cleanup_ok=True, signature_verified=True, target_verified=True,
                full_input=True, app_grade=None, app_outcome=outcome, files=['capture.png'],
                runtime=dict(text='word;', slot=0),
                outcome_evidence=dict(app_outcome=outcome, source='installed_apk_word_icon_pixels',
                                      screen_sha256=nav.Screen(frame()).sha256))


ITEM = dict(subject=nav.SUBJECT, level=2, week=1, stage=1, session=1, word_id='20001',
            order=1, kind='word', text='word', target='word;', meaning='meaning')


class WordPixels(unittest.TestCase):
    def test_word_sequence_can_end_at_exact_quiz_prompt_without_starting_quiz(self):
        popup = node('flStagePopupOverlay', [node('tvStagePopupTitle', text='\ub2e8\uc5b4 \ud034\uc988'),
            node('tvStagePopupDesc', text='\ubc30\uc6b4 \ub2e8\uc5b4\ub97c \ud034\uc988\ub85c \ubcf5\uc2b5\ud574 \ubcf4\uc138\uc694!'),
            node('btnStagePopupStart', text='\uc2dc\uc791')])
        raw = frame(extra=[popup])
        self.assertTrue(device.DeviceAdapter._sequence_terminal(ET.fromstring(raw), word=True))
        self.assertFalse(device.DeviceAdapter._sequence_terminal(ET.fromstring(raw.replace('단어 퀴즈', '다른 학습')), word=True))
        self.assertFalse(device.DeviceAdapter._sequence_terminal(ET.fromstring(frame(extra=[popup, node('layoutMessageBox')])), word=True))

    def test_word_cover_has_its_own_title_and_guide(self):
        popup = node('flTodayLessonCoverOverlay', [
            node('tvTodayLessonTitle', text='\uc624\ub298\uc758 \ud559\uc2b5 \ub2e8\uc5b4'),
            node('gvTodayLessonGuide', [node('guide', text='\uc624\ub298 \ud559\uc2b5\ud560 \ub2e8\uc5b4\ub97c \ud655\uc778\ud558\uc138\uc694.')]),
            node('btnTodayLessonNext', text='\ub2e4\uc74c', clickable='true')])
        raw = frame(extra=[popup])
        selected = nav.next_intro_selector(raw, ITEM)
        self.assertEqual(selected['intro_phase'], 'word_today_cover')
        self.assertEqual(device._semantic_control(raw.encode(), selected, {'session': 1})['resource_id'],
                         nav.PREFIX + 'btnTodayLessonNext')
        for bad in [raw.replace('오늘의 학습 단어', '오늘의 학습'),
                    raw.replace('오늘 학습할 단어를 확인하세요.', '다른 안내'),
                    frame(extra=[popup, node('flStagePopupOverlay')])]:
            with self.assertRaises(nav.NavigationBlocked):
                nav.next_intro_selector(bad, ITEM)

    def test_word_intro_exact_popup_uses_fresh_semantic_control(self):
        popup = node('flStagePopupOverlay', [
            node('tvStagePopupTitle', text='\ub2e8\uc5b4 \ub5a0\uc62c\ub9ac\uae30'),
            node('tvStagePopupDesc', text='\ub73b\uacfc \uadf8\ub9bc\uc744 \ubcf4\uace0 \ub2e8\uc5b4\ub97c \ub5a0\uc62c\ub824 \ubcf4\uc138\uc694.'),
            node('btnStagePopupStart', text='\uc2dc\uc791', clickable='true')])
        raw = frame(extra=[popup])
        selected = nav.next_intro_selector(raw, ITEM)
        self.assertEqual(selected['intro_phase'], 'word_recall_start')
        self.assertFalse(selected['target_verified'])
        self.assertEqual(device._semantic_control(raw.encode(), selected, {'session': 1})['resource_id'],
                         nav.PREFIX + 'btnStagePopupStart')
        for bad in [raw.replace('단어 떠올리기', '문장 떠올리기'), frame(extra=[popup, node('layoutMessageBox')])]:
            with self.assertRaises(nav.NavigationBlocked):
                nav.next_intro_selector(bad, ITEM)
        for change in [dict(order=2), dict(session=2), dict(build=12), dict(target='missing-semicolon')]:
            with self.assertRaises(nav.NavigationBlocked):
                nav.next_intro_selector(raw, {**ITEM, **change})
        self.assertIsNone(nav.next_intro_selector(frame(), ITEM))

    def test_correct_wrong_are_outcomes_not_grades(self):
        refs = templates()
        for outcome in ('word_correct', 'word_wrong'):
            observed = observe_word(frame(), capture(refs[outcome]), refs)
            self.assertEqual(observed['app_outcome'], outcome)
            self.assertNotIn('app_grade', observed)

    def test_ready_speaking_blank_and_colored_square_are_not_success(self):
        refs = templates()
        for data in [refs['word_ready'], refs['word_speak'], png(Image.new('RGB', (38, 38), 'white')),
                     png(Image.new('RGB', (38, 38), '#14c858'))]:
            self.assertIsNone(observe_word(frame(), capture(data), refs)['app_outcome'])

    def test_overlay_duplicate_and_invalid_bounds_block(self):
        refs = templates()
        for raw in [frame(extra=[node('layoutMessageBox')]),
                    frame(extra=[node('ivStudyWordSpeakIcon')]),
                    frame().replace('[20,30][58,68]', '[20,30][20,30]')]:
            with self.assertRaises(nav.NavigationBlocked):
                observe_word(raw, capture(refs['word_correct']), refs)

    def test_unknown_scale_is_inconclusive(self):
        refs = templates()
        self.assertIsNone(observe_word(frame().replace('[20,30][58,68]', '[20,30][59,69]'),
                                      capture(refs['word_correct']), refs)['app_outcome'])

    def test_checkpoint_required(self):
        refs = templates()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            data = capture(refs['word_correct'])
            (folder / 'capture.png').write_bytes(data)
            (folder / 'result.xml').write_text(frame(), encoding='utf-8')
            adapter = device.DeviceAdapter(SimpleNamespace(), tmp)
            report = dict(capture_valid=True, png_capture_valid=True, png_sha256=hashlib.sha256(data).hexdigest())
            self.assertEqual(adapter._observe_word(None, folder, report, refs)['app_outcome'], 'word_correct')
            # A late XML must not invalidate the separately target-bound PNG.
            late = adapter._observe_word(None, folder, dict(report, capture_valid=False), refs)
            self.assertEqual(late['app_outcome'], 'word_correct')
            self.assertFalse(late['xml_used'])
            (folder / 'result.xml').write_text(frame(extra=[node('flStagePopupOverlay')]), encoding='utf-8')
            late = adapter._observe_word(None, folder, report, refs)
            self.assertEqual(late['app_outcome'], 'word_correct')
            self.assertFalse(late['xml_used'])
            for field in ('png_capture_valid', 'png_sha256'):
                bad = {**report, field: False}
                self.assertIsNone(adapter._observe_word(None, folder, bad, refs)['app_outcome'])

    def test_png_only_requires_unique_exact_icon_and_never_uses_late_xml(self):
        refs = templates()
        for state in refs:
            result = observe_word_png(capture(refs[state]), refs)
            self.assertEqual(result['app_outcome'], state if state in {'word_correct', 'word_wrong'} else None)
        image = Image.new('RGB', (180,180), 'white')
        self.assertIsNone(observe_word_png(png(image), refs)['app_outcome'])
        image.paste(Image.open(io.BytesIO(refs['word_correct'])), (10,10))
        image.paste(Image.open(io.BytesIO(refs['word_correct'])), (70,70))
        self.assertIsNone(observe_word_png(png(image), refs)['app_outcome'])

    def test_unknown_apk_never_contacts_device(self):
        adapter = device.DeviceAdapter(SimpleNamespace(), '.')
        with patch.object(adapter, '_adb', side_effect=AssertionError('device IO')):
            with self.assertRaises(device._Blocked):
                adapter._word_templates('device', 'a' * 64, 0, None)


class WordPolicy(unittest.TestCase):
    def test_explicit_outcome_policy(self):
        expected = policy(dict(approved=True, expected_outcomes=['word_correct']))
        self.assertEqual(verdict(attempt(), expected)[0], 'passed')
        self.assertEqual(verdict(attempt('word_wrong'), expected)[0], 'failed')

    def test_old_perfect_policy_is_not_migrated(self):
        expected = policy(dict(approved=True, expected_grades=['Perfect']))
        self.assertEqual(verdict(attempt(), expected)[0], 'unverified')
        self.assertEqual(expected['expected_grades'], ['Perfect'])

    def test_no_policy_or_no_evidence_never_passes(self):
        self.assertEqual(verdict(attempt(), policy({}))[0], 'unverified')
        expected = policy(dict(approved=True, expected_outcomes=['word_correct']))
        for field, value in [('cleanup_ok', False), ('full_input', False), ('target_verified', False),
                             ('signature_verified', False), ('files', []), ('outcome_evidence', {}),
                             ('app_grade', 'Perfect')]:
            self.assertEqual(verdict({**attempt(), field: value}, expected)[0], 'blocked')

    def test_mixed_invalid_policy_rejected(self):
        for value in [dict(expected_grades=['Perfect']), dict(expect_reject=True)]:
            with self.assertRaises(ValueError):
                policy(dict(expected_outcomes=['word_correct'], **value))
        with self.assertRaises(ValueError):
            policy(dict(expected_outcomes=['Perfect']))

    def test_retry_outcome_variation_is_unverified(self):
        expected = policy(dict(approved=True, expected_outcomes=['word_correct']))
        self.assertEqual(conclude([attempt('word_wrong'), attempt()], expected)[0], 'unverified')


class Host:
    ADB_SERIAL = 'offline-word'

    def __init__(self, screens):
        self.screens = list(screens)
        self.inputs = []
        self._adb_input_lock = threading.Lock()

    def dump_ui_xml(self):
        return self.screens[0]

    def adb(self, *args, **kwargs):
        if args[:3] == ('shell', 'dumpsys', 'package'):
            return b'versionCode=11', b'', 0
        assert self._adb_input_lock.locked()
        self.inputs.append(args)
        if len(self.screens) > 1:
            self.screens.pop(0)
        return b'', b'', 0


class WordNavigation(unittest.TestCase):
    def navigator(self, raw=None):
        host = Host([raw or frame()])
        navigator = nav.Navigator(host, host.ADB_SERIAL, threading.Event())
        navigator._scope = navigator._identity(ITEM)
        navigator._unit = navigator._scope[:4]
        navigator._item = ITEM.copy()
        return navigator, host

    def test_verified_result_hands_next_to_armed_adapter_without_tap(self):
        navigator, host = self.navigator()
        navigator.record_attempt(ITEM, attempt())
        result = navigator.prepare(dict(ITEM, order=2, word_id='20002', target='next;'))
        self.assertTrue(result['ready'], result)
        self.assertFalse(result['target_verified'])
        self.assertTrue(result['requires_armed_start'])
        self.assertEqual(result['start_selector']['resource_id'], nav.PREFIX + 'ivCtrlNext')
        self.assertEqual(host.inputs, [])
        device._semantic_control(frame(), result['start_selector'], result['start_selector']['scope'])

    def test_missing_changed_or_unclean_observation_never_taps(self):
        for actual in [None, {**attempt(), 'cleanup_ok': False}, attempt()]:
            navigator, host = self.navigator(frame('changed') if actual == attempt() else None)
            if actual:
                navigator.record_attempt(ITEM, actual)
            result = navigator.prepare(dict(ITEM, order=2, word_id='20002'))
            self.assertFalse(result['ready'])
            self.assertEqual(host.inputs, [])

    def test_transient_xml_change_keeps_current_feedback_guard(self):
        navigator, host = self.navigator(frame().replace('<hierarchy>', '<hierarchy rotation="1">'))
        navigator.record_attempt(ITEM, attempt())
        result = navigator.prepare(dict(ITEM, order=2, word_id='20002', target='next;'))
        self.assertTrue(result['ready'], result)
        self.assertNotEqual(result['previous_result_screen_sha256'], result['start_selector']['screen_sha256'])
        self.assertEqual(result['start_selector']['word_transition']['app_outcome'], 'word_correct')
        self.assertEqual(host.inputs, [])

    def test_input_rechecks_pixels_not_only_xml(self):
        navigator, _ = self.navigator()
        navigator.record_attempt(ITEM, attempt())
        route = navigator.prepare(dict(ITEM, order=2, word_id='20002', target='next;'))
        refs = templates()
        for state in ('word_correct', 'word_wrong', 'word_ready'):
            with tempfile.TemporaryDirectory() as tmp:
                host = Host([frame()])
                adapter = device.DeviceAdapter(host, tmp)
                calls = []

                def adb(serial, args, *unused):
                    self.assertTrue(host._adb_input_lock.locked())
                    calls.append(args)
                    if args[:2] == ['exec-out', 'uiautomator']:
                        return frame().encode()
                    if args[:2] == ['exec-out', 'screencap']:
                        return capture(refs[state])
                    return b''

                with patch.object(adapter, '_adb', side_effect=adb):
                    if state == 'word_correct':
                        adapter._start(host.ADB_SERIAL, route['start_selector'], {}, None, Path(tmp),
                                       time.monotonic() + 10, None, word_templates=refs)
                    else:
                        with self.assertRaisesRegex(device._Blocked, 'changed_before_input'):
                            adapter._start(host.ADB_SERIAL, route['start_selector'], {}, None, Path(tmp),
                                           time.monotonic() + 10, None, word_templates=refs)
                taps = [args for args in calls if args[:3] == ['shell', 'input', 'tap']]
                self.assertEqual(len(taps), 1 if state == 'word_correct' else 0)

    def test_cancelled_word_navigation_never_taps(self):
        navigator, host = self.navigator()
        navigator.record_attempt(ITEM, attempt())
        navigator.stop.set()
        result = navigator.prepare(dict(ITEM, order=2, word_id='20002'))
        self.assertTrue(result['stopped'])
        self.assertEqual(host.inputs, [])

    def test_return_to_menu_from_word_requires_observed_exit_dialog(self):
        dialog = '<hierarchy>' + ET.tostring(node('layoutMessageBox', [
            node('popup_content', text=nav.EXIT_MESSAGE), node('btn_sel2', text=nav.CANCEL),
            node('btn_sel1', text=nav.CONFIRM, clickable='true')]), encoding='unicode') + '</hierarchy>'
        menu = '<hierarchy>' + ET.tostring(node('menu', [node('tvLevelTitle', text='Level 2'),
                        node('tvEpTitleNum', text='Week 1')]), encoding='unicode') + '</hierarchy>'
        host = Host([frame(), dialog, menu])
        navigator = nav.Navigator(host, host.ADB_SERIAL, threading.Event())
        navigator._begin()
        self.assertEqual(navigator._return_to_menu(navigator._read()).level(), 2)
        self.assertEqual([args[2] for args in host.inputs], ['keyevent', 'tap'])

    def test_stranded_word_intro_exits_without_starting_recognition(self):
        popup = node('flStagePopupOverlay', [
            node('tvStagePopupTitle', text='\ub2e8\uc5b4 \ub5a0\uc62c\ub9ac\uae30'),
            node('tvStagePopupDesc', text='\ub73b\uacfc \uadf8\ub9bc\uc744 \ubcf4\uace0 \ub2e8\uc5b4\ub97c \ub5a0\uc62c\ub824 \ubcf4\uc138\uc694.'),
            node('btnStagePopupStart', text='\uc2dc\uc791', clickable='true')])
        raw = frame(extra=[popup])
        dialog = '<hierarchy>' + ET.tostring(node('layoutMessageBox', [
            node('popup_content', text=nav.EXIT_MESSAGE), node('btn_sel2', text=nav.CANCEL),
            node('btn_sel1', text=nav.CONFIRM, clickable='true')]), encoding='unicode') + '</hierarchy>'
        menu = '<hierarchy>' + ET.tostring(node('menu', [node('tvLevelTitle', text='Level 2'),
                        node('tvEpTitleNum', text='Week 1')]), encoding='unicode') + '</hierarchy>'
        for first, valid in [(raw, True), (raw.replace('단어 떠올리기', '알 수 없는 안내'), False)]:
            host = Host([first, dialog, menu])
            navigator = nav.Navigator(host, host.ADB_SERIAL, threading.Event())
            navigator._begin()
            if valid:
                self.assertEqual(navigator._return_to_menu(navigator._read()).level(), 2)
                self.assertEqual([args[2] for args in host.inputs], ['keyevent', 'tap'])
                self.assertEqual(navigator._actions[0]['action'], 'back')
            else:
                with self.assertRaises(nav.NavigationBlocked):
                    navigator._return_to_menu(navigator._read())
                self.assertEqual(host.inputs, [])

    def test_no_progress_exit_is_bounded(self):
        navigator, host = self.navigator()
        navigator._begin()
        with self.assertRaisesRegex(nav.NavigationBlocked, 'no progress'):
            navigator._return_to_menu(navigator._read())
        self.assertEqual(len(host.inputs), 1)


if __name__ == '__main__':
    unittest.main()
