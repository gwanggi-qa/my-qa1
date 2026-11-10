"""Read a linked Figma page or frame. No arbitrary URLs or secret persistence."""
import hashlib
import json
import math
import os
import re
import threading
from urllib.parse import urlsplit, parse_qs, urlencode
import urllib.request
import urllib.error

from app_analysis import AnalysisError
from reference_materials import image_preview

CDN_HOSTS = {'s3-alpha.figma.com', 's3-alpha-sig.figma.com',
             'figma-alpha-api.s3.us-west-2.amazonaws.com'}
IMPORT_LOCK = threading.Lock()
MAX_PAGE_NODES = 20000
MAX_PAGE_TEXT = 1000000


def frame_url(value):
    if not isinstance(value, str) or len(value) > 2048 or re.search(r'[\s\\\x00-\x1f]', value):
        raise AnalysisError('Figma 페이지 또는 프레임 링크를 입력하세요.')
    try:
        url = urlsplit(value)
        match = re.fullmatch(r'/(?:design|file)/([A-Za-z0-9]+)/[^/]*', url.path.rstrip('/'))
        nodes = parse_qs(url.query).get('node-id', [])
        if (url.scheme != 'https' or url.hostname not in {'figma.com', 'www.figma.com'} or
                url.username or url.password or url.port not in (None, 443) or not match or
                len(nodes) != 1 or not re.fullmatch(r'\d+[-:]\d+', nodes[0])):
            raise ValueError()
    except ValueError as exc:
        raise AnalysisError('node-id가 포함된 Figma 페이지 또는 프레임 링크를 사용하세요.') from exc
    key, node = match[1], nodes[0].replace('-', ':')
    canonical = 'https://www.figma.com/design/' + key + '/frame?' + urlencode({'node-id': node})
    return key, node, canonical


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(url, token=None, limit=8 * 1024 * 1024):
    try:
        parsed = urlsplit(url)
        safe = (parsed.scheme == 'https' and not parsed.username and not parsed.password
                and parsed.port in (None, 443) and not re.search(r'[\s\\\x00-\x1f]', url))
    except ValueError:
        raise AnalysisError('Figma returned an invalid download URL.', 502)
    if not safe or parsed.hostname not in ({'api.figma.com'} if token else CDN_HOSTS):
        raise AnalysisError('Figma returned an unsupported download host.', 502)
    headers = {'X-Figma-Token': token} if token else {}
    req = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(req, timeout=20) as response:
            data = response.read(limit + 1)
        if len(data) > limit:
            raise AnalysisError('Figma response exceeds the import limit.', 413)
        return data
    except urllib.error.HTTPError as exc:
        messages = {401: 'Figma authentication failed.', 403: 'Figma file permission or file_content:read scope required.',
                    404: 'Figma file/frame not found.', 429: 'Figma rate limit reached; retry later.'}
        raise AnalysisError(messages.get(exc.code, 'Figma request failed.'),
                            exc.code if exc.code in messages else 502) from exc
    except (OSError, TimeoutError) as exc:
        raise AnalysisError('Figma connection failed or timed out.', 502) from exc


def api_json(path, params, token):
    raw = fetch('https://api.figma.com/v1/' + path + '?' + urlencode(params), token)
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except ValueError as exc:
        raise AnalysisError('Figma returned invalid JSON.', 502) from exc


