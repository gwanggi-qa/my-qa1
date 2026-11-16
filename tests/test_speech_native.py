"""Synthetic package-index checks; never reads the real device or private audio."""
import struct
import unittest
from unittest.mock import patch

import speech_native as native
import speech_materials as materials


ITEM = {"word_id": 20001, "text": "Synthetic word", "target": "synthetic;",
        "subject": native.SUBJECT, "package": native.PACKAGE, "build": 11,
        "res_id": 0, "kind": "word", "db_sha256": "synthetic-db"}
PRIMARY = "/storage/emulated/0/" + native.DATA_DIRECTORY + "/sw020001.pak"
EXTERNAL = "/storage/1234-ABCD/" + native.DATA_DIRECTORY + "/sw020001.pak"


def package(version=5):
    count = 400
    key = (~count) & 0xffffffff
    index = bytearray(struct.pack(">4sIIII", b"WAUF", version, count, 0, 0))
    index += struct.pack(">II", key, key) * count
    data = bytearray()
    for slot in (1, 2, 3, 4, 399):
        payload = struct.pack(">I", 1400) + b"ID3synthetic-mp3-" + bytes([slot % 256])
        struct.pack_into(">II", index, 20 + slot * 8, (len(index) + len(data)) ^ key, len(payload) ^ key)
        data += payload
    return bytes(index + data)


class FakeDevice:
    def __init__(self, files):
        self.files, self.calls = files, []

    def __call__(self, device, *args, **kwargs):
        assert device == "synthetic-device"
        self.calls.append(args)
        if args == ("exec-out", "ls", "-1", "/storage"):
            return b"emulated\nself\n1234-ABCD\n5678-EFAB\nmalicious;path\n"
        if args[:3] == ("exec-out", "sh", "-c"):
            path = args[3].split("'")[1]
            return (str(len(self.files[path])) + ":100:12").encode() if path in self.files else b"missing"
        if args[:2] == ("exec-out", "dd"):
            assert "status=none" in args
            params = dict(a.split("=", 1) for a in args[2:])
            start, size = int(params["skip"]), int(params["count"])
            return self.files[params["if"]][start:start + size]
        raise AssertionError("Unexpected device operation: " + repr(args))


