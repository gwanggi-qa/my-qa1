"""Local speech evidence, immutable input assets, and explicit verdict policy."""
import copy
import hashlib
import io
import json
import re
import threading
import uuid
import wave
from pathlib import Path

from execution_results import now

SUBJECT = 'japanese-premium'
GRADES = {'Perfect', 'Excellent', 'Good', 'Bad', 'Fail'}
OUTCOMES = {'word_correct', 'word_wrong'}
MAX_AUDIO_BYTES = 16000 * 2 * 60
ID = re.compile(r'^[a-f0-9]{32}$')


def checked_id(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError('잘못된 기록 ID입니다.')
    return value


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def pcm_audio(data, suffix, metadata):
    if not data or len(data) > MAX_AUDIO_BYTES + 65536:
        raise ValueError('음성은 60초 이하의 PCM 또는 WAV 파일이어야 합니다.')
    if suffix.lower() == '.wav':
        try:
            with wave.open(io.BytesIO(data), 'rb') as wav:
                if wav.getcomptype() != 'NONE':
                    raise ValueError('압축 WAV는 지원하지 않습니다.')
                rate, channels, width = wav.getframerate(), wav.getnchannels(), wav.getsampwidth()
                pcm = wav.readframes(wav.getnframes())
                if len(pcm) != wav.getnframes() * channels * width:
                    raise ValueError('WAV에 선언된 전체 음성 데이터가 없습니다. 잘린 파일은 입력하지 않습니다.')
        except (wave.Error, EOFError) as exc:
            raise ValueError('WAV 파일을 읽을 수 없습니다.') from exc
    elif suffix.lower() == '.pcm':
        try:
            rate, channels, width = (int(metadata[k]) for k in ('sample_rate', 'channels', 'sample_width'))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('PCM의 표본 속도, 채널, 바이트 폭을 명시하세요.') from exc
        pcm = data
    else:
        raise ValueError('PCM/WAV만 지원합니다.')
    if (rate, channels, width) != (16000, 1, 2):
        raise ValueError('현재 직접 주입은 16 kHz, 모노, 16-bit little-endian PCM만 지원합니다.')
    if not pcm or len(pcm) % 2 or len(pcm) > MAX_AUDIO_BYTES:
        raise ValueError('PCM 길이 또는 형식이 잘못됐습니다.')
    return pcm, {'sample_rate': rate, 'channels': channels, 'sample_width': width,
                 'samples': len(pcm)//2, 'duration_s': len(pcm)/32000}


def policy(item):
    approved = item.get('approved') is True
    grades = item.get('expected_grades', [])
    if not isinstance(grades, list) or any(g not in GRADES for g in grades):
        raise ValueError('합격 기준 등급을 확인하세요.')
    reject = item.get('expect_reject') is True
    outcomes = item.get('expected_outcomes', [])
    if not isinstance(outcomes, list) or any(o not in OUTCOMES for o in outcomes):
        raise ValueError('단어의 기대 판정은 정답 또는 오답으로 지정하세요.')
    if outcomes and (grades or reject):
        raise ValueError('단어 판정 기준과 등급·거절 기준은 동시에 지정할 수 없습니다.')
    if approved and not grades and not reject and not outcomes:
        raise ValueError('승인한 항목에는 기대 등급 또는 거절 기대를 지정하세요.')
    if reject and grades:
        raise ValueError('등급 기대와 거절 기대는 동시에 선택할 수 없습니다.')
    result = {'approved': approved, 'expected_grades': list(dict.fromkeys(grades)), 'expect_reject': reject}
    if outcomes:
        result['expected_outcomes'] = list(dict.fromkeys(outcomes))
    return result


def verdict(attempt, expected):
    if attempt.get('stopped'):
        return 'stopped', '사용자가 중단했습니다.'
    if not attempt.get('cleanup_ok', True):
        return 'blocked', '디버거 해제를 확인하지 못했습니다. 새 실행 전에 연결을 확인하세요.'
    if attempt.get('status') != 'completed' or attempt.get('signature_verified') is not True:
        return 'blocked', attempt.get('reason') or '검증된 입력 어댑터의 완료 결과가 없습니다.'
    if attempt.get('target_verified') is not True or attempt.get('full_input') is not True:
        return 'blocked', attempt.get('reason') or '대상 문장 또는 전체 음성 전달을 확인하지 못했습니다.'
    grade = attempt.get('app_grade')
    outcome = attempt.get('app_outcome')
    if outcome in OUTCOMES:
        evidence = attempt.get('outcome_evidence') or {}
        if (grade is not None or attempt.get('cleanup_ok') is not True or evidence.get('app_outcome') != outcome
                or evidence.get('source') != 'installed_apk_word_icon_pixels'
                or not re.fullmatch(r'[a-f0-9]{64}', str(evidence.get('screen_sha256', '')))
                or not any(str(f).lower().endswith('.png') for f in attempt.get('files', []))):
            return 'blocked', '단어 판정의 캡처 근거가 없거나 등급 정보와 충돌합니다.'
        if not expected['approved'] or not expected.get('expected_outcomes'):
            return 'unverified', '단어 판정은 관찰됐지만 정답·오답 기준의 명시적 승인이 필요합니다. 등급으로 환산하지 않습니다.'
        return ('passed', '실제 단어 판정이 승인한 기대 판정과 일치합니다.') if outcome in expected['expected_outcomes'] else (
            'failed', '실제 단어 판정이 승인한 기대 판정과 다릅니다.')
    if grade not in GRADES or not any(str(f).lower().endswith('.png') for f in attempt.get('files', [])):
        return 'blocked', attempt.get('reason') or '실제 앱 등급과 캡처 근거가 부족합니다.'
    if not expected['approved']:
        return 'unverified', '앱 등급은 관찰됐지만 합격 기준이 미승인되어 검토가 필요합니다.'
    if expected.get('expected_outcomes'):
        return 'unverified', '관찰된 등급만으로는 단어 정답·오답 판정을 확인할 수 없습니다.'
    matched = grade == 'Fail' if expected['expect_reject'] else grade in expected['expected_grades']
    return ('passed', '승인한 기대 등급과 실제 앱 등급이 일치합니다.') if matched else (
        'failed', '실제 앱 등급이 승인한 기대 등급과 다릅니다.')


def conclude(attempts, expected):
    if not attempts:
        return 'blocked', '음성 입력을 수행하지 못했습니다.'
    judgments = [verdict(a, expected) for a in attempts]
    if len(attempts) > 1 and any(attempts[0].get(k) != attempts[-1].get(k) for k in ('app_grade', 'app_outcome')):
        if any(s in {'blocked', 'stopped'} for s, _ in judgments):
            return judgments[-1]
        return 'unverified', '동일 음성 재시도에서 앱 판정이 달라졌습니다. 최초 결과를 보존했으며 검토가 필요합니다.'
    return judgments[-1]


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.lock = threading.RLock()

    def add_asset(self, data, filename, metadata=None):
        metadata = metadata or {}
        if not isinstance(metadata.get('source', {}), dict):
            raise ValueError('음원 출처 정보의 형식이 잘못됐습니다.')
        pcm, details = pcm_audio(data, Path(filename).suffix, metadata)
        original_hash = digest(data)
        # Re-importing an unchanged recording must not accumulate duplicate copies.
        with self.lock:
            for existing in self.assets():
                if (existing['original_sha256'] == original_hash and existing['name'] == Path(filename).name[:180]
                        and existing['kind'] == str(metadata.get('kind') or 'upload')[:40]
                        and existing['word_id'] == str(metadata.get('word_id') or '')[:80]
                        and existing['text'] == str(metadata.get('text') or '')[:2000]):
                    self.asset(existing['id'])
                    return existing
            return self._write_asset(data, filename, metadata, pcm, details, original_hash)

    def _write_asset(self, data, filename, metadata, pcm, details, original_hash):
        key = uuid.uuid4().hex
        folder = self.root / 'assets' / key
        folder.mkdir(parents=True)
        (folder / ('original' + Path(filename).suffix.lower())).write_bytes(data)
        (folder / 'input.pcm').write_bytes(pcm)
        with wave.open(str(folder / 'preview.wav'), 'wb') as wav:
            wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            wav.writeframes(pcm)
        record = {**details, 'id': key, 'name': Path(filename).name[:180],
                  'created_at': now(), 'sha256': digest(pcm), 'original_sha256': digest(data),
                  'kind': str(metadata.get('kind') or 'upload')[:40],
                  'text': str(metadata.get('text') or '')[:2000],
                  'word_id': str(metadata.get('word_id') or '')[:80],
                  'source': copy.deepcopy(metadata.get('source') or {}), 'transforms': []}
        atomic_json(folder / 'asset.json', record)
        return record

    def asset(self, key):
        folder = self.root / 'assets' / checked_id(key)
        record = json.loads((folder / 'asset.json').read_text(encoding='utf-8'))
        data = (folder / 'input.pcm').read_bytes()
        if digest(data) != record['sha256']:
            raise ValueError('저장된 음성 해시가 다릅니다. 원본을 다시 확인하세요.')
        return record, folder / 'input.pcm'

    def assets(self):
        rows = []
        for p in (self.root / 'assets').glob('*/asset.json'):
            try:
                row = json.loads(p.read_text(encoding='utf-8'))
                if row.get('kind') == 'device_recording':
                    fields = row.get('source', {}).get('engine_fields', {})
                    row['input_record'] = {k: str(fields[k])[:80] for k in ('recogState', 'mode', 'recMs') if k in fields}
                # Engine raw logs remain in the evidence store, not the list API.
                row['source'] = {k:v for k,v in row.get('source', {}).items()
                                 if k not in {'txt_base64', 'engine_fields'}}
                rows.append(row)
            except (OSError, ValueError):
                continue
        return sorted(rows, key=lambda a: a.get('created_at', ''), reverse=True)

    def folder(self, key):
        return self.root / SUBJECT / checked_id(key)

    def save(self, run):
        with self.lock:
            run['updated_at'] = now()
            atomic_json(self.folder(run['id']) / 'run.json', run)

    def get(self, key):
        with self.lock:
            return json.loads((self.folder(key) / 'run.json').read_text(encoding='utf-8'))

    def history(self):
        # Share the writer lock: Windows forbids replace while a reader has the file open.
        with self.lock:
            return self._history()

    def _history(self):
        rows = []
        for p in (self.root / SUBJECT).glob('*/run.json'):
            try:
                rows.append(json.loads(p.read_text(encoding='utf-8')))
            except (OSError, ValueError):
                continue
        return sorted(rows, key=lambda r: r.get('started_at', ''), reverse=True)

    def recover(self):
        for run in self.history():
            if run.get('status') in {'running', 'stopping'}:
                run.update(status='interrupted', cleanup_required=True,
                           reason='서버 재시작으로 중단됐습니다. 새 실행 전 기기 정리 상태를 확인하세요.')
                self.save(run)

    def file(self, key, filename):
        root = self.folder(key).resolve()
        path = (root / filename).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() not in {'.png', '.json', '.txt', '.log', '.xml', '.wav', '.pcm'}:
            raise ValueError('허용되지 않은 증거 경로입니다.')
        return path


def workbook(run):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = Workbook()
    summary = wb.active
    summary.title = '회차 요약'
    for key, value in [('회차', run['id']), ('과목', '더위크 일본어 프리미엄'), ('상태', run['status']),
                       ('기기', run.get('device')), ('시작', run.get('started_at')), ('완료', run.get('finished_at')),
                       ('단원 완료', run.get('unit_completed')), ('PC 스피커', '사용하지 않음')]:
        summary.append([key, json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value])
    cases = wb.create_sheet('문장별 결과')
    cases.append(['WordID', '문장', '자동화 결과', '앱 등급', '비고', 'Level', 'Week', 'Stage', '기준 승인', '화면 점수', '단어 판정'])
    attempts = wb.create_sheet('입력 시도')
    attempts.append(['WordID', '시도', '앱 등급', '엔진 상태', '전체 입력', '음성 SHA256', '비고', '화면 점수', '단어 판정'])
    words = wb.create_sheet('내부 점수')
    words.append(['WordID', '시도', '점수 종류', '관찰값'])
    evidence = wb.create_sheet('증거')
    evidence.append(['WordID', '시도', '파일'])
    for item in run.get('results', []):
        history = item.get('attempts', [])
        cases.append([item['word_id'], item['text'], item['status'], history[-1].get('app_grade') if history else None,
                      item.get('reason'), item.get('level'), item.get('week'), item.get('stage'), item.get('policy', {}).get('approved'),
                      history[-1].get('app_score') if history else None,
                      history[-1].get('app_outcome') if history else None])
        for n, a in enumerate(history, 1):
            attempts.append([item['word_id'], n, a.get('app_grade'), a.get('engine_state'), a.get('full_input'), a.get('asset_sha256'), a.get('reason'), a.get('app_score'), a.get('app_outcome')])
            for kind, values in (a.get('scores') or {}).items():
                words.append([item['word_id'], n, kind, json.dumps(values, ensure_ascii=False)])
            for file in a.get('files', []):
                evidence.append([item['word_id'], n, file])
    for sheet in wb:
        sheet.freeze_panes = 'A2'
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor='29323D')
        for row in sheet:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = 's'
                cell.alignment = Alignment(wrap_text=True, vertical='top')
        for col in sheet.columns:
            sheet.column_dimensions[col[0].column_letter].width = 26
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return out
