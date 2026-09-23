#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[已撤销 · 留档] Export the Nahimic / A-Volute HRIR set as EasyEffects `.irs`.

STATUS (2026-09-22)
-------------------
**B 阶段已撤销。** 生成的 6 个 `.irs` 与配套的 `耳机-Nahimic外化-*.json`
预设**已从 EasyEffects 目录删除**：实测效果不合格 —— 正前方向的 HRIR 左右耳
几乎相同（261 个样本里 260 个一致），听起来只是音色被染色，既不是虚拟环绕，
也不比直通更好。

脚本本身逻辑正确、可复现，保留是因为：

* 它是目前唯一能把这批 HRIR 喂进 EasyEffects 的途径（SOFA 路线走不通，
  见 tools/hrir_to_sofa.py 与 reverse-engineering-notes.md 陷阱 7）
* 若将来做真正的多声道双耳化（方案文档 §C1），这批核仍然可用

重新生成直接运行本脚本；预设另见 tools/build_headphone_presets.py。

Why this route instead of SOFA
------------------------------
EasyEffects' Convolver has a SOFA mode (sofa.azimuth/elevation/radius), and
tools/hrir_to_sofa.py writes valid AES69-2022 SOFA files with h5py.  But
libmysofa 1.3.5 -- the reader EasyEffects links against -- cannot read them:

    dataobject.c:1206: cannot read signature of data object      (superblock v0/1)
    OHDR: unsupported flags bit 4                                (custom attr phase change)
    OHDR unknown header message of type 6                        (compact links)

libmysofa ships **its own minimal HDF5 reader**.  It only supports HDF5
object header version 2, and it cannot follow the link-message / fractal-heap
layout that modern libhdf5 (>= 1.10) emits for a group.  Files written by the
old netCDF-4 / MATLAB SOFA API (e.g. libmysofa's own reference file
tests/LISTEN_1002_IRC_1002_C_HRIR.sofa) happen to use a layout it does read.

So the HRIRs are exported as plain `.irs` instead, which the Convolver reads
through libsndfile and which therefore always works.

The kernel written here is a **2-channel (stereo) WAV**: channel 0 = left-ear
HRIR, channel 1 = right-ear HRIR.  The Convolver then convolves input L with
the left-ear IR and input R with the right-ear IR -- exactly the mapping
EasyEffects performs for a single-emitter SOFA file
(see src/convolver_kernel_manager.cpp), so nothing is lost by going this way.

What the effect actually is: a fixed-direction binaural coloration, **not**
virtual surround -- see hrtf-format.md section 6.
"""

import argparse
import glob
import os
import re
import struct
import sys
import wave
from array import array

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ARCHIVE = os.path.dirname(HERE)
SRC = os.path.join(ARCHIVE, "data", "HRTF")
EE_IRS = os.path.expanduser(
    "~/.var/app/com.github.wwmm.easyeffects/data/easyeffects/irs")

GLOBAL_HDR = 400
RECORD = 2104
REC_HDR = 16
EAR_BYTES = 1044
TAPS = 261
AZIMUTH_STEP = 5
FS = 48000


def load_hex(path):
    raw = open(path, "rb").read()
    return bytes(int(t, 16) for t in re.findall(rb"0x([0-9A-Fa-f]{2})", raw))


def read_all(path):
    """Return ir[M, 2, N] float32 for one .hex file."""
    b = load_hex(path)
    m_count = (len(b) - GLOBAL_HDR) // RECORD
    ir = np.zeros((m_count, 2, TAPS), dtype=np.float32)
    for k in range(m_count):
        rec = b[GLOBAL_HDR + RECORD * k: GLOBAL_HDR + RECORD * (k + 1)]
        for ear in range(2):
            off = REC_HDR + ear * EAR_BYTES
            a = array("f")
            a.frombytes(rec[off:off + EAR_BYTES])
            ir[k, ear, :] = a
    return ir


def write_irs(path, left, right):
    """32-bit IEEE-float stereo WAV, the format EasyEffects itself writes."""
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(4)
        w.setframerate(FS)
        frames = bytearray()
        for l, r in zip(left, right):
            frames += struct.pack("<ff", float(l), float(r))
        w.writeframes(bytes(frames))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--azimuth", type=int, default=0,
                    help="方位角，0=正前 90=左 180=后 270=右（默认 0）")
    ap.add_argument("--all-azimuths", action="store_true",
                    help="导出全部 72 个方位（每个受试者 72 个文件）")
    ap.add_argument("--outdir", default=EE_IRS)
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(SRC, "Binaural_medium_LISTEN*_48000.hex")))
    if not files:
        sys.exit("在 %s 找不到 HRIR 文件" % SRC)
    os.makedirs(args.outdir, exist_ok=True)

    print("%-34s %s" % ("输出文件", "来源"))
    print("-" * 72)
    written = 0
    avg = None

    for path in files:
        m = re.search(r"LISTEN(\d+)", os.path.basename(path))
        if not m:
            continue
        subject = m.group(1)
        ir = read_all(path)
        avg = ir if avg is None else avg + ir

        if args.all_azimuths:
            for k in range(ir.shape[0]):
                name = "Nahimic-HRTF-L%s-az%03d" % (subject, k * AZIMUTH_STEP)
                write_irs(os.path.join(args.outdir, name + ".irs"),
                          ir[k, 0], ir[k, 1])
                written += 1
            print("%-34s 全部 72 个方位" % ("Nahimic-HRTF-L%s-az***" % subject))
        else:
            k = (args.azimuth % 360) // AZIMUTH_STEP
            name = "Nahimic-HRTF-L%s-az%03d" % (subject, k * AZIMUTH_STEP)
            write_irs(os.path.join(args.outdir, name + ".irs"),
                      ir[k, 0], ir[k, 1])
            written += 1
            print("%-34s LISTEN %s @ %d°" % (name, subject, k * AZIMUTH_STEP))

    if avg is not None and not args.all_azimuths:
        avg = avg / len(files)
        k = (args.azimuth % 360) // AZIMUTH_STEP
        name = "Nahimic-HRTF-average-az%03d" % (k * AZIMUTH_STEP)
        write_irs(os.path.join(args.outdir, name + ".irs"), avg[k, 0], avg[k, 1])
        written += 1
        print("%-34s 5 受试者平均" % name)

    print("\n写出 %d 个 .irs → %s" % (written, args.outdir))
    if not args.all_azimuths:
        print("\n在 EasyEffects 卷积器里把 Kernel 选成上面任一名字即可。")


if __name__ == "__main__":
    main()