class NativeIndexTests(unittest.TestCase):
    def test_wordid_rule_covers_boundaries_and_types_without_fixed_offsets(self):
        for word, kind, filename, slot in (
            (20001, "word", "sw020001.pak", 1), (20399, "word", "sw020001.pak", 399),
            (20400, "word", "sw020002.pak", 0), (20954, "word", "sw020003.pak", 154),
            (30056, "sentence_variation", "sw030001.pak", 56),
            (30718, "sentence_core", "sw030002.pak", 318),
            (40724, "conversation", "sw040002.pak", 324)):
            with self.subTest(word=word):
                spec = native.source_spec({**ITEM, "word_id": word, "kind": kind})
                self.assertEqual((spec["filename"], spec["slot"]), (filename, slot))

    def test_wrong_scope_missing_target_and_unverified_types_are_not_supported(self):
        for changes in ({"word_id": True}, {"word_id": 20001.0}, {"word_id": "20001"},
                        {"word_id": 10001}, {"package": "other"}, {"build": 12},
                        {"subject": "other"}, {"res_id": 1}, {"target": ""}, {"text": ""},
                        {"kind": "phonetic_unmapped"}, {"kind": "conversation"}):
            with self.subTest(changes=changes):
                self.assertFalse(native.support({**ITEM, **changes})[0])

    def test_v5_v10_index_and_duration_not_part_of_mp3(self):
        for version in (5, 10):
            raw = package(version)
            entry = native.locate(raw[:3220], 1, len(raw))
            audio, duration = native.unpack_audio(raw[entry["record_offset"]:entry["offset"] + entry["length"]])
            self.assertEqual(audio, b"ID3synthetic-mp3-\x01")
            self.assertEqual(duration, 1400)
            self.assertEqual(entry["offset"], 3224)

    def test_invalid_headers_missing_and_truncated_entries(self):
        raw = package()
        for header in (raw[:19], b"FAIL" + raw[4:20], struct.pack(">4sIIII", b"WAUF", 99, 400, 0, 0),
                       struct.pack(">4sIIII", b"WAUF", 5, 401, 0, 0),
                       struct.pack(">4sIIII", b"WAUF", 5, 400, 1, 0)):
            with self.subTest(header=header), self.assertRaises(ValueError):
                native.header_count(header)
        for index, slot, size in ((raw[:3220], 0, len(raw)), (raw[:3219], 1, len(raw)),
                                  (raw[:3220], 400, len(raw)), (raw[:3220], 1, 3221)):
            with self.subTest(slot=slot, size=size), self.assertRaises(ValueError):
                native.locate(index, slot, size)

    def test_out_of_bounds_oversize_and_overlapping_entries_rejected(self):
        raw = package()
        for pos, length in ((1, 20), (3220, 0), (3220, 99999999), (3220, 50)):
            index = bytearray(raw[:3220])
            key = (~400) & 0xffffffff
            struct.pack_into(">II", index, 28, pos ^ key, length ^ key)
            with self.subTest(pos=pos, length=length), self.assertRaises(ValueError):
                native.locate(index, 1, len(raw))

    def test_bad_duration_and_payload_rejected(self):
        for record in (b"", struct.pack(">I", 0) + b"ID3data", struct.pack(">I", 61000) + b"ID3data",
                       struct.pack(">I", 1000) + b"not-audio"):
            with self.subTest(record=record), self.assertRaises(ValueError):
                native.unpack_audio(record)

    def test_sd_fallback_returns_exact_audio_provenance_and_stable_hash(self):
        fake = FakeDevice({EXTERNAL: package()})
        with patch.object(materials, "_adb", side_effect=fake), patch.object(materials, "_check_build"):
            audio, source = materials._native_source("synthetic-device", ITEM)
        self.assertEqual(audio, b"ID3synthetic-mp3-\x01")
        self.assertEqual(source["path"], EXTERNAL)
        self.assertEqual(source["sha256"], materials.digest(audio))
        self.assertEqual(source["db_sha256"], "synthetic-db")
        self.assertEqual(source["rule_version"], native.RULE_VERSION)
        self.assertTrue(source["read_only"])
        self.assertEqual(sum(c[:2] == ("exec-out", "dd") for c in fake.calls), 5)

    def test_primary_preferred_and_missing_or_ambiguous_cards_rejected(self):
        for files, expect in (({PRIMARY: package(), EXTERNAL: package()}, PRIMARY),
                              ({EXTERNAL: package()}, EXTERNAL), ({}, None),
                              ({EXTERNAL: package(), EXTERNAL.replace("1234-ABCD", "5678-EFAB"): package()}, None)):
            fake = FakeDevice(files)
            with self.subTest(paths=list(files)), patch.object(materials, "_adb", side_effect=fake):
                if expect:
                    self.assertEqual(materials._native_path("synthetic-device", "sw020001.pak")[0], expect)
                else:
                    with self.assertRaises(materials.MaterialsUnavailable):
                        materials._native_path("synthetic-device", "sw020001.pak")

    def test_short_read_is_rejected_not_sliced_or_padded(self):
        for data in (b"ab", b"abcd1+0 records out\n"):
            with patch.object(materials, "_adb", return_value=data), self.assertRaises(materials.MaterialsUnavailable):
                materials._native_bytes("synthetic-device", PRIMARY, 0, 4)

    def test_file_change_during_extraction_is_rejected(self):
        raw = package()
        for change in ("record", "index", "stamp"):
            fake = FakeDevice({PRIMARY: raw})
            counts = {"record": 0, "index": 0, "stamp": 0}

            def read(device, *args):
                data = fake(device, *args)
                if args[:3] == ("exec-out", "sh", "-c"):
                    kind = "stamp"
                elif "count=3220" in args:
                    kind = "index"
                elif "skip=3220" in args:
                    kind = "record"
                else:
                    return data
                counts[kind] += 1
                return data[:-1] + bytes([data[-1] ^ 1]) if kind == change and counts[kind] == 2 else data

            with self.subTest(change=change), patch.object(materials, "_adb", side_effect=read), \
                    patch.object(materials, "_check_build"), self.assertRaises(materials.MaterialsUnavailable):
                materials._native_source("synthetic-device", ITEM)


if __name__ == "__main__":
    unittest.main()
