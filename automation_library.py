"""Immutable approved workflow versions. Artifacts are data, never exec/import input."""
import copy
import json
import re
import uuid
from pathlib import Path
import execution_results as records

REVISION = 1


def source_key(steps, metadata):
    return records.fingerprint({'steps': steps, 'project': metadata.get('project_id', ''),
                                'file_hash': metadata.get('file_hash')})


def code_for(document):
    # Values are encoded as a JSON string literal, not interpolated executable code.
    payload = json.dumps(document, ensure_ascii=False, sort_keys=True)
    return ('# Generated approved workflow; use the server validator/runtime.\n'
            'import json\n\n'
            'def run(runtime):\n'
            '    return runtime.execute_approved(json.loads(' + repr(payload) + '))\n')


class Library:
    def __init__(self, root):
        self.root = Path(root)

    def path(self, version):
        if not isinstance(version, str) or not re.fullmatch('[a-f0-9]{32}', version):
            raise ValueError('Invalid automation version')
        return self.root / (version + '.json')

    def save(self, document):
        item = copy.deepcopy(document)
        item.update(version=uuid.uuid4().hex, revision=REVISION, approved_at=records.now())
        item['digest'] = records.fingerprint(item)
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.path(item['version'])
        temporary = target.with_suffix('.tmp')
        with temporary.open('x', encoding='utf-8') as f:
            json.dump(item, f, ensure_ascii=False, indent=2)
        temporary.replace(target)
        return item

    def read(self, version):
        try:
            item = json.loads(self.path(version).read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise ValueError('저장된 자동화 버전을 읽을 수 없습니다.') from exc
        digest = item.pop('digest', None)
        if item.get('version') != version or item.get('revision') != REVISION or records.fingerprint(item) != digest:
            raise ValueError('자동화 버전이 변경되었거나 실행기 재검증이 필요합니다.')
        item['digest'] = digest
        return item

    def listing(self):
        result = []
        for path in self.root.glob('*.json'):
            try:
                item = self.read(path.stem)
            except ValueError:
                continue
            result.append({k: item.get(k) for k in ('version', 'approved_at', 'name', 'source_key',
                                                   'engine', 'ai_required', 'environment')})
        return sorted(result, key=lambda x: x['approved_at'], reverse=True)
