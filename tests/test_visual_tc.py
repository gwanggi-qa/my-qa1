import copy
import io
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

import ai_tc
import mirror_server as m
import narrative_excel as ne
import visual_tc as v


def step(expected='오른쪽 영역에 LC·RC 막대그래프 2개가 표시된다.(축 0~500)'):
    return {'source': 'narrative_qa', 'action': 'blocked', 'ai_pending': True,
            'case_no': 'VIS-1', 'expected': expected, 'test_item': '현재 화면 확인',
            'test_purpose': 'LC·RC를 각각 산출해 막대로 보여주는가', 'precondition': '',
            'block_reason': '화면 검증 대기', 'verification_gap': ''}


def response(tc):
    return {'findings': [{'id': r['id'], 'status': 'met', 'observation': r['facet'] + ' 확인',
                          'regions': [[0, 0, 160, 100]], 'node_ids': [], 'relations': []}
                         for r in v.requirements(tc)]}


def png():
    image = Image.new('RGB', (160, 100), 'white')
    ImageDraw.Draw(image).rectangle((100, 25, 130, 85), fill='blue')
    data = io.BytesIO()
    image.save(data, format='PNG')
    return data.getvalue()


class VisualTests(unittest.TestCase):
    def test_reference_metadata_does_not_require_data_or_visual_checks(self):
        tc = step('설정으로 돌아온다.')
        tc.update(title='다른 문서', test_purpose='점수 계산 정확성과 그래프 색상 확인')
        self.assertEqual(ne.verification_gap(tc), '')
        self.assertFalse(ne.needs_visual_verification(tc))
        tc['expected'] = '점수 계산 결과가 원본 데이터와 일치한다.'
        self.assertTrue(ne.verification_gap(tc))

    def setUp(self):
        m._test_stop_event.clear()
        self.addCleanup(m._test_stop_event.clear)

    def mocks(self, stack, reply=None):
        folder = stack.enter_context(tempfile.TemporaryDirectory())
        stack.enter_context(patch.object(m, 'QA_RESULTS_DIR', Path(folder)))
        stack.enter_context(patch.object(m, 'capture_screen_png', return_value=png()))
        stack.enter_context(patch.object(m, 'dump_ui_xml', return_value='<hierarchy/>'))
        ocr = stack.enter_context(patch.object(m, 'ocr_screen_texts', return_value=[]))
        stack.enter_context(patch.object(m, 'emit_test_ai_status'))
        ask = stack.enter_context(patch.object(m, 'request_test_ai_json', return_value=(reply, {})))
        adb = stack.enter_context(patch.object(m, 'adb'))
        return ask, ocr, adb

    def test_display_purpose_does_not_force_calculation_check(self):
        tc = step()
        self.assertEqual(ne.verification_gap(tc), '')
        self.assertTrue(ne.needs_visual_verification(tc))
        tc['expected'] += '\n점수 계산 결과가 원본 데이터와 일치한다.'
        self.assertIn('원본 학습 데이터', ne.verification_gap(tc))

    def test_every_facet_and_procedure_must_be_returned(self):
        tc = step()
        req = v.requirements(tc)
        self.assertTrue({'position', 'object_type', 'count_and_all_members', 'displayed_scale'} <= {r['facet'] for r in req})
        data = response(tc)
        data['findings'].pop()
        with self.assertRaisesRegex(ValueError, 'omitted'):
            v.validate_verdict(data, req, 160, 100, [])
        data = response(tc)
        data['findings'][-1] = data['findings'][0]
        with self.assertRaises(ValueError):
            v.validate_verdict(data, req, 160, 100, [])

    def test_unknown_and_failed_are_not_pass(self):
        tc = step()
        data = response(tc)
        data['findings'][0]['status'] = 'unknown'
        self.assertEqual(v.validate_verdict(data, v.requirements(tc), 160, 100, []), 'blocked')
        data['findings'][1]['status'] = 'unmet'
        self.assertEqual(v.validate_verdict(data, v.requirements(tc), 160, 100, []), 'failed')

    def test_invalid_boxes_ids_empty_evidence_and_claim_only_are_rejected(self):
        tc = step()
        variants = [{'regions': [[0, 0, 200, 100]]}, {'regions': []}, {'regions': [[0, 0, 1]]},
                    {'node_ids': ['old-node']}, {'observation': ''}, {'status': 'done'}]
        for bad in variants:
            with self.subTest(bad=bad):
                data = response(tc)
                data['findings'][0].update(bad)
                with self.assertRaises(ValueError):
                    v.validate_verdict(data, v.requirements(tc), 160, 100, [])

    def test_ui_bounds_contradiction_overrides_ai_pass(self):
        tc = step('카드 안에 토글이 노출된다')
        data = response(tc)
        data['findings'][0]['relations'] = [{'kind': 'inside', 'subject_id': 'a', 'reference_id': 'b'}]
        nodes = [{'id': 'a', 'bounds': [120, 20, 150, 40]}, {'id': 'b', 'bounds': [0, 0, 100, 80]}]
        self.assertEqual(v.validate_verdict(data, v.requirements(tc), 160, 100, nodes), 'failed')

    def test_negation_and_quoted_requirement_preserved(self):
        tc = step('카드 안에 "오류"가 표시되지 않는다')
        self.assertIn('표시되지 않는다', v.requirements(tc)[0]['source'])
        self.assertTrue(ne.needs_visual_verification(tc))

    def test_wrong_screen_or_unexecuted_procedure_is_block_not_app_failure(self):
        tc = step()
        tc['precondition'] = '실력 분석 화면'
        for prerequisite in ['precondition', 'procedure']:
            data = response(tc)
            for finding in data['findings']:
                if finding['id'] in {prerequisite, 'v1'}:
                    finding['status'] = 'unmet'
            self.assertEqual(v.validate_verdict(data, v.requirements(tc), 160, 100, []), 'blocked')

    def test_success_saves_same_image_ocr_and_no_device_input(self):
        tc = step()
        with ExitStack() as stack:
            ask, ocr, adb = self.mocks(stack, response(tc))
            result = m.execute_standard_test_step(tc)
            self.assertIn('AI 시각 검증', result)
            self.assertEqual(Path(tc['_visual_evidence']['screenshot']).read_bytes(), png())
            self.assertEqual(ask.call_args.kwargs['image_png'], ocr.call_args.kwargs['image_png'])
            adb.assert_not_called()
        self.assertEqual(tc['_visual_evidence']['status'], 'passed')

    def test_api_failure_and_invalid_response_block_no_input(self):
        for error in [RuntimeError('quota exceeded'), ValueError('invalid JSON')]:
            with ExitStack() as stack:
                tc = step()
                ask, _, adb = self.mocks(stack)
                ask.side_effect = error
                with self.assertRaises(m.BlockedStep):
                    v.verify(m, tc)
                self.assertEqual(tc['_visual_evidence']['status'], 'blocked')
                adb.assert_not_called()

    def test_capture_error_blank_and_changed_screen_never_call_ai(self):
        for captures in [[b'', b''], [png(), b''], [png(), self.solid_png()]]:
            with ExitStack() as stack:
                ask, _, _ = self.mocks(stack)
                stack.enter_context(patch.object(m, 'capture_screen_png', side_effect=captures))
                with self.assertRaises(m.BlockedStep):
                    v.verify(m, step())
                ask.assert_not_called()

    def test_transient_screen_change_reobserves_without_input(self):
        tc = step()
        with ExitStack() as stack:
            ask, _, adb = self.mocks(stack, response(tc))
            stack.enter_context(patch.object(m, 'capture_screen_png',
                side_effect=[self.solid_png(), png(), png(), png()]))
            v.verify(m, tc)
            self.assertEqual(tc['_visual_evidence']['observation_attempts'], 2)
            ask.assert_called_once()
            adb.assert_not_called()

    def test_persistent_screen_change_blocks_after_two_observations(self):
        with ExitStack() as stack:
            ask, _, adb = self.mocks(stack)
            capture = stack.enter_context(patch.object(m, 'capture_screen_png',
                side_effect=[self.solid_png(), png()] * 2))
            with self.assertRaises(m.BlockedStep):
                v.verify(m, step())
            self.assertEqual(capture.call_count, 4)
            ask.assert_not_called()
            adb.assert_not_called()

    def solid_png(self):
        stream = io.BytesIO()
        Image.new('RGB', (160, 100), 'black').save(stream, 'PNG')
        return stream.getvalue()

    def test_stop_propagates_instead_of_block(self):
        with ExitStack() as stack:
            ask, _, _ = self.mocks(stack)
            ask.side_effect = ai_tc.Stopped('stop')
            tc = step()
            with self.assertRaises(ai_tc.Stopped):
                v.verify(m, tc)
            self.assertEqual(tc['_visual_evidence']['status'], 'stopped')

    def test_mixed_calculation_cannot_pass_visual_only(self):
        tc = step('막대그래프가 표시된다. 점수 계산이 정확하다.')
        tc['verification_gap'] = ne.verification_gap(tc)
        with ExitStack() as stack:
            self.mocks(stack, response(tc))
            with self.assertRaises(m.BlockedStep):
                v.verify(m, tc)
        self.assertTrue(tc['_visual_evidence']['findings'])

    def test_standard_procedure_executes_before_visual_without_lock_during_ai(self):
        tc = {**step(), 'action': 'tap', 'target': 'Report'}
        order = []
        def visual(_):
            self.assertFalse(m._adb_input_lock.locked())
            order.append('visual')
            return 'verified'
        with patch.object(m, 'execute_step', side_effect=lambda _: order.append('tap') or 'tapped'), \
                patch.object(m, 'verify_visual_step', side_effect=visual):
            m.execute_standard_test_step(tc)
        self.assertEqual(order, ['tap', 'visual'])

    def test_explicit_exclusion_and_risky_operation_not_overridden(self):
        for tc in [{**step(), 'source': 'automation', 'block_reason': 'Excluded by author'},
                   {**step(), 'test_item': '계정 삭제'}]:
            with patch.object(m, 'verify_visual_step') as visual:
                with self.assertRaises(m.BlockedStep):
                    m.execute_standard_test_step(tc)
                visual.assert_not_called()

    def test_planned_visual_case_uses_common_verifier_not_text_only(self):
        tc = step()
        tc['ai_hint'] = {'index': 1, 'intent': 'Inspect graph', 'actions': [], 'checks': [],
                         'preconditions': [], 'unsupported_reason': ''}
        ai_tc.validate_hint(tc['ai_hint'], tc)
        with patch.object(m, 'verify_visual_step', return_value='visual evidence') as visual, \
                patch.object(ai_tc, 'verify') as text:
            ai_tc.execute_plan(m, tc)
            visual.assert_called_once()
            text.assert_not_called()

    def test_legacy_manual_ui_row_becomes_visual_inspection(self):
        from openpyxl import Workbook
        wb = Workbook()
        wb.active.append(['1Depth', 'Check', 'Expected Result'])
        wb.active.append(['분석', 'UI 리소스', '카드 안에 LC/RC 토글이 표시된다'])
        tc = m.parse_manual_qa_excel(wb)[0]
        self.assertTrue(tc['visual_inspection'])
        self.assertEqual(ai_tc.hard_block_reason(m, tc), '')
        with patch.object(m, 'verify_visual_step', return_value='visual') as visual, \
                patch.object(m, 'execute_step') as execute:
            m.execute_standard_test_step(tc)
            execute.assert_not_called()
            visual.assert_called_once()

    def test_both_runners_continue_and_persist_visual_evidence(self):
        for ai in [False, True]:
            with self.subTest(ai=ai), ExitStack() as stack:
                cases = [step(), step(), step()]
                stack.enter_context(patch.object(m, '_test_steps', cases))
                stack.enter_context(patch.object(m, '_test_metadata', {}))
                for name in ['validate_recorder_resolution', 'append_ai_test_history', 'record_linked_retest',
                             'queue_test_failure', 'request_ai_test_overview']:
                    stack.enter_context(patch.object(m, name))
                hints = {i: {'index': i, 'intent': 'Inspect', 'preconditions': [], 'actions': [],
                             'checks': [], 'unsupported_reason': ''} for i in range(1, 4)}
                stack.enter_context(patch.object(m, 'request_ai_test_batch_hints', return_value=(hints, {})))
                stack.enter_context(patch.object(m, 'check_device', return_value=True))
                stack.enter_context(patch.object(m, 'failure_extra', return_value={}))
                history = stack.enter_context(patch.object(m, 'append_test_run_history'))
                emit = stack.enter_context(patch.object(m.socketio, 'emit'))
                recovery = stack.enter_context(patch.object(m, 'run_ai_test_recovery'))
                states = iter(['failed', 'blocked', 'passed'])
                def verify(tc):
                    status = next(states)
                    tc['_visual_evidence'] = {'status': status, 'method': 'ai-visual', 'screenshot_url': '/proof.png'}
                    if status == 'failed':
                        raise AssertionError('Count mismatch')
                    if status == 'blocked':
                        raise m.BlockedStep('Clipped card')
                    return 'All visible'
                stack.enter_context(patch.object(m, 'verify_visual_step', side_effect=verify))
                m.run_test_steps(ai_mode=ai)
                done = [c.args[1] for c in emit.call_args_list if c.args[0] == 'test_run_finished'][0]
                self.assertEqual((done['passed'], done['failed'], done['blocked']), (1, 1, 1))
                results = history.call_args.args[0]['results']
                self.assertEqual([r['visual_verification']['status'] for r in results], ['failed', 'blocked', 'passed'])
                recovery.assert_not_called()


if __name__ == '__main__':
    unittest.main()
