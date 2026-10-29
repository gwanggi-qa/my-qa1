"""Build two read-only card flip fixture TCs; not the user's workbook."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import tc_format

rows=[]
for number,initial,expected in [(1,'표시되지 않는다','표시된다'),(2,'표시된다','표시되지 않는다')]:
    step={'case_no':f'LIB-{number:03}','title':'카드 뒤집기 문구 확인','priority':'P1',
          'precondition':f'"press release"가 {initial}\n"보도자료"가 표시된다',
          'test_item':'1. "카드 뒤집기" 버튼을 클릭한다.',
          'expected':f'"press release"가 {expected}\n"보도자료"가 표시된다',
          'note':'대표 TC v2: 앞면 영문 표제어 표시, 뒷면 영문 표제어 미표시. 기존 실패 회차 보존.'}
    rows.append(tc_format.step_row(step))
Path('qa_results/library-smoke.xlsx').write_bytes(tc_format.save(tc_format.workbook(rows)))
