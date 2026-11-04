import ast
import copy
import json
from unittest.mock import Mock, patch
from tests import test_tc_workflow as fixtures
import automation_library


class SavedWorkflowTests(fixtures.WorkflowTests):
    def setUp(self):
        super().setUp()
        self.host.foreground_app_evidence.return_value={'package':'fixture','version':'1'}
        self.host.get_device_info.return_value={'serial':'device1','android':'16'}

    def saved(self):
        flow=self.prepare()
        result=self.call(flow.id+'/save',{'approved':True,'plan_digest':flow.digest})
        self.assertEqual(result.status_code,201,result.json)
        return result.json['version']

    def test_save_requires_approval(self):
        flow=self.prepare()
        self.assertEqual(self.call(flow.id+'/save',{'plan_digest':flow.digest}).status_code,409)

    def test_state_assertion_saved_replayed_and_failure_evidence(self):
        s=self.host._test_steps[0]
        s.update(expected='"설정"이 선택된다.',qa_expected='"설정"이 선택된다.')
        self.host.request_test_ai_json.return_value=({'plans':[fixtures.ai_plan(s)]}, {})
        flow=self.prepare(ai=True)
        self.assertEqual(flow.public()['cases'][0]['assertions'][0]['method'],'ui-assertion')
        version=self.call(flow.id+'/save',{'approved':True,'plan_digest':flow.digest}).json['version']
        store=automation_library.Library(self.host.QA_RESULTS_DIR/'automation_library')
        item=store.read(version)
        self.assertEqual(item['assertions'][0][0]['conditions'][0]['attribute'],'selected')
        for selected, status in [('true','passed'),('false','failed')]:
            self.assertEqual(self.call('library/'+version+'/load',{}).status_code,200)
            driver=Mock()
            driver.xml.return_value=self.host.dump_ui_xml.return_value.replace('clickable="true"',f'clickable="true" selected="{selected}"')
            with patch('tc_workflow.prepared_appium.AppiumSession',return_value=driver):
                result=self.start(self.service['current'])
            self.assertEqual(result['counts'][status],1)
            self.assertFalse(result['results'][0]['ai_assisted'])
            evidence=[e for stage in result['results'][0]['stages'] for e in stage['evidence']]
            self.assertTrue(any(e.get('method')=='ui-assertion' for e in evidence))

    def test_missing_state_attribute_blocks_with_evidence(self):
        s=self.host._test_steps[0]
        s.update(expected='"설정"이 선택된다.',qa_expected='"설정"이 선택된다.')
        flow=self.prepare(False)
        self.assertFalse(flow.plans[0]['blocked_reason'])
        result=self.start(flow)
        self.assertEqual(result['counts']['blocked'],1)
        self.assertTrue(any(e.get('unverified_reason') for e in result['results'][0]['verification']))
        self.host.verify_visual_step.assert_not_called()

    def test_assertion_revision_change_requires_reapproval(self):
        version=self.saved()
        with patch('expectation_checks.REVISION',999):
            self.assertEqual(self.call('library/'+version+'/load',{}).status_code,409)

    def test_uncompiled_condition_never_uses_legacy_presence_or_calls_ai(self):
        version=self.saved()
        self.assertEqual(self.call('library/'+version+'/load',{}).status_code,200)
        flow=self.service['current']
        s={**fixtures.step(),'_workflow_index':1}
        with self.assertRaisesRegex(self.host.BlockedStep,'재승인'):
            flow.check(s,'설정 카드가 오른쪽에 표시된다.')
        flow.offline_checks=None
        flow.ai_mode=False
        with self.assertRaisesRegex(self.host.BlockedStep,'AI TC RUN'):
            flow.check(s,'설정 카드가 오른쪽에 표시된다.')
        self.host.dump_ui_xml.assert_not_called()
        self.host.verify_visual_step.assert_not_called()

    def test_library_survives_service_reset_and_exports_safe_code(self):
        version=self.saved()
        self.service['current']=None
        self.assertEqual(len(self.call('library').json['versions']),1)
        source=self.call('library/'+version+'/code').data.decode()
        ast.parse(source)
        self.assertIn('execute_approved',source)
        self.assertEqual(self.call('library/'+version+'/load',{}).status_code,200)
        self.host.request_test_ai_json.assert_not_called()

    def test_replay_creates_new_run_and_preserves_version(self):
        version=self.saved()
        ids=[]
        for _ in range(2):
            self.assertEqual(self.call('library/'+version+'/load',{}).status_code,200)
            flow=self.service['current']
            driver=Mock()
            driver.xml.return_value=self.host.dump_ui_xml.return_value
            with patch('tc_workflow.prepared_appium.AppiumSession',return_value=driver):
                result=self.start(flow)
            self.assertEqual(result['counts']['passed'],1)
            self.assertEqual(result['automation_version'],version)
            ids.append(result['run_id'])
        self.assertNotEqual(*ids)

    def test_changed_source_rejected(self):
        version=self.saved()
        self.host._test_steps[0]['expected']='changed'
        self.assertEqual(self.call('library/'+version+'/load',{}).status_code,409)

    def test_changed_app_rejected(self):
        version=self.saved()
        self.host.foreground_app_evidence.return_value['version']='2'
        self.assertEqual(self.call('library/'+version+'/load',{}).status_code,409)

    def test_tamper_rejected(self):
        version=self.saved()
        store=automation_library.Library(self.host.QA_RESULTS_DIR/'automation_library')
        item=json.loads(store.path(version).read_text(encoding='utf-8'))
        item['plans'][0]['actions'][0]['action']='shell'
        store.path(version).write_text(json.dumps(item),encoding='utf-8')
        self.assertEqual(self.call('library/'+version+'/load',{}).status_code,409)

    def test_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            automation_library.Library(self.host.QA_RESULTS_DIR).read('../secret')

    def test_running_save_and_load_rejected(self):
        version=self.saved()
        self.host._random_running=True
        self.assertEqual(self.call('library/'+version+'/load',{}).status_code,409)
        flow=self.service['current']
        self.assertEqual(self.call(flow.id+'/save',{'approved':True,'plan_digest':flow.digest}).status_code,409)

    def test_generated_values_are_not_python(self):
        source=automation_library.code_for({'name':"'); import os; #"})
        tree=ast.parse(source)
        self.assertEqual([n.names[0].name for n in ast.walk(tree) if isinstance(n,ast.Import)],['json'])

    def test_partial_expected_not_compiled_to_presence(self):
        import tc_workflow as workflow
        step=fixtures.step()
        step.update(expected='"설정"이 표시되고 색상이 파란색이다',qa_expected='"설정"이 표시되고 색상이 파란색이다')
        self.assertIsNone(workflow.literal_checks([step],[fixtures.ai_plan(step)]))

    def test_explicit_absence_is_compiled(self):
        import tc_workflow as workflow
        step=fixtures.step()
        step.update(expected='"설정"이 표시되지 않는다',qa_expected='"설정"이 표시되지 않는다')
        checks=workflow.literal_checks([step],[fixtures.ai_plan(step)])
        self.assertFalse(checks[1][step['expected']][0]['present'])

    def test_appium_failure_does_not_pass(self):
        version=self.saved()
        self.call('library/'+version+'/load',{})
        with patch('tc_workflow.prepared_appium.AppiumSession',side_effect=RuntimeError('offline')):
            result=self.start(self.service['current'])
        self.assertEqual(result['counts']['passed'],0)
        self.assertEqual(result['counts']['blocked'],1)
