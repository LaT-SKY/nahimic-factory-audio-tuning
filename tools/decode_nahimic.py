#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Decode the A-Volute / Nahimic factory speaker optimisation filter for
MECHREVO JIAOLONG (Conexant SN6140, SUBSYS 1D053010).

kSet_DeviceOptimizationFilterFL/FR hold the per-channel speaker correction
that the Windows driver applies. If this is a time-domain FIR (impulse
response) it can be loaded straight into EasyEffects' Convolver plugin, which
would reproduce the factory correction exactly instead of guessing at it.
"""

import base64
import glob
import os
import re
import struct
import sys
import xml.etree.ElementTree as ET

import numpy as np

BASE = os.environ.get("NAHIMIC_ARCHIVE", os.path.dirname(os.path.dirname(__file__)))
FILES = sorted(glob.glob(os.path.join(BASE, "Devices/1D053010_*_Speakers.nsx")))


def scalars(path):
    """All kSet_* settings that carry a Value attribute, plus blob entries."""
    root = ET.parse(path).getroot()
    out = {}
    for s in root.iter("Settings"):
        for child in s:
            tag = child.tag
            if "Value" in child.attrib:
                out[tag] = child.attrib["Value"]
            else:
                v = child.find("Value")
                if v is not None and v.text:
                    out[tag] = v.text.strip()
    data = {}
    for d in root.iter("Data"):
        for child in d:
            v = child.find("Value")
            if v is not None and v.text:
                data[child.tag] = v.text.strip()
    return out, data


def main():
    print("=" * 74)
    print("A-Volute / Nahimic factory speaker tuning -- MECHREVO (1D053010)")
    print("=" * 74)

    print(f"\nfound {len(FILES)} device files matching SUBSYS 1D053010:")
    for f in FILES:
        print(f"  {os.path.basename(f)}")

    # ---------------------------------------------------------- scalar dump
    s0, d0 = scalars(FILES[0])
    print(f"\nSMBIOS id            : {d0.get('SMBIOS')}")
    print(f"FormFactor           : {d0.get('FormFactor')}")
    print("\n--- scalar tuning parameters ---")
    for k in sorted(s0):
        if len(s0[k]) > 60:
            continue
        print(f"  {k:<46} = {s0[k]}")

    # ------------------------------------------- do the filters differ?
    print("\n--- DeviceOptimizationFilter blobs per file ---")
    blobs = {}
    for f in FILES:
        s, d = scalars(f)
        key = (os.path.basename(f), d.get("SMBIOS"))
        blobs[key] = (s.get("kSet_DeviceOptimizationFilterFL"),
                      s.get("kSet_DeviceOptimizationFilterFR"))
        fl, fr = blobs[key]
        same = "IDENTICAL" if fl == fr else "differ"
        print(f"  {key[1]}: FL={len(fl)} chars  FR={len(fr)} chars  L/R {same}")

    uniq = set()
    for fl, fr in blobs.values():
        uniq.add(fl)
        uniq.add(fr)
    print(f"  -> {len(uniq)} distinct filter blob(s) across all files")

    # ------------------------------------------------------- decode blobs
    fl_b64 = blobs[list(blobs)[0]][0]
    raw = base64.b64decode(fl_b64)
    print(f"\n--- decoding FL blob ---")
    print(f"  base64 chars : {len(fl_b64)}")
    print(f"  decoded bytes: {len(raw)}")
    print(f"  first 32 bytes: {raw[:32].hex()}")

    print("\n--- format candidates ---")
    n = len(raw)
    for name, dt, sz in (("float32", np.float32, 4), ("float64", np.float64, 8),
                         ("int16", np.int16, 2), ("int32", np.int32, 4)):
        if n % sz == 0:
            a = np.frombuffer(raw, dtype=dt)
            fin = np.isfinite(a).all() if dt in (np.float32, np.float64) else True
            print(f"  {name:<8} -> {len(a):>6} samples   all-finite={fin}")
            if dt in (np.float32, np.float64) and fin:
                print(f"             min={a.min():.6g} max={a.max():.6g} "
                      f"absmax={np.abs(a).max():.6g} rms={np.sqrt((a.astype(np.float64)**2).mean()):.6g}")

    # a real impulse response has its energy concentrated at the start
    print("\n--- impulse-response sanity check (float32) ---")
    if n % 4 == 0:
        a = np.frombuffer(raw, dtype=np.float32).astype(np.float64)
        e = a ** 2
        tot = e.sum()
        if tot > 0:
            c = np.cumsum(e) / tot
            i50 = int(np.searchsorted(c, 0.50))
            i90 = int(np.searchsorted(c, 0.90))
            print(f"  total energy      : {tot:.6g}")
            print(f"  50% energy within : first {i50} samples ({i50 / 48000 * 1000:.2f} ms @48k)")
            print(f"  90% energy within : first {i90} samples ({i90 / 48000 * 1000:.2f} ms @48k)")
            print(f"  peak sample index : {int(np.argmax(np.abs(a)))}")

    np.save("/tmp/nahimic_fl_raw.npy", np.frombuffer(raw, dtype=np.float32))
    print("\nsaved raw decode to /tmp/nahimic_fl_raw.npy")


if __name__ == "__main__":
    main()
