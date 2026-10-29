import unittest
import tc_adaptive as a
import tc_lifecycle as lifecycle
import tc_workflow as w


class ScopeTests(unittest.TestCase):
    def test_logout_observation_does_not_authorize_input(self):
        for text in ['로그아웃 버튼의 표시 위치를 확인한다. 로그아웃은 누르지 않는다.',
                     '로그아웃 버튼이 상태바에 겹치지 않는다.']:
            self.assertIsNone(w.RISK.search(a.observation_risk_text(text)))
        for text in ['로그아웃 버튼을 클릭한다.', '로그아웃 버튼의 위치를 확인하고 클릭한다.',
                     '로그아웃은 누르지 않는다. 계정을 삭제한다.']:
            self.assertIsNotNone(w.RISK.search(a.observation_risk_text(text)))

    def test_temporal_conditions_are_both_required(self):
        step={'expected':'이용약관 본문이 표시되고 시스템 뒤로가기를 누르면 설정 화면으로 돌아온다.'}
        self.assertEqual(a.expectations(step), ['이용약관 본문이 표시된다.', '시스템 뒤로가기를 누르면 설정 화면으로 돌아온다.'])

    def test_single_restart_and_extra_requirements(self):
        step={'test_item':'1. 앱을 실행한다.\n2. 홈, 강의, 설정 화면으로 이동한다.\n3. 앱을 종료하고 재실행한다.',
              'expected':'이동한 실행화면, 이동·종료·재실행 구간에서 강제 종료나 비정상 종료가 발생하지 않는다.'}
        self.assertEqual(lifecycle.spec(step)['cycles'],1)
        step['expected']+=' 로그인 시간이 1초 이하이다.'
        self.assertIsNone(lifecycle.spec(step))
