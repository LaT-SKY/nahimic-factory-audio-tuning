#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate the complete Nahimic-chain presets against the live plugin schemas."""

import glob
import json
import os
import sys
from collections import OrderedDict

EE = os.path.expanduser("~/.var/app/com.github.wwmm.easyeffects/data/easyeffects")
IRS = os.path.join(EE, "irs")
OUT = os.path.join(EE, "output")

CONV_KEYS = {"bypass", "input-gain", "output-gain", "kernel-name",
             "ir-width", "autogain", "dry", "wet", "sofa"}
EQ_TYPES = {"Off", "Bell", "Hi-pass", "Hi-shelf", "Lo-pass", "Lo-shelf",
            "Notch", "Resonance", "Allpass", "Bandpass",
            "Ladder-pass", "Ladder-rej"}
EQ_MODES = {"RLC (BT)", "RLC (MT)", "BWC (BT)", "BWC (MT)",
            "LRX (BT)", "LRX (MT)", "APO (DR)"}
SLOPES = {"x1", "x2", "x3", "x4"}

errs = []


def chk(cond, msg):
    print(("    OK   " if cond else "    FAIL ") + msg)
    if not cond:
        errs.append(msg)


def main():
    ref = json.load(open(os.path.join(OUT, "Music.json"), encoding="utf-8"),
                    object_pairs_hook=OrderedDict)

    targets = sorted(glob.glob(os.path.join(OUT, "*Nahimic*.json")))
    for p in targets:
        print("\n" + "=" * 70)
        print(os.path.basename(p))
        print("=" * 70)
        out = json.load(open(p, encoding="utf-8"),
                        object_pairs_hook=OrderedDict)["output"]

        cv = out.get("convolver#0")
        chk(cv is not None, "convolver#0 present")
        if cv:
            chk(set(cv.keys()) == CONV_KEYS, "convolver key set matches source")
            kn = cv["kernel-name"]
            chk(not kn.lower().endswith(".irs"), f"kernel-name {kn!r} has no extension")
            chk(os.path.isfile(os.path.join(IRS, kn + ".irs")),
                f"resolves to irs/{kn}.irs")
            chk(cv["autogain"] is False, "convolver.autogain = False")

        if "equalizer#0" in out:
            eq = out["equalizer#0"]
            nb = eq["num-bands"]
            chk(len(eq["left"]) == nb, f"EQ has {nb} bands")
            chk(eq["mode"] == "IIR", "EQ engine mode IIR")
            for ch in ("left", "right"):
                for bn, b in eq[ch].items():
                    chk(b["type"] in EQ_TYPES, f"{bn} type {b['type']!r} valid")
                    chk(b["mode"] in EQ_MODES, f"{bn} mode valid")
                    chk(b["slope"] in SLOPES, f"{bn} slope valid")
            chk(json.dumps(eq["left"], sort_keys=True) ==
                json.dumps(eq["right"], sort_keys=True), "L/R identical")
            desc = ", ".join(
                "{:.0f}Hz{:+.0f}dB({},Q{:.2f})".format(
                    b["frequency"], b["gain"], b["type"], b["q"])
                for b in eq["left"].values())
            print(f"    EQ bands: {desc}")
            print(f"    EQ preamp: {eq['input-gain']} dB")

        if "limiter#0" in out:
            chk(set(out["limiter#0"].keys()) ==
                set(ref["output"]["limiter#0"].keys()),
                "limiter key set matches reference")

        chk("autogain#0" not in out, "no autogain plugin in chain")
        order = out["plugins_order"]
        for pid in order:
            chk(pid in out, f"plugins_order entry {pid} exists")
        print(f"    chain: {' -> '.join(x.replace('#0', '') for x in order)}")

    print("\n" + "=" * 70)
    print("RESULT:", "ALL CHECKS PASSED" if not errs else f"{len(errs)} FAILURE(S)")
    for e in errs:
        print("  -", e)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
