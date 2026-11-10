import hashlib
import io
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image

PACKAGE = "com.weaversmind.tjp2"
FIELDS = ("tvCheckTermJp", "tvCheckTermKo", "tvCheckDescription")


class Unobservable(Exception):
    """No defensible device verdict for the requested screen."""


def nodes(root, rid):
    return [n for n in root.iter("node") if n.get("resource-id") == PACKAGE + ":id/" + rid]


def one(root, rid):
    matches = nodes(root, rid)
    if len(matches) != 1:
        raise Unobservable(f"{rid}: 화면 요소 {len(matches)}개, 위치를 특정하지 못했습니다.")
    return matches[0]


def text_of(node):
    s = node.get("text", "").strip()
    if not s:
        raise Unobservable("현재 단계의 표시 문구를 읽을 수 없습니다.")
    return s


def bounds(n):
    match = re.fullmatch(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", n.get("bounds", ""))
    if not match:
        raise Unobservable("화면 영역을 특정하지 못했습니다.")
    x,y,r,b = map(int, match.groups())
    if x < 0 or y < 0 or r <= x or b <= y:
        raise Unobservable("화면 요소의 위치가 유효하지 않습니다.")
    return x,y,r,b


def signature(png, node):
    x,y,r,b = bounds(node)
    with Image.open(io.BytesIO(png)) as im:
        if im.width < r or im.height < b:
            raise Unobservable("요소의 일부가 화면 밖에 있습니다.")
        crop = im.convert("RGB").crop((x,y,r,b))
        return [crop.width, crop.height, hashlib.sha256(crop.tobytes()).hexdigest()]


class Device:
    def __init__(self, serial):
        self.serial = serial
        self.last_capture = None

    def adb(self, *args):
        try:
            return subprocess.run(["adb", "-s", self.serial, *map(str,args)], capture_output=True,
                                  timeout=18, check=True).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            raise Unobservable("선택한 기기의 ADB 명령을 완료하지 못했습니다.") from exc

    def version(self):
        data = self.adb("shell", "dumpsys", "package", PACKAGE).decode(errors="replace")
        ver = re.search(r"\bversionCode=(\d+)", data)
        if not ver:
            raise Unobservable("일본어P 패키지의 설치 빌드를 확인할 수 없습니다.")
        return ver[1]

    def read(self):
        for attempt in range(5):
            data = self.adb("exec-out", "uiautomator", "dump", "/dev/tty")
            start, end = data.find(b"<hierarchy"), data.find(b"</hierarchy>")
            if start >= 0 and end >= start:
                break
            time.sleep(.4 + .3*attempt)
        if start < 0 or end < start:
            raise Unobservable("현재 UI XML을 읽을 수 없습니다.")
        try:
            root = ET.fromstring(data[start:end + len(b"</hierarchy>")])
        except ET.ParseError as exc:
            raise Unobservable("현재 UI XML이 불완전합니다.") from exc
        if not any(n.get("package") == PACKAGE for n in root.iter("node")):
            raise Unobservable("일본어P를 전면에 열고 학습 진도 화면에서 시작하세요.")
        return root

    def tap(self, n):
        x,y,r,b = bounds(n)
        self.adb("shell","input","tap",(x+r)//2,(y+b)//2)
        time.sleep(.35)

    def stage(self, case):
        slot = {"문장1":"2", "문장2":"3", "회화":"4"}[case["lesson"]]
        for _ in range(15):
            root = self.read()
            if nodes(root, "listStagePath"):
                if text_of(one(root, "tvLevelTitle")) != "Level 2":
                    raise Unobservable("시작 조건: 학습 진도 Level 2를 선택하세요.")
                if not any(n.get("text") == "Week 1" for n in nodes(root, "tvEpTitleNum")):
                    raise Unobservable("시작 조건: Week 1 인사를 선택하세요.")
                path = one(root, "listStagePath")
                cards = [c for c in path if any(n.get("text")==case["stage"] for n in nodes(c,"tvStageNum"))
                         and any(n.get("text")==case["title"] for n in nodes(c,"tvJpTitle"))]
                if len(cards) == 1:
                    start = nodes(cards[0], "flStart"+slot)
                    if start:
                        self.tap(one(cards[0], "flStart"+slot))
                        return
                    circle = nodes(cards[0], "flNode"+slot+"Circle")
                    if circle:
                        self.tap(one(cards[0], "flNode"+slot+"Circle"))
                    else:
                        x,y,r,b = bounds(path)
                        self.adb("shell","input","swipe",(x+r)//2,y+(b-y)*3//4,
                                 (x+r)//2,y+(b-y)*2//5,480)
                        time.sleep(.4)
                elif not cards:
                    nums = [int(m[1]) for n in nodes(path,"tvStageNum")
                            if (m := re.fullmatch(r"Stage (\d+)", n.get("text","")))]
                    goal = int(case["stage"].split()[-1])
                    if not nums or min(nums) <= goal <= max(nums):
                        raise Unobservable("Stage 번호와 카드 제목의 연결을 확인할 수 없습니다.")
                    x,y,r,b = bounds(path)
                    a,z = (y+int((b-y)*.8), y+int((b-y)*.22))
                    if goal < min(nums):
                        a,z = z,a
                    self.adb("shell","input","swipe",(x+r)//2,a,(x+r)//2,z,450)
                    time.sleep(.4)
                else:
                    raise Unobservable("Stage 카드가 중복됩니다.")
            elif any(n.get("text")=="학습을 중단하고 나가시겠습니까?" for n in nodes(root,"popup_content")):
                btn = one(root, "btn_sel1")
                if btn.get("text") != "확인":
                    raise Unobservable("학습 종료 동작을 식별할 수 없습니다.")
                self.tap(btn)
            elif nodes(root,"imgContent") and nodes(root,"lyBottomSheet"):
                self.tap(one(root,"btn_sel1"))
            elif any(nodes(root,k) for k in ("layoutStudySentence","layoutSingleQuiz","svConvTalk",
                                              "layoutSentenceLecture","flTodayLessonCoverOverlay","cvDescCard")):
                self.adb("shell","input","keyevent","4")
                time.sleep(.4)
            else:
                raise Unobservable("일본어P 학습 진도 Level 2 > Week 1에서 시작하세요.")
        raise Unobservable("Stage 선택 또는 학습 시작을 확인하지 못했습니다.")


def check_text(actual, expected):
    norm = lambda s: " ".join(s.split())
    if norm(actual) != norm(expected):
        raise AssertionError(f"기대: {expected} / 실제: {actual}")


def inspect_case(dev, c):
    dev.stage(c)
    screen = c["screen"]
    prior = None
    for _ in range(32):
        root = dev.read()
        if nodes(root,"flTodayLessonCoverOverlay"):
            dev.tap(one(root,"btnTodayLessonNext"))
        elif screen == "대화" and nodes(root,"llConvTalkList"):
            bubbles = nodes(one(root,"llConvTalkList"),"cvTalkBubble")
            # The visible list can scroll; anchor the sequence to the first two speakers.
            if len(bubbles) > c["index"] and all(nodes(b,"tvTalkName") and
                    text_of(one(b,"tvTalkName")) == speaker for b,speaker in
                    zip(bubbles, ("ユジン","小川さえ子")[:c["index"]+1])):
                root = bubbles[c["index"]]
                break
            time.sleep(.8)
        elif screen == "어법 더보기 팝업" and nodes(root,"lyBottomSheet") and prior:
            break
        elif nodes(root,"grammar_balloon_button"):
            if screen == "핵심 표현":
                break
            if screen == "어법 더보기 팝업":
                prior = [text_of(one(root,k)) for k in FIELDS]
                dev.tap(one(root,"grammar_balloon_button"))
            elif screen == "활용 연습":
                dev.tap(one(root,"ivCtrlNext"))
            else:
                raise Unobservable("기대하지 않은 학습 단계입니다.")
        elif screen == "활용 연습" and nodes(root,"tvSingleQuizPage"):
            page = text_of(one(root,"tvSingleQuizPage"))
            if page.startswith("2 / 3"):
                break
            if not page.startswith("1 / 3"):
                raise Unobservable("첫 활용 문장을 특정하지 못했습니다.")
            dev.tap(one(root,"ivCtrlNext"))
        elif nodes(root,"cvDescCard"):
            dev.tap(one(root,"cvDescCard"))
        elif nodes(root,"rlLecturePopup") and any(n.get("text")=="정말 강의를 건너뛰시겠어요?" for n in root.iter("node")):
            skip = one(root,"btn_sel1")
            if skip.get("text") != "건너뛰기":
                raise Unobservable("강의 건너뛰기 버튼을 확인하지 못했습니다.")
            dev.tap(skip)
        elif nodes(root,"btnCtrlNext"):
            dev.tap(one(root,"btnCtrlNext"))
        elif nodes(root,"layoutSentenceLecture"):
            skip = [n for n in root.iter("node") if n.get("package")==PACKAGE and
                    "건너뛰기" in (n.get("text"),n.get("content-desc"))]
            if len(skip)==1:
                dev.tap(skip[0])
            else:
                video = one(root,"mvLecture")
                x,y,r,b = bounds(video)
                dev.tap(video)
                # The video overlay disappears before the next accessibility dump.
                dev.adb("shell","input","tap",x+(r-x)*89//100,y+(b-y)*94//100)
        else:
            time.sleep(.5)
    else:
        raise Unobservable(f"{c['stage']} {c['lesson']}: {screen}에 도달하지 못했습니다.")
    dev.last_capture = dev.adb("exec-out","screencap","-p")
    expected = c["expected"]
    if screen == "핵심 표현":
        title, desc = expected.translate(str.maketrans("", "", "[]")).split("\n",1)
        jp,ko = title.split("  ",1)
        for k,v in zip(FIELDS,(jp,ko,desc)):
            check_text(text_of(one(root,k)),v)
    elif screen == "어법 더보기 팝업":
        normalize_title = lambda s: re.sub(r"\s*:\s*", ":", s)
        check_text(normalize_title(text_of(one(root,"txtTitle"))),
                   normalize_title(expected.replace("<c>","").replace("</c>","")))
        if prior != c["balloon"]:
            raise Unobservable("말풍선이 이미지 대조 기준과 달라 본문 연동 재검토 필요")
    else:
        ko = expected.split("\n뜻: ",1)[1]
        check_text(text_of(one(root,"tvSingleQuizMean" if screen=="활용 연습" else "tvTalkMean")),ko)
        node = one(root,c["region"])
        jp = expected.splitlines()[0].removeprefix("일본어: ").strip()
        labels = {"001-start":"先(せん)生(せい)、こんにちは。", "031-conversation-lines":"おはようございます。"}
        if c["reference"] in labels and not node.get("text","").strip():
            check_text(labels[c["reference"]], jp)
            if signature(dev.last_capture,node) == c["image"]:
                return
            refdir = Path(__file__).with_name("glyphs")
            for attempt in range(3):
                if attempt:
                    time.sleep(.45)
                    fresh = dev.read()
                    if screen == "대화":
                        bubbles = nodes(one(fresh,"llConvTalkList"),"cvTalkBubble")
                        if not bubbles:
                            raise Unobservable("첫 화자의 대사 영역을 찾지 못했습니다.")
                        fresh = bubbles[0]
                        check_text(text_of(one(fresh,"tvTalkName")), "ユジン")
                    else:
                        check_text(text_of(one(fresh,"tvSingleQuizPage")),text_of(one(root,"tvSingleQuizPage")))
                    check_text(text_of(one(fresh,"tvTalkMean" if screen=="대화" else "tvSingleQuizMean")),ko)
                    node = one(fresh,c["region"])
                    dev.last_capture = dev.adb("exec-out","screencap","-p")
                with Image.open(io.BytesIO(dev.last_capture)) as im:
                    crop = im.crop(bounds(node))
                for suffix in ("-reviewed.png",".png"):
                    filename = refdir / (c["reference"]+suffix)
                    if filename.is_file():
                        with Image.open(filename) as ref:
                            if matches_lines(ref,crop,screen=="대화",2 if screen=="활용 연습" else 1):
                                return
            import os
            evidence = os.environ.get("AUTOMATION_EVIDENCE_DIR")
            if evidence:
                path = Path(evidence)
                path.mkdir(parents=True, exist_ok=True)
                crop.save(path/(c["reference"]+"-unread.png"))
            raise Unobservable("해당 학습 단계에서 일본어 본문·읽기의 글자 모양을 모두 확인하지 못했습니다.")
        elif node.get("text","").strip():
            check_text(text_of(node),jp)
        elif signature(dev.last_capture,node) != c["image"]:
            # Compare only the reviewed Japanese glyphs (including readings), not surrounding whitespace.
            ref = Path(__file__).with_name("glyphs") / (c["reference"]+".png")
            if not ref.exists():
                raise Unobservable("일본어 표시 글자의 판독 기준이 없습니다.")
            with Image.open(ref) as im:
                a = glyph_mask(im)
            with Image.open(io.BytesIO(dev.last_capture)) as im:
                b = glyph_mask(im.crop(bounds(node)))
            # Optional second rendering from independently reviewed device evidence.
            reviewed = ref.with_name(ref.stem+"-reviewed.png")
            if reviewed.exists():
                from PIL import ImageChops, ImageFilter
                with Image.open(reviewed) as im:
                    alt = glyph_mask(im)
                if alt.size == b.size:
                    diff = ImageChops.difference(alt,b)
                    # Compression noise is isolated; do not tolerate a missing glyph/dot.
                    if diff.getextrema()[1] == 0 or (
                        diff.histogram()[255] <= .012 * alt.histogram()[255]
                        and not diff.filter(ImageFilter.MedianFilter(3)).getbbox()
                    ):
                        return
            if abs(a.width/a.height-b.width/b.height) > .04*(a.width/a.height):
                raise Unobservable("일본어 글자 영역의 비율이 달라 직접 판독이 필요합니다.")
            from PIL import ImageChops, ImageFilter
            b = b.resize(a.size, Image.Resampling.NEAREST)
            overlap = ImageChops.darker(a,b).histogram()[255]
            union = ImageChops.lighter(a,b).histogram()[255]
            # Require local stroke agreement too; a missing punctuation mark must not
            # disappear inside a high whole-sentence similarity score.
            local_ok = True
            for left in range(0,a.width,max(4,a.height//3)):
                box = (left,0,min(left+max(4,a.height//3),a.width),a.height)
                for src,dst in ((a,b),(b,a)):
                    count = src.crop(box).histogram()[255]
                    missing = ImageChops.subtract(src,dst.filter(ImageFilter.MaxFilter(3))).crop(box).histogram()[255]
                    if missing > .07*count:
                        local_ok = False
            if not local_ok or overlap / max(union,1) < .75:
                raise Unobservable("일본어 표시 글자가 기준과 일치하는지 판독할 수 없습니다.")


def glyph_mask(im):
    gray = im.convert("L")
    # Learner subtitles are white on a dark bubble; other speakers use dark ink.
    dark = sum(gray.histogram()[:128]) > gray.width*gray.height/2
    mask = gray.point(lambda v: 255 if (v > 195 if dark else v < 170) else 0)
    box = mask.getbbox()
    if not box:
        raise Unobservable("일본어 표시 영역에 판독할 글자가 없습니다.")
    return mask.crop(box)


def matches_lines(ref, actual, light, count):
    from PIL import ImageChops, ImageFilter
    def lines(im):
        mask = im.convert("L").point(lambda v: 255 if (v>198 if light else v<174) else 0)
        groups = []
        for y in range(mask.height):
            if mask.crop((0,y,mask.width,y+1)).histogram()[255] < 2:
                continue
            if not groups or y-groups[-1][-1]>2:
                groups.append([])
            groups[-1].append(y)
        out = []
        for group in groups:
            strip = mask.crop((0,group[0],mask.width,group[-1]+1))
            if strip.height>=4:
                out.append(strip.crop(strip.getbbox()))
        return out
    a,b = lines(ref),lines(actual)
    if len(a)!=count or len(b)!=count:
        return False
    for refline,line in zip(a,b):
        if abs((line.width/line.height)/(refline.width/refline.height)-1)>.10:
            return False
        height = min(32,refline.height,line.height)
        size = (round(height*refline.width/refline.height),height)
        norm = lambda im: im.resize(size,Image.Resampling.LANCZOS).point(lambda p:255 if p>=128 else 0)
        first,second = norm(refline),norm(line)
        for s,d in ((first,second),(second,first)):
            near = d.filter(ImageFilter.MaxFilter(3))
            err = ImageChops.subtract(s,near)
            for x in range(0,size[0],8):
                box=(x,0,min(x+8,size[0]),height)
                if err.crop(box).histogram()[255] > max(1,.10*s.crop(box).histogram()[255]):
                    return False
        if ImageChops.darker(first,second).histogram()[255] < .72*ImageChops.lighter(first,second).histogram()[255]:
            return False
    return True
