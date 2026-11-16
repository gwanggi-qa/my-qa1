"""Speech sources for com.weaversmind.tjp2 build 11.

Nothing is executed at import time. Device arguments are serial strings (or
dicts with a serial/device_serial field). All device reads are explicitly
serial-bound, bounded ADB subprocesses; the user database is never opened.

catalog(device, root) -> {items, db_sha256, ...}
import_recordings(device, store_callback) -> {assets, errors, skipped}
    store_callback(pcm_bytes, filename, metadata) -> asset
generate_tts(text, outputPath) -> metadata including path and sha256
extract_native(device, item, outputPath) -> {available, reason, ...}

The recording callback receives only PCM. The exact companion TXT is retained
as base64 in metadata.source, because the main asset store accepts audio only.
No target-to-word-ID association is inferred from a recording's text.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import speech_native

PACKAGE = "com.weaversmind.tjp2"
SUBJECT = "japanese-premium"
BUILD = 11
DB_REMOTE = "databases/main_tjp.dat"
RECORDINGS_REMOTE = f"/sdcard/Android/data/{PACKAGE}/files/sr_debug"
LOCAL_TTS = Path(__file__).resolve().parent / "qa_results/tools/local-japanese-tts"
MAX_PCM_BYTES = 16000 * 2 * 60
MAX_DB_BYTES = 64 * 1024 * 1024
RECORD_NAME = re.compile(r"\d{8}_\d{6}_\d{3}\.(?:txt|pcm)\Z")


class MaterialsUnavailable(RuntimeError):
    """An explicit missing, incompatible, or unverifiable source."""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def device_serial(device):
    if isinstance(device, dict):
        device = device.get("serial") or device.get("device_serial")
    if not isinstance(device, str) or not re.fullmatch(r"[A-Za-z0-9_.:\[\]-]+", device):
        raise ValueError("An explicit ADB device serial is required")
    if device.startswith("-"):
        raise ValueError("Invalid device serial")
    return device


def _adb(device, *args, timeout=30):
    result = subprocess.run(
        ["adb", "-H", "127.0.0.1", "-P", "5037", "-s", device_serial(device), *args],
        capture_output=True, timeout=timeout, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise MaterialsUnavailable(
            "ADB read failed: " + result.stderr.decode("utf-8", "replace")[:500]
        )
    return result.stdout


def _check_build(device):
    details = _adb(device, "shell", "dumpsys", "package", PACKAGE).decode("utf-8", "replace")
    versions = set(re.findall(r"\bversionCode=(\d+)\b", details))
    if versions != {str(BUILD)}:
        raise MaterialsUnavailable("Expected com.weaversmind.tjp2 versionCode=11")


def _snapshot(device):
    _check_build(device)
    # Copying main alone while a WAL is active can silently omit committed rows.
    wal_check = f"if [ -s {DB_REMOTE}-wal ]; then printf active; fi"
    if _adb(device, "exec-out", "run-as", PACKAGE, "sh", "-c", wal_check).strip():
        raise MaterialsUnavailable("Content DB has an active WAL; no checkpoint or reset attempted")
    raw = _adb(device, "exec-out", "run-as", PACKAGE, "cat", DB_REMOTE)
    if not raw.startswith(b"SQLite format 3\0") or len(raw) > MAX_DB_BYTES:
        raise MaterialsUnavailable("Invalid content database snapshot")
    again = _adb(device, "exec-out", "run-as", PACKAGE, "cat", DB_REMOTE)
    if raw != again or _adb(
        device, "exec-out", "run-as", PACKAGE, "sh", "-c", wal_check
    ).strip():
        raise MaterialsUnavailable("Content database changed during extraction")
    return raw


def _read_catalog(path):
    """Parse a local snapshot with SQLite mode=ro, never a writable connection."""
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA quick_check").fetchall()[0][0] != "ok":
            raise MaterialsUnavailable("Content DB integrity check failed")
        rows = connection.execute("""
            SELECT s.*, w.WordText, w.WordMeaning, w.ResID, c.SRText,
                   c.CognitionText, t.MainWordID, e.EpisodeName
            FROM WordSetStudy AS s JOIN WordData AS w USING(WordID)
            LEFT JOIN WordDataCog AS c USING(WordID)
            LEFT JOIN WordDataTrans AS t USING(WordID)
            LEFT JOIN StudyEpisodeInfo AS e USING(LevelNum, EpisodeNum)
            ORDER BY s.LevelNum,s.EpisodeNum,s.StageNum,s.TypeNum,s.OrderNum,s.WordID
        """).fetchall()
        sessions = [dict(row) for row in connection.execute("SELECT * FROM SessionInfo")]
    finally:
        connection.close()
    session_map = {
        (s["LevelNum"], s["EpisodeNum"], s["StageNum"], s["StepNum"]): s
        for s in sessions
    }
    cores = {}
    for row in rows:
        if row["TypeNum"] == 6:
            unit = (row["LevelNum"], row["EpisodeNum"], row["StageNum"])
            cores.setdefault(unit, []).append(row)
    items = []
    for row in rows:
        unit = (row["LevelNum"], row["EpisodeNum"], row["StageNum"])
        word, typ, order = row["WordID"], row["TypeNum"], row["OrderNum"]
        step, kind, issue = None, "unmapped", ""
        if typ == 4:
            step, kind = 2, "word"
        elif typ == 5:
            step, kind = 4, "conversation"
        elif typ in (6, 7):
            kind = "sentence_core" if typ == 6 else "sentence_variation"
            candidates = sorted(cores.get(unit, []), key=lambda r: r["OrderNum"])
            main_id = word if typ == 6 else row["MainWordID"]
            matches = [i for i, core in enumerate(candidates) if core["WordID"] == main_id]
            # The observed build uses the two TypeNum=6 roots at orders 1 and 4.
            if [r["OrderNum"] for r in candidates] == [1, 4] and len(matches) == 1:
                index = matches[0]
                if order in (range(1, 4) if index == 0 else range(4, 7)):
                    step = (3, 9)[index]
            if step is None:
                issue = "Sentence session mapping is not established"
        else:
            kind, issue = "phonetic_unmapped", "Level-1 session mapping is not established"
        session = session_map.get((*unit, step))
        if session is None:
            issue = issue or "No verified SessionInfo mapping"
        raw_target = row["SRText"] or ""
        target = raw_target if raw_target.endswith(";") else raw_target + ";" if raw_target else ""
        identity = f"{SUBJECT}:L{unit[0]}:W{unit[1]}:S{unit[2]}:T{typ}:O{order}:WID{word}"
        items.append({
            "id": identity, "word_id": word, "text": row["WordText"] or "",
            "target": target, "expected_target": target, "sr_text": raw_target,
            "subject": SUBJECT, "package": PACKAGE, "build": BUILD,
            "level": unit[0], "week": unit[1], "stage": unit[2],
            "session": session["SessionOrd"] if session else None,
            "session_order": session["SessionOrd"] if session else None,
            "session_label": session["SessionType"] if session else None,
            "session_step": step, "kind": kind, "type_num": typ, "order": order,
            "meaning": row["WordMeaning"] or "", "week_title": row["EpisodeName"] or "",
            "cognition_text": row["CognitionText"] or "",
            "main_word_id": row["MainWordID"], "res_id": row["ResID"],
            "navigation_available": not bool(issue) and bool(target),
            "navigation_reason": issue or ("" if target else "Missing recognition target"),
        })
        available, reason = speech_native.support(items[-1])
        items[-1].update(native_available=available, native_reason=reason,
                         native_rule=speech_native.RULE_VERSION if available else None)
    if not items or len({i["id"] for i in items}) != len(items):
        raise MaterialsUnavailable("Empty or ambiguous content catalog")
    return items


def catalog(device, root):
    """Extract the connected build's content DB; never fall back to stale artifacts."""
    raw = _snapshot(device)
    sha = digest(raw)
    folder = Path(root).resolve() / "content-db"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"main_tjp-{sha}.dat"
    if path.exists():
        if digest(path.read_bytes()) != sha:
            raise MaterialsUnavailable("Existing content snapshot hash mismatch")
    else:
        # A unique temp avoids a half-written snapshot becoming a cache hit.
        with tempfile.NamedTemporaryFile(dir=folder, suffix=".dat", delete=False) as output:
            temporary = Path(output.name)
            output.write(raw)
        try:
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    items = _read_catalog(path)
    for item in items:
        item["db_sha256"] = sha
    return {"items": items, "db_sha256": sha, "db_path": str(path),
            "device": device_serial(device), "package": PACKAGE, "build": BUILD,
            "source": f"run-as {PACKAGE} {DB_REMOTE}", "read_only": True}


