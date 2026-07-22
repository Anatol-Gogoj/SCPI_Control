#!/usr/bin/env python3
"""HIL self-test: fancy arbs out of the SG (over LAN) -> scope (USB).

Verifies the whole LAN arb pipeline end to end: upload over the wire,
TrueArb playback, and a scope capture whose SHAPE matches what we sent
(normalised cross-correlation r ~ 1.0). Wire SG CH2 -> scope CH1 (or pass
--sg-ch/--scope-ch). Needs the sig gen on Ethernet (arb upload is LAN-only).

    .venv/bin/python bench/test_arb_scope_lan.py \\
        --resource TCPIP0::192.168.71.230::INSTR --sg-ch 2 --scope-ch 1

Verified 2026-07-22: ring-down / gaussian-doublet / synthetic-ECG all r>=0.999.
"""
# Runnable from anywhere: put the repo root (one level up) on sys.path.
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import argparse
import time

import numpy as np

from instruments import BK4055B, TekMSO24   # noqa: E402

SG_RES = 'TCPIP0::192.168.71.230::INSTR'   # LAN; arb upload is LAN-only
SG_CH = 2
SCOPE_CH = 1
FREQ = 1000.0        # Hz
AMP = 2.0            # Vpp
NPTS = 1024
OUT = '/tmp/lan_arb_selftest.png'


def _norm(x):
    x = np.asarray(x, float)
    x = x - x.mean()
    m = np.max(np.abs(x))
    return x / m if m else x


def make_arbs(n=NPTS):
    t = np.linspace(0, 1, n, endpoint=False)
    arbs = {}
    arbs['Ring-down'] = _norm(np.sin(2 * np.pi * 6 * t) * np.exp(-4 * t))
    c, w = 0.5, 0.05
    arbs['Gaussian doublet'] = _norm(-(t - c) / w * np.exp(-((t - c) / w) ** 2))

    def g(mu, s, a):
        return a * np.exp(-((t - mu) / s) ** 2)
    ecg = (g(0.20, 0.025, 0.15) - g(0.375, 0.008, 0.10) + g(0.40, 0.010, 1.0)
           - g(0.425, 0.010, 0.25) + g(0.62, 0.040, 0.30))
    arbs['Synthetic ECG'] = _norm(ecg)
    return t, arbs


def best_corr(sent, captured):
    """Max normalized circular cross-correlation of two 1-period shapes."""
    a = _norm(sent)
    m = len(a)
    # resample the captured period to match and normalize
    b = _norm(np.interp(np.linspace(0, 1, m, endpoint=False),
                        np.linspace(0, 1, len(captured), endpoint=False),
                        captured))
    fa = np.fft.rfft(a)
    fb = np.fft.rfft(b)
    xc = np.fft.irfft(fa * np.conj(fb), m)
    denom = np.sqrt((a * a).sum() * (b * b).sum())
    k = int(np.argmax(xc))
    return xc[k] / denom, np.roll(b, k)


def one_period(wf, period_s):
    """Pull one clean period out of a scope capture (start at a rising 0-x)."""
    t = np.asarray(wf['t'], float)
    v = np.asarray(wf['v'], float)
    v0 = v - v.mean()
    dt = wf['dt']
    npp = int(round(period_s / dt))
    # first rising zero-crossing with room for a full period after it
    for i in range(1, len(v0) - npp - 1):
        if v0[i - 1] <= 0 < v0[i]:
            return v0[i:i + npp]
    return v0[:npp]


def main():
    global SG_RES, SG_CH, SCOPE_CH, OUT
    ap = argparse.ArgumentParser()
    ap.add_argument('--resource', default=SG_RES)
    ap.add_argument('--sg-ch', type=int, default=SG_CH)
    ap.add_argument('--scope-ch', type=int, default=SCOPE_CH)
    ap.add_argument('--out', default=OUT)
    a = ap.parse_args()
    SG_RES, SG_CH, SCOPE_CH, OUT = a.resource, a.sg_ch, a.scope_ch, a.out
    t, arbs = make_arbs()
    sg = BK4055B(resource=SG_RES)
    scope = TekMSO24()
    results = {}
    try:
        print("SG :", sg.idn, "(LAN)")
        print("DSO:", scope.idn, "(USB)")
        sg.set_load_polarity(SG_CH, load='HZ', polarity='NOR')
        scope.set_channel_enable(SCOPE_CH, True)
        scope.set_vertical(SCOPE_CH, scale=AMP / 4.0, position=0,
                           coupling='DC')
        scope.set_horizontal(scale=0.2e-3)                  # 0.2 ms/div
        scope.set_trigger_edge(source=f'CH{SCOPE_CH}', level=0.0, slope='RISE')

        for name, samples in arbs.items():
            clean = sg.upload_arb(SG_CH, name.replace(' ', '')[:12],
                                  list(samples), points=NPTS,
                                  freq_hz=FREQ, amp_vpp=AMP, offset_v=0.0)
            sg.select_arb(SG_CH, clean)
            sg.set_sample_rate(SG_CH, mode='TARB', value=FREQ * NPTS)
            sg.set_basic_wave(SG_CH, AMP=AMP, OFST=0.0)
            sg.set_output(SG_CH, True)
            time.sleep(0.6)
            scope.run()
            time.sleep(0.5)
            meas = scope.get_all_measurements(SCOPE_CH)
            wf = scope.get_waveform(SCOPE_CH)
            per = one_period(wf, 1.0 / FREQ)
            corr, aligned = best_corr(samples, per)
            results[name] = dict(meas=meas, samples=samples, aligned=aligned,
                                 corr=corr)
            print(f"  {name:18s} freq={meas.get('freq')!s:>10}  "
                  f"pk2pk={meas.get('pk2pk')!s:>8}  shape r={corr:.3f}")
        sg.set_output(SG_CH, False)
    finally:
        sg.close()
        scope.close()

    # plot: sent vs captured, per arb
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(len(results), 1, figsize=(9, 2.5 * len(results)))
    if len(results) == 1:
        axes = [axes]
    for ax, (name, r) in zip(axes, results.items()):
        x = np.linspace(0, 1, len(r['samples']), endpoint=False)
        ax.plot(x, _norm(r['samples']), 'k-', lw=2, label='sent (arb)')
        ax.plot(x, r['aligned'], color='#1565c0', lw=1.2, alpha=0.8,
                label='captured (scope CH1)')
        f = r['meas'].get('freq')
        pp = r['meas'].get('pk2pk')
        fs = f"{f/1e3:.3f} kHz" if isinstance(f, (int, float)) else str(f)
        pps = f"{pp:.2f} V" if isinstance(pp, (int, float)) else str(pp)
        ax.set_title(f"{name}   —   meas {fs}, {pps} pk-pk, "
                     f"shape match r={r['corr']:.3f}", fontsize=10)
        ax.set_ylim(-1.3, 1.3)
        ax.set_yticks([])
        ax.legend(loc='upper right', fontsize=8)
    axes[-1].set_xlabel('one period (normalised)')
    fig.suptitle('LAN arb self-test: SG CH2 (arb-over-wire) → scope CH1',
                 fontsize=12, y=1.0)
    fig.tight_layout()
    fig.savefig(OUT, dpi=110)
    print("saved plot:", OUT)
    ok = all(r['corr'] > 0.9 for r in results.values())
    print("\nOVERALL:", "PASS (all shapes match r>0.9)" if ok
          else "review (some shapes below r=0.9)")


if __name__ == '__main__':
    main()
