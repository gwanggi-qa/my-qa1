"""Disposable localhost integration server. Uses synthetic data only."""
import argparse
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from flask import Flask
from local_access import LocalAccessPolicy
import app_analysis
import figma_import


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--real-ai', action='store_true')
    args = parser.parse_args()
    import mirror_server as m
    app = Flask('draft-integration', static_folder=str(ROOT / 'static'))
    policy = LocalAccessPolicy('127.0.0.1', 5015, ['localhost', '127.0.0.1'])
    policy.install(app)
    with tempfile.TemporaryDirectory(prefix='qa-drafts-check-') as folder:
        def runner(instructions, context, metrics, schema, cancel, stop_type, image):
            if args.real_ai:
                return m.request_draft_ai_json(instructions, context, metrics, schema, cancel, stop_type, image)
            metrics['planner_calls'] += 1
            eid = next(e['id'] for e in context['evidence'] if e['reference_id'])
            case = {'title': '학습 관리 LC·RC 표시', 'objective': '두 결과 영역 표시 확인',
                    'depth1': '학습 관리', 'depth2': '실력 분석', 'depth3': 'LC/RC', 'test_type': 'UI-UX',
                    'preconditions': '합성 학습 관리 화면 진입', 'steps': ['학습 관리 화면 확인'],
                    'expected_results': ['LC와 RC 결과 영역이 표시된다'], 'priority': 'P1',
                    'category': 'normal', 'source_ids': [eid], 'assumptions': ['합성 테스트 자료이며 실기기에서 실행하지 않음']}
            return {'summary': '합성 초안 UI 검증', 'questions': [], 'cases': [case]}, {'input_tokens': 1, 'output_tokens': 1}
        def status():
            return ({**m.codex_chatgpt_status(), **m.test_ai_identity()} if args.real_ai else
                    {'configured': True, 'provider': 'mock-test-only', 'model': 'fixture'})
        app_analysis.register(app, policy, folder, runner, status)
        store = app.extensions['app_analysis'].store
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('AndroidManifest.xml', b'synthetic fixture, not an actual app')
        stream.seek(0)
        sha = store.register(stream, 'synthetic-qa-fixture.apk')[0]['sha256']
        document = {'name': 'Synthetic learning design', 'version': '123', 'nodes': {'1:2': {'document': {
            'id': '1:2', 'type': 'FRAME', 'name': 'Learning management', 'children': [
                {'id': '1:3', 'type': 'TEXT', 'name': 'LC', 'characters': 'LC results'},
                {'id': '1:4', 'type': 'TEXT', 'name': 'RC', 'characters': 'RC results'}]}}}}
        def figma_json(path, params, token):
            return document if path.startswith('files/') else {'images': {'1:2': 'https://s3-alpha.figma.com/fixture'}}
        image = (ROOT / 'qa_results/reference-ui/fixtures/design.png').read_bytes()
        with patch.dict(os.environ, {'FIGMA_ACCESS_TOKEN': 'fixture-only-not-real'}), \
             patch.object(figma_import, 'api_json', side_effect=figma_json), \
             patch.object(figma_import, 'fetch', return_value=image):
            print(json.dumps({'ready': True, 'build': sha, 'real_ai': args.real_ai}), flush=True)
            app.run(host='127.0.0.1', port=5015, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
