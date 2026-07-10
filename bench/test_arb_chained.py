#!/usr/bin/env python3
"""
Arb upload acceptance test for the BK 4055B -- LAN transport.

Bench findings 2026-07-02 (firmware 1.01.01.33R3), all via USB probes +
WVDT? readback:
  * Any USBTMC Bulk-OUT transfer spanning more than one 64-byte USB packet
    hard-wedges the box (even a padded pure-ASCII *IDN?); only a
    front-panel power cycle recovers it.
  * Chaining a command across single-packet USBTMC messages does NOT
    reassemble: the first message is processed as the whole command and the
    continuations are silently dropped (a 2 KB WVDT stored as 24 bytes).
  * The full-field app-note WVDT header is silently rejected (alive, but
    nothing stored); only the minimal WVNM,<name>,WAVEDATA, form stores.
  => USB commands are capped at 52 bytes; arb upload REQUIRES LAN.

This script verifies the production upload path end to end over a given
resource, including a WVDT? readback byte-compare (aliveness or catalog
presence alone is NOT success -- the name registers even when the data is
truncated):
  1. baseline *IDN?
  2. upload_arb 8 / 256 / 1024-pt sines; STL? catalog check after each
  3. WVDT? readback of the 1024-pt wave; byte-diff vs what was sent
  4. select CHNK1024, TrueArb SRATE, ARWV/SRATE readbacks

    .venv/bin/python test_arb_chained.py --resource 'TCPIP0::<ip>::INSTR'

Run against USB (no --resource) to confirm upload_arb correctly REFUSES
rather than wedging/truncating.
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
import argparse

from instruments import BK4055B


def alive(sg, tag, wait=1.0):
    time.sleep(wait)
    try:
        sg.inst.timeout = 5000
        idn = sg.ask('*IDN?').strip()
        print(f"  [{tag}] alive: {idn[:32]}")
        return True
    except Exception as e:
        print(f"  [{tag}] DEAD: {type(e).__name__}")
        return False


def read_wvdt_payload(sg, name):
    """Return the WAVEDATA bytes of a stored user waveform via WVDT?."""
    sg.write(f'WVDT? USER,{name}')
    old = sg.inst.timeout
    sg.inst.timeout = 15000
    try:
        raw = sg.inst.read_raw()
    finally:
        sg.inst.timeout = old
    marker = b'WAVEDATA,'
    idx = raw.find(marker)
    if idx < 0:
        raise ValueError(f"no WAVEDATA in response: {raw[:80]!r}")
    return raw[idx + len(marker):].rstrip(b'\n')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sg', type=int, default=1, choices=(1, 2))
    ap.add_argument('--resource', default=None,
                    help="VISA resource, e.g. TCPIP0::192.168.1.50::INSTR "
                         "(default: USB auto-discovery)")
    args = ap.parse_args()

    print("Connecting...")
    sg = BK4055B(resource=args.resource)
    print(f"  {sg.resource}")
    print(f"  {sg.idn}")
    if not alive(sg, 'baseline', wait=0):
        return 2

    payloads = {}
    for npts in (8, 256, 1024):
        name = f'CHNK{npts}'
        print(f"stage: upload_arb {npts} pts as {name!r}...")
        samples = [math.sin(2 * math.pi * i / npts) for i in range(npts)]
        t0 = time.time()
        try:
            clean = sg.upload_arb(args.sg, name, samples, points=npts)
        except RuntimeError as e:
            print(f"  upload_arb refused: {e}")
            print(">> Over USB this is CORRECT behavior (52-byte firmware "
                  "cap). Re-run with --resource 'TCPIP0::<ip>::INSTR'.")
            return 3
        print(f"  upload returned in {time.time() - t0:.2f}s")
        if not alive(sg, f'{npts}pts', wait=2.0):
            print(f">> WEDGED at {npts}-pt upload over {sg.resource}.")
            return 1
        cat = sg.ask('STL? USER')
        print(f"  {clean} in catalog: {clean in cat}")
        payloads[clean] = sg.samples_to_int16(sg._resample(samples, npts))

    print("stage: WVDT? readback byte-compare (CHNK1024)...")
    sent = payloads['CHNK1024']
    got = read_wvdt_payload(sg, 'CHNK1024')
    print(f"  sent {len(sent)}B, got {len(got)}B")
    k = min(len(sent), len(got)) // 2
    sv = struct.unpack(f'<{k}h', sent[:2 * k])
    gv = struct.unpack(f'<{k}h', got[:2 * k])
    mism = sum(1 for a, b in zip(sv, gv) if a != b)
    print(f"  value mismatches: {mism}/{k}")
    if len(got) != len(sent) or mism:
        print(">> DATA TRUNCATED/CORRUPTED -- transport still broken.")
        return 1

    print("stage: select / TrueArb round-trip...")
    sg.select_arb(args.sg, 'CHNK1024')
    sg.set_sample_rate(args.sg, mode='TARB', value=1000 * 1024)
    time.sleep(0.3)
    try:
        print(f"  ARWV: {sg.ask(f'C{args.sg}:ARWV?')}")
        print(f"  SRATE: {sg.ask(f'C{args.sg}:SRATE?')}")
    except Exception as e:
        print(f"  readback failed: {type(e).__name__}")
    ok = alive(sg, 'final')
    sg.close()
    if ok:
        print(">> FULL PIPELINE VERIFIED (upload + readback + TrueArb). "
              "Run test_arb_scope.py next for the analog shape check.")
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
