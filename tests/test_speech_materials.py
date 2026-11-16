"""Portable synthetic tests; no private artifacts, devices or installed audio tools.

Codec/TTS doubles exercise orchestration and calibration, not audio quality.
"""

import base64
from contextlib import contextmanager
import json
import math
from pathlib import Path
import sqlite3
import struct
import sys
import tempfile
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch
import xml.etree.ElementTree as ET

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import speech_materials as materials
import speech_navigation as navigation

PACKAGE = "com.weaversmind.tjp2"
PREFIX = PACKAGE + ":id/"
LABELS = {1: "\ub2e8\uc5b4", 2: "\ubb38\uc7a51", 3: "\ubb38\uc7a52", 4: "\ud68c\ud654"}
EXIT_MESSAGE = "\ud559\uc2b5\uc744 \uc911\ub2e8\ud558\uace0 \ub098\uac00\uc2dc\uaca0\uc2b5\ub2c8\uae4c?"
CONFIRM, CANCEL = "\ud655\uc778", "\ucde8\uc18c"
NEXT, START, COMPLETE = "\ub2e4\uc74c", "\ud559\uc2b5 \uc2dc\uc791", "\ud559\uc2b5 \uc644\ub8cc"


def make_database(path):
    db = sqlite3.connect(path)
    try:
        db.executescript("""
            CREATE TABLE WordSetStudy (
                WordID INTEGER, LevelNum INTEGER, EpisodeNum INTEGER,
                StageNum INTEGER, TypeNum INTEGER, OrderNum INTEGER,
                PRIMARY KEY(WordID,LevelNum,EpisodeNum,StageNum,TypeNum,OrderNum));
            CREATE TABLE WordData (
                WordID INTEGER PRIMARY KEY, WordText TEXT, WordMeaning TEXT, ResID INTEGER);
            CREATE TABLE WordDataCog (WordID INTEGER PRIMARY KEY, SRText TEXT, CognitionText TEXT);
            CREATE TABLE WordDataTrans (WordID INTEGER PRIMARY KEY, MainWordID INTEGER);
            CREATE TABLE StudyEpisodeInfo (
                LevelNum INTEGER, EpisodeNum INTEGER, EpisodeName TEXT,
                PRIMARY KEY(LevelNum,EpisodeNum));
            CREATE TABLE SessionInfo (
                LevelNum INTEGER, EpisodeNum INTEGER, StageNum INTEGER,
                StepNum INTEGER, SessionOrd INTEGER, SessionType TEXT,
                PRIMARY KEY(LevelNum,EpisodeNum,StageNum,StepNum));
        """)
        db.executemany("INSERT INTO StudyEpisodeInfo VALUES(?,?,?)", [
            (1, 1, "Synthetic alphabet"), (2, 1, "Synthetic week 1"), (2, 2, "Synthetic week 2")])
        for week, stage in ((1, 1), (1, 2), (1, 5), (2, 5)):
            db.executemany("INSERT INTO SessionInfo VALUES(?,?,?,?,?,?)", [
                (2, week, stage, step, ordinal, LABELS[ordinal])
                for step, ordinal in ((2, 1), (3, 2), (9, 3), (4, 4))])
        rows = [
            (30001, 2, 1, 1, 6, 1), (30004, 2, 1, 1, 6, 4),
            (40001, 2, 1, 1, 5, 1), (40002, 2, 1, 1, 5, 2),
            (30007, 2, 1, 2, 6, 1), (30010, 2, 1, 2, 6, 4),
            (30025, 2, 1, 5, 6, 1), (30028, 2, 1, 5, 6, 4),
            (30055, 2, 2, 5, 6, 1), (30056, 2, 2, 5, 7, 2),
            (30058, 2, 2, 5, 6, 4), (40038, 2, 2, 5, 5, 2),
            (20049, 2, 2, 5, 4, 1), (10001, 1, 1, 1, 1, 1),
            (20050, 2, 2, 5, 4, 2), (30059, 2, 2, 5, 7, 5),
        ]
        for row in rows:
            word = row[0]
            text = f"Synthetic word {30055 if word == 40038 else word}"
            target = "" if word == 20050 else f"target-{word}" + (";" if word == 30058 else "")
            db.execute("INSERT INTO WordSetStudy VALUES(?,?,?,?,?,?)", row)
            db.execute("INSERT INTO WordData VALUES(?,?,?,?)", (word, text, "Synthetic meaning", 0))
            db.execute("INSERT INTO WordDataCog VALUES(?,?,?)", (word, target, target))
        db.executemany("INSERT INTO WordDataTrans VALUES(?,?)", [(30056, 30055), (30059, 999)])
        db.commit()
    finally:
        db.close()


def node(rid="", text="", children=(), clickable=False, bounds="[10,10][110,60]", **attrs):
    result = ET.Element("node", {
        "resource-id": PREFIX + rid if rid else "", "text": text, "package": PACKAGE,
        "content-desc": "", "enabled": "true", "clickable": str(clickable).lower(),
        "scrollable": "false", "bounds": bounds, **attrs,
    })
    result.extend(children)
    return result


def screen(*children):
    root = ET.Element("hierarchy", rotation="1")
    root.append(node(children=children, bounds="[0,0][1200,800]"))
    return "<?xml version='1.0' encoding='UTF-8'?>\n" + ET.tostring(root, encoding="unicode") + "\n"


def menu(level=2, week=1, stages=(2, 3), selected_session=3, clipped_stage=None):
    rows, groups = [], []
    for number in (1, 2):
        labels = [node("tvEpTitleSubject", f"Synthetic week {number}")]
        if number == week:
            labels.insert(0, node("tvEpTitleNum", f"Week {number}"))
        rows.append(node("llEpisodeRow", children=labels, clickable=True,
                         bounds=f"[20,{number * 100}][220,{number * 100 + 70}]"))
    for index, stage in enumerate(stages):
        if stage == clipped_stage:
            groups.append(node(children=[node("tvStageNum", f"Stage {stage}",
                                               bounds="[306,735][386,740]")],
                               bounds="[270,730][1140,740]"))
            continue
        top = 100 + index * 240
        controls = [node("tvStageNum", f"Stage {stage}", bounds=f"[300,{top}][400,{top + 30}]")]
        for session, label in LABELS.items():
            left = 300 + (session - 1) * 180
            controls.extend([
                node(f"flNode{session}Circle", clickable=True,
                     bounds=f"[{left},{top + 40}][{left + 90},{top + 90}]"),
                node(f"tvNode{session}Label", label),
            ])
            if session == selected_session:
                controls.append(node(f"flStart{session}", children=[node(text=START)], clickable=True,
                                     bounds=f"[{left},{top + 110}][{left + 120},{top + 170}]"))
        groups.append(node(children=controls, bounds=f"[270,{top}][1140,{top + 210}]"))
    return screen(node("tvLevelTitle", f"Level {level}"), node("cvLeftArrow", clickable=True),
                  node("cvRightArrow", clickable=True), *rows,
                  node("listStagePath", children=groups, scrollable="true", bounds="[260,80][1160,740]"))


def cover(session=3):
    title = "\uc624\ub298\uc758 \uc0c1\ud669" if session == 4 else "\uc624\ub298\uc758 \ud559\uc2b5"
    return screen(node("flTodayLessonCoverOverlay", children=[
        node("tvTodayLessonTitle", title), node("btnTodayLessonNext", NEXT, clickable=True)]))


def exit_dialog(message=EXIT_MESSAGE):
    return screen(node("layoutMessageBox", children=[
        node("popup_content", message), node("btn_sel1", CONFIRM, clickable=True),
        node("btn_sel2", CANCEL, clickable=True)]))


