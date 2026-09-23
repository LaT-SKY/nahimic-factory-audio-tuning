#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build the four Nahimic scenario presets (Music / Movie / Gaming / Communication)
for EasyEffects, from the factory XML archived in data/AudioProfiles/.

What comes from where
---------------------
* Scenario tone values      -> data/AudioProfiles/<Scenario>.nsx   (factory XML)
* Speaker correction FIR    -> the existing .irs files
* Chain shape               -> the validated third-round Music preset
                               (tools/build_nahimic_music.py + add_bass_compressor.py)

Two deliberate deviations from the factory chain, both documented:

1. **VoiceBoost is not modelled.**  Its real algorithm is still unconfirmed and
   the static-EQ approximation was proven harmful (it refills the 800 Hz notch).
   See dsp-architecture.md.

2. **Our low-frequency recovery band replaces the factory's <200 Hz compressor
   band.**  The FIR costs ~7.5 dB of midrange headroom, and Nahimic buys it back
   with DRC / upward compression.  We buy it back with a 20-400 Hz band
   compressor (band0).  The factory's own 2-band compressor (200 Hz split,
   release 150/50 ms, threshold -36 dB) is added as band1 *above* our split.

`kSet_Compressor*Rate = 0.5` is read as "pull the signal halfway toward the
threshold".  At ReferenceDB = -12 dB with ThresholdDB = -36 dB that is 12 dB of
reduction 24 dB over threshold -> slope 0.5 -> **ratio 2:1**, which is what the
presets use.

