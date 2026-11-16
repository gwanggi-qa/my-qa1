"""Speech QA orchestration. No cloud calls, speaker playback, or automatic issue publishing."""
import copy
import json
import re
import shutil
import threading
import traceback
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, make_response, request, send_file

import execution_results
from speech_verification import Store, SUBJECT, checked_id, conclude, now, policy, verdict, workbook


class SelectedDevice:
    """Bind navigation to the chosen serial without changing the mirror's global device."""
    def __init__(self, host, serial):
        self.host, self.ADB_SERIAL = host, serial
        self._adb_input_lock = host._adb_input_lock

    def adb(self, *args, timeout_s=8):
        configured = self.host.ADB_SERIAL
        if configured and configured != self.ADB_SERIAL:
            raise ValueError('미러링 기기와 선택 기기가 다릅니다.')
        return self.host.adb(*args, timeout_s=timeout_s) if configured else self.host.adb('-s', self.ADB_SERIAL, *args, timeout_s=timeout_s)

    def dump_ui_xml(self):
        out, _, rc = self.adb('exec-out', 'uiautomator', 'dump', '/dev/tty')
        text = out.decode('utf-8', 'replace')
        begin, end = text.find('<hierarchy'), text.rfind('</hierarchy>')
        if rc or begin < 0 or end < begin:
            raise ValueError('현재 화면 구조를 읽지 못했습니다.')
        return text[begin:end + len('</hierarchy>')]