def conversation_grade():
    return screen(node("tvConvGuide", "Synthetic conversation"),
                  node("cvTalkBubble", children=[node("tvTalkText", "Old target"), node("ivTalkGrade")]),
                  node("btnConvNext", NEXT, clickable=True))


def speaking(text="", reference=None):
    children = [node("fvSingleQuizText", text)]
    if reference is not None:
        children.append(node("tvKeySentenceText", reference))
    return screen(node("layoutSingleQuiz", children=children), node("ivCtrlPlay", clickable=True))


def intro(phase, level=2, week=2, stage=5):
    """Minimal, synthetic versions of the observed production intro layouts."""
    if phase == "today_cover":
        return cover(session=2)
    if phase == "lecture_cover":
        return screen(node("flLectureCoverOverlay", clickable=True, children=[
            node("tvLectureCoverLevel", f"Level {level}"),
            node("tvLectureCoverChapter", f"Week {week}"),
            node("tvLectureCoverStage", f"Stage {stage}"),
            node("tvLectureCoverTodayLabel", "\ubb38\uc7a51 \uc624\ub298\uc758 \uac15\uc758")]))
    if phase == "lecture_skip_confirm":
        return screen(node("rlLecturePopup", children=[node("layoutLecturePopup", children=[
            node(text="\uc815\ub9d0 \uac15\uc758\ub97c \uac74\ub108\ub6f0\uc2dc\uaca0\uc5b4\uc694?"),
            node(text="\ub3d9\uc601\uc0c1 \uac15\uc758\ub97c \uac74\ub108\ub6f0\uba74\n"
                      "\ub2e4\uc74c \uacfc\uc815\uc758 \ub0b4\uc6a9\uc774 \uc5b4\ub835\uac8c \ub290\uaef4\uc9c8 \uc218 \uc788\uc5b4\uc694.\n"
                      "\uadf8\ub798\ub3c4 \uac15\uc758\ub97c \uac74\ub108\ub6f0\uc2dc\uaca0\uc5b4\uc694?"),
            node("btn_sel1", "\uac74\ub108\ub6f0\uae30", clickable=True),
            node("btn_sel2", CANCEL, clickable=True)])]))
    if phase == "lecture_note":
        return screen(node("txtGuideMsg", children=[
            node(text="\ud559\uc2b5\ud55c \uac15\uc758 \ub0b4\uc6a9\uc744 \uc815\ub9ac\ud574\ubcf4\uc138\uc694.")]),
            node("lytContent", children=[node("imgContent")]),
            node("rlBottomControlBar", children=[
                node("btnCtrlReplayLecture", "\uac15\uc758 \ub2e4\uc2dc\ubcf4\uae30", clickable=True),
                node("btnCtrlNext", NEXT, clickable=True)]))
    if phase == "grammar":
        return screen(node("layoutSentenceDesc", children=[
            node("cvDescCard", children=[node("txtDescText")])]),
            node("txtGuideMsg", children=[
                node(text="\ud559\uc2b5\ud560 \ubb38\uc7a5\uc758 \uc911\uc694\ud55c \uc5b4\ubc95\uc744 \uc0b4\ud3b4\ubcf4\uc138\uc694.")]),
            node("rlBottomControlBar", children=[
                node("ivCtrlPrev", clickable=True), node("ivCtrlNext", clickable=True)]))
    if phase == "lecture_playback":
        return screen(node("layoutSentenceLecture", children=[
            node("layoutLecture", children=[node("mvLecture")])]))
    if phase == "first_speaking":
        return screen(node("layoutSingleQuiz", children=[
            node("tvSingleQuizGuide", "\ud575\uc2ec \ubb38\uc7a5\uc744 \uc775\ud788\uace0 \uc9c1\uc811 \ub9d0\ud574 \ubcf4\uc138\uc694."),
            node("tvSingleQuizPage", "1 / 3 \ubb38\uc7a5"), node("fvSingleQuizText")]))
    raise AssertionError("Unknown synthetic intro phase: " + phase)


def fixture(name):
    return {
        "menu": menu(), "cover": cover(), "conversation-grade": conversation_grade(),
        "speaking-hidden": speaking(), "speaking-reference": speaking(reference="Synthetic reference"),
        "session-result": screen(node("tvSlotName", LABELS[2]), node("tvScoreText", "50"),
                                 node("tvItemText", "Synthetic word 30001"),
                                 node("btnResultConfirm", COMPLETE, clickable=True)),
    }[name]


class SyntheticCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        for target in ("speech_materials._adb", "subprocess.run", "subprocess.Popen",
                       "socket.socket", "socket.create_connection", "socket.getaddrinfo"):
            guard = patch(target, side_effect=AssertionError("Unexpected external operation: " + target))
            guard.start()
            self.addCleanup(guard.stop)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root, self.db = Path(temp.name), Path(temp.name) / "synthetic.dat"
        make_database(self.db)
        self.rows = materials._read_catalog(self.db)


class InputLock:
    def __init__(self):
        self.held = False

    def __enter__(self):
        if self.held:
            raise AssertionError("Unexpected lock reentry")
        self.held = True
        return self

    def __exit__(self, *args):
        self.held = False


class Host:
    ADB_SERIAL = "offline-test"

    def __init__(self, screens, stop=None, cancel_after_input=False):
        self.screens = list(screens)
        self.inputs = []
        self._adb_input_lock = InputLock()
        self.stop = stop
        self.cancel_after_input = cancel_after_input

    def dump_ui_xml(self):
        return self.screens[0]

    def adb(self, *args, timeout_s=None):
        if args == ("shell", "dumpsys", "package", materials.PACKAGE):
            return b"versionCode=11", b"", 0
        if args[:2] != ("shell", "input"):
            raise AssertionError("Unexpected device call: " + repr(args))
        if not self._adb_input_lock.held:
            raise AssertionError("Input was dispatched without the host lock")
        self.inputs.append(args)
        if len(self.screens) > 1:
            self.screens.pop(0)
        if self.cancel_after_input:
            self.stop.set()
        return b"", b"", 0


class Samples:
    """A numeric double for the gain policy, not a resampling implementation."""
    ndim = 1

    def __init__(self, values):
        self.values = list(values)
        self.size = len(self.values)

    def all(self):
        return all(self.values)

    def __mul__(self, other):
        return Samples(v * other for v in self.values)

    def __gt__(self, other):
        return Samples(v > other for v in self.values)

    def __lt__(self, other):
        return Samples(v < other for v in self.values)

    def __or__(self, other):
        return Samples(a or b for a, b in zip(self.values, other.values))

    def astype(self, dtype):
        if dtype != "<i2":
            raise AssertionError("Expected little-endian PCM16")
        return SimpleNamespace(tobytes=lambda: struct.pack("<" + "h" * self.size, *self.values))


