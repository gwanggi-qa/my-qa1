"""Isolated static APK declaration analysis. No device or model calls."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import struct
import subprocess
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone

from flask import Blueprint, Response, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

ROOT = Path(__file__).resolve().parent
MAX_APK = 200 * 1024 * 1024
MAX_OUTPUT = 16 * 1024 * 1024
MAX_STORAGE = 2 * 1024 ** 3
JADX_HASH = '6e5eacf500b64292bfb73c49797c1958f6ee44646e43e868039ae7feb573ff75'
LIMITATIONS = [
    'Static declarations only; runtime behavior and business correctness are unverified.',
    'Signing certificate and APK authenticity have not been verified.',
    'Native code, dynamic loading, split APK/AAB and server behavior are outside this MVP.',
    'No AI model was called. Analysis completion is not a test Pass.',
]


class AnalysisError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def tool_paths():
    tools = ROOT / 'qa_results/tools'
    return {
        'rea': tools / 'rea/node_modules/rea-agents/scripts/rea.mjs',
        'jadx': tools / 'jadx-headless-mcp-0.7.1-all.jar',
        'java_home': Path(os.environ.get('QA_ANALYSIS_JAVA_HOME') or
                          'C:/Program Files/Android/Android Studio/jbr'),
        'node': os.environ.get('QA_ANALYSIS_NODE') or shutil.which('node') or '',
    }


def package_version(path):
    try:
        return str(json.loads(Path(path).read_text(encoding='utf-8'))['version'])
    except (OSError, ValueError, KeyError):
        return None


def environment():
    paths = tool_paths()
    java_version = None
    try:
        release = (paths['java_home'] / 'release').read_text(encoding='utf-8')
        match = re.search(r'JAVA_VERSION="([^"]+)"', release)
        java_version = match[1] if match else None
    except OSError:
        pass
    java_ok = bool(java_version and re.match(r'^\d+', java_version)
                   and int(java_version.split('.')[0]) >= 17
                   and (paths['java_home'] / 'bin/java.exe').is_file()
                   and (paths['java_home'] / 'bin/javac.exe').is_file())
    version = package_version(paths['rea'].parent.parent / 'package.json')
    ready = bool(paths['node'] and Path(paths['node']).is_file() and paths['rea'].is_file() and version == '6.4.0'
                 and paths['jadx'].is_file() and java_ok)
    appium_home = Path(os.environ.get('APPIUM_HOME') or Path.home() / '.appium')
    return {'ready': ready, 'rea_version': version, 'jadx_present': paths['jadx'].is_file(),
            'java_version': java_version, 'java_ready': java_ok, 'node_present': bool(paths['node']),
            'appium_version': package_version(Path(os.environ.get('APPDATA', '')) /
                                              'npm/node_modules/appium/package.json'),
            'uiautomator2_version': package_version(appium_home /
                'node_modules/appium-uiautomator2-driver/package.json'),
            'appium_session': 'not_checked_by_this_page', 'analysis_mode': 'static_local', 'ai_used': False}


def validate_archive(path):
    # Bound central-directory allocation before ZipFile creates member objects.
    with open(path, 'rb') as stream:
        stream.seek(max(0, path.stat().st_size - 65557))
        tail = stream.read()
    offset = tail.rfind(b'PK\x05\x06')
    if offset < 0 or len(tail) - offset < 22:
        raise AnalysisError('Invalid APK ZIP directory.')
    _, disk, start_disk, on_disk, count, directory_bytes, _, comment = struct.unpack(
        '<4s4H2LH', tail[offset:offset + 22])
    if (disk or start_disk or on_disk != count or count == 65535 or count > 20000
            or directory_bytes > 8 * 1024 * 1024 or len(tail) - offset != 22 + comment):
        raise AnalysisError('ZIP directory exceeds the supported APK limits.')
    try:
        with zipfile.ZipFile(path) as archive:
            names, expanded = set(), 0
            for entry in archive.infolist():
                name = entry.filename
                if (name.casefold() in names or '\\' in name or ':' in name or name.startswith('/')
                        or '..' in PurePosixPath(name).parts or entry.flag_bits & 1 or '\x00' in name):
                    raise AnalysisError('Unsafe, encrypted or duplicate APK member.')
                if name.lower().endswith(('.apk', '.aab', '.apks')):
                    raise AnalysisError('Nested APK bundles are not supported.')
                names.add(name.casefold())
                expanded += entry.file_size
                if expanded > 1024 ** 3:
                    raise AnalysisError('Expanded APK exceeds 1 GiB.')
            if 'AndroidManifest.xml' not in archive.namelist():
                raise AnalysisError('A standalone APK with a root AndroidManifest.xml is required.')
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise AnalysisError('Unreadable APK archive.') from exc


def identifier(value):
    value = str(value or '')
    return value if len(value) <= 300 and re.fullmatch(r'[A-Za-z0-9_.$:-]+', value) else ''


def sanitize_result(payload, build_hash):
    data = payload.get('normalized_result') if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not identifier(data.get('package_name')):
        raise AnalysisError('REA returned an invalid package result.', 502)
    manifest = data.get('manifest', {})
    xml = manifest.get('text', '') if isinstance(manifest, dict) else ''
    if (not isinstance(xml, str) or len(xml) > 2 * 1024 * 1024
            or '<!DOCTYPE' in xml.upper() or '<!ENTITY' in xml.upper()):
        raise AnalysisError('Manifest exceeds the safe parser limits.', 502)
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise AnalysisError('REA did not return a readable manifest.', 502) from exc
    ns = '{http://schemas.android.com/apk/res/android}'
    if (root.tag != 'manifest' or root.get('package') != data['package_name']
            or root.get('split') or root.get(ns + 'isFeatureSplit') == 'true'
            or root.find('uses-split') is not None):
        raise AnalysisError('Split packages or inconsistent manifest declarations are unsupported.')
    components = []
    application = root.find('application')
    if application is not None:
        for element in application:
            if element.tag not in {'activity', 'activity-alias', 'service', 'receiver', 'provider'}:
                continue
            name = identifier(element.get(ns + 'name'))
            if name:
                components.append({'type': element.tag, 'name': name,
                    'exported': element.get(ns + 'exported') if element.get(ns + 'exported')
                    in {'true', 'false'} else 'not_declared'})
    if len(components) > 5000:
        raise AnalysisError('Too many component declarations.', 502)
    permissions = data.get('permissions', [])
    if not isinstance(permissions, list) or len(permissions) > 10000:
        raise AnalysisError('Invalid permission inventory.', 502)
    result = {'build_sha256': build_hash, 'provider': 'rea', 'rea_version': '6.4.0',
              'evidence_kind': 'static_declaration', 'observed_at': now(),
              'package_name': data['package_name'], 'components': components,
              'permissions': sorted({identifier(p) for p in permissions if identifier(p)}),
              'limitations': list(LIMITATIONS), 'ai_used': False,
              'signature_verification': 'not_performed',
              'manifest_status': 'complete' if manifest.get('status') == 'complete' else 'partial'}
    for name in ('version_name', 'version_code', 'min_sdk', 'target_sdk'):
        value = str(data.get(name) or '')
        result[name] = value[:100] if re.fullmatch(r'[\w. +()/-]*', value) else 'not_retained'
    for name in ('class_count', 'resource_count'):
        value = data.get(name)
        result[name] = value if type(value) is int and 0 <= value <= 10000000 else None
    engine = data.get('engine', {})
    result['engine'] = {key: identifier(engine.get(key)) for key in
                       ('name', 'version', 'artifact_sha256')} if isinstance(engine, dict) else {}
    return result


def stop_process(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    else:
        process.kill()
    process.wait(timeout=10)


def rea_analyze(path, build_hash, cancel):
    if cancel.is_set():
        raise AnalysisError('Analysis stopped.', 409)
    paths = tool_paths()
    if not environment()['ready']:
        raise AnalysisError('REA/JDK tools are not ready. Run tools/setup_analysis.py.', 503)
    if digest(paths['jadx']) != JADX_HASH:
        raise AnalysisError('JADX integrity check failed.', 503)
    if digest(path) != build_hash:
        raise AnalysisError('Registered APK hash changed.', 409)
    allowed = ('PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'TEMP', 'TMP',
               'USERPROFILE', 'HOME', 'LOCALAPPDATA', 'APPDATA')
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update(JAVA_HOME=str(paths['java_home']), REA_JADX_MCP_JAR=str(paths['jadx']),
               REA_JADX_HEAP_MIB='2048', REA_JADX_ACTIVE_PROCESSOR_COUNT='2')
    command = [paths['node'], str(paths['rea']), 'inspect-android-package', str(path),
               '--format', 'json', '--filter-output', 'normalized_result']
    if cancel.is_set():
        raise AnalysisError('Analysis stopped.', 409)
    output, overflow = bytearray(), threading.Event()
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env=env, cwd=str(ROOT), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
        def drain(stream, retain=False):
            size = 0
            for chunk in iter(lambda: stream.read(65536), b''):
                size += len(chunk)
                if size > MAX_OUTPUT:
                    overflow.set()
                elif retain:
                    output.extend(chunk)
        readers = [threading.Thread(target=drain, args=(process.stdout, True), daemon=True),
                   threading.Thread(target=drain, args=(process.stderr,), daemon=True)]
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + 180
        try:
            while process.poll() is None:
                if cancel.is_set():
                    raise AnalysisError('Analysis stopped.', 409)
                if overflow.is_set():
                    raise AnalysisError('REA output exceeded the safety limit.', 502)
                if time.monotonic() >= deadline:
                    raise AnalysisError('REA timed out after 180 seconds.', 504)
                cancel.wait(0.1)
        finally:
            stop_process(process)
            for reader in readers:
                reader.join(timeout=5)
        if overflow.is_set():
            raise AnalysisError('REA output exceeded the safety limit.', 502)
        if process.returncode != 0:
            raise AnalysisError('REA analysis failed. Check the pinned tool environment and APK.', 502)
    try:
        return sanitize_result(json.loads(output.decode('utf-8')), build_hash)
    except (ValueError, UnicodeError) as exc:
        raise AnalysisError('REA returned invalid JSON.', 502) from exc


class AnalysisStore:
    def __init__(self, root):
        self.root = Path(root)
        self._ready = False
        self._init_lock = threading.Lock()

    @contextmanager
    def connect(self):
        with self._init_lock:
            if not self._ready:
                (self.root / 'builds').mkdir(parents=True, exist_ok=True)
                db = sqlite3.connect(self.root / 'analysis.sqlite3')
                try:
                    if db.execute('PRAGMA user_version').fetchone()[0] > 3:
                        raise AnalysisError('Analysis database schema is newer than this server.', 503)
                    db.executescript('''
                        PRAGMA journal_mode=WAL;
                        CREATE TABLE IF NOT EXISTS archived_builds (sha256 TEXT PRIMARY KEY, deleted_at TEXT NOT NULL);
                        CREATE TABLE IF NOT EXISTS builds (
                            sha256 TEXT PRIMARY KEY, filename TEXT NOT NULL,
                            size_bytes INTEGER NOT NULL, created_at TEXT NOT NULL);
                        CREATE TABLE IF NOT EXISTS jobs (
                            id TEXT PRIMARY KEY, build_sha256 TEXT NOT NULL REFERENCES builds(sha256),
                            status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
                            result_json TEXT, error TEXT);
                        CREATE UNIQUE INDEX IF NOT EXISTS one_running_job ON jobs(status)
                            WHERE status='running';
                        CREATE TABLE IF NOT EXISTS reference_materials (
                            id TEXT PRIMARY KEY,
                            build_sha256 TEXT NOT NULL REFERENCES builds(sha256),
                            sha256 TEXT NOT NULL, filename TEXT NOT NULL,
                            kind TEXT NOT NULL, source_url TEXT NOT NULL,
                            size_bytes INTEGER NOT NULL, created_at TEXT NOT NULL,
                            content BLOB NOT NULL, preview_json TEXT NOT NULL,
                            image BLOB,
                            UNIQUE(build_sha256, sha256, kind, source_url));
                        CREATE TABLE IF NOT EXISTS tc_drafts (
                            id TEXT PRIMARY KEY, build_sha256 TEXT NOT NULL REFERENCES builds(sha256),
                            status TEXT NOT NULL, created_at TEXT NOT NULL, finished_at TEXT,
                            context_json TEXT NOT NULL, original_json TEXT, cases_json TEXT,
                            metrics_json TEXT, error TEXT, revision INTEGER NOT NULL DEFAULT 0);
                        CREATE UNIQUE INDEX IF NOT EXISTS one_tc_generation ON tc_drafts(status)
                            WHERE status='running';
                        PRAGMA user_version=3;
                    ''')
                    db.execute("UPDATE jobs SET status='interrupted', finished_at=?, error=? WHERE status='running'",
                               (now(), 'Server restarted; analysis was not resumed.'))
                    db.execute("UPDATE tc_drafts SET status='interrupted',finished_at=?,error=? WHERE status='running'",
                               (now(), 'Server restarted; generation was not resumed.'))
                    db.commit()
                finally:
                    db.close()
                self._ready = True
        db = sqlite3.connect(self.root / 'analysis.sqlite3', timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def path(self, sha):
        if not re.fullmatch(r'[0-9a-f]{64}', sha):
            raise AnalysisError('Unknown build.', 404)
        return self.root / 'builds' / (sha + '.apk')

    def register(self, stream, filename):
        name = str(filename or '').replace('\\', '/').rsplit('/', 1)[-1]
        if not name.lower().endswith('.apk') or any(ord(c) < 32 for c in name):
            raise AnalysisError('Select a standalone .apk file.')
        with self.connect():
            pass
        temp = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.root, suffix='.upload', delete=False) as out:
                temp = Path(out.name)
                size, sha = 0, hashlib.sha256()
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    size += len(chunk)
                    if size > MAX_APK:
                        raise AnalysisError('APK exceeds 200 MiB.', 413)
                    sha.update(chunk)
                    out.write(chunk)
            validate_archive(temp)
            key = sha.hexdigest()
            destination = self.path(key)
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                found = db.execute('SELECT 1 FROM builds WHERE sha256=?', (key,)).fetchone()
                used = db.execute('SELECT COALESCE(SUM(size_bytes),0) FROM builds').fetchone()[0]
                if not found and used + size > MAX_STORAGE:
                    raise AnalysisError('Local APK storage limit (2 GiB) reached. Contact the operator.', 413)
                if not destination.exists():
                    os.replace(temp, destination)
                elif digest(destination) != key:
                    raise AnalysisError('Stored APK integrity check failed.', 409)
                if not found:
                    db.execute('INSERT INTO builds VALUES (?,?,?,?)', (key, name[:160], size, now()))
                db.execute('DELETE FROM archived_builds WHERE sha256=?', (key,))
            return self.get(key), bool(found)
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)

    def get(self, sha):
        self.path(sha)
        with self.connect() as db:
            row = db.execute('SELECT * FROM builds WHERE sha256=?', (sha,)).fetchone()
            if not row:
                raise AnalysisError('Unknown build.', 404)
            job = db.execute('SELECT * FROM jobs WHERE build_sha256=? ORDER BY started_at DESC LIMIT 1',
                             (sha,)).fetchone()
        build = dict(row)
        build['job'] = dict(job) if job else None
        if job:
            build['job']['result'] = json.loads(build['job'].pop('result_json') or 'null')
        return build

    def list(self):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM builds WHERE sha256 NOT IN (SELECT sha256 FROM archived_builds) ORDER BY created_at DESC LIMIT 100').fetchall()
        return [dict(row) for row in rows]


class AnalysisService:
    def __init__(self, root, runner=rea_analyze):
        self.store, self.runner = AnalysisStore(root), runner
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.active_id = None
        self.worker = None

    def start(self, sha):
        self.store.get(sha)
        if not environment()['ready']:
            raise AnalysisError('REA/JDK tools are not ready. Run tools/setup_analysis.py.', 503)
        with self.lock:
            if self.active_id:
                raise AnalysisError('Another analysis is running.', 409)
            job_id = uuid.uuid4().hex
            with self.store.connect() as db:
                try:
                    db.execute('INSERT INTO jobs(id,build_sha256,status,started_at) VALUES (?,?,?,?)',
                               (job_id, sha, 'running', now()))
                except sqlite3.IntegrityError as exc:
                    raise AnalysisError('Another analysis is running.', 409) from exc
            self.active_id, self.cancel = job_id, threading.Event()
            self.worker = threading.Thread(target=self._run, args=(job_id, sha, self.cancel), daemon=True)
            try:
                self.worker.start()
            except Exception as exc:
                self.active_id = None
                with self.store.connect() as db:
                    db.execute("UPDATE jobs SET status='failed',finished_at=?,error=? WHERE id=?",
                               (now(), 'Analysis worker could not start.', job_id))
                raise AnalysisError('Analysis worker could not start.', 503) from exc
        return self.store.get(sha)

    def _run(self, job_id, sha, cancel):
        status, result, error = 'completed', None, None
        try:
            result = self.runner(self.store.path(sha), sha, cancel)
        except AnalysisError as exc:
            status, error = 'failed', str(exc)
        except Exception:
            status, error = 'failed', 'Internal analysis error; no device action was performed.'
        finally:
            with self.lock:
                if cancel.is_set():
                    status, result, error = 'stopped', None, 'Analysis stopped.'
                try:
                    with self.store.connect() as db:
                        db.execute('UPDATE jobs SET status=?,finished_at=?,result_json=?,error=? WHERE id=?',
                                   (status, now(), json.dumps(result) if result else None, error, job_id))
                finally:
                    self.active_id = None

    def stop(self, job_id):
        if not re.fullmatch(r'[0-9a-f]{32}', job_id):
            raise AnalysisError('Unknown analysis job.', 404)
        with self.lock:
            with self.store.connect() as db:
                row = db.execute('SELECT status FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                raise AnalysisError('Unknown analysis job.', 404)
            if job_id == self.active_id:
                self.cancel.set()
                return {'status': 'stopping'}
            return {'status': row['status']}


def register(app, access, root=None, ai_runner=None, ai_status=None):
    service = AnalysisService(root or ROOT / 'qa_results/app_analysis')
    app.extensions['app_analysis'] = service
    api = Blueprint('app_analysis', __name__)

    @api.errorhandler(AnalysisError)
    def error(exc):
        return jsonify(error=str(exc)), exc.status

    @api.errorhandler(RequestEntityTooLarge)
    def too_large(exc):
        return jsonify(error='Upload exceeds the file size limit.'), 413

    @api.get('/app-analysis')
    def page():
        html = (ROOT / 'static/app-analysis.html').read_text(encoding='utf-8')
        return access.bootstrap(Response(html.replace('__PAGE_TOKEN__', access.token), mimetype='text/html'))

    @api.get('/api/app-analysis/environment')
    def env():
        return jsonify(environment())

    @api.route('/api/app-analysis/builds', methods=['GET', 'POST'])
    def builds():
        if request.method == 'GET':
            return jsonify(builds=service.store.list())
        request.max_content_length = MAX_APK + 1024 * 1024
        uploaded = request.files.get('file')
        if uploaded is None:
            raise AnalysisError('APK file is required.')
        build, duplicate = service.store.register(uploaded.stream, uploaded.filename)
        return jsonify(build=build, duplicate=duplicate), 200 if duplicate else 201

    @api.get('/api/app-analysis/builds/<sha>')
    def build(sha):
        return jsonify(build=service.store.get(sha))

    @api.delete('/api/app-analysis/builds/<sha>')
    def delete_build(sha):
        if request.get_json() != {'confirm': True}:
            raise AnalysisError('빌드 삭제 확인이 필요합니다.')
        drafts = app.extensions['tc_drafts']
        with service.lock, drafts.lock:
            service.store.get(sha)
            apk_path = service.store.path(sha)
            if apk_path.resolve().parent != (service.store.root / 'builds').resolve():
                raise AnalysisError('Invalid APK storage path.', 409)
            with service.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if (db.execute("SELECT 1 FROM jobs WHERE build_sha256=? AND status='running'", (sha,)).fetchone()
                        or db.execute("SELECT 1 FROM tc_drafts WHERE build_sha256=? AND status='running'", (sha,)).fetchone()):
                    raise AnalysisError('분석 또는 TC 생성 중인 빌드는 중단·완료 후 삭제하세요.', 409)
                db.execute('DELETE FROM draft_checkpoints WHERE job_id IN (SELECT id FROM tc_drafts WHERE build_sha256=?)', (sha,))
                db.execute('DELETE FROM tc_drafts WHERE build_sha256=?', (sha,))
                db.execute('DELETE FROM reference_materials WHERE build_sha256=?', (sha,))
                db.execute('DELETE FROM jobs WHERE build_sha256=?', (sha,))
                db.execute('DELETE FROM archived_builds WHERE sha256=?', (sha,))
                db.execute('DELETE FROM builds WHERE sha256=?', (sha,))
                apk_path.unlink(missing_ok=True)
        return jsonify(deleted=True, execution_results_preserved=True)

    @api.post('/api/app-analysis/builds/<sha>/analyze')
    def analyze(sha):
        if request.get_json() != {}:
            raise AnalysisError('This endpoint accepts an empty JSON object only.')
        return jsonify(build=service.start(sha)), 202

    @api.post('/api/app-analysis/jobs/<job_id>/stop')
    def stop(job_id):
        if request.get_json() != {}:
            raise AnalysisError('This endpoint accepts an empty JSON object only.')
        return jsonify(service.stop(job_id))

    from reference_materials import register_routes
    references = register_routes(api, service.store)
    from tc_drafts import register_draft_routes
    app.extensions['tc_drafts'] = register_draft_routes(api, service.store, references, ai_runner, ai_status)
    app.register_blueprint(api)
