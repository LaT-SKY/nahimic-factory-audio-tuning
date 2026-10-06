#!/usr/bin/env python3
"""Resolve which Nahimic device profile applies to this machine -- deterministically.

Why this works
--------------
`NahimicPnPAPO4ConfiguratorDaemonModule.dll` contains the string:

    SELECT OEMStringArray FROM Win32_ComputerSystem

`Win32_ComputerSystem.OEMStringArray` *is* SMBIOS Type 11 (OEM Strings).  Every
`Devices/*.nsx` in `NH3CNXTProductSettings.cab` carries a `<Data>` block:

    <HWID>       <Value>SUBSYS_1D053010</Value>  </HWID>
    <FormFactor> <Value>Speakers</Value>         </FormFactor>
    <ID>         <Value>{753ed818-...}</Value>   </ID>
    <SMBIOS>     <Value>1D053010K5500103</Value> </SMBIOS>

so the configurator picks candidates by HWID + FormFactor and then matches the
machine's OEM string against `<SMBIOS>`.  No listening test, no sweep, no guess.

The `FT-NOTE` comment is NOT trustworthy: all seven `K5500101`..`K5500107`
files claim `SMBIOS-1D053010K5500103` in their comment regardless of their own
`<SMBIOS>` value.  Only `<SMBIOS>` counts.

Getting the table
-----------------
`/sys/firmware/dmi/tables/DMI`, `/sys/firmware/dmi/entries/11-0/raw` and
`/sys/firmware/dmi/entries/*` are all `-r-------- root`, and
`/sys/class/dmi/id/*` does not expose OEM strings at all.  Windows 11 also no
longer caches the table under `HKLM\\SYSTEM\\...\\mssmbios\\Data`.  Hence one
read-only privileged read is required:

    pkexec /usr/bin/cat /sys/firmware/dmi/tables/DMI > data/smbios/DMI.bin

Usage
-----
    python3 tools/read_smbios_oem.py data/smbios/DMI.bin
"""

import argparse
import glob
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("NAHIMIC_ARCHIVE", os.path.dirname(HERE))
DEVICES = os.environ.get("NAHIMIC_DEVICES", os.path.join(ROOT, "data", "Devices"))

TYPE_NAMES = {
    0: "BIOS", 1: "System", 2: "Baseboard", 3: "Chassis", 4: "Processor",
    11: "OEM Strings", 12: "System Config Options", 13: "BIOS Language",
}


def parse_smbios(data):
    """Yield (type, handle, formatted_bytes, [strings]) for every structure."""
    off = 0
    while off + 4 <= len(data):
        typ, length = data[off], data[off + 1]
        handle = struct.unpack_from("<H", data, off + 2)[0]
        if length < 4 or off + length > len(data):
            break
        fmt = data[off:off + length]
        p = off + length
        strings = []
        while p < len(data) and data[p] != 0:
            end = data.index(b"\x00", p)
            strings.append(data[p:end].decode("utf-8", "replace"))
            p = end + 1
        p += 1  # the second NUL terminates the structure
        yield typ, handle, fmt, strings
        if p <= off:
            break
        off = p


def oem_strings(data):
    out = []
    for typ, _handle, _fmt, strings in parse_smbios(data):
        if typ == 11:
            out.extend(strings)
    return out


def device_profiles():
    """{<SMBIOS> value: (filename, hwid, formfactor, guid)} from data/Devices/."""
    profiles = {}
    for path in sorted(glob.glob(os.path.join(DEVICES, "*.nsx"))):
        text = open(path, encoding="utf-8", errors="replace").read()
        def field(tag):
            m = re.search(r"<%s>\s*<Value>([^<]*)</Value>" % tag, text)
            return m.group(1).strip() if m else None
        key = field("SMBIOS")
        if key:
            profiles[key] = (os.path.basename(path), field("HWID"),
                             field("FormFactor"), field("ID"))
    return profiles


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dmi", nargs="?", default=os.path.join(ROOT, "data", "smbios", "DMI.bin"),
                    help="raw SMBIOS table dump (default: data/smbios/DMI.bin)")
    args = ap.parse_args()

    if not os.path.exists(args.dmi):
        sys.exit("找不到 %s -- 先用 pkexec 导出（见脚本头部说明）" % args.dmi)

    data = open(args.dmi, "rb").read()
    print("SMBIOS 表: %s (%d 字节)\n" % (args.dmi, len(data)))

    oems = oem_strings(data)
    print("Type 11 OEM Strings (%d 条):" % len(oems))
    for i, s in enumerate(oems, 1):
        mark = ""
        if re.fullmatch(r"[0-9A-F]{8}[A-Z0-9]{8}", s):
            mark = "   <-- 形如 SUBSYS+型号键（候选选择键）"
        print("  [%2d] %-24r%s" % (i, s, mark))

    profiles = device_profiles()
    print("\ndata/Devices/ 里的候选档案: %d 个" % len(profiles))
    for key in sorted(profiles):
        fn, hwid, ff, guid = profiles[key]
        print("  %-22s %-16s %-10s %s" % (key, hwid, ff, fn))

    # bind by HWID first (all of ours share SUBSYS_1D053010)
    oem_set = set(oems)
    hits = sorted(k for k in profiles if k in oem_set)

    print("\n=== 判定 ===")
    if not hits:
        print("❌ 没有任何候选的 <SMBIOS> 与本机 OEM String 匹配 -> 走引擎回退逻辑（新课题）")
        return 2

    if len(hits) > 1:
        print("⚠️ 命中多个（不该发生）: %s" % hits)

    for key in hits:
        fn, hwid, ff, guid = profiles[key]
        print("✅ 命中: %s" % key)
        print("   档案: %s" % fn)
        print("   HWID=%s  FormFactor=%s  ID=%s" % (hwid, ff, guid))
    print("\n结论：本机应使用命中档案的调校参数（变体判定完毕，无需扫频/听感）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())