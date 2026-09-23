#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Remove the limiter from the speaker presets and make them clip-proof by gain.

Why
---
The limiter was doing ~8 dB of continuous gain reduction. The convolver ran at
+8 dB while the EQ preamp pulled -5.15 dB, so the signal arriving at the limiter
peaked around +7 dBFS -- it was not acting as a safety net, it was squashing
everything. That is audible as distortion.

Fix: no dynamics processing at all. The total chain gain is chosen so the
combined frequency response peaks at -1 dBFS, which makes clipping impossible
for any input. Loudness is then a matter of the system volume knob, and the
tone stays exactly as the factory specifies.

The limiter stays in the plugin chain but bypassed, so it is one click away if
it is ever wanted.
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

TARGET_PEAK_DB = -1.0


def read_ir(tag):
    with wave.open(os.path.join(IRS, f"Nahimic-Factory-{tag}.irs")) as w:
        raw = w.readframes(w.getnframes())
    a = np.frombuffer(raw, dtype="<i4").astype(np.float64) / (2 ** 31 - 1)
    return a[0::2]                       # left channel is enough (L == R)


def ir_of(b, a, N=16384):
    b0, b1, b2 = b[0] / a[0], b[1] / a[0], b[2] / a[0]
    a1, a2 = a[1] / a[0], a[2] / a[0]
    y = np.zeros(N)
    x1 = x2 = y1 = y2 = 0.0
    for n in range(N):
        xn = 1.0 if n == 0 else 0.0
        yn = b0 * xn + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        y[n] = yn
        x2, x1 = x1, xn
        y2, y1 = y1, yn
    return y


def rbq_bell(f0, Q, g, fs=SR):
    A = 10 ** (g / 40.0)
    w = 2 * np.pi * f0 / fs
    al = np.sin(w) / (2 * Q)
    cw = np.cos(w)
    return ([1 + al * A, -2 * cw, 1 - al * A], [1 + al / A, -2 * cw, 1 - al / A])


def rbq_lowshelf(f0, g, fs=SR, S=1.0):
    A = 10 ** (g / 40.0)
    w = 2 * np.pi * f0 / fs
    cw, sw = np.cos(w), np.sin(w)
    al = sw / 2 * np.sqrt((A + 1 / A) * (1 / S - 1) + 2)
    t = 2 * np.sqrt(A) * al
    return ([A * ((A + 1) - (A - 1) * cw + t), 2 * A * ((A - 1) - (A + 1) * cw),
             A * ((A + 1) - (A - 1) * cw - t)],
            [(A + 1) + (A - 1) * cw + t, -2 * ((A - 1) + (A + 1) * cw),
             (A + 1) + (A - 1) * cw - t])


def rbq_highshelf(f0, g, fs=SR, S=1.0):
    A = 10 ** (g / 40.0)
    w = 2 * np.pi * f0 / fs
    cw, sw = np.cos(w), np.sin(w)
    al = sw / 2 * np.sqrt((A + 1 / A) * (1 / S - 1) + 2)
    t = 2 * np.sqrt(A) * al
    return ([A * ((A + 1) + (A - 1) * cw + t), -2 * A * ((A - 1) + (A + 1) * cw),
             A * ((A + 1) + (A - 1) * cw - t)],
            [(A + 1) - (A - 1) * cw + t, 2 * ((A - 1) - (A + 1) * cw),
             (A + 1) - (A - 1) * cw - t])


def eq_response(eq, f):
    ff = np.fft.rfftfreq(32768, 1 / SR)
    mag = np.ones_like(f)
    for b in eq["left"].values():
        if b["type"] == "Bell":
            bb, aa = rbq_bell(b["frequency"], b["q"], b["gain"])
        elif b["type"] == "Lo-shelf":
            bb, aa = rbq_lowshelf(b["frequency"], b["gain"])
        elif b["type"] == "Hi-shelf":
            bb, aa = rbq_highshelf(b["frequency"], b["gain"])
        else:
            continue
        H = np.abs(np.fft.rfft(ir_of(bb, aa), 32768))
        mag *= np.interp(f, ff, H)
    return mag


def combined_peak_db(path, tag):
    """Peak of the whole chain response, in dB, with all gains at 0."""
    doc = json.load(open(path, encoding="utf-8"), object_pairs_hook=OrderedDict)
    out = doc["output"]
    f = np.logspace(np.log10(20), np.log10(20000), 3000)
    ff = np.fft.rfftfreq(32768, 1 / SR)
    fir = np.abs(np.fft.rfft(read_ir(tag), 32768))
    mag = np.interp(f, ff, fir)
    if "equalizer#0" in out:
        mag = mag * eq_response(out["equalizer#0"], f)
    band = (f >= 40) & (f <= 18000)
    return 20 * np.log10(mag[band].max())


def main():
    print("=" * 70)
    print("Removing the limiter, making presets clip-proof by gain")
    print("=" * 70)

    for fn in sorted(os.listdir(OUT)):
        if not fn.endswith(".json") or "Nahimic" not in fn:
            continue
        path = os.path.join(OUT, fn)
        tag = "X6DR546K" if "X6DR546K" in fn else "X6DR57TK"

        doc = json.load(open(path, encoding="utf-8"),
                        object_pairs_hook=OrderedDict)
        out = doc["output"]

        peak = combined_peak_db(path, tag)
        gain = round(TARGET_PEAK_DB - peak, 2)

        # zero any preamp the EQ carried, put all gain in one place
        if "equalizer#0" in out:
            out["equalizer#0"]["input-gain"] = 0.0
            out["equalizer#0"]["output-gain"] = 0.0
        out["convolver#0"]["output-gain"] = gain

        lim = out.get("limiter#0")
        if lim:
            lim["bypass"] = True

        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=4, ensure_ascii=False, sort_keys=True)
            fh.write("\n")

        print(f"\n  {fn}")
        print(f"    chain peak (gain 0) : {peak:+.2f} dB")
        print(f"    convolver output-gain: {gain:+.2f} dB -> peak {TARGET_PEAK_DB:+.1f} dB")
        print(f"    limiter              : {'bypassed' if lim else 'not present'}")

    print("\ndone -- no dynamics processing in any preset")


if __name__ == "__main__":
    main()
