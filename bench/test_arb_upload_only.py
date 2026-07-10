#!/usr/bin/env python3
"""
Upload-only wedge-isolation test for the BK 4055B arb path.

Uploads short buffers at increasing point counts and checks the box is still
alive with *IDN? after EACH upload. Deliberately sends NO STL? / WVDT? query
-- the 2026-06-28 wedge happened right after `STL? USER`, so this isolates
whether the upload itself or the catalog query kills the USBTMC endpoint.

    .venv/bin/python test_arb_upload_only.py --sg 1 [--sizes 256 512 1024]

Safe to re-run; leaves the output OFF.
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
import argparse

from instruments import BK4055B


def alive(sg, tag):
    try:
        sg.inst.timeout = 5000
        idn = sg.ask('*IDN?').strip()
        print(f"  [{tag}] alive: {idn[:40]}")
        return True
    except Exception as e:
        print(f"  [{tag}] DEAD: {type(e).__name__}: {e}")
        return False


def main():
    ap = argparse.ArgumentParser(description="Arb upload-only wedge isolation")
    ap.add_argument('--sg', type=int, default=1, choices=(1, 2))
    ap.add_argument('--sizes', type=int, nargs='+', default=[256, 512, 1024])
    args = ap.parse_args()

    print("Connecting (sig gen only)...")
    sg = BK4055B()
    print(f"  sig gen: {sg.idn}")

    ok = alive(sg, 'pre')
    if not ok:
        print("Box not answering before any upload -- aborting.")
        return 2

    try:
        for n in args.sizes:
            samples = [math.sin(2 * math.pi * i / n) for i in range(n)]
            name = f'UPONLY{n}'
            print(f"uploading {n} pts ({2 * n} bytes) as '{name}' "
                  f"to CH{args.sg}...")
            t0 = time.time()
            sg.upload_arb(args.sg, name, samples, points=n)
            print(f"  upload returned in {time.time() - t0:.2f}s")
            time.sleep(1.0)                 # let firmware settle before query
            if not alive(sg, f'{n}pts'):
                print(f">> WEDGED after the {n}-pt upload itself "
                      f"(no catalog query was sent).")
                return 1
        print(">> ALL uploads survived; box answers *IDN? after each. "
              "The 06-28 wedge points at the STL?/WVDT? query path.")
        return 0
    finally:
        try:
            sg.close()
        except Exception:
            pass


if __name__ == '__main__':
    sys.exit(main())
