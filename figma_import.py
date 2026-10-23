"""Read one explicit Figma frame. No arbitrary URL fetching or secret persistence."""
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


def frame_url(value):
    if not isinstance(value, str) or len(value) > 2048 or re.search(r'[\s\\\x00-\x1f]', value):
        raise AnalysisError('Enter a Figma frame URL.')
    try:
        url = urlsplit(value)
        match = re.fullmatch(r'/(?:design|file)/([A-Za-z0-9]+)/[^/]*', url.path.rstrip('/'))
        nodes = parse_qs(url.query).get('node-id', [])
        if (url.scheme != 'https' or url.hostname not in {'figma.com', 'www.figma.com'} or
                url.username or url.password or url.port not in (None, 443) or not match or
                len(nodes) != 1 or not re.fullmatch(r'\d+[-:]\d+', nodes[0])):
            raise ValueError()
    except ValueError as exc:
        raise AnalysisError('Use a Figma design/file frame link containing node-id.') from exc
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
    if not isinstance(root, dict) or root.get('id') != node_id or root.get('type') not in {'FRAME', 'COMPONENT', 'INSTANCE', 'SECTION', 'GROUP'}:
        raise AnalysisError('Select a frame/component rather than an entire page.', 422)
    nodes, warnings, total = [], [], 0
    stack = [(root, 0, '')]
    while stack:
        item, depth, parent = stack.pop()
        if len(nodes) >= 1000 or depth > 12 or total >= 80000:
            warnings.append('Figma node/text preview truncated.')
            break
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
        nodes.append({'id': identity, 'parent': parent, 'type': str(item.get('type', ''))[:50],
                      'name': name, 'text': text, 'visible': item.get('visible') is not False, 'bounds': bounds})
        children = item.get('children', [])
        if not isinstance(children, list):
            raise AnalysisError('Invalid Figma node children.', 502)
        if len(children) > 1000:
            warnings.append('Figma child nodes truncated.')
        stack.extend((child, depth + 1, identity) for child in reversed(children[:1000]))
    version = str(data.get('version', ''))
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', version):
        raise AnalysisError('Figma response has no usable version snapshot.', 502)
    return {'format': 'figma', 'status': 'partial' if warnings else 'ready',
            'name': str(data.get('name', 'Figma frame'))[:200], 'version': version,
            'node_id': node_id, 'nodes': nodes, 'warnings': list(set(warnings)),
            'units': [{'label': root.get('name', node_id), 'text': json.dumps(nodes, ensure_ascii=False, indent=2)}]}


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
            result = api_json('images/' + key, {'ids': node, 'format': 'png', 'scale': 1,
                                               'version': document['version']}, token)
            image_url = result['images'].get(node) if isinstance(result.get('images'), dict) else None
            if not isinstance(image_url, str):
                raise AnalysisError('Figma image render unavailable.', 502)
            _, image = image_preview(fetch(image_url, limit=20 * 1024 * 1024))
        except (AnalysisError, OSError, ValueError) as exc:
            document['warnings'].append(str(exc) if isinstance(exc, AnalysisError) else 'Figma rendered image could not be decoded.')
            document['status'] = 'partial'
        document['image_available'] = image is not None
        # Versioned normalized evidence, never raw tokens/comments or signed URLs.
        data = json.dumps({**document, 'image_sha256': hashlib.sha256(image).hexdigest() if image else None},
                          ensure_ascii=False, sort_keys=True).encode('utf-8')
        return store.persist(sha, data, 'figma-' + node.replace(':', '-') + '.json',
                             'design', canonical, document, image)
    finally:
        IMPORT_LOCK.release()
