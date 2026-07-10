#!/usr/bin/env python3
"""
Hardware-in-the-loop self-test: signal generator -> oscilloscope.

Wire ONE BK4055B output to ONE TekMSO24 input with a BNC, then run this. It
drives a sequence of waveforms on the sig gen and auto-verifies frequency,
amplitude and DC offset on the scope, printing PASS/FAIL for each.

    .venv/bin/python test_siggen_scope.py                 # sg CH1 -> scope CH1
    .venv/bin/python test_siggen_scope.py --sg 2 --scope 1
    .venv/bin/python test_siggen_scope.py --list          # just print the plan

The sig gen output LOAD is set to High-Z to match the scope's 1 MOhm input, so
the set Vpp equals what the scope sees. Requires the instruments to be powered
and enumerated (same as instruments.py). The sig gen output is turned OFF when
the run finishes.
"""
# Runnable from anywhere: put the repo root (one level up) on sys.path
# so the app modules import when this file is executed directly.
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))
import sys
import time
import argparse

from instruments import BK4055B, TekMSO24

# (label, waveform, freq_hz | None, amp_vpp, offset_v)
TESTS = [
    ("sine  1 kHz  2 Vpp",        "SINE",   1_000, 2.0, 0.0),
    ("sine  10 kHz 1 Vpp",        "SINE",  10_000, 1.0, 0.0),
    ("square 1 kHz 2 Vpp",        "SQUARE",  1_000, 2.0, 0.0),
    ("ramp  2 kHz  2 Vpp",        "RAMP",    2_000, 2.0, 0.0),
    ("sine  1 kHz  2 Vpp +0.5 V", "SINE",    1_000, 2.0, 0.5),
    ("DC  +1.0 V",                "DC",       None, 0.0, 1.0),
]

FREQ_TOL = 0.03      # 3 %
AMP_TOL = 0.12       # 12 % (scope amplitude vs. scaling/sampling)


def _within(meas, expected, frac, absol=0.0):
    if meas is None:
        return False
    return abs(meas - expected) <= max(abs(expected) * frac, absol)


def _fmt_hz(v):
    if v is None:
        return "--"
    if v >= 1e6:
        return f"{v / 1e6:.3f} MHz"
    if v >= 1e3:
        return f"{v / 1e3:.3f} kHz"
    return f"{v:.2f} Hz"


def configure_scope(scope, scope_ch, amp_vpp, offset_v, freq):
    """Scale the scope so the signal fills the screen and triggers cleanly."""
    peak = amp_vpp / 2.0 + abs(offset_v)
    vdiv = max(peak / 3.0, 0.005)        # peak ~3 of 4 divisions
    scope.set_vertical(scope_ch, scale=vdiv, position=0, coupling='DC')
    if freq:
        hdiv = max(3.0 / (freq * 10.0), 1e-9)   # ~3 periods across 10 divs
        scope.set_horizontal(hdiv)
        scope.set_trigger_edge(source=f'CH{scope_ch}', level=offset_v, slope='RISE')
    scope.run()


def measure(scope, scope_ch):
    """Read measurements, retrying once if the first acquisition is invalid."""
    for attempt in (1, 2):
        time.sleep(1.0)
        m = scope.get_all_measurements(scope_ch)
        if m.get('freq') is not None or m.get('pk2pk') is not None:
            return m
    return m


def run_case(sg, scope, sg_ch, scope_ch, label, wave, freq, amp, offset):
    if wave == 'DC':
        sg.set_basic_wave(sg_ch, WVTP='DC', OFST=offset)
    else:
        sg.set_basic_wave(sg_ch, WVTP=wave, FRQ=freq, AMP=amp, OFST=offset)
    sg.set_output(sg_ch, True)
    configure_scope(scope, scope_ch, amp, offset, freq)
    m = measure(scope, scope_ch)

    results = []   # (text, ok)
    if freq is not None:
        fm = m.get('freq')
        results.append((f"freq {_fmt_hz(fm)} (exp {_fmt_hz(freq)})",
                        _within(fm, freq, FREQ_TOL)))
        # Settled AMPLITUDE underreads ramps; PK2PK overreads edges/overshoot.
        # The true set Vpp should sit between them (within tolerance).
        lo, hi = m.get('amplitude'), m.get('pk2pk')
        amp_ok = (lo is not None and hi is not None
                  and lo * (1 - AMP_TOL) <= amp <= hi * (1 + AMP_TOL))
        lo_s = f"{lo:.2f}" if lo is not None else "--"
        hi_s = f"{hi:.2f}" if hi is not None else "--"
        results.append((f"Vpp {lo_s}-{hi_s} (exp {amp:.2f})", amp_ok))
    else:  # DC: no frequency, expect ~zero Vpp
        pp = m.get('pk2pk')
        results.append((f"Vpp {pp:.3f} (exp ~0)" if pp is not None else "Vpp --",
                        _within(pp, 0.0, 0.0, absol=0.1)))
    mn = m.get('mean')
    results.append((f"mean {mn:.3f} (exp {offset:.3f})" if mn is not None else "mean --",
                    _within(mn, offset, 0.0, absol=max(0.05, 0.05 * amp))))

    ok = all(r[1] for r in results)
    parts = [f"{txt}{'' if good else ' X'}" for txt, good in results]
    print(f"  {'PASS' if ok else 'FAIL'}  {label:<26}  " + "  ".join(parts))
    return ok


def main():
    ap = argparse.ArgumentParser(description="Sig gen -> scope self-test")
    ap.add_argument('--sg', type=int, default=1, choices=(1, 2),
                    help="BK4055B output channel that is wired (default 1)")
    ap.add_argument('--scope', type=int, default=1,
                    help="TekMSO24 input channel that is wired (default 1)")
    ap.add_argument('--list', action='store_true',
                    help="print the test plan and exit (no hardware)")
    args = ap.parse_args()

    if args.list:
        print(f"Plan ({len(TESTS)} cases), sig gen CH{args.sg} -> scope CH{args.scope}:")
        for label, wave, freq, amp, offset in TESTS:
            print(f"  - {label:<26} {wave} freq={_fmt_hz(freq)} "
                  f"amp={amp} Vpp offset={offset} V")
        return 0

    print("Connecting...")
    sg = BK4055B()
    scope = TekMSO24()
    print(f"  sig gen: {sg.idn}")
    print(f"  scope:   {scope.idn}")
    print(f"=== sig gen CH{args.sg} -> scope CH{args.scope} "
          f"(connect the BNC now if you haven't) ===")

    # Match the scope's 1 MOhm input so set Vpp == observed Vpp.
    sg.set_load_polarity(args.sg, load='HZ', polarity='NOR')
    scope.set_channel_enable(args.scope, True)

    passed = 0
    try:
        for label, wave, freq, amp, offset in TESTS:
            if run_case(sg, scope, args.sg, args.scope, label, wave, freq, amp, offset):
                passed += 1
    finally:
        sg.set_output(args.sg, False)
        sg.close()
        scope.close()

    print(f"\n{passed}/{len(TESTS)} passed.")
    return 0 if passed == len(TESTS) else 1


if __name__ == '__main__':
    sys.exit(main())
