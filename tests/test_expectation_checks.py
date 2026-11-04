import unittest
import expectation_checks as checks


class AssertionTests(unittest.TestCase):
    def evaluate(self, source, attributes):
        return checks.evaluate('<hierarchy><node text="학습" '+attributes+'/></hierarchy>', checks.compile_text(source))[0]

    def test_selected_is_not_presence(self):
        self.assertFalse(self.evaluate('"학습" 탭이 선택된다.', 'selected="false"')['passed'])
        self.assertTrue(self.evaluate('"학습" 탭이 선택된다.', 'selected="true"')['passed'])

    def test_missing_attribute_blocks(self):
        with self.assertRaises(ValueError):
            self.evaluate('"학습"이 선택된다.', '')

    def test_disabled_and_checked(self):
        self.assertTrue(self.evaluate('"학습" 버튼이 비활성화된다.', 'enabled="false"')['passed'])
        self.assertFalse(self.evaluate('"학습"이 체크된다.', 'checked="false"')['passed'])

    def test_hidden_is_not_present(self):
        self.assertFalse(self.evaluate('"학습"이 표시된다.', 'displayed="false"')['passed'])

    def test_no_semantic_weakening(self):
        for s in ['"학습"이 표시되고 선택된다.', '카드가 뒤집힌다.', '이용권이 차감되지 않는다.', '"학습"이 오른쪽에 표시된다.']:
            self.assertIsNone(checks.compile_text(s))

    def test_count_and_ambiguity(self):
        xml='<hierarchy><node text="학습" displayed="true"/><node text="학습" displayed="false"/></hierarchy>'
        self.assertTrue(checks.evaluate(xml,checks.compile_text('"학습"이 1개 표시된다.'))[0]['passed'])
        with self.assertRaises(ValueError):
            checks.evaluate(xml,checks.compile_text('"학습"이 표시된다.'))

    def test_empty_xml_is_not_absence(self):
        with self.assertRaises(ValueError):
            checks.evaluate('<hierarchy/>',checks.compile_text('"학습"이 표시되지 않는다.'))
