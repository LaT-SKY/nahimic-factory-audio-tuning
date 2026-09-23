#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Decode the A-Volute "Horizontal plane generic binaural IR" files shipped with
Nahimic (Assets/HRTF/*.hex) into plain float32 HRIR pairs.

Format (reverse-engineered, see hrtf-format.md)
----------------------------------------------
    file   = 400-byte global header + 72 records x 2104 bytes
    record = 16-byte record header + 1044-byte left ear + 1044-byte right ear
    ear    = 261 x float32 little-endian @ 48 kHz  (~5.44 ms)

    record index k  ->  azimuth 5 deg * k on the horizontal plane

The .hex files are ASCII: one byte per line, written as "0xNN," (CRLF).

Usage
-----
    ./decode_hrtf.py info    data/HRTF/Binaural_medium_LISTEN1008_48000.hex
    ./decode_hrtf.py dump    <file> 90            # print one azimuth's IR
    ./decode_hrtf.py wav     <file> 90 out.wav    # export a stereo IR pair
    ./decode_hrtf.py all     <file> outdir        # export all 72 azimuths
"""

import array
import os
import re
import struct
import sys

GLOBAL_HDR = 400
RECORD = 2104
REC_HDR = 16
EAR_BYTES = 1044
EAR_TAPS = EAR_TAPS_N = 261
AZIMUTH_STEP = 5
SAMPLE_RATE = 48000


def load(path):
    """Read an ASCII-hex HRIR file and return the raw bytes."""
    raw = open(path, "rb").read()
    if raw[:2] != b"0x":  # already binary?
        return raw
    return bytes(int(t, 16) for t in re.findall(rb"0x([0-9A-Fa-f]{2})", raw))


def global_header(b):
    """Parse the 400-byte global header into a dict."""
    size = struct.unpack_from("<I", b, 0x08)[0]
    # UTF-16LE strings live from 0x74 up to 0x190, NUL-separated.
    blob = b[0x74:GLOBAL_HDR].decode("utf-16-le", errors="replace")
    # fields are NUL-separated, some contain embedded newlines, and the name is
    # stored twice back-to-back -> flatten then drop consecutive duplicates
    flat = [s for part in blob.split("\x00") for s in part.split("\n") if s.strip()]
    strings = [s for i, s in enumerate(flat) if i == 0 or s != flat[i - 1]]
    return {
        "file_size": size,
        "sample_rate": struct.unpack_from("<I", b, 0x30)[0],
        "channels": struct.unpack_from("<I", b, 0x34)[0],
        "strings": strings,
        "name": strings[0] if strings else "",
        "copyright": strings[1] if len(strings) > 1 else "",
        "version": strings[2] if len(strings) > 2 else "",
        "created": strings[3] if len(strings) > 3 else "",
    }


def records(b):
    """Yield (azimuth_deg, left, right) with left/right as array('f')."""
    n = (len(b) - GLOBAL_HDR) // RECORD
    for k in range(n):
        off = GLOBAL_HDR + RECORD * k
        rec = b[off:off + RECORD]
        left = array.array("f")
        left.frombytes(rec[REC_HDR:REC_HDR + EAR_BYTES])
        right = array.array("f")
        right.frombytes(rec[REC_HDR + EAR_BYTES:REC_HDR + 2 * EAR_BYTES])
        yield k * AZIMUTH_STEP, left, right


def onset(ir, thresh=1e-3):
    """First sample index whose magnitude exceeds thresh (the ITD delay)."""
    for i, x in enumerate(ir):
        if abs(x) > thresh:
            return i
    return -1


def write_wav(path, left, right, rate=SAMPLE_RATE):
    """Minimal 32-bit float stereo WAV writer (no dependencies)."""
    frames = len(left)
    data = bytearray()
    for l, r in zip(left, right):
        data += struct.pack("<ff", l, r)
    fmt = struct.pack("<HHIIHH", 3, 2, rate, rate * 8, 8, 32)  # 3 = IEEE float
    chunks = (b"fmt ", fmt, b"data", struct.pack("<I", len(data)) + bytes(data))
    body = b"".join(c if i % 2 else c + struct.pack("<I", len(chunks[i + 1]))
                    for i, c in enumerate(chunks))
    riff = b"WAVE" + body
    with open(path, "wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", len(riff)) + riff)


def cmd_info(path):
    b = load(path)
    h = global_header(b)
    print("file      : %s" % path)
    print("size      : %d bytes" % len(b))
    print("name      : %s" % h["name"])
    print("copyright : %s" % h["copyright"])
    print("version   : %s" % h["version"])
    print("created   : %s" % h["created"])
    print("rate/ch   : %d Hz / %d" % (h["sample_rate"], h["channels"]))
    print("records   : %d  (azimuth step %d deg)" % ((len(b) - GLOBAL_HDR) // RECORD,
                                                     AZIMUTH_STEP))
    print("per record: %d B header + 2 x %d B (%d float32 taps)"
          % (REC_HDR, EAR_BYTES, EAR_TAPS))
    print()
    print("azim   L-onset  R-onset   ITD   L-peak   R-peak")
    for az, l, r in records(b):
        if az % 45:
            continue
        lp = max(l, key=abs)
        rp = max(r, key=abs)
        print("%4d   %5d    %5d   %+5d   %+.4f  %+.4f"
              % (az, onset(l), onset(r), onset(r) - onset(l), lp, rp))


def cmd_dump(path, azimuth):
    b = load(path)
    for az, l, r in records(b):
        if az != azimuth % 360:
            continue
        print("azimuth %d deg" % az)
        print("  L onset %d, peak %+.4f, energy %.4f"
              % (onset(l), max(l, key=abs), sum(x * x for x in l)))
        print("  R onset %d, peak %+.4f, energy %.4f"
              % (onset(r), max(r, key=abs), sum(x * x for x in r)))
        print("  first 16 L:", " ".join("%+.4f" % x for x in l[:16]))
        print("  first 16 R:", " ".join("%+.4f" % x for x in r[:16]))
        return
    sys.exit("azimuth %d not found" % azimuth)


def cmd_wav(path, azimuth, out):
    b = load(path)
    for az, l, r in records(b):
        if az == azimuth % 360:
            write_wav(out, l, r)
            print("wrote %s (azimuth %d)" % (out, az))
            return
    sys.exit("azimuth %d not found" % azimuth)


def cmd_all(path, outdir):
    b = load(path)
    os.makedirs(outdir, exist_ok=True)
    for az, l, r in records(b):
        p = os.path.join(outdir, "az%03d.wav" % az)
        write_wav(p, l, r)
    print("wrote 72 WAVs to %s" % outdir)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "info":
        cmd_info(*args)
    elif cmd == "dump":
        cmd_dump(args[0], int(args[1]))
    elif cmd == "wav":
        cmd_wav(args[0], int(args[1]), args[2])
    elif cmd == "all":
        cmd_all(args[0], args[1])
    else:
        sys.exit(__doc__)
