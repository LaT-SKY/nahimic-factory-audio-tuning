#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""

STATUS (2026-09-22) -- 留档，当前不可用
---------------------------------------
本脚本能写出结构完全合规的 SOFA 文件（h5dump 校验通过），**但 EasyEffects 读不了**：
它的读取端 libmysofa 自带极简 HDF5 读取器，只支持对象头 v2，又follow不了现代
libhdf5 的组链接布局。完整错误链见 tools/hrir_to_irs.py 与
reverse-engineering-notes.md 陷阱 7。保留备用 —— 若上游修好即可直接使用。

Convert the Nahimic / A-Volute horizontal-plane HRIR set into AES69-2015 SOFA
files that EasyEffects' Convolver can load.

Input : data/HRTF/Binaural_medium_LISTEN####_48000.hex
        (format reverse-engineered in hrtf-format.md)
Output: <easyeffects>/irs/Nahimic-HRTF-LISTEN####.sofa

Convention: SimpleFreeFieldHRIR
    Data.IR            (M, R, N) = (72, 2, 261) float64
    SourcePosition     (M, 3)    spherical: azimuth, elevation, radius
    Data.SamplingRate  48000 Hz

Azimuth mapping (verified in hrtf-format.md): record k -> 5 deg * k, with
0 deg = front, 90 deg = left, 180 deg = back, 270 deg = right -- the same
counter-clockwise convention SOFA uses, so no conversion is needed.

