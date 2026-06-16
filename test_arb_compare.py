#!/usr/bin/env python3
"""Headless tests for arb_compare (no hardware).

Also demonstrates *why* shape comparison is needed: a sine and a square share
frequency, Vpp and mean, yet correlation and the harmonic spectrum separate
them clearly. Run: .venv/bin/python test_arb_compare.py
"""
import math
import random

import arb_compare as ac

N = 256


def _sine(n=N, cycles=1):
    return [math.sin(2 * math.pi * cycles * i / n) for i in range(n)]


def _square(n=N):
    return [1.0 if (i / n) < 0.5 else -1.0 for i in range(n)]


def _ramp(n=N):
    return [-1.0 + 2.0 * (i / n) for i in range(n)]


def _shift(s, k):
    return s[k:] + s[:k]


def _noisy(s, amp, seed=0):
    rng = random.Random(seed)
    return [x + rng.uniform(-amp, amp) for x in s]


def test_correlation_self_and_phase_invariance():
    s = _sine()
    assert ac.best_correlation(s, s) > 0.999
    assert ac.best_correlation(s, _shift(s, 73)) > 0.999       # any phase
    assert ac.best_correlation(s, [3 * x for x in s]) > 0.999  # amplitude-invariant


def test_correlation_survives_noise():
    s = _sine()
    assert ac.best_correlation(s, _noisy(s, 0.1, seed=1)) > 0.95


def test_scalars_match_but_shape_differs():
    # Sine and square: same fundamental, same +/-1 swing, zero mean -- the
    # scalar checks (freq/Vpp/mean) would all pass, but the shapes are not
    # the same signal. Correlation and spectrum must catch that.
    sine, square = _sine(), _square()
    assert abs(sum(sine) / N) < 1e-6 and abs(sum(square) / N) < 1e-6   # equal mean
    assert max(sine) == 1.0 and max(square) == 1.0                     # equal peak
    assert ac.best_correlation(sine, square) < 0.92                    # shape differs
    assert ac.harmonic_distance(sine, square) > 0.30                   # spectrum differs


def test_harmonic_profile_content():
    sine, square = _sine(), _square()
    hp_sine = ac.harmonic_profile(sine, 8)
    hp_square = ac.harmonic_profile(square, 8)
    # Sine: essentially all energy in the fundamental.
    assert hp_sine[0] > 0.99 and hp_sine[2] < 0.05
    # Square: strong odd harmonics (3rd ~ 1/3 of fundamental), even ~ 0.
    assert hp_square[2] > 0.25                 # 3rd harmonic present
    assert hp_square[1] < 0.05                 # 2nd (even) absent


def test_resample_and_fold():
    s = _sine(1000, cycles=1)
    r = ac.resample(s, 100)
    assert len(r) == 100
    assert ac.best_correlation(r, _sine(100)) > 0.999
    # fold 4 noisy periods back to one clean period
    multi = _noisy(_sine(4000, cycles=4), 0.05, seed=2)
    folded = ac.fold_average(multi, samples_per_period=1000.0, n_out=256)
    assert ac.best_correlation(folded, _sine(256)) > 0.97


def test_harmonic_distance_close_for_same_shape():
    s = _sine()
    assert ac.harmonic_distance(s, _noisy(s, 0.05, seed=3)) < 0.15
    assert ac.harmonic_distance(_ramp(), _ramp()) < 1e-9


if __name__ == '__main__':
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\nAll {len(tests)} arb_compare tests passed.")
