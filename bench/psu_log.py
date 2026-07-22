#!/usr/bin/env python3
"""Read-only CSV logger for the BK9174B DC supply (Phase-1 bring-up tool).

Polls the set voltage plus measured voltage/current on one or both channels
every --interval seconds and appends applied-V / measured-A / calculated-power
rows to a CSV. It sends ONLY queries (VOLT?, MEAS:VOLT?, MEAS:CURR?) and a
channel-select -- it never changes voltage, current, or output state, so it is
safe to run against a channel that is powering a live load.

    python bench/psu_log.py --channels 1 --interval 1 --out psu_ch1.csv
    python bench/psu_log.py --channels 1,2 --duration 600 --out run.csv

Ctrl-C stops cleanly and closes the file. Before trusting a long unattended
run, confirm the SCPI tokens in instruments.BK9174B match this unit (a quick
`*IDN?` + a MEAS:VOLT? that reads back the known output voltage is enough).
"""
import argparse
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from instruments import BK9174B          # noqa: E402
from psu_logger import PsuCsvLogger       # noqa: E402


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--port', default=BK9174B.DEFAULT_PORT,
                    help=f'serial port (default {BK9174B.DEFAULT_PORT})')
    ap.add_argument('--baud', type=int, default=BK9174B.DEFAULT_BAUD)
    ap.add_argument('--channels', default='1',
                    help='comma list, e.g. "1" or "1,2" (default 1)')
    ap.add_argument('--interval', type=float, default=1.0,
                    help='seconds between sample rounds (default 1.0)')
    ap.add_argument('--duration', type=float, default=None,
                    help='stop after N seconds (default: run until Ctrl-C)')
    ap.add_argument('--out', default='psu_log.csv', help='CSV output path')
    args = ap.parse_args()

    channels = [int(c) for c in args.channels.split(',') if c.strip()]
    for ch in channels:
        if ch not in BK9174B.CHANNELS:
            ap.error(f"channel {ch} not in {BK9174B.CHANNELS}")

    psu = BK9174B(port=args.port, baud=args.baud)
    print(f"Connected on {args.port} @ {args.baud} baud")
    print(f"  *IDN? -> {psu.idn or '(no reply -- check baud/tokens)'}")
    print(f"Logging channel(s) {channels} every {args.interval}s -> {args.out}")
    print("  read-only: no V/I/output changes.  Ctrl-C to stop.\n")

    t0 = time.monotonic()
    n = 0
    try:
        with PsuCsvLogger(args.out) as logger:
            while True:
                for ch in channels:
                    reading = psu.read_channel(ch)
                    elapsed = time.monotonic() - t0
                    ts = datetime.now(timezone.utc).astimezone().isoformat(
                        timespec='milliseconds')
                    row = logger.log(ts, elapsed, reading)
                    print(f"  t={row['elapsed_s']:8.3f}s  CH{ch}  "
                          f"set={row['set_voltage_v']:6.3f} V  "
                          f"meas={row['meas_voltage_v']:6.3f} V  "
                          f"{row['meas_current_a']:8.5f} A  "
                          f"{row['power_w']:8.4f} W")
                    n += 1
                if args.duration is not None \
                        and (time.monotonic() - t0) >= args.duration:
                    break
                time.sleep(max(0.0, args.interval))
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        psu.close()
    print(f"\nWrote {n} row(s) to {args.out}")


if __name__ == '__main__':
    main()
