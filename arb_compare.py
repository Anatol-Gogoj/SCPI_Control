#!/usr/bin/env python3
"""
Waveform-shape comparison for the arb self-test (pure Python, no numpy).

For arbitrary waveforms, frequency + Vpp + mean are not enough -- many shapes
share them. These helpers compare the *shape*:

- best_correlation(): phase-aligned Pearson r between two one-period shapes
  (1.0 = identical shape; phase/trigger offset is searched over circular lags).
- harmonic_profile() / harmonic_distance(): DFT magnitudes at the first K
  harmonics, scale-normalized -- a spectral (FFT) comparison. Phase-blind, so
  it complements correlation rather than replacing it.

All comparisons are amplitude- and phase-invariant (shape only); amplitude is
checked separately on the scope.
"""
import math
import cmath


def resample(samples, n):
    """Linearly resample a sequence to n points."""
    m = len(samples)
    if m == 0:
        return [0.0] * n
    if m == 1:
        return [float(samples[0])] * n
    out = []
    for i in range(n):
        x = i * (m - 1) / (n - 1) if n > 1 else 0.0
        lo = int(x)
        frac = x - lo
        hi = min(lo + 1, m - 1)
        out.append(samples[lo] * (1 - frac) + samples[hi] * frac)
    return out


def _interp(samples, x):
    """Linear interpolation at float index x (clamped to the ends)."""
    n = len(samples)
    if x <= 0:
        return samples[0]
    if x >= n - 1:
        return samples[-1]
    i = int(x)
    frac = x - i
    return samples[i] * (1 - frac) + samples[i + 1] * frac


def fold_average(v, samples_per_period, n_out=256, max_periods=8):
    """Fold a multi-period capture into one averaged period of n_out points.

    Averaging over whole periods suppresses noise. samples_per_period may be
    fractional. Returns a list of length n_out.
    """
    n = len(v)
    spp = float(samples_per_period)
    if spp <= 1 or n < spp:
        return resample(v, n_out)
    periods = max(1, min(int(n // spp), max_periods))
    out = []
    for i in range(n_out):
        phase = i / n_out
        acc = 0.0
        cnt = 0
        for m in range(periods):
            x = (m + phase) * spp
            if x <= n - 1:
                acc += _interp(v, x)
                cnt += 1
        out.append(acc / cnt if cnt else 0.0)
    return out


def normalize_shape(s):
    """Remove DC and scale to unit RMS (shape-only, amplitude-invariant)."""
    m = sum(s) / len(s)
    centered = [x - m for x in s]
    rms = math.sqrt(sum(x * x for x in centered) / len(centered))
    if rms == 0:
        return centered
    return [x / rms for x in centered]


def best_correlation(a, b):
    """Max Pearson correlation of a vs b over all circular shifts of b.

    Inputs need not be pre-normalized. Returns r in [-1, 1]; ~1.0 means the
    shapes match (any phase). a and b must be the same length.
    """
    a = normalize_shape(a)
    b = normalize_shape(b)
    n = len(a)
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    best = -1.0
    for lag in range(n):
        dot = 0.0
        for i in range(n):
            dot += a[i] * b[i - lag]   # negative index = circular wrap
        r = dot / (na * nb)
        if r > best:
            best = r
    return best


def harmonics(s, k_max):
    """Amplitude of the first k_max harmonics via direct DFT (DC removed)."""
    n = len(s)
    m = sum(s) / n
    centered = [x - m for x in s]
    out = []
    for k in range(1, k_max + 1):
        acc = 0j
        for i, val in enumerate(centered):
            acc += val * cmath.exp(-2j * math.pi * k * i / n)
        out.append(abs(acc) * 2.0 / n)
    return out


def harmonic_profile(s, k_max=8):
    """Harmonic magnitudes normalized to unit L2 (scale-invariant spectrum)."""
    h = harmonics(s, k_max)
    norm = math.sqrt(sum(x * x for x in h)) or 1.0
    return [x / norm for x in h]


def harmonic_distance(a, b, k_max=8):
    """Euclidean distance between two harmonic profiles (0 = identical spectra)."""
    pa = harmonic_profile(a, k_max)
    pb = harmonic_profile(b, k_max)
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(pa, pb)))
