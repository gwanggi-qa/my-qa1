"""Source-bound, deterministic navigation runner. No AI, retries or recovery inputs.

Mappings are explicit local reviews, not inferred from TC Type. Until a mapping
has been reviewed against the real app it must not be submitted for execution.
Only the existing three read-only navigation targets are supported in v1.
"""
import copy
import threading
import time
import uuid
import re
from pathlib import Path
import execution_results as records
import ui_guard
import reviewed_tc

class Stopped(Exception):
    pass

class EnvironmentError(RuntimeError):
    pass

class ProductMismatch(AssertionError):
    pass

def clauses(value):
    return [s.strip() for s in re.split(r'[\n;]+|\s+및\s+|\s+그리고\s+|\s+and\s+', str(value or '')) if s.strip()]

def validate_atomic_check(check):
    """A whole sentence cannot be credited merely by attaching one matching label.

    v1 supports only literal label presence/absence. Counts, calculations, layout,
    conjunctions and arbitrary natural-language paraphrases require another verifier.
    """
    selector = check['selector']
    label = selector.get('text') or selector.get('content-desc')
    if not isinstance(label, str) or not label.strip():
        raise ValueError('Text assertions need an explicit visible label as well as any resource ID')
    compact = lambda v: re.sub(r"[\s\"'‘’“”]+", '', v).rstrip('.!?').casefold()
    source, label = compact(check['source']), compact(label)
    positive = ('표시', '표시된다', '출력된다', '노출된다', '존재한다', '보인다', '있다',
                'visible', 'displayed', 'exists', 'isvisible', 'isdisplayed', 'ispresent')
    negative = ('표시되지않는다', '표시되지않음', '노출되지않는다', '존재하지않는다', '없다',
                '없음', '사라진다', 'notvisible', 'notdisplayed', 'absent', 'notpresent', 'isabsent')
    forms = {label + particle + verb for particle in ('', '이', '가', '은', '는')
             for verb in (positive if check['present'] else negative)}
    if source not in forms:
        raise ValueError('This condition is not a supported atomic text assertion; a full verifier is required')

def validate_mapping(step, mapping, metadata):
    original = records.source(step, metadata)
    if not isinstance(mapping, dict) or set(mapping) != {'source_digest', 'review', 'stages'}:
        raise ValueError('Mapping needs source_digest, review and stages')
    if mapping['source_digest'] != records.fingerprint(original):
        raise ValueError('Mapping belongs to a different TC/source version')
    review = mapping['review']
    if not isinstance(review, dict) or review.get('confirmed') is not True or not str(review.get('evidence', '')).strip():
        raise ValueError('Explicit real-screen mapping review and evidence are required')
    stages = mapping['stages']
    if not isinstance(stages, list) or not 1 <= len(stages) <= 20:
        raise ValueError('Every mandatory procedure requires a reviewed stage')
    procedure = clauses(original['procedure'])
    if [s.get('source') for s in stages] != procedure:
        raise ValueError('Missing, reordered or extra original procedure')
    all_expected, all_preconditions = [], []
    allowed_preconditions = set(clauses(original['precondition'])) | set(clauses(original['expected']))
    for stage in stages:
        if set(stage) != {'source', 'action', 'selector', 'preconditions', 'checks', 'screen'}:
            raise ValueError('Unexpected stage fields')
        target = reviewed_tc.PROCEDURES.get(re.sub(r'\s+', '', stage['source']))
        if stage['action'] != 'tap' or not target or reviewed_tc.RISK.search(stage['source'] + original['precondition']):
            raise ValueError('Only reviewed read-only help/settings/product navigation is supported')
        if not isinstance(stage['selector'], dict) or stage['selector'].get('text', stage['selector'].get('content-desc')) != target:
            raise ValueError('Selector does not match the original navigation target')
        # Validate syntax without inventing a device mapping.
        if set(stage['selector']) - {'text', 'content-desc', 'resource-id', 'package', 'class'}:
            raise ValueError('Unsupported selector')
        if not stage['screen'] or not isinstance(stage['screen'], dict):
            raise ValueError('A post-action screen anchor is required to distinguish mapping failure')
        if not isinstance(stage['preconditions'], list) or not stage['preconditions'] or not isinstance(stage['checks'], list) or not stage['checks']:
            raise ValueError('Each action needs preconditions and postconditions')
        for group in ('preconditions', 'checks'):
            for check in stage[group]:
                if not isinstance(check, dict) or set(check) != {'source', 'selector', 'present'} or type(check['present']) is not bool:
                    raise ValueError('Checks need literal source, selector and present boolean')
                if not isinstance(check['selector'], dict) or not check['selector']:
                    raise ValueError('Missing check selector')
                if group == 'preconditions' and check['source'] not in allowed_preconditions:
                    raise ValueError('Unrelated precondition mapping')
                validate_atomic_check(check)
                (all_preconditions if group == 'preconditions' else all_expected).append(check['source'])
    if not clauses(original['expected']) or set(all_expected) != set(clauses(original['expected'])):
        raise ValueError('Expected conditions are missing or unrelated')
    if not clauses(original['precondition']) or not set(clauses(original['precondition'])).issubset(all_preconditions):
        raise ValueError('Original preconditions are missing')
    return copy.deepcopy(mapping)

