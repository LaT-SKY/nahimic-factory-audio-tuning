#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build EasyEffects **input** (microphone) presets from Nahimic's capture chain.

Data source
-----------
* data/Global.nsx      -- capture-side global switches
* data/MicProfile/     -- Chat.nsx / Conference.nsx (the UI's two capture modes)

What Nahimic configures, and what we can do about it
----------------------------------------------------
    VoiceStabilizerState        1  -> Compressor (slow attack/release)      approximate
    NoiseSuppressionState       1  -> DeepFilterNet                        good
    NoisSuppressionValue    0.0316 -> noise floor, see Global.nsx          see note
    AECState                    1  -> Echo Canceller (Speex)               good
    BeamformingState        1 / 0  -> (needs a mic array)                  NOT POSSIBLE
    kSet_NoiseGateState         0  -> Gate                                 off by default

Note on 0.0316: the same number appears as
`kSet_CaptureAmbientNoiseReductionFloor` in Global.nsx, so it is Nahimic's
noise-floor setting rather than a UI percentage.  EasyEffects' noise
suppression plugins have no direct equivalent control, so it is recorded in the
preset description instead of being mapped onto an unrelated knob.

Chat and Conference differ **only** in BeamformingState (1 vs 0).  Since
beamforming cannot be reproduced, both collapse to the same preset; that is
stated rather than papered over with invented differences.
"""

import json
import os
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
EE = os.path.expanduser("~/.var/app/com.github.wwmm.easyeffects/data/easyeffects")
EE_IN = os.path.join(EE, "input")


def deepfilternet():
    """Neural noise suppression -- stands in for CaptureAmbientNoiseReduction."""
    return OrderedDict([
        ("attenuation-limit", 100.0), ("bypass", False), ("input-gain", 0.0),
        ("max-df-processing-threshold", 20.0), ("max-erb-processing-threshold", 30.0),
        ("min-processing-buffer", 0), ("min-processing-threshold", -10.0),
        ("output-gain", 0.0), ("post-filter-beta", 0.02),
    ])


def echo_canceller():
    """Speex AEC + its own NS/HPF/AGC -- matches AECState = 1."""
    return OrderedDict([
        ("bypass", False),
        ("echo-canceller", OrderedDict([
            ("automatic-gain-control", True),
            ("enable", True),
            ("enforce-high-pass", True),
            ("mobile-mode", False),
        ])),
        ("high-pass", OrderedDict([("enable", True), ("full-band", True)])),
        ("input-gain", 0.0),
        ("noise-suppression", OrderedDict([("enable", True), ("level", "Moderate")])),
        ("output-gain", 0.0),
    ])


def compressor():
    """Voice stabiliser stand-in: slow, gentle levelling of speech."""
    return OrderedDict([
        ("attack", 20.0), ("boost-amount", 6.0), ("boost-threshold", -72.0),
        ("bypass", False), ("dry", -80.01), ("hpf-frequency", 10.0),
        ("hpf-mode", "Off"), ("input-gain", 0.0), ("input-to-link", -80.01),
        ("input-to-sidechain", -80.01), ("knee", -6.0), ("link-to-input", -80.01),
        ("link-to-sidechain", -80.01), ("lpf-frequency", 20000.0),
        ("lpf-mode", "Off"), ("makeup", 0.0), ("mode", "Downward"),
        ("output-gain", 0.0), ("ratio", 2.0), ("release", 200.0),
        ("release-threshold", -80.01),
        ("sidechain", OrderedDict([
            ("lookahead", 0.0), ("mode", "RMS"), ("preamp", 0.0),
            ("reactivity", 10.0), ("source", "Middle"),
            ("stereo-split-source", "Left/Right"), ("type", "Feed-forward"),
        ])),
        ("sidechain-to-input", -80.01), ("sidechain-to-link", -80.01),
        ("stereo-split", False), ("threshold", -24.0), ("wet", 0.0),
    ])


def gate():
    """Noise gate -- kSet_NoiseGateState is 0 in Global.nsx, so it ships bypassed.

    Present because the user can flip it on for a noisy room; the threshold is a
    starting point, not an XML value (the factory gate is disabled).
    """
    return OrderedDict([
        ("attack", 20.0), ("bypass", True), ("curve-threshold", -60.0),
        ("curve-zone", -12.0), ("dry", 0.0), ("hpf-frequency", 10.0),
        ("hpf-mode", "Off"), ("hysteresis", False), ("hysteresis-threshold", 0.0),
        ("hysteresis-zone", 0.0), ("input-gain", 0.0), ("input-to-link", -80.01),
        ("input-to-sidechain", -80.01), ("link-to-input", -80.01),
        ("link-to-sidechain", -80.01), ("lpf-frequency", 20000.0),
        ("lpf-mode", "Off"), ("makeup", 0.0), ("reduction", -36.0),
        ("release", 250.0),
        ("sidechain", OrderedDict([
            ("lookahead", 0.0), ("mode", "RMS"), ("preamp", 0.0),
            ("reactivity", 10.0), ("source", "Middle"),
            ("stereo-split-source", "Left/Right"), ("type", "Internal"),
        ])),
        ("sidechain-to-input", -80.01), ("sidechain-to-link", -80.01),
        ("stereo-split", False), ("wet", 0.0),
    ])


def build():
    plugins = OrderedDict()
    order = ["deepfilternet#0", "echo_canceller#0", "gate#0", "compressor#0"]
    plugins["deepfilternet#0"] = deepfilternet()
    plugins["echo_canceller#0"] = echo_canceller()
    plugins["gate#0"] = gate()
    plugins["compressor#0"] = compressor()
    return OrderedDict([("input", OrderedDict(
        [("blocklist", [])] + list(plugins.items()) + [("plugins_order", order)]))])


def main():
    os.makedirs(EE_IN, exist_ok=True)
    name = "麦克风-Nahimic降噪.json"
    path = os.path.join(EE_IN, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(build(), fh, ensure_ascii=False, indent=4)
    print("写出 %s" % path)
    print("链路: DeepFilterNet -> Echo Canceller -> Gate(旁路) -> Compressor")
    print()
    print("对应关系:")
    print("  kSet_CaptureAmbientNoiseReductionState=1 -> DeepFilterNet")
    print("  AECState=1                               -> Echo Canceller")
    print("  kSet_NoiseGateState=0                    -> Gate（旁路，可手动开）")
    print("  VoiceStabilizerState=1                   -> Compressor（近似）")
    print("  BeamformingState=1/0                     -> 无对应（需麦阵），Chat/Conference 因此合并")
    print("  NoisSuppressionValue=0.0316              -> 无对应旋钮，见脚本注释")


if __name__ == "__main__":
    main()
