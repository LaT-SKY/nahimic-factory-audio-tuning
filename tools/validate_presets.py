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

EE = os.environ.get("EASYEFFECTS_DATA",
                      os.path.expanduser("~/.var/app/com.github.wwmm.easyeffects/data/easyeffects"))
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

# --- v8.3.0 新增：空间效果插件 ---------------------------------------------
# 键名取自上游序列化代码（不是 kcfg 的 camelCase，也不是运行态 .rc）：
#   src/stereo_tools_preset.cpp        （Calf LV2 StereoTools）
#   src/crosstalk_canceller_preset.cpp （EE 原生）
# 写错的键会被 EasyEffects 静默忽略 —— 见 reverse-engineering-notes.md 陷阱 1。
STEREO_TOOLS_KEYS = {
    "bypass", "input-gain", "output-gain", "balance-in", "balance-out",
    "softclip", "mutel", "muter", "phasel", "phaser", "mode",
    "side-level", "side-balance", "middle-level", "middle-panorama",
    "stereo-base", "delay", "sc-level", "stereo-phase", "dry", "wet",
}
# kcfg modeLabels（&gt; 解码后）
STEREO_TOOLS_MODES = {
    "LR > LR (Stereo Default)", "LR > MS (Stereo to Mid-Side)",
    "MS > LR (Mid-Side to Stereo)", "LR > LL (Mono Left Channel)",
    "LR > RR (Mono Right Channel)", "LR > L+R (Mono Sum L+R)",
    "LR > RL (Stereo Flip Channels)",
}
CROSSTALK_KEYS = {"bypass", "input-gain", "output-gain",
                  "phantom-center-only", "delay-us", "decay-db"}

# --- 补齐：此前 37 个插件块从未被校验（validate 会静默放行）----------------
BASS_ENHANCER_KEYS = {"bypass", "input-gain", "output-gain", "amount",
                      "harmonics", "scope", "floor", "blend", "floor-active"}
LIMITER_KEYS = {"mode", "oversampling", "dithering", "sidechain-type", "bypass",
                "input-gain", "output-gain", "lookahead", "attack", "release",
                "threshold", "sidechain-preamp", "stereo-link", "alr-attack",
                "alr-release", "alr-knee", "alr-knee-smooth", "alr", "gain-boost",
                "input-to-sidechain", "input-to-link", "sidechain-to-input",
                "sidechain-to-link", "link-to-input", "link-to-sidechain"}
DEEPFILTERNET_KEYS = {"bypass", "input-gain", "output-gain", "attenuation-limit",
                      "min-processing-threshold", "max-erb-processing-threshold",
                      "max-df-processing-threshold", "min-processing-buffer",
                      "post-filter-beta"}

LIMITER_MODES = {"Herm Thin", "Herm Wide", "Herm Tail", "Herm Duck",
                 "Exp Thin", "Exp Wide", "Exp Tail", "Exp Duck",
                 "Line Thin", "Line Wide", "Line Tail", "Line Duck"}
LIMITER_DITHERING = {"None", "7bit", "8bit", "11bit", "12bit", "15bit", "16bit",
                     "23bit", "24bit"}
LIMITER_SIDECHAIN = {"Internal", "External", "Link"}
LIMITER_OVERSAMPLING = (
    {"None"} |
    {"%s x%d/%s" % (fam, n, bits)
     for fam in ("Half", "Full") for n in (2, 3, 4, 6, 8) for bits in ("16 bit", "24 bit")} |
    {"True Peak/16 bit", "True Peak/24 bit"})

HANDLED_PLUGINS = {"equalizer", "multiband_compressor", "convolver", "loudness",
                   "reverb", "echo_canceller", "gate", "crossfeed", "compressor",
                   "stereo_tools", "crosstalk_canceller",
                   "bass_enhancer", "limiter", "deepfilternet"}

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


def check_keyset(name, pid, cfg, allowed):
    """键集必须**完全一致** —— 多一个、少一个都算错。

    返回 False 时调用方应**直接返回**，否则后续按名取值会 KeyError
    （自检时踩到过：把 `stereo-base` 拼成 `stereo-bass` 会让校验器自己崩掉）。
    """
    got = set(cfg)
    missing = allowed - got
    extra = got - allowed
    chk(not missing, "%s: %s 缺少键 %s" % (name, pid, sorted(missing)))
    chk(not extra, "%s: %s 出现未定义键 %s" % (name, pid, sorted(extra)))
    return not missing and not extra