class DeviceAdapter:
    def __init__(self, host, run_id):
        self.host, self.run_id = host, run_id
    def environment(self):
        if not self.host.check_device():
            raise EnvironmentError('No connected authorized ADB device')
        return {'device': self.host.get_device_info(), 'app': self.host.foreground_app_evidence()}
    def observe(self):
        try:
            xml = self.host.dump_ui_xml()
            png = self.host.capture_screen_png()
            ui_guard.nodes(xml)
            return xml, png
        except Exception as exc:
            raise EnvironmentError('Cannot capture device evidence: ' + str(exc)) from exc
    def evidence(self, observation, label):
        xml, png = observation
        filename = 'execution-' + self.run_id + '-' + uuid.uuid4().hex + '-' + label + '.png'
        path = self.host.QA_RESULTS_DIR / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png)
        import hashlib
        return {'file': filename, 'url': self.host.qa_result_screenshot_url(path),
                'sha256': hashlib.sha256(png).hexdigest(), 'captured_at': records.now()}
    def tap(self, point):
        _, _, rc = self.host.adb('shell', 'input', 'tap', *map(str, point), timeout_s=8)
        if rc:
            raise EnvironmentError('Input outcome uncertain; no automatic retry')

class Run:
    def __init__(self, host, steps, metadata, mappings, adapter=None, method='automated_device'):
        if len(steps) != len(mappings) or not 1 <= len(steps) <= 20:
            raise ValueError('Select 1-20 uniquely identified TC mappings')
        identities = [(s.get('sheet'), s.get('row'), s.get('case_no')) for s in steps]
        if len(set(identities)) != len(identities) or any(not all(x) for x in identities):
            raise ValueError('Missing or duplicate source TC ID/sheet/row')
        self.host = host
        self.metadata = copy.deepcopy(metadata)
        self.steps = copy.deepcopy(steps)
        self.mappings = [validate_mapping(s, m, metadata) for s, m in zip(steps, mappings)]
        self.plan_digest = records.fingerprint(self.mappings)
        self.data = records.new_run(steps, metadata, method)
        self.data['mapping_digest'] = self.plan_digest
        self.data['mapping_reviews'] = [copy.deepcopy(m['review']) for m in self.mappings]
        self.adapter = adapter or DeviceAdapter(host, self.data['run_id'])
        self.cancel = threading.Event()
        self.lock = threading.RLock()
        self.finished = False
        self.started = False

    def snapshot(self):
        with self.lock:
            return records.normalize(self.data)

    def stop(self):
        self.cancel.set()  # Set before waiting on an in-flight input.
        with self.host._adb_input_lock:
            pass

    def guard(self):
        if self.cancel.is_set():
            raise Stopped('Stopped; remaining actions were not sent')
        if self.host._test_metadata.get('upload_id') != self.metadata.get('upload_id'):
            raise ui_guard.MappingError('Upload replaced; stale run invalidated')
        if records.fingerprint(self.mappings) != self.plan_digest:
            raise ui_guard.MappingError('Reviewed mapping changed')

    def checks(self, xml, checks):
        ui_guard.nodes(xml)
        return [c for c in checks if (ui_guard.select(xml, c['selector'], optional=True) is not None) != c['present']]

    def execute(self):
        with self.lock:
            if self.started:
                raise ValueError('A run cannot be replayed; prepare a new run with fresh observations')
            self.started = True
        try:
            self.guard()
            env = self.adapter.environment()
            with self.lock:
                self.data['environment'] = env
            self.guard()
            if getattr(self,'review_reset_token',None) is not None:
                self.host.begin_redmine_review_run(self.data['run_id'],self.review_reset_token)
            for case, mapping in zip(self.data['results'], self.mappings):
                self.guard()
                with self.lock:
                    self.data['current_tc'] = case['source']['tc_id']
                    case.update(status='running', started_at=records.now(), reason='')
                    case['stages'] = [{'number': i, 'source': s['source'], 'action': s['action'], 'selector': copy.deepcopy(s['selector']), 'preconditions': copy.deepcopy(s['preconditions']),
                        'expected': copy.deepcopy(s['checks']), 'actual': '', 'status': 'not_run', 'error_kind': None,
                        'preconditions_verified': False, 'expectations_verified': False,
                        'evidence': [], 'started_at': None, 'finished_at': None} for i,s in enumerate(mapping['stages'],1)]
                for stage, spec in zip(case['stages'], mapping['stages']):
                    started = time.monotonic()
                    with self.lock:
                        self.data['current_step'] = stage['number']
                        stage.update(status='running', started_at=records.now())
                    try:
                        self.guard()
                        before = self.adapter.observe()
                        with self.lock:
                            stage['evidence'].append(self.adapter.evidence(before, 'before'))
                        if self.checks(before[0], spec['preconditions']):
                            raise ui_guard.MappingError('Preconditions do not match the reviewed screen')
                        ui_guard.stamp(*before, spec['selector'])
                        with self.lock:
                            stage['preconditions_verified'] = True
                        with self.host._adb_input_lock:
                            self.guard()
                            fresh = self.adapter.observe()
                            point = ui_guard.unchanged(*before, *fresh, spec['selector'])
                            self.guard()
                            with self.lock:
                                stage['input_point'] = point
                                stage['input_dispatched_at'] = records.now()
                            self.adapter.tap(point)
                        # Bounded settling, cancelled immediately; never replay input.
                        if self.cancel.wait(0.15):
                            raise Stopped('Stopped after input; expectation not verified')
                        after = self.adapter.observe()
                        self.guard()
                        with self.lock:
                            stage['evidence'].append(self.adapter.evidence(after, 'after'))
                        ui_guard.select(after[0], spec['screen'])
                        missing = self.checks(after[0], spec['checks'])
                        with self.lock:
                            stage['actual'] = 'Observed: ' + json_checks(spec['checks'], missing)
                        if missing:
                            raise ProductMismatch(stage['actual'])
                        with self.lock:
                            stage.update(status='passed', expectations_verified=True)
                    except Stopped as exc:
                        with self.lock:
                            stage.update(status='stopped', error_kind='cancelled', actual=str(exc))
                        raise
                    except Exception as exc:
                        kind = ('product_mismatch' if isinstance(exc, ProductMismatch) else
                                'mapping' if isinstance(exc, (ui_guard.MappingError, ValueError)) else
                                'environment' if isinstance(exc, (EnvironmentError, OSError)) else 'internal')
                        with self.lock:
                            stage.update(status='failed' if kind == 'product_mismatch' else 'blocked', error_kind=kind, actual=str(exc))
                        break
                    finally:
                        with self.lock:
                            stage.update(finished_at=records.now(), elapsed_ms=int((time.monotonic()-started)*1000))
                with self.lock:
                    records.finish_case(case)
                    if case['status'] == 'blocked':
                        failed_stage = next((s for s in case['stages'] if s['status'] == 'blocked'), None)
                        if failed_stage:
                            case.update(error_kind=failed_stage['error_kind'], reason=failed_stage['actual'])
                if getattr(self,'review_reset_token',None) is not None:
                    self.host.record_tc_review_result(self.data,case,case['source']['original'],self.metadata,'reviewed')
                if case['error_kind'] in {'environment', 'internal'}:
                    for remaining in self.data['results']:
                        if remaining['status'] == 'not_run':
                            records.finish_case(remaining, status='not_run', error_kind=case['error_kind'],
                                                reason='Previous input/environment outcome uncertain; execution halted')
                    break
        except Exception as exc:
            with self.lock:
                status = 'stopped' if isinstance(exc, Stopped) else 'blocked'
                kind = 'cancelled' if status == 'stopped' else 'mapping' if isinstance(exc, ui_guard.MappingError) else 'environment'
                for case in self.data['results']:
                    if case['status'] in {'running', 'not_run'}:
                        records.finish_case(case, status=status if case['status'] == 'running' or kind != 'cancelled' else 'not_run',
                                            reason=str(exc), error_kind=kind)
        finally:
            with self.lock:
                self.data.update(state='stopped' if self.cancel.is_set() else 'finished', finished_at=records.now(),
                                 current_tc=None, current_step=None)
                self.finished = True
            self.host.append_test_run_history(self.snapshot())

def json_checks(checks, missing):
    return '; '.join(('mismatch ' if c in missing else 'met ') + c['source'] for c in checks)

