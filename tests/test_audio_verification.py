import unittest
from audio_verification import classify


class AudioComparisonTests(unittest.TestCase):
    def test_normalization(self):
        self.assertEqual(classify('４月５日。', '4月5日')['status'], 'match')

    def test_short_requires_review(self):
        self.assertEqual(classify('スキー', '好き', duration=1.2)['status'], 'review')

    def test_question_mismatch(self):
        self.assertEqual(classify('４月５日にピクニックに行きます。',
                                  '4月5日に何をしますか')['status'], 'mismatch_candidate')

    def test_missing_audio(self):
        self.assertEqual(classify('a', 'a', audio_available=False)['status'], 'unavailable')

    def test_numbers_not_ignored(self):
        self.assertEqual(classify('4月5日', '4月6日')['status'], 'mismatch_candidate')

    def test_japanese_and_arabic_numerals_are_equivalent(self):
        self.assertEqual(classify('すいかは二つあります。', 'スイカは2つあります')['status'], 'match')
        self.assertEqual(classify('十二時です。', '12時です')['status'], 'match')

    def test_different_japanese_numerals_remain_mismatch(self):
        self.assertEqual(classify('猫が二匹います。', '猫が3匹います')['status'], 'mismatch_candidate')

    def test_missing_number_in_ambiguous_transcript_requires_review(self):
        self.assertEqual(classify('インコを二羽飼っています。', 'インコーに分かっています')['status'], 'review')

    def test_phone_number_separators_do_not_create_false_mismatch(self):
        self.assertEqual(classify('０９０－１２３４－５６７８です。', '090の1234の5678です')['status'], 'review')