The `-响度` variants additionally insert EasyEffects' Loudness plugin
(LSP Loudness Compensator) as a stand-in for the factory's volume-adaptive
`kSet_BassEnhancement*` block.
"""

import json
import os
import xml.etree.ElementTree as ET
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
ARCHIVE = os.path.dirname(HERE)
PROFILES = os.path.join(ARCHIVE, "data", "AudioProfiles")
EE = os.path.expanduser("~/.var/app/com.github.wwmm.easyeffects/data/easyeffects")
EE_OUT = os.path.join(EE, "output")

SCENARIOS = [
    ("Music", "音乐", "X6DR57TK"),
    ("Movie", "电影", "X6DR57TK"),
    ("Gaming", "游戏", "X6DR57TK"),
    ("Communication", "通讯", "X6DR57TK"),
]
VARIANTS = ["X6DR57TK", "X6DR546K"]

CONV_OUTPUT_GAIN = -3.23      # FIR peak compensation, from the validated Music preset
MB_OUTPUT_GAIN = 3.06         # low-band compression headroom recovery


# --------------------------------------------------------------------------- #
# factory XML
# --------------------------------------------------------------------------- #
def read_profile(name):
    """Return {key: float} for kSet_* entries of one AudioProfiles file."""
    root = ET.parse(os.path.join(PROFILES, name + ".nsx")).getroot()
    out = {}
    for s in root.iter("Settings"):
        for child in s:
            out[child.tag] = float(child.get("Value"))
    return out


def get(p, key, default=0.0):
    return float(p.get(key, default))


# --------------------------------------------------------------------------- #
# plugin blocks
# --------------------------------------------------------------------------- #
def bass_enhancer(gain_db):
    """kSet_BassBoostGainDB -> Calf Bass Enhancer drive.

    Calibrated on the validated Music preset: +6 dB -> amount 9 / harmonics 9.
    The mapping is linear in dB, so 4 dB -> 6 and 2 dB -> 3.
    """
    d = gain_db / 6.0 * 9.0
    return OrderedDict([
        ("amount", round(d, 2)),
        ("blend", 2.0),
        ("bypass", False),
        ("floor", 30.0),
        ("floor-active", True),
        ("harmonics", round(d, 2)),
        ("input-gain", 0.0),
        ("output-gain", 0.0),
        ("scope", 250.0),
    ])


def convolver(variant):
    return OrderedDict([
        ("autogain", False),
        ("bypass", False),
        ("dry", -100.0),
        ("input-gain", 0.0),
        ("ir-width", 100),
        ("kernel-name", "Nahimic-Factory-" + variant),
        ("output-gain", CONV_OUTPUT_GAIN),
        ("sofa", OrderedDict([("azimuth", 0.0), ("elevation", 0.0), ("radius", 1.0)])),
        ("wet", 0.0),
    ])


def eq_band(freq, gain, btype="Hi-shelf", q=0.707):
    return OrderedDict([
        ("frequency", float(freq)), ("gain", float(gain)), ("mode", "APO (DR)"),
        ("mute", False), ("q", q), ("slope", "x1"), ("solo", False),
        ("type", btype), ("width", 4.0),
    ])


def equalizer(bands):
    left = OrderedDict(("band%d" % i, b) for i, b in enumerate(bands))
    right = OrderedDict(("band%d" % i, b) for i, b in enumerate(bands))
    return OrderedDict([
        ("balance", 0.0), ("bypass", False), ("input-gain", 0.0),
        ("left", left), ("mode", "IIR"), ("num-bands", len(bands)),
        ("output-gain", 0.0), ("pitch-left", 0.0), ("pitch-right", 0.0),
        ("right", right), ("split-channels", False),
    ])


def mb_band(split=None, enable=True, comp=True, thr=-12.0, ratio=2.5,
            attack=20.0, release=250.0, knee=-6.0, mode="Downward"):
    b = OrderedDict([
        ("attack-threshold", thr), ("attack-time", attack),
        ("boost-amount", 6.0), ("boost-threshold", -72.0),
        ("compression-mode", mode), ("compressor-enable", comp),
        ("knee", knee), ("makeup", 0.0), ("mute", False), ("ratio", ratio),
        ("release-threshold", -80.01), ("release-time", release),
        ("sidechain-custom-highcut-filter", False),
        ("sidechain-custom-lowcut-filter", False),
        ("sidechain-highcut-frequency", 500.0), ("sidechain-lookahead", 0.0),
        ("sidechain-lowcut-frequency", 10.0), ("sidechain-mode", "RMS"),
        ("sidechain-preamp", 0.0), ("sidechain-reactivity", 10.0),
        ("sidechain-source", "Middle"), ("sidechain-type", "Internal"),
        ("solo", False), ("stereo-split-source", "Left/Right"),
    ])
    if split is not None:
        b["enable-band"] = enable
        b["split-frequency"] = split
    return b


def multiband(factory_comp):
    """band0 = our low-frequency recovery; band1 = factory compressor (optional)."""
    bands = OrderedDict()
    bands["band0"] = mb_band(split=None, comp=True, thr=-12.0, ratio=2.5,
                             attack=20.0, release=250.0, knee=-6.0)
    for i in range(1, 8):
        if i == 1:
            if factory_comp:
                # factory Compressor2: threshold -36, release 50 ms, ratio 2:1
                bands["band%d" % i] = mb_band(split=400.0, comp=True, thr=-36.0,
                                              ratio=2.0, attack=20.0,
                                              release=50.0, knee=-6.0)
            else:
                bands["band%d" % i] = mb_band(split=400.0, comp=False, ratio=1.0)
        else:
            b = mb_band(split=[1000.0, 3000.0, 6000.0, 10000.0, 14000.0, 18000.0][i - 2],
                        comp=False, ratio=1.0)
            bands["band%d" % i] = b
    return OrderedDict([
        ("band0", bands["band0"]), ("band1", bands["band1"]),
        ("band2", bands["band2"]), ("band3", bands["band3"]),
        ("band4", bands["band4"]), ("band5", bands["band5"]),
        ("band6", bands["band6"]), ("band7", bands["band7"]),
        ("bypass", False), ("compressor-mode", "Modern"), ("dry", -80.01),
        ("envelope-boost", "None"), ("input-gain", 0.0), ("input-to-link", -80.01),
        ("input-to-sidechain", -80.01), ("link-to-input", -80.01),
        ("link-to-sidechain", -80.01), ("output-gain", MB_OUTPUT_GAIN),
        ("sidechain-to-input", -80.01), ("sidechain-to-link", -80.01),
        ("stereo-split", False), ("wet", 0.0),
    ])


def reverb(gain_db):
    """kSet_ReverbGainDB (dB) maps 1:1 onto the plugin's 'amount' (dB)."""
    return OrderedDict([
        ("amount", float(gain_db)), ("bass-cut", 300.0), ("bypass", False),
        ("decay-time", 1.5), ("diffusion", 0.5), ("dry", 0.0), ("hf-damp", 5000.0),
        ("input-gain", 0.0), ("output-gain", 0.0), ("predelay", 0.0),
        ("room-size", "Large"), ("treble-cut", 5000.0),
    ])


