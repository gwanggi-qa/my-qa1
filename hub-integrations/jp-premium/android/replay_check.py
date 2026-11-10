"""Offline regression only. Does not import the device runner or publish Hub results."""
import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from build_reference import REGIONS
from premium_check import Unobservable, inspect_case, nodes


class Captures:
    def __init__(self, folder, screens):
        self.folder = folder
        self.screens = iter(screens)

    def stage(self, case):
        pass  # Navigation is outside this offline verdict check.

    def read(self):
        self.current = next(self.screens)
        return ET.parse(self.folder / (self.current + ".xml")).getroot()

    def tap(self, node):
        assert node.get("resource-id", "").endswith("/grammar_balloon_button")

    def adb(self, *args):
        assert args == ("exec-out", "screencap", "-p")
        return (self.folder / (self.current + ".png")).read_bytes()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", type=Path)
    ap.add_argument("captures", type=Path)
    args = ap.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    for i, (frame, _, _, prior) in enumerate(REGIONS, 1):
        tc = cases[f"JPP-{i:04d}"]
        data = Captures(args.captures, [prior, frame] if prior else [frame])
        try:
            inspect_case(data, tc)
        except AssertionError as e:
            assert i in (5, 6) and " / 좋은 아침" in str(e), (i, e)
        else:
            assert i not in (5, 6), i
    tc = cases["JPP-0002"]
    inspect_case(Captures(args.captures, ["093-no1-balloon", "094-no2-popup"]),
                 dict(tc, image=[0, 0, "different rendering"]))
    try:
        inspect_case(Captures(args.captures, ["093-no1-balloon", "094-no2-popup"]),
                     dict(tc, balloon=["not the expected lesson"]))
    except Unobservable:
        pass
    else:
        raise AssertionError("Popup title alone must not pass")
    print("Offline: 10 historical screen paths and insufficient visual evidence checked.")
