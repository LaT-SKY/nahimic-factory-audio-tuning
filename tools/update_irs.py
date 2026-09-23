#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Install impulse responses into EasyEffects under a content-hashed name, and
repoint every Nahimic preset at the new name.

Why this exists
---------------
EasyEffects' Convolver only re-reads an `.irs` file when the **kernel-name
changes**.  Rewriting a file under the same name leaves the old impulse in
memory through a preset reload and even through a GUI re-pick -- so a corrected
FIR would silently keep using the old one.

Naming each impulse after a hash of its samples makes that impossible: new
content is always a new name, hence always a fresh read.

Second reason: EasyEffects watches its IR directory and rebuilds its list models
on every change, emitting row inserts inside a model reset -- which Qt forbids.
With the Convolver page open, a burst of changes segfaults it (reproduced on
8.2.8/8.2.9, upstream fix not yet released).  This tool therefore refuses to
write while EasyEffects is running unless --force is given.

Usage
-----
    # re-hash the impulse responses already installed and repoint presets
    ./update_irs.py

    # install a new FIR for a variant, then repoint presets
    ./update_irs.py --src new_fir.wav --variant X6DR57TK

    # see what would happen
    ./update_irs.py --dry-run
"""

import argparse
import glob
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import wave

EE = os.path.expanduser("~/.var/app/com.github.wwmm.easyeffects/data/easyeffects")
IRS_DIR = os.path.join(EE, "irs")
OUT_DIR = os.path.join(EE, "output")
IN_DIR = os.path.join(EE, "input")
PREFIX = "Nahimic-Factory-"
VARIANTS = ["X6DR57TK", "X6DR546K"]


def read_wav_samples(path):
    """Return (raw_bytes, n_channels, sample_rate) from a PCM-float WAV."""
    with wave.open(path, "rb") as w:
        if w.getsampwidth() != 4:
            raise SystemExit("%s: expected 32-bit samples, got %d bytes"
                             % (path, w.getsampwidth()))
        return w.readframes(w.getnframes()), w.getnchannels(), w.getframerate()


def content_hash(path):
    raw, _, _ = read_wav_samples(path)
    return hashlib.sha1(raw).hexdigest()[:8]


def hashed_name(variant, hash8):
    return "%s%s-%s" % (PREFIX, variant, hash8)


def easyeffects_running():
    try:
        out = subprocess.run(["pgrep", "-x", "easyeffects"],
                             capture_output=True, text=True)
        return out.returncode == 0
    except FileNotFoundError:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", help="new FIR (32-bit float WAV) to install")
    ap.add_argument("--variant", choices=VARIANTS,
                    help="which variant --src belongs to")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="write even while EasyEffects is running")
    args = ap.parse_args()

    if args.src and not args.variant:
        sys.exit("--src requires --variant")

    if easyeffects_running() and not args.force and not args.dry_run:
        sys.exit(
            "EasyEffects 正在运行。\n"
            "  它监听 IR 目录，批量改动 + 卷积器页面打开会让它段错误（8.2.8/8.2.9 已知 bug）。\n"
            "  请先关闭 EasyEffects（或切到别的页面并最小化），或加 --force 自行承担风险。")

    plan = {}          # variant -> new stem
    print("%-12s %-34s %s" % ("变体", "新 kernel-name", "来源"))
    print("-" * 78)
    for variant in VARIANTS:
        if args.src:
            if variant != args.variant:
                continue
            src = args.src
        else:
            src = os.path.join(IRS_DIR, PREFIX + variant + ".irs")
            if not os.path.isfile(src):
                cand = sorted(glob.glob(os.path.join(IRS_DIR, PREFIX + variant + "-*.irs")))
                if not cand:
                    print("%-12s %s" % (variant, "跳过（找不到 .irs）"))
                    continue
                src = cand[0]
        h = content_hash(src)
        stem = hashed_name(variant, h)
        plan[variant] = stem
        print("%-12s %-34s %s" % (variant, stem, os.path.basename(src)))

    if not plan:
        sys.exit("没有可处理的变体")

    if args.dry_run:
        print("\n--dry-run：未写入任何文件")
        return

    # 1. install impulse responses under the hashed name
    for variant, stem in plan.items():
        dst = os.path.join(IRS_DIR, stem + ".irs")
        src = args.src if args.src else os.path.join(IRS_DIR, PREFIX + variant + ".irs")
        if not os.path.isfile(src):
            src = sorted(glob.glob(os.path.join(IRS_DIR, PREFIX + variant + "-*.irs")))[0]
        if os.path.abspath(src) != os.path.abspath(dst):
            shutil.copy2(src, dst)
            print("写入 %s" % os.path.basename(dst))

    # 2. repoint every preset
    changed = 0
    for pattern in (os.path.join(OUT_DIR, "*.json"), os.path.join(IN_DIR, "*.json")):
        for path in glob.glob(pattern):
            try:
                doc = json.load(open(path, encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            section = "output" if "output" in doc else ("input" if "input" in doc else None)
            if section is None:
                continue
            body = doc[section]
            touched = False
            for pid, cfg in body.items():
                if not isinstance(cfg, dict) or "kernel-name" not in cfg:
                    continue
                cur = cfg["kernel-name"]
                for variant, stem in plan.items():
                    if cur == PREFIX + variant or cur.startswith(PREFIX + variant + "-"):
                        if cur != stem:
                            cfg["kernel-name"] = stem
                            touched = True
            if touched:
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(doc, fh, ensure_ascii=False, indent=4)
                changed += 1
    print("更新 %d 个预设的 kernel-name" % changed)

    # 3. drop stale hashed impulses (keep the un-hashed originals)
    for variant, stem in plan.items():
        for path in glob.glob(os.path.join(IRS_DIR, PREFIX + variant + "-*.irs")):
            if os.path.basename(path)[:-4] != stem:
                os.remove(path)
                print("清理旧文件 %s" % os.path.basename(path))


if __name__ == "__main__":
    main()
