"""Versioned result records. Legacy provenance is preserved, never promoted."""
import copy
import hashlib
import json
import uuid
from datetime import datetime, timezone

METHODS = {'automated_device', 'manual_observation', 'software_test', 'legacy_unknown'}
TERMINAL = {'passed', 'failed', 'blocked', 'stopped', 'not_run', 'unverified'}

def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')

def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def source(step, metadata):
    return {'tc_id': str(step.get('case_no') or ''), 'sheet': step.get('sheet'), 'row': step.get('row'),
            'version': metadata.get('file_hash', ''), 'upload_id': metadata.get('upload_id', ''),
            'filename': metadata.get('filename', ''), 'title': step.get('title') or step.get('description', ''),
            'precondition': str(step.get('precondition') or ''),
            'procedure': str(step.get('test_item') or step.get('procedure') or step.get('description') or ''),
            'expected': str(step.get('qa_expected') or step.get('expected') or ''),
            'original': copy.deepcopy(step)}

def new_run(steps, metadata, method, environment=None, run_id=None):
    if method not in METHODS:
        raise ValueError('Unknown execution source')
    return {'schema_version': 2, 'run_id': run_id or uuid.uuid4().hex, 'method': method,
            'source_version': metadata.get('file_hash', ''), 'upload_id': metadata.get('upload_id', ''),
            'filename': metadata.get('filename', ''), 'started_at': now(), 'finished_at': None,
            'state': 'running', 'current_tc': None, 'current_step': None,
            'environment': copy.deepcopy(environment or {'device': None, 'app': None, 'reason': 'Not captured'}),
            'results': [{'index': i, 'source': source(s, metadata), 'status': 'not_run', 'error_kind': None,
                         'actual': '', 'reason': 'Not executed', 'stages': [], 'evidence': [],
                         'started_at': None, 'finished_at': None} for i, s in enumerate(steps, 1)]}

def finish_case(case, status=None, reason='', error_kind=None):
    stages = case['stages']
    if status is None:
        if any(s.get('status') == 'failed' and s.get('error_kind') == 'product_mismatch' for s in stages):
            status, error_kind = 'failed', 'product_mismatch'
        elif stages and all(s.get('status') == 'passed' and s.get('preconditions_verified') is True
                            and s.get('expectations_verified') is True and s.get('evidence') for s in stages):
            status = 'passed'
        else:
            status, error_kind = 'blocked', 'incomplete_verification'
    if status == 'passed' and (not stages or any(s.get('status') != 'passed' or not s.get('evidence')
            or s.get('preconditions_verified') is not True or s.get('expectations_verified') is not True for s in stages)):
        status, error_kind, reason = 'blocked', 'incomplete_verification', 'Mandatory stages or evidence missing'
    case.update(status=status, error_kind=error_kind, reason=reason, finished_at=now())
    case['actual'] = '\n'.join(s.get('actual', '') for s in stages if s.get('actual'))
    case['evidence'] = [e for s in stages for e in s.get('evidence', [])]
    return case

def counts(run):
    return {s: sum(r.get('status') == s for r in run.get('results', [])) for s in sorted(TERMINAL)}

def normalize(record):
    if record.get('schema_version') == 2:
        out = copy.deepcopy(record)
        out['counts'] = counts(out)
        return out
    reviewed = record.get('verification_method') == 'operator-reviewed-local'
    metadata = {k: record.get(k, '') for k in ('file_hash', 'upload_id', 'filename')}
    out = new_run([], metadata, 'manual_observation' if reviewed else 'legacy_unknown',
                  run_id=record.get('run_id') or 'legacy-' + fingerprint(record)[:24])
    out.update(state='stopped' if record.get('stopped') else 'finished', started_at=record.get('created_at'), finished_at=record.get('created_at'), legacy=True)
    for item in record.get('results', []):
        step = item.get('tc') or {}
        if not isinstance(step, dict):
            step = {'case_no': str(step)}
        index = item.get('index', len(out['results']) + 1)
        original_steps = record.get('steps') or []
        if isinstance(index, int) and 0 < index <= len(original_steps):
            step = {**original_steps[index-1], **step}
        status = item.get('status', 'unverified')
        if status == 'passed' and not reviewed:
            status = 'unverified'
        screenshot = item.get('screenshot_file') or (item.get('visual_verification') or {}).get('screenshot_file')
        out['results'].append({'index': index, 'source': source(step, metadata), 'status': status,
            'legacy_status': item.get('status'), 'error_kind': 'legacy_evidence', 'actual': item.get('message', ''),
            'reason': 'Legacy record; provenance/step coverage not established' if not reviewed else 'Operator-reviewed local observation',
            'stages': copy.deepcopy(item.get('actions') or item.get('ai_trace') or []),
            'evidence': [{'file': screenshot, 'url': '/api/results/screenshot/' + screenshot}] if screenshot else [],
            'started_at': None, 'finished_at': None, 'legacy_record': copy.deepcopy(item)})
    out['counts'] = counts(out)
    return out

def read_history(path, limit=100):
    if not path.exists():
        return [], []
    records, warnings = {}, []
    with path.open('rb') as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 8 * 1024 * 1024))
        if f.tell():
            f.readline()
            warnings.append('Only the latest 8 MiB of history is shown')
        for line in f:
            try:
                item = normalize(json.loads(line.decode('utf-8')))
                records[item['run_id']] = item
            except (ValueError, TypeError, AttributeError):
                warnings.append('An unreadable history line was skipped')
    return list(records.values())[-limit:][::-1], list(dict.fromkeys(warnings))

