#!/usr/bin/env python3
"""
Staged USB-transfer-layer probe for the BK 4055B WVDT wedge.

Every command this bench has ever verified fits in ONE 64-byte USB packet
(<= 52 payload bytes after the 12-byte USBTMC header). Every upload that
wedged the box -- minimal header, full header, 256..1024 pts -- is the only
kind of message LONGER than one packet. This probes whether the firmware
mishandles multi-packet / multi-message transfers at all, using harmless
ASCII queries before risking another WVDT.

Stages (aliveness-checked after each; stops at first wedge):
  1. baseline *IDN?
  2. padded *IDN? as ONE USBTMC message spanning 2 USB packets (oneshot)
  3. padded *IDN? as TWO chained USBTMC messages (plain write path)
  4. full-field WVDT, 256 pts, WITH trailing newline (last framing variant)

    .venv/bin/python test_arb_usb_probe.py --sg 1
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

PAD = ' ' * 64          # leading spaces are legal SCPI whitespace


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sg', type=int, default=1, choices=(1, 2))
    args = ap.parse_args()

    print("Connecting...")
    sg = BK4055B()
    print(f"  {sg.idn}")
    if not alive(sg, 'stage1-baseline', wait=0):
        return 2

    # Stage 2: one USBTMC message, two USB packets (padded query via oneshot)
    print("stage 2: 70-byte query as ONE message / 2 packets...")
    try:
        sg.write_raw_oneshot((PAD + '*IDN?\n').encode())
        sg.inst.timeout = 5000
        resp = sg.inst.read().strip()
        print(f"  got response: {resp[:32]}")
    except Exception as e:
        print(f"  padded query failed: {type(e).__name__}")
    if not alive(sg, 'stage2'):
        print(">> WEDGED by a multi-PACKET ASCII query -> USB transfer layer "
              "is broken for anything > 1 packet; WVDT is innocent. "
              "Pivot to kernel usbtmc driver or LAN.")
        return 1

    # Stage 3: same query via the PLAIN write path (chained USBTMC messages)
    print("stage 3: 70-byte query as chained messages (plain write)...")
    try:
        sg.write(PAD + '*IDN?')
        sg.inst.timeout = 5000
        resp = sg.inst.read().strip()
        print(f"  got response: {resp[:32]}")
    except Exception as e:
        print(f"  chained query failed: {type(e).__name__}")
    if not alive(sg, 'stage3'):
        print(">> WEDGED by chained USBTMC messages (EOM only on last).")
        return 1

    # Stage 4: WVDT full header + trailing newline, 256 pts
    print("stage 4: WVDT (full header) + trailing \\n, 256 pts...")
    n = 256
    samples = [math.sin(2 * math.pi * i / n) for i in range(n)]
    _, blob = sg.build_wvdt(args.sg, 'PROBE256', samples, points=n)
    old = sg.inst.timeout
    sg.inst.timeout = 20000
    try:
        sg.write_raw_oneshot(blob + b'\n')
    finally:
        sg.inst.timeout = old
    print("  upload returned")
    if not alive(sg, 'stage4', wait=2.0):
        print(">> WEDGED by WVDT itself even with trailing newline; "
              "ASCII multi-packet was fine -> firmware WVDT-over-USB bug. "
              "Pivot to kernel usbtmc driver or LAN.")
        return 1

    print(">> ALL STAGES SURVIVED. Check catalog:")
    try:
        cat = sg.ask('STL? USER')
        print("  STL? USER:", cat[:120])
    except Exception as e:
        print("  STL? failed:", type(e).__name__)
    alive(sg, 'post-STL')
    sg.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
