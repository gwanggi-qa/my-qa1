"""Build-scoped untrusted reference files. No model, URL fetching or device work."""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
from urllib.parse import urlsplit
import uuid
import warnings

from flask import Response, jsonify, request

# Isolated PDF workers load only this trusted repository, not the caller's cwd.
if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from app_analysis import AnalysisError, now
from workbook_safety import validate_workbook

MAX_FILE = 20 * 1024 * 1024
MAX_TOTAL = 100 * 1024 * 1024
MAX_TEXT = 100000
KINDS = {'requirements', 'design', 'testcase'}
EXTENSIONS = {'.md', '.txt', '.xlsx', '.pdf', '.png', '.jpg', '.jpeg'}
META = 'id,build_sha256,sha256,filename,kind,source_url,size_bytes,created_at'


def provenance(value):
    value = str(value or '').strip()
    if not value:
        return ''
    try:
        url = urlsplit(value)
        host = (url.hostname or '').lower()
        permitted = any(host == d or host.endswith('.' + d)
                        for d in ('notion.so', 'notion.site', 'figma.com'))
        if (len(value) > 2048 or re.search(r'[\s\\\x00-\x1f]', value) or
                url.scheme != 'https' or not permitted or url.username or url.password
                or url.port not in (None, 443)):
            raise ValueError()
    except ValueError as exc:
        raise AnalysisError('Use an HTTPS Notion or Figma source URL without credentials.') from exc
    return value


def text_preview(data):
    try:
        text = data.decode('utf-8-sig')
    except UnicodeError as exc:
        raise AnalysisError('Markdown/text must use UTF-8 encoding.') from exc
    if '\x00' in text:
        raise AnalysisError('Binary data is not a text document.')
    truncated = len(text) > MAX_TEXT
    text = text[:MAX_TEXT]
    return {'format': 'text', 'status': 'partial' if truncated else 'ready',
            'warnings': ['Text preview limited to 100,000 characters.'] if truncated else [],
            'units': [{'label': 'Document', 'lines': [
                {'line': i, 'text': line} for i, line in enumerate(text.splitlines(), 1)]}]}


def workbook_preview(data):
    from openpyxl import load_workbook
    validate_workbook(data)
    book = load_workbook(io.BytesIO(data), read_only=False, data_only=False, keep_links=False)
    result = {'format': 'workbook', 'status': 'ready', 'warnings': [], 'units': []}
    remaining = MAX_TEXT
    try:
        if len(book.worksheets) > 20:
            result['warnings'].append('Only the first 20 sheets are previewed.')
        for sheet in book.worksheets[:20]:
            unit = {'label': sheet.title, 'sheet_state': sheet.sheet_state,
                    'rows': [], 'merged_ranges': [str(r) for r in list(sheet.merged_cells.ranges)[:200]],
                    'source_rows': sheet.max_row, 'source_columns': sheet.max_column}
            if sheet.max_row > 200 or sheet.max_column > 30 or len(sheet.merged_cells.ranges) > 200:
                result['warnings'].append(sheet.title + ': preview limited to 200 rows / 30 columns / 200 merges.')
            for row in sheet.iter_rows(max_row=min(sheet.max_row, 200), max_col=min(sheet.max_column, 30)):
                cells = []
                for cell in row:
                    if cell.value is None:
                        continue
                    raw = str(cell.value)
                    text = raw[:min(remaining, 2000)]
                    remaining -= len(text)
                    if len(text) < len(raw) and 'Cell/text preview truncated.' not in result['warnings']:
                        result['warnings'].append('Cell/text preview truncated.')
                    if text:
                        cells.append({'cell': cell.coordinate, 'value': text, 'formula': cell.data_type == 'f'})
                if cells:
                    unit['rows'].append({'row': row[0].row, 'cells': cells})
                if not remaining:
                    break
            result['units'].append(unit)
            if not remaining:
                break
        if result['warnings']:
            result['status'] = 'partial'
        result['notes'] = ['Formulas are shown literally, never evaluated. Merged values remain at their original cells.']
        return result
    finally:
        book.close()


def image_preview(data):
    from PIL import Image
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {'PNG', 'JPEG'} or image.width * image.height > 20000000:
                raise AnalysisError('Image must be PNG/JPEG with at most 20 million pixels.')
            size = [image.width, image.height]
            image.load()
            image = image.convert('RGB')
            image.thumbnail((1600, 1600))
            # Copy pixels into a new object to discard EXIF and other metadata.
            clean = Image.new('RGB', image.size)
            clean.paste(image)
            output = io.BytesIO()
            clean.save(output, format='PNG')
    return {'format': 'image', 'status': 'visual_only', 'width': size[0], 'height': size[1],
            'warnings': ['Image registered; OCR and AI interpretation have not been performed.'], 'units': []}, output.getvalue()


