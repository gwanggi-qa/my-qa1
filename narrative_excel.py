"""Read human-authored QA sheets without discarding their original intent."""
import re


FIELDS = {
    "case_no": ("TC ID", "TCID", "통합ID", "테스트ID", "No.", "case_no"),
    "title": ("제목", "테스트명", "title"),
    "priority": ("우선순위", "priority"),
    "check_type": ("Type", "유형", "분류", "Check"),
    "test_purpose": ("테스트목적", "검증목적", "목적", "test purpose", "objective"),
    "precondition": ("사전조건", "전제조건", "precondition", "preconditions"),
    "test_item": ("테스트절차", "테스트항목", "테스트내용", "procedure", "steps", "test procedure"),
    "expected": ("기대결과", "예상결과", "expected", "expected result"),
    "note": ("비고", "note", "notes"),
    "automation_possible": ("자동화가능", "automatable"),
    "enabled": ("enabled", "use", "사용", "실행"),
    "qa_result": ("결과", "검증결과", "result"),
    **{f"depth{i}": (f"{i}Depth",) for i in range(1, 8)},
}
LABELS = {key: aliases[0] for key, aliases in FIELDS.items()}


def text(value):
    return "" if value is None else str(value).strip()


def normalized(value):
    return re.sub(r"[\s_\-().,/]+", "", text(value)).casefold()


def rows_of(ws):
    rows = [list(row) for row in ws.iter_rows(values_only=True)]
    # Only actual merged cells inherit a value; unrelated blank cells remain blank.
    for area in ws.merged_cells.ranges:
        value = rows[area.min_row - 1][area.min_col - 1]
        for row in range(area.min_row - 1, area.max_row):
            for col in range(area.min_col - 1, area.max_col):
                rows[row][col] = value
    return rows


def header_mapping(row):
    mapping, ambiguous = {}, []
    for key, aliases in FIELDS.items():
        matches = [i for i, cell in enumerate(row) if normalized(cell) in {normalized(a) for a in aliases}]
        if len(matches) == 1:
            mapping[key] = matches[0]
        elif len(matches) > 1:
            ambiguous.append(key)
    return mapping, ambiguous


def inspect_workbook(workbook):
    sheets, candidates = [], []
    for ws in workbook.worksheets:
        rows = rows_of(ws)
        previews = []
        for index, row in enumerate(rows[:50], 1):
            if not any(text(cell) for cell in row):
                continue
            mapping, ambiguous = header_mapping(row)
            preview = {"header_row": index, "columns": [text(c) for c in row],
                       "mapping": mapping, "ambiguous": ambiguous,
                       "sample": [[text(c) for c in r] for r in rows[index:index + 3]]}
            previews.append(preview)
            if "test_item" in mapping and "expected" in mapping:
                candidates.append({"sheet": ws.title, **preview})
        sheets.append({"name": ws.title, "rows": previews})
    return {"fields": LABELS, "sheets": sheets, "candidates": candidates}


class MappingRequired(ValueError):
    def __init__(self, workbook):
        super().__init__("시트와 열 매핑을 확인해 주세요.")
        self.inspection = inspect_workbook(workbook)


def detect(workbook):
    candidates = inspect_workbook(workbook)["candidates"]
    # These identifying fields distinguish narrative QA from legacy action sheets.
    found = [c for c in candidates if "test_purpose" in c["mapping"] or
             any(normalized(v) == "통합id" for v in c["columns"])]
    if len(found) > 1 or any(c["ambiguous"] for c in found):
        raise MappingRequired(workbook)
    return found[0] if found else None


def required_labels(purpose, expected):
    """Expand an explicit enumerated list, never invent labels for vague counts."""
    match = re.search(r"([^\n]+?)\s*(\d+)\s*종", purpose)
    if not match or not re.search(r"모두|전체|종", expected):
        return []
    phrase = re.split(r"하위[가는]?\s*|[:：]", match[1])[-1]
    labels = [p.strip(" '‘’\"“”") for p in phrase.split("/")]
    return labels if len(labels) == int(match[2]) and all(labels) else []


