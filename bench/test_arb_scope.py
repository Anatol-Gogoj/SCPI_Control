#!/usr/bin/env python3
"""
Hardware-in-the-loop self-test for ARBITRARY waveforms: sig gen -> scope.

Uploads complex waveforms (a two-tone and a multi-segment piecewise shape)
through the real arb pipeline, captures them on the scope, and verifies the
*shape* -- not just freq/Vpp/mean, which can't distinguish arbitrary signals.

Shape is checked two ways (see arb_compare):
  - cross-correlation r : phase-aligned time-domain match (>= 0.95 to pass)
  - harmonic distance d : FFT-magnitude (spectral) difference (smaller better)

Wire ONE sig gen output to ONE scope input, then:
    .venv/bin/python test_arb_scope.py --sg 2 --scope 1

The sig gen LOAD is set to High-Z (matches the scope's 1 MOhm input); the
output is turned off when the run finishes.
"""
# Runnable from anywhere: put the repo root (one level up) on sys.path
# so the app modules import when this file is executed directly.
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))
import sys
import math
import time
import struct
import re
import argparse

from instruments import BK4055B, TekMSO24
import arb_build as ab
import arb_compare as ac

FREQ = 1_000          # fundamental Hz
AMP_VPP = 2.0         # into High-Z
CORR_MIN = 0.95       # shape correlation to pass
HD_MAX = 0.25         # harmonic-profile distance, informational/secondary
AMP_TOL = 0.15
N_CMP = 256           # shape-comparison resolution
K_HARM = 8            # harmonics compared


def _norm_peak(s):
    peak = max(abs(x) for x in s) or 1.0
    return [x / peak for x in s]


def two_tone(n=1024):
    """Fundamental + 0.4 * 3rd harmonic -- a classic spectral test case."""
    return _norm_peak([math.sin(2 * math.pi * i / n)
                       + 0.4 * math.sin(2 * math.pi * 3 * i / n) for i in range(n)])


def piecewise():
    """A multi-segment arb (rise / hold / fall / sine-rippled return)."""
    recipe = {
        'total_points': 1024,
        'breakpoints': [[0, 0.0], [1, 0.8], [2, 0.8], [3, -0.8], [4, 0.0]],
        'segments': [{'type': 'LINE'}, {'type': 'HOLD'}, {'type': 'LINE'},
                     {'type': 'SINE', 'params': {'cycles': 3, 'amp': 0.4}}],
    }
    return _norm_peak(ab.render_recipe(recipe))


CASES = [
    ("two-tone f0 + 0.4*3f0", two_tone()),
    ("piecewise rise/hold/fall/sine", piecewise()),
]


def capture(scope, ch):
    """Scale the scope to ~4 periods, limit the record, and grab the trace."""
    peak = AMP_VPP / 2.0
    scope.set_vertical(ch, scale=max(peak / 3.0, 0.005), position=0, coupling='DC')
    scope.set_horizontal(max(4.0 / (FREQ * 10.0), 1e-9))   # ~4 periods over 10 div
    scope.set_trigger_edge(source=f'CH{ch}', level=0.0, slope='RISE')
    try:
        scope.write('HORIZONTAL:RECORDLENGTH 10000')
        rl = int(float(scope.ask('HORIZONTAL:RECORDLENGTH?')))
    except Exception:
        rl = 10000
    scope.write('DATA:START 1')
    scope.write(f'DATA:STOP {rl}')
    scope.run()
    time.sleep(1.5)
    return scope.get_waveform(ch)


def run_case(sg, scope, sg_ch, scope_ch, label, samples):
    # Short buffer + TrueArb: play npts points at FREQ*npts Sa/s so the output
    # period is 1/FREQ. Avoids the 32 KB DDS upload that wedges USBTMC (#20).
    npts = sg.ARB_DEFAULT_POINTS
    name = sg.upload_arb(sg_ch, label.split()[0], samples, points=npts,
                         freq_hz=FREQ, amp_vpp=AMP_VPP, offset_v=0.0)
    sg.select_arb(sg_ch, name)
    sg.set_sample_rate(sg_ch, mode='TARB', value=FREQ * npts)
    sg.set_basic_wave(sg_ch, AMP=AMP_VPP, OFST=0.0)
    sg.set_output(sg_ch, True)

    wf = capture(scope, scope_ch)
    spp = (1.0 / FREQ) / wf['dt'] if wf['dt'] else len(wf['v'])
    folded = ac.fold_average(wf['v'], spp, n_out=N_CMP, max_periods=4)
    expected = ac.resample(samples, N_CMP)

    r = ac.best_correlation(folded, expected)
    hd = ac.harmonic_distance(folded, expected, K_HARM)
    m = scope.get_all_measurements(scope_ch)
    lo, hi = m.get('amplitude'), m.get('pk2pk')
    amp_ok = (lo is not None and hi is not None
              and lo * (1 - AMP_TOL) <= AMP_VPP <= hi * (1 + AMP_TOL))
    mean_ok = m.get('mean') is not None and abs(m['mean']) <= 0.1

    ok = r >= CORR_MIN and amp_ok and mean_ok
    lo_s = f"{lo:.2f}" if lo is not None else "--"
    hi_s = f"{hi:.2f}" if hi is not None else "--"
    print(f"  {'PASS' if ok else 'FAIL'}  {label:<30}  "
          f"shape r={r:.3f}{'' if r >= CORR_MIN else ' X'}  "
          f"spectrum d={hd:.3f}  Vpp {lo_s}-{hi_s}{'' if amp_ok else ' X'}  "
          f"mean {m.get('mean'):.3f}")
    exp_h = [f"{x:.2f}" for x in ac.harmonic_profile(expected, 5)]
    got_h = [f"{x:.2f}" for x in ac.harmonic_profile(folded, 5)]
    print(f"        harmonics 1-5  expected [{', '.join(exp_h)}]  "
          f"measured [{', '.join(got_h)}]")
    return ok