def recording_metadata(pcm, log, filename):
    """Validate a completed sr_debug pair without normalizing either byte stream."""
    if not pcm or len(pcm) % 2 or len(pcm) > MAX_PCM_BYTES:
        raise MaterialsUnavailable("Invalid or oversized PCM recording")
    if len(log) > 1024 * 1024:
        raise MaterialsUnavailable("Oversized recognition log")
    text = log.decode("utf-8-sig")
    fields = {}
    for line in text.splitlines():
        if "=" not in line or line.startswith("RecognitionFrame "):
            continue
        key, value = line.split("=", 1)
        if key in fields and fields[key] != value:
            raise MaterialsUnavailable("Conflicting recognition log fields")
        fields[key] = value
    target = fields.get("text")
    if not target or not re.fullmatch(r"-?\d+", fields.get("recogState", "")):
        raise MaterialsUnavailable("Missing target or final recogState; recording may be in progress")
    source = {
        "package": PACKAGE, "remote_path": f"{RECORDINGS_REMOTE}/{filename}",
        "pcm_sha256": digest(pcm), "txt_sha256": digest(log),
        "txt_filename": Path(filename).with_suffix(".txt").name,
        "txt_base64": base64.b64encode(log).decode("ascii"),
        "expected_target": target, "engine_fields": fields,
        "format_basis": "build11 sr_debug PCM16LE / 16000 Hz / mono",
        "original_unchanged": True,
    }
    return {
        "kind": "device_recording", "text": target, "target": target,
        "expected_target": target, "sample_rate": 16000, "channels": 1,
        "sample_width": 2, "sample_format": "s16le", "sha256": digest(pcm),
        "original_sha256": digest(pcm), "txt_sha256": digest(log),
        "samples": len(pcm) // 2, "duration_s": len(pcm) / 32000,
        "source": source, "transforms": [],
    }


