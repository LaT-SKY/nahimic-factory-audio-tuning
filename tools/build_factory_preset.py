#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build the EasyEffects preset that applies the A-Volute / Nahimic factory
speaker correction for the MECHREVO JIAOLONG (Conexant SN6140, SUBSYS 1D053010).

Pipeline:
    convolver (2048-tap factory DeviceOptimizationFilter)
      -> limiter (true-peak safety)

Design notes
------------
* The factory filter is peak-normalised to 0 dB before export. That way the
  tonal correction is bit-exact while the filter itself can never push the
  signal above full scale. Overall loudness is restored with `output-gain`
  instead, which is a pure scalar and does not alter the correction.
* `autogain` is disabled: EasyEffects would otherwise re-normalise the impulse
  response and throw away the gain structure the factory intended.
* The IR is written as 32-bit PCM WAV because that is the format EasyEffects
  itself uses (SF_FORMAT_WAV | SF_FORMAT_PCM_32).
* `kernel-name` must NOT carry a file extension. ConvolverKernelManager looks
  the name up against a list of known extensions; a name that already has one
  fails to resolve and the convolver silently enters passthrough mode.
"""

import base64
import json
import os
import wave
import xml.etree.ElementTree as ET
from collections import OrderedDict

import numpy as np

HOME = os.path.expanduser("~")
EE_DATA = os.environ.get("EASYEFFECTS_DATA", os.path.join(HOME, ".var/app/com.github.wwmm.easyeffects/data/easyeffects"))
IRS_DIR = os.path.join(EE_DATA, "irs")
OUT_DIR = os.path.join(EE_DATA, "output")
NAH = os.environ.get("NAHIMIC_DEVICES", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "Devices"))

REF_PRESET = os.path.join(OUT_DIR, "Music.json")   # for the limiter key template

VARIANTS = {
    "X6DR57TK": "1D053010_1D053010K5500103_Speakers.nsx",   # 7 of 8 SKUs
    "X6DR546K": "1D053010_1D053010K5500100_Speakers.nsx",   # 1 of 8 SKUs
}


def read_factory_fir(filename):
    root = ET.parse(os.path.join(NAH, filename)).getroot()
    for st in root.iter("Settings"):
        for c in st:
            if c.tag == "kSet_DeviceOptimizationFilterFL":
                raw = base64.b64decode(c.find("Value").text.strip())
                return np.frombuffer(raw, dtype=np.float32).astype(np.float64)
    raise RuntimeError(f"no filter in {filename}")


def write_ir_pcm32(path, ir, sr=48000):
    """32-bit signed PCM WAV, stereo (factory FL == FR), peak-normalised."""
    peak = float(np.abs(np.fft.rfft(ir, 32768)).max())
    x = ir / peak
    x = np.clip(np.round(x * (2 ** 31 - 1)), -(2 ** 31), 2 ** 31 - 1).astype("<i4")
    inter = np.empty(len(x) * 2, dtype="<i4")
    inter[0::2] = x
    inter[1::2] = x
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(4)          # 4 bytes = 32-bit PCM
        w.setframerate(sr)
        w.writeframes(inter.tobytes())
    return peak


def main():
    os.makedirs(IRS_DIR, exist_ok=True)

    ref = json.load(open(REF_PRESET, encoding="utf-8"),
                    object_pairs_hook=OrderedDict)
    T_LM = ref["output"]["limiter#0"]

    print("=" * 72)
    print("writing impulse responses (32-bit PCM, peak-normalised)")
    print("=" * 72)
    for tag, fn in VARIANTS.items():
        ir = read_factory_fir(fn)
        name = f"Nahimic-Factory-{tag}"
        p = os.path.join(IRS_DIR, name + ".irs")
        peak = write_ir_pcm32(p, ir)
        print(f"  {name}.irs   {len(ir)} taps  {os.path.getsize(p)} bytes  "
              f"(pre-norm peak {20*np.log10(peak):+.2f} dB)")

    def limiter(threshold, release):
        o = OrderedDict(T_LM)
        o.update({
            "threshold": float(threshold),
            "release": float(release),
            "attack": 5.0,
            "lookahead": 5.0,
            "oversampling": "True Peak/16 bit",
            "mode": "Herm Thin",
            "stereo-link": 100.0,
            "alr": False,
            "gain-boost": False,
            "dithering": "None",
            "bypass": False,
        })
        assert set(o.keys()) == set(T_LM.keys())
        return o

    def convolver(kernel):
        return OrderedDict([
            ("autogain", False),        # keep the factory gain structure
            ("bypass", False),
            ("dry", -100.0),            # fully wet: the correction replaces the signal
            ("input-gain", 0.0),
            ("ir-width", 100),          # IR is stereo with identical channels
            ("kernel-name", kernel),    # NO file extension
            ("output-gain", 4.0),       # scalar loudness restore, does not alter EQ
            ("sofa", OrderedDict([("azimuth", 0.0), ("elevation", 0.0),
                                  ("radius", 1.0)])),
            ("wet", 0.0),
        ])

    presets = OrderedDict()
    for tag in VARIANTS:
        name = f"笔记本扬声器-Nahimic原厂-{tag}"
        out = OrderedDict()
        out["convolver#0"] = convolver(f"Nahimic-Factory-{tag}")
        out["limiter#0"] = limiter(-1.0, 20.0)   # release 合法上限为 20（kcfg 0.25…20）
        out["plugins_order"] = ["convolver#0", "limiter#0"]
        out["blocklist"] = []
        presets[name] = OrderedDict([("output", out)])

    print("\n" + "=" * 72)
    print("writing presets")
    print("=" * 72)
    for name, doc in presets.items():
        order = doc["output"]["plugins_order"]
        for pid in order:
            assert pid in doc["output"], f"{name}: {pid} missing"
        for k in doc["output"]:
            if k in ("plugins_order", "blocklist"):
                continue
            assert k in order
        path = os.path.join(OUT_DIR, name + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=4, ensure_ascii=False, sort_keys=True)
            f.write("\n")
        print(f"  {name}.json   chain: {' -> '.join(x.replace('#0','') for x in order)}")

    print("\ndone")


if __name__ == "__main__":
    main()