class SpeechService:
    def __init__(self, server, root, adapter=None, materials=None, navigator=None):
        self.server = server
        self.store = Store(root)
        self._adapter = adapter
        self._materials = materials
        self._navigator = navigator
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.busy = False
        self.cleanup_required = False
        self.current = None
        self.previews = {}
        self.catalogs = {}
        self.recovered = False

    @property
    def adapter(self):
        if self._adapter is None:
            from speech_device import DeviceAdapter
            self._adapter = DeviceAdapter(self.server(), self.store.root)
        return self._adapter

    @property
    def materials(self):
        if self._materials is None:
            import speech_materials
            self._materials = speech_materials
        return self._materials

    def recover(self):
        with self.lock:
            if not self.recovered:
                self.store.recover()
                history = self.store.history()
                self.current = history[0] if history else None
                self.cleanup_required = any(r.get('cleanup_required') for r in history)
                if self.cleanup_required:
                    self.server()._speech_running = True
                self.recovered = True

    def snapshot(self):
        self.recover()
        with self.lock:
            return {'busy': self.busy, 'run': copy.deepcopy(self.current),
                    'cleanup_required': self.cleanup_required,
                    'subjects': [{'id': SUBJECT, 'name': '더위크 일본어 프리미엄'}]}

    def devices(self):
        # Discovery is read-only. Never silently select the first of several devices.
        out, _, rc = self.server().adb('devices', timeout_s=5)
        if rc:
            return []
        return [{'serial': line.split()[0], 'state': line.split()[1]}
                for line in out.decode('utf-8', 'replace').splitlines()[1:]
                if len(line.split()) >= 2 and line.split()[1] == 'device']

    @staticmethod
    def require_subject(subject):
        # Omission retains the original single-subject API, never a cross-course fallback.
        if subject is not None and subject != SUBJECT:
            raise ValueError('선택한 과목은 음성 인식 검증 연동이 필요합니다.')
        return SUBJECT

    def subjects(self, device):
        if not isinstance(device, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', device):
            raise ValueError('연결 기기를 선택하세요.')
        return self.adapter.subjects(device)

    def catalog(self, device, subject=None):
        self.require_subject(subject)
        if not isinstance(device, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', device):
            raise ValueError('연결 기기를 선택하세요.')
        info = self.adapter.inspect(device)
        if info.get('supported') is not True:
            raise ValueError(info.get('reason') or '지원 여부가 검증되지 않은 앱 빌드입니다.')
        result = self.materials.catalog(device, self.store.root / 'catalog')
        result['device'] = info
        result['subject'] = SUBJECT
        with self.lock:
            self.catalogs[device] = copy.deepcopy(result)
        return result

    def preview(self, payload):
        device = payload.get('device')
        catalog = self.catalog(device, payload.get('subject'))
        mode = payload.get('mode', 'selected')
        if mode not in {'selected', 'unit'}:
            raise ValueError('검사 모드를 확인하세요.')
        choices = payload.get('items')
        if not isinstance(choices, list) or not choices or len(choices) > 200:
            raise ValueError('검사할 문장을 1~200개 선택하세요.')
        by_id = {str(i['word_id']): i for i in catalog['items']}
        if len(by_id) != len(catalog['items']):
            raise ValueError('서로 다른 학습 단계에 중복 WordID가 있습니다. 자동 연결하지 않습니다.')
        selected, seen, issues = [], set(), []
        for choice in choices:
            word_id = str(choice.get('word_id', ''))
            if word_id in seen or word_id not in by_id:
                raise ValueError('중복되거나 현재 DB에 없는 WordID입니다.')
            seen.add(word_id)
            item = copy.deepcopy(by_id[word_id])
            item['word_id'] = word_id
            item['policy'] = policy(choice)
            item['asset_id'] = choice.get('asset_id')
            if item.get('navigation_available') is False:
                issues.append({'word_id': word_id, 'reason': item.get('navigation_reason') or '지원하지 않는 학습 경로입니다.', 'blocking': True})
            try:
                asset, _ = self.store.asset(item['asset_id'])
                item['asset_sha256'] = asset['sha256']
                item['asset_kind'] = asset['kind']
                if asset['duration_s'] > 15:
                    issues.append({'word_id': word_id, 'reason': '직접 주입 어댑터는 문장당 15초 이하를 지원합니다. 음성을 자르지 않고 차단합니다.', 'blocking': True})
            except (ValueError, OSError):
                issues.append({'word_id': word_id, 'reason': '입력 음성이 준비되지 않았습니다.', 'blocking': True})
            if not item['policy']['approved']:
                issues.append({'word_id': word_id, 'reason': '합격 기준 미승인: 실제 등급만 수집하고 검토 대상으로 남깁니다.', 'blocking': False})
            selected.append(item)
        scope = {k: payload.get(k) for k in ('level', 'week', 'stage')}
        if mode == 'unit':
            if any(v is None or str(v) == '' for v in scope.values()):
                raise ValueError('단원 전체 모드에는 Level, Week, Stage를 지정하세요.')
            unit = [i for i in catalog['items'] if all(str(i.get(k)) == str(v) for k, v in scope.items())]
            needed = {str(i['word_id']) for i in unit}
            if not needed or seen != needed:
                issues.append({'reason': '단원 전체의 모든 문장을 선택하고 입력 음성을 준비하세요.', 'blocking': True,
                               'missing_word_ids': sorted(needed - seen)})
            selected.sort(key=lambda i: (int(i.get('session') or 0), int(i.get('order') or 0)))
        conversation_scopes = {self.item_scope(i) for i in selected if i.get('session') == 4}
        for context in conversation_scopes:
            required = {str(i['word_id']) for i in catalog['items'] if self.item_scope(i) == context}
            supplied = {i['word_id'] for i in selected if self.item_scope(i) == context}
            if supplied != required or len(required) != 4:
                issues.append({'reason': '회화는 자동으로 다음 녹음이 시작되므로 같은 Session의 4개 문장과 음원을 함께 준비하세요.', 'blocking': True})
            else:
                issues.append({'reason': '회화는 4개 문장을 연속 입력합니다. 낮은 판정이 있으면 동일 묶음을 최대 한 번 재시도하며 첫 결과도 보존합니다.', 'blocking': False})
        result = {'id': uuid.uuid4().hex, 'created_at': now(), 'device': catalog['device'], 'subject': SUBJECT,
                  'db_sha256': catalog.get('db_sha256'), 'mode': mode, 'scope': scope, 'items': selected,
                  'issues': issues, 'ready': not any(i['blocking'] for i in issues),
                  'retry_limit': 1, 'input_transforms': [], 'speaker_output': False}
        with self.lock:
            self.previews[result['id']] = copy.deepcopy(result)
            while len(self.previews) > 20:
                self.previews.pop(next(iter(self.previews)))
        return result

    @staticmethod
    def item_scope(item):
        return tuple(item.get(k) for k in ('level', 'week', 'stage', 'session'))

    @staticmethod
    def retry_needed(result, expected):
        state, _ = verdict(result, expected)
        return state == 'failed' or (state == 'unverified' and result.get('app_grade') in {'Bad', 'Fail'})

    def conversation(self, run, indices, nav):
        items = [run['results'][i] for i in indices]
        words = all(i.get('kind') == 'word' and i.get('session') == 1 for i in items)
        label = '단어' if words else '회화'
        if len(items) != 4 or any(i['status'] != 'not_run' for i in items):
            for item in items:
                if item['status'] == 'not_run':
                    item.update(status='blocked', reason=label + '의 중간 지점만 재개할 수 없습니다. 같은 Session 4개를 새 회차로 실행하세요.')
            self.checkpoint(run)
            return
        for attempt_no in (1, 2):
            if self.stop_event.is_set():
                return
            run.update(phase=label + ' 화면 준비 중', current_index=indices[0]+1, current_text=items[0]['text'])
            self.checkpoint(run)
            route = (nav.prepare(items[0], mode=run['mode']) if attempt_no == 1
                     else nav.restart_session(items[0], mode=run['mode']))
            if not route.get('ready'):
                for item in items:
                    item.setdefault('navigation', []).extend(route.get('actions', []))
                    if attempt_no == 1:
                        item.update(status='blocked', reason=route.get('reason') or '회화 화면 진입 실패')
                    else:
                        item['retry_unavailable'] = route.get('reason') or '회화 재시도 화면 진입 실패'
                self.checkpoint(run)
                return
            run.update(phase=label + ' 4개 연속 입력·판정 수집 중' if attempt_no == 1 else label + ' 묶음 동일 음성 재시도',
                       current_index=indices[0]+1, current_text=items[0]['text'])
            self.checkpoint(run)
            targets, folders = [], []
            for index, item in zip(indices, items):
                asset, pcm = self.store.asset(item['asset_id'])
                relative = Path('evidence') / ('%03d' % (index+1)) / str(attempt_no)
                folder = self.store.folder(run['id']) / relative
                folder.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(pcm, folder / 'input.pcm')
                shutil.copyfile(pcm.with_name('preview.wav'), folder / 'input.wav')
                folders.append(relative)
                targets.append({'text':item['target'], 'word_id':item['word_id'], 'pcm_path':str(pcm),
                                'kind':item.get('kind'),
                                'meaning':item.get('meaning'),
                                'order':int(item['order']), 'db_sha256':run['db_sha256'],
                                'evidence_dir':str(folder), 'scope':dict(zip(('level','week','stage','session'), self.item_scope(item))),
                                'start_selector':route.get('start_selector') if not targets else None})
            observations = self.adapter.run_sequence(run['device']['serial'], targets, self.stop_event)
            if not isinstance(observations, list) or len(observations) != len(items):
                raise ValueError('Invalid sequence result count')
            for item, relative, observation in zip(items, folders, observations):
                observation.update(asset_sha256=item['asset_sha256'], started_by='automated_device', attempt=attempt_no,
                                   transforms=[], captured_at=now())
                observation['files'] = [(relative / f).as_posix() for f in [*observation.get('files', []), 'input.pcm', 'input.wav']
                                        if not Path(f).is_absolute() and '..' not in Path(f).parts]
                item.setdefault('navigation', []).extend(route.get('actions', []))
                item['attempts'].append(observation)
                item['status'], item['reason'] = conclude(item['attempts'], item['policy'])
                if not observation.get('cleanup_ok', False):
                    self.cleanup_required = run['cleanup_required'] = True
            run.update(current_index=indices[-1]+1, phase='앱 판정 확인')
            self.checkpoint(run)
            if self.cleanup_required or self.stop_event.is_set() or any(i['status'] in {'blocked','stopped'} for i in items):
                return
            if not any(self.retry_needed(o, i['policy']) for o, i in zip(observations, items)):
                return

    def start(self, payload, resume=False):
        self.require_subject(payload.get('subject'))
        if payload.get('approved') is not True:
            raise ValueError('학습 진도 변경과 실행 범위를 확인한 뒤 실행을 승인하세요.')
        self.recover()
        m = self.server()
        with m._automation_start_lock:
            with self.lock:
                if self.busy or self.cleanup_required or m._test_running or m._random_running or m._record_play_running:
                    raise RuntimeError('다른 실행이 진행 중이거나 디버거 해제 확인이 필요합니다.')
                # Clear before slow preflight, never after a concurrent Stop request.
                self.stop_event.clear()
                if resume:
                    previous = self.store.get(payload.get('run_id'))
                    pending = any(i.get('status') in {'not_run','stopped','blocked'} for i in previous.get('results', []))
                    if previous['status'] not in {'stopped', 'interrupted', 'blocked', 'finished'} or not pending:
                        raise ValueError('중단된 회차만 재개할 수 있습니다.')
                    run = copy.deepcopy(previous)
                    original_id = run['id']
                    run.update(id=uuid.uuid4().hex, resumed_from=original_id, started_at=now(), finished_at=None,
                               status='running', reason='', unit_completed=False)
                    # A resumed attempt is a new run; past evidence keeps its original run URL.
                    for result in run['results']:
                        if result['status'] not in {'passed', 'failed', 'unverified'}:
                            result.update(status='not_run', attempts=[], reason='재개 대기')
                            result.pop('inherited_from', None)
                        else:
                            result.setdefault('inherited_from', original_id)
                else:
                    preview = self.previews.get(payload.get('preview_id'))
                    if not preview or not preview['ready']:
                        raise ValueError('현재 기기의 실행 미리보기를 먼저 확인하세요.')
                    run = copy.deepcopy(preview)
                    run.update(id=uuid.uuid4().hex, preview_id=preview['id'], subject=SUBJECT,
                               started_at=now(), finished_at=None, status='running', unit_completed=False,
                               source='automated_device', results=[{**i, 'status': 'not_run', 'attempts': [], 'reason': ''} for i in preview['items']])
                device = run['device']['serial']
                actual = self.adapter.inspect(device)
                if not actual.get('supported') or any(actual.get(k) != run['device'].get(k) for k in ('serial', 'package', 'build', 'apk_sha256')):
                    raise ValueError('기기 또는 앱 빌드가 달라졌습니다. 미리보기를 다시 준비하세요.')
                # DB identity and audio hashes are rechecked before every new/resumed run.
                current_catalog = self.materials.catalog(device, self.store.root / 'catalog')
                if current_catalog.get('db_sha256') != run.get('db_sha256'):
                    raise ValueError('학습 DB가 바뀌었습니다. 미리보기를 다시 준비하세요.')
                for item in run['results']:
                    asset, _ = self.store.asset(item['asset_id'])
                    if asset['sha256'] != item['asset_sha256']:
                        raise ValueError('입력 음성이 변경됐습니다.')
                if self.stop_event.is_set():
                    raise RuntimeError('사용자가 실행 준비를 중단했습니다. 기기 입력은 수행하지 않았습니다.')
                self.busy, m._speech_running = True, True
                self.current = run
                try:
                    self.store.save(run)
                    worker = threading.Thread(target=self.execute, args=(run,), daemon=True, name='speech-verification')
                    worker.start()
                except Exception:
                    self.busy, m._speech_running = False, False
                    run['status'] = 'interrupted'
                    try:
                        self.store.save(run)
                    except OSError:
                        pass
                    raise
        return copy.deepcopy(run)

    def checkpoint(self, run):
        with self.lock:
            self.current = run
            self.store.save(run)

    def execute(self, run):
        m = self.server()
        terminal_status = 'blocked'
        try:
            if self._navigator is None:
                from speech_navigation import Navigator
                nav = Navigator(SelectedDevice(m, run['device']['serial']), run['device']['serial'], self.stop_event)
            else:
                nav = self._navigator(m, run['device']['serial'], self.stop_event)
            for index, item in enumerate(run['results']):
                if self.stop_event.is_set():
                    break
                if item['status'] in {'passed', 'failed', 'unverified'}:
                    continue
                if item.get('kind') == 'word' and item.get('session') == 1:
                    indices = sorted([i for i, other in enumerate(run['results'])
                                      if other.get('kind') == 'word' and self.item_scope(other) == self.item_scope(item)],
                                     key=lambda i: run['results'][i]['order'])
                    if len(indices) == 4 and [run['results'][i]['order'] for i in indices] == [1, 2, 3, 4]:
                        if index != min(indices):
                            continue
                        self.conversation(run, indices, nav)
                        if self.cleanup_required or self.stop_event.is_set():
                            break
                        continue
                if item.get('session') == 4:
                    indices = sorted([i for i, other in enumerate(run['results']) if self.item_scope(other) == self.item_scope(item)],
                                     key=lambda i: run['results'][i]['order'])
                    if index != min(indices):
                        continue
                    self.conversation(run, indices, nav)
                    if self.cleanup_required or self.stop_event.is_set():
                        break
                    if run['mode'] == 'unit' and any(run['results'][i]['status'] in {'blocked','failed'} for i in indices):
                        break
                    continue
                run.update(current_index=index + 1, current_text=item['text'], phase='사전조건 준비')
                self.checkpoint(run)
                route = nav.prepare(item, mode=run['mode'])
                item['navigation'] = route.get('actions', [])
                if not route.get('ready'):
                    item.update(status='blocked', reason=route.get('reason') or '학습 단계에 도달하지 못했습니다.')
                    self.checkpoint(run)
                    if run['mode'] == 'unit':
                        break
                    continue
                for attempt_no in range(1, 3):
                    if self.stop_event.is_set():
                        break
                    if attempt_no == 2:
                        route = nav.retry(item)
                        item['navigation'].extend(route.get('actions', []))
                        if not route.get('ready'):
                            item['retry_unavailable'] = route.get('reason') or '같은 단계 재시도 불가'
                            break
                    run['phase'] = '음성 직접 입력 중' if attempt_no == 1 else '동일 음성 1회 재시도'
                    self.checkpoint(run)
                    asset, pcm = self.store.asset(item['asset_id'])
                    relative = Path('evidence') / ('%03d' % (index+1)) / str(attempt_no)
                    folder = self.store.folder(run['id']) / relative
                    folder.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(pcm, folder / 'input.pcm')
                    shutil.copyfile(pcm.with_name('preview.wav'), folder / 'input.wav')
                    target = {'text': item.get('target') or item['text'], 'word_id': item['word_id'],
                              'start_selector': route.get('start_selector'),
                              'scope': {k:item[k] for k in ('level', 'week', 'stage', 'session') if k in item}}
                    result = self.adapter.run_attempt(run['device']['serial'], target, pcm, folder, self.stop_event)
                    if item.get('kind') == 'word':
                        nav.record_attempt(item, result)
                    result.update(asset_sha256=asset['sha256'], started_by='automated_device', attempt=attempt_no,
                                  transforms=[], captured_at=now())
                    result['files'] = [(relative / f).as_posix() for f in [*result.get('files', []), 'input.pcm', 'input.wav']
                                       if not Path(f).is_absolute() and '..' not in Path(f).parts]
                    item['attempts'].append(result)
                    item['status'], item['reason'] = conclude(item['attempts'], item['policy'])
                    run['phase'] = '앱 판정 확인'
                    if not result.get('cleanup_ok', True):
                        self.cleanup_required = True
                        run['cleanup_required'] = True
                    self.checkpoint(run)
                    if self.cleanup_required:
                        break
                    if not self.retry_needed(result, item['policy']):
                        break
                if self.cleanup_required or self.stop_event.is_set():
                    break
                if run['mode'] == 'unit' and item['status'] in {'blocked', 'failed'}:
                    run['reason'] = '실패/차단된 문장 이후의 단원 진행은 확인하지 않았습니다.'
                    break
            if self.stop_event.is_set():
                terminal_status = 'stopped'
                run['reason'] = '사용자 중단: 부분 입력은 재개 시 새 시도로 수행합니다.'
            elif self.cleanup_required:
                terminal_status = 'blocked'
                run['reason'] = '디버거 해제 확인 필요'
            else:
                if run['mode'] == 'unit' and all(i['status'] in {'passed', 'unverified'} for i in run['results']):
                    completion = nav.complete_unit()
                    run['unit_completed'] = completion.get('unit_completed') is True
                    run['completion_evidence'] = completion
                terminal_status = 'blocked' if any(i['status'] in {'not_run','blocked'} for i in run['results']) else 'finished'
        except Exception as exc:
            # Persist locations, not exception text, locals, commands or secrets.
            terminal_status = 'blocked'
            run['reason'] = '실행 환경 또는 화면 처리 오류: ' + type(exc).__name__
            run['error_diagnostic'] = {'type': type(exc).__name__, 'frames': [
                {'file': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
                for frame in traceback.extract_tb(exc.__traceback__)]}
        finally:
            # Serialize terminal publication with Stop; busy stays true through persistence.
            with self.lock:
                run.update(status=terminal_status, finished_at=now(),
                           phase='완료' if terminal_status == 'finished' else '중단 또는 확인 필요')
            try:
                self.checkpoint(run)
                m.append_test_run_history(self.dashboard(run))
            except OSError:
                with self.lock:
                    run.update(status='interrupted', reason='결과 저장 실패: 로컬 저장소의 권한과 여유 공간을 확인하세요.')
                    self.current = run
            finally:
                with m._automation_start_lock:
                    with self.lock:
                        self.busy = False
                        m._speech_running = self.cleanup_required

    def dashboard(self, run):
        steps = [{'case_no': 'SPEECH-' + i['word_id'], 'title': i['text'], 'expected': json.dumps(i['policy'], ensure_ascii=False),
                  'procedure': '원본 음성 직접 주입 후 실제 앱 등급 확인'} for i in run['results']]
        result = execution_results.new_run(steps, {'filename': '음성 인식 검증', 'file_hash': run.get('db_sha256'),
                                                  'project_id': 'japanese-premium'}, 'automated_device', run['device'], run_id=run['id'])
        result.update(started_at=run['started_at'], finished_at=run.get('finished_at'), state=run['status'], speech_run=True)
        for record, item in zip(result['results'], run['results']):
            record.update(status=item['status'], reason=item['reason'], actual=' / '.join(str(a.get('app_grade') or a.get('app_outcome') or '판정 없음') for a in item['attempts']),
                          evidence=[{'file': f, 'url': '/api/speech-verification/runs/' + item.get('inherited_from', run['id']) + '/files/' + f}
                                    for a in item['attempts'] for f in a.get('files', [])],
                          speech_attempts=copy.deepcopy(item['attempts']),
                          verification_scope='speech_word_outcome_policy' if item.get('kind') == 'word' else 'speech_grade_policy',
                          started_at=run['started_at'], finished_at=run.get('finished_at'))
            for attempt in item['attempts']:
                state, reason = verdict(attempt, item['policy'])
                record['stages'].append({
                    'action': 'speech_input', 'status': state, 'actual': attempt.get('app_grade') or attempt.get('app_outcome'),
                    'reason': reason, 'preconditions_verified': attempt.get('target_verified') is True,
                    'expectations_verified': state == 'passed', 'input_dispatched': attempt.get('input', {}).get('supplied_samples', 0) > 0,
                    'full_input': attempt.get('full_input') is True, 'asset_sha256': attempt.get('asset_sha256'),
                    'evidence': [{'file': f, 'url': '/api/speech-verification/runs/' + item.get('inherited_from', run['id']) + '/files/' + f}
                                 for f in attempt.get('files', [])]})
        return result


def register(app, server, root=None, **dependencies):
    service = SpeechService(server, root or Path(__file__).parent / 'qa_results' / 'speech_verification', **dependencies)
    bp = Blueprint('speech_verification', __name__)

    def body():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError('JSON 객체로 요청하세요.')
        return data

    @bp.errorhandler(ValueError)
    @bp.errorhandler(FileNotFoundError)
    def invalid(exc):
        return jsonify(error=str(exc) if isinstance(exc, ValueError) else '해당 기록이 없습니다.'), 400

    @bp.errorhandler(RuntimeError)
    def busy(exc):
        return jsonify(error=str(exc)), 409

    @bp.get('/speech-verification')
    def page():
        html = (Path(__file__).parent / 'static' / 'speech-verification.html').read_text(encoding='utf-8')
        response = make_response(html.replace('__TOKEN__', server().local_access.token))
        response.headers['Cache-Control'] = 'no-store'
        return server().local_access.bootstrap(response)

    @bp.get('/api/speech-verification/status')
    def status():
        state = service.snapshot()
        state['devices'] = service.devices()
        return jsonify(state)

    @bp.post('/api/speech-verification/catalog')
    def catalog():
        if service.busy:
            raise RuntimeError('검사 중에는 학습 목록을 다시 읽을 수 없습니다.')
        data = body()
        return jsonify(service.catalog(data.get('device'), data.get('subject')))

    @bp.post('/api/speech-verification/subjects')
    def subjects():
        return jsonify(service.subjects(body().get('device')))

    @bp.post('/api/speech-verification/prepare/reset')
    def reset_preparation():
        preview_id = body().get('preview_id')
        if preview_id is not None:
            checked_id(preview_id)
        with service.lock:
            if service.busy:
                raise RuntimeError('검사 실행 중에는 준비 상태를 변경할 수 없습니다.')
            service.previews.pop(preview_id, None)
        return jsonify(reset=True)

    @bp.route('/api/speech-verification/assets', methods=['GET', 'POST'])
    def assets():
        if request.method == 'GET':
            return jsonify(items=service.store.assets())
        service.require_subject(request.form.get('subject'))
        f = request.files.get('file')
        if not f:
            raise ValueError('PCM/WAV 파일을 선택하세요.')
        from speech_verification import MAX_AUDIO_BYTES
        return jsonify(service.store.add_asset(f.read(MAX_AUDIO_BYTES + 65537), f.filename, request.form.to_dict()))

    @bp.post('/api/speech-verification/recordings/import')
    def recordings():
        if service.busy:
            raise RuntimeError('검사 중에는 녹음을 가져올 수 없습니다.')
        data = body()
        device = data.get('device')
        service.catalog(device, data.get('subject'))
        options = {key: data[key] for key in ('limit', 'after') if key in data}
        return jsonify(service.materials.import_recordings(device, service.store.add_asset, **options))

    @bp.post('/api/speech-verification/tts')
    def tts():
        data = body()
        service.require_subject(data.get('subject'))
        text = str(data.get('text') or '').strip()
        if not text or len(text) > 500:
            raise ValueError('TTS 문장은 1~500자로 입력하세요.')
        import tempfile
        with tempfile.TemporaryDirectory(prefix='speech-tts-') as folder:
            path = Path(folder) / 'tts.pcm'
            metadata = service.materials.generate_tts(text, path)
            return jsonify(service.store.add_asset(path.read_bytes(), 'local-tts.pcm', {
                'text': text, 'word_id': str(data.get('word_id') or ''), 'kind': 'local-tts',
                'sample_rate': 16000, 'channels': 1, 'sample_width': 2,
                'source': {k:v for k,v in metadata.items() if k != 'path'}}))

    @bp.post('/api/speech-verification/native')
    def native():
        if service.busy:
            raise RuntimeError('검사 중에는 기준 음원을 가져올 수 없습니다.')
        data = body()
        catalog = service.catalog(data.get('device'), data.get('subject'))
        item = next((i for i in catalog['items'] if str(i['word_id']) == str(data.get('word_id'))), None)
        if item is None:
            raise ValueError('현재 학습 DB에 없는 문장입니다.')
        import tempfile
        with tempfile.TemporaryDirectory(prefix='speech-native-') as folder:
            path = Path(folder) / 'native.pcm'
            meta = service.materials.extract_native(data['device'], item, path)
            if not meta.get('available'):
                raise ValueError(meta.get('reason') or '기준 음원 연결이 확인되지 않았습니다.')
            return jsonify(service.store.add_asset(path.read_bytes(), 'native.pcm', {
                'text': item['text'], 'word_id': str(item['word_id']), 'kind': 'native',
                'sample_rate': 16000, 'channels': 1, 'sample_width': 2,
                'source': {k:v for k,v in meta.items() if k != 'path'}}))

    @bp.post('/api/speech-verification/preview')
    def preview():
        if service.busy:
            raise RuntimeError('검사 중입니다.')
        return jsonify(service.preview(body()))

    @bp.post('/api/speech-verification/run')
    def run():
        return jsonify(service.start(body()))

    @bp.post('/api/speech-verification/resume')
    def resume():
        return jsonify(service.start(body(), resume=True))

    @bp.post('/api/speech-verification/stop')
    def stop():
        service.stop_event.set()
        with service.lock:
            status = 'idle'
            if service.busy and service.current is not None:
                status = service.current['status']
                if status in {'running', 'stopping'}:
                    status = service.current['status'] = 'stopping'
                    service.store.save(service.current)
        return jsonify(status=status)

    @bp.post('/api/speech-verification/cleanup/check')
    def cleanup_check():
        service.recover()
        device = body().get('device')
        with server()._automation_start_lock, service.lock:
            if service.busy:
                raise RuntimeError('중단 처리가 끝난 뒤 연결 해제를 확인하세요.')
            result = service.adapter.check_cleanup(device)
            if result.get('cleanup_ok'):
                for record in service.store.history():
                    if record.get('device', {}).get('serial') == device and record.get('cleanup_required'):
                        record['cleanup_required'] = False
                        service.store.save(record)
                        if service.current and service.current['id'] == record['id']:
                            service.current = record
                service.cleanup_required = any(r.get('cleanup_required') for r in service.store.history())
                server()._speech_running = service.cleanup_required
        return jsonify(result)

    @bp.get('/api/speech-verification/history')
    def history():
        service.recover()
        return jsonify(runs=service.store.history())

    @bp.get('/api/speech-verification/runs/<run_id>')
    def detail(run_id):
        return jsonify(service.store.get(run_id))

    @bp.get('/api/speech-verification/runs/<run_id>/export')
    def export(run_id):
        return send_file(workbook(service.store.get(run_id)), as_attachment=True,
                         download_name='speech-verification-' + checked_id(run_id) + '.xlsx',
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    @bp.get('/api/speech-verification/runs/<run_id>/files/<path:filename>')
    def evidence(run_id, filename):
        return send_file(service.store.file(run_id, filename), as_attachment=True)

    app.register_blueprint(bp)
    return service
