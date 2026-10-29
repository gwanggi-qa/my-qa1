import unittest
from unittest.mock import patch
import audio_service as service

class ToeicTests(unittest.TestCase):
    def test_catalog_without_week(self):
        with patch.object(service,'fetch',side_effect=[b'<div onclick="func.goWeek(1)"></div>', b'<a href="toeday.php?l=1&d=1">Unit 1</a>']):
            units=service.discover_catalog('toeic')
        self.assertEqual(units[0]['url'],service.BASE+'toeday.php?l=1&d=1')
    def test_all_accents(self):
        attrs=' '.join('data-url-'+a+'="seoch.php?app=toe_'+a+'"' for a in ['us','uk','au','ca'])
        html='<div class="playlist-item" '+attrs+'><div class="playlist-subtitle">budget</div></div>'
        items=service.parse_items(html,service.BASE+'toeday.php?l=1&d=1','toeic')
        self.assertEqual(len(items),4)
        self.assertEqual(len({x['source_url'] for x in items}),4)
        with self.assertRaises(ValueError):
            service.parse_items(html.replace('app=toe_uk','app=jp'),service.BASE,'toeic')
