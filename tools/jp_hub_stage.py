"""Prepare the two content checklists in an isolated Hub data tree."""
import csv
import hashlib
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/jp-hub-pilot"
HUB = Path(os.environ.get("QA_HUB_SOURCE", "D:/QA/qa-automation"))
os.environ["HUB_DATA"] = str(OUT / "data")
os.environ["HUB_AUTOMATION_ROOT"] = str(OUT / "automation")
sys.path.insert(0, str(HUB))
import hub
from tc import tc_store


def write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


PILOTS = {
    "JP-0039": "어순 배열 문제의 선택 카드 3장과 쉼표를 확인한다. 정답 문장만으로 대체하지 않는다. 지정 문제가 나오지 않으면 BLOCK.",
    "JP-0086": "1단계와 2단계의 네 번째 단어를 각각 확인한다. 안정된 표시 상태에서 時 위의 とき와 뜻 때를 확인하며, 마이크 입력 중의 한 프레임만으로 미노출을 판정하지 않는다.",
    "JPP-0200": "P판 Level 4 > Week 3 > Stage 5 회화에서 다섯 번째 대사의 표기·후리가나 위치·뜻을 확인한다. 일반판 결과를 재사용하지 않는다.",
}


def prepare(edition, code, prefix, count, history):
    src = Path.home() / "Desktop" / f"QA팀_확인목록_{edition}_20261028.csv"
    with src.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == count and sorted(int(r["No"]) for r in rows) == list(range(1,count+1))
    project = Path(hub.pdir("mind", code))
    if not project.exists():
        hub.create_project("mind", code, edition+" DB 검수", sources={"sources":[]})
    groups = {}
    mapping = []
    for row in rows:
        tid = f"{prefix}-{int(row['No']):04d}"
        route = [row[f"메뉴 {i}"] for i in range(1,6) if row[f"메뉴 {i}"]]
        stage, where = row["화면·단계"], row["어디서 (몇 번째)"]
        expected = row["이렇게 보여야 함 (최종값)"]
        assert expected.strip()
        group = row["구분"]
        item = {"ID":tid,"제목":" · ".join(filter(None,(stage,where,row["무엇을 볼지"]))) or tid,
                "사전조건":" > ".join(route+[stage]),"테스트절차":" / ".join(filter(None,(where,row["무엇을 볼지"],row["찾기 힌트"]))),
                "기대결과":expected,"1Depth":group,"2Depth":row["메뉴 2"],"3Depth":row["메뉴 3"],
                "4Depth":row["메뉴 4"],"비고":"원본 No: "+row["No"]+" / 콘텐츠 ID: "+row["ID"],
                "근거":src.name+" / "+row["바뀐 칸"]}
        groups.setdefault(group,[]).append(item)
        mapping.append({"tc_id":tid,"source":row})
    sets = []
    for name, items in groups.items():
        data=tc_store.build_xlsx(items)
        preview=tc_store.save("mind",code,name,data,dry_run=True)
        assert preview["rows"]==len(items) and not preview["rejected"],preview
        meta=tc_store.save("mind",code,name,data,filename=src.name,actor="local-pilot")
        fp=project/"tc"/meta["slug"]/f"v{meta['v']}.xlsx"
        book=load_workbook(fp,read_only=True)
        records=list(book.active.values); book.close()
        cols={tc_store.key_of(s):i for i,s in enumerate(records[0])}
        assert [(r[cols["ID"]],r[cols["기대결과"]],r[cols["비고"]]) for r in records[1:]]==[(r["ID"],r["기대결과"],r["비고"]) for r in items]
        result_col = records[0].index("Result")
        if any(r[result_col] not in (None, "") for r in records[1:]):
            raise ValueError("새 등록 목록에 실행 결과가 포함되어 있습니다.")
        sets.append((fp.relative_to(OUT).as_posix(), name, len(items)))
    write_json(project/"source-map.json",{"sha256":hashlib.sha256(src.read_bytes()).hexdigest(),"rows":mapping})
    refs=[]
    for pattern,kind in history:
        for f in sorted((ROOT/"outputs").glob(pattern)):
            if f.is_file():
                refs.append({"kind":kind,"path":str(f),"sha256":hashlib.sha256(f.read_bytes()).hexdigest()})
    write_json(project/"history-index.json",{"origin":"historical; not a Hub execution","files":refs})
    pilots = [
        {"tc_id": m["tc_id"], "content_id": m["source"]["ID"],
         "expected": m["source"]["이렇게 보여야 함 (최종값)"], "inspection": PILOTS[m["tc_id"]],
         "db_result": None, "device_result": None}
        for m in mapping if m["tc_id"] in PILOTS
    ]
    if {p["tc_id"] for p in pilots} != {k for k in PILOTS if k.startswith(prefix+"-")}:
        raise ValueError("대표 항목이 누락되었습니다.")
    write_json(project/"pending-pilot.json", {
        "state": "awaiting_target", "edition": edition, "device_serial": None,
        "package": {"JP":"com.weaversmind.tjp","JPP":"com.weaversmind.tjp2"}[prefix],
        "target_version_code": None, "target_db_sha256": None, "cases": pilots})
    print(code,{k:len(v) for k,v in groups.items()},"history files:",len(refs))
    return [f"- [{edition} {name} ({size:,}건)]({quote(path)})" for path,name,size in sets]


if __name__=="__main__":
    (OUT/"data/mind").mkdir(parents=True,exist_ok=True)
    links = prepare("일본어","jp-standard","JP",1076,[("japanese-db-all1076-20261104/*","DB 비교"),("jp23-recheck/*.xlsx","빌드23 실기기"),("jp23-recheck/*.html","빌드23 실기기"),("jp23-recheck/*.png","빌드23 불일치 증거")])
    links += prepare("일본어P","jp-premium","JPP",1062,[("japanese-build9-db-20261109/*.json","빌드9 DB 비교"),("japanese-build9-no200-device/**/*.json","빌드9 실기기"),("japanese-build9-no200-device/**/*.png","빌드9 실기기 증거")])
    (OUT/"README.md").write_text("\n".join([
        "# 일본어 DB 검수 등록본", "",
        "9999 운영 반영 전의 별도 작업본입니다. 목록 등록은 기기 검수 완료를 뜻하지 않습니다.", "",
        *links, "", "## 새 기기에서 확인할 항목", "",
        *[f"- {k}: {v}" for k,v in PILOTS.items()], "",
        "새 회차를 시작할 때 판본, 기기, 설치 빌드, 비교 DB 해시를 지정하세요. "
        "DB 조회와 기기 관찰은 별도 판정이며, 증거가 없는 항목을 PASS로 처리하지 않습니다.",
        "판본별 source-map.json은 원본 대조표, history-index.json은 과거 파일 목록, "
        "pending-pilot.json은 새 회차의 미시행 항목입니다.", ""]), encoding="utf-8")
