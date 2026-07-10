#!/usr/bin/env python3
"""Headless tests for the EasyWaveX CSV export (no instrument needed).

The gold standard is easywavex_template.csv -- the lab's own EasyWaveX
template ("It has to be in the exact waveform template otherwise the
EasywaveX won't generate the waveform"). The round-trip test parses it and
rebuilds it BYTE-IDENTICALLY. Run: .venv/bin/python tests/test_easywave_export.py
"""
# Runnable from anywhere: put the repo root (one level up) on sys.path
# so the app modules import when this file is executed directly.
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))
import os

from easywave_export import (EASYWAVE_POINTS, build_easywave_csv,
                             parse_easywave_csv, resample_linear,
                             suggest_header, write_easywave_csv)

_TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'easywavex_template.csv')


def _lines(blob):
    assert blob.endswith(b'\r\n'), "file must end with CRLF"
    return blob[:-2].split(b'\r\n')


def test_structure():
    blob = build_easywave_csv([0.0, 1.0, 2.0], 0.01, 5.0, 2.5)
    assert isinstance(blob, bytes)
    # CRLF everywhere: every \n is preceded by \r, counts match line count
    assert blob.count(b'\n') == blob.count(b'\r\n') == 16397
    lines = _lines(blob)
    assert len(lines) == 16397, len(lines)
    assert lines[0] == b'data length,16384'
    assert lines[1] == b'frequency,0.010000'
    assert lines[2] == b'amp,5.000000'
    assert lines[3] == b'offset,2.500000'
    assert lines[4] == b'phase,0.000000'
    assert lines[5:12] == [b''] * 7, "exactly 7 blank lines after header"
    assert lines[12] == b'xpos,value'
    assert lines[13].startswith(b'1,')
    assert lines[-1].startswith(b'16384,')


def test_values_resampled_and_formatted():
    # 3 input points -> linear ramp 0..2 across 16384 rows, %.6g values
    blob = build_easywave_csv([0.0, 1.0, 2.0], 0.01, 2.0, 1.0)
    lines = _lines(blob)
    data = lines[13:]
    assert len(data) == EASYWAVE_POINTS
    assert data[0] == b'1,0'
    assert data[-1] == b'16384,2'
    # midpoint of the ramp ~ 1.0
    mid = float(data[EASYWAVE_POINTS // 2].split(b',')[1])
    assert abs(mid - 1.0) < 1e-3, mid
    # xpos is 1-based and consecutive
    assert data[99].startswith(b'100,')


def test_resample_linear():
    # identity when already the right length
    vals = [float(i) for i in range(EASYWAVE_POINTS)]
    assert resample_linear(vals) == vals
    # endpoints preserved, midpoint interpolated
    out = resample_linear([0.0, 10.0], points=5)
    assert out == [0.0, 2.5, 5.0, 7.5, 10.0], out
    # single value repeats
    assert resample_linear([3.0], points=4) == [3.0] * 4
    try:
        resample_linear([])
        assert False, "empty input must raise"
    except ValueError:
        pass


def test_suggest_header_lab_rules():
    # amp = highest value, offset = amp/2, freq = 1/T_total, phase 0
    h = suggest_header([0.0, 1.5, 2.0, 0.5], duration_s=100.0)
    assert h['amp_v'] == 2.0
    assert h['offset_v'] == 1.0
    assert abs(h['freq_hz'] - 0.01) < 1e-12
    assert h['phase_deg'] == 0.0


def test_template_round_trip_byte_identical():
    # Parse the lab's own template and rebuild it byte-for-byte.
    with open(_TEMPLATE, 'rb') as f:
        original = f.read()
    header, values = parse_easywave_csv(original)
    assert len(values) == EASYWAVE_POINTS, len(values)
    rebuilt = build_easywave_csv(values,
                                 float(header['frequency']),
                                 float(header['amp']),
                                 float(header['offset']),
                                 float(header['phase']))
    assert rebuilt == original, "rebuild must be byte-identical to the template"


def test_write_easywave_csv(tmp='/tmp/_easywave_test.csv'):
    n = write_easywave_csv(tmp, [0.0, 1.0], 1.0, 1.0, 0.5)
    try:
        with open(tmp, 'rb') as f:
            blob = f.read()
        assert len(blob) == n
        assert blob.count(b'\r\n') == 16397, "CRLF must survive the write"
    finally:
        os.unlink(tmp)


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed")


if __name__ == '__main__':
    _run()
