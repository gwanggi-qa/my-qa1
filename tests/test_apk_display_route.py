import unittest
from unittest.mock import Mock,patch
from xml.etree.ElementTree import Element,SubElement
import apk_display_route as a


def screen(*ids):
    root=Element('hierarchy')
    for key in ids:
        SubElement(root,'node',{'package':a.PACKAGE,'resource-id':a.PACKAGE+':id/'+key,'text':'',
                                'enabled':'true','clickable':'true','bounds':'[0,0][20,20]'})
    return root


class DisplayRouteTests(unittest.TestCase):
    def test_menu_and_identity(self):
        root=screen(*a.SCREENS['menu_schedule_new'][1])
        self.assertEqual(a.route(root,'MenuScheduleNewActivity','menu_main')[0],'back')
        home=screen(*a.SCREENS['menu_main'][1],'cvNote')
        self.assertEqual(a.route(home,'MainActivity','menu_note'),('tap','cvNote'))
        with self.assertRaises(ValueError):a.route(home,'MenuNoteActivity','menu_note')

    def test_expected_value_not_a_landmark(self):
        root=screen(*a.SCREENS['menu_schedule_new'][1],'tvLevelTitle')
        root[-1].set('text','Level 2')
        self.assertEqual(a.route(root,'MenuScheduleNewActivity','menu_schedule_new')[0],'arrived')
        self.assertEqual(a.observe(root,{'screen':'menu_schedule_new','view':'tvLevelTitle','label':'Level 1'}),'Level 2')

    def test_update_cancel_only(self):
        root=screen('popup_content','btn_sel1','btn_sel2')
        root[0].set('text',a.UPDATE);root[1].set('text','확인');root[2].set('text','취소')
        self.assertEqual(a.route(root,'MenuSettingActivity','menu_setting'),('tap','btn_sel2'))
        root[0].set('text','초기화하시겠습니까?')
        with self.assertRaises(ValueError):a.route(root,'MenuSettingActivity','menu_setting')

    def test_no_repeat_on_ineffective_back(self):
        w=Mock(); w.host.BlockedStep=ValueError;w.host.adb_text.return_value='versionCode=7'
        import threading
        w.host._adb_input_lock=threading.Lock();w.host.adb.return_value=(b'',b'',0)
        w.cancel.wait.return_value=False
        root=screen(*a.SCREENS['menu_schedule_new'][1])
        with patch.object(a,'snapshot',return_value=(root,'MenuScheduleNewActivity')):
            with self.assertRaisesRegex(ValueError,'재시도'):
                a.navigate(w,{'_display_spec':{'screen':'menu_main'},'_display_build':'7'},{'stages':[]})
        self.assertEqual(w.host.adb.call_count,1)
