"""Structural and target-pixel freshness; no fuzzy image thresholds."""
import hashlib
import io
import json
import re
import xml.etree.ElementTree as ET
from PIL import Image

class MappingError(ValueError):
    pass

def nodes(xml):
    try:
        root = ET.fromstring(xml)
    except (ET.ParseError, TypeError) as exc:
        raise MappingError('UI hierarchy is unavailable or invalid') from exc
    found = list(root.iter('node'))
    if not found or not any(n.get('package') or n.get('text') or n.get('content-desc') for n in found):
        raise MappingError('Empty UI evidence')
    return root, found

def select(xml, selector, optional=False):
    if not isinstance(selector, dict) or not selector or set(selector) - {'text', 'content-desc', 'resource-id', 'package', 'class'}:
        raise MappingError('Use explicit text, accessibility label or resource selector')
    if not any(selector.get(k) for k in ('text', 'content-desc', 'resource-id')):
        raise MappingError('A unique semantic selector is required')
    if not all(isinstance(v, str) and v for v in selector.values()):
        raise MappingError('Empty selector value')
    _, all_nodes = nodes(xml)
    hits = [n for n in all_nodes if all(n.get(k) == v for k, v in selector.items())]
    if len(hits) > 1 or (not hits and not optional):
        raise MappingError('Target missing or ambiguous: ' + json.dumps(selector, ensure_ascii=False))
    return hits[0] if hits else None

def bounds(node):
    match = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', node.get('bounds', ''))
    if not match:
        raise MappingError('Target bounds missing')
    box = tuple(map(int, match.groups()))
    if box[0] >= box[2] or box[1] >= box[3]:
        raise MappingError('Invalid target bounds')
    return box

def stamp(xml, png, selector):
    root, _ = nodes(xml)
    node = select(xml, selector)
    box = bounds(node)
    if node.get('enabled') == 'false' or node.get('clickable') != 'true':
        raise MappingError('Target is not an enabled clickable element')
    parents = {child: parent for parent in root.iter() for child in parent}
    near = parents.get(node, node)
    attrs = ('class', 'package', 'resource-id', 'bounds', 'clickable', 'enabled', 'focusable', 'scrollable', 'window-id', 'rotation', 'checked', 'selected', 'focused', 'visible-to-user')
    def structure(n):
        return [n.tag, [n.get(k, '') for k in attrs], [structure(c) for c in n]]
    neighborhood = [(n.get('text',''), n.get('content-desc','')) for n in near.iter()]
    try:
        with Image.open(io.BytesIO(png)) as im:
            width, height = im.size
            if box[2] > width or box[3] > height:
                raise MappingError('Target is outside screenshot bounds')
            crop = im.convert('RGB').crop((max(0,box[0]-4), max(0,box[1]-4), min(width,box[2]+4), min(height,box[3]+4)))
            pixels = hashlib.sha256(crop.tobytes()).hexdigest()
    except (OSError, ValueError) as exc:
        raise MappingError('Screenshot unavailable') from exc
    return {'structure': structure(root), 'neighborhood': neighborhood, 'target': dict(node.attrib),
            'pixels': pixels, 'size': [width,height], 'point': [(box[0]+box[2])//2,(box[1]+box[3])//2]}

def unchanged(before_xml, before_png, after_xml, after_png, selector):
    before = stamp(before_xml, before_png, selector)
    if before != stamp(after_xml, after_png, selector):
        raise MappingError('Target, surrounding structure or overlay changed; observe and review again')
    return before['point']

