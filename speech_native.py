"""Read-only TJP2 MP3 package index, scoped to the inspected build-11 format.

TJPPakReader uses PACK_COUNT[0]=400 and a 5000 WordID separator. The WAUF
index layout was cross-checked with the app's getPos/getLen for WordID 30056.
No file paths or byte offsets are accepted from HTTP clients.
"""
import struct

PACKAGE = "com.weaversmind.tjp2"
SUBJECT = "japanese-premium"
RULE_VERSION = "tjp2-build11-mp3-v1"
PACK_COUNT = 400
MAX_PACKAGE_BYTES = 256 * 1024 * 1024
MAX_AUDIO_BYTES = 4 * 1024 * 1024
DATA_DIRECTORY = "WMDATA/8400/WMTJP2/data"


def source_spec(item):
    word = item.get("word_id")
    if type(word) is not int or item.get("package") != PACKAGE or item.get("build") != 11:
        raise ValueError("지원하지 않는 앱 또는 WordID입니다.")
    if item.get("subject") != SUBJECT or item.get("res_id") != 0:
        raise ValueError("원본 음원 연결 규칙이 확인되지 않은 항목입니다.")
    ranges = {"word": (20000, 25000), "sentence_core": (30000, 35000),
              "sentence_variation": (30000, 35000), "conversation": (40000, 45000)}
    lower, upper = ranges.get(item.get("kind"), (0, 0))
    if not lower < word < upper or not item.get("target") or not item.get("text"):
        raise ValueError("이 학습 유형의 원본 음원 연결은 아직 지원하지 않습니다.")
    base = (word // 5000) * 5000
    number, slot = divmod(word - base, PACK_COUNT)
    return {"filename": f"sw{base + number + 1:06d}.pak", "slot": slot,
            "word_id": word, "rule_version": RULE_VERSION}


def support(item):
    try:
        source_spec(item)
        return True, ""
    except ValueError as exc:
        return False, str(exc)


def header_count(header):
    if len(header) != 20:
        raise ValueError("원본 음원 묶음의 헤더가 잘렸습니다.")
    magic, version, count, flag1, flag2 = struct.unpack(">4sIIII", header)
    if magic != b"WAUF" or version not in (5, 10) or flag1 or flag2:
        raise ValueError("검증되지 않은 원본 음원 묶음 형식입니다.")
    if not 1 <= count <= PACK_COUNT:
        raise ValueError("원본 음원 인덱스 개수가 올바르지 않습니다.")
    return count


def locate(index, slot, file_size):
    count = header_count(index[:20])
    table_end = 20 + count * 8
    if len(index) != table_end or type(slot) is not int or not 0 <= slot < count:
        raise ValueError("원본 음원 인덱스 범위를 확인할 수 없습니다.")
    if not table_end <= file_size <= MAX_PACKAGE_BYTES:
        raise ValueError("원본 음원 묶음의 크기가 올바르지 않습니다.")
    key = (~count) & 0xffffffff
    entries, occupied = [], []
    for number in range(count):
        pos, length = struct.unpack_from(">II", index, 20 + number * 8)
        pos, length = pos ^ key, length ^ key
        entries.append((pos, length))
        if (pos, length) == (0, 0):
            continue
        if pos < table_end or not 4 < length <= MAX_AUDIO_BYTES + 4 or pos + length > file_size:
            raise ValueError("원본 음원 위치 또는 길이가 파일 범위를 벗어났습니다.")
        occupied.append((pos, pos + length))
    occupied.sort()
    if any(right > next_left for (_, right), (next_left, _) in zip(occupied, occupied[1:])):
        raise ValueError("원본 음원 인덱스 영역이 겹칩니다.")
    pos, length = entries[slot]
    if not pos or not length:
        raise ValueError("이 WordID에 해당하는 원본 음원이 비어 있습니다.")
    return {"offset": pos + 4, "length": length - 4, "record_offset": pos,
            "record_length": length, "index_count": count}


def unpack_audio(record):
    if len(record) < 8:
        raise ValueError("원본 음원 데이터가 잘렸습니다.")
    duration = struct.unpack_from(">I", record)[0]
    audio = record[4:]
    if not 0 < duration <= 60000 or len(audio) > MAX_AUDIO_BYTES:
        raise ValueError("원본 음원의 길이가 지원 범위를 벗어났습니다.")
    if not (audio.startswith(b"ID3") or (audio[0] == 0xff and audio[1] & 0xe0 == 0xe0)):
        raise ValueError("원본 MP3 형식을 확인할 수 없습니다.")
    return audio, duration
