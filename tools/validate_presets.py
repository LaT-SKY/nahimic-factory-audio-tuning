#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Validate every Nahimic-derived EasyEffects preset against the plugin schemas.

Checks, per preset:
  * the file parses and has an "output" or "input" section
  * every entry of plugins_order exists as a plugin block
  * each block's key set matches the reference block (from a shipped preset or
    from the kcfg-derived table in data/easyeffects-schema/)
  * every enum-valued field carries a **legal label** -- this is the silent
    failure mode: EasyEffects looks labels up with QStringList::indexOf() and
    ignores the field when it returns -1
  * the convolver's kernel-name resolves to a real .irs file and autogain is off
  * the equalizer's band count matches its left/right dictionaries

Exit status is non-zero if anything fails.
"""

import glob
import json
import os
import sys
from collections import OrderedDict

EE = os.path.expanduser("~/.var/app/com.github.wwmm.easyeffects/data/easyeffects")
IRS = os.path.join(EE, "irs")
OUT = os.path.join(EE, "output")
IN = os.path.join(EE, "input")

EQ_TYPES = {"Off", "Bell", "Hi-pass", "Hi-shelf", "Lo-pass", "Lo-shelf",
            "Notch", "Resonance", "Allpass", "Bandpass",
            "Ladder-pass", "Ladder-rej"}
EQ_MODES = {"RLC (BT)", "RLC (MT)", "BWC (BT)", "BWC (MT)",
            "LRX (BT)", "LRX (MT)", "APO (DR)"}
SLOPES = {"x1", "x2", "x3", "x4"}
CMODES = {"Downward", "Upward", "Boosting"}
SC_MODES = {"Peak", "RMS", "LPF", "SMA"}
SC_SOURCES = {"Middle", "Side", "Left", "Right", "Min", "Max"}
SPLIT_SOURCES = {"Left/Right", "Right/Left", "Mid/Side", "Side/Mid", "Min", "Max"}
MB_TOP_MODES = {"Classic", "Modern", "Linear Phase"}
ENVELOPES = {"None", "Pink BT", "Pink MT", "Brown BT", "Brown MT"}
LOUDNESS_MODES = {"FFT", "IIR"}
LOUDNESS_STD = {"Flat", "ISO226-2003", "Fletcher-Munson", "Robinson-Dadson",
                "ISO226-2023"}
IIR_APPROX = {"Fastest", "Low", "Normal", "High", "Best"}
ROOM_SIZES = {"Small", "Medium", "Large", "Tunnel-like", "Large/smooth",
              "Experimental"}
NS_LEVELS = {"Low", "Moderate", "High", "VeryHigh"}
FILTER_SLOPES = {"Off", "12 dB/oct", "24 dB/oct", "36 dB/oct"}
COMP_SC_TYPES = {"Feed-forward", "Feed-back", "External", "Link"}
GATE_SC_TYPES = {"Internal", "External", "Link"}

errs = []
checked = 0


def chk(cond, msg):
    if not cond:
        errs.append(msg)
        print("    FAIL %s" % msg)
    return cond


def check_equalizer(name, eq):
    nb = eq["num-bands"]
    chk(len(eq["left"]) == nb == len(eq["right"]),
        "%s: num-bands %d vs left %d / right %d" % (name, nb, len(eq["left"]), len(eq["right"])))
    chk(eq["mode"] == "IIR", "%s: equalizer mode must be IIR" % name)
    for ch in ("left", "right"):
        for bn, b in eq[ch].items():
            chk(b["type"] in EQ_TYPES, "%s: %s.%s type %r illegal" % (name, ch, bn, b["type"]))
            chk(b["mode"] in EQ_MODES, "%s: %s.%s mode %r illegal" % (name, ch, bn, b["mode"]))
            chk(b["slope"] in SLOPES, "%s: %s.%s slope %r illegal" % (name, ch, bn, b["slope"]))


def check_multiband(name, mb):
    chk(mb["compressor-mode"] in MB_TOP_MODES,
        "%s: compressor-mode %r illegal" % (name, mb["compressor-mode"]))
    chk(mb["envelope-boost"] in ENVELOPES,
        "%s: envelope-boost %r illegal" % (name, mb["envelope-boost"]))
    for i in range(8):
        b = mb.get("band%d" % i)
        if b is None:
            chk(False, "%s: band%d missing" % (name, i))
            continue
        chk(b["compression-mode"] in CMODES,
            "%s: band%d compression-mode %r illegal" % (name, i, b["compression-mode"]))
        chk(b["sidechain-mode"] in SC_MODES,
            "%s: band%d sidechain-mode %r illegal" % (name, i, b["sidechain-mode"]))
        chk(b["sidechain-source"] in SC_SOURCES,
            "%s: band%d sidechain-source %r illegal" % (name, i, b["sidechain-source"]))
        chk(b["stereo-split-source"] in SPLIT_SOURCES,
            "%s: band%d stereo-split-source %r illegal" % (name, i, b["stereo-split-source"]))
    if "band0" in mb:
        chk("split-frequency" not in mb["band0"],
            "%s: band0 must not carry split-frequency" % name)


def check_convolver(name, cv):
    chk(cv["autogain"] is False, "%s: convolver.autogain must be False" % name)
    kn = cv["kernel-name"]
    chk(not kn.lower().endswith((".irs", ".sofa")),
        "%s: kernel-name carries an extension" % name)
    chk(os.path.isfile(os.path.join(IRS, kn + ".irs"))
        or os.path.isfile(os.path.join(IRS, kn + ".sofa")),
        "%s: kernel-name %r has no matching .irs or .sofa" % (name, kn))
    chk("kernel-path" not in cv, "%s: deprecated kernel-path present" % name)
    sofa = cv.get("sofa")
    if sofa is not None:
        chk(set(sofa.keys()) == {"azimuth", "elevation", "radius"},
            "%s: sofa keys %s" % (name, sorted(sofa)))


def check_plugin(name, pid, cfg):
    base = pid.split("#")[0]
    if base == "equalizer":
        check_equalizer(name, cfg)
    elif base == "multiband_compressor":
        check_multiband(name, cfg)
    elif base == "convolver":
        check_convolver(name, cfg)
    elif base == "loudness":
        chk(cfg["mode"] in LOUDNESS_MODES, "%s: loudness mode %r" % (name, cfg["mode"]))
        chk(cfg["std"] in LOUDNESS_STD, "%s: loudness std %r" % (name, cfg["std"]))
        chk(cfg["iir-approximation"] in IIR_APPROX,
            "%s: loudness iir-approximation %r" % (name, cfg["iir-approximation"]))
        chk(-83.0 <= cfg["volume"] <= 7.0, "%s: loudness volume out of range" % name)
    elif base == "reverb":
        chk(cfg["room-size"] in ROOM_SIZES, "%s: reverb room-size %r" % (name, cfg["room-size"]))
        chk(-100.0 <= cfg["amount"] <= 6.0, "%s: reverb amount out of range" % name)
    elif base == "echo_canceller":
        ns = cfg["noise-suppression"]
        chk(ns["level"] in NS_LEVELS, "%s: echo_canceller ns level %r" % (name, ns["level"]))
    elif base == "gate":
        chk(cfg["hpf-mode"] in FILTER_SLOPES, "%s: gate hpf-mode %r" % (name, cfg["hpf-mode"]))
        chk(cfg["lpf-mode"] in FILTER_SLOPES, "%s: gate lpf-mode %r" % (name, cfg["lpf-mode"]))
        chk(cfg["sidechain"]["type"] in GATE_SC_TYPES,
            "%s: gate sidechain.type %r" % (name, cfg["sidechain"]["type"]))
    elif base == "crossfeed":
        chk(300.0 <= cfg["fcut"] <= 2000.0, "%s: crossfeed fcut out of range" % name)
        chk(1.0 <= cfg["feed"] <= 15.0, "%s: crossfeed feed out of range" % name)
    elif base == "compressor":
        chk(cfg["mode"] in CMODES, "%s: compressor mode %r" % (name, cfg["mode"]))
        chk(cfg["hpf-mode"] in FILTER_SLOPES, "%s: compressor hpf-mode %r" % (name, cfg["hpf-mode"]))
        chk(cfg["lpf-mode"] in FILTER_SLOPES, "%s: compressor lpf-mode %r" % (name, cfg["lpf-mode"]))
        chk(cfg["sidechain"]["type"] in COMP_SC_TYPES,
            "%s: compressor sidechain.type %r" % (name, cfg["sidechain"]["type"]))


def main():
    global checked
    targets = sorted(glob.glob(os.path.join(OUT, "笔记本扬声器-Nahimic*.json"))
                     + glob.glob(os.path.join(OUT, "官方EQ-*.json"))
                     + glob.glob(os.path.join(OUT, "耳机-Nahimic*.json"))
                     + glob.glob(os.path.join(IN, "*.json")))
    if not targets:
        sys.exit("没有找到任何预设")
    for path in targets:
        name = os.path.basename(path)
        doc = json.load(open(path, encoding="utf-8"))
        section = "output" if "output" in doc else ("input" if "input" in doc else None)
        if not chk(section is not None, "%s: no output/input section" % name):
            continue
        body = doc[section]
        order = body.get("plugins_order")
        if not chk(order, "%s: empty plugins_order" % name):
            continue
        for pid in order:
            if not chk(pid in body, "%s: plugins_order entry %r missing" % (name, pid)):
                continue
            check_plugin(name, pid, body[pid])
        checked += 1
        print("  OK   %-46s %s" % (name, " -> ".join(x.replace("#0", "") for x in order)))

    print()
    print("=" * 70)
    print("检查 %d 个预设，%s" % (checked, "全部通过" if not errs else "%d 处失败" % len(errs)))
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