What EasyEffects actually does with this
----------------------------------------
From src/convolver_kernel_manager.cpp: for a single emitter it fills
channel_L with receiver 0 and channel_R with receiver 1, i.e. a plain stereo
kernel -- input L convolved with the left-ear HRIR, input R with the right-ear
HRIR, **both at the same chosen direction**.  So this is a fixed-direction
binaural coloration, not a spatialiser.  See hrtf-format.md section 6.
"""

import argparse
import datetime
import glob
import os
import re
import struct
import sys
from array import array

import h5py
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


# --------------------------------------------------------------------------- #
def load_hex(path):
    """ASCII '0xNN,' per line -> raw bytes (see hrtf-format.md)."""
    raw = open(path, "rb").read()
    return bytes(int(t, 16) for t in re.findall(rb"0x([0-9A-Fa-f]{2})", raw))


def read_hrirs(path):
    """Return (source_position[M,3], ir[M,2,N], delay[M,2])."""
    b = load_hex(path)
    if len(b) < GLOBAL_HDR or (len(b) - GLOBAL_HDR) % RECORD:
        sys.exit("%s: 长度不符合已知布局" % path)
    m_count = (len(b) - GLOBAL_HDR) // RECORD

    pos = np.zeros((m_count, 3))
    ir = np.zeros((m_count, 2, TAPS))
    delay = np.zeros((m_count, 2))

    for k in range(m_count):
        rec = b[GLOBAL_HDR + RECORD * k: GLOBAL_HDR + RECORD * (k + 1)]
        for ear in range(2):
            off = REC_HDR + ear * EAR_BYTES
            a = array("f")
            a.frombytes(rec[off:off + EAR_BYTES])
            ir[k, ear, :] = a
            # onset = first sample above the noise floor; that is the
            # propagation delay mysofa/EasyEffects report as Data.Delay
            idx = np.argmax(np.abs(ir[k, ear, :]) > 1e-3)
            delay[k, ear] = idx / FS

        # SOFA spherical: azimuth 0 = front, counter-clockwise (90 = left)
        pos[k] = (k * AZIMUTH_STEP, 0.0, 1.0)

    return pos, ir, delay


def put_str_attr(obj, name, value):
    """Write an ASCII string attribute the way libmysofa expects it.

    h5py's default (``obj.attrs[k] = np.bytes_(...)``) stores fixed-length
    strings with **NULLPAD** padding.  libmysofa's built-in HDF5 reader then
    compares the raw buffer against "SOFA" / "FIR" / ... and the comparison
    fails, so ``mysofa_load()`` returns MYSOFA_INVALID_FORMAT (10000) even
    though the file is perfectly valid HDF5.

    Every real SOFA file (and libmysofa's own reference file
    tests/LISTEN_1002_IRC_1002_C_HRIR.sofa) uses **NULLTERM** instead, so we
    build the datatype by hand.  Empty values use a null dataspace, which is
    also what the reference file does.
    """
    b = value.encode("ascii")
    n = len(b) + 1               # NULLTERM keeps the last byte for the NUL
    dt = h5py.h5t.C_S1.copy()
    dt.set_size(n)
    dt.set_strpad(h5py.h5t.STR_NULLTERM)
    dt.set_cset(h5py.h5t.CSET_ASCII)
    space = h5py.h5s.create(h5py.h5s.NULL if not b else h5py.h5s.SCALAR)
    aid = h5py.h5a.create(obj.id, name.encode("ascii"), dt, space)
    if b:
        aid.write(np.array(b, dtype="S%d" % n))


def write_sofa(path, pos, ir, delay, meta):
    m_count, r_count, n_taps = ir.shape
    with h5py.File(path, "w") as f:
        for key, val in (
                ("Conventions", "SOFA"),
                ("SOFAConventions", "SimpleFreeFieldHRIR"),
                ("SOFAConventionsVersion", "1.0"),
                ("Version", "2.0"),
                ("DataType", "FIR"),
                ("RoomType", "free field"),
                ("Title", meta["title"]),
                ("Origin", meta["origin"]),
                ("DatabaseName", meta["database"]),
                ("License", meta["license"]),
                ("ApplicationName", "hrir_to_sofa.py"),
                ("History", meta["history"]),
                ("References", meta["references"]),
                ("AuthorContact", meta["author"]),
                ("Organization", "A-Volute / IRCAM LISTEN"),
                ("Comment", meta["comment"]),
                ("DateCreated", datetime.datetime.now().astimezone().isoformat()),
                ("DateModified", datetime.datetime.now().astimezone().isoformat()),
        ):
            put_str_attr(f, key, val)

        # ---- HDF5 dimension scales -------------------------------------
        # libmysofa (the reader EasyEffects uses) does NOT use the system
        # HDF5 library -- it ships its own minimal reader and derives the
        # SOFA "DIMENSION_LIST" attribute from dimension scales.  It insists
        # on root datasets literally named I, C, R, E, N and M (see
        # src/hrtf/reader.c: dimensionflags must reach 0x3f), with I == 1
        # and C == 3.  Without them mysofa_load() fails with
        # MYSOFA_INVALID_FORMAT (10000).
        #
        # It also insists that each scale's NAME attribute be the netCDF-4
        # marker string with the size appended, right-aligned in 10 columns
        # ("This is a netCDF dimension but not a netCDF variable.         3").
        # A plain h5py make_scale() name is rejected by getDimension().
        # This was verified against libmysofa's own reference file
        # tests/LISTEN_1002_IRC_1002_C_HRIR.sofa.
        NETCDF_MARK = "This is a netCDF dimension but not a netCDF variable."
        scales = {}
        for nm, size in (("I", 1), ("C", 3), ("R", r_count),
                         ("E", 1), ("N", n_taps), ("M", m_count)):
            s = f.create_dataset(nm, data=np.zeros(size))
            s.make_scale(nm)
            del s.attrs["NAME"]          # drop h5py's own scale label
            put_str_attr(s, "NAME", "%s%10d" % (NETCDF_MARK, size))
            scales[nm] = s

        def ds(name, data, dims, **attrs):
            d = f.create_dataset(name, data=data, dtype="float64")
            for key, val in attrs.items():
                put_str_attr(d, key, val)
            for i, nm in enumerate(dims):
                d.dims[i].attach_scale(scales[nm])
                d.dims[i].label = nm
            return d

        # listener / receiver geometry, SOFA cartesian: x front, y left, z up
        ds("ListenerPosition", np.array([[0.0, 0.0, 0.0]]), "IC",
           Type="cartesian", Units="meter")
        ds("ListenerView", np.array([[1.0, 0.0, 0.0]]), "IC",
           Type="cartesian", Units="meter")
        ds("ListenerUp", np.array([[0.0, 0.0, 1.0]]), "IC",
           Type="cartesian", Units="meter")
        # receiver 0 = left ear (y > 0), receiver 1 = right ear; mysofa checks
        # this symmetry explicitly
        ds("ReceiverPosition",
           np.array([[[0.0, 0.09, 0.0]], [[0.0, -0.09, 0.0]]]), "RCI",
           Type="cartesian", Units="meter")
        ds("EmitterPosition", np.zeros((1, 3, 1)), "ECI",
           Type="cartesian", Units="meter")

        ds("SourcePosition", pos, "MC",
           Type="spherical", Units="degree, degree, meter")

        f.create_group("Data")
        ds("Data.IR", ir, "MRN", Units="1")
        ds("Data.SamplingRate", np.array([float(FS)]), "I", Units="hertz")
        ds("Data.Delay", delay, "MR", Units="second")

    return m_count, r_count, n_taps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", action="append",
                    help="LISTEN 受试者编号，可重复；默认全部")
    ap.add_argument("--outdir", default=EE_IRS)
    ap.add_argument("--no-average", action="store_true",
                    help="不额外生成 5 人平均的 SOFA")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(SRC, "Binaural_medium_LISTEN*_48000.hex")))
    if not files:
        sys.exit("在 %s 找不到 HRIR 文件" % SRC)

    os.makedirs(args.outdir, exist_ok=True)
    loaded = []
    print("%-14s %-34s %s" % ("受试者", "输出文件", "结构"))
    print("-" * 74)

    for path in files:
        m = re.search(r"LISTEN(\d+)", os.path.basename(path))
        if not m:
            continue
        subject = m.group(1)
        if args.subject and subject not in args.subject:
            continue
        pos, ir, delay = read_hrirs(path)
        loaded.append((subject, pos, ir, delay))

        out = os.path.join(args.outdir, "Nahimic-HRTF-LISTEN%s.sofa" % subject)
        meta = {
            "title": "Nahimic / A-Volute horizontal-plane binaural IR",
            "origin": "A-Volute Nahimic package, Assets/HRTF (see hrtf-format.md)",
            "database": "LISTEN (IRCAM/AKG) subject %s, repacked by A-Volute" % subject,
            "license": "Derived from the IRCAM LISTEN HRTF database; personal use only",
            "history": "read from %s by hrir_to_sofa.py" % os.path.basename(path),
            "references": "http://recherche.ircam.fr/equipes/salles/listen/",
            "author": "A-Volute (original), converted locally",
            "comment": "72 horizontal-plane directions, 5 deg step, 261 taps, "
                       "azimuth 0=front 90=left; converted from the .hex container",
        }
        mm, rr, nn = write_sofa(out, pos, ir, delay, meta)
        print("%-14s %-34s M=%d R=%d N=%d" % (subject, os.path.basename(out), mm, rr, nn))

    if loaded and not args.no_average:
        pos = loaded[0][1]
        ir = np.mean([x[2] for x in loaded], axis=0)
        delay = np.mean([x[3] for x in loaded], axis=0)
        out = os.path.join(args.outdir, "Nahimic-HRTF-average.sofa")
        meta = {
            "title": "Nahimic / A-Volute horizontal-plane binaural IR (5-subject average)",
            "origin": "A-Volute Nahimic package, Assets/HRTF (see hrtf-format.md)",
            "database": "mean of LISTEN subjects " + ", ".join(x[0] for x in loaded),
            "license": "Derived from the IRCAM LISTEN HRTF database; personal use only",
            "history": "averaged by hrir_to_sofa.py",
            "references": "http://recherche.ircam.fr/equipes/salles/listen/",
            "author": "A-Volute (original), converted locally",
            "comment": "arithmetic mean of the shipped LISTEN subjects; a generic "
                       "profile for listeners who do not match any single subject",
        }
        mm, rr, nn = write_sofa(out, pos, ir, delay, meta)
        print("%-14s %-34s M=%d R=%d N=%d" % ("平均", os.path.basename(out), mm, rr, nn))

    print("\n输出目录: %s" % args.outdir)


if __name__ == "__main__":
    main()
