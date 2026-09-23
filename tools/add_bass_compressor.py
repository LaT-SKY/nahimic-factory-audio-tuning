#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Add a bass-only multiband compressor to the Nahimic Music preset.

The problem it solves
---------------------
The factory correction boosts 80-400 Hz by ~10 dB. Since the digital signal must
stay below 0 dBFS, the midrange has to sit ~7.5 dB lower to make room. That is
why the corrected preset sounds quieter. Nahimic solves the same problem with
kSet_DRC* (threshold -70 dB) plus Ape::SBP::MBCompressor.

Approach
--------
Compress ONLY band0 (20-400 Hz), leave every other band transparent. The bass
peaks come down, which frees headroom that the whole chain can then use. The
midrange and treble keep their dynamics.

Gain staging is computed, not guessed: the bass peak after compression and the
highest uncompressed level are both evaluated, and output-gain is set so the
louder of the two lands just below full scale.

Schema notes (from multiband_compressor_preset.cpp)
---------------------------------------------------
* 8 bands, "band0".."band7". band0 has NO enable-band / split-frequency.
* These five keys serialise as LABEL STRINGS, not integers:
    compression-mode   Downward | Upward | Boosting
    sidechain-type     Internal | External | Link
    sidechain-mode     Peak | RMS | LPF | SMA
    sidechain-source   Middle | Side | Left | Right | Min | Max
    stereo-split-source  Left/Right | Right/Left | Mid/Side | Side/Mid | Min | Max
  All other band keys are numbers or booleans.
"""

import json
import os
import wave
from collections import OrderedDict

import numpy as np

SR = 48000
EE = os.path.expanduser("~/.var/app/com.github.wwmm.easyeffects/data/easyeffects")
OUT = os.path.join(EE, "output")
IRS = os.path.join(EE, "irs")

# compressor settings for the bass band
THRESHOLD_DB = -12.0
RATIO = 2.5
ATTACK_MS = 20.0
RELEASE_MS = 250.0
KNEE_DB = -6.0

TARGET_PEAK_DB = -1.0
SPLITS = [400.0, 1000.0, 3000.0, 6000.0, 10000.0, 14000.0, 18000.0]  # band1..band7


def read_ir(tag):
    with wave.open(os.path.join(IRS, f"Nahimic-Factory-{tag}.irs")) as w:
        raw = w.readframes(w.getnframes())
    a = np.frombuffer(raw, dtype="<i4").astype(np.float64) / (2 ** 31 - 1)
    return a[0::2]


def band_keys(n):
    d = OrderedDict()
    if n > 0:
        d["enable-band"] = True
        d["split-frequency"] = SPLITS[n - 1]
    d["compressor-enable"] = (n == 0)
    d["mute"] = False
    d["solo"] = False
    d["attack-threshold"] = THRESHOLD_DB
    d["attack-time"] = ATTACK_MS
    d["release-threshold"] = -80.01
    d["release-time"] = RELEASE_MS
    d["ratio"] = RATIO if n == 0 else 1.0
    d["knee"] = KNEE_DB
    d["makeup"] = 0.0
    d["compression-mode"] = "Downward"
    d["sidechain-type"] = "Internal"
    d["sidechain-mode"] = "RMS"
    d["sidechain-source"] = "Middle"
    d["sidechain-lookahead"] = 0.0
    d["sidechain-reactivity"] = 10.0
    d["sidechain-preamp"] = 0.0
    d["sidechain-custom-lowcut-filter"] = False
    d["sidechain-custom-highcut-filter"] = False
    d["sidechain-lowcut-frequency"] = 10.0
    d["sidechain-highcut-frequency"] = 500.0
    d["boost-threshold"] = -72.0
    d["boost-amount"] = 6.0
    d["stereo-split-source"] = "Left/Right"
    return d


def main():
    f = np.logspace(np.log10(20), np.log10(20000), 4000)
    ff = np.fft.rfftfreq(32768, 1 / SR)

    for tag in ("X6DR57TK", "X6DR546K"):
        path = os.path.join(OUT, f"笔记本扬声器-NahimicMusic-{tag}.json")
        doc = json.load(open(path, encoding="utf-8"),
                        object_pairs_hook=OrderedDict)
        out = doc["output"]

        conv_gain = out["convolver#0"]["output-gain"]
        fir = np.interp(f, ff, np.abs(np.fft.rfft(
            read_ir(tag) * 1.0, 32768)))
        # response of the chain as currently configured
        resp_db = 20 * np.log10(fir + 1e-12) + conv_gain

        bass = f <= 400.0
        other = ~bass
        p_bass = resp_db[bass].max()
        p_other = resp_db[other].max()

        # band0 through a downward compressor
        def comp(x):
            if x <= THRESHOLD_DB:
                return x
            return THRESHOLD_DB + (x - THRESHOLD_DB) / RATIO

        p_bass_after = comp(p_bass)
        new_peak = max(p_bass_after, p_other)
        out_gain = round(TARGET_PEAK_DB - new_peak, 2)

        mb = OrderedDict()
        mb["bypass"] = False
        mb["input-gain"] = 0.0
        mb["output-gain"] = out_gain
        mb["dry"] = -80.01
        mb["wet"] = 0.0
        mb["compressor-mode"] = "Modern"
        mb["envelope-boost"] = "None"
        mb["stereo-split"] = False
        mb["input-to-sidechain"] = -80.01
        mb["input-to-link"] = -80.01
        mb["sidechain-to-input"] = -80.01
        mb["sidechain-to-link"] = -80.01
        mb["link-to-input"] = -80.01
        mb["link-to-sidechain"] = -80.01
        for n in range(8):
            mb[f"band{n}"] = band_keys(n)

        # rebuild chain: ... -> multiband_compressor -> limiter
        new = OrderedDict()
        for k in ("bass_enhancer#0", "convolver#0", "equalizer#0"):
            new[k] = out[k]
        new["multiband_compressor#0"] = mb
        new["limiter#0"] = out["limiter#0"]
        new["plugins_order"] = ["bass_enhancer#0", "convolver#0", "equalizer#0",
                                "multiband_compressor#0", "limiter#0"]
        new["blocklist"] = []

        json.dump(OrderedDict([("output", new)]),
                  open(path, "w", encoding="utf-8"),
                  indent=4, ensure_ascii=False, sort_keys=True)

        print(f"\n  {tag}")
        print(f"    bass peak before comp : {p_bass:+.2f} dB "
              f"(after {comp(p_bass):+.2f} dB)")
        print(f"    highest other band    : {p_other:+.2f} dB")
        print(f"    -> output-gain        : {out_gain:+.2f} dB "
              f"(new peak {TARGET_PEAK_DB:+.1f} dB)")
        print(f"    midrange improvement  : {out_gain:+.2f} dB")

    print("\ndone -- only band0 (20-400 Hz) is compressed")


if __name__ == "__main__":
    main()
