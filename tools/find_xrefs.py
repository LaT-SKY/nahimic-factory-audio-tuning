#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Locate code that references a given string inside NahimicAPO4.dll.

The DLL has no symbol table and A-Volute's own classes carry no RTTI (only 50
RTTI descriptors exist and all are std/ATL), so neither names nor vtables can be
used to find a function. What IS present are stage-name string literals such as
"clarity_stage/clarity" in .rdata. On x86-64 those are reached through
RIP-relative LEA instructions, so scanning .text for a LEA whose target equals
the string's virtual address pinpoints the registration code.

Usage:  find_xrefs.py <string> [<string> ...]
"""

import re
import os
import struct
import subprocess
import sys

DLL = os.environ.get("NAHIMIC_DLL", "NahimicAPO4.dll")


def sections():
    out = subprocess.run(["objdump", "-h", DLL], capture_output=True, text=True).stdout
    secs = []
    for line in out.splitlines():
        m = re.match(r"\s*\d+\s+(\S+)\s+([0-9a-f]+)\s+([0-9a-f]+)\s+([0-9a-f]+)\s+([0-9a-f]+)\s+([0-9a-f]+)", line)
        if m:
            name, size, vma, lma, off, algn = m.groups()
            secs.append((name, int(size, 16), int(vma, 16), int(off, 16)))
    return secs


def image_base():
    out = subprocess.run(["objdump", "-p", DLL], capture_output=True, text=True).stdout
    m = re.search(r"ImageBase\s+([0-9a-f]+)", out)
    return int(m.group(1), 16) if m else 0x180000000


def vma_to_off(secs, vma):
    for name, size, svma, off in secs:
        if svma <= vma < svma + size and off:
            return off + (vma - svma)
    return None


def off_to_vma(secs, off):
    for name, size, svma, soff in secs:
        if soff and soff <= off < soff + size:
            return svma + (off - soff), name
    return None, None


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    data = open(DLL, "rb").read()
    secs = sections()
    base = image_base()
    print(f"ImageBase = 0x{base:x}   sections = {len(secs)}")

    text = next(s for s in secs if s[0] == ".text")
    tname, tsize, tvma, toff = text
    print(f".text: VMA 0x{tvma:x} size 0x{tsize:x} fileoff 0x{toff:x}")
    tbytes = data[toff:toff + tsize]

    for needle in sys.argv[1:]:
        print("\n" + "=" * 70)
        print(f"string: {needle!r}")
        print("=" * 70)
        hits = []
        start = 0
        nb = needle.encode()
        while True:
            i = data.find(nb, start)
            if i < 0:
                break
            hits.append(i)
            start = i + 1
        if not hits:
            print("  not found")
            continue
        for h in hits[:3]:
            svma, sname = off_to_vma(secs, h)
            if svma is None:
                continue
            target = svma
            print(f"  file offset 0x{h:x}  section {sname}  VA 0x{target:x}")

            # scan .text for LEA reg,[rip+disp32] resolving to target
            found = 0
            for m in re.finditer(rb"\x48\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]", tbytes):
                pos = m.start()
                disp = struct.unpack_from("<i", tbytes, pos + 3)[0]
                insn_va = tvma + pos
                if insn_va + 7 + disp == target:
                    print(f"    xref at .text+0x{pos:x}  (VA 0x{insn_va:x})")
                    found += 1
                    if found >= 6:
                        break
            if not found:
                print("    no RIP-relative LEA xref found in .text")
    return 0


if __name__ == "__main__":
    sys.exit(main())