@contextmanager
def synthetic_tts(root):
    package = root / "pyopenjtalk"
    dictionary = package / "dictionary"
    dictionary.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    voice = package / "synthetic.voice"
    voice.write_bytes(b"synthetic voice")
    for name in ("sys.dic", "unk.dic", "matrix.bin", "char.bin"):
        (dictionary / name).write_bytes(b"synthetic dictionary placeholder")
    numpy = ModuleType("numpy")
    numpy.asarray = Samples
    numpy.isfinite = lambda a: Samples(math.isfinite(v) for v in a.values)
    numpy.abs = lambda a: Samples(abs(v) for v in a.values)
    numpy.rint = lambda a: Samples(round(v) for v in a.values)
    numpy.max = lambda a: max(a.values)
    numpy.min = lambda a: min(a.values)
    numpy.sum = lambda a: sum(a.values)
    scipy, signal, engine = ModuleType("scipy"), ModuleType("scipy.signal"), ModuleType("pyopenjtalk")
    scipy.signal = signal
    signal.resample_poly = Mock(return_value=Samples([60000.0, -40000.0, 0.0, 20000.0]))
    engine.DEFAULT_HTS_VOICE, engine.OPEN_JTALK_DICT_DIR = str(voice), str(dictionary)
    engine.__version__ = "synthetic-test-engine"
    engine.tts = Mock(return_value=([40000.0, -50000.0, 0.0, 10000.0], 48000))
    modules = {"numpy": numpy, "scipy": scipy, "scipy.signal": signal, "pyopenjtalk": engine}
    with patch.dict(sys.modules, modules), patch.object(materials, "LOCAL_TTS", root), \
            patch.object(sys, "path", list(sys.path)), patch("platform.uname"), \
            patch.object(sys, "addaudithook") as audit:
        yield engine, signal, audit


