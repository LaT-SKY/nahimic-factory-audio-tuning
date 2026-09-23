#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Nahimic "Music" preset, rebuilt to match the REAL DSP architecture.

The NahimicAPO4.dll symbol table revealed what each setting actually is:

  kSet_* setting          Nahimic implementation            correct EE model
  ---------------------------------------------------------------------------
  DeviceOptimizationFilter  fft_fwd_optimization_stage /    convolver   (already right)
                            optimization_convo_stage
  BassBoost                 Ape::SBP::VirtualBassBoost       bass_enhancer
                            (+ VBBCompressor)               NOT an EQ shelf
  TrebleBoost               trebleboost_stage               high shelf  (OK)
  VoiceBoost                Ape::SBP::Clarity (fVoiceGain)  no static EQ equivalent
  DRC                       drc_stage                       skipped (auto gain)
  Attenuator                fft_fwd_attenuator_stage        skipped

Two modelling errors this corrects
-----------------------------------
1. BassBoost is a *psychoacoustic* bass enhancer that synthesises harmonics
   from the bass band and adds them back, so the ear reconstructs a
   fundamental the driver cannot reproduce. It is not a +6 dB low shelf.
   "VirtualBassBoost" is the class name.

2. VoiceBoost is a dynamic clarity processor. Modelling it as a static +4 dB
   bell at 1414 Hz partially refilled the 800 Hz notch that the device filter
   deliberately creates -- i.e. it put back some of the "plastic" colour.
   It is therefore left out.

Chain: bass_enhancer -> convolver -> equalizer(TrebleBoost) -> limiter(bypassed)

The bass enhancer runs BEFORE the convolver so it sees the un-attenuated
original bass; the FIR then corrects the combined result linearly.
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

# BassBoost from AudioProfiles/Music.nsx
BASS_GAIN_DB = 6.0
BASS_FREQ = 250.0
BASS_BW_OCT = 0.707
# TrebleBoost from AudioProfiles/Music.nsx
TREBLE_GAIN_DB = 4.0
TREBLE_FREQ = 8000.0


def read_ir(tag):
    with wave.open(os.path.join(IRS, f"Nahimic-Factory-{tag}.irs")) as w:
        raw = w.readframes(w.getnframes())
    a = np.frombuffer(raw, dtype="<i4").astype(np.float64) / (2 ** 31 - 1)
    return a[0::2]


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


def main():
    ref = json.load(open(os.path.join(OUT, "Music.json"), encoding="utf-8"),
                    object_pairs_hook=OrderedDict)["output"]
    T_BE = ref["bass_enhancer#0"]
    T_EQ = ref["equalizer#0"]
    T_LM = ref["limiter#0"]

    for tag in ("X6DR57TK", "X6DR546K"):
        # ---- linear part of the chain, for gain staging ------------------
        f = np.logspace(np.log10(20), np.log10(20000), 3000)
        ff = np.fft.rfftfreq(32768, 1 / SR)
        fir = np.interp(f, ff, np.abs(np.fft.rfft(read_ir(tag), 32768)))
        shelf = np.interp(f, ff, np.abs(np.fft.rfft(
            ir_of(*rbq_highshelf(TREBLE_FREQ, TREBLE_GAIN_DB)), 32768)))
        lin = 20 * np.log10((fir * shelf)[(f >= 40) & (f <= 18000)].max())

        # the bass enhancer is non-linear: leave extra headroom for the
        # harmonics it injects
        ENHANCER_MARGIN_DB = 3.0
        conv_gain = round(-1.0 - lin - ENHANCER_MARGIN_DB, 2)

        be = OrderedDict(T_BE)
        be.update({
            "amount": BASS_GAIN_DB * 1.5,   # VBB "gain dB" -> EE amount scale
            "blend": 2.0,
            "bypass": False,
            "floor": 30.0,
            "floor-active": True,
            "harmonics": 9.0,
            "input-gain": 0.0,
            "output-gain": 0.0,
            "scope": BASS_FREQ,
        })
        assert set(be.keys()) == set(T_BE.keys())

        eq = OrderedDict()
        eq["balance"] = 0.0
        eq["bypass"] = False
        eq["input-gain"] = 0.0
        eq["mode"] = "IIR"
        eq["num-bands"] = 1
        eq["output-gain"] = 0.0
        eq["pitch-left"] = 0.0
        eq["pitch-right"] = 0.0
        eq["split-channels"] = False
        band = OrderedDict([
            ("frequency", TREBLE_FREQ), ("gain", TREBLE_GAIN_DB),
            ("mode", "APO (DR)"), ("mute", False), ("q", 0.707),
            ("slope", "x1"), ("solo", False), ("type", "Hi-shelf"),
            ("width", 4.0)])
        eq["left"] = OrderedDict([("band0", OrderedDict(band))])
        eq["right"] = OrderedDict([("band0", OrderedDict(band))])
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
        lm["bypass"] = True        # user reported the limiter distorts

        out = OrderedDict()
        out["bass_enhancer#0"] = be
        out["convolver#0"] = cv
        out["equalizer#0"] = eq
        out["limiter#0"] = lm
        out["plugins_order"] = ["bass_enhancer#0", "convolver#0",
                                "equalizer#0", "limiter#0"]
        out["blocklist"] = []

        name = f"笔记本扬声器-NahimicMusic-{tag}"
        path = os.path.join(OUT, name + ".json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(OrderedDict([("output", out)]), fh, indent=4,
                      ensure_ascii=False, sort_keys=True)
            fh.write("\n")
        print(f"  {name}.json")
        print(f"     linear peak {lin:+.2f} dB -> convolver gain "
              f"{conv_gain:+.2f} dB (incl. {ENHANCER_MARGIN_DB} dB enhancer margin)")
        print(f"     bass_enhancer: amount={be['amount']} scope={be['scope']}Hz "
              f"harmonics={be['harmonics']}")
        print(f"     equalizer: Hi-shelf {TREBLE_FREQ:.0f}Hz {TREBLE_GAIN_DB:+.0f}dB"
              f"  (VoiceBoost omitted - it is Ape::SBP::Clarity, not a static EQ)")

    print("\ndone")


if __name__ == "__main__":
    main()