def loudness(volume_db=-12.0):
    """LSP Loudness Compensator, standing in for kSet_BassEnhancement*.

    'volume' is the assumed listening level relative to the reference: the lower
    it is set, the more low/high boost is applied -- i.e. the manual equivalent
    of Nahimic's automatic volume-adaptive bass.  -12 dB is a starting point,
    not a measurement.
    """
    return OrderedDict([
        ("bypass", False), ("clipping", False), ("clipping-range", 6.0),
        ("fft", 4096), ("iir-approximation", "Normal"), ("input-gain", 0.0),
        ("mode", "IIR"), ("output-gain", 0.0), ("std", "Fletcher-Munson"),
        ("volume", float(volume_db)),
    ])


def limiter():
    return OrderedDict([
        ("alr", False), ("alr-attack", 5.0), ("alr-knee", 0.0),
        ("alr-knee-smooth", -5.0), ("alr-release", 50.0), ("attack", 5.0),
        ("bypass", True), ("dithering", "None"), ("gain-boost", False),
        ("input-gain", 0.0), ("input-to-link", 0.0), ("input-to-sidechain", 0.0),
        ("link-to-input", 0.0), ("link-to-sidechain", 0.0), ("lookahead", 5.0),
        ("mode", "Herm Thin"), ("output-gain", 0.0),
        ("oversampling", "True Peak/16 bit"), ("release", 10.0),
        ("sidechain-preamp", 0.0), ("sidechain-to-input", 0.0),
        ("sidechain-to-link", 0.0), ("sidechain-type", "Internal"),
        ("stereo-link", 100.0), ("threshold", -1.0),
    ])


# --------------------------------------------------------------------------- #
def build(scenario, variant, with_loudness):
    p = read_profile(scenario)
    plugins = OrderedDict()
    order = []

    bb = get(p, "kSet_BassBoostGainDB")
    plugins["bass_enhancer#0"] = bass_enhancer(bb)
    order.append("bass_enhancer#0")

    plugins["convolver#0"] = convolver(variant)
    order.append("convolver#0")

    if get(p, "kSet_TrebleBoostState") >= 1 and get(p, "kSet_TrebleBoostGainDB") > 0:
        plugins["equalizer#0"] = equalizer(
            [eq_band(8000.0, get(p, "kSet_TrebleBoostGainDB"))])
        order.append("equalizer#0")

    if get(p, "kSet_ReverbState") >= 1:
        plugins["reverb#0"] = reverb(get(p, "kSet_ReverbGainDB", -10.0))
        order.append("reverb#0")

    plugins["multiband_compressor#0"] = multiband(get(p, "kSet_CompressorState") >= 1)
    order.append("multiband_compressor#0")

    if with_loudness:
        plugins["loudness#0"] = loudness()
        order.append("loudness#0")

    plugins["limiter#0"] = limiter()
    order.append("limiter#0")

    return OrderedDict([("output", OrderedDict(
        [("blocklist", [])] + list(plugins.items()) + [("plugins_order", order)]))])


def main():
    os.makedirs(EE_OUT, exist_ok=True)
    n = 0
    for scenario, cn, _ in SCENARIOS:
        p = read_profile(scenario)
        for variant in VARIANTS:
            for with_loudness in (False, True):
                preset = build(scenario, variant, with_loudness)
                suffix = "-响度" if with_loudness else ""
                name = "笔记本扬声器-Nahimic%s%s-%s.json" % (cn, suffix, variant)
                with open(os.path.join(EE_OUT, name), "w", encoding="utf-8") as fh:
                    json.dump(preset, fh, ensure_ascii=False, indent=4)
                n += 1
                chain = " -> ".join(x.replace("#0", "")
                                    for x in preset["output"]["plugins_order"])
                print("%-46s %s" % (name, chain))
        print("   %s: BassBoost %+.0f dB, Treble %s, Reverb %s, Compressor %s"
              % (scenario, get(p, "kSet_BassBoostGainDB"),
                 "%+.0f dB" % get(p, "kSet_TrebleBoostGainDB")
                 if get(p, "kSet_TrebleBoostState") else "off",
                 "on" if get(p, "kSet_ReverbState") else "off",
                 "on" if get(p, "kSet_CompressorState") else "off"))
    print("\n写出 %d 个预设 → %s" % (n, EE_OUT))


if __name__ == "__main__":
    main()
