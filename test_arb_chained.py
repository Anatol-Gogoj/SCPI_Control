#!/usr/bin/env python3
"""
Single-packet-per-message arb upload test for the BK 4055B.

Bench findings 2026-07-02: the 4055B firmware hard-wedges on ANY USBTMC
transfer that spans more than one 64-byte USB packet (even a padded *IDN?).
Every working command ever sent fits one packet. So: chunk large payloads
into USBTMC messages of <= 52 data bytes (12-byte header + 52 = exactly one
64-byte packet), EOM only on the last message -- spec-legal continuation,
and no transfer ever exceeds a single packet.

pyvisa-py already chunks at usb_send_ep.wMaxPacketSize *data* bytes per
message; temporarily lowering that attribute to 52 turns its stock writer
into exactly the framing we need.

Stages (aliveness-gated, stops at first wedge):
  1. baseline *IDN?
  2. padded 70-byte *IDN? via 52-byte chunking (2 single-packet messages)
  3. WVDT full header, 8 pts, single-packet chunked
  4. WVDT 256 pts, then 1024 pts
  5. STL? USER catalog, ARWV select, SRATE TARB round-trip

    .venv/bin/python test_arb_chained.py --sg 1
"""
import sys
import math
import time
import argparse
from contextlib import contextmanager

from instruments import BK4055B

PACKET = 64          # bulk endpoint wMaxPacketSize on this bench
HDR = 12             # USBTMC BulkOutMessage header size


@contextmanager
def one_packet_messages(sg):
    """Force pyvisa-py to emit single-packet USBTMC messages (<=52B data)."""
    sess = sg.inst.visalib.sessions[sg.inst.session]
    ep = sess.interface.usb_send_ep
    old = ep.wMaxPacketSize
    ep.wMaxPacketSize = PACKET - HDR
    try:
        yield
    finally:
        ep.wMaxPacketSize = old


def chunked_write_raw(sg, blob):
    with one_packet_messages(sg):
        sg.inst.write_raw(blob)


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


def upload(sg, ch, name, npts):
    samples = [math.sin(2 * math.pi * i / npts) for i in range(npts)]
    clean, blob = sg.build_wvdt(ch, name, samples, points=npts)
    old = sg.inst.timeout
    sg.inst.timeout = 30000
    t0 = time.time()
    try:
        chunked_write_raw(sg, blob)
    finally:
        sg.inst.timeout = old
    msgs = (len(blob) + PACKET - HDR - 1) // (PACKET - HDR)
    print(f"  sent {len(blob)}B as {msgs} single-packet messages "
          f"in {time.time() - t0:.2f}s")
    return clean


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sg', type=int, default=1, choices=(1, 2))
    args = ap.parse_args()

    print("Connecting...")
    sg = BK4055B()
    print(f"  {sg.idn}")
    if not alive(sg, 'stage1-baseline', wait=0):
        return 2

    print("stage 2: padded query via 52-byte chunking...")
    try:
        chunked_write_raw(sg, (' ' * 64 + '*IDN?\n').encode())
        sg.inst.timeout = 5000
        print(f"  response: {sg.inst.read().strip()[:32]}")
    except Exception as e:
        print(f"  padded query failed: {type(e).__name__}")
    if not alive(sg, 'stage2'):
        print(">> WEDGED even at one packet per message -> firmware cannot "
              "take multi-message commands either. USB path is hopeless; "
              "use LAN or a firmware update.")
        return 1

    for npts in (8, 256, 1024):
        print(f"stage: WVDT {npts} pts...")
        name = upload(sg, args.sg, f'CHNK{npts}', npts)
        if not alive(sg, f'{npts}pts', wait=2.0):
            print(f">> WEDGED at {npts}-pt upload (chunked). Uploads below "
                  f"this size survived -- note the boundary.")
            return 1

    print("stage 5: catalog / select / TrueArb round-trip...")
    try:
        cat = sg.ask('STL? USER')
        print(f"  STL? USER: {cat[:120]}")
    except Exception as e:
        print(f"  STL? failed: {type(e).__name__}")
    if not alive(sg, 'post-STL'):
        return 1
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
        print(">> FULL PIPELINE SURVIVED -- single-packet chunking is the "
              "transport fix. Run test_arb_scope.py --sg 1 --scope 1 next.")
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
