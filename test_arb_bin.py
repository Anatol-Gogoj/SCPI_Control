#!/usr/bin/env python3
"""Headless tests for the 4055B flash-drive .bin export (no instrument).

Ground truth is arb_bin_reference_9step.bin -- a known-good lab file the
4055B reads from a flash drive (9-step staircase, EasyWaveX-generated).
Run: .venv/bin/python test_arb_bin.py
"""
import os
import struct

from arb_bin import (BIN_POINTS, FULL_SCALE, build_arb_bin, parse_arb_bin,
                     write_arb_bin)

_REFERENCE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'arb_bin_reference_9step.bin')


def test_build_size_and_encoding():
    blob = build_arb_bin([0.0, 1.0, -1.0, 0.0])
    assert len(blob) == BIN_POINTS * 2 == 32768, len(blob)
    vals = struct.unpack(f'<{BIN_POINTS}h', blob)
    assert max(vals) == FULL_SCALE and min(vals) == -FULL_SCALE
    # first sample exact, endpoints preserved by the resampler
    assert vals[0] == 0 and vals[-1] == 0


def test_build_normalizes_overscale():
    blob = build_arb_bin([2.0, -2.0, 1.0], points=3)
    assert struct.unpack('<3h', blob) == (32767, -32767, 16384)


def test_parse_roundtrip_values():
    blob = struct.pack('<4h', 0, 32767, -32767, 16384)
    vals = parse_arb_bin(blob)
    assert vals[0] == 0.0 and vals[1] == 1.0 and vals[2] == -1.0
    assert abs(vals[3] - 0.5) < 1e-4


def test_parse_rejects_bad_input():
    for bad in (b'', b'\x00'):
        try:
            parse_arb_bin(bad)
            assert False, f"must raise on {bad!r}"
        except ValueError:
            pass


def test_reference_file_structure():
    # The lab file: headerless, 16384 samples, a 9-plateau staircase from
    # -FS to +FS in FS/4 steps.
    with open(_REFERENCE, 'rb') as f:
        blob = f.read()
    assert len(blob) == 32768
    vals = struct.unpack(f'<{BIN_POINTS}h', blob)
    plateaus = [vals[0]]
    for a, b in zip(vals, vals[1:]):
        if b != a:
            plateaus.append(b)
    assert len(plateaus) == 9, plateaus
    assert plateaus[0] == -FULL_SCALE and plateaus[-1] == FULL_SCALE
    assert plateaus[4] == 0
    # monotone rising staircase
    assert all(b > a for a, b in zip(plateaus, plateaus[1:]))


def test_reference_roundtrip_byte_identical():
    # parse -> rebuild must reproduce the lab file byte-for-byte
    with open(_REFERENCE, 'rb') as f:
        original = f.read()
    rebuilt = build_arb_bin(parse_arb_bin(original))
    assert rebuilt == original, "rebuild must be byte-identical"


def test_write_arb_bin(tmp='/tmp/_arb_bin_test.bin'):
    n = write_arb_bin(tmp, [0.0, 1.0, 0.0, -1.0])
    try:
        with open(tmp, 'rb') as f:
            blob = f.read()
        assert n == len(blob) == BIN_POINTS * 2
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
