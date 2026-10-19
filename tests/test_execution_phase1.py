"""Synthetic device fixtures only; never touches a tablet or external service."""
import copy
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PIL import Image, ImageDraw
import execution_results as er
import ui_guard as ui
import verified_execution as ve
import mirror_server as m

def png(animation=False, target=False):
    im=Image.new('RGB',(200,200),'white')
    d=ImageDraw.Draw(im);d.rectangle((20,20,80,60),fill='red' if target else 'blue')
    if animation:d.rectangle((140,140,160,160),fill='orange')
    b=io.BytesIO();im.save(b,'PNG');return b.getvalue()

def xml(label='도움말', result=False, bad=False, overlay=False, disabled=False, shifted=False, animation='clock'):
    target='[25,20][85,60]' if shifted else '[20,20][80,60]'
    return ('<hierarchy rotation="0"><node class="Root" package="fixture.app" bounds="[0,0][200,200]">'
      '<node class="Panel" bounds="[0,0][100,100]">'
      '<node text="메인" class="Text" bounds="[0,0][15,15]"/>'
      f'<node text="{label}" resource-id="nav" class="Button" clickable="true" enabled="{str(not disabled).lower()}" bounds="{target}"/>'
      + ('<node text="결과 화면" resource-id="result" class="Text" bounds="[5,70][95,90]"/>' if result else '')
      + ('<node text="내용" class="Text" bounds="[5,91][95,99]"/>' if result and not bad else '')
      + '</node><node class="Animation" bounds="[120,120][190,190]">'
      f'<node text="{animation}" class="Text" bounds="[140,140][160,160]"/></node>'
      + ('<node class="Dialog" text="overlay" bounds="[0,0][200,200]"/>' if overlay else '')+'</node></hierarchy>')

PROCEDURES=list(ve.reviewed_tc.PROCEDURES)
SOURCES=['메인화면에서 도움말 버튼을 클릭한다.','메인화면에서 환경설정 버튼을 클릭한다.','제품정보 탭을 선택한다.']
TARGETS=['도움말','환경설정','제품정보']
META={'upload_id':'upload-one','file_hash':'abc123','filename':'fixture.xlsx'}
def fixture(i=1):
    j=(i-1)%3
    step={'case_no':f'FIX-{i:03}', 'sheet':'Fixture', 'row':i+1,'title':'Synthetic fixture',
          'test_item':SOURCES[j], 'precondition':'메인 표시', 'expected':'내용 표시'}
    mapping={'source_digest':er.fingerprint(er.source(step,META)),
      'review':{'confirmed':True,'evidence':'Synthetic XML fixture mapping; NOT real-device validation'},
      'stages':[{'source':SOURCES[j],'action':'tap','selector':{'text':TARGETS[j],'resource-id':'nav'},
        'preconditions':[{'source':'메인 표시','selector':{'text':'메인'},'present':True}],
        'checks':[{'source':'내용 표시','selector':{'text':'내용'},'present':True}],
        'screen':{'resource-id':'result'}}]}
    return step,mapping

class FakeDevice:
    def __init__(self,label='도움말',bad=False):
        self.label,self.bad,self.taps=label,bad,0
        self.observations=0;self.fail_connection=False;self.changed=False;self.on_observe=None
    def environment(self):
        if self.fail_connection:raise ve.EnvironmentError('Fixture disconnected')
        return {'device':{'version':'fixture-1'},'app':{'version':'fixture-app'},'synthetic':True}
    def observe(self):
        self.observations+=1
        if self.on_observe:self.on_observe(self.observations)
        return xml(self.label,result=self.taps>0,bad=self.bad,overlay=self.changed and self.observations>1),png(animation=self.observations%2==0)
    def evidence(self,obs,label):
        return {'file':'fixture.png','url':'/api/results/screenshot/fixture.png','captured_at':er.now(),'synthetic':True}
    def tap(self,point):
        self.taps+=1

