import unittest
from unittest.mock import patch
import audio_service as service
from audio_verification import classify, normalize


class TravelEnglishTests(unittest.TestCase):
    def test_english_case(self):
        self.assertEqual(normalize('Towel', language='en'), 'towel')
        self.assertEqual(classify('I have a reservation.', 'i have a reservation', duration=3, language='en')['status'], 'match')
        self.assertEqual(classify('towel', 'towel', duration=1, language='en')['status'], 'review')

    def test_different_words_not_passed(self):
        self.assertEqual(classify('I need a towel', 'I need a table', duration=3, language='en')['status'], 'review')

    def test_catalog(self):
        with patch.object(service, 'fetch', side_effect=[b'<div onclick="func.goWeek(1)"></div>', b'<a href="tday.php?l=1&w=1&d=1">Topic 1</a><a href="jday.php?l=1&w=1&d=2">Other</a>']):
            units = service.discover_catalog('travel-english')
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0]['title'], 'Topic 1')
        self.assertEqual(units[0]['subject'], 'travel-english')
        self.assertIn(service.SUBJECTS['travel-english']['level_names']['1'], units[0]['label'])

    def test_audio_mapping(self):
        def html(app):
            return '<div class="playlist-item" audio_url="seoch.php?Id=1&amp;app='+app+'"><div class="playlist-subtitle">Towel</div><div class="playlist-description">translation</div></div>'
        url = service.BASE+'tday.php?l=1&w=1&d=1'
        for app in ['trip_w', 'trip_c']:
            self.assertEqual(service.parse_items(html(app), url, 'travel-english')[0]['expected'], 'Towel')
        for app in ['jp', 'cn', 'trip_c&amp;app=trip_w']:
            with self.assertRaises(ValueError):
                service.parse_items(html(app), url, 'travel-english')
        self.assertEqual(service.SUBJECTS['travel-english']['language'], 'en')


if __name__ == '__main__':
    unittest.main()