def verification_gap(step):
    source = text(step.get("qa_expected") or step.get("expected"))
    if not source:
        return "기대결과가 없어 통과 기준을 정할 수 없습니다. 원본 TC에 기대결과가 필요합니다."
    # A display-only expected result is not a score-calculation test just because
    # its category/purpose mentions calculation. Otherwise resolve terse references.
    if not re.search(r"표시|노출|보여|보인다|존재", source):
        source += " " + text(step.get("test_purpose"))
    if re.search(r"정확|일치|동일|반영|계산|산출|환산|보간|반올림|중복\s*제외|분모|분자|가중|추정|정합", source) and re.search(
            r"점수|정답률|데이터|환산|보간|반올림|중복\s*제외|분모|분자|계산|산출|가중치|합계|합산|평균", source):
        return "원본 학습 데이터와 계산 기준/환산표가 필요합니다. 현재 화면 문구만으로 데이터 정합성을 검증할 수 없습니다."
    return ""


def needs_visual_verification(step):
    if step.get("source") == "recorder":
        return False
    expected = text(step.get("qa_expected") or step.get("expected"))
    if not expected:
        return False
    context = expected + " " + text(step.get("test_purpose"))
    return bool(re.search(r"카드|막대|그래프|레이아웃|배치|색상|테두리|토글|아이콘|이미지|오른쪽|왼쪽|상단|하단|한\s*페이지|선택.*상태|Figma|애니메이션", context, re.I))


def parse(workbook, selection):
    if not isinstance(selection, dict) or selection.get("sheet") not in workbook.sheetnames:
        raise ValueError("올바른 시트를 선택해 주세요.")
    ws = workbook[selection["sheet"]]
    header = selection.get("header_row")
    mapping = selection.get("mapping")
    if type(header) is not int or not 1 <= header <= min(ws.max_row, 50):
        raise ValueError("헤더행은 첫 50행 안에서 선택해 주세요.")
    if not isinstance(mapping, dict) or not {"test_item", "expected"}.intersection(mapping):
        raise ValueError("테스트절차 또는 기대결과 열을 지정해 주세요.")
    if any(k not in FIELDS or type(v) is not int or not 0 <= v < ws.max_column for k, v in mapping.items()):
        raise ValueError("열 매핑이 올바르지 않습니다.")
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("동일한 열을 여러 필드에 지정할 수 없습니다.")
    rows = rows_of(ws)
    steps = []
    originals = ws.iter_rows(min_row=header + 1, values_only=True)
    for number, (row, original) in enumerate(zip(rows[header:], originals), header + 1):
        if not any(text(cell) for cell in original):
            continue
        record = {key: text(row[col]) for key, col in mapping.items()}
        if not any(record.get(k) for k in ("case_no", "title", "test_purpose", "test_item", "expected")):
            continue
        if normalized(record.get("enabled")) in {"n", "no", "false", "0", "skip", "disabled", "off"}:
            continue
        # Repeated table headers are not cases.
        if header_mapping(row)[0] == mapping:
            continue
        step = {**record, "source": "narrative_qa", "sheet": ws.title, "row": number,
                "step": len(steps) + 1, "action": "blocked", "target": "", "value": "",
                "qa_expected": record.get("expected", ""), "ai_pending": True,
                "block_reason": "AI 분석 대기: 사람용 TC의 사전조건과 절차를 해석해야 합니다.",
                "depths": [record.get(f"depth{i}", "") for i in range(1, 8)],
                "raw_cells": [{"column": i + 1, "header": text(rows[header - 1][i]), "value": text(v)}
                              for i, v in enumerate(row) if text(v)]}
        step["description"] = f"{record.get('case_no') or 'TC'} [{ws.title} row {number}] {record.get('title') or record.get('test_purpose') or record.get('test_item', '')}"
        step["required_labels"] = required_labels(record.get("test_purpose", ""), record.get("expected", ""))
        step["verification_gap"] = verification_gap(step)
        step["visual_verification"] = needs_visual_verification(step)
        if step["verification_gap"]:
            step["block_reason"] = step["verification_gap"]
        elif step["visual_verification"]:
            step["block_reason"] = "화면 검증 대기"
        # Only literal, standalone text checks are safe without AI interpretation.
        literal = re.fullmatch(r'["“]([^"”]+)["”]\s*(?:표시|노출|존재)(?:된다|됨| 확인)?[.!]?', record.get("expected", ""))
        if literal and not record.get("precondition") and not record.get("test_purpose") and not step["verification_gap"] and not record.get("test_item"):
            step.update(action="assert_text", target=literal[1], assert_candidates=[literal[1]],
                        ai_pending=False, block_reason="")
        steps.append(step)
    if not steps:
        raise ValueError("선택한 시트와 매핑에서 실행 대상 TC를 찾지 못했습니다.")
    return steps
