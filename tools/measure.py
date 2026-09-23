#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Laptop speaker frequency-response measurement via exponential sine sweep.

Method
------
Farina-style exponential sweep + regularised spectral inverse filter.

    IR = IFFT( FFT(recorded) * CONJ(FFT(sweep)) / (|FFT(sweep)|^2 + eps) )

The exponential sweep has a useful property: a harmonic distortion product of
order n generated at time t appears in the deconvolved impulse response at a
NEGATIVE time offset proportional to ln(n), i.e. *before* the linear-response
peak. So windowing tightly around the main peak rejects harmonic distortion and
gives the linear transfer function only. That matters here because the active
EasyEffects chain contains a Bass Enhancer, which deliberately generates
harmonics and would otherwise contaminate the measurement.

Only the magnitude response is used downstream, so absolute timing and the
loudspeaker->microphone delay do not need to be known in advance; the delay is
recovered from the IR peak position.
"""

import numpy as np
import wave
import os
import sys

SR = 48000


# ------------------------------------------------------------------ signals

def log_sweep(f1=20.0, f2=20000.0, T=7.0, sr=SR, fade=0.05):
    """Exponential (logarithmic) sine sweep, unit amplitude, faded ends."""
    L = np.log(f2 / f1)
    n = int(sr * T)
    t = np.arange(n) / sr
    x = np.sin(2.0 * np.pi * f1 * T / L * (np.exp(t * L / T) - 1.0))
    nf = int(sr * fade)
    if nf > 0:
        x[:nf] *= np.linspace(0.0, 1.0, nf)
        x[-nf:] *= np.linspace(1.0, 0.0, nf)
    return x


def inverse_filter(x, eps_db=-60.0):
    """Regularised spectral inverse of the sweep (Wiener-style)."""
    n = len(x)
    N = 1 << int(np.ceil(np.log2(n * 2)))
    X = np.fft.rfft(x, N)
    mag2 = np.abs(X) ** 2
    eps = mag2.max() * (10.0 ** (eps_db / 10.0))
    INV = np.conj(X) / (mag2 + eps)
    return INV, N


def deconvolve(rec, INV, N):
    """Convolve recording with the inverse filter -> impulse response."""
    Y = np.fft.rfft(rec, N)
    ir = np.fft.irfft(Y * INV, N)
    return ir


# ------------------------------------------------------------------ analysis

def ir_to_response(ir, peak, sr=SR, win_ms=25.0, pre_ms=1.0):
    """Window around the IR peak and return (freqs, magnitude_dB)."""
    npre = int(sr * pre_ms / 1000.0)
    npost = int(sr * win_ms / 1000.0)
    a = peak - npre
    b = peak + npost
    seg = np.zeros(b - a)
    lo, hi = max(a, 0), min(b, len(ir))
    seg[lo - a:hi - a] = ir[lo:hi]
    # Tukey-ish window: keep the onset intact, taper the tail
    w = np.ones(len(seg))
    nt = max(int(len(seg) * 0.5), 1)
    w[-nt:] = 0.5 * (1 + np.cos(np.pi * np.arange(nt) / nt))
    seg = seg * w
    N = 1 << 16
    H = np.fft.rfft(seg, N)
    f = np.fft.rfftfreq(N, 1.0 / sr)
    return f, 20.0 * np.log10(np.abs(H) + 1e-12)


def smooth_log(f, y, frac=24):
    """Fractional-octave smoothing on a log frequency grid."""
    out = np.empty_like(y)
    ratio = 2.0 ** (1.0 / (2 * frac))
    for i, fc in enumerate(f):
        if fc <= 0:
            out[i] = y[i]
            continue
        m = (f >= fc / ratio) & (f <= fc * ratio)
        out[i] = y[m].mean() if m.any() else y[i]
    return out


def find_peaks(f, y, fmin=150.0, fmax=12000.0, min_prom=2.0):
    """Local maxima on the smoothed curve with a minimum prominence (dB)."""
    m = (f >= fmin) & (f <= fmax)
    idx = np.where(m)[0]
    peaks = []
    for i in idx[1:-1]:
        if y[i] > y[i - 1] and y[i] >= y[i + 1]:
            # prominence: drop to the lower of the two surrounding minima
            l = i
            while l > idx[0] and y[l - 1] <= y[l]:
                l -= 1
            r = i
            while r < idx[-1] and y[r + 1] <= y[r]:
                r += 1
            prom = y[i] - max(y[l], y[r])
            if prom >= min_prom:
                peaks.append((f[i], y[i], prom))
    peaks.sort(key=lambda p: -p[2])
    return peaks


# ------------------------------------------------------------------ self test

def self_test():
    """Verify the pipeline recovers a known transfer function."""
    print("SELF-TEST: recovering a synthetic transfer function")
    sr = SR
    x = log_sweep(T=4.0)
    INV, N = inverse_filter(x)

    # synthetic "speaker": two resonances + a high-pass
    f = np.fft.rfftfreq(N, 1.0 / sr)
    H = np.ones_like(f, dtype=complex)
    for fc, Q, g in ((900.0, 6.0, 8.0), (2600.0, 9.0, 10.0)):
        s = 1j * f / fc
        H *= (1 + s / Q + s ** 2) / (1 + s / (Q * 10 ** (-g / 20)) + s ** 2)
    H *= (1j * f / 180.0) / (1 + 1j * f / 180.0)          # 180 Hz high-pass
    H[f < 20] = 0

    y = np.fft.irfft(np.fft.rfft(x, N) * H, N)[:len(x)]
    y = y / (np.abs(y).max() + 1e-12) * 0.5

    ir = deconvolve(y, INV, N)
    pk = int(np.argmax(np.abs(ir)))
    print(f"  IR peak at sample {pk} (expected ~{len(x) - 1})")

    fr, resp = ir_to_response(ir, pk)
    rs = smooth_log(fr, resp, frac=24)
    band = (fr > 200) & (fr < 10000)
    err = rs[band] - 20 * np.log10(np.abs(H[band]) + 1e-12)
    err -= np.median(err)          # ignore overall gain
    print(f"  residual error vs truth: rms={err.std():.2f} dB  max={np.abs(err).max():.2f} dB")
    ok = err.std() < 1.5
    print(f"  {'PASS' if ok else 'FAIL'} (rms < 1.5 dB)")
    return ok


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        sys.exit(0 if self_test() else 1)
    print(__doc__)
