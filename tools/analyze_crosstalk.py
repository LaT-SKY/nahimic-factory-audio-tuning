#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Why the Crosstalk Canceller sounds wrong on laptop speakers -- computed, not guessed.

What the plugin actually is
---------------------------
`src/crosstalk_canceller.cpp` (EasyEffects v8.3.0) is **RACE** -- Recursive
Ambiophonic Crosstalk Elimination, by Antti S. Lankila.  It is *not* a simple
delayed-and-inverted feedforward canceller.  It is a **recursive** structure:

    phantom-center-only = true (what our presets use):
        M[n]  = L[n] + R[n]
        Mo[n] = M[n] - g * h[n-D]          # h = 6-biquad feedback filter
        out_L = (Mo + S)/2 ,  out_R = (Mo - S)/2   with S = L - R

    ⇒ Mo(z) = M(z) / (1 + g*H_fb(z)*z^-D)

so the mid channel gets a **feedback comb** 1/(1 + g*H_fb*z^-D), and the result
is re-mixed into L/R:

    L->L :  (H_mid + 1) / 2
    L->R :  (H_mid - 1) / 2

The feedback filter (from the header, coefficients as coded):
    f1 high-pass 250 Hz Q 0.710
    f2 low-pass  3245 Hz Q 0.710
    f3 peaking    688 Hz -2.90 dB Q 1.000
    f4 peaking   1066 Hz -6.40 dB Q 3.352
    f5 peaking   2190 Hz -5.60 dB Q 2.037
    f6 peaking   3792 Hz -4.50 dB Q 3.232

D = round(delay_us/1e6 * rate) samples -- 313 us @48k = 15 samples, i.e. the
comb spacing is 1/D = 3200 Hz, and the first quarter-cycle point is at
1/(4D) = 800 Hz.  That is exactly where the factory FIR carves its notch for the
cabinet resonance -- hence the "plastic" midrange the ear reports.

Usage
-----
    python3 tools/analyze_crosstalk.py            # table
    python3 tools/analyze_crosstalk.py --plot     # + data/crosstalk-analysis.png
