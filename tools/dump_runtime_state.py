#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dump every on-disk place where Nahimic could persist runtime state.

Motivation
----------
`unexplored-inventory.md` assumed `Settings/settings.dat` would reveal the real
values of the 390 `kSet_*` keys that never appear in the factory XML.  This tool
walks *every* remaining candidate so that assumption can be settled instead of
guessed at.

What it reads (all read-only, from the mounted Windows partition)
----------------------------------------------------------------
  1. `.../Packages/A-Volute.Nahimic_*/Settings/settings.dat`   REGF hive
  2. `HKCU` (NTUSER.DAT)  -> Software\\A-Volute, Software\\Nahimic
  3. `HKLM\\SOFTWARE`     -> MMDevices\\Audio\\Render\\*\\FxProperties
                             (where APOs normally persist their parameters)
  4. `HKLM\\SYSTEM`       -> Enum\\SWD\\*AVOLUTE*  Device Parameters

Requires a registry parser.  A throw-away venv keeps it off the system:

    uv venv ~/.cache/nahimic-venv
    uv pip install --python ~/.cache/nahimic-venv/bin/python python-registry

Usage
-----
    ~/.cache/nahimic-venv/bin/python tools/dump_runtime_state.py
    # -> writes data/runtime-state/*.txt
"""

import glob
import os
import sys

try:
    from Registry import Registry
except ImportError:
    sys.exit("需要 python-registry：uv pip install --python <venv>/bin/python python-registry")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("NAHIMIC_ARCHIVE", os.path.dirname(HERE))
OUT = os.path.join(ROOT, "data", "runtime-state")

def _find_windows_mount():
    """Windows 分区挂载点：优先 NAHIMIC_WINDOWS_MOUNT，否则在常见挂载根下自动探测。

    刻意不写死 /mnt/windows —— 这是要发布到公开仓库的脚本，不预设任何机器布局。
    判据：该目录下存在 Windows/System32/config/SYSTEM。
    """
    env = os.environ.get("NAHIMIC_WINDOWS_MOUNT")
    if env:
        return env
    for base in ("/mnt", "/media", "/run/media", "/windows"):
        for cand in sorted(glob.glob(os.path.join(base, "*"))):
            if os.path.isfile(os.path.join(cand, "Windows/System32/config/SYSTEM")):
                return cand
    raise SystemExit(
        "找不到 Windows 分区。请设置 NAHIMIC_WINDOWS_MOUNT，例如：\n"
        "    export NAHIMIC_WINDOWS_MOUNT=/mnt/windows")


WIN = _find_windows_mount()


def _win_user_dir():
    """Windows 用户目录：优先环境变量，否则在 Users/ 下自动找带 NTUSER.DAT 的那个。

    刻意不写死用户名 —— 这是要发布到公开仓库的脚本。
    """
    env = os.environ.get("NAHIMIC_WINDOWS_USER_DIR")
    if env:
        return env
    for cand in sorted(glob.glob(os.path.join(WIN, "Users", "*"))):
        if os.path.isfile(os.path.join(cand, "NTUSER.DAT")):
            return cand
    return os.path.join(WIN, "Users", "Public")


WIN_USER = _win_user_dir()
NTUSER = os.path.join(WIN_USER, "NTUSER.DAT")
SOFTWARE = os.path.join(WIN, "Windows/System32/config/SOFTWARE")
SYSTEM = os.path.join(WIN, "Windows/System32/config/SYSTEM")

SETTINGS_DAT = glob.glob(os.path.join(
    WIN_USER, "AppData/Local/Packages/A-Volute.Nahimic_*/Settings/settings.dat"))


def render_value(v, limit=400):
    try:
        val = v.value()
    except Exception as exc:                                   # noqa: BLE001
        return "<err %s>" % exc
    s = repr(val)
    if len(s) > limit:
        s = s[:limit] + " ... <%d B>" % len(val)
    return s


def dump_key(lines, key, depth=0, max_depth=6):
    pad = "  " * depth
    lines.append("%s[%s]  values=%d subkeys=%d  mtime=%s"
                 % (pad, key.name(), len(key.values()), len(key.subkeys()),
                    key.timestamp()))
    for v in key.values():
        lines.append("%s  - %s [%s] = %s"
                     % (pad, v.name(), v.value_type_str(), render_value(v)))
    if depth < max_depth:
        for sub in key.subkeys():
            dump_key(lines, sub, depth + 1, max_depth)


def write(name, lines):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("写入 %s  (%d 行)" % (os.path.relpath(path, ROOT), len(lines)))


def part1_settings_dat():
    lines = ["# 1. Settings/settings.dat  (UWP LocalSettings 容器, REGF hive)",
             "#    档案曾假设它保存「当前生效的场景/EQ/虚拟环绕强度」—— 实测只有 2 个应用级值",
             ""]
    if not SETTINGS_DAT:
        lines.append("!! 找不到 settings.dat")
        return write("01-settings-dat.txt", lines)
    path = SETTINGS_DAT[0]
    lines.append("来源: %s" % path)
    lines.append("大小: %d B" % os.path.getsize(path))
    reg = Registry.Registry(path)
    dump_key(lines, reg.root())
    write("01-settings-dat.txt", lines)


def part2_hkcu():
    lines = ["# 2. HKCU (NTUSER.DAT) —— Software\\A-Volute 与 Software\\Nahimic",
             "#    内容为**守护模块登记表**（dll 路径 / 状态），无 DSP 参数",
             ""]
    reg = Registry.Registry(NTUSER)
    for path in (r"Software\A-Volute", r"Software\Nahimic"):
        lines.append("=" * 84)
        lines.append("PATH: %s" % path)
        try:
            dump_key(lines, reg.open(path), max_depth=3)
        except Exception as exc:                               # noqa: BLE001
            lines.append("  <打不开: %s>" % exc)
    write("02-hkcu-avolute.txt", lines)


def part3_fxproperties():
    lines = ["# 3. HKLM\\SOFTWARE\\...\\MMDevices\\Audio\\Render\\*",
             "#    FxProperties 是 APO 通常持久化参数的地方。实测：**只有 APO 注册信息**，没有 kSet_* 值。",
             "#    另外从端点的 Properties 里筛出提到 Nahimic / A-Volute 的条目 ——",
             "#    这是「本机音频链上到底哪个 APO 在实际生效」的直接证据。",
             ""]
    reg = Registry.Registry(SOFTWARE)
    base = r"Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render"

    lines.append("#" * 84)
    lines.append("# 3a. 生效的 APO 供应商（从 endpoint Properties 里筛）")
    lines.append("#" * 84)
    for ep in reg.open(base).subkeys():
        hit = []
        for sub in ep.subkeys():
            if sub.name().lower() != "properties":
                continue
            for v in sub.values():
                text = render_value(v, limit=200)
                if "nahimic" in text.lower() or "avolute" in text.lower() \
                        or "a-volute" in text.lower() or "senary" in text.lower():
                    hit.append("  %s = %s" % (v.name(), text))
        if hit:
            lines.append("ENDPOINT %s" % ep.name())
            lines.extend(hit)
            lines.append("")

    lines.append("#" * 84)
    lines.append("# 3b. FxProperties 全量（APO 注册）")
    lines.append("#" * 84)
    for ep in reg.open(base).subkeys():
        for sub in ep.subkeys():
            if sub.name().lower() != "fxproperties":
                continue
            lines.append("=" * 84)
            lines.append("ENDPOINT %s   mtime=%s" % (ep.name(), sub.timestamp()))
            dump_key(lines, sub, max_depth=1)
    write("03-apo-registration.txt", lines)


def part4_swd():
    lines = ["# 4. HKLM\\SYSTEM\\...\\Enum\\SWD\\*AVOLUTE* 与 Nahimic 设备项",
             "#    `[Device Parameters] values=0` ⇒ APO 没有参数存储区",
             ""]
    reg = Registry.Registry(SYSTEM)
    for cs in [k.name() for k in reg.root().subkeys()
               if k.name().startswith("ControlSet")]:
        base = cs + r"\Enum\SWD"
        try:
            top = reg.open(base)
        except Exception:                                      # noqa: BLE001
            continue
        for dev in top.subkeys():
            for inst in dev.subkeys():
                name = dev.name() + "\\" + inst.name()
                if not any(t in name.upper() for t in ("AVOLUTE", "NAHIMIC")):
                    continue
                lines.append("=" * 84)
                lines.append("%s\\%s" % (base, name))
                try:
                    dump_key(lines, inst, max_depth=2)
                except Exception as exc:                       # noqa: BLE001
                    lines.append("  <err %s>" % exc)
    write("04-apo-device-parameters.txt", lines)


if __name__ == "__main__":
    part1_settings_dat()
    part2_hkcu()
    part3_fxproperties()
    part4_swd()
    print("\n结论见 runtime-state.md")