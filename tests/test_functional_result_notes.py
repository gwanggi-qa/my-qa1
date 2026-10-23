import copy
import io
import unittest
from openpyxl import load_workbook
import tc_format
import tc_workflow as w
import result_workbook as rw


class FunctionalNotesTests(unittest.TestCase):
    def test_document_scope_preserves_source_and_explicit_comparison(self):
        source={'test_item':'1. 개인정보 본문을 홈페이지와 비교한다.',
                'expected':'본문이 표시된다.','case_no':'DTC-010'}
        old=copy.deepcopy(source)
        effective=w.functional_step(source)
        self.assertEqual(source,old)
        self.assertNotIn('비교',effective['test_item'])
        self.assertIn('_scope_note',effective)
        source['expected']='홈페이지와 본문이 일치한다.'
        self.assertEqual(w.functional_step(source),source)

    def test_notes_export_keeps_original_note_and_other_rows(self):
        steps=[{'case_no':f'TC-{i}','sheet':'TestCase','row':i+1,'note':'원래 비고'} for i in (1,2)]
        raw=tc_format.save(tc_format.workbook(tc_format.step_row(s) for s in steps))
        template=rw.prepare_template(raw,'test.xlsx',steps)
        snapshot=rw.finish_run('a'*32,template,steps,[{'index':1,'status':'blocked','note':'Block: 대상 없음'}],selected_indices=[1])
        book=load_workbook(io.BytesIO(rw.export_run(snapshot,template.file_hash)))
        self.assertEqual(book['TestCase']['M2'].value,'BLOCK')
        self.assertEqual(book['TestCase']['N2'].value,'원래 비고\nBlock: 대상 없음')
        self.assertEqual(book['TestCase']['N3'].value,'원래 비고')
        book.close()

    def test_result_note_contains_input_count_and_block_reason(self):
        text=w.result_note({'status':'blocked','input_count':2,'reason':'기기 환경 부족'}, {'_scope_note':'본문 확인'})
        self.assertIn('2회',text)
        self.assertIn('Block 사유: 기기 환경 부족',text)
        self.assertIn('본문 확인',text)
