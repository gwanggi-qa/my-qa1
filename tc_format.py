"""Shared TestCase column contract for draft export and execution results."""
import io
import math
import unicodedata
import zipfile
import re
import xml.etree.ElementTree as ET

VERSION = 'testcase-v1'
HEADERS = ('통합ID', '제목', '우선순위', '1Depth', '2Depth', '3Depth', 'Type',
           '테스트목적', '사전조건', '테스트절차', '기대결과', '결과', '자동화 결과', '비고')
KEYS = ('case_no', 'title', 'priority', 'depth1', 'depth2', 'depth3', 'check_type',
        'test_purpose', 'precondition', 'test_item', 'expected', 'qa_result', 'automation_result', 'note')
WIDTHS = (16, 30, 12, 20, 20, 20, 18, 48, 40, 56, 56, 14, 18, 48)
STATUS_CODES = {'passed': 'PASS', 'failed': 'FAIL', 'blocked': 'BLOCK',
                'stopped': 'STOPPED', 'not_run': 'NOT RUN', 'not_applicable': 'N/A', 'unverified': 'UNVERIFIED'}


def draft_row(case):
    notes = '검토 승인된 AI 초안 · 미실행\n' + '\n'.join(case['assumptions'])
    notes += '\n시나리오: ' + case['category'] + '\n근거: ' + ', '.join(case['source_ids'])
    return [case['id'], case['title'], case['priority'], *(case.get('depth'+str(i), '') for i in range(1,4)),
            case.get('test_type', ''), case['objective'], case['preconditions'],
            '\n'.join(f'{i}. {s}' for i,s in enumerate(case['steps'],1)),
            '\n'.join(case['expected_results']), '', '', notes]


def step_row(step, status=None):
    values = {**step, 'expected': step.get('qa_expected') or step.get('expected', '')}
    if status is not None:
        values['automation_result'] = STATUS_CODES[status]
    return [str(values.get(key) or '') for key in KEYS]


def append_text(sheet, values):
    sheet.append(values)
    for cell in sheet[sheet.max_row]:
        cell.data_type = 's'


def style_table(sheet, widths):
    from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter
    sheet.freeze_panes = 'A2'
    sheet.auto_filter.ref = sheet.dimensions
    sheet.sheet_view.showGridLines = False
    sheet.row_dimensions[1].height = 28
    for i,width in enumerate(widths,1):
        sheet.column_dimensions[get_column_letter(i)].width = width
    line = Side(style='thin', color='D9E2E6')
    for row in sheet:
        for cell in row:
            cell.font = Font(name='맑은 고딕', size=11, color='FFFFFF' if cell.row==1 else '17252B', bold=cell.row==1)
            cell.alignment = Alignment(vertical='top', wrap_text=True)
            cell.border = Border(bottom=line)
            if cell.row==1:
                cell.fill = PatternFill('solid', fgColor='285E68')
        if row[0].row>1:
            lines=max(sum(max(1,math.ceil(sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in text)/max(1,widths[i]-3)))
                          for text in str(cell.value or '').split('\n')) for i,cell in enumerate(row))
            sheet.row_dimensions[row[0].row].height = min(409, max(32, lines*17+8))


def workbook(rows=()):
    from openpyxl import Workbook
    book = Workbook()
    sheet = book.active
    sheet.title = 'TestCase'
    append_text(sheet, HEADERS)
    for row in rows:
        append_text(sheet, row)
    style_table(sheet, WIDTHS)
    return book


def save(book):
    output = io.BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