def _read_wvdt(sg, name):
    """Query WVDT? USER,<name> and return (header_bytes, wavedata_bytes, length)."""
    sg.inst.timeout = 12000
    sg.write(f'WVDT? USER,{name}')
    raw = sg.read_raw()
    idx = raw.find(b'WAVEDATA,')
    while idx == -1:                        # ensure we have the header
        more = sg.read_raw()
        if not more:
            break
        raw += more
        idx = raw.find(b'WAVEDATA,')
    m = re.search(rb'LENGTH,(\d+)B', raw[:idx] if idx >= 0 else raw)
    length = int(m.group(1)) if m else None
    data = raw[idx + len(b'WAVEDATA,'):] if idx >= 0 else b''
    while length and len(data) < length:    # complete multi-chunk reads
        more = sg.read_raw()
        if not more:
            break
        data += more
    return (raw[:idx] if idx >= 0 else raw), (data[:length] if length else data), length


def readback_check(sg, ch):
    """Upload a full-scale sine, read it back, and diff bytes vs what we sent
    (and vs the known-good wave1). Tells us transport-corruption vs playback."""
    n = BK4055B.ARB_DEFAULT_POINTS          # short buffer = the path we use
    sent_samples = [math.sin(2 * math.pi * i / n) for i in range(n)]
    name = sg.upload_arb(ch, 'RBCHK', sent_samples, points=n)
    sent = BK4055B.samples_to_int16(BK4055B._resample(sent_samples, n))
    print(f"uploaded '{name}': {len(sent)} bytes; in STL? USER:",
          'RBCHK' in sg.ask('STL? USER'))

    hdr, got, length = _read_wvdt(sg, name)
    print(f"readback header: {hdr[:80]!r}")
    print(f"declared LENGTH={length}  got {len(got)} bytes  sent {len(sent)} bytes")
    k = min(len(sent), len(got)) // 2
    sv = struct.unpack(f'<{k}h', sent[:2 * k])
    gv = struct.unpack(f'<{k}h', got[:2 * k])
    mism = sum(1 for a, b in zip(sv, gv) if a != b)
    print(f"value mismatches: {mism}/{k}   "
          f"sent peak={max(abs(x) for x in sv)}  got peak={max(abs(x) for x in gv) if gv else 0}")
    print(f"first 8 sent: {sv[:8]}")
    print(f"first 8 got : {gv[:8]}")
    if mism == 0 and len(got) == len(sent):
        print(">> DATA INTACT -> problem is playback/format/amplitude, not transport")
    else:
        print(">> DATA DIFFERS -> upload transport is still corrupting the waveform")

    try:
        _h, g2, l2 = _read_wvdt(sg, 'wave1')
        v2 = struct.unpack(f'<{len(g2) // 2}h', g2[:len(g2) // 2 * 2])
        print(f"reference wave1: LENGTH={l2}  peak={max(abs(x) for x in v2) if v2 else 0}")
    except Exception as e:
        print('wave1 readback error:', type(e).__name__, e)


def main():
    ap = argparse.ArgumentParser(description="Arbitrary-waveform sig gen -> scope self-test")
    ap.add_argument('--sg', type=int, default=2, choices=(1, 2))
    ap.add_argument('--scope', type=int, default=1)
    ap.add_argument('--readback', action='store_true',
                    help="upload a sine, read it back with WVDT? and diff the "
                         "bytes (no scope) -- isolates transport vs playback")
    args = ap.parse_args()

    if args.readback:
        print("Connecting (sig gen only)...")
        sg = BK4055B()
        print(f"  sig gen: {sg.idn}")
        try:
            readback_check(sg, args.sg)
        finally:
            sg.close()
        return 0

    print("Connecting...")
    sg = BK4055B()
    scope = TekMSO24()
    print(f"  sig gen: {sg.idn}")
    print(f"  scope:   {scope.idn}")
    print(f"=== arb self-test: sig gen CH{args.sg} -> scope CH{args.scope} "
          f"@ {FREQ} Hz, {AMP_VPP} Vpp ===")

    sg.set_load_polarity(args.sg, load='HZ', polarity='NOR')
    scope.set_channel_enable(args.scope, True)

    passed = 0
    try:
        for label, samples in CASES:
            if run_case(sg, scope, args.sg, args.scope, label, samples):
                passed += 1
    finally:
        sg.set_output(args.sg, False)
        sg.close()
        scope.close()

    print(f"\n{passed}/{len(CASES)} passed.")
    return 0 if passed == len(CASES) else 1


if __name__ == '__main__':
    sys.exit(main())
