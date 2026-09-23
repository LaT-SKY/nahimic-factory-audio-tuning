#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Disassemble a window around an address in NahimicAPO4.dll and annotate any
string references, so the code's meaning can be inferred without symbols.

Why this is needed
------------------
The DLL has no symbol table and A-Volute's own classes carry no RTTI, so there
is no name-based route to a function. String literals in RT_CONST are reachable
via RIP-relative LEA from RT_CODE, and those references are enough to find
registration / configuration code.
"""

import re
import os
import struct
import subprocess
import sys

DLL = os.environ.get("NAHIMIC_DLL", "NahimicAPO4.dll")


def load():
    data = open(DLL, "rb").read()
    out = subprocess.run(["objdump", "-h", DLL], capture_output=True, text=True).stdout
    secs = []
    for line in out.splitlines():
        m = re.match(r"\s*\d+\s+(\S+)\s+([0-9a-f]+)\s+([0-9a-f]+)\s+([0-9a-f]+)\s+([0-9a-f]+)\s+([0-9a-f]+)", line)
        if m:
            n, s, v, l, o, a = m.groups()
            secs.append((n, int(s, 16), int(v, 16), int(o, 16)))
    return data, secs


def sec_of(secs, va):
    for n, s, v, o in secs:
        if v and v <= va < v + s:
            return n, o + (va - v)
    return None, None


def read_str(data, secs, va, maxlen=80):
    n, off = sec_of(secs, va)
    if off is None:
        return None
    raw = data[off:off + maxlen]
    end = raw.find(b"\x00")
    if end < 0:
        return None
    try:
        s = raw[:end].decode("ascii")
    except UnicodeDecodeError:
        return None
    return s if s and all(32 <= ord(c) < 127 for c in s) else None


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    start = int(sys.argv[1], 16)
    length = int(sys.argv[2], 16)
    data, secs = load()

    out = subprocess.run(
        ["objdump", "-d", "--start-address", hex(start),
         "--stop-address", hex(start + length), DLL],
        capture_output=True, text=True).stdout

    for line in out.splitlines():
        m = re.match(r"\s*([0-9a-f]+):\t([0-9a-f ]+)\t(.*)", line)
        if not m:
            if ">:" in line:
                print("\n" + line.strip())
            continue
        va = int(m.group(1), 16)
        raw = bytes.fromhex(m.group(2).replace(" ", ""))
        asm = m.group(3)

        note = ""
        # RIP-relative lea  -> annotate the string it points at
        if len(raw) >= 7 and raw[0] == 0x48 and raw[1] == 0x8D:
            disp = struct.unpack_from("<i", raw, 3)[0]
            tgt = va + 7 + disp
            s = read_str(data, secs, tgt)
            if s:
                note = f'   ; "{s}"'
            else:
                note = f"   ; -> 0x{tgt:x}"
        print(f"  {va:x}:  {asm}{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
