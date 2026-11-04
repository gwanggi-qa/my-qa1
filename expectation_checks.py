"""Compile exact source clauses into bounded, read-only UI assertions."""
import re
import ui_guard

REVISION = 1


def compile_text(value):
    checks = []
    for source in str(value or '').splitlines():
        if not source.strip():
            continue
        m = re.fullmatch(r'["“\']([^"”\']+)["”\']\s*(?:탭|버튼|항목)?\s*(?:이|가|은|는)?\s*(.+?)\.?', source.strip())
        if not m:
            return None
        label, predicate = m.groups()
        check = {'source': source, 'selector': {'text': label}}
        if predicate in ('표시된다', '존재한다', '표시되지 않는다'):
            check['present'] = predicate != '표시되지 않는다'
        elif predicate in ('선택된다', '선택되어 있다', '체크된다', '체크되어 있다', '활성화된다', '비활성화된다'):
            check.update(attribute=('selected' if predicate.startswith('선택') else
                                    'checked' if predicate.startswith('체크') else 'enabled'),
                         value=predicate != '비활성화된다')
        else:
            count = re.fullmatch(r'(\d+)개(?:가)? 표시된다', predicate)
            if not count or int(count[1]) > 1000:
                return None
            check['count'] = int(count[1])
        checks.append(check)
    return checks


def evaluate(xml, checks):
    # Invalid/empty XML is missing evidence, never proof of absence.
    _, nodes = ui_guard.nodes(xml)
    findings = []
    for c in checks:
        matches = [n for n in nodes if all(n.get(k) == v for k, v in c['selector'].items())]
        if 'count' in c:
            # UI hierarchy counts are restricted to explicitly visible nodes.
            if any(n.get('displayed', n.get('visible-to-user')) not in ('true', 'false') for n in matches):
                raise ValueError('개수 검증에 필요한 요소 가시성 정보가 없습니다: ' + c['source'])
            actual = sum(n.get('displayed', n.get('visible-to-user')) == 'true' for n in matches)
            expected = c['count']
        else:
            if len(matches) > 1:
                raise ValueError('검사 대상이 중복됩니다: ' + c['source'])
            node = matches[0] if matches else None
            visible = node is not None and node.get('displayed', node.get('visible-to-user', 'true')) != 'false'
            if 'present' in c:
                actual, expected = visible, c['present']
            else:
                if node is None or not visible:
                    actual, expected = '대상 없음', c['value']
                else:
                    raw = node.get(c['attribute'])
                    if raw not in ('true', 'false'):
                        raise ValueError('검사 속성 정보가 없습니다: ' + c['attribute'] + ' / ' + c['source'])
                    actual, expected = raw == 'true', c['value']
        findings.append({'source': c['source'], 'selector': c['selector'],
                         'actual': actual, 'expected': expected, 'passed': actual == expected})
    return findings


def describe(step, clauses):
    return [{'expectation': i, 'source': text,
             'method': 'ui-assertion' if compile_text(text) else 'ai-visual',
             'conditions': compile_text(text) or []}
            for i, text in enumerate(clauses(step), 1)]