def extract_pdf(path):
    from pypdf import PdfReader
    reader = PdfReader(path, strict=True)
    if reader.is_encrypted:
        raise ValueError('Encrypted PDF')
    result = {'format': 'pdf', 'status': 'ready', 'warnings': [], 'units': [], 'page_count': len(reader.pages)}
    remaining = MAX_TEXT
    if len(reader.pages) > 100:
        result['warnings'].append('Only the first 100 PDF pages are previewed.')
    for index in range(min(100, len(reader.pages))):
        raw = reader.pages[index].extract_text() or ''
        text = raw[:min(remaining, 10000)]
        remaining -= len(text)
        if len(text) < len(raw) and 'PDF text preview truncated.' not in result['warnings']:
            result['warnings'].append('PDF text preview truncated.')
        if not text.strip() and 'Some pages have no extractable text; visual review/OCR is required.' not in result['warnings']:
            result['warnings'].append('Some pages have no extractable text; visual review/OCR is required.')
        result['units'].append({'label': 'Page ' + str(index + 1), 'page': index + 1, 'text': text})
        if not remaining:
            break
    if result['warnings'] or not result['units']:
        result['status'] = 'partial'
    return result


def pdf_preview(data):
    import importlib.util
    if importlib.util.find_spec('pypdf') is None:
        raise AnalysisError('PDF reader unavailable. Install pypdf with setup_and_run.bat.', 503)
    if not data.startswith(b'%PDF-'):
        raise AnalysisError('Invalid PDF signature.')
    allowed = {'SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'LOCALAPPDATA'}
    env = {key: val for key, val in os.environ.items() if key.upper() in allowed}
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'reference.pdf'
        path.write_bytes(data)
        try:
            proc = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()), '--pdf', str(path)],
                                  capture_output=True, timeout=30, env=env,
                                  creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired as exc:
            raise AnalysisError('PDF preview timed out; upload a smaller exported document.', 422) from exc
    if proc.returncode or len(proc.stdout) > 2 * 1024 * 1024:
        raise AnalysisError('PDF could not be read. Check for encryption or damaged content.', 422)
    try:
        result = json.loads(proc.stdout)
        if result.get('format') != 'pdf' or not isinstance(result.get('units'), list):
            raise ValueError()
        return result
    except (ValueError, AttributeError) as exc:
        raise AnalysisError('PDF preview returned invalid content.', 422) from exc


def preview(data, extension):
    try:
        if extension in {'.md', '.txt'}:
            return text_preview(data), None
        if extension == '.xlsx':
            return workbook_preview(data), None
        if extension == '.pdf':
            return pdf_preview(data), None
        return image_preview(data)
    except AnalysisError:
        raise
    except Exception as exc:
        raise AnalysisError('Cannot read this file. Check the format and export it again.') from exc


