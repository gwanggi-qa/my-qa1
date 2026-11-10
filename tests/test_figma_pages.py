"""Page import and evidence propagation without Figma/AI network calls."""
import json
import tempfile
import unittest
from unittest.mock import patch

import app_analysis as a
import figma_import as f
import reference_materials as r
import tc_drafts as d
from test_app_analysis import apk


def page():
    return {'name': 'Learning design', 'version': '123', 'nodes': {'0:1': {'document': {
        'id': '0:1', 'name': 'Learning page', 'type': 'CANVAS',
        'flowStartingPoints': [{'nodeId': '1:1', 'name': 'Study'}],
        'children': [
            {'id': '1:0', 'type': 'SECTION', 'name': 'Study section', 'children': [
                {'id': '1:1', 'type': 'FRAME', 'name': 'Words', 'children': [
                    {'id': '1:2', 'type': 'TEXT', 'name': 'Next', 'characters': 'Next lesson',
                     'interactions': [{'trigger': {'type': 'ON_CLICK'}, 'actions': [
                         {'type': 'NODE', 'navigation': 'NAVIGATE', 'destinationId': '2:1'}]}]}]},
                {'id': '2:1', 'type': 'FRAME', 'name': 'Sentences', 'children': [
                    {'id': '2:2', 'type': 'TEXT', 'name': 'Sentence', 'characters': 'Read aloud'}]}]},
            {'id': '3:1', 'type': 'RECTANGLE', 'name': 'Screenshot', 'fills': [{'type': 'IMAGE'}]},
            {'id': '4:1', 'type': 'FRAME', 'name': 'Hidden', 'visible': False, 'children': [
                {'id': '4:2', 'type': 'FRAME', 'name': 'Hidden child'}]}]}}}}


class FigmaPageTests(unittest.TestCase):
    def test_page_structure_flow_and_image_gaps(self):
        p = f.normalize(page(), '0:1')
        self.assertEqual(p['scope'], 'page')
        self.assertEqual([s['id'] for s in p['screens']], ['1:1', '2:1', '3:1'])
        self.assertTrue(p['screens'][2]['image_only'])
        self.assertEqual(p['flows'][0]['screen'], '1:1')
        self.assertTrue(p['flows'][0]['destination_in_page'])
        self.assertEqual(p['flow_starts'][0]['nodeId'], '1:1')
        self.assertEqual(p['status'], 'partial')
        self.assertIn('이미지로만', ' '.join(p['warnings']))
        self.assertIn('Read aloud', json.dumps(list(d.source_lines(p))))

    def test_no_connections_is_not_invented_order(self):
        data = page()
        first = data['nodes']['0:1']['document']['children'][0]['children'][0]['children'][0]
        first['interactions'] = []
        p = f.normalize(data, '0:1')
        self.assertEqual(p['flows'], [])
        self.assertIn('실제 학습 순서', ' '.join(p['warnings']))

    def test_external_destination_and_legacy_links(self):
        data = page()
        node = data['nodes']['0:1']['document']['children'][2]
        node['transitionNodeID'] = '99:9'
        p = f.normalize(data, '0:1')
        self.assertFalse(p['flows'][-1]['destination_in_page'])
        self.assertEqual(p['flows'][-1]['trigger'], 'UNSPECIFIED')

    def test_limit_and_malformed_tree(self):
        with patch.object(f, 'MAX_PAGE_NODES', 3):
            p = f.normalize(page(), '0:1')
        self.assertEqual(len(p['nodes']), 3)
        self.assertIn('Figma node/text preview truncated.', p['warnings'])
        data = page()
        data['nodes']['0:1']['document']['children'] = None
        with self.assertRaises(a.AnalysisError):
            f.normalize(data, '0:1')

    def test_import_persists_page_and_missing_image(self):
        with tempfile.TemporaryDirectory() as folder:
            store = a.AnalysisStore(folder)
            sha = store.register(apk(), 'fixture.apk')[0]['sha256']
            refs = r.ReferenceStore(store)
            with patch.dict(f.os.environ, {'FIGMA_ACCESS_TOKEN': 'fixture-secret'}), patch.object(
                    f, 'api_json', side_effect=[page(), {'images': {}}]) as api:
                saved, duplicate = f.import_frame(refs, sha, 'https://figma.com/design/abc/Page?node-id=0-1')
            self.assertFalse(duplicate)
            self.assertEqual(saved['preview']['scope'], 'page')
            self.assertNotIn('fixture-secret', json.dumps(saved))
            self.assertEqual(api.call_args_list[1].args[1]['version'], '123')
            settings = {'reference_ids': [saved['id']], 'scope': 'Study flow', 'count': 0, 'include_images': False}
            context, _, _ = d.make_context(store, refs, sha, settings, {'provider': 'fixture'})
            evidence = json.dumps(context['evidence'])
            self.assertIn('Read aloud', evidence)
            self.assertIn('destination_in_page', evidence)
            self.assertTrue(context['warnings'])

    def test_later_screens_not_lost_at_old_10k_limit(self):
        data = page()
        root = data['nodes']['0:1']['document']
        root['children'] = [
            {'id': f'{i}:0', 'type': 'FRAME', 'name': 'Screen ' + str(i), 'children': [
                {'id': f'{i}:1', 'type': 'TEXT', 'characters': 'Content ' + str(i) + 'x' * 800}]}
            for i in range(1, 41)]
        preview = f.normalize(data, '0:1')
        with tempfile.TemporaryDirectory() as folder:
            store = a.AnalysisStore(folder)
            sha = store.register(apk(), 'fixture.apk')[0]['sha256']
            refs = r.ReferenceStore(store)
            ref, _ = refs.persist(sha, json.dumps(preview).encode(), 'page.json', 'design', '', preview, None)
            context, _, _ = d.make_context(store, refs, sha, {
                'reference_ids': [ref['id']], 'scope': 'All screens', 'count': 0, 'include_images': False}, {})
            self.assertTrue(any('Content 40' in e['text'] for e in context['evidence']))
            self.assertFalse(any('source excerpts truncated' in w for w in context['warnings']))

    def test_page_image_uses_bounded_scale_and_version(self):
        data = page()
        frame = data['nodes']['0:1']['document']['children'][0]['children'][0]
        frame['absoluteBoundingBox'] = {'x': 0, 'y': 0, 'width': 20000, 'height': 10000}
        with tempfile.TemporaryDirectory() as folder:
            store = a.AnalysisStore(folder)
            sha = store.register(apk(), 'fixture.apk')[0]['sha256']
            with patch.dict(f.os.environ, {'FIGMA_ACCESS_TOKEN': 'fixture'}), patch.object(
                    f, 'api_json', side_effect=[data, {'images': {}}]) as api:
                f.import_frame(r.ReferenceStore(store), sha, 'https://figma.com/design/abc/Page?node-id=0-1')
            self.assertEqual(api.call_args_list[1].args[1]['scale'], .15)


if __name__ == '__main__':
    unittest.main()
