#!/usr/bin/env python3
"""Headless tests for webcam.py pure logic (no camera needed).

focus_score needs numpy; the rest is stdlib-only. Run:
    .venv/bin/python tests/test_webcam.py
"""
# Runnable from anywhere: put the repo root (one level up) on sys.path
# so the app modules import when this file is executed directly.
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))
import datetime as dt

import webcam as wc


def test_frange_ascending():
    assert wc.frange(0, 5, 1) == [0, 1, 2, 3, 4, 5]
    assert wc.frange(0.0, 1.0, 0.25) == [0.0, 0.25, 0.5, 0.75, 1.0]


def test_frange_descending():
    assert wc.frange(2, 0, -1) == [2, 1, 0]


def test_frange_single_point():
    assert wc.frange(3, 3, 1) == [3]


def test_frange_errors():
    for args in [(0, 5, 0), (0, 5, -1), (5, 0, 1)]:
        try:
            wc.frange(*args)
            assert False, f"expected ValueError for {args}"
        except ValueError:
            pass


def test_capture_filename_basic():
    assert wc.capture_filename('shot', 1) == 'shot_0001.png'
    assert wc.capture_filename('shot', 42, ext='jpg') == 'shot_0042.jpg'


def test_capture_filename_value_safe():
    # -2.5 V -> filename-safe token
    assert wc.capture_filename('v', 3, value=-2.5) == 'v_0003_m2p5V.png'
    assert wc.capture_filename('v', 3, value=1.0) == 'v_0003_1V.png'


def test_capture_filename_sanitises_prefix():
    out = wc.capture_filename('my run/#1', 0)
    assert '/' not in out and '#' not in out
    assert out.endswith('_0000.png')


def test_capture_filename_timestamp():
    ts = dt.datetime(2026, 6, 27, 14, 30, 5)
    out = wc.capture_filename('t', 1, ts=ts)
    assert out == 't_0001_20260627-143005.png'


def test_focus_score_flat_is_zero():
    flat = [[100] * 8 for _ in range(8)]
    assert wc.focus_score(flat) == 0.0


def test_focus_score_edges_higher_than_flat():
    flat = [[10] * 8 for _ in range(8)]
    checker = [[(0 if (i + j) % 2 == 0 else 255) for j in range(8)]
               for i in range(8)]
    assert wc.focus_score(checker) > wc.focus_score(flat)


def test_focus_score_rejects_tiny():
    try:
        wc.focus_score([[1, 2], [3, 4]])
        assert False, "expected ValueError for 2x2"
    except ValueError:
        pass


def test_deps_available_returns_tuple():
    ok, reason = wc.deps_available()
    assert isinstance(ok, bool) and isinstance(reason, str)


def test_list_cameras_returns_list():
    assert isinstance(wc.list_cameras(max_index=2), list)


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed")


if __name__ == '__main__':
    _run()
