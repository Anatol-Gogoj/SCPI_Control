#!/usr/bin/env python3
"""CSV data-logger for the BK9174B DC supply.

Turns a stream of per-channel readings (from BK9174B.read_channel) into a
timestamped CSV of applied voltage, measured current, and calculated power.
Kept transport-free and free of any clock call so it is fully unit-testable
without hardware -- the caller supplies the ISO timestamp and elapsed time.

Columns::

    timestamp_iso     wall-clock ISO-8601 of the sample (caller supplies)
    elapsed_s         seconds since logging started
    channel           1 or 2
    set_voltage_v     programmed voltage (VOLT?)
    meas_voltage_v    measured output voltage (MEAS:VOLT?)
    meas_current_a    measured output current (MEAS:CURR?)
    power_w           calculated P = meas_voltage_v * meas_current_a

Every row is flushed immediately so an interrupted run keeps its data.

Headless self-test: .venv/bin/python tests/test_psu_logger.py
"""
import csv

LOG_COLUMNS = ['timestamp_iso', 'elapsed_s', 'channel', 'set_voltage_v',
               'meas_voltage_v', 'meas_current_a', 'power_w']


def power_w(voltage_v, current_a):
    """Calculated power P = V * I in watts. Computed in software (not read
    off the supply) so it is portable across firmware."""
    return float(voltage_v) * float(current_a)


def build_row(timestamp_iso, elapsed_s, reading):
    """Build a LOG_COLUMNS row dict from a BK9174B.read_channel() result.

    `reading` needs channel/set_voltage_v/meas_voltage_v/meas_current_a; its
    power_w is used if present, else recomputed here."""
    v = float(reading['meas_voltage_v'])
    i = float(reading['meas_current_a'])
    p = reading['power_w'] if 'power_w' in reading else power_w(v, i)
    return {
        'timestamp_iso': timestamp_iso,
        'elapsed_s': round(float(elapsed_s), 3),
        'channel': reading['channel'],
        'set_voltage_v': round(float(reading['set_voltage_v']), 4),
        'meas_voltage_v': round(v, 4),
        'meas_current_a': round(i, 5),
        'power_w': round(float(p), 5),
    }


class PsuCsvLogger:
    """Streaming CSV writer; flushes every row (crash-safe). Usable as a
    context manager."""

    def __init__(self, path):
        self.path = path
        self._f = open(path, 'w', newline='')
        self._w = csv.DictWriter(self._f, fieldnames=LOG_COLUMNS)
        self._w.writeheader()
        self._f.flush()
        self.rows = 0

    def write_row(self, row):
        self._w.writerow(row)
        self._f.flush()
        self.rows += 1

    def log(self, timestamp_iso, elapsed_s, reading):
        """Build a row from a reading, write it, and return the row dict."""
        row = build_row(timestamp_iso, elapsed_s, reading)
        self.write_row(row)
        return row

    def close(self):
        try:
            self._f.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
