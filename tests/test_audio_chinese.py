import unittest
from unittest.mock import patch
import audio_service as service
from audio_verification import classify, normalize

class ChineseTests(unittest.TestCase):
    def test_pinyin_requires_review(self):
        self.assertEqual(classify('māma','妈妈',duration=3,language='zh')['status'],'review')
    def test_chinese_match(self):
        self.assertEqual(classify('你好！','你好',duration=3,language='zh')['status'],'match')
    def test_no_japanese_normalization(self):
        self.assertEqual(normalize('一カ',language='zh'),'一カ')
        self.assertEqual(classify('你好','你好か',duration=3,language='zh')['status'],'review')
    def test_catalog_and_mapping(self):
        with patch.object(service,'fetch',side_effect=[b'<div onclick="func.goWeek(1)"></div>',b'<a href="cday.php?l=1&w=1&d=1">Day</a>']):
            units=service.discover_catalog('chinese')
        self.assertEqual(units[0]['subject'],'chinese')
        html='<div class="playlist-item" audio_url="seoch.php?Id=1&amp;app=cn"><div class="playlist-subtitle">你好</div></div>'
        self.assertEqual(service.parse_items(html,units[0]['url'],'chinese')[0]['expected'],'你好')
        with self.assertRaises(ValueError):service.parse_items(html,units[0]['url'],'japanese')

if __name__=='__main__':unittest.main()
