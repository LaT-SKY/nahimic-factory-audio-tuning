#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build speaker-side spatial-effect presets (Nahimic Movie / Gaming analogue).

Scope
-----
用户已明确：**研究仅限笔记本内置扬声器**。 所以这里不碰 HRTF / 双耳化
（那是耳机技术 —— 见 research-roadmap.md §8.3），只做扬声器侧的空间感。

What Nahimic actually does on speakers
--------------------------------------
`data/AudioProfiles/*.nsx` 里，只有 Movie / Gaming 开了空间效果：

    kSet_SpkVirtualSurroundState   1     ← 扬声器虚拟环绕
    kSet_StereoWideningState       1     ← 立体声扩展
    kSet_UpmixState                1     ← 上混
    kSet_ReverbState               1     ← 混响（已实现）
    kSet_ReverbGainDB          -10.0

而且在引擎的 **461 个键里，这三个只有 State、没有任何强度参数**
（见 data/dll-analysis/NahimicAPO4API-keys.txt）—— 强度是引擎内固定实现。
所以我们无法"标定到某个数值"，只能给出**分级试听集**由耳朵定夺。

How each is reproduced here
---------------------------
| Nahimic                   | EasyEffects                 | 本文档 |
|---------------------------|-----------------------------|--------|
| `StereoWideningState`     | `stereo_tools`（Calf LV2）  | ✅ 用 `stereo-base` |
| `SpkVirtualSurroundState` | `crosstalk_canceller`（EE 原生） | ⚠️ 原理对位，但见下 |
| `UpmixState`              | —                           | ❌ EE 无上混器；没有下游虚拟化器时上混也无意义 |
| `ReverbState`             | `reverb`                    | ✅ 已在基础预设里 |

⚠️ 关于串扰抵消的物理前提：笔记本扬声器间距约 15–20 cm、听距约 50 cm，
张角只有 ±10° 上下，而串扰抵消对头部位置极其敏感。它**可能**让声场外扩，
也**可能**发虚、染色。这正是要你试听而不是我替你决定的原因。

Key names
---------
**不靠记忆**，取自上游 v8.3.0 的序列化代码（写错键名会被静默忽略 ——
见 reverse-engineering-notes.md 陷阱 1）：

  src/crosstalk_canceller_preset.cpp   -> bypass, input-gain, output-gain,
                                          phantom-center-only, delay-us, decay-db
  src/stereo_tools_preset.cpp          -> bypass, input-gain, output-gain,
                                          balance-in, balance-out, softclip,
                                          mutel, muter, phasel, phaser, mode,
                                          side-level, side-balance,
                                          middle-level, middle-panorama,
                                          stereo-base, delay, sc-level,
                                          stereo-phase, dry, wet

`mode` 是**标签字符串**（`UPDATE_ENUM_LIKE_PROPERTY`），合法值取自
`easyeffects_db_stereo_tools.kcfg` 的 `modeLabels`。

Usage
-----
    python3 tools/build_spatial_presets.py
    python3 tools/validate_presets.py