def import_recordings(device, store_callback, *, limit=None, after=""):
    """Read stable PCM+TXT pairs; preserve partial-import failures explicitly."""
    if not callable(store_callback):
        raise TypeError("store_callback(data, filename, metadata) must be callable")
    if limit is not None and (type(limit) is not int or not 1 <= limit <= 25):
        raise ValueError("Recording import batch must contain 1 to 25 pairs")
    if not isinstance(after, str) or (after and (not RECORD_NAME.fullmatch(after) or not after.endswith('.txt'))):
        raise ValueError("Invalid recording import cursor")
    _check_build(device)
    names = _adb(device, "exec-out", "ls", "-1", RECORDINGS_REMOTE).decode("utf-8").splitlines()
    files = {name for name in names if RECORD_NAME.fullmatch(name)}
    logs = sorted(n for n in files if n.endswith(".txt"))
    if len(logs) > 2000:
        raise MaterialsUnavailable("Recording import exceeds the 2000-pair safety limit")
    result = {"assets": [], "errors": [], "skipped": []}
    for name in sorted(n for n in files if n.endswith(".pcm") and not after):
        if name[:-4] + ".txt" not in files:
            result["skipped"].append({"filename": name, "reason": "Missing companion TXT"})
    remaining = [name for name in logs if name > after]
    batch = remaining if limit is None else remaining[:limit]
    for name in batch:
        pcm_name = name[:-4] + ".pcm"
        if pcm_name not in files:
            result["skipped"].append({"filename": name, "reason": "Missing companion PCM"})
            continue
        try:
            log = _adb(device, "exec-out", "cat", f"{RECORDINGS_REMOTE}/{name}")
            pcm = _adb(device, "exec-out", "cat", f"{RECORDINGS_REMOTE}/{pcm_name}")
            if (log != _adb(device, "exec-out", "cat", f"{RECORDINGS_REMOTE}/{name}") or
                    pcm != _adb(device, "exec-out", "cat", f"{RECORDINGS_REMOTE}/{pcm_name}")):
                raise MaterialsUnavailable("Recording changed during import")
            metadata = recording_metadata(pcm, log, pcm_name)
            metadata["source"]["device"] = device_serial(device)
            result["assets"].append(store_callback(pcm, pcm_name, metadata))
        except (MaterialsUnavailable, OSError, ValueError, subprocess.SubprocessError) as exc:
            result["errors"].append({"filename": name, "reason": str(exc)})
    result.update(total=len(logs), processed=sum(name <= after for name in logs) + len(batch),
                  next_after=batch[-1] if len(batch) < len(remaining) else None)
    return result


