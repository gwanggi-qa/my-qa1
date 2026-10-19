"""Real inputs/network prohibited by the outer harness; all device evidence mocked."""
import copy
import io
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image
import reviewed_tc as review
import result_workbook as rw
import mirror_server as m
from test_result_workbook import fixture, read


class ReviewedTests(unittest.TestCase):
    def setUp(self):
        payload, self.steps = fixture()
        self.steps[0].update(test_item="메인화면에서 도움말 버튼을 클릭한다.", expected="도움말이 출력된다.")
        self.template = rw.prepare_template(payload, "source.xlsx", self.steps)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        png = io.BytesIO(); Image.new("RGB", (100, 100), "blue").save(png, "PNG")
        self.host = SimpleNamespace(_test_workbook=self.template, _test_metadata={"upload_id":"one"},
            _test_running=False, _last_workbook_run=None, _test_stop_event=threading.Event(),
            _adb_input_lock=threading.RLock(), QA_RESULTS_DIR=Path(self.folder.name),
            capture_screen_png=Mock(return_value=png.getvalue()), adb=Mock(return_value=("", "", 0)),
            dump_ui_xml=Mock(return_value='<hierarchy><node text="도움말" package="fixture" class="Button" clickable="true" enabled="true" bounds="[10,10][40,40]"/></hierarchy>'),
            socketio=SimpleNamespace(emit=Mock()), append_test_run_history=Mock(),
            qa_result_screenshot_url=lambda p:'/local/'+p.name, workbook_export_info=lambda s:{"run_id":s.run_id})
        self.plan = review.ReviewedRun(self.host,self.template,self.host._test_metadata,self.steps,[review.case_key(self.steps[0])])

    def approve(self):
        self.plan.approve({"plan_digest":self.plan.plan_digest,"reviewed":True})

    def execute(self):
        obs=self.plan.observe()
        self.plan.execute({"observation_token":obs["token"],"x":20,"y":20,"target_confirmed":True,
            "prerequisites_confirmed":True,"prerequisite_evidence":"Home visible; help icon confirmed"})

    def verdict(self,status="met",reason=""):
        obs=self.plan.observe()
        return self.plan.verdict({"observation_token":obs["token"],"findings":[{"id":"whole","status":status,"evidence":"Observed help contents"}],"reason":reason})

    def test_explicit_unique_original_selection(self):
        for selected in ([],[review.case_key(self.steps[0])]*2,[{"sheet":"TestCase","row":999,"case_no":"TSCL-001"}]):
            with self.assertRaises(ValueError):review.selected_rows(self.steps,selected)

    def test_unapproved_and_tampered_plan_cannot_input(self):
        with self.assertRaises(ValueError):self.plan.observe()
        self.approve();self.plan.cases[0]["actions"][0]["target"]="delete"
        with self.assertRaises(ValueError):self.execute()
        self.host.adb.assert_not_called()

    def test_ai_addition_omission_wrong_target_coordinates_blocked(self):
        for actions in ([],[{"action":"tap","target":"삭제","source":self.steps[0]["test_item"]}],
                        [{**self.plan.cases[0]["actions"][0],"x":20}],self.plan.cases[0]["actions"]*2):
            self.assertTrue(review.make_case(1,self.steps[0],actions)["blocked_reason"])

    def test_unknown_extra_procedure_and_risky_preconditions_blocked(self):
        for change in ({"test_item":"메인화면에서 도움말 버튼을 클릭한다. 계속 누른다."}, {"precondition":"데이터 삭제 후"}):
            self.assertTrue(review.make_case(1,{**self.steps[0],**change})["blocked_reason"])

    def test_source_steps_frozen_and_reupload_invalidates(self):
        self.steps[0]["case_no"]="changed"
        self.assertEqual(self.plan.steps[0]["case_no"],"TSCL-001")
        self.host._test_metadata={"upload_id":"two"}
        with self.assertRaises(ValueError):self.approve()

    def test_stop_before_input_and_replay_rejected(self):
        self.approve();self.host._test_stop_event.set()
        with self.assertRaises(ValueError):self.execute()
        self.host.adb.assert_not_called()
        self.host._test_stop_event.clear();self.execute()
        with self.assertRaises(ValueError):self.execute()
        self.assertEqual(self.host.adb.call_count,1)

    def test_changed_screen_cannot_input(self):
        self.approve();obs=self.plan.observe();self.host.capture_screen_png.return_value=b"changed"
        with self.assertRaises(ValueError):self.plan.execute({"observation_token":obs["token"],"x":1,"y":1,
            "target_confirmed":True,"prerequisites_confirmed":True,"prerequisite_evidence":"home"})
        self.host.adb.assert_not_called()

    def test_uncertain_adb_never_retries_or_passes(self):
        self.approve();self.host.adb.return_value=("","",1)
        with self.assertRaises(RuntimeError):self.execute()
        with self.assertRaises(ValueError):self.execute()
        self.assertEqual(self.host.adb.call_count,1)
        self.assertEqual(self.verdict(reason="ADB outcome uncertain")["results"][0]["status"],"blocked")

    def test_no_procedure_cannot_pass(self):
        self.approve()
        self.assertEqual(self.verdict(reason="procedure not executed")["results"][0]["status"],"blocked")

    def test_pass_and_export_preserves_unselected_cells(self):
        self.approve();self.execute();self.verdict()
        snapshot=self.host._last_workbook_run
        self.assertEqual(snapshot.statuses, ("passed",)+( "preserved",)*5)
        output=read(rw.export_run(snapshot,self.template.file_hash))
        self.assertEqual(output["TestCase"]["K6"].value,"Pass")
        self.assertTrue(all(output["TestCase"][f"K{r}"].value is None for r in range(7,12)))
        output.close()
        self.assertEqual(self.host.append_test_run_history.call_args.args[0]["verification_method"],"operator-reviewed-local")

    def test_missing_facet_or_empty_evidence_never_passes(self):
        self.plan.cases[0]["requirements"].append({"id":"second","source":"another condition"})
        self.plan.plan_digest=review.digest(self.plan.cases)
        self.approve();self.execute()
        with self.assertRaises(ValueError):self.verdict()
        self.assertEqual(self.plan.results,[])

    def test_preparation_back_bounded_and_not_tc_credit(self):
        self.approve()
        for _ in range(2):
            obs=self.plan.observe();self.plan.prepare_back({"observation_token":obs["token"],"safe_dismissal_confirmed":True,"evidence":"Help popup, no pending change"})
        obs=self.plan.observe()
        with self.assertRaises(ValueError):self.plan.prepare_back({"observation_token":obs["token"],"safe_dismissal_confirmed":True,"evidence":"Help popup"})
        self.assertEqual(self.plan.action_cursor,0)
        self.assertEqual(self.verdict(reason="TC input missing")["results"][0]["status"],"blocked")

    def test_stop_finishes_selected_only(self):
        self.approve();self.host._test_stop_event.set();self.plan.finish(stopped=True)
        self.assertEqual(self.host._last_workbook_run.statuses,("stopped",)+("preserved",)*5)
        with self.assertRaises(ValueError):self.plan.observe()

    def test_remote_review_and_manual_bypass_rejected(self):
        client=m.app.test_client()
        self.assertEqual(client.post('/api/reviewed/plans',json={},environ_base={"REMOTE_ADDR":"10.1.2.3"}).status_code,403)
        self.approve()
        with patch.object(m,"_reviewed_plan",self.plan):
            self.assertEqual(client.post('/api/input',json={"action":"tap","x":1,"y":1}).status_code,409)


    def test_identical_image_never_allows_tap_outside_approved_target(self):
        self.approve();obs=self.plan.observe()
        with self.assertRaises(ValueError):
            self.plan.execute({"observation_token":obs["token"],"x":70,"y":20,"target_confirmed":True,
                "prerequisites_confirmed":True,"prerequisite_evidence":"home"})
        self.host.adb.assert_not_called()

    def test_identical_image_without_ui_target_evidence_cannot_input(self):
        self.approve();self.host.dump_ui_xml.return_value=''
        with self.assertRaises(ValueError):self.execute()
        self.host.adb.assert_not_called()

if __name__ == '__main__':unittest.main()