"""

import argparse
import os
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EE = os.environ.get("EASYEFFECTS_DATA",
                          os.path.expanduser("~/.var/app/com.github.wwmm.easyeffects/data/easyeffects"))
IRS = os.path.join(EE, "irs", "Nahimic-Factory-X6DR57TK-c03355d3.irs")

RATE = 48000.0
KEY_FREQS = [100, 200, 250, 400, 500, 688, 800, 1000, 1066, 1597, 2190, 3195, 4000, 8000]


# --------------------------------------------------------------------- biquads
# 直接照抄 crosstalk_canceller.hpp 的系数公式（RBJ cookbook），保证与插件一致。
def _norm(b0, b1, b2, a0, a1, a2):
    return (b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0)


def hp(fc, q, fs=RATE):
    w0 = 2 * np.pi * fc / fs
    alpha = np.sin(w0) / (2 * q)
    return _norm((1 + np.cos(w0)) / 2, -(1 + np.cos(w0)), (1 + np.cos(w0)) / 2,
                 1 + alpha, -2 * np.cos(w0), 1 - alpha)


def lp(fc, q, fs=RATE):
    w0 = 2 * np.pi * fc / fs
    alpha = np.sin(w0) / (2 * q)
    return _norm((1 - np.cos(w0)) / 2, 1 - np.cos(w0), (1 - np.cos(w0)) / 2,
                 1 + alpha, -2 * np.cos(w0), 1 - alpha)


def pk(fc, gain_db, q, fs=RATE):
    w0 = 2 * np.pi * fc / fs
    A = 10 ** (gain_db / 40)
    alpha = np.sin(w0) / (2 * q)
    return _norm(1 + alpha * A, -2 * np.cos(w0), 1 - alpha * A,
                 1 + alpha / A, -2 * np.cos(w0), 1 - alpha / A)


def resp(coefs, f):
    b0, b1, b2, a1, a2 = coefs
    z = np.exp(-2j * np.pi * f / RATE)
    return (b0 + b1 * z + b2 * z ** 2) / (1 + a1 * z + a2 * z ** 2)


FEEDBACK_FILTER = [hp(250, 0.710), lp(3245, 0.710), pk(688, -2.9, 1.000),
                   pk(1066, -6.4, 3.352), pk(2190, -5.6, 2.037), pk(3792, -4.5, 3.232)]


def h_fb(f):
    h = np.ones_like(f, dtype=complex)
    for c in FEEDBACK_FILTER:
        h = h * resp(c, f)
    return h


def h_mid(f, decay_db, delay_us=313.0):
    """RACE 中频传函 1/(1 + g*H_fb*z^-D)。  [phantom-center-only = true]

    注意约定：下面 resp() 用的是 z = e^{-jw}，所以 **延迟 D 个采样 = z^(+D)**。
    这里曾把符号写反（写成 z^(-D)，即超前），被稳态正弦自检抓出来 —— 见 --self-test。
    """
    g = 10 ** (decay_db / 20)
    d = int(round(delay_us / 1e6 * RATE))
    z = np.exp(-2j * np.pi * f / RATE)
    return 1.0 / (1.0 + g * h_fb(f) * z ** d)


def h_lr(f, decay_db, delay_us=313.0):
    """[phantom-center-only = false] 即**真正的 RACE**：对 L/R 直接做递归交叉抵消。

        A = L - g*H*z^-D*B ;  B = R - g*H*z^-D*A
        ⇒ H_direct = 1/(1 - g²H²z^-2D)          （分母，可能 >1）
          H_cross  = -g*H*z^-D/(1 - g²H²z^-2D)  （**负号** = 反相，才是抵消）

    对比 phantom-center-only=true：那条路上交叉项是 (H_mid-1)/2，**同相相加**，
    并不是抵消信号 —— 这是两种模式听感差异的根源。
    """
    g = 10 ** (decay_db / 20)
    d = int(round(delay_us / 1e6 * RATE))
    z = np.exp(-2j * np.pi * f / RATE)
    denom = 1.0 - (g * h_fb(f) * z ** d) ** 2
    return 1.0 / denom, -(g * h_fb(f) * z ** d) / denom


def read_irs(path):
    """.irs 是 **PCM 32-bit 整数**（audioFormat=1）。读成 float32 会得到 NaN。"""
    with wave.open(path) as w:
        ch, n = w.getnchannels(), w.getnframes()
        raw = w.readframes(n)
    i4 = np.frombuffer(raw, dtype="<i4").astype(np.float64)
    if ch > 1:
        i4 = i4.reshape(-1, ch)
    return i4


def db(x):
    return 20 * np.log10(np.maximum(np.abs(x), 1e-12))


def self_test():
    """用稳态正弦（无 FFT 泄漏）仲裁解析模型 vs 逐样本仿真。

    这个自检抓到过一个真 bug：h_mid() 里延迟项符号写反（z^-D 在 z=e^{-jw}
    约定下等于超前）。修好后两者吻合到 0.003 dB。
    """
    g = 10 ** (-3.0 / 20)
    d = int(round(313e-6 * RATE))

    class BQ:
        def __init__(s, c):
            s.b0, s.b1, s.b2, s.a1, s.a2 = c
            s.x1 = s.x2 = s.y1 = s.y2 = 0.0

        def f(s, x):
            y = s.b0 * x + s.b1 * s.x1 + s.b2 * s.x2 - s.a1 * s.y1 - s.a2 * s.y2
            s.x2, s.x1, s.y2, s.y1 = s.x1, x, s.y1, y
            return y

    def steady(fq, ntot=1 << 17, skip=1 << 15):
        filt = [BQ(c) for c in FEEDBACK_FILTER]
        buf = np.zeros(d)
        idx = 0
        acc = 0j
        cnt = 0
        for n in range(ntot):
            left = np.sin(2 * np.pi * fq * n / RATE)
            mo = left - g * buf[idx]           # right = 0
            s = mo
            for flt in filt:
                s = flt.f(s)
            buf[idx] = s
            idx = (idx + 1) % d
            out_l = (mo + left) * 0.5          # side = left - right = left
            if n >= skip:
                acc += out_l * np.exp(-2j * np.pi * fq * n / RATE)
                cnt += 1
        return 2 * acc / cnt

    freqs = [100., 200., 400., 688., 800., 1000., 1066., 1597., 2190., 3195., 8000.]
    ana = (h_mid(np.array(freqs), -3.0) + 1) / 2
    worst = 0.0
    print("%9s %12s %14s %10s" % ("f(Hz)", "解析(dB)", "稳态仿真(dB)", "差(dB)"))
    for i, fq in enumerate(freqs):
        sim = db(steady(fq))
        a = db(ana[i])
        worst = max(worst, abs(sim - a))
        print("%9.0f %12.2f %14.2f %+10.3f" % (fq, a, sim, sim - a))
    print("\n最大偏差 %.3f dB -> %s" % (worst, "✅ 解析模型验证通过" if worst < 0.05 else "❌ 仍有偏差"))
    return worst < 0.05


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plot", action="store_true")
    ap.add_argument("--self-test", action="store_true",
                    help="仲裁解析模型 vs 逐样本仿真")
    args = ap.parse_args()

    if args.self_test:
        raise SystemExit(0 if self_test() else 1)

    # ---------------------------------------------------------- 1. 厂校 FIR
    irs = read_irs(IRS)
    left = irs[:, 0] / 2.0 ** 31
    right = irs[:, 1] / 2.0 ** 31
    print("脉冲响应 %s" % os.path.basename(IRS))
    print("  形状 %s  左/右是否相同: %s" % (irs.shape, np.array_equal(left, right)))
    taps = np.flatnonzero(np.abs(left) > 1e-6)
    print("  有效抽头 %d / %d，峰值 %.4f @ %d" % (len(taps), len(left), np.abs(left).max(), np.abs(left).argmax()))
    # 相邻抽头成对相同 -> 2x 复制（等价于再乘一个 [1,1]）
    dup = np.mean(left[0::2][:1024] == left[1::2][:1024])
    print("  偶奇抽头相同比例 %.2f（≈1 表示是 2× 复制出来的）" % dup)

    n = 1 << 15
    H = np.fft.rfft(left, n)
    f = np.fft.rfftfreq(n, 1 / RATE)

    # ------------------------------------------------- 2. RACE 反馈环稳定性
    fb = np.abs(h_fb(f))
    d = int(round(313e-6 * RATE))
    print("\nRACE 反馈滤波器 |H_fb|: 最大 %.3f @ %.0f Hz" % (fb.max(), f[fb.argmax()]))
    print("  延迟 D = %d 采样 = %.1f us -> 梳状间隔 1/D = %.0f Hz, 首个四分之一周期 %.0f Hz"
          % (d, 1e6 * d / RATE, RATE / d, RATE / (4 * d)))
    for decay in (-2.0, -3.0, 0.0):
        g = 10 ** (decay / 20)
        print("  decay-db=%+.1f (g=%.3f): 环路增益最大 %.3f  -> %s"
              % (decay, g, g * fb.max(), "稳定" if g * fb.max() < 1 else "**不稳定**"))

    # --------------------------------------------- 3. 对频响的实际影响
    print("\n%-9s %10s %10s %10s %10s %10s" %
          ("频率", "FIR(dB)", "H_direct", "FIR×dir", "偏差", "串到对侧"))
    print("-" * 78)
    rows = []
    for decay in (-3.0,):
        Hm = h_mid(f, decay)
        direct = (Hm + 1) / 2
        cross = (Hm - 1) / 2
        for fq in KEY_FREQS:
            i = np.argmin(np.abs(f - fq))
            a = db(H[i])
            dd = db(direct[i])
            tot = a + dd
            rows.append((fq, a, dd, tot, tot - a, db(cross[i])))
            print("%-9.0f %10.2f %10.2f %10.2f %+10.2f %10.2f" % rows[-1])

    # 800 Hz 凹陷深度对比
    Hm3 = h_mid(f, -3.0)
    band = (f > 700) & (f < 900)
    i800 = np.argmin(np.abs(f - 800))
    print("\n=== 关键：800 Hz 的箱体共振凹陷 ===")
    print("  FIR 单独            : %.2f dB" % db(H[i800]))
    print("  FIR × RACE(H_direct): %.2f dB" % (db(H[i800]) + db((Hm3[i800] + 1) / 2)))
    print("  -> 凹陷被填回 %.2f dB" % db((Hm3[i800] + 1) / 2))
    print("  RACE 在 700-900 Hz 的平均增益: %+.2f dB"
          % np.mean(db((Hm3[band] + 1) / 2)))
    band2 = (f > 250) & (f < 3245)
    print("  RACE 在 250-3245 Hz（反馈带内）的平均增益: %+.2f dB，峰 %+.2f dB @ %.0f Hz"
          % (np.mean(db((Hm3[band2] + 1) / 2)),
             np.max(db((Hm3[band2] + 1) / 2)),
             f[band2][np.argmax(db((Hm3[band2] + 1) / 2))]))

    # ------------------------------------- 4. 两种模式对比（关键）
    print("\n" + "=" * 82)
    print("两种模式对比：交叉项是「同相相加」还是「反相抵消」？")
    print("=" * 82)
    print("%-8s | %-30s | %-30s" % ("", "phantom-center-only = true", "= false（真正的 RACE）"))
    print("%-8s | %10s %10s %6s | %10s %10s %6s"
          % ("频率", "直达", "交叉", "相位", "直达", "交叉", "相位"))
    print("-" * 82)
    Hm = h_mid(f, -3.0)
    dirf, crsf = h_lr(f, -3.0)
    for fq in KEY_FREQS:
        i = np.argmin(np.abs(f - fq))
        d_t = (Hm[i] + 1) / 2
        c_t = (Hm[i] - 1) / 2
        ph_t = np.degrees(np.angle(c_t / d_t))
        ph_f = np.degrees(np.angle(crsf[i] / dirf[i]))
        print("%-8.0f | %10.2f %10.2f %5.0f° | %10.2f %10.2f %5.0f°"
              % (fq, db(d_t), db(c_t), ph_t, db(dirf[i]), db(crsf[i]), ph_f))
    print("\n读法：交叉项若与直达**同相**（0° 附近），它不是抵消信号，而是一个延迟 312 us 的副本")
    print("      —— 只会梳状滤波染色。真正的抵消要求交叉项**反相**（180° 附近）且幅度匹配真实声学串扰。")

    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        # 中文字形：系统有 Noto Sans CJK，不设就会出现豆腐块
        plt.rcParams["font.sans-serif"] = ["Noto Sans CJK JP", "Source Han Sans CN",
                                           "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False
        fig, ax = plt.subplots(4, 1, figsize=(11, 14), sharex=True)
        fm = f > 20
        ax[0].semilogx(f[fm], db(h_fb(f))[fm], label="|H_fb| 反馈滤波器")
        ax[0].axhline(0, color="k", lw=0.5)
        ax[0].set_title("RACE 反馈滤波器（6 个 biquad 级联，250 Hz 高通 ~ 3245 Hz 低通 + 4 个衰减峰）")
        ax[0].set_ylim(-40, 5)
        ax[0].set_ylabel("dB"); ax[0].grid(True, which="both", alpha=.3); ax[0].legend()

        for decay, style in ((-2.0, "--"), (-3.0, "-")):
            Hm = h_mid(f, decay)
            ax[1].semilogx(f[fm], db((Hm + 1) / 2)[fm], style,
                           label="decay=%+.0f dB" % decay)
        ax[1].axhline(0, color="k", lw=0.5)
        ax[1].axvline(800, color="r", lw=0.8, alpha=.6)
        ax[1].set_title("直达通路 H_direct —— 全程 ±2 dB，几乎透明")
        ax[1].set_ylabel("dB"); ax[1].grid(True, which="both", alpha=.3); ax[1].legend()

        # 关键：交叉项（这才是听感的来源）
        dirf, crsf = h_lr(f, -3.0)
        Hm = h_mid(f, -3.0)
        ax[2].semilogx(f[fm], db((Hm - 1) / 2)[fm], "-",
                       label="phantom-center-only = true（我们用的）")
        ax[2].semilogx(f[fm], db(crsf)[fm], "--", label="= false（真正的 RACE）")
        ax[2].axhline(0, color="k", lw=0.5)
        ax[2].axvspan(250, 3245, color="orange", alpha=.10)
        ax[2].set_title("注入对侧的交叉项 —— 250–3245 Hz（橙色带）内高达 -9 dB，"
                        "且只作用在中置声道上")
        ax[2].set_ylim(-35, 5)
        ax[2].set_ylabel("dB"); ax[2].grid(True, which="both", alpha=.3); ax[2].legend()

        ax[3].semilogx(f[fm], db(H)[fm], "k-", lw=1.0, label="厂校 FIR 单独（绝对 dB）")
        ax[3].semilogx(f[fm], (db(H) + db((Hm3 + 1) / 2))[fm], "r-", lw=1.2,
                       label="FIR × RACE(H_direct)")
        ax[3].axhline(0, color="k", lw=0.5)
        ax[3].axvline(800, color="r", lw=0.8, alpha=.6)
        # 曾被假设为"凹陷被填回"，实测只有 -0.12 dB —— 标注按实测写
        ax[3].annotate("800 Hz 箱体共振凹陷\nRACE 只改变了 %+.2f dB\n（凹陷保留，不是染色的来源）"
                       % db((Hm3[i800] + 1) / 2),
                       xy=(800, db(H[i800])), xytext=(1800, -20),
                       arrowprops=dict(arrowstyle="->", color="r"))
        ax[3].set_title("厂校 FIR vs 串上 RACE 之后 —— 直达通路几乎透明")
        ax[3].set_xlabel("Hz"); ax[3].set_ylabel("dB")
        ax[3].grid(True, which="both", alpha=.3); ax[3].legend()
        out = os.path.join(ROOT, "data", "crosstalk-analysis.png")
        fig.tight_layout(); fig.savefig(out, dpi=130)
        print("\n图已写入 %s" % os.path.relpath(out, ROOT))


if __name__ == "__main__":
    main()