def append_execution_details(payload, run):
    """Append evidence without reserializing unrelated source sheets or drawings."""
    from result_workbook import NS, REL, _open_package
    rows=[['통합ID','원본 위치','단계','자동화 결과','절차 / 검증 조건','실제 결과 / 사유','캡처 근거','실행 ID']]
    for case in run['results']:
        source=case['source']
        location=f"{source['sheet']}:{source['row']}"
        entries=[{'number':'TC 결과','status':case['status'],'source':source['expected'],
                  'actual':case['actual'] or case['reason'],'evidence':case['evidence']},*case['stages']]
        for entry in entries:
            rows.append([source['tc_id'],location,str(entry['number']),STATUS_CODES.get(entry['status'],entry['status']),
                         entry.get('source',''),entry.get('actual',''),
                         '\n'.join(dict.fromkeys(e.get('url') or e.get('file') or '' for e in entry.get('evidence',[]))),run['run_id']])
    # Author only the new sheet; existing ZIP members retain their original bytes.
    detail=workbook([])
    sheet=detail.active
    sheet.delete_rows(1)
    for row in rows:
        append_text(sheet,row)
    style_table(sheet,(16,26,14,18,52,60,44,36))
    with zipfile.ZipFile(io.BytesIO(save(detail))) as generated:
        sheet_xml=generated.read('xl/worksheets/sheet1.xml').decode('utf-8')
    with _open_package(payload) as package:
        wb=package.read('xl/workbook.xml').decode('utf-8')
        root=ET.fromstring(wb)
        sheets=root.find(f'{{{NS}}}sheets')
        base_name='실행 상세 '+run['run_id'][:8]
        name=base_name
        used={s.get('name') for s in sheets}
        suffix=1
        while name in used:
            suffix+=1
            name=base_name+' '+str(suffix)
        sheet_id=max(int(s.get('sheetId')) for s in sheets)+1
        i=1
        while f'xl/worksheets/tc-execution-{i}.xml' in package.namelist():
            i+=1
        part=f'xl/worksheets/tc-execution-{i}.xml'
        rels=package.read('xl/_rels/workbook.xml.rels').decode('utf-8')
        rel_ids={e.get('Id') for e in ET.fromstring(rels)}
        rid='rIdTcExecution'+str(i)
        while rid in rel_ids:
            rid+='x'
        def append(xml, tag, content):
            match=re.search(r'</((?:[\w]+:)?'+tag+r')\s*>',xml)
            if not match:
                raise ValueError('원본 Excel 패키지의 '+tag+' 구조를 확인할 수 없습니다.')
            prefix=match[1][:-len(tag)]
            return xml[:match.start()]+content(prefix)+xml[match.start():]
        wb=append(wb,'sheets',lambda p:f'<{p}sheet name="{name}" sheetId="{sheet_id}" xmlns:tcRel="{REL}" tcRel:id="{rid}"/>')
        rels=append(rels,'Relationships',lambda p:f'<{p}Relationship Id="{rid}" Type="{REL}/worksheet" Target="worksheets/tc-execution-{i}.xml"/>')
        ct=package.read('[Content_Types].xml').decode('utf-8')
        ct=append(ct,'Types',lambda p:f'<{p}Override PartName="/{part}" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
        styles=package.read('xl/styles.xml').decode('utf-8')
        style_root=ET.fromstring(styles)
        style_id=len(style_root.find(f'{{{NS}}}cellXfs'))
        font_id=len(style_root.find(f'{{{NS}}}fonts'))
        fill_id=len(style_root.find(f'{{{NS}}}fills'))
        styles=append(styles,'fonts',lambda p:f'<{p}font><{p}b/><{p}sz val="11"/><{p}color rgb="FFFFFFFF"/><{p}name val="맑은 고딕"/></{p}font>')
        styles=append(styles,'fills',lambda p:f'<{p}fill><{p}patternFill patternType="solid"><{p}fgColor rgb="FF285E68"/></{p}patternFill></{p}fill>')
        styles=append(styles,'cellXfs',lambda p:''.join(
            f'<{p}xf numFmtId="0" fontId="{font}" fillId="{fill}" borderId="0" xfId="0" applyAlignment="1"><{p}alignment horizontal="left" vertical="top" wrapText="1"/></{p}xf>'
            for font,fill in ((0,0),(font_id,fill_id))))
        for tag,count in (('fonts',font_id+1),('fills',fill_id+1),('cellXfs',style_id+2)):
            styles=re.sub(r'(<(?:[\w]+:)?'+tag+r'\b[^>]*\bcount=")[^"]+("[^>]*>)',lambda m:m[1]+str(count)+m[2],styles,count=1)
        sheet_xml=re.sub(r'\bs="\d+"',f's="{style_id}"',sheet_xml)
        sheet_xml=re.sub(r'(<row\b[^>]*\br="1"[^>]*>)(.*?)(</row>)',
                         lambda m:m[1]+re.sub(r'\bs="\d+"',f's="{style_id+1}"',m[2])+m[3],sheet_xml,count=1,flags=re.S)
        edits={'xl/workbook.xml':wb,'xl/_rels/workbook.xml.rels':rels,'[Content_Types].xml':ct,
               'xl/styles.xml':styles,part:sheet_xml}
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as dest:
            for info in package.infolist():
                dest.writestr(info,edits.pop(info.filename).encode('utf-8') if info.filename in edits else package.read(info.filename))
            for filename,content in edits.items():
                dest.writestr(filename,content.encode('utf-8'))
        return output.getvalue()