class ReferenceStore:
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()

    def list(self, sha):
        self.store.get(sha)
        with self.store.connect() as db:
            rows = db.execute('SELECT ' + META + ',preview_json FROM reference_materials WHERE build_sha256=? ORDER BY created_at DESC', (sha,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            document = json.loads(item.pop('preview_json'))
            item['status'] = document['status']
            result.append(item)
        return result

    def get(self, sha, ref_id, image=False):
        self.store.get(sha)
        if not re.fullmatch(r'[0-9a-f]{32}', ref_id):
            raise AnalysisError('Unknown reference.', 404)
        with self.store.connect() as db:
            row = db.execute('SELECT ' + META + (',image' if image else ',preview_json') +
                             ' FROM reference_materials WHERE build_sha256=? AND id=?', (sha, ref_id)).fetchone()
        if not row:
            raise AnalysisError('Unknown reference.', 404)
        item = dict(row)
        if image:
            if not item['image']:
                raise AnalysisError('No image preview for this reference.', 404)
            return item['image']
        item['preview'] = json.loads(item.pop('preview_json'))
        return item

    def add(self, sha, stream, filename, kind, source_url=''):
        self.store.get(sha)
        name = str(filename or '').replace('\\', '/').rsplit('/', 1)[-1]
        extension = Path(name).suffix.lower()
        if not name or len(name) > 180 or any(ord(c) < 32 for c in name):
            raise AnalysisError('Invalid reference filename.')
        if extension not in EXTENSIONS:
            raise AnalysisError('Supported files: XLSX, PDF, MD, TXT, PNG, JPG.', 415)
        if kind not in KINDS:
            raise AnalysisError('Select requirements, design or testcase.')
        source_url = provenance(source_url)
        if not self.lock.acquire(blocking=False):
            raise AnalysisError('Another reference upload is being processed.', 409)
        try:
            data = stream.read(MAX_FILE + 1)
            if not data or len(data) > MAX_FILE:
                raise AnalysisError('Reference files must be nonempty and at most 20 MiB.', 413)
            digest = hashlib.sha256(data).hexdigest()
            with self.store.connect() as db:
                found = db.execute('SELECT id FROM reference_materials WHERE build_sha256=? AND sha256=? AND kind=? AND source_url=?',
                                   (sha, digest, kind, source_url)).fetchone()
            if found:
                return self.get(sha, found['id']), True
            document, image = preview(data, extension)
            return self.persist(sha, data, name, kind, source_url, document, image)
        finally:
            self.lock.release()

    def persist(self, sha, data, name, kind, source_url, document, image):
        self.store.get(sha)
        digest = hashlib.sha256(data).hexdigest()
        ref_id = uuid.uuid4().hex
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            found = db.execute('SELECT id FROM reference_materials WHERE build_sha256=? AND sha256=? AND kind=? AND source_url=?',
                               (sha, digest, kind, source_url)).fetchone()
            if not found:
                used = db.execute('SELECT COALESCE(SUM(size_bytes),0) FROM reference_materials').fetchone()[0]
                count = db.execute('SELECT COUNT(*) FROM reference_materials WHERE build_sha256=?', (sha,)).fetchone()[0]
                if used + len(data) > MAX_TOTAL or count >= 50 or len(data) > MAX_FILE:
                    raise AnalysisError('Reference storage limit reached (100 MiB total / 50 per build).', 413)
                db.execute('INSERT INTO reference_materials VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                           (ref_id, sha, digest, name, kind, source_url, len(data), now(), data,
                            json.dumps(document, ensure_ascii=False), image))
        return self.get(sha, found['id'] if found else ref_id), bool(found)

    def delete(self, sha, ref_id):
        self.get(sha, ref_id)
        with self.store.connect() as db:
            db.execute('DELETE FROM reference_materials WHERE build_sha256=? AND id=?', (sha, ref_id))


def register_routes(api, store):
    references_store = ReferenceStore(store)

    @api.route('/api/app-analysis/builds/<sha>/references', methods=['GET', 'POST'])
    def references(sha):
        if request.method == 'GET':
            return jsonify(references=references_store.list(sha))
        request.max_content_length = MAX_FILE + 1024 * 1024
        uploaded = request.files.get('file')
        if (uploaded is None or set(request.files) != {'file'} or len(request.files.getlist('file')) != 1
                or set(request.form) - {'kind', 'source_url'}):
            raise AnalysisError('One reference file and its category are required.')
        item, duplicate = references_store.add(sha, uploaded.stream, uploaded.filename,
                                               request.form.get('kind'), request.form.get('source_url', ''))
        return jsonify(reference=item, duplicate=duplicate), 200 if duplicate else 201

    @api.route('/api/app-analysis/builds/<sha>/references/<ref_id>', methods=['GET', 'DELETE'])
    def reference(sha, ref_id):
        if request.method == 'DELETE':
            if request.get_json() != {}:
                raise AnalysisError('Deletion accepts an empty JSON object only.')
            references_store.delete(sha, ref_id)
            return jsonify(deleted=True)
        return jsonify(reference=references_store.get(sha, ref_id))

    @api.get('/api/app-analysis/builds/<sha>/references/<ref_id>/image')
    def reference_image(sha, ref_id):
        return Response(references_store.get(sha, ref_id, image=True), mimetype='image/png')

    @api.post('/api/app-analysis/builds/<sha>/figma')
    def figma(sha):
        from figma_import import import_frame
        payload = request.get_json()
        if set(payload) != {'url'}:
            raise AnalysisError('A Figma frame URL is required.')
        item, duplicate = import_frame(references_store, sha, payload['url'])
        return jsonify(reference=item, duplicate=duplicate), 200 if duplicate else 201

    return references_store


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] != '--pdf':
        sys.exit(2)
    try:
        sys.stdout.buffer.write(json.dumps(extract_pdf(sys.argv[2]), ensure_ascii=True).encode('ascii'))
    except Exception:
        sys.exit(1)