def _write_new(path, data):
    path = Path(path).resolve()
    if path.suffix.lower() != ".pcm":
        raise ValueError("Output must be a .pcm path (raw signed PCM16LE)")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output:
        output.write(data)
    return str(path)


def _tts_worker(text):
    # NumPy asks platform.machine(); on Windows this can run the local "ver"
    # command. Cache that stdlib OS probe before denying child processes.
    import platform
    platform.uname()
    # This hook lives only in the short-lived TTS child, not the web service.
    def deny_external(event, args):
        if ((event.startswith("socket.") and event != "socket.gethostname") or
                event in {"subprocess.Popen", "os.system", "os.posix_spawn"}):
            raise MaterialsUnavailable("Local TTS forbids network and external processes")
    sys.addaudithook(deny_external)
    if not (LOCAL_TTS / "pyopenjtalk/__init__.py").is_file():
        raise MaterialsUnavailable("Installed local OpenJTalk is unavailable; downloads are disabled")
    sys.path.insert(0, str(LOCAL_TTS))
    import numpy as np
    from scipy.signal import resample_poly
    import pyopenjtalk

    voice = Path(os.fsdecode(pyopenjtalk.DEFAULT_HTS_VOICE))
    dictionary = Path(os.fsdecode(pyopenjtalk.OPEN_JTALK_DICT_DIR))
    if not voice.is_file() or not all(
        (dictionary / name).is_file() for name in ("sys.dic", "unk.dic", "matrix.bin", "char.bin")
    ):
        raise MaterialsUnavailable("Local voice/dictionary missing; downloads are disabled")
    waveform, rate = pyopenjtalk.tts(
        text, speed=1.0, half_tone=0.0,
        run_marine=False, use_tsqyomi=False, predict_nani=False,
    )
    waveform = np.asarray(waveform)
    rate = int(rate)
    if rate <= 0 or waveform.ndim != 1 or not waveform.size or not np.isfinite(waveform).all():
        raise MaterialsUnavailable("Invalid local TTS waveform")
    divisor = math.gcd(rate, 16000)
    converted = resample_poly(waveform, 16000 // divisor, rate // divisor)
    if not converted.size or not np.isfinite(converted).all():
        raise MaterialsUnavailable("Invalid resampled TTS waveform")
    # HTS returns floating-point sample amplitudes, not already-clipped PCM.
    # Normalize NEW synthesis to a documented peak before integer conversion.
    # This policy is never used by recording imports or native extraction.
    peak = float(np.max(np.abs(converted)))
    if peak == 0:
        raise MaterialsUnavailable("Local TTS produced silence")
    gain = (0.9 * 32767) / peak
    samples = np.rint(converted * gain)
    if np.max(samples) > 32767 or np.min(samples) < -32768:
        raise MaterialsUnavailable("TTS calibration exceeded PCM16 range")
    pcm = samples.astype("<i2").tobytes()
    if not pcm or len(pcm) > MAX_PCM_BYTES:
        raise MaterialsUnavailable("TTS output exceeds 60 seconds")
    return {
        "pcm_base64": base64.b64encode(pcm).decode("ascii"),
        "engine": "pyopenjtalk", "engine_version": pyopenjtalk.__version__,
        "voice_sha256": digest(voice.read_bytes()), "native_sample_rate": rate,
        "sha256": digest(pcm), "sample_rate": 16000, "channels": 1, "sample_width": 2,
        "samples": len(pcm) // 2, "duration_s": len(pcm) / 32000,
        "speed": 1.0, "half_tone": 0.0, "network": "denied in isolated worker",
        "speaker_used": False, "kind": "local_tts", "text": text,
        "gain_policy": "new_synthesis_peak_normalization", "target_peak_fraction": 0.9,
        "synthesis_gain": gain, "synthesis_gain_db": 20 * math.log10(gain),
        "pre_gain_peak": peak, "native_peak": float(np.max(np.abs(waveform))),
        "pre_gain_out_of_pcm16_samples": int(np.sum((converted > 32767) | (converted < -32768))),
        "clipping_samples": 0,
        "transforms": ["resample synthesized float waveform to 16000 Hz",
                       "normalize new TTS to 0.9 of PCM16 positive full scale before quantization"],
    }


def generate_tts(text, outputPath):
    """Generate new PCM only; no playback, downloads, or changes to an existing file."""
    if not isinstance(text, str) or not text.strip() or len(text) > 2000 or "\0" in text:
        raise ValueError("TTS text must contain 1..2000 characters without NUL")
    path = Path(outputPath).resolve()
    if path.exists() or path.suffix.lower() != ".pcm":
        raise ValueError("TTS output must be a new .pcm file")
    result = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", str(Path(__file__).resolve()), "--tts-worker"],
        input=json.dumps({"text": text}).encode("utf-8"), capture_output=True,
        timeout=120, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise MaterialsUnavailable("Local TTS unavailable: " + result.stderr.decode("utf-8", "replace")[-1200:])
    metadata = json.loads(result.stdout)
    pcm = base64.b64decode(metadata.pop("pcm_base64"), validate=True)
    if digest(pcm) != metadata["sha256"] or not pcm or len(pcm) % 2 or len(pcm) > MAX_PCM_BYTES:
        raise MaterialsUnavailable("Invalid TTS worker output")
    metadata["path"] = _write_new(path, pcm)
    return metadata


generate = generate_tts

def _native_stat(device, path):
    # Paths come only from fixed directories, a validated mount ID and our rule.
    command = f"if [ -f '{path}' ]; then stat -c '%s:%Y:%i' '{path}'; else printf missing; fi"
    value = _adb(device, "exec-out", "sh", "-c", command).decode("ascii").strip()
    if value == "missing":
        return None
    if not re.fullmatch(r"\d+:\d+:\d+", value):
        raise MaterialsUnavailable("원본 음원 파일 정보를 읽지 못했습니다.")
    return value


def _native_path(device, filename):
    primary = f"/storage/emulated/0/{speech_native.DATA_DIRECTORY}/{filename}"
    stamp = _native_stat(device, primary)
    if stamp is not None:
        return primary, stamp
    mounts = _adb(device, "exec-out", "ls", "-1", "/storage").decode("utf-8").splitlines()
    found = []
    for mount in sorted(set(mounts)):
        if not re.fullmatch(r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}", mount):
            continue
        path = f"/storage/{mount}/{speech_native.DATA_DIRECTORY}/{filename}"
        stamp = _native_stat(device, path)
        if stamp is not None:
            found.append((path, stamp))
    if not found:
        raise MaterialsUnavailable("기기의 내부 저장소와 SD 카드에 해당 원본 음원 파일이 없습니다.")
    if len(found) != 1:
        raise MaterialsUnavailable("여러 SD 카드에서 원본 음원이 발견되어 앱의 사용 위치 확인이 필요합니다.")
    return found[0]


def _native_bytes(device, path, offset, length):
    # exec-out merges remote stderr; dd statistics must not enter the audio bytes.
    data = _adb(device, "exec-out", "dd", f"if={path}", "bs=1", f"skip={offset}",
                f"count={length}", "status=none")
    if len(data) != length:
        raise MaterialsUnavailable("원본 음원 파일이 잘렸거나 추출 중 변경되었습니다.")
    return data


def _native_source(device, item):
    spec = speech_native.source_spec(item)
    _check_build(device)
    path, stamp = _native_path(device, spec["filename"])
    size = int(stamp.split(":", 1)[0])
    if size > speech_native.MAX_PACKAGE_BYTES:
        raise MaterialsUnavailable("원본 음원 묶음 크기가 지원 범위를 벗어났습니다.")
    header = _native_bytes(device, path, 0, 20)
    count = speech_native.header_count(header)
    index = _native_bytes(device, path, 0, 20 + count * 8)
    if index[:20] != header:
        raise MaterialsUnavailable("원본 음원 인덱스가 추출 중 변경되었습니다.")
    entry = speech_native.locate(index, spec["slot"], size)
    record = _native_bytes(device, path, entry["record_offset"], entry["record_length"])
    if (record != _native_bytes(device, path, entry["record_offset"], entry["record_length"])
            or index != _native_bytes(device, path, 0, len(index))
            or stamp != _native_stat(device, path)):
        raise MaterialsUnavailable("원본 음원이 추출 중 변경되었습니다. 다시 조회하세요.")
    audio, duration = speech_native.unpack_audio(record)
    return audio, {**spec, **entry, "path": path, "package_bytes": size,
                   "index_sha256": digest(index), "sha256": digest(audio),
                   "duration_ms": duration, "db_sha256": item.get("db_sha256"),
                   "package": PACKAGE, "build": BUILD, "device": device_serial(device),
                   "target": item["target"], "read_only": True}


def extract_native(device, item, outputPath):
    """Resolve current-device package entries; no playback or network fallback."""
    try:
        speech_native.source_spec(item)
        import av
        source, mapping = _native_source(device, item)
        chunks = []
        byte_count = 0
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        with av.open(io.BytesIO(source), format="mp3") as container:
            for frame in container.decode(audio=0):
                for converted in resampler.resample(frame):
                    chunk = converted.to_ndarray().astype("<i2").tobytes()
                    byte_count += len(chunk)
                    if byte_count > MAX_PCM_BYTES:
                        raise MaterialsUnavailable("원본 음원이 60초 제한을 초과했습니다.")
                    chunks.append(chunk)
            for converted in resampler.resample(None):
                chunks.append(converted.to_ndarray().astype("<i2").tobytes())
        pcm = b"".join(chunks)
        if not pcm or len(pcm) % 2 or len(pcm) > MAX_PCM_BYTES:
            raise MaterialsUnavailable("Invalid decoded native audio")
        return {
            "available": True, "reason": "", "path": _write_new(outputPath, pcm),
            "sha256": digest(pcm), "kind": "native", "word_id": item["word_id"],
            "sample_rate": 16000, "channels": 1, "sample_width": 2,
            "expected_target": mapping["target"], "source": dict(mapping),
            "transforms": ["decode MP3 and resample to PCM16LE 16kHz mono"],
        }
    except (ImportError, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        return {"available": False, "reason": str(exc)}


if __name__ == "__main__":
    if sys.argv[1:] != ["--tts-worker"]:
        raise SystemExit("This module has no device-operation CLI")
    payload = json.loads(sys.stdin.buffer.read(16384))
    print(json.dumps(_tts_worker(payload["text"]), ensure_ascii=True))
