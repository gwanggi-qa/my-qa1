import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock

import mirror_server as m


class ReviewScopeTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.file=self.root/'redmine_failures.json'
        for key,value in {'QA_RESULTS_DIR':self.root,'REDMINE_FAILURES_FILE':self.file,
                          '_redmine_review_session':'server-session-1','_redmine_review_run_id':None,
                          'socketio':Mock(),'redmine_api_json':Mock(),'record_linked_retest':Mock()}.items():
            p=patch.object(m,key,value);p.start();self.addCleanup(p.stop)
        self.metadata={'file_hash':'source-hash','filename':'fixture.xlsx'}
        self.step={'case_no':'TC-1','row':2,'action':'assert_text','expected':'설정 표시'}
        self.begin('run-1')

    def begin(self,run_id):
        state=m.redmine_review_state()
        token=m.require_review_reset_confirmation({'review_reset_confirmed':True,'review_reset_token':state['token']})
        m.begin_redmine_review_run(run_id,token)

    def queue(self):
        return m.queue_test_failure('run-1',1,1,self.step,'Missing label',12,
            {'screenshot_file':'failure.png','screenshot_url':'/api/results/screenshot/failure.png'},self.metadata,'standard',device_evidence={})

    def test_next_run_hides_pending_and_linked_but_keeps_evidence_and_result_links(self):
        pending=self.queue();linked=self.queue()
        m.finalize_redmine_failure(linked['id'],{'issue_id':'123','issue_url':'https://redmine.example/issues/123'},'issued')
        evidence=self.root/'failure.png';evidence.write_bytes(b'fixture')
        with self.assertRaises(m.ReviewResetRequired):m.require_review_reset_confirmation({})
        self.assertEqual(len(m.list_redmine_failures()),2)
        self.begin('run-2')
        self.assertEqual(m.list_redmine_failures(),[])
        self.assertEqual(m.redmine_review_state()['pending'],0)
        self.assertEqual(len(json.loads(self.file.read_text(encoding='utf-8'))),2)
        self.assertTrue(evidence.exists())
        runs=[{'run_id':'run-1','results':[{'index':1}]}]
        m.attach_redmine_result_links(runs)
        reviews=runs[0]['results'][0]['redmine_reviews']
        self.assertEqual(reviews[1]['issue']['issue_id'],'123')
        self.assertEqual(reviews[1]['evidence']['screenshot_file'],'failure.png')
        with self.assertRaises(ValueError):m.update_redmine_failure(pending['id'],{'status':'not_an_issue'})
        m.redmine_api_json.assert_not_called()

    def test_restart_empty_without_removing_records_or_contacting_redmine(self):
        self.queue()
        saved=self.file.read_bytes()
        with patch.object(m,'_redmine_review_session','server-session-2'),patch.object(m,'_redmine_review_run_id',None):
            self.assertEqual(m.list_redmine_failures(),[])
            self.assertEqual(m.redmine_review_state()['total'],0)
        self.assertEqual(self.file.read_bytes(),saved)
        m.redmine_api_json.assert_not_called()

    def test_stale_confirmation_and_publish_in_progress_do_not_clear(self):
        item=self.queue();state=m.redmine_review_state()
        m.update_redmine_failure(item['id'],{'subject':'edited'})
        with self.assertRaises(m.ReviewResetRequired):
            m.require_review_reset_confirmation({'review_reset_confirmed':True,'review_reset_token':state['token']})
        with self.assertRaises(ValueError):m.begin_redmine_review_run('run-2',state['token'])
        with m._redmine_publish_lock:
            with self.assertRaises(ValueError):m.begin_redmine_review_run('run-2',m.redmine_review_state()['token'])
        self.assertEqual(len(m.list_redmine_failures()),1)

    def test_issued_only_resets_without_pending_prompt(self):
        item=self.queue()
        m.finalize_redmine_failure(item['id'],{'issue_id':'123'},'issued')
        token=m.require_review_reset_confirmation({})
        m.begin_redmine_review_run('run-2',token)
        self.assertEqual(m.list_redmine_failures(),[])

    def test_new_workflow_failure_captures_source_and_queue_failure_keeps_verdict(self):
        case={'index':1,'status':'failed','actual':'Observed mismatch','reason':'Missing setting',
              'source':{'precondition':'Home','procedure':'Open settings'},
              'evidence':[{'file':'failure.png','url':'/api/results/screenshot/failure.png'}]}
        run={'run_id':'run-1','results':[case],'environment':{'device':{'model':'fixture'},'app':{'package':'fixture.app'}}}
        m.record_tc_review_result(run,case,self.step,self.metadata,'ai')
        queued=m.list_redmine_failures()[0]
        self.assertEqual(queued['test_case']['id'],'TC-1')
        self.assertIn('Open settings',queued['reproduction'])
        self.assertEqual(queued['evidence']['screenshot_file'],'failure.png')
        self.assertEqual(queued['device']['app_package'],'fixture.app')
        with patch.object(m,'queue_test_failure',side_effect=OSError('disk')):
            m.record_tc_review_result(run,case,self.step,self.metadata,'ai')
        self.assertEqual(case['status'],'failed')
        self.assertIn('review_error',case)
        blocked=copy.deepcopy(case);blocked['status']='blocked'
        before=len(m.list_redmine_failures())
        m.record_tc_review_result(run,blocked,self.step,self.metadata,'standard')
        self.assertEqual(len(m.list_redmine_failures()),before)