def normalize(data, node_id):
    inventory = data.get('nodes')
    if not isinstance(inventory, dict):
        raise AnalysisError('Figma returned invalid node inventory.', 502)
    entry = inventory.get(node_id)
    root = entry.get('document') if isinstance(entry, dict) else None
    if not isinstance(root, dict) or root.get('id') != node_id or root.get('type') not in {'CANVAS', 'FRAME', 'COMPONENT', 'INSTANCE', 'SECTION', 'GROUP'}:
        raise AnalysisError('Figma 페이지 또는 프레임/그룹을 선택하세요.', 422)
    page = root.get('type') == 'CANVAS'
    nodes, warnings, total = [], [], 0
    stack = [(root, 0, '', True)]
    while stack:
        item, depth, parent, visible = stack.pop()
        if len(nodes) >= (MAX_PAGE_NODES if page else 1000) or total >= (MAX_PAGE_TEXT if page else 80000):
            warnings.append('Figma node/text preview truncated.')
            break
        if depth > (40 if page else 12):
            warnings.append('Figma node/text preview truncated.')
            continue
        if not isinstance(item, dict):
            raise AnalysisError('Invalid Figma node tree.', 502)
        identity = str(item.get('id', ''))[:100]
        name, text = str(item.get('name', ''))[:200], str(item.get('characters', ''))[:2000]
        if len(str(item.get('name', ''))) > 200 or len(str(item.get('characters', ''))) > 2000:
            warnings.append('Figma node/text preview truncated.')
        total += len(name) + len(text)
        bounds = item.get('absoluteBoundingBox', {})
        bounds = {k: v for k, v in bounds.items() if k in {'x', 'y', 'width', 'height'} and type(v) in (int, float)
                  and math.isfinite(v) and abs(v) <= 10000000} if isinstance(bounds, dict) else {}
        visible = visible and item.get('visible') is not False
        normalized = {'id': identity, 'parent': parent, 'type': str(item.get('type', ''))[:50],
                      'name': name, 'text': text, 'visible': visible, 'bounds': bounds,
                      'image_fill': any(isinstance(p, dict) and p.get('type') == 'IMAGE'
                                        for p in (item.get('fills') if isinstance(item.get('fills'), list) else []))}
        normalized['interactions'] = interactions(item, warnings)
        nodes.append(normalized)
        children = item.get('children', [])
        if not isinstance(children, list):
            raise AnalysisError('Invalid Figma node children.', 502)
        child_limit = MAX_PAGE_NODES if page else 1000
        if len(children) > child_limit:
            warnings.append('Figma child nodes truncated.')
        stack.extend((child, depth + 1, identity, visible) for child in reversed(children[:child_limit]))
    version = str(data.get('version', ''))
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', version):
        raise AnalysisError('Figma response has no usable version snapshot.', 502)
    document = {'format': 'figma', 'status': 'partial' if warnings else 'ready',
            'name': str(data.get('name', 'Figma frame'))[:200], 'version': version,
            'node_id': node_id, 'nodes': nodes, 'warnings': list(set(warnings)),
            'units': [{'label': root.get('name', node_id), 'text': json.dumps(nodes, ensure_ascii=False, indent=2)}]}
    if page:
        describe_page(document, root)
    return document


def interactions(item, warnings):
    """Keep navigation evidence, not external URLs, variables or arbitrary actions."""
    result = []
    raw = item.get('interactions', [])
    if not isinstance(raw, list):
        raise AnalysisError('Invalid Figma interactions.', 502)
    if len(raw) > 50:
        warnings.append('Figma interactions truncated.')
    for interaction in raw[:50]:
        if not isinstance(interaction, dict):
            continue
        trigger = interaction.get('trigger') or {}
        actions = interaction.get('actions') or []
        if not isinstance(actions, list):
            continue
        if len(actions) > 50:
            warnings.append('Figma interactions truncated.')
        for action in actions[:50]:
            if not isinstance(action, dict):
                continue
            result.append({'trigger': str(trigger.get('type', 'UNKNOWN'))[:80] if isinstance(trigger, dict) else 'UNKNOWN',
                           'type': str(action.get('type', 'UNKNOWN'))[:80],
                           'navigation': str(action.get('navigation', ''))[:80],
                           'destination': str(action.get('destinationId') or '')[:100]})
    destination = item.get('transitionNodeID')
    if destination and not any(a['destination'] == destination for a in result):
        result.append({'trigger': 'UNSPECIFIED', 'type': 'NODE', 'navigation': 'LEGACY',
                       'destination': str(destination)[:100]})
    return result