"""

import glob
import json
import os

EE = os.environ.get("EASYEFFECTS_DATA",
                          os.path.expanduser("~/.var/app/com.github.wwmm.easyeffects/data/easyeffects"))
OUT_DIR = os.path.join(EE, "output")
VARIANT = "X6DR57TK"          # 2026-10-06 已定案，见 research-roadmap.md §8.1

# 只处理出厂就开了空间效果的场景（Music / Communication 全关）
# 但用户 2026-10-06 明确要求给「音乐」也加上**扩展**（试听后觉得效果好）。
# 这是**对出厂设置的主动偏离**，不是复现 —— 所以只给音乐加"加宽"，
# 不加"串扰"（串扰在扬声器上已被证伪，见 crosstalk-analysis.md）。
SCENARIOS = {
    "电影": ["空间-加宽弱", "空间-加宽强", "空间-串扰", "空间-综合"],
    "游戏": ["空间-加宽弱", "空间-加宽强", "空间-串扰", "空间-综合"],
    "音乐": ["空间-加宽弱", "空间-加宽强"],      # ← 出厂为关，属新增
}

# `mode` 的合法标签（kcfg modeLabels 解码 &gt; 之后）
MODE_LR_LR = "LR > LR (Stereo Default)"

SPATIAL_ORDER_ANCHOR = ("convolver#0", "equalizer#0")   # 插在它们之后


def stereo_tools(stereo_base, output_gain=0.0):
    """Calf Stereo Tools —— 用 stereo-base 做立体声扩展。其余全为默认值。"""
    return {
        "bypass": False,
        "input-gain": 0.0,
        "output-gain": output_gain,
        "balance-in": 0.0,
        "balance-out": 0.0,
        "softclip": False,
        "mutel": False,
        "muter": False,
        "phasel": False,
        "phaser": False,
        "mode": MODE_LR_LR,
        "side-level": 0.0,
        "side-balance": 0.0,
        "middle-level": 0.0,
        "middle-panorama": 0.0,
        "stereo-base": stereo_base,
        "delay": 0.0,
        "sc-level": 1.0,
        "stereo-phase": 0.0,
        "dry": -100.0,
        "wet": 0.0,
    }


def crosstalk_canceller(delay_us=313.0, decay_db=-3.0, phantom_center_only=True):
    """EE 原生串扰抵消。默认值 delay 313 us / decay -2 dB，这里 decay 略深一档。"""
    return {
        "bypass": False,
        "input-gain": 0.0,
        "output-gain": 0.0,
        "phantom-center-only": phantom_center_only,
        "delay-us": delay_us,
        "decay-db": decay_db,
    }


# 变体表：名字 -> [(插件 id, 配置)]，按顺序插入
VARIANTS = {
    "空间-加宽弱": [("stereo_tools#0", stereo_tools(0.25))],
    "空间-加宽强": [("stereo_tools#0", stereo_tools(0.60))],
    "空间-串扰":   [("crosstalk_canceller#0", crosstalk_canceller())],
    "空间-综合":   [("stereo_tools#0", stereo_tools(0.35)),
                    ("crosstalk_canceller#0", crosstalk_canceller())],
}


def insert_after(order, anchors, new_ids):
    """把 new_ids 插到 anchors 中最后一个出现位置之后；都没有就插到最前。"""
    pos = 0
    for i, pid in enumerate(order):
        if pid in anchors:
            pos = i + 1
    return order[:pos] + list(new_ids) + order[pos:]


def main():
    written = []
    for scenario, allowed in SCENARIOS.items():
        src = os.path.join(OUT_DIR, "笔记本扬声器-Nahimic%s-%s.json" % (scenario, VARIANT))
        if not os.path.isfile(src):
            print("跳过 %s（找不到基底预设 %s）" % (scenario, os.path.basename(src)))
            continue

        base = json.load(open(src, encoding="utf-8"))

        for name, plugins in VARIANTS.items():
            if name not in allowed:
                continue
            doc = json.loads(json.dumps(base))          # 深拷贝
            body = doc["output"]
            ids = [pid for pid, _ in plugins]

            # 已经是空间预设就不再叠（幂等）
            if any(pid in body for pid in ids):
                print("跳过 %s-%s（已存在）" % (scenario, name))
                continue

            for pid, cfg in plugins:
                body[pid] = cfg
            body["plugins_order"] = insert_after(body["plugins_order"],
                                                 SPATIAL_ORDER_ANCHOR, ids)

            dst = os.path.join(OUT_DIR, "笔记本扬声器-Nahimic%s-%s-%s.json"
                               % (scenario, name, VARIANT))
            with open(dst, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, ensure_ascii=False, indent=4)
            written.append((os.path.basename(dst), " -> ".join(body["plugins_order"])))

    print("写出 %d 个空间预设：\n" % len(written))
    for fn, chain in written:
        print("  %s" % fn)
        print("      %s" % chain)

    print("""
试听建议
--------
1. 先只比 `-空间-加宽弱` / `-空间-加宽强` —— 判断「声场是否真的变宽、有没有发虚」
2. 再单独比 `-空间-串扰` —— 判断串扰抵消在这个几何条件下是否可用
3. 最后听 `-空间-综合`
4. 挑中之后删掉其余，并把选中的那个改名去掉"空间-"前缀

强度调整（只有一个数）
----------------------
`stereo_tools#0` 的 `stereo-base`：0 = 不变，越大越宽（合法范围 -1 … 1）。
若觉得变宽后整体偏响或偶有削顶，给同一块的 `output-gain` 填一个负值（如 -2.0）即可。
""")


if __name__ == "__main__":
    main()