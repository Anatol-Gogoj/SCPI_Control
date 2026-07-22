#!/usr/bin/env python3
"""Headless tests for psu_logger (CSV data-logger, no hardware).

Run: .venv/bin/python tests/test_psu_logger.py
"""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))
import csv
import os
import shutil
import tempfile

from psu_logger import LOG_COLUMNS, power_w, build_row, PsuCsvLogger


def test_power_calc():
    assert abs(power_w(12, 0.3) - 3.6) < 1e-9
    assert abs(power_w('11.998', '0.3') - 3.5994) < 1e-9      # coerces strings


def test_build_row_shape_and_rounding():
    reading = {'channel': 1, 'set_voltage_v': 12.0,
               'meas_voltage_v': 11.9983, 'meas_current_a': 0.29996}
    row = build_row('2026-07-22T10:00:00.000-04:00', 1.2345, reading)
    assert set(row) == set(LOG_COLUMNS)
    assert row['channel'] == 1
    assert abs(row['elapsed_s'] - 1.2345) < 1e-3
    assert abs(row['power_w'] - round(11.9983 * 0.29996, 5)) < 1e-9


def test_build_row_uses_precomputed_power():
    reading = {'channel': 2, 'set_voltage_v': 5, 'meas_voltage_v': 5.0,
               'meas_current_a': 1.0, 'power_w': 5.0}
    assert build_row('t', 0.0, reading)['power_w'] == 5.0


def test_csv_round_trip():
    d = tempfile.mkdtemp(prefix='psulog_')
    try:
        path = os.path.join(d, 'log.csv')
        with PsuCsvLogger(path) as lg:
            lg.log('2026-07-22T10:00:00.000-04:00', 0.0,
                   {'channel': 1, 'set_voltage_v': 12,
                    'meas_voltage_v': 12.0, 'meas_current_a': 0.3})
            lg.log('2026-07-22T10:00:01.000-04:00', 1.0,
                   {'channel': 1, 'set_voltage_v': 12,
                    'meas_voltage_v': 11.99, 'meas_current_a': 0.31})
            assert lg.rows == 2
        with open(path) as f:
            reader = csv.DictReader(f)
            assert reader.fieldnames == LOG_COLUMNS       # header order kept
            rows = list(reader)
        assert len(rows) == 2
        assert rows[0]['channel'] == '1'
        assert abs(float(rows[1]['power_w']) - round(11.99 * 0.31, 5)) < 1e-9
    finally:
        shutil.rmtree(d)


def test_flush_is_crash_safe():
    # rows are readable before close() (flushed each write)
    d = tempfile.mkdtemp(prefix='psulog_')
    try:
        path = os.path.join(d, 'log.csv')
        lg = PsuCsvLogger(path)
        lg.log('t', 0.0, {'channel': 1, 'set_voltage_v': 5,
                          'meas_voltage_v': 5.0, 'meas_current_a': 1.0})
        with open(path) as f:                 # not closed yet
            data = list(csv.DictReader(f))
        assert len(data) == 1 and data[0]['power_w'] == '5.0'
        lg.close()
    finally:
        shutil.rmtree(d)


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed")


if __name__ == '__main__':
    _run()
