import copy
import io
import threading
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PIL import Image

import tc_lifecycle as life
import tc_workflow as workflow
import tc_adaptive


def timed():
    return {'test_item': '1. 메인화면에서 시스템 뒤로가기를 한 번 수행한다.\n2. 첫 뒤로가기 후 2초 안에 시스템 뒤로가기를 한 번 더 수행한다.',
            'precondition': '메인화면',
            'expected': "첫 뒤로가기에 「'뒤로' 버튼을 한 번 더 누르면 종료합니다.」 안내가 표시된다.\n두 번째 뒤로가기에 앱이 종료된다."}


class LifecycleTests(unittest.TestCase):
    def test_only_known_complete_source_is_supported(self):
        self.assertEqual(life.spec(timed())['kind'], 'timed_back')
        for change in [{'expected': timed()['expected']+'\n서버 데이터가 삭제된다.'},
                       {'test_item':timed()['test_item']+'\n3. 구매한다.'},
                       {'test_item':timed()['test_item'].replace('2초','0.1초')}]:
            self.assertIsNone(life.spec({**timed(), **change}))

    def test_korean_open_verb_requires_real_action(self):
        s={'test_item':'1. 내 학습에서 임의의 강의 재생 화면을 연다.', 'expected':'강의 화면이 표시된다.'}
        a={'procedure':1,'basis':'procedure','source':s['test_item'],'action':'navigate',
           'target':'강의 재생 화면','direction':'','wait_ms':0}
        p={'index':1,'blocked_reason':'','actions':[a],'checks':[{'expectation':1,'after_action':1}]}
        tc_adaptive.validate(p,s,workflow.procedures,workflow.RISK)
        p['actions'][0]['action']='inspect'
        with self.assertRaises(ValueError):
            tc_adaptive.validate(p,s,workflow.procedures,workflow.RISK)

    def test_crash_logs_are_bounded_by_package_and_time(self):
        self.assertFalse(life.crash_records('100.0 1 2 E Process: com.example.app',101,'com.example.app'))
        self.assertFalse(life.crash_records('102.0 1 2 E Process: com.example.app2',101,'com.example.app'))
        self.assertTrue(life.crash_records('102.0 1 2 E Process: com.example.app',101,'com.example.app'))

    def test_explicit_state_before_back_can_be_prepared(self):
        s={'test_item':'1. 전체화면이 아닌 상태에서 시스템 뒤로가기를 수행한다.', 'expected':'홈으로 돌아온다.'}
        a={'procedure':1,'basis':'procedure','source':s['test_item'],'action':'navigate',
           'target':'전체화면이 아닌 상태','direction':'','wait_ms':0}
        p={'index':1,'blocked_reason':'','actions':[a,{**a,'action':'back','target':''}],
           'checks':[{'expectation':1,'after_action':2}]}
        tc_adaptive.validate(p,s,workflow.procedures,workflow.RISK)
        p['actions'][0]['target']='시스템'
        with self.assertRaises(ValueError):
            tc_adaptive.validate(p,s,workflow.procedures,workflow.RISK)

    def flow(self, root):
        class Blocked(Exception):
            pass
        host=SimpleNamespace(BlockedStep=Blocked,_adb_input_lock=threading.Lock(),QA_RESULTS_DIR=Path(root),
            adb=Mock(return_value=(b'',b'',0)),capture_screen_png=Mock(return_value=b'png'),
            ocr_screen_texts=Mock(return_value=[{'text':"'뒤로' 버튼을 한 번 더 누르면 종료합니다."}]),
            qa_result_screenshot_url=lambda p:'/'+p.name)
        f=SimpleNamespace(host=host,id='test',guard=Mock(),cancel=Mock(),phase=Mock(),
            navigate=Mock(return_value=('ready',[{'file':'pre.png'}])), evidence=Mock(return_value={'file':'after.png'}))
        f.cancel.wait.return_value=False
        return f

    def test_timed_inputs_are_measured_and_evidence_recorded(self):
        with tempfile.TemporaryDirectory() as root:
            f=self.flow(root); s=timed(); case={'source':{'tc_id':'TC'},'stages':[]}
            with patch.object(life,'foreground',side_effect=['com.example.app','com.example.app','com.android.launcher']), \
                 patch.object(life,'component',side_effect=['com.example.app/.Main','com.android.launcher/.Home']), \
                 patch.object(life,'launch') as launch, patch.object(life.time,'monotonic',side_effect=[0,.4,.7]):
                msg=life.execute(f,s,case,life.spec(s))
            self.assertIn('0.700',msg)
            self.assertEqual(len(s['_input_events']),2)
            self.assertEqual(case['stages'][0]['status'],'passed')
            self.assertTrue((Path(root)/'tc-timed-toast-test.png').exists())
            launch.assert_called_once()

    def test_deadline_does_not_send_second_back(self):
        with tempfile.TemporaryDirectory() as root:
            f=self.flow(root); s=timed(); case={'source':{'tc_id':'TC'},'stages':[]}
            with patch.object(life,'foreground',return_value='com.example.app'), \
                 patch.object(life,'component',side_effect=['com.example.app/.Main','com.android.launcher/.Home']), \
                 patch.object(life.time,'monotonic',side_effect=[0,2.1]):
                with self.assertRaises(f.host.BlockedStep):
                    life.execute(f,s,case,life.spec(s))
            self.assertEqual(len(s['_input_events']),1)

    def test_unknown_toast_is_not_pass(self):
        with tempfile.TemporaryDirectory() as root:
            f=self.flow(root); f.host.ocr_screen_texts.return_value=[]
            with patch.object(life,'foreground',side_effect=['com.example.app','com.example.app','com.android.launcher']), \
                 patch.object(life,'component',side_effect=['com.example.app/.Main','com.android.launcher/.Home']), \
                 patch.object(life,'launch'), patch.object(life,'toast_text',return_value=''), patch.object(life.time,'monotonic',side_effect=[0,.4,.7]):
                with self.assertRaises(f.host.BlockedStep):
                    life.execute(f,timed(),{'source':{'tc_id':'TC'},'stages':[]},life.spec(timed()))

    def test_native_workflow_emits_result_and_note(self):
        from tests.test_tc_workflow import WorkflowTests, step
        fixture=WorkflowTests()
        fixture.setUp()
        try:
            fixture.host._test_steps=[{**step(), **timed(), 'qa_expected':timed()['expected']}]
            flow=fixture.prepare(ai=True)
            self.assertEqual(flow.plans[0]['native']['kind'],'timed_back')
            def measured(flow, s, case, value):
                case['stages'].append({'status':'passed','preconditions_verified':True,
                    'expectations_verified':True,'evidence':[{'file':'fixture.png'}]})
                s['_input_events']=[{'status':'adb_accepted'},{'status':'adb_accepted'}]
                return 'Measured 0.7 seconds'
            with patch.object(life,'execute',side_effect=measured):
                result=fixture.start(flow)
            self.assertEqual(result['counts']['passed'],1)
            self.assertIn('Measured 0.7 seconds',result['results'][0]['note'])
            self.assertEqual(result['results'][0]['input_count'],2)
            self.assertEqual(fixture.host.emit_progress.call_args.args[0].status,'passed')
        finally:
            fixture.doCleanups()

    def test_contrast_scope_is_explicit_not_all_visual_cases(self):
        s={'test_item':'1. 그랭 기본동작 대상 화면을 차례로 연다.\n2. UI를 확인한다.',
           'expected':'흰 배경에서 상태바 아이콘이 흰색으로 묻히지 않는다.\n어두운 배경이 표시되는 경우 상태바 아이콘이 어두운 색으로 묻히지 않는다.'}
        converted=workflow.functional_step(s)
        self.assertEqual(len(life.spec(converted)['pages']),5)
        self.assertIn('범위',converted['_scope_note'])
        self.assertIsNone(life.spec(workflow.functional_step({**s,'expected':s['expected']+'\n레이아웃은 Figma와 동일하다.'})))

    def test_withdrawal_entry_never_matches_confirmation(self):
        xml='<hierarchy><node text="설정"/><node text="개인정보 처리방침"/><node text="고객센터"/><node text="계정탈퇴" clickable="true" bounds="[1,2][30,40]"/></hierarchy>'
        self.assertEqual(life.withdrawal_entry(xml),'[1,2][30,40]')
        with self.assertRaises(ValueError):
            life.withdrawal_entry('<hierarchy><node text="계정탈퇴" clickable="true"/><node text="정말 탈퇴하시겠습니까?"/></hierarchy>')
        with self.assertRaises(ValueError):
            life.withdrawal_entry(xml.replace('</hierarchy>','<node text="계정탈퇴" clickable="true"/></hierarchy>'))
        self.assertFalse(life.withdrawal_information_visible(xml))
        self.assertTrue(life.withdrawal_information_visible('<hierarchy><node text="계정탈퇴"/><node text="계정을 탈퇴하시면 학습 정보가 삭제됩니다."/></hierarchy>'))
        self.assertFalse(life.withdrawal_information_visible('<hierarchy><node text="계정탈퇴"/></hierarchy>'))

    def test_toast_transcription_requires_bottom_image_evidence(self):
        png=io.BytesIO()
        Image.new('RGB',(100,200)).save(png,'PNG')
        with tempfile.TemporaryDirectory() as root:
            f=self.flow(root)
            f.runtime_choice=Mock(return_value=({'text':'안내','bounds':[5,130,95,190]},{}))
            stage={}
            self.assertEqual(life.toast_text(f,png.getvalue(),stage),'안내')
            self.assertEqual(stage['toast_visual_read']['bounds'],[5,130,95,190])
            # The expected wording is not sent to the transcriber for parroting.
            self.assertEqual(f.runtime_choice.call_args.args[1],{})
            f.runtime_choice.return_value=({'text':'안내','bounds':[5,0,95,30]}, {})
            with self.assertRaises(f.host.BlockedStep):
                life.toast_text(f,png.getvalue(),{})


if __name__=='__main__':
    unittest.main()
