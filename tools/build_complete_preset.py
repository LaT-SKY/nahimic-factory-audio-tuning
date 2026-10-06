#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build the complete Nahimic Music-chain preset for the MECHREVO JIAOLONG.

Factory chain (from AudioProfiles/Music.nsx + Devices/*_Speakers.nsx)
--------------------------------------------------------------------
  1. DeviceOptimizationFilter   2048-tap FIR          -> convolver
  2. BassBoost                  +6 dB @ 250 Hz        -> equalizer, bell
  3. TrebleBoost                +4 dB                 -> equalizer, high shelf
  4. VoiceBoost                 +4 dB, 500-4000 Hz    -> equalizer, wide bell
  5. DRC                        threshold -70 dB      -> SKIPPED (see below)
  6. Attenuator                 9-band voice-coil cap -> SKIPPED (see below)
  7. Limiter1                   @ 400 Hz              -> limiter

Why stages 5 and 6 are skipped
------------------------------
The DRC has a -70 dB threshold with rate 0.98 -- it compresses essentially
everything, i.e. it is a leveler / automatic gain control. The user explicitly
asked for no automatic gain. The Attenuator is also a dynamic stage and has no
direct EasyEffects counterpart. Both roles are covered, to the extent needed,
by the true-peak limiter at the end.

BassBoost is modelled as a BELL, not a low shelf
------------------------------------------------
kSet_BassBoostBandWidthOctave = 0.707 exists in the device schema, which only
makes sense for a peaking filter. A low shelf at 250 Hz would re-boost the
20-250 Hz region that the factory FIR deliberately cuts by -15 dB, undoing the
driver protection. Bandwidth 0.707 octave -> Q = 1/(2*sinh(ln2/2*0.707)) ~ 2.0.

Gain staging
------------
LSP's "APO (DR)" band mode is documented as textbook biquads equivalent to
Equalizer APO, so the RBJ cookbook coefficients reproduce it accurately. The
combined FIR + EQ magnitude is computed numerically and the preamp is chosen so
the peak sits just below full scale.
"""

import base64
import json
import os
import xml.etree.ElementTree as ET
from collections import OrderedDict

import numpy as np

HOME = os.path.expanduser("~")
EE_DATA = os.environ.get("EASYEFFECTS_DATA", os.path.join(HOME, ".var/app/com.github.wwmm.easyeffects/data/easyeffects"))
OUT_DIR = os.path.join(EE_DATA, "output")
NAH = os.environ.get("NAHIMIC_DEVICES", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "Devices"))
SR = 48000

# factory profile values (AudioProfiles/Music.nsx)
BASS_GAIN = 6.0
BASS_FREQ = 250.0
BASS_BW_OCT = 0.707
VOICE_GAIN = 4.0
VOICE_LO, VOICE_HI = 500.0, 4000.0
TREBLE_GAIN = 4.0
TREBLE_FREQ = 8000.0

VARIANTS = {"X6DR57TK": "1D053010_1D053010K5500103_Speakers.nsx",
            "X6DR546K": "1D053010_1D053010K5500100_Speakers.nsx"}


# ------------------------------------------------------------------ biquads
def rbq_bell(f0, Q, gain_db, fs=SR):
    A = 10 ** (gain_db / 40.0)
    w = 2 * np.pi * f0 / fs
    alpha = np.sin(w) / (2 * Q)
    b = [1 + alpha * A, -2 * np.cos(w), 1 - alpha * A]
    a = [1 + alpha / A, -2 * np.cos(w), 1 - alpha / A]
    return np.array(b) / a[0], np.array(a) / a[0]


def rbq_lowshelf(f0, gain_db, fs=SR, S=1.0):
    """RBJ low shelf.

    BassBoost is modelled as a SHELF, not a narrow bell. Measured comparison at
    +6 dB @ 250 Hz:
        bell Q2.02 :  40Hz +0.0   60Hz +0.1  100Hz +0.3  250Hz +6.0
        low shelf  :  40Hz +6.0   60Hz +6.0  100Hz +5.8  250Hz +3.0
    A 0.707-octave bell produces a bump at 250 Hz and no actual bass at all,
    which is not what "BassBoost" means.
    """
    A = 10 ** (gain_db / 40.0)
    w = 2 * np.pi * f0 / fs
    cw, sw = np.cos(w), np.sin(w)
    alpha = sw / 2 * np.sqrt((A + 1 / A) * (1 / S - 1) + 2)
    tsa = 2 * np.sqrt(A) * alpha
    b = [A * ((A + 1) - (A - 1) * cw + tsa),
         2 * A * ((A - 1) - (A + 1) * cw),
         A * ((A + 1) - (A - 1) * cw - tsa)]
    a = [(A + 1) + (A - 1) * cw + tsa,
         -2 * ((A - 1) + (A + 1) * cw),
         (A + 1) + (A - 1) * cw - tsa]
    return b, a


def rbq_highshelf(f0, gain_db, fs=SR, S=1.0):
    """RBJ high shelf. a1 carries a PLUS sign -- writing -2*(...) inverts the
    filter, which was a real bug caught by evaluating the response."""
    A = 10 ** (gain_db / 40.0)
    w = 2 * np.pi * f0 / fs
    cw, sw = np.cos(w), np.sin(w)
    alpha = sw / 2 * np.sqrt((A + 1 / A) * (1 / S - 1) + 2)
    tsa = 2 * np.sqrt(A) * alpha
    b = [A * ((A + 1) + (A - 1) * cw + tsa),
         -2 * A * ((A - 1) + (A + 1) * cw),
         A * ((A + 1) + (A - 1) * cw - tsa)]
    a = [(A + 1) - (A - 1) * cw + tsa,
         2 * ((A - 1) - (A + 1) * cw),
         (A + 1) - (A - 1) * cw - tsa]
    return b, a


def ir_of(b, a, N=16384):
    """Impulse response by running the difference equation directly.

    Evaluating the transfer function by substituting z = exp(-jw) requires
    agreeing on a coefficient sign convention, which is easy to get wrong.
    Running the recursion and taking an FFT has no such ambiguity.
    """
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


def biquad_mag(b, a, f, fs=SR):
    H = np.abs(np.fft.rfft(ir_of(b, a), 32768))
    ff = np.fft.rfftfreq(32768, 1 / fs)
    return np.interp(f, ff, H)


def read_fir(fn):
    for st in ET.parse(os.path.join(NAH, fn)).getroot().iter("Settings"):
        for c in st:
            if c.tag == "kSet_DeviceOptimizationFilterFL":
                return np.frombuffer(base64.b64decode(c.find("Value").text.strip()),
                                     dtype=np.float32).astype(np.float64)
    raise RuntimeError(fn)


def bw_to_q(bw_oct):
    return 1.0 / (2.0 * np.sinh(np.log(2) / 2 * bw_oct))


def main():
    f = np.logspace(np.log10(20), np.log10(20000), 4000)

    # ---- factory boosts as biquads -------------------------------------
    q_bass = bw_to_q(BASS_BW_OCT)
    q_voice = bw_to_q(np.log2(VOICE_HI / VOICE_LO))
    f_voice = np.sqrt(VOICE_LO * VOICE_HI)
    print(f"BassBoost : low shelf {BASS_FREQ:.0f} Hz  {BASS_GAIN:+.1f} dB"
          f"   (a {BASS_BW_OCT}-oct bell gives no bass below 200 Hz)")
    print(f"VoiceBoost: bell {f_voice:.0f} Hz  Q={q_voice:.2f}  {VOICE_GAIN:+.1f} dB"
          f"   (covers {VOICE_LO:.0f}-{VOICE_HI:.0f} Hz)")
    print(f"TrebleBoost: high shelf {TREBLE_FREQ:.0f} Hz  {TREBLE_GAIN:+.1f} dB")

    bands = [
        (BASS_FREQ, BASS_GAIN, 0.707, "Lo-shelf"),
        (f_voice, VOICE_GAIN, q_voice, "Bell"),
        (TREBLE_FREQ, TREBLE_GAIN, 0.707, "Hi-shelf"),
    ]

    for tag, fn in VARIANTS.items():
        fir = read_fir(fn)
        fir_norm = fir / np.abs(np.fft.rfft(fir, 32768)).max()   # peak -> 0 dB
        ff = np.fft.rfftfreq(32768, 1 / SR)
        fir_mag = np.interp(f, ff, np.abs(np.fft.rfft(fir_norm, 32768)))

        eq_mag = np.ones_like(f)
        for f0, g, q, t in bands:
            if t == "Hi-shelf":
                b, a = rbq_highshelf(f0, g)
            elif t == "Lo-shelf":
                b, a = rbq_lowshelf(f0, g)
            else:
                b, a = rbq_bell(f0, g, q)
            eq_mag *= biquad_mag(b, a, f)

        total_db = 20 * np.log10(fir_mag * eq_mag + 1e-12)
        peak_db = total_db[(f >= 40) & (f <= 18000)].max()
        preamp = -(peak_db + 1.0)          # leave 1 dB headroom

        # Gain staging. The FIRST attempt applied the preamp but forgot that the
        # FIR-only preset runs the convolver at +4 dB, so the "enhanced" preset
        # ended up ~4 dB quieter in the midrange than the plain one. By equal
        # loudness that reads as "the effect got weaker", which is exactly what
        # the user reported. Compensate so the midrange matches the base preset.
        BASE_CONV_GAIN = 4.0               # what the FIR-only preset uses
        fir_db = 20 * np.log10(fir_mag + 1e-12)
        mid = (f >= 300) & (f <= 6000)
        conv_gain = float((fir_db[mid] + BASE_CONV_GAIN
                           - total_db[mid] - preamp).mean())
        conv_gain = round(min(conv_gain, 8.0), 2)   # cap: limiter must cope
        base_mid = float((fir_db[mid] + BASE_CONV_GAIN).mean())

        print(f"\n--- {tag} ---")
        print(f"  combined peak : {peak_db:+.2f} dB -> preamp {preamp:+.2f} dB")
        print(f"  convolver out : {conv_gain:+.2f} dB  (base preset uses "
              f"{BASE_CONV_GAIN:+.1f} dB)")
        print(f"  mid 300-6k    : base {base_mid:+.2f} dB -> "
              f"complete {total_db[mid].mean() + preamp + conv_gain:+.2f} dB")
        for fc in (100, 250, 500, 800, 1000, 2000, 4000, 8000, 12000):
            i = int(np.argmin(np.abs(f - fc)))
            print(f"    {fc:>6} Hz : {total_db[i]:+7.2f} dB")

        # ---- build preset ---------------------------------------------
        ref = json.load(open(os.path.join(OUT_DIR, "Music.json"), encoding="utf-8"),
                        object_pairs_hook=OrderedDict)
        T_LM = ref["output"]["limiter#0"]
        T_EQ = ref["output"]["equalizer#0"]

        eq = OrderedDict()
        eq["balance"] = 0.0
        eq["bypass"] = False
        eq["input-gain"] = round(preamp, 2)
        eq["mode"] = "IIR"
        eq["num-bands"] = len(bands)
        eq["output-gain"] = 0.0
        eq["pitch-left"] = 0.0
        eq["pitch-right"] = 0.0
        eq["split-channels"] = False

        def mkband(i, f0, g, q, t):
            return f"band{i}", OrderedDict([
                ("frequency", float(f0)), ("gain", float(g)), ("mode", "APO (DR)"),
                ("mute", False), ("q", float(round(q, 4))), ("slope", "x1"),
                ("solo", False), ("type", t), ("width", 4.0)])

        bl = OrderedDict(mkband(i, *b) for i, b in enumerate(bands))
        eq["left"] = OrderedDict((k, OrderedDict(v)) for k, v in bl.items())
        eq["right"] = OrderedDict((k, OrderedDict(v)) for k, v in bl.items())
        assert set(eq.keys()) == set(T_EQ.keys())

        cv = OrderedDict([
            ("autogain", False), ("bypass", False), ("dry", -100.0),
            ("input-gain", 0.0), ("ir-width", 100),
            ("kernel-name", f"Nahimic-Factory-{tag}"),
            ("output-gain", conv_gain),
            ("sofa", OrderedDict([("azimuth", 0.0), ("elevation", 0.0),
                                  ("radius", 1.0)])),
            ("wet", 0.0)])

        lm = OrderedDict(T_LM)
        lm.update({"threshold": -1.0, "release": 20.0, "attack": 5.0,   # release 合法上限为 20
                   "lookahead": 5.0, "oversampling": "True Peak/16 bit",
                   "mode": "Herm Thin", "stereo-link": 100.0, "alr": False,
                   "gain-boost": False, "dithering": "None", "bypass": False})

        out = OrderedDict()
        out["convolver#0"] = cv
        out["equalizer#0"] = eq
        out["limiter#0"] = lm
        out["plugins_order"] = ["convolver#0", "equalizer#0", "limiter#0"]
        out["blocklist"] = []

        name = f"笔记本扬声器-Nahimic完整-{tag}"
        path = os.path.join(OUT_DIR, name + ".json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(OrderedDict([("output", out)]), fh, indent=4,
                      ensure_ascii=False, sort_keys=True)
            fh.write("\n")
        print(f"  written -> {name}.json")


if __name__ == "__main__":
    main()
