"""Build local comparison data from the registered checklist and reviewed screen regions."""
import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from premium_check import one, text_of, signature


# Prior observations are visual references only, never new device verdicts.
REGIONS = [
    ("093-no1-balloon", "grammar_check_content", 0, None),
    ("094-no2-popup", "imgContent", 0, "093-no1-balloon"),
    ("001-start", "fvSingleQuizText", 0, None),
    ("044-no4-popup", "imgContent", 0, "043-no4-popup"),
    ("031-conversation-lines", "tvTalkText", 0, None),
    ("032-conversation-line2", "tvTalkText", 1, None),
    ("038-no7-popup", "imgContent", 0, "037-no7-balloon"),
    ("025-core-tap", "grammar_check_content", 0, None),
    ("026-popup7004", "imgContent", 0, "025-core-tap"),
    ("101-no10-popup", "imgContent", 0, "100-no10-balloon"),
]


def build(source, captures, glyphs=None):
    from premium_check import nodes
    rows = {r["tc_id"]: r["source"] for r in json.loads(source.read_text(encoding="utf-8"))["rows"]}
    out = {}
    for i, (stem, region, idx, balloon) in enumerate(REGIONS, 1):
        tid = f"JPP-{i:04d}"
        row = rows[tid]
        if row["메뉴 2"] != "Level 2" or row["메뉴 3"] != "Week 1 인사":
            raise ValueError(f"{tid}: 재검토가 필요한 이동 경로")
        xml = ET.parse(captures / (stem + ".xml")).getroot()
        sig = signature((captures / (stem + ".png")).read_bytes(), nodes(xml, region)[idx])
        if glyphs is not None and region in ("fvSingleQuizText", "tvTalkText"):
            from PIL import Image
            from premium_check import bounds
            glyphs.mkdir(exist_ok=True, parents=True)
            with Image.open(captures / (stem + ".png")) as im:
                im.crop(bounds(nodes(xml, region)[idx])).save(glyphs / (stem + ".png"))
        case = dict(expected=row["이렇게 보여야 함 (최종값)"], content_id=row["ID"],
                    stage=row["메뉴 4"], title=row["찾기 힌트"].split(": ", 1)[1],
                    lesson=row["메뉴 5"], screen=row["화면·단계"], region=region,
                    image=sig, reference=stem, index=idx)
        if balloon:
            root = ET.parse(captures / (balloon + ".xml")).getroot()
            case["balloon"] = [text_of(one(root, k)) for k in
                               ("tvCheckTermJp", "tvCheckTermKo", "tvCheckDescription")]
        out[tid] = case
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    for arg in ("source", "captures", "output"):
        ap.add_argument(arg, type=Path)
    args = ap.parse_args()
    args.output.write_text(json.dumps(build(args.source, args.captures, args.output.parent / "glyphs"), ensure_ascii=False,
                                      indent=2), encoding="utf-8")
