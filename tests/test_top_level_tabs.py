"""Layout and read-only status regressions. No device or execution actions."""
from html.parser import HTMLParser
import unittest
from unittest.mock import patch
import mirror_server as m

class Tree(HTMLParser):
    def __init__(self):
        super().__init__();self.stack=[];self.nodes=[]
    def handle_starttag(self,tag,attrs):
        node={'tag':tag,'attrs':dict(attrs),'parents':list(self.stack)}
        self.nodes.append(node)
        if tag not in {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}:self.stack.append(node)
    def handle_endtag(self,tag):
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i]['tag']==tag:
                self.stack=self.stack[:i];break

class TopLevelTabsTests(unittest.TestCase):
    def setUp(self):
        self.tree=Tree();self.tree.feed(m.HTML)
    def node(self,ident):
        return next(n for n in self.tree.nodes if n['attrs'].get('id')==ident)
    def test_main_grid_has_only_workspace_and_mirror(self):
        root=self.node('test-execution-panel')
        children=[n for n in self.tree.nodes if n['parents'] and n['parents'][-1] is root]
        self.assertEqual([n['attrs'].get('class') for n in children],['workspace','mirror'])
    def test_review_controls_are_inside_existing_tc_pane(self):
        review=self.node('review-panel')
        self.assertEqual(review['tag'],'details')
        self.assertIn(self.node('tab-test-case'),review['parents'])
        self.assertIn(review,self.node('review-ids')['parents'])
    def test_default_test_tab_and_stop_controls_outside_views(self):
        test=self.node('test-execution-tab');dash=self.node('execution-open')
        self.assertEqual(test['attrs']['aria-selected'],'true')
        self.assertEqual(dash['attrs']['aria-selected'],'false')
        self.assertEqual(test['attrs']['aria-controls'],'test-execution-panel')
        self.assertNotIn(self.node('test-execution-panel'),self.node('view-stop-controls')['parents'])
    def test_unique_existing_control_ids_and_source_actions_preserved(self):
        ids=[n['attrs']['id'] for n in self.tree.nodes if 'id' in n['attrs']]
        self.assertEqual(len(ids),len(set(ids)))
        for ident in ['screen','excel-file','btn-test-stop','btn-random-stop','btn-record-stop','btn-record-play-stop','case-list']:
            self.assertIn(ident,ids)
        self.assertEqual(self.node('btn-test-stop')['attrs']['onclick'],'stopTestCase()')
    def test_status_endpoint_is_local_read_only_and_cache_disabled(self):
        with patch.object(m,'_test_running',True),patch.object(m,'_random_running',False),patch.object(m,'_record_play_running',True),patch.object(m,'_streaming',True),patch.object(m,'_test_steps',[{}]),patch.object(m,'adb') as adb:
            client=m.app.test_client();response=client.get('/api/runtime/status')
            self.assertEqual(response.get_json(),{'test_running':True,'random_running':False,'record_play_running':True,'streaming':True,'upload_count':1})
            self.assertEqual(response.headers['Cache-Control'],'no-store')
            self.assertEqual(client.post('/api/runtime/status').status_code,405)
            self.assertEqual(client.get('/api/runtime/status',environ_base={'REMOTE_ADDR':'10.0.0.1'}).status_code,403)
            adb.assert_not_called()

if __name__=='__main__':unittest.main()

