#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""

STATUS (2026-09-22) -- 已撤销，留档
-----------------------------------
生成的 6 个 `耳机-Nahimic外化-*.json` **已从 EasyEffects 目录删除**：
实测效果不合格（正前 HRIR 左右耳几乎相同，只是染色，不是环绕）。
脚本保留供将来复用。

Build EasyEffects **headphone** presets around the Nahimic HRTF kernels.

Pairs with tools/hrir_to_irs.py: each `Nahimic-HRTF-*.irs` in the IRS folder
becomes one preset whose chain is

    Convolver (frontal HRTF)  ->  Crossfeed (bs2b)

Be clear about what this is
---------------------------
The Convolver applies **one** HRTF pair to the whole signal -- the same thing
EasyEffects does with a single-emitter SOFA file.  It colours the sound with
the direction-dependent filtering of a source at that angle; it does **not**
place individual sources around you.  Real 7.1 -> binaural needs a
multichannel renderer outside EasyEffects (see
easyeffects-application-plan.md section C1).

The Crossfeed stage afterwards is the conventional headphone treatment: it
mixes a little of each channel into the other to reduce the "sound inside your
head" effect.  It is included but can be bypassed.

Presets are named `耳机-Nahimic外化-<kernel>.json`.
"""

import glob
import json
import os
from collections import OrderedDict

EE = os.path.expanduser("~/.var/app/com.github.wwmm.easyeffects/data/easyeffects")
IRS = os.path.join(EE, "irs")
OUT = os.path.join(EE, "output")


def convolver(kernel):
    return OrderedDict([
        ("autogain", False),
        ("bypass", False),
        ("dry", -100.0),
        ("input-gain", 0.0),
        ("ir-width", 100),
        ("kernel-name", kernel),
        ("output-gain", 0.0),
        ("sofa", OrderedDict([("azimuth", 0.0), ("elevation", 0.0), ("radius", 1.0)])),
        ("wet", 0.0),
    ])


def crossfeed():
    """bs2b defaults: gentle inter-aural blending, no level change."""
    return OrderedDict([
        ("bypass", False),
        ("fcut", 700.0),
        ("feed", 4.5),
        ("input-gain", 0.0),
        ("output-gain", 0.0),
    ])


def build(kernel):
    plugins = OrderedDict([("convolver#0", convolver(kernel)),
                           ("crossfeed#0", crossfeed())])
    return OrderedDict([("output", OrderedDict(
        [("blocklist", [])] + list(plugins.items())
        + [("plugins_order", ["convolver#0", "crossfeed#0"])]))])


def main():
    kernels = sorted(os.path.splitext(os.path.basename(p))[0]
                     for p in glob.glob(os.path.join(IRS, "Nahimic-HRTF-*.irs")))
    if not kernels:
        raise SystemExit("irs/ 里没有 Nahimic-HRTF-*.irs，先跑 tools/hrir_to_irs.py")
    n = 0
    for kernel in kernels:
        name = "耳机-Nahimic外化-%s.json" % kernel.replace("Nahimic-HRTF-", "")
        with open(os.path.join(OUT, name), "w", encoding="utf-8") as fh:
            json.dump(build(kernel), fh, ensure_ascii=False, indent=4)
        n += 1
        print("%-46s convolver(%s) -> crossfeed" % (name, kernel))
    print("\n写出 %d 个耳机预设" % n)


if __name__ == "__main__":
    main()