def describe_page(document, root):
    screens, owners, links = [], {}, []
    for node in document['nodes']:
        owner = owners.get(node['parent'])
        if not owner and node['visible'] and (node['type'] in {'FRAME', 'COMPONENT', 'INSTANCE'} or node['image_fill']):
            owner = node['id']
            screens.append({'id': owner, 'name': node['name'], 'bounds': node['bounds'], 'nodes': []})
        owners[node['id']] = owner
    grouped = {screen['id']: screen for screen in screens}
    other = []
    ids = {n['id'] for n in document['nodes']}
    for node in document['nodes']:
        owner = owners[node['id']]
        (grouped[owner]['nodes'] if owner else other).append(node)
        for action in node['interactions']:
            links.append({'source': node['id'], 'screen': owner, **action,
                          'destination_in_page': action['destination'] in ids})
    starts = root.get('flowStartingPoints', [])
    starts = [{'nodeId': str(s.get('nodeId', ''))[:100], 'name': str(s.get('name', ''))[:200]}
              for s in starts if isinstance(s, dict)] if isinstance(starts, list) else []
    units = [{'label': '페이지 구성', 'text': '페이지: ' + str(root.get('name', '')) + '\n' +
              '\n'.join(s['id'] + ' | ' + s['name'] for s in screens)},
             {'label': '프로토타입 연결', 'text': json.dumps({'starts': starts, 'links': links}, ensure_ascii=False, indent=2)}]
    for screen in screens:
        screen['text_count'] = sum(bool(n['text']) for n in screen['nodes'])
        screen['image_only'] = not screen['text_count'] and any(n['image_fill'] for n in screen['nodes'])
        units.append({'label': screen['name'] + ' [' + screen['id'] + ']',
                      'text': json.dumps(screen['nodes'], ensure_ascii=False, indent=2)})
    units.append({'label': '페이지 설명 및 기타 요소', 'text': json.dumps(other, ensure_ascii=False, indent=2)})
    document.update(scope='page', page_name=str(root.get('name', ''))[:200],
                    screens=[{k: v for k, v in s.items() if k != 'nodes'} for s in screens],
                    flows=links, flow_starts=starts, units=units)
    if not links:
        document['warnings'].append('프로토타입 연결 정보가 없습니다. 화면 배치 순서를 실제 학습 순서로 단정할 수 없습니다.')
    if any(s['image_only'] for s in screens):
        document['warnings'].append('이미지로만 된 화면이 있습니다. 내부 문구는 미추출이며 개별 화면 이미지 또는 기획 설명 보완이 필요합니다.')
    if not screens:
        document['warnings'].append('인식된 화면 프레임이 없습니다. 페이지의 설명과 요소만 가져왔습니다.')
    if document['warnings']:
        document['status'] = 'partial'


def generation_lines(document):
    """Page-wide index first; bounded chunks keep later screens in the AI context."""
    for screen in document['screens']:
        yield 'screen ' + screen['id'], json.dumps(screen, ensure_ascii=False)
    for start in document['flow_starts']:
        yield 'flow start', json.dumps(start, ensure_ascii=False)
    for link in document['flows']:
        yield 'flow ' + link['source'], json.dumps(link, ensure_ascii=False)
    for node in document['nodes']:
        # Vector internals add volume, not readable content. Preserve text and controls.
        if node['text'] or node['interactions'] or node['type'] in {'SECTION', 'GROUP'}:
            yield 'node ' + node['id'], json.dumps(node, ensure_ascii=False)


def import_frame(store, sha, url):
    store.store.get(sha)
    key, node, canonical = frame_url(url)
    token = os.environ.get('FIGMA_ACCESS_TOKEN', '').strip()
    if not token:
        raise AnalysisError('FIGMA_ACCESS_TOKEN is not configured on the server.', 503)
    if not IMPORT_LOCK.acquire(blocking=False):
        raise AnalysisError('Another Figma import is running.', 409)
    try:
        document = normalize(api_json('files/' + key + '/nodes', {'ids': node}, token), node)
        image = None
        try:
            # A page overview is contextual, never proof that every screen is legible.
            scale = 1
            if document.get('scope') == 'page':
                boxes = [s['bounds'] for s in document['screens'] if all(k in s['bounds'] for k in ('x', 'y', 'width', 'height'))]
                if boxes:
                    extent = max(max(b['x'] + b['width'] for b in boxes) - min(b['x'] for b in boxes),
                                 max(b['y'] + b['height'] for b in boxes) - min(b['y'] for b in boxes))
                    scale = max(.01, min(1, 3000 / max(1, extent)))
                document['warnings'].append('페이지 이미지는 전체 배치 참고용입니다. 축소된 문구의 판독과 모든 화면의 시각 검증을 보장하지 않습니다.')
            result = api_json('images/' + key, {'ids': node, 'format': 'png', 'scale': scale,
                                               'version': document['version']}, token)
            image_url = result['images'].get(node) if isinstance(result.get('images'), dict) else None
            if not isinstance(image_url, str):
                raise AnalysisError('Figma image render unavailable.', 502)
            _, image = image_preview(fetch(image_url, limit=20 * 1024 * 1024))
        except (AnalysisError, OSError, ValueError) as exc:
            document['warnings'].append(str(exc) if isinstance(exc, AnalysisError) else 'Figma rendered image could not be decoded.')
            document['status'] = 'partial'
        document['image_available'] = image is not None
        if document['warnings']:
            document['status'] = 'partial'
        # Versioned normalized evidence, never raw tokens/comments or signed URLs.
        data = json.dumps({**document, 'image_sha256': hashlib.sha256(image).hexdigest() if image else None},
                          ensure_ascii=False, sort_keys=True).encode('utf-8')
        return store.persist(sha, data, 'figma-' + node.replace(':', '-') + '.json',
                             'design', canonical, document, image)
    finally:
        IMPORT_LOCK.release()