class StrictExecutionTests(unittest.TestCase):
    def setUp(self):
        self.host=SimpleNamespace(_test_metadata=copy.deepcopy(META),_adb_input_lock=threading.Lock(),append_test_run_history=Mock())
    def run_one(self,device=None):
        step,mapping=fixture()
        run=ve.Run(self.host,[step],META,[mapping],adapter=device or FakeDevice(),method='software_test')
        run.execute();return run
    def test_six_representative_tc_fixtures_are_software_tests(self):
        for i in range(1,7):
            s,mp=fixture(i);device=FakeDevice(TARGETS[(i-1)%3])
            run=ve.Run(self.host,[s],META,[mp],adapter=device,method='software_test');run.execute()
            record=run.snapshot();self.assertEqual(record['method'],'software_test')
            self.assertEqual(record['results'][0]['status'],'passed');self.assertEqual(device.taps,1)
            self.assertEqual(record['results'][0]['source']['tc_id'],s['case_no'])
            stage=record['results'][0]['stages'][0]
            self.assertEqual(len(stage['evidence']),2);self.assertTrue(stage['preconditions_verified'])
            self.assertTrue(stage['expectations_verified']);self.assertIsNotNone(stage['elapsed_ms'])
    def test_missing_procedure_or_expected_or_precondition_rejected(self):
        for change in ('procedure','expected','precondition'):
            s,mp=fixture()
            if change=='procedure':mp['stages']=[]
            if change=='expected':mp['stages'][0]['checks']=[]
            if change=='precondition':mp['stages'][0]['preconditions']=[]
            with self.assertRaises(ValueError):ve.Run(self.host,[s],META,[mp],adapter=FakeDevice())
    def test_omitted_second_original_procedure_rejected(self):
        s,mp=fixture();s['test_item']+='\n'+SOURCES[1]
        mp['source_digest']=er.fingerprint(er.source(s,META))
        with self.assertRaises(ValueError):ve.Run(self.host,[s],META,[mp],adapter=FakeDevice())
    def test_source_version_tamper_and_risky_unrelated_target_rejected(self):
        for mutation in ('version','target','action','review'):
            s,mp=fixture()
            if mutation=='version':mp['source_digest']='wrong'
            if mutation=='target':mp['stages'][0]['selector']['text']='삭제'
            if mutation=='action':mp['stages'][0]['action']='input'
            if mutation=='review':mp['review']['confirmed']=False
            with self.assertRaises(ValueError):ve.Run(self.host,[s],META,[mp],adapter=FakeDevice())
    def test_negative_expectation_cannot_be_inverted(self):
        s,mp=fixture();s['expected']='내용 표시되지 않음';mp['source_digest']=er.fingerprint(er.source(s,META))
        mp['stages'][0]['checks'][0]['source']=s['expected']
        with self.assertRaises(ValueError):ve.Run(self.host,[s],META,[mp],adapter=FakeDevice())
    def test_partial_label_cannot_cover_compound_or_calculation_condition(self):
        for expected in ['내용과 그래프가 모두 표시된다.', '내용 표시 및 3개 메뉴 존재', '내용의 정답률은 80%이다']:
            step,mapping=fixture();step['expected']=expected
            mapping['source_digest']=er.fingerprint(er.source(step,META))
            mapping['stages'][0]['checks'][0]['source']=expected
            with self.assertRaises(ValueError):ve.Run(self.host,[step],META,[mapping],adapter=FakeDevice())
    def test_absence_polarity_is_validated_as_whole_atomic_condition(self):
        for source in ['내용 없다','내용이 존재하지 않는다','내용 is absent']:
            check={'source':source,'selector':{'text':'내용'},'present':False}
            ve.validate_atomic_check(check)
            with self.assertRaises(ValueError):ve.validate_atomic_check({**check,'present':True})

    def test_product_mismatch_is_fail(self):
        run=self.run_one(FakeDevice(bad=True));r=run.snapshot()['results'][0]
        self.assertEqual((r['status'],r['error_kind']),('failed','product_mismatch'))
    def test_connection_is_block_with_no_input(self):
        d=FakeDevice();d.fail_connection=True;r=self.run_one(d).snapshot()['results'][0]
        self.assertEqual((r['status'],r['error_kind']),('blocked','environment'));self.assertEqual(d.taps,0)
    def test_wrong_mapping_is_block_with_no_input(self):
        d=FakeDevice('다른 버튼');r=self.run_one(d).snapshot()['results'][0]
        self.assertEqual((r['status'],r['error_kind']),('blocked','mapping'));self.assertEqual(d.taps,0)
    def test_overlay_change_blocks_before_input(self):
        d=FakeDevice();d.changed=True;r=self.run_one(d).snapshot()['results'][0]
        self.assertEqual(r['status'],'blocked');self.assertEqual(d.taps,0)
    def test_stop_race_between_observation_and_dispatch(self):
        d=FakeDevice();s,mp=fixture();run=ve.Run(self.host,[s],META,[mp],adapter=d,method='software_test')
        d.on_observe=lambda n:run.cancel.set() if n==2 else None
        run.execute();self.assertEqual(d.taps,0);self.assertEqual(run.snapshot()['results'][0]['status'],'stopped')
    def test_stop_ack_waits_for_inflight_input_and_no_replay(self):
        d=FakeDevice();s,mp=fixture();run=ve.Run(self.host,[s],META,[mp],adapter=d)
        entered,release,ack=threading.Event(),threading.Event(),threading.Event()
        def tap(point):entered.set();release.wait(2);d.taps+=1
        d.tap=tap
        worker=threading.Thread(target=run.execute);worker.start();self.assertTrue(entered.wait(2))
        stopper=threading.Thread(target=lambda:(run.stop(),ack.set()));stopper.start()
        self.assertFalse(ack.wait(.02));release.set();worker.join(3);stopper.join(3)
        self.assertTrue(ack.is_set());self.assertEqual(d.taps,1);self.assertEqual(run.snapshot()['state'],'stopped')
    def test_reupload_and_tampering_cannot_send_input(self):
        for mutate in ('upload','plan'):
            d=FakeDevice();s,mp=fixture();run=ve.Run(self.host,[s],META,[mp],adapter=d)
            if mutate=='upload':self.host._test_metadata={'upload_id':'new'}
            else:run.mappings[0]['stages'][0]['selector']['text']='new'
            run.execute();self.assertEqual(d.taps,0);self.assertEqual(run.snapshot()['results'][0]['status'],'blocked')
            self.host._test_metadata=copy.deepcopy(META)
    def test_uncertain_input_no_retry(self):
        d=FakeDevice()
        def tap(point):d.taps+=1;raise ve.EnvironmentError('Uncertain ADB')
        d.tap=tap
        r=self.run_one(d).snapshot()['results'][0]
        self.assertEqual(d.taps,1);self.assertEqual((r['status'],r['error_kind']),('blocked','environment'))
    def test_finished_run_cannot_replay_inputs(self):
        d=FakeDevice();run=self.run_one(d)
        with self.assertRaises(ValueError):run.execute()
        self.assertEqual(d.taps,1)
    def test_uncertain_input_halts_later_tc(self):
        steps,mappings=zip(*(fixture(i) for i in [1,4]))
        d=FakeDevice()
        def tap(point):d.taps+=1;raise ve.EnvironmentError('Input acknowledgement lost')
        d.tap=tap
        run=ve.Run(self.host,list(steps),META,list(mappings),adapter=d,method='software_test');run.execute()
        self.assertEqual(d.taps,1)
        self.assertEqual([r['status'] for r in run.snapshot()['results']],['blocked','not_run'])
    def test_post_action_missing_screen_is_mapping_block_not_product_fail(self):
        d=FakeDevice()
        original=d.observe
        def observe():
            result=original()
            if d.taps:return xml(),png()
            return result
        d.observe=observe
        r=self.run_one(d).snapshot()['results'][0]
        self.assertEqual((r['status'],r['error_kind']),('blocked','mapping'))

    def test_partial_stage_evidence_never_passes(self):
        case=er.new_run([fixture()[0]],META,'software_test')['results'][0]
        case['stages']=[{'status':'passed','preconditions_verified':True,'expectations_verified':False,'evidence':[{}]}]
        er.finish_case(case,'passed');self.assertEqual(case['status'],'blocked')

