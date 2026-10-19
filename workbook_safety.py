"""Bounded workbook ingestion and contained artifact paths; no device/network work."""
import io
import re
import zipfile
from pathlib import Path, PurePosixPath
from xml.parsers import expat

MAX_WORKBOOK_BYTES = 20 * 1024 * 1024
MAX_EXPANDED_BYTES = 100 * 1024 * 1024
MAX_ENTRIES = 2048
MAX_SHEETS = 64
MAX_CELLS = 500_000
MAX_ROWS = 50_000
MAX_COLUMNS = 1024


def artifact_path(directory, filename):
    name = str(filename)
    if not name or name in {'.', '..'} or re.search(r'[\\/:\x00-\x1f]', name):
        raise ValueError('Invalid artifact filename')
    root = Path(directory).resolve()
    target = root / name
    if target.resolve().parent != root or target.is_symlink():
        raise ValueError('Artifact path escapes its directory')
    return target


def read_upload(stream, filename):
    if not isinstance(filename, str) or len(filename) > 180 or not re.fullmatch(r'[^\\/:\x00-\x1f]+\.xls[xm]', filename, re.I):
        raise ValueError('Upload a plain .xlsx or .xlsm filename without a path')
    stream.seek(0)
    payload = stream.read(MAX_WORKBOOK_BYTES + 1)
    validate_workbook(payload)
    stream.seek(0)
    return payload


def validate_workbook(payload):
    if not payload or len(payload) > MAX_WORKBOOK_BYTES:
        raise ValueError('Excel upload must be at most 20 MiB')
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as package:
            infos = package.infolist()
            names = [i.filename for i in infos]
            if len(infos) > MAX_ENTRIES or len(names) != len(set(names)):
                raise ValueError('Too many or duplicate workbook package entries')
            if sum(i.file_size for i in infos) > MAX_EXPANDED_BYTES:
                raise ValueError('Expanded workbook exceeds 100 MiB')
            for info in infos:
                parts = PurePosixPath(info.filename).parts
                if not parts or info.filename.startswith('/') or '\\' in info.filename or ':' in info.filename or '..' in parts or info.flag_bits & 1:
                    raise ValueError('Invalid or encrypted workbook entry')
            if '[Content_Types].xml' not in names or 'xl/workbook.xml' not in names:
                raise ValueError('Not an Excel workbook package')
            sheets = [i for i in infos if re.fullmatch(r'xl/worksheets/[^/]+\.xml', i.filename)]
            if len(sheets) > MAX_SHEETS:
                raise ValueError('Too many workbook sheets')
            total_cells = 0
            for info in infos:
                if not info.filename.lower().endswith((".xml", ".rels")):
                    continue
                is_sheet = info in sheets
                bounds = [0, 0, 0]
                def start(name, attrs):
                    tag = name.rsplit(':', 1)[-1]
                    if tag == 'c':
                        bounds[2] += 1
                    refs = re.findall(r'([A-Za-z]+)([0-9]+)', attrs.get('ref', '') if tag == 'dimension' else attrs.get('r', '') if tag == 'c' else '')
                    for letters, row in refs:
                        col = 0
                        for letter in letters.upper(): col = col * 26 + ord(letter) - 64
                        bounds[0], bounds[1] = max(bounds[0], int(row)), max(bounds[1], col)
                    if tag == 'row' and attrs.get('r', '').isdigit(): bounds[0] = max(bounds[0], int(attrs['r']))
                    if bounds[0] > MAX_ROWS or bounds[1] > MAX_COLUMNS or bounds[0] * bounds[1] > MAX_CELLS or bounds[2] > MAX_CELLS:
                        raise ValueError('Workbook row/column/cell limits exceeded')
                parser = expat.ParserCreate()
                parser.StartElementHandler = start if is_sheet else None
                def no_doctype(*args): raise ValueError('Workbook XML may not declare a DTD')
                parser.StartDoctypeDeclHandler = no_doctype
                parser.ExternalEntityRefHandler = lambda *args: 0
                with package.open(info) as source:
                    while True:
                        chunk = source.read(64 * 1024)
                        parser.Parse(chunk, not chunk)
                        if not chunk: break
                total_cells += max(bounds[2], bounds[0] * bounds[1])
                if total_cells > MAX_CELLS:
                    raise ValueError('Total workbook cell limits exceeded')
    except (zipfile.BadZipFile, expat.ExpatError, OSError, RuntimeError) as exc:
        raise ValueError('Invalid workbook package: ' + str(exc)) from exc
