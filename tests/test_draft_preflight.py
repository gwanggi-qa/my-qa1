import unittest
from test_tc_drafts import DraftTests, result
from app_analysis import AnalysisError
import draft_batches
import tc_drafts as d


class PilotTests(DraftTests):
    def test_full_run_requires_same_source_pilot(self):
        self.settings['count'] = 0
        with self.assertRaises(AnalysisError):
            self.service.start(self.sha, self.request())
        self.settings['count'] = 3
        pilot = self.generate()
        self.assertTrue(pilot['cases'])
        context = self.service.context(self.sha, {**self.settings, 'count': 0})[0]
        self.assertFalse(self.service.pilot_ready(self.sha, context))
        c = pilot['cases'][0]
        self.service.review(self.sha, pilot['id'], c['id'],
                            {**{k:c[k] for k in d.FIELDS}, 'review_status':'approved', 'revision':0})
        self.assertTrue(self.service.pilot_ready(self.sha, context))
        self.assertFalse(self.service.pilot_ready(self.sha, {**context, 'scope': 'different'}))

    def test_empty_pilot_is_not_success_or_permission_to_expand(self):
        self.service.runner = lambda *a: ({'summary': 'No evidence', 'questions': ['Need data'], 'cases': []}, {})
        job = self.generate()
        self.assertEqual(job['outcome'], 'no_cases')
        self.assertEqual(self.service.list(self.sha)[0]['outcome'], 'no_cases')
        self.settings['count'] = 0
        with self.assertRaises(AnalysisError):
            self.service.start(self.sha, self.request())

    def test_repeated_empty_chunks_stop_without_resume(self):
        pilot = self.generate()
        c = pilot['cases'][0]
        self.service.review(self.sha, pilot['id'], c['id'],
                            {**{k:c[k] for k in d.FIELDS}, 'review_status':'approved', 'revision':0})
        self.settings['count'] = 0
        calls = []
        def runner(instructions, context, metrics, schema, *args):
            calls.append(context)
            if 'units' in schema['properties']:
                return {'units': [{'title': str(i), 'source_ids': [context['evidence'][0]['id']]} for i in range(20)]}, {}
            return {'summary': 'Missing', 'questions': ['Need evidence'], 'cases': []}, {}
        self.service.runner = runner
        job = self.generate()
        self.assertEqual(len(calls), 3)  # One plan, two empty chunks; not twenty calls.
        self.assertEqual(job['outcome'], 'material_stop')
        self.assertEqual(job['progress']['completed'], 2)
        self.assertFalse(job['progress']['resumable'])
        with self.assertRaises(AnalysisError):
            draft_batches.resume(self.service, self.sha, job['id'])


if __name__ == '__main__':
    unittest.main()
