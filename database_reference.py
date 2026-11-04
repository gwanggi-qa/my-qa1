"""Bounded, read-only SQLite inspection and explicit Japanese curriculum scopes."""
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time

DB_EXTENSIONS = {'.db', '.sqlite', '.sqlite3', '.dat'}
MAX_SESSIONS = 5000
SESSION_COLUMNS = ('LevelNum', 'EpisodeNum', 'StageNum', 'StepNum', 'SessionOrd',
                   'SessionType', 'SessionTitle', 'SessionSubTitle', 'StageTitle', 'StageTitleKr')


def inspect_file(path):
    deadline = time.monotonic() + 15
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('PRAGMA trusted_schema=OFF')
    db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    quote = lambda s: '"' + s.replace('"', '""') + '"'
    result = {'format': 'database', 'status': 'ready', 'warnings': [], 'units': [],
              'engine': 'SQLite', 'curriculum': None}
    try:
        tables = db.execute("SELECT name,sql FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        result['table_count'] = len(tables)
        if len(tables) > 128:
            result['warnings'].append('테이블 미리보기는 앞 128개로 제한됩니다.')
        for table in tables[:128]:
            name = table['name']
            if re.search(r'CREATE\s+VIRTUAL\s+TABLE', table['sql'] or '', re.I):
                result['warnings'].append(name + ': 가상 테이블은 분석하지 않습니다.')
                continue
            columns = [dict(r) for r in db.execute('PRAGMA table_info(' + quote(name) + ')')]
            count = db.execute('SELECT count(*) FROM ' + quote(name)).fetchone()[0]
            # Bound field reads inside SQLite (large BLOBs never enter Python).
            fields = [f"CASE WHEN typeof({quote(c['name'])})='blob' THEN '[BLOB]' ELSE substr(CAST({quote(c['name'])} AS TEXT),1,160) END AS {quote(c['name'])}" for c in columns[:30]]
            sample = [dict(r) for r in db.execute('SELECT ' + ','.join(fields) + ' FROM ' + quote(name) + ' LIMIT 2')] if fields else []
            content = {'table': name, 'row_count': count, 'columns': [{'name': c['name'], 'type': c['type'], 'pk': c['pk']} for c in columns[:60]], 'sample_rows': sample}
            result['units'].append({'label': name, 'text': json.dumps(content, ensure_ascii=False, indent=2)})
        session_table = next((t for t in tables if t['name'] == 'SessionInfo' and not re.search(r'VIRTUAL', t['sql'] or '', re.I)), None)
        if session_table:
            cols = {r['name'] for r in db.execute('PRAGMA table_info(SessionInfo)')}
            if set(SESSION_COLUMNS).issubset(cols):
                records = db.execute('SELECT ' + ','.join(SESSION_COLUMNS) + ' FROM SessionInfo ORDER BY LevelNum,EpisodeNum,StageNum,SessionOrd,StepNum LIMIT ?', (MAX_SESSIONS + 1,)).fetchall()
                complete = len(records) <= MAX_SESSIONS
                sessions = []
                for record in records[:MAX_SESSIONS]:
                    row = dict(record)
                    if any(type(row[k]) is not int for k in SESSION_COLUMNS[:5]):
                        complete = False
                        continue
                    for key in SESSION_COLUMNS[5:]:
                        raw = str(row[key] or '')
                        if len(raw) > 1000:
                            complete = False
                        row[key] = raw[:1000]
                    sessions.append(row)
                levels = [{'level': level, 'sessions': sum(s['LevelNum'] == level for s in sessions)} for level in sorted({s['LevelNum'] for s in sessions})]
                result['curriculum'] = {'adapter': 'japanese_session_info_v1', 'complete': complete, 'levels': levels, 'sessions': sessions}
                summary = '학습 순서: 레벨 → 에피소드 → 스테이지 → SessionOrd → StepNum\n' + '\n'.join(f"레벨 {v['level']}: {v['sessions']}개 학습 단계" for v in levels)
                result['units'].insert(0, {'label': '레벨별 학습 구성', 'text': summary})
                if not complete:
                    result['warnings'].append('학습 순서 전체를 읽지 못했습니다. 완주 범위 생성에 사용할 수 없습니다.')
        if not result['curriculum']:
            result['warnings'].append('테이블 구조는 분석했지만 레벨별 학습 구조는 인식하지 못했습니다. 이 DB 전용 연결 규칙이 필요합니다.')
        result['notes'] = ['원본 파일을 변경하지 않고 분석했습니다. 테이블 값은 일부 예시이며, 인식된 학습 순서는 별도로 보관합니다.',
                           'DB 값은 구현 자료입니다. 완료 조건과 정답 기준은 기획서·승인된 확인목록으로 확인하세요.']
        if result['warnings']:
            result['status'] = 'partial'
        return result
    finally:
        db.close()


def database_preview(data):
    from app_analysis import AnalysisError
    if not data.startswith(b'SQLite format 3\x00'):
        raise AnalysisError('SQLite 형식의 DB만 분석할 수 있습니다. 암호화·압축·전용 DAT는 SQLite로 내보낸 파일이 필요합니다.', 415)
    env = {k: v for k, v in os.environ.items() if k.upper() in {'SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'LOCALAPPDATA'}}
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'reference.db'
        path.write_bytes(data)
        try:
            proc = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()), str(path)],
                                  capture_output=True, timeout=20, env=env,
                                  creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired as exc:
            raise AnalysisError('DB 분석 시간이 초과되었습니다. 필요한 테이블만 포함한 DB로 다시 등록하세요.', 422) from exc
    if proc.returncode or len(proc.stdout) > 8 * 1024 * 1024:
        raise AnalysisError('DB를 읽을 수 없습니다. 손상 여부를 확인하고 최신 SQLite 스냅샷으로 내보내세요.', 422)
    return json.loads(proc.stdout)


def select_sessions(preview, scope):
    """None: ordinary scope; list: explicit level curriculum, never a guessed subset."""
    from app_analysis import AnalysisError
    if not re.search(r'레벨|level', scope, re.I):
        return None
    curriculum = preview.get('curriculum')
    if not curriculum or not curriculum['complete']:
        raise AnalysisError('선택한 DB의 전체 레벨 구조를 인식하지 못했습니다. DB 분석 결과를 확인하세요.', 422)
    numbers = [int(x) for x in re.findall(r'(\d+)\s*레벨', scope)]
    numbers += [int(x) for x in re.findall(r'(?:레벨|level)\s*(\d+)', scope, re.I)]
    if re.search(r'\d+\s*[~～\-–]\s*\d+', scope):
        raise AnalysisError('레벨 범위는 “1레벨, 2레벨, 3레벨 학습하기”처럼 개별 번호로 입력하세요.')
    available = {x['level'] for x in curriculum['levels']}
    if numbers:
        selected = set(numbers)
        if selected - available:
            raise AnalysisError('DB에 없는 레벨입니다: ' + ', '.join(map(str, sorted(selected - available))))
    elif re.search(r'각\s*레벨|레벨\s*별|(?:전체|모든)\s*레벨', scope):
        selected = available
    else:
        raise AnalysisError('학습할 레벨을 지정하세요. 예: “1레벨 전부 학습하기”, “각 레벨별 학습하기”')
    if not selected:
        raise AnalysisError('DB에 학습 단계가 없습니다.')
    return [s for s in curriculum['sessions'] if s['LevelNum'] in selected]


if __name__ == '__main__':
    try:
        sys.stdout.buffer.write(json.dumps(inspect_file(sys.argv[1]), ensure_ascii=True).encode('ascii'))
    except Exception:
        sys.exit(1)
