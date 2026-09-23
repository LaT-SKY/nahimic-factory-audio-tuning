#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Convert Nahimic's built-in 10-band graphic EQ presets into EasyEffects presets.

Data source
-----------
Nahimic's UWP app ships a 10-band graphic EQ (UI sliders) that is *separate* from
the factory XML tone controls.  The presets live in the user's package data:

    .../Packages/A-Volute.Nahimic_*/LocalState/Settings/v1/EQPresets/
        <Scenario>/OriginalSettings/<Name>.json     <- factory defaults
        <Scenario>/<Name>.json                      <- current values

Each file is just ten numbers, e.g.:
    {"EQ32HzGainDB": 2, "EQ64HzGainDB": 7, ..., "EQ16kHzGainDB": 1}

EasyEffects has no graphic-EQ mode (its Equalizer is the LSP parametric EQ), so
each band becomes a peaking (Bell) filter at the same centre frequency with
Q = 1.41 -- the standard value for one-octave-spaced graphic EQ bands.

The preamp is not guessed: the combined magnitude response of the ten biquads is
computed with the RBJ cookbook formulas and the preamp is set to the negative of
its peak, so the preset cannot clip.
"""

import glob
import json
import os
from collections import OrderedDict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ARCHIVE = os.path.dirname(HERE)
SRC = os.path.join(ARCHIVE, "data", "ui-eq-presets")
EE_OUT = os.path.expanduser(
    "~/.var/app/com.github.wwmm.easyeffects/data/easyeffects/output")

# Nahimic slider frequencies -> Hz
BANDS = [
    ("EQ32HzGainDB", 32.0),
    ("EQ64HzGainDB", 64.0),
    ("EQ125HzGainDB", 125.0),
    ("EQ250HzGainDB", 250.0),
    ("EQ500HzGainDB", 500.0),
    ("EQ1kHzGainDB", 1000.0),
    ("EQ2kHzGainDB", 2000.0),
    ("EQ4kHzGainDB", 4000.0),
    ("EQ8kHzGainDB", 8000.0),
    ("EQ16kHzGainDB", 16000.0),
]

Q = 1.41          # one-octave graphic-EQ band spacing
FS = 48000.0
SCENARIO_CN = {"Music": "音乐", "Movie": "电影",
               "Gaming": "游戏", "Communication": "通讯"}


def peaking_response(gains, freqs=None, q=Q, fs=FS, n=1 << 15):
    """Combined magnitude response (dB) of cascaded RBJ peaking filters."""
    f = np.linspace(20.0, fs / 2.0, n) if freqs is None else freqs
    H = np.ones(len(f), dtype=complex)
    for g_db, f0 in zip(gains, (b[1] for b in BANDS)):
        A = 10.0 ** (g_db / 40.0)
        w0 = 2.0 * np.pi * f0 / fs
        alpha = np.sin(w0) / (2.0 * q)
        cosw0 = np.cos(w0)
        b0 = 1 + alpha * A
        b1 = -2 * cosw0
        b2 = 1 - alpha * A
        a0 = 1 + alpha / A
        a1 = -2 * cosw0
        a2 = 1 - alpha / A
        z = np.exp(-1j * 2.0 * np.pi * f / fs)
        H *= (b0 + b1 * z + b2 * z ** 2) / (a0 + a1 * z + a2 * z ** 2)
    return f, 20.0 * np.log10(np.abs(H))


def band_block(freq, gain, q=Q):
    return OrderedDict([
        ("frequency", freq),
        ("gain", float(gain)),
        ("mode", "APO (DR)"),
        ("mute", False),
        ("q", q),
        ("slope", "x1"),
        ("solo", False),
        ("type", "Bell"),
        ("width", 4.0),
    ])


def build_preset(gains, preamp):
    left = OrderedDict(
        ("band%d" % i, band_block(b[1], g)) for i, (b, g) in enumerate(zip(BANDS, gains)))
    right = OrderedDict(
        ("band%d" % i, band_block(b[1], g)) for i, (b, g) in enumerate(zip(BANDS, gains)))
    eq = OrderedDict([
        ("balance", 0.0),
        ("bypass", False),
        ("input-gain", round(preamp, 2)),
        ("left", left),
        ("mode", "IIR"),
        ("num-bands", len(BANDS)),
        ("output-gain", 0.0),
        ("pitch-left", 0.0),
        ("pitch-right", 0.0),
        ("right", right),
        ("split-channels", False),
    ])
    return OrderedDict([("output", OrderedDict([
        ("blocklist", []),
        ("equalizer#0", eq),
        ("plugins_order", ["equalizer#0"]),
    ]))])


def main():
    os.makedirs(EE_OUT, exist_ok=True)
    written, skipped = 0, 0
    print("%-34s %-28s %8s %8s" % ("文件", "预设名", "峰值dB", "preamp"))
    print("-" * 84)

    for path in sorted(glob.glob(os.path.join(SRC, "*", "OriginalSettings", "*.json"))):
        scenario = os.path.basename(os.path.dirname(os.path.dirname(path)))
        name = os.path.splitext(os.path.basename(path))[0]
        data = json.load(open(path, encoding="utf-8-sig"))
        gains = [float(data.get(k, 0.0)) for k, _ in BANDS]

        if not any(gains):
            skipped += 1                      # "Custom" = flat, nothing to do
            continue

        _, resp = peaking_response(gains)
        peak = float(resp.max())
        preamp = -max(0.0, peak)

        preset = build_preset(gains, preamp)
        out_name = "官方EQ-%s-%s.json" % (SCENARIO_CN.get(scenario, scenario), name)
        with open(os.path.join(EE_OUT, out_name), "w", encoding="utf-8") as fh:
            json.dump(preset, fh, ensure_ascii=False, indent=4)
        written += 1
        print("%-34s %-28s %8.2f %8.2f" % (
            os.path.relpath(path, SRC), out_name, peak, preamp))

    print("-" * 84)
    print("写出 %d 个预设，跳过 %d 个（Custom = 全平）" % (written, skipped))
    print("输出目录: %s" % EE_OUT)


if __name__ == "__main__":
    main()