class UiGuardTests(unittest.TestCase):
    def test_unrelated_pixel_and_text_animation_accepted(self):
        self.assertEqual(ui.unchanged(xml(),png(),xml(animation='new clock'),png(animation=True),{'text':'도움말'}),[50,40])
    def test_changed_button_overlay_bounds_enabled_rejected(self):
        for newxml,newpng in [(xml(),png(target=True)),(xml(overlay=True),png()),(xml(shifted=True),png()),(xml(disabled=True),png()),(xml(label='삭제'),png())]:
            with self.assertRaises(ui.MappingError):ui.unchanged(xml(),png(),newxml,newpng,{'text':'도움말'})
    def test_empty_xml_cannot_prove_absence(self):
        with self.assertRaises(ui.MappingError):ui.select('<hierarchy/>',{'text':'absent'},optional=True)
    def test_ambiguous_selector_rejected(self):
        x=xml().replace('</hierarchy>','<node text="도움말"/></hierarchy>')
        with self.assertRaises(ui.MappingError):ui.select(x,{'text':'도움말'})

class ResultApiTests(unittest.TestCase):
    def test_legacy_success_not_promoted_and_original_unchanged(self):
        old={'run_id':'old','results':[{'index':1,'status':'passed','message':'old'}]}
        before=copy.deepcopy(old);new=er.normalize(old)
        self.assertEqual(old,before);self.assertEqual(new['method'],'legacy_unknown')
        self.assertEqual(new['results'][0]['status'],'unverified');self.assertEqual(new['counts']['passed'],0)
    def test_manual_observation_is_not_automatic_device_run(self):
        old={'verification_method':'operator-reviewed-local','results':[{'status':'passed'}]}
        self.assertEqual(er.normalize(old)['method'],'manual_observation')
    def test_history_empty_corrupt_and_duplicate_run(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'history.jsonl';self.assertEqual(er.read_history(p),([],[]))
            p.write_text('bad\n'+json.dumps({'run_id':'same','results':[]})+'\n'+json.dumps({'run_id':'same','results':[{'status':'blocked'}]})+'\n')
            runs,warnings=er.read_history(p);self.assertEqual(len(runs),1);self.assertTrue(warnings)
    def test_api_empty_remote_filter_source_identity(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m,'TEST_RUN_HISTORY_FILE',Path(d)/'empty.jsonl'),patch.object(m,'_verified_run',None),patch.object(m,'_reviewed_plan',None):
            c=m.app.test_client();data=c.get('/api/execution/results').get_json()
            self.assertEqual(data['history'],[]);self.assertIsNone(data['current'])
            self.assertEqual(c.get('/api/execution/results',environ_base={'REMOTE_ADDR':'10.1.2.3'}).status_code,403)
            s,_=fixture()
            with patch.object(m,'_test_steps',[s]),patch.object(m,'_test_metadata',META):
                row=c.get('/api/execution/sources').get_json()['cases'][0]
                self.assertEqual(row['source_digest'],er.fingerprint(er.source(s,META)))
    def test_invalid_or_stale_api_start_never_starts_thread(self):
        s,mp=fixture();c=m.app.test_client()
        with patch.object(m,'_test_steps',[s]),patch.object(m,'_test_metadata',META),patch.object(m,'_test_running',False),patch.object(m,'_random_running',False),patch.object(m,'_record_play_running',False),patch.object(m.threading,'Thread') as thread:
            payload={'upload_id':'wrong','file_hash':META['file_hash'],'selection':[ve.reviewed_tc.case_key(s)],'mappings':[mp]}
            self.assertEqual(c.post('/api/execution/run',json=payload).status_code,409)
            payload['upload_id']=META['upload_id'];payload['mappings'][0]['stages']=[]
            self.assertEqual(c.post('/api/execution/run',json=payload).status_code,400)
            thread.assert_not_called()
    def test_legacy_narrative_input_route_cannot_bypass_reviewed_mapping(self):
        with patch.object(m,'_test_steps',[{'action':'tap','source':'narrative_qa','target':'삭제'}]),patch.object(m,'_test_workbook',None),patch.object(m,'check_device') as device:
            self.assertEqual(m.app.test_client().post('/api/run',json={}).status_code,409)
            device.assert_not_called()

    def test_api_success_export_preserves_unselected_original(self):
        from test_result_workbook import fixture as excel_fixture, read
        import result_workbook as rw
        payload,steps=excel_fixture()
        steps[0].update(test_item=SOURCES[0],precondition='메인 표시',expected='내용 표시')
        template=rw.prepare_template(payload,'fixture.xlsx',steps)
        metadata={**META,'file_hash':template.file_hash}
        _,mapping=fixture()
        mapping['source_digest']=er.fingerprint(er.source(steps[0],metadata))
        device=FakeDevice()
        class ImmediateThread:
            def __init__(self,target,**kwargs):self.target=target
            def start(self):self.target()
        with patch.object(m,'_test_steps',steps),patch.object(m,'_test_metadata',metadata),patch.object(m,'_test_workbook',template),patch.object(m,'_test_running',False),patch.object(m,'_random_running',False),patch.object(m,'_record_play_running',False),patch.object(m,'_verified_run',None),patch.object(m,'_last_workbook_run',None),patch.object(m.threading,'Thread',ImmediateThread),patch.object(ve,'DeviceAdapter',return_value=device),patch.object(m,'append_test_run_history') as history:
            c=m.app.test_client()
            response=c.post('/api/execution/run',json={'upload_id':metadata['upload_id'],'file_hash':metadata['file_hash'],'selection':[ve.reviewed_tc.case_key(steps[0])],'mappings':[mapping]})
            self.assertEqual(response.status_code,202,response.get_json())
            self.assertEqual(m._last_workbook_run.statuses,('passed',)+('preserved',)*5)
            output=read(rw.export_run(m._last_workbook_run,metadata['file_hash']))
            self.assertEqual(output['TestCase']['K6'].value,'Pass')
            self.assertTrue(all(output['TestCase'][f'K{r}'].value is None for r in range(7,12)))
            output.close()
            first=response.get_json()['run_id']
            again=c.post('/api/execution/run',json={'upload_id':metadata['upload_id'],'file_hash':metadata['file_hash'],'selection':[ve.reviewed_tc.case_key(steps[0])],'mappings':[mapping]})
            self.assertNotEqual(first,again.get_json()['run_id'])
            self.assertEqual(c.get('/api/execution/results').get_json()['current']['run_id'],again.get_json()['run_id'])
            self.assertEqual(history.call_count,2)
    def test_stop_during_planning_rejects_late_ai_response(self):
        from test_result_workbook import fixture as excel_fixture
        import result_workbook as rw
        payload,steps=excel_fixture();steps[0].update(test_item=SOURCES[0],expected='도움말 표시')
        template=rw.prepare_template(payload,'fixture.xlsx',steps)
        def response(*a,**k):
            m._stop_generation+=1
            return {'plans':[{'index':1,'actions':[{'action':'tap','target':'도움말','source':SOURCES[0]}]}]},{}
        with patch.object(m,'_test_steps',steps),patch.object(m,'_test_metadata',META),patch.object(m,'_test_workbook',template),patch.object(m,'_test_running',False),patch.object(m,'_random_running',False),patch.object(m,'_record_play_running',False),patch.object(m,'_stop_generation',0),patch.object(m,'request_test_ai_json',side_effect=response),patch.object(m,'_reviewed_plan',None):
            r=m.app.test_client().post('/api/reviewed/plans',json={'file_hash':template.file_hash,'selection':[ve.reviewed_tc.case_key(steps[0])],'ai_mode':True})
            self.assertEqual(r.status_code,409)
            self.assertIn('Stop',r.get_json()['error'])
            self.assertIsNone(m._reviewed_plan)
    def test_common_history_write_preserves_legacy_payload(self):
        with tempfile.TemporaryDirectory() as d,patch.object(m,'QA_RESULTS_DIR',Path(d)),patch.object(m,'TEST_RUN_HISTORY_FILE',Path(d)/'runs.jsonl'):
            old={'run_id':'old-format','results':[{'index':1,'status':'passed','message':'sent'}]}
            m.append_test_run_history(old)
            saved=json.loads(m.TEST_RUN_HISTORY_FILE.read_text(encoding='utf-8'))
            self.assertEqual(saved['schema_version'],2)
            self.assertEqual(saved['compatibility_record'],old)
            self.assertEqual(saved['results'][0]['status'],'unverified')

    def test_empty_ui_legacy_absence_assertion_is_block(self):
        with patch.object(m,'dump_ui_xml',return_value=''),patch.object(m,'ocr_screen_texts',return_value=[]):
            m._test_stop_event.clear()
            with self.assertRaises(m.BlockedStep):m.execute_step({'action':'assert_not_text','target':'missing'})
    def test_successful_reupload_invalidates_previous_live_run(self):
        from test_result_workbook import fixture as excel_fixture
        import result_workbook as rw
        payload,steps=excel_fixture();template=rw.prepare_template(payload,'fixture.xlsx',steps)
        with patch.object(m,'_verified_run',object()),patch.object(m,'_reviewed_plan',None),patch.object(m,'_last_workbook_run',object()),patch.object(m,'_test_running',False),patch.object(m,'_test_steps',[]),patch.object(m,'_test_metadata',{}),patch.object(m,'_test_workbook',None),patch.object(m,'parse_excel',return_value=steps),patch.object(m,'recorder_resolution_status',return_value={}):
            response=m.app.test_client().post('/api/testcase',data={'file':(io.BytesIO(payload),'fixture.xlsx')})
            self.assertEqual(response.status_code,200,response.get_json())
            self.assertIsNone(m._verified_run);self.assertIsNone(m._last_workbook_run)
    def test_failed_upload_preserves_previous_live_record(self):
        old=object()
        with patch.object(m,'_verified_run',old):
            r=m.app.test_client().post('/api/testcase',data={'file':(io.BytesIO(b'invalid'),'broken.xlsx')})
            self.assertEqual(r.status_code,400);self.assertIs(m._verified_run,old)

    def test_dashboard_is_integrated_and_contains_no_seed_measurements(self):
        c=m.app.test_client();self.assertIn(b'/execution-dashboard.js',c.get('/').data)
        js=c.get('/execution-dashboard.js').get_data(as_text=True)
        self.assertIn('software_test',js);self.assertIn('requestSerial',js);self.assertNotIn('FIX-001',js)

if __name__=='__main__':unittest.main()

