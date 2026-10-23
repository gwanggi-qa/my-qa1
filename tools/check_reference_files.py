"""Local synthetic fixtures and real PDF-worker check; no device/model calls."""
import io
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from openpyxl import Workbook
from PIL import Image, ImageDraw
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
from reference_materials import pdf_preview


def main():
    output = ROOT / 'qa_results/reference-ui/fixtures'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'requirements.md').write_text('# QA reference fixture\n## Learning management\nShow LC and RC results.\n<script>window.injected=true</script>\n', encoding='utf-8')
    book = Workbook()
    sheet = book.active
    sheet.title = 'Test Cases'
    sheet.append(['TC ID', 'Precondition', 'Steps', 'Expected'])
    sheet.append(['QA-001', 'Learning history exists', 'Open learning management', 'LC and RC are visible'])
    sheet.append(['QA-002', 'Screen opened', 'Select RC', 'RC is selected'])
    hidden = book.create_sheet('Notes')
    hidden.append(['Version', '1'])
    book.save(output / 'cases.xlsx')
    book.close()
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=300)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
    contents = DecodedStreamObject()
    contents.set_data(b'BT /F1 16 Tf 30 260 Td (QA reference requirement: LC and RC visible.) Tj ET')
    page[NameObject('/Contents')] = contents
    data = io.BytesIO()
    writer.write(data)
    (output / 'requirements.pdf').write_bytes(data.getvalue())
    result = pdf_preview(data.getvalue())
    assert 'LC and RC' in result['units'][0]['text'], result
    image = Image.new('RGB', (900, 500), '#ffffff')
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 900, 60), fill='#202228')
    draw.text((24, 22), 'QA fixture / Learning management', fill='white')
    draw.rectangle((40, 100, 420, 430), fill='#ddf0eb')
    draw.rectangle((480, 100, 860, 430), fill='#e0ebfa')
    draw.text((70, 130), 'LC results', fill='#101216')
    draw.text((510, 130), 'RC results', fill='#101216')
    image.save(output / 'design.png')
    print('Real PDF worker: passed. Synthetic fixtures: ' + str(output))


if __name__ == '__main__':
    main()