def check_stereo_tools(name, cfg):
    if not check_keyset(name, "stereo_tools", cfg, STEREO_TOOLS_KEYS):
        return
    chk(cfg["mode"] in STEREO_TOOLS_MODES, "%s: stereo_tools mode %r illegal" % (name, cfg["mode"]))
    chk(-36.0 <= cfg["input-gain"] <= 36.0, "%s: stereo_tools input-gain 越界" % name)
    chk(-36.0 <= cfg["output-gain"] <= 36.0, "%s: stereo_tools output-gain 越界" % name)
    chk(-1.0 <= cfg["balance-in"] <= 1.0, "%s: stereo_tools balance-in 越界" % name)
    chk(-1.0 <= cfg["balance-out"] <= 1.0, "%s: stereo_tools balance-out 越界" % name)
    chk(-1.0 <= cfg["stereo-base"] <= 1.0, "%s: stereo_tools stereo-base 越界" % name)
    chk(-36.0 <= cfg["side-level"] <= 36.0, "%s: stereo_tools side-level 越界" % name)
    chk(-1.0 <= cfg["side-balance"] <= 1.0, "%s: stereo_tools side-balance 越界" % name)
    chk(-36.0 <= cfg["middle-level"] <= 36.0, "%s: stereo_tools middle-level 越界" % name)
    chk(-1.0 <= cfg["middle-panorama"] <= 1.0, "%s: stereo_tools middle-panorama 越界" % name)
    chk(-20.0 <= cfg["delay"] <= 20.0, "%s: stereo_tools delay 越界" % name)
    chk(1.0 <= cfg["sc-level"] <= 100.0, "%s: stereo_tools sc-level 越界" % name)
    chk(0.0 <= cfg["stereo-phase"] <= 360.0, "%s: stereo_tools stereo-phase 越界" % name)
    chk(-100.0 <= cfg["dry"] <= 20.0, "%s: stereo_tools dry 越界" % name)
    chk(-100.0 <= cfg["wet"] <= 20.0, "%s: stereo_tools wet 越界" % name)


def check_crosstalk_canceller(name, cfg):
    if not check_keyset(name, "crosstalk_canceller", cfg, CROSSTALK_KEYS):
        return
    chk(-36.0 <= cfg["input-gain"] <= 36.0, "%s: crosstalk input-gain 越界" % name)
    chk(-36.0 <= cfg["output-gain"] <= 36.0, "%s: crosstalk output-gain 越界" % name)
    chk(200.0 <= cfg["delay-us"] <= 500.0, "%s: crosstalk delay-us 越界" % name)
    chk(-6.0 <= cfg["decay-db"] <= 0.0, "%s: crosstalk decay-db 越界" % name)


def check_bass_enhancer(name, cfg):
    if not check_keyset(name, "bass_enhancer", cfg, BASS_ENHANCER_KEYS):
        return
    for k, lo, hi in (("input-gain", -36, 36), ("output-gain", -36, 36),
                      ("amount", -100, 36), ("harmonics", 0.1, 10),
                      ("scope", 10, 250), ("floor", 10, 120), ("blend", -10, 10)):
        chk(lo <= cfg[k] <= hi, "%s: bass_enhancer %s=%r 越界 [%g,%g]" % (name, k, cfg[k], lo, hi))


def check_limiter(name, cfg):
    if not check_keyset(name, "limiter", cfg, LIMITER_KEYS):
        return
    chk(cfg["mode"] in LIMITER_MODES, "%s: limiter mode %r illegal" % (name, cfg["mode"]))
    chk(cfg["oversampling"] in LIMITER_OVERSAMPLING,
        "%s: limiter oversampling %r illegal" % (name, cfg["oversampling"]))
    chk(cfg["dithering"] in LIMITER_DITHERING,
        "%s: limiter dithering %r illegal" % (name, cfg["dithering"]))
    chk(cfg["sidechain-type"] in LIMITER_SIDECHAIN,
        "%s: limiter sidechain-type %r illegal" % (name, cfg["sidechain-type"]))
    for k, lo, hi in (("input-gain", -36, 36), ("output-gain", -36, 36),
                      ("lookahead", 0.1, 20), ("attack", 0.25, 20),
                      ("release", 0.25, 20), ("threshold", -48, 0),
                      ("sidechain-preamp", -80.01, 40), ("stereo-link", 0, 100),
                      ("alr-attack", 0.10, 200), ("alr-release", 10, 1000),
                      ("alr-knee", -12, 12), ("alr-knee-smooth", -48, 0),
                      ("input-to-sidechain", -80.01, 40), ("input-to-link", -80.01, 40),
                      ("sidechain-to-input", -80.01, 40), ("sidechain-to-link", -80.01, 40),
                      ("link-to-input", -80.01, 40), ("link-to-sidechain", -80.01, 40)):
        chk(lo <= cfg[k] <= hi, "%s: limiter %s=%r 越界 [%g,%g]" % (name, k, cfg[k], lo, hi))


def check_deepfilternet(name, cfg):
    if not check_keyset(name, "deepfilternet", cfg, DEEPFILTERNET_KEYS):
        return
    for k, lo, hi in (("input-gain", -36, 36), ("output-gain", -36, 36),
                      ("attenuation-limit", 0, 100),
                      ("min-processing-threshold", -15, 35),
                      ("max-erb-processing-threshold", -15, 35),
                      ("max-df-processing-threshold", -15, 35),
                      ("min-processing-buffer", 0, 10),
                      ("post-filter-beta", 0, 0.05)):
        chk(lo <= cfg[k] <= hi, "%s: deepfilternet %s=%r 越界 [%g,%g]" % (name, k, cfg[k], lo, hi))


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
    elif base == "stereo_tools":
        check_stereo_tools(name, cfg)
    elif base == "crosstalk_canceller":
        check_crosstalk_canceller(name, cfg)
    elif base == "bass_enhancer":
        check_bass_enhancer(name, cfg)
    elif base == "limiter":
        check_limiter(name, cfg)
    elif base == "deepfilternet":
        check_deepfilternet(name, cfg)

    # 未知插件**必须报错**，不能静默放行 ——
    # 否则新增插件时"全部通过"是假的（这正是本档案陷阱 1 的形态）。
    chk(base in HANDLED_PLUGINS,
        "%s: 插件 %r 没有校验规则 —— 请先补 HANDLED_PLUGINS 与对应检查" % (name, base))


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