class MaterialsTests(SyntheticCase):
    def test_catalog_session_and_target_identity(self):
        rows = {r["word_id"]: r for r in self.rows}
        self.assertEqual(len(self.rows), 16)
        self.assertEqual(len({r["id"] for r in self.rows}), 16)
        self.assertEqual(rows[30056]["session"], 2)
        self.assertEqual(rows[30058]["session"], 3)
        self.assertEqual(rows[40038]["session"], 4)
        self.assertEqual(rows[30055]["text"], rows[40038]["text"])
        self.assertNotEqual(rows[30055]["id"], rows[40038]["id"])
        self.assertEqual(rows[30056]["target"], "target-30056;")
        self.assertEqual(rows[30058]["target"], "target-30058;")
        self.assertFalse(next(r for r in self.rows if r["level"] == 1)["navigation_available"])
        self.assertFalse(rows[20050]["navigation_available"])
        self.assertIn("Missing recognition target", rows[20050]["navigation_reason"])
        self.assertFalse(rows[30059]["navigation_available"])

    def test_repeated_word_ids_are_distinct_records(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "snapshot.dat"
            source = sqlite3.connect(self.db.as_uri() + "?mode=ro", uri=True)
            dest = sqlite3.connect(path)
            try:
                source.backup(dest)
                dest.execute("INSERT INTO WordSetStudy VALUES(20049,2,1,1,4,999)")
                dest.commit()
            finally:
                source.close()
                dest.close()
            rows = [r for r in materials._read_catalog(path) if r["word_id"] == 20049]
            self.assertEqual(len(rows), 2)
            self.assertEqual(len({r["id"] for r in rows}), 2)

    def test_catalog_reader_uses_readonly_sqlite(self):
        original = sqlite3.connect
        before = self.db.read_bytes()
        with patch.object(materials.sqlite3, "connect", wraps=original) as connect:
            materials._read_catalog(self.db)
        self.assertEqual(connect.call_args.args, (self.db.as_uri() + "?mode=ro",))
        self.assertTrue(connect.call_args.kwargs["uri"])
        self.assertEqual(self.db.read_bytes(), before)

    def test_missing_session_mapping_is_explicit_for_preview(self):
        with sqlite3.connect(self.db) as db:
            db.execute("DELETE FROM SessionInfo WHERE LevelNum=2 AND EpisodeNum=2 AND StageNum=5 AND StepNum=9")
        row = next(r for r in materials._read_catalog(self.db) if r["word_id"] == 30058)
        self.assertIsNone(row["session"])
        self.assertFalse(row["navigation_available"])
        self.assertIn("No verified SessionInfo mapping", row["navigation_reason"])

    def test_unsupported_core_order_does_not_guess_a_session(self):
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE WordSetStudy SET OrderNum=2 WHERE WordID=30055")
        row = next(r for r in materials._read_catalog(self.db) if r["word_id"] == 30056)
        self.assertIsNone(row["session"])
        self.assertFalse(row["navigation_available"])
        self.assertIn("not established", row["navigation_reason"])

    def test_connected_catalog_only_reads_main_and_rechecks_snapshot(self):
        raw = self.db.read_bytes()
        calls = []

        def adb(device, *args, **kwargs):
            calls.append(args)
            if args[0] == "shell":
                return b"versionCode=11"
            return b"" if "sh" in args else raw

        with tempfile.TemporaryDirectory() as root, patch.object(materials, "_adb", side_effect=adb):
            first = materials.catalog("offline-test", root)
            second = materials.catalog("offline-test", root)
        self.assertEqual(first["db_sha256"], materials.digest(raw))
        self.assertEqual(second["db_sha256"], first["db_sha256"])
        self.assertEqual(sum(c[-1] == "databases/main_tjp.dat" for c in calls), 4)
        self.assertTrue(all("user" not in " ".join(c) for c in calls))

    def test_active_wal_refused(self):
        with patch.object(materials, "_adb", side_effect=[b"versionCode=11", b"active"]):
            with self.assertRaisesRegex(materials.MaterialsUnavailable, "WAL"):
                materials._snapshot("offline-test")

    def test_changed_snapshot_refused(self):
        raw = self.db.read_bytes()
        with patch.object(materials, "_adb", side_effect=[b"versionCode=11", b"", raw, raw + b"x"]):
            with self.assertRaisesRegex(materials.MaterialsUnavailable, "changed"):
                materials._snapshot("offline-test")

    def test_recording_pcm_and_txt_unchanged(self):
        name = "20000101_120000_001"
        pcm = struct.pack("<6h", 0, 123, -456, 32767, -32768, 0)
        log = b"text=synthetic-target;\r\nmode=STANDARD\r\nRecognitionFrame frame=1 ret=1\r\nrecogState=1\r\n"
        stored = []

        def adb(device, *args, **kwargs):
            if args[0] == "shell":
                return b"versionCode=11"
            if args[1] == "ls":
                return (name + ".txt\n" + name + ".pcm\n20000101_000000_001.pcm\n").encode()
            return log if args[-1].endswith(".txt") else pcm

        def store(data, filename, metadata):
            stored.append((data, filename, metadata))
            return {"id": "asset"}

        with patch.object(materials, "_adb", side_effect=adb):
            result = materials.import_recordings("offline-test", store)
        self.assertEqual(result["assets"], [{"id": "asset"}])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertFalse(result["errors"])
        self.assertEqual(stored[0][0], pcm)
        source = stored[0][2]["source"]
        self.assertEqual(source["pcm_sha256"], materials.digest(pcm))
        self.assertEqual(source["txt_sha256"], materials.digest(log))
        self.assertEqual(base64.b64decode(source["txt_base64"]), log)
        self.assertEqual(source["expected_target"], "synthetic-target;")
        self.assertEqual(stored[0][2]["transforms"], [])

    def test_unfinished_or_invalid_recording_rejected(self):
        for pcm, log in ((b"\0\0", b"text=target;\n"), (b"x", b"text=target;\nrecogState=1\n"),
                         (b"\0\0", b"text=a;\ntext=b;\nrecogState=1\n")):
            with self.subTest(log=log):
                with self.assertRaises(materials.MaterialsUnavailable):
                    materials.recording_metadata(pcm, log, "test.pcm")

    def test_recording_batches_preserve_stability_checks_and_cursor(self):
        names = [f"20000101_120000_{number:03d}" for number in range(3)]
        calls = []
        def adb(device, *args, **kwargs):
            calls.append(args)
            if args[0] == 'shell':
                return b'versionCode=11'
            if args[1] == 'ls':
                return '\n'.join(name + suffix for name in names for suffix in ('.txt', '.pcm')).encode()
            return b'text=target;\nrecogState=1\n' if args[-1].endswith('.txt') else b'\0\0'
        with patch.object(materials, '_adb', side_effect=adb):
            first = materials.import_recordings('offline-test', lambda data, name, meta: name, limit=2)
            second = materials.import_recordings('offline-test', lambda data, name, meta: name,
                                                limit=2, after=first['next_after'])
        self.assertEqual(first['processed'], 2)
        self.assertEqual(first['total'], 3)
        self.assertEqual(second['processed'], 3)
        self.assertIsNone(second['next_after'])
        self.assertEqual(first['assets'] + second['assets'], [name + '.pcm' for name in names])
        self.assertEqual(sum(args[1] == 'cat' for args in calls if args[0] == 'exec-out'), 12)

    def test_invalid_recording_cursor_or_limit_never_reads_device(self):
        for options in ({'limit': 0}, {'limit': 26}, {'limit': True}, {'after': '../bad'}, {'after': None}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                materials.import_recordings('offline-test', lambda *args: None, **options)
        materials._adb.assert_not_called()

    def test_native_unknown_does_not_access_device(self):
        self.assertFalse(materials.extract_native("offline-test", {"word_id": 999}, "unused.pcm")["available"])
        materials._adb.assert_not_called()

    def test_native_target_mismatch_does_not_access_device(self):
        item = next(r for r in self.rows if r["word_id"] == 20049)
        result = materials.extract_native("offline-test", {**item, "subject": "other"}, "unused.pcm")
        self.assertFalse(result["available"])
        materials._adb.assert_not_called()

    def test_native_decode_contract_with_synthetic_codec(self):
        source = b"synthetic encoded audio"
        item = next(r for r in self.rows if r["word_id"] == 20049)
        mapping = {"path": "/synthetic/audio.pak", "offset": 17, "length": len(source),
                   "sha256": materials.digest(source), "target": item["target"]}
        codec = ModuleType("av")
        container = MagicMock()
        container.__enter__.return_value = container
        frame = object()
        container.decode.return_value = [frame]
        codec.open = Mock(return_value=container)
        converted = SimpleNamespace(to_ndarray=lambda: Samples([123, -456]))
        flushed = SimpleNamespace(to_ndarray=lambda: Samples([789]))
        resampler = Mock()
        resampler.resample.side_effect = [[converted], [flushed]]
        codec.AudioResampler = Mock(return_value=resampler)
        with patch.dict(sys.modules, {"av": codec}), \
                patch.object(materials, "_native_source", return_value=(source, mapping)):
            result = materials.extract_native(
                "offline-test", item, self.root / "native.pcm")
            self.assertTrue(result["available"], result)
            self.assertEqual(result["sha256"], materials.digest(Path(result["path"]).read_bytes()))
            self.assertEqual(result["sample_rate"], 16000)
            self.assertEqual(Path(result["path"]).read_bytes(), struct.pack("<3h", 123, -456, 789))
        codec.AudioResampler.assert_called_once_with(format="s16", layout="mono", rate=16000)
        self.assertEqual([c.args for c in resampler.resample.call_args_list], [(frame,), (None,)])
        self.assertEqual(codec.open.call_args.args[0].getvalue(), source)

    def test_native_corrupt_or_truncated_source_is_not_decoded_or_saved(self):
        item = next(r for r in self.rows if r["word_id"] == 20049)
        codec = ModuleType("av")
        codec.open = Mock()
        path = self.root / "rejected-native.pcm"
        for message in ("truncated", "changed", "unknown format"):
            with self.subTest(message=message), patch.dict(sys.modules, {"av": codec}), \
                    patch.object(materials, "_native_source", side_effect=materials.MaterialsUnavailable(message)):
                result = materials.extract_native(
                    "offline-test", item, path)
                self.assertFalse(result["available"])
                self.assertEqual(message, result["reason"])
                self.assertFalse(path.exists())
        codec.open.assert_not_called()

    def test_bad_device_serial(self):
        for serial in ("", None, "-d", "device;rm", "two devices"):
            with self.subTest(serial=serial), self.assertRaises(ValueError):
                materials.device_serial(serial)

    def test_tts_peak_calibration_with_synthetic_engine(self):
        path = self.root / "tts.pcm"
        with synthetic_tts(self.root / "engine") as (engine, signal, audit):
            def run_worker(args, **kwargs):
                self.assertEqual(args[-1], "--tts-worker")
                self.assertEqual(kwargs["timeout"], 120)
                payload = materials._tts_worker(json.loads(kwargs["input"])["text"])
                return SimpleNamespace(returncode=0, stdout=json.dumps(payload).encode(), stderr=b"")

            with patch.object(materials.subprocess, "run", side_effect=run_worker) as run:
                result = materials.generate_tts("Synthetic utterance", path)
            self.assertEqual(run.call_count, 1)
            with self.assertRaises(ValueError):
                materials.generate_tts("again", path)
            engine.tts.assert_called_once_with("Synthetic utterance", speed=1.0, half_tone=0.0,
                                              run_marine=False, use_tsqyomi=False, predict_nani=False)
            self.assertEqual(signal.resample_poly.call_args.args[1:], (1, 3))
            hook = audit.call_args.args[0]
            for event in ("socket.connect", "socket.getaddrinfo", "subprocess.Popen"):
                with self.subTest(event=event), self.assertRaises(materials.MaterialsUnavailable):
                    hook(event, ())
        pcm = struct.pack("<4h", 29490, -19660, 0, 9830)
        self.assertEqual(path.read_bytes(), pcm)
        self.assertEqual((result["sample_rate"], result["channels"], result["sample_width"]), (16000, 1, 2))
        self.assertEqual(result["sha256"], materials.digest(pcm))
        self.assertEqual(result["clipping_samples"], 0)
        self.assertAlmostEqual(result["synthesis_gain"], 0.9 * 32767 / 60000)
        self.assertEqual(result["pre_gain_out_of_pcm16_samples"], 2)
        self.assertEqual(result["gain_policy"], "new_synthesis_peak_normalization")
        self.assertFalse(result["speaker_used"])


class NavigationTests(SyntheticCase):
    def setUp(self):
        super().setUp()
        rows = self.rows
        self.item = next(r for r in rows if (r["level"], r["week"], r["stage"], r["session"], r["order"]) == (2, 1, 2, 3, 4))
        self.menu = fixture("menu")
        self.cover = fixture("cover")
        self.exit = exit_dialog()
        self.stop = threading.Event()

    def nav(self, screens, **kwargs):
        host = Host(screens, self.stop, **kwargs)
        return navigation.Navigator(host, host.ADB_SERIAL, self.stop), host

    def test_existing_menu_progresses_to_cover_without_starting_mic(self):
        nav, host = self.nav([self.menu, self.cover])
        result = nav.prepare(self.item)
        self.assertTrue(result["ready"], result)
        self.assertEqual(len(host.inputs), 1)
        self.assertEqual(result["start_selector"]["resource_id"], navigation.PREFIX + "btnTodayLessonNext")
        self.assertFalse(result["target_verified"])
        self.assertTrue(result["requires_armed_start"])
        self.assertFalse(result["unit_completed"])

    def test_level_selection_progresses_with_observed_arrow(self):
        earlier = self.menu.replace("Level 2", "Level 1")
        nav, host = self.nav([earlier, self.menu, self.cover])
        result = nav.prepare(self.item)
        self.assertTrue(result["ready"], result)
        self.assertEqual(result["actions"][0]["selector"]["resource_id"], navigation.PREFIX + "cvRightArrow")
        self.assertEqual(len(host.inputs), 2)

    def test_week_selection_verifies_new_week_before_stage_entry(self):
        item = next(r for r in self.rows if r["word_id"] == 30058)
        changed = self.menu.replace("Week 1", "Week 2").replace("Stage 2", "Stage 5").replace("Stage 3", "Stage 6")
        nav, host = self.nav([self.menu, changed, self.cover])
        result = nav.prepare(item)
        self.assertTrue(result["ready"], result)
        self.assertEqual(result["actions"][0]["selector"]["resource_id"], navigation.PREFIX + "llEpisodeRow")
        self.assertEqual(result["start_selector"]["scope"]["week"], 2)
        self.assertEqual(result["start_selector"]["scope"]["stage"], 5)
        self.assertEqual(len(host.inputs), 2)

    def test_stage_scrolling_uses_observed_scroll_container(self):
        item = next(r for r in self.rows
                    if (r["level"], r["week"], r["stage"], r["session"]) == (2, 1, 5, 3))
        changed = self.menu.replace("Stage 2", "Stage 5").replace("Stage 3", "Stage 6")
        nav, host = self.nav([self.menu, changed, self.cover])
        result = nav.prepare(item)
        self.assertTrue(result["ready"], result)
        self.assertEqual(result["actions"][0]["action"], "scroll")
        self.assertEqual(host.inputs[0][2], "swipe")
        self.assertEqual(result["start_selector"]["scope"]["stage"], 5)

    def test_clipped_stage_header_scrolls_until_controls_are_visible(self):
        item = next(r for r in self.rows if r["word_id"] == 30058)
        clipped = menu(week=2, stages=(4, 5), clipped_stage=5)
        nav, host = self.nav([clipped, menu(week=2, stages=(5,)), self.cover])
        result = nav.prepare(item)
        self.assertTrue(result["ready"], result)
        self.assertEqual([a["action"] for a in result["actions"]], ["scroll", "tap"])
        self.assertEqual(host.inputs[0][2], "swipe")
        self.assertEqual(result["start_selector"]["scope"]["stage"], 5)

    def test_clipped_stage_without_scroll_progress_blocks(self):
        item = next(r for r in self.rows if r["word_id"] == 30058)
        nav, host = self.nav([menu(week=2, stages=(4, 5), clipped_stage=5)])
        result = nav.prepare(item)
        self.assertFalse(result["ready"])
        self.assertEqual(len(host.inputs), 1)
        self.assertIn("no progress", result["reason"])

    def test_stage_alignment_does_not_overshoot_visible_header(self):
        xml = menu(week=2, stages=(4, 5), clipped_stage=5)
        xml = xml.replace('[306,735][386,740]', '[306,340][386,370]')
        nav, host = self.nav([xml, menu(week=2, stages=(5,)), self.cover])
        item = next(r for r in self.rows if r["word_id"] == 30058)
        result = nav.prepare(item)
        self.assertTrue(result["ready"], result)
        command = host.inputs[0]
        self.assertEqual(int(command[4]) - int(command[6]), 227)
        self.assertEqual(command[-1], "800")

    def test_stage_root_never_borrows_clipped_sibling_controls(self):
        root = ET.fromstring(menu(week=2, stages=(4, 5), clipped_stage=5))
        view = navigation.Screen(ET.tostring(root, encoding="unicode"))
        previous_header = view.one("tvStageNum", "Stage 4")
        view.parents[previous_header].remove(previous_header)
        header = view.one("tvStageNum", "Stage 5")
        target_row = view.parents[header]
        target_row.remove(header)
        target_row.append(node("flChapterBanner", children=[header]))
        xml = ET.tostring(view.root, encoding="unicode")
        view = navigation.Screen(xml)
        scroller = view.one("listStagePath")
        target_row = view.stage(5)
        self.assertIs(view.parents[target_row], scroller)
        self.assertIsNot(target_row, scroller)
        self.assertFalse(view.find("flNode1Circle", scope=target_row))
        self.assertFalse(view.find("flStart3", scope=target_row))
        self.assertTrue(view.find("flStart3", scope=scroller))
        item = next(r for r in self.rows if r["word_id"] == 30058)
        nav, host = self.nav([xml, menu(week=2, stages=(5,)), self.cover])
        result = nav.prepare(item)
        self.assertTrue(result["ready"], result)
        self.assertEqual([a["action"] for a in result["actions"]], ["scroll", "tap"])
        self.assertEqual(host.inputs[0][2], "swipe")

    def test_stage_does_not_require_other_sessions_to_be_visible(self):
        view = navigation.Screen(menu(week=2, stages=(5,)))
        for session in (1, 2, 4):
            for rid in (f"flNode{session}Circle", f"tvNode{session}Label"):
                control = view.one(rid)
                view.parents[control].remove(control)
        xml = ET.tostring(view.root, encoding="unicode")
        nav, host = self.nav([xml, self.cover])
        item = next(r for r in self.rows if r["word_id"] == 30058)
        result = nav.prepare(item)
        self.assertTrue(result["ready"], result)
        self.assertEqual([a["action"] for a in result["actions"]], ["tap"])
        self.assertEqual(result["actions"][0]["selector"]["resource_id"], PREFIX + "flStart3")
        self.assertEqual(len(host.inputs), 1)

    def test_stage_header_outside_list_cannot_claim_sibling_controls(self):
        root = ET.fromstring(menu(week=2, stages=(4,)))
        root.find("node").append(node("tvStageNum", "Stage 5"))
        xml = ET.tostring(root, encoding="unicode")
        nav, host = self.nav([xml])
        item = next(r for r in self.rows if r["word_id"] == 30058)
        result = nav.prepare(item)
        self.assertFalse(result["ready"])
        self.assertFalse(host.inputs)
        self.assertIn("tvStageNum", result["reason"])

    def test_stage_rejects_multiple_headers_in_one_list_row(self):
        view = navigation.Screen(menu(week=2, stages=(5,)))
        row = view.parents[view.one("tvStageNum", "Stage 5")]
        row.append(node("tvStageNum", "Stage 6"))
        nav, host = self.nav([ET.tostring(view.root, encoding="unicode")])
        item = next(r for r in self.rows if r["word_id"] == 30058)
        result = nav.prepare(item)
        self.assertFalse(result["ready"])
        self.assertFalse(host.inputs)
        self.assertIn("unique list row", result["reason"])

    def test_aligned_or_tiny_offset_stage_does_not_dispatch_scroll(self):
        item = next(r for r in self.rows if r["word_id"] == 30058)
        for offset in (0, 1, -1, 15, -15):
            with self.subTest(offset=offset):
                view = navigation.Screen(menu(week=2, stages=(5,), clipped_stage=5))
                header = view.one("tvStageNum", "Stage 5")
                top = 80 + (740 - 80) // 20 + offset
                header.set("bounds", f"[306,{top}][386,{top + 30}]")
                view.parents[header].set("bounds", f"[270,{top}][1140,740]")
                nav, host = self.nav([ET.tostring(view.root, encoding="unicode")])
                result = nav.prepare(item)
                self.assertFalse(result["ready"])
                self.assertFalse(host.inputs)
                self.assertFalse(result["actions"])
                self.assertIn("cannot make safe progress", result["reason"])

    def test_alignment_stops_after_scroll_reaches_header_without_controls(self):
        initial = menu(week=2, stages=(5,), clipped_stage=5)
        view = navigation.Screen(initial)
        header = view.one("tvStageNum", "Stage 5")
        header.set("bounds", "[306,113][386,143]")
        view.parents[header].set("bounds", "[270,113][1140,740]")
        nav, host = self.nav([initial, ET.tostring(view.root, encoding="unicode")])
        item = next(r for r in self.rows if r["word_id"] == 30058)
        result = nav.prepare(item)
        self.assertFalse(result["ready"])
        self.assertEqual(len(host.inputs), 1)
        self.assertEqual(host.inputs[0][-1], "800")
        self.assertIn("cannot make safe progress", result["reason"])

    def test_stage_scrolling_keeps_ninety_second_deadline(self):
        nav, host = self.nav([self.menu])
        with patch.object(navigation.time, "monotonic", return_value=100):
            nav._begin()
            self.assertEqual(nav._deadline, 190)
            nav._scroll_stage(navigation.Screen(self.menu), 5)
        self.assertEqual(host.inputs[0][-1], "800")
        with patch.object(navigation.time, "monotonic", return_value=191):
            with self.assertRaisesRegex(navigation.NavigationBlocked, "time limit"):
                nav._scroll_stage(navigation.Screen(self.menu), 5)
        self.assertEqual(len(host.inputs), 1)

    def test_tiny_stage_viewport_does_not_dispatch_swipe(self):
        view = navigation.Screen(self.menu)
        view.one("listStagePath").set("bounds", "[260,80][1160,90]")
        nav, host = self.nav([ET.tostring(view.root, encoding="unicode")])
        item = next(r for r in self.rows
                    if (r["level"], r["week"], r["stage"], r["session"]) == (2, 1, 5, 3))
        result = nav.prepare(item)
        self.assertFalse(result["ready"])
        self.assertFalse(host.inputs)
        self.assertIn("viewport is too small", result["reason"])

    def test_word_30056_from_exit_dialog_to_armed_sentence1_cover(self):
        """Ready here means armable cover, not verified third/first engine target."""
        selected = next(r for r in self.rows if r["word_id"] == 30056)
        for order in (2, 3):
            with self.subTest(order=order):
                item = {**selected, "order": order,
                        "text": "\u307e\u3060\u5927\u5b66\u751f\u3067\u3059\u3002",
                        "target": "\u307e\u3060\u3060\u3044\u304c\u304f\u305b\u3044\u3067\u3059;"}
                nav, host = self.nav([
                    self.exit, self.menu, menu(week=2, stages=(4, 5), clipped_stage=5),
                    menu(week=2, stages=(5,), selected_session=None),
                    menu(week=2, stages=(5,), selected_session=2), cover(session=2),
                ])
                self.assertIsNone(nav._scope)
                result = nav.prepare(item)
                self.assertTrue(result["ready"], result)
                self.assertEqual([a["selector"]["resource_id"] for a in result["actions"]], [
                    PREFIX + rid for rid in ("btn_sel1", "llEpisodeRow", "listStagePath",
                                             "flNode2Circle", "flStart2")])
                self.assertEqual(len(host.inputs), 5)
                self.assertEqual(result["start_selector"]["resource_id"], PREFIX + "btnTodayLessonNext")
                self.assertEqual(result["start_selector"]["scope"], {
                    "subject": "japanese-premium", "level": 2, "week": 2, "stage": 5, "session": 2})
                self.assertEqual(result["ready_for"], "armed_start")
                self.assertEqual(result["expected_target"], item["target"])
                self.assertTrue(result["requires_armed_start"])
                self.assertFalse(result["target_verified"])
                self.assertFalse(result["unit_completed"])
                self.assertEqual(host.screens, [cover(session=2)])

    def test_exact_exit_confirmation_recovers_and_progresses(self):
        nav, host = self.nav([self.exit, self.menu, self.cover])
        result = nav.prepare(self.item)
        self.assertTrue(result["ready"], result)
        self.assertEqual(len(host.inputs), 2)
        self.assertEqual(result["actions"][0]["selector"]["resource_id"], navigation.PREFIX + "btn_sel1")
        self.assertEqual(result["actions"][0]["selector"]["text"], navigation.CONFIRM)

    def test_current_conversation_without_scope_recovers_through_exit(self):
        nav, host = self.nav([fixture("conversation-grade"), self.exit, self.menu, self.cover])
        result = nav.prepare(self.item)
        self.assertTrue(result["ready"], result)
        self.assertEqual(host.inputs[0], ("shell", "input", "keyevent", "4"))
        self.assertEqual(len(host.inputs), 3)

    def test_restart_same_conversation_uses_safe_exit_then_verified_menu(self):
        item = next(r for r in self.rows if r["word_id"] == 40001)
        nav, host = self.nav([
            conversation_grade(), self.exit,
            menu(stages=(1,), selected_session=4), cover(session=4),
        ])
        nav._scope = nav._identity(item)
        nav._item = dict(item)
        nav._unit = nav._scope[:4]
        old_handoff = {"resource_id": PREFIX + "btnConvNext"}
        nav._last_handoff = old_handoff
        result = nav.restart_session(item)
        self.assertTrue(result["ready"], result)
        self.assertEqual([a["action"] for a in result["actions"]], ["back", "tap", "tap"])
        self.assertEqual(host.inputs[0], ("shell", "input", "keyevent", "4"))
        self.assertEqual([a["selector"]["resource_id"] for a in result["actions"][1:]],
                         [PREFIX + "btn_sel1", PREFIX + "flStart4"])
        self.assertIsNone(result["actions"][0]["selector"]["scope"])
        self.assertEqual(result["start_selector"]["resource_id"], PREFIX + "btnTodayLessonNext")
        self.assertEqual(result["start_selector"]["scope"]["session"], 4)
        self.assertEqual(result["expected_target"], item["target"])
        self.assertTrue(result["requires_armed_start"])
        self.assertFalse(result["target_verified"])
        self.assertFalse(result["unit_completed"])
        self.assertNotIn("previous_word_id", result)
        self.assertIsNot(nav._last_handoff, old_handoff)
        self.assertEqual(len(host.inputs), 3)
        self.assertEqual(host.screens, [cover(session=4)])

    def test_restart_does_not_use_sequential_next_handoff(self):
        previous = next(r for r in self.rows if r["word_id"] == 40001)
        following = next(r for r in self.rows if r["word_id"] == 40002)
        nav, host = self.nav([conversation_grade(), self.exit,
                              menu(stages=(1,), selected_session=4), cover(session=4)])
        nav._scope = nav._identity(previous)
        nav._item = previous
        result = nav.restart_session(following)
        self.assertTrue(result["ready"], result)
        self.assertEqual(result["actions"][0]["action"], "back")
        self.assertEqual(result["expected_target"], following["target"])
        self.assertNotIn("previous_word_id", result)
        self.assertEqual(len(host.inputs), 3)

    def test_restart_unknown_dialog_does_not_tap_or_preserve_continuity(self):
        nav, host = self.nav([exit_dialog("Unverified confirmation")])
        nav._scope, nav._item = nav._identity(self.item), self.item
        nav._last_handoff = {"old": True}
        result = nav.restart_session(self.item)
        self.assertFalse(result["ready"])
        self.assertIsNone(nav._scope)
        self.assertIsNone(nav._last_handoff)
        self.assertFalse(host.inputs)

    def test_restart_cancellation_after_back_prevents_exit_confirmation(self):
        nav, host = self.nav([conversation_grade(), self.exit], cancel_after_input=True)
        nav._scope, nav._item = nav._identity(self.item), self.item
        result = nav.restart_session(self.item)
        self.assertFalse(result["ready"])
        self.assertTrue(result["stopped"])
        self.assertEqual(host.inputs, [("shell", "input", "keyevent", "4")])

    def test_restart_preserves_whole_unit_boundary(self):
        nav, host = self.nav([conversation_grade()])
        nav._scope, nav._item = nav._identity(self.item), self.item
        nav._unit = nav._scope[:4]
        result = nav.restart_session({**self.item, "stage": 5}, mode="unit")
        self.assertFalse(result["ready"])
        self.assertIn("cannot cross", result["reason"])
        self.assertFalse(host.inputs)

    def test_other_confirmation_is_never_clicked(self):
        raw = self.exit.replace(navigation.EXIT_MESSAGE, "Unrecognized confirmation")
        nav, host = self.nav([raw])
        result = nav.prepare(self.item)
        self.assertFalse(result["ready"])
        self.assertFalse(host.inputs)

    def test_cancel_immediately_after_input(self):
        nav, host = self.nav([self.exit, self.menu, self.cover], cancel_after_input=True)
        result = nav.prepare(self.item)
        self.assertTrue(result["stopped"], result)
        self.assertFalse(result["ready"])
        self.assertEqual(len(host.inputs), 1)
        self.assertTrue(result["actions"][0]["sent"])

    def test_cancel_while_waiting_for_input_lock(self):
        nav, host = self.nav([self.exit])
        stop = self.stop

        class BusyLock:
            def acquire(self, timeout):
                stop.set()
                return False

            def release(self):
                raise AssertionError("Unowned lock released")

        host._adb_input_lock = BusyLock()
        result = nav.prepare(self.item)
        self.assertTrue(result["stopped"])
        self.assertFalse(host.inputs)

    def test_changed_screen_before_dispatch_sends_no_input(self):
        nav, host = self.nav([self.menu])
        calls = []

        def dump():
            calls.append(1)
            return self.menu if len(calls) == 1 else self.menu.replace("Stage 2", "Stage 9")

        host.dump_ui_xml = dump
        result = nav.prepare(self.item)
        self.assertFalse(result["ready"])
        self.assertIn("changed", result["reason"])
        self.assertFalse(host.inputs)

    def test_unknown_screen_cannot_be_ready(self):
        raw = '<hierarchy><node package="com.weaversmind.tjp2" enabled="true" /></hierarchy>'
        nav, host = self.nav([raw])
        self.assertFalse(nav.prepare(self.item)["ready"])
        self.assertFalse(host.inputs)

    def test_foreign_package_refused(self):
        nav, host = self.nav([self.menu.replace(materials.PACKAGE, "com.weaversmind.tjp")])
        self.assertFalse(nav.prepare(self.item)["ready"])
        self.assertFalse(host.inputs)

    def test_no_progress_is_bounded(self):
        item = {**self.item, "level": 3}
        nav, host = self.nav([self.menu])
        result = nav.prepare(item)
        self.assertFalse(result["ready"])
        self.assertEqual(len(host.inputs), 1)
        self.assertIn("no progress", result["reason"])

    def test_wrong_session_label_refused(self):
        nav, host = self.nav([self.menu])
        self.assertFalse(nav.prepare({**self.item, "session_label": "wrong"})["ready"])
        self.assertFalse(host.inputs)

    def test_unsupported_mapping_never_sends_input(self):
        nav, host = self.nav([self.menu])
        result = nav.prepare({**self.item, "navigation_available": False,
                              "navigation_reason": "Synthetic unsupported mapping"})
        self.assertFalse(result["ready"])
        self.assertEqual(result["reason"], "Synthetic unsupported mapping")
        self.assertFalse(host.inputs)

    def test_unsupported_generic_identity_never_sends_input(self):
        for change in ({"level": 1}, {"session": 99}, {"subject": "another-subject"}, {"kind": "unknown"}):
            with self.subTest(change=change):
                nav, host = self.nav([self.menu])
                self.assertFalse(nav.prepare({**self.item, **change})["ready"])
                self.assertFalse(host.inputs)

    def test_scope_never_guessed_from_hidden_target(self):
        nav, host = self.nav([fixture("speaking-hidden")])
        nav._scope = nav._identity(self.item)
        result = nav.prepare(self.item)
        self.assertFalse(result["ready"])
        self.assertIn("hidden", result["reason"])
        self.assertFalse(host.inputs)

    def test_reference_heading_is_not_current_target(self):
        screen = navigation.Screen(fixture("speaking-reference"))
        item = {**self.item, "text": screen.one("tvKeySentenceText").get("text")}
        with self.assertRaisesRegex(navigation.NavigationBlocked, "hidden"):
            navigation.Navigator._target_node(screen, item)

    def test_sequential_prepare_hands_off_new_target_not_old_title(self):
        rows = self.rows
        previous = next(r for r in rows if (r["level"], r["week"], r["stage"], r["session"], r["order"]) == (2, 1, 1, 4, 1))
        following = next(r for r in rows if (r["level"], r["week"], r["stage"], r["session"], r["order"]) == (2, 1, 1, 4, 2))
        nav, host = self.nav([fixture("conversation-grade")])
        nav._scope = nav._identity(previous)
        nav._item = previous
        nav._unit = nav._scope[:4]
        result = nav.prepare(following)
        self.assertTrue(result["ready"], result)
        self.assertEqual(result["expected_target"], following["target"])
        self.assertEqual(result["previous_word_id"], previous["word_id"])
        self.assertFalse(result["target_verified"])
        self.assertFalse(host.inputs)

    def test_whole_stage_never_completed_from_empty_context(self):
        nav, host = self.nav([self.menu])
        result = nav.complete_unit()
        self.assertFalse(result["unit_completed"])
        self.assertFalse(host.inputs)

    def test_session_result_is_not_whole_stage_completion(self):
        item = next(r for r in self.rows if r["word_id"] == 30001)
        menu = self.menu.replace("Stage 2", "Stage 1").replace("Stage 3", "Stage 2")
        nav, host = self.nav([fixture("session-result"), menu])
        nav._scope = nav._identity(item)
        nav._unit = nav._scope[:4]
        nav._item = item
        result = nav.complete_unit()
        self.assertTrue(result.get("session_completed"), result)
        self.assertFalse(result["unit_completed"])
        self.assertEqual(result["completion_scope"], "session")
        self.assertEqual(len(host.inputs), 1)

    def test_hash_ignores_xml_declaration_and_trailing_newline(self):
        raw = self.cover[self.cover.find("<hierarchy"):self.cover.rfind("</hierarchy>") + len("</hierarchy>")]
        self.assertEqual(navigation.Screen(self.cover).sha256, navigation.Screen(raw).sha256)

    def test_scope_does_not_accept_fractional_identifiers(self):
        nav, host = self.nav([self.menu])
        result = nav.prepare({**self.item, "stage": 2.1})
        self.assertFalse(result["ready"])
        self.assertFalse(host.inputs)


class IntroTests(SyntheticCase):
    def setUp(self):
        super().setUp()
        self.item = next(r for r in self.rows if r["word_id"] == 30055)
        self.scope = {**self.item, "subject": "japanese-premium"}

    def test_first_core_from_exit_to_cover_carries_intro_scope(self):
        host = Host([exit_dialog(), menu(week=2, stages=(5,), selected_session=2), cover(2)])
        nav = navigation.Navigator(host, host.ADB_SERIAL, None)
        route = nav.prepare(self.item)
        self.assertTrue(route["ready"], route)
        self.assertEqual(len(host.inputs), 2)
        self.assertEqual(route["start_selector"]["intro_scope"]["word_id"], 30055)
        selector = navigation.next_intro_selector(cover(2), route["start_selector"]["intro_scope"])
        self.assertEqual(selector["resource_id"], PREFIX + "btnTodayLessonNext")
        self.assertEqual(selector["expected_target"], self.item["target"])
        self.assertFalse(selector["target_verified"])
        self.assertEqual(host.screens, [cover(2)])

    def test_observed_intro_sequence_returns_selectors_without_input(self):
        expected = {
            "today_cover": "btnTodayLessonNext", "lecture_cover": "flLectureCoverOverlay",
            "lecture_skip_confirm": "btn_sel1", "lecture_note": "btnCtrlNext", "grammar": "ivCtrlNext",
        }
        scope = self.scope
        for phase, rid in expected.items():
            with self.subTest(phase=phase):
                raw = intro(phase)
                selected = navigation.next_intro_selector(raw, scope)
                self.assertEqual(selected["resource_id"], PREFIX + rid)
                self.assertEqual(selected["intro_phase"], phase)
                self.assertEqual(selected["expected_target"], self.item["target"])
                self.assertEqual(selected["screen_sha256"], navigation.Screen(raw).sha256)
                self.assertEqual(selected["screen_hash_format"], "hierarchy")
                self.assertTrue(selected["requires_armed_start"])
                self.assertFalse(selected["target_verified"])
                self.assertNotIn("bounds", selected)
                self.assertNotIn("coordinates", selected)
                scope = selected["intro_scope"]

    def test_nonfirst_core_cannot_get_intro_scope_or_skip_recognition(self):
        variation = next(r for r in self.rows if r["word_id"] == 30056)
        host = Host([menu(week=2, stages=(5,), selected_session=2), cover(2)])
        nav = navigation.Navigator(host, host.ADB_SERIAL, None)
        route = nav.prepare(variation)
        self.assertTrue(route["ready"], route)
        self.assertNotIn("intro_scope", route["start_selector"])
        self.assertFalse(route["target_verified"])
        for item in (variation, {**self.scope, "order": 3}, {**self.scope, "session": 3}):
            with self.subTest(item=item["word_id"], order=item["order"]):
                with self.assertRaisesRegex(navigation.NavigationBlocked, "first core only"):
                    navigation.next_intro_selector(intro("grammar"), {**item, "subject": "japanese-premium"})

    def test_intro_requires_complete_exact_scope(self):
        for key in ("subject", "word_id", "level", "week", "stage", "session", "kind", "order", "target"):
            with self.subTest(missing=key):
                scope = dict(self.scope)
                scope.pop(key, None)
                with self.assertRaises(navigation.NavigationBlocked):
                    navigation.next_intro_selector(intro("grammar"), scope)
        for changed in ({"subject": "other"}, {"package": "other"}, {"build": 12}, {"stage": 5.1}):
            with self.subTest(changed=changed), self.assertRaises(navigation.NavigationBlocked):
                navigation.next_intro_selector(intro("grammar"), {**self.scope, **changed})

    def test_lecture_cover_must_confirm_level_week_stage_and_session(self):
        for args in ({"level": 3}, {"week": 1}, {"stage": 4}):
            with self.subTest(args=args), self.assertRaises(navigation.NavigationBlocked):
                navigation.next_intro_selector(intro("lecture_cover", **args), self.scope)
        raw = intro("lecture_cover").replace("\ubb38\uc7a51", "\ubb38\uc7a52")
        with self.assertRaises(navigation.NavigationBlocked):
            navigation.next_intro_selector(raw, self.scope)

    def test_skip_requires_exact_observed_layout_title_body_and_buttons(self):
        raw = intro("lecture_skip_confirm")
        for old in ("rlLecturePopup", "layoutLecturePopup", "\uc815\ub9d0", "\ub3d9\uc601\uc0c1", CANCEL, "\uac74\ub108\ub6f0\uae30"):
            with self.subTest(changed=old), self.assertRaises(navigation.NavigationBlocked):
                navigation.next_intro_selector(raw.replace(old, "unrecognized"), self.scope)
        generic = screen(node("layoutMessageBox", children=[
            node("popup_content", "\uc815\ub9d0 \uac15\uc758\ub97c \uac74\ub108\ub6f0\uc2dc\uaca0\uc5b4\uc694?"),
            node("btn_sel1", "\uac74\ub108\ub6f0\uae30", clickable=True), node("btn_sel2", CANCEL, clickable=True)]))
        with self.assertRaisesRegex(navigation.NavigationBlocked, "Generic dialog"):
            navigation.next_intro_selector(generic, self.scope)

    def test_note_and_grammar_require_exact_guides_and_observed_structure(self):
        for phase, marker in (("lecture_note", "imgContent"), ("grammar", "cvDescCard")):
            for old in (marker, "txtGuideMsg", "rlBottomControlBar", "\ud559\uc2b5"):
                with self.subTest(phase=phase, changed=old), self.assertRaises(navigation.NavigationBlocked):
                    navigation.next_intro_selector(intro(phase).replace(old, "unrecognized"), self.scope)

    def test_disabled_ambiguous_or_overlapping_intro_controls_block(self):
        root = ET.fromstring(intro("grammar"))
        control = next(n for n in root.iter("node") if n.get("resource-id") == PREFIX + "ivCtrlNext")
        control.set("enabled", "false")
        with self.assertRaises(navigation.NavigationBlocked):
            navigation.next_intro_selector(ET.tostring(root, encoding="unicode"), self.scope)
        control.set("enabled", "true")
        root[0].append(node("ivCtrlNext", clickable=True))
        with self.assertRaises(navigation.NavigationBlocked):
            navigation.next_intro_selector(ET.tostring(root, encoding="unicode"), self.scope)
        root[0].append(node("layoutSingleQuiz"))
        with self.assertRaisesRegex(navigation.NavigationBlocked, "overlapping"):
            navigation.next_intro_selector(ET.tostring(root, encoding="unicode"), self.scope)

    def test_playback_and_first_speaking_are_no_input_boundaries(self):
        self.assertIsNone(navigation.next_intro_selector(intro("lecture_playback"), self.scope))
        self.assertIsNone(navigation.next_intro_selector(intro("first_speaking"), self.scope))
        for raw in (intro("lecture_playback").replace('clickable="false"', 'clickable="true"'),
                    intro("first_speaking").replace("1 / 3", "2 / 3"), screen(node("unknown"))):
            with self.subTest(raw=raw), self.assertRaises(navigation.NavigationBlocked):
                navigation.next_intro_selector(raw, self.scope)

    def test_prepare_and_skip_only_handoff_verified_first_core_intro(self):
        for phase in ("lecture_cover", "lecture_skip_confirm", "lecture_note", "grammar"):
            with self.subTest(phase=phase):
                host = Host([intro(phase)])
                nav = navigation.Navigator(host, host.ADB_SERIAL, None)
                nav._scope, nav._item = nav._identity(self.item), self.item
                route = nav.prepare(self.item)
                self.assertTrue(route["ready"], route)
                self.assertEqual(route["start_selector"]["intro_phase"], phase)
                self.assertEqual(route["start_selector"]["intro_scope"]["word_id"], 30055)
                self.assertFalse(route["target_verified"])
                if phase == "lecture_skip_confirm":
                    self.assertTrue(nav.skip(self.item)["ready"])
                self.assertFalse(host.inputs)

    def test_skip_never_uses_generic_dialog_or_unselected_recognition(self):
        for raw, item in ((exit_dialog(), self.item), (speaking(self.item["text"]), self.item),
                          (intro("lecture_skip_confirm"), next(r for r in self.rows if r["word_id"] == 30056))):
            with self.subTest(raw=raw, word_id=item["word_id"]):
                host = Host([raw])
                nav = navigation.Navigator(host, host.ADB_SERIAL, None)
                nav._scope, nav._item = nav._identity(item), item
                route = nav.skip(item)
                self.assertFalse(route["ready"])
                self.assertFalse(host.inputs)


if __name__ == "__main__":
    unittest.main()
