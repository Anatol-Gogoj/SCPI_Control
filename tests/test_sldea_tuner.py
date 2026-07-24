#!/usr/bin/env python3
"""Headless tests for sldea_tuner's pure logic (no window).

Run: .venv/bin/python tests/test_sldea_tuner.py
"""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))

import sldea_tuner as st


def _rows(specs):
    # specs: list of (tag, kv, has_frame)
    out = []
    for k, (tag, kv, has) in enumerate(specs):
        out.append({'tag': tag, 'nominal_kV': ('' if kv is None else str(kv)),
                    'frame_file': (f'f{k}.png' if has else ''), 'step': k})
    return out


def test_choose_indices_baseline_mid_late():
    rows = _rows([('baseline', 0.0, True), ('post-ramp', 1.0, True),
                  ('post-ramp', 2.0, True), ('post-ramp', 3.0, True),
                  ('post-ramp', 4.0, True)])
    picks = st.choose_indices(rows)
    assert [p[0] for p in picks] == ['baseline', 'mid-run', 'late']
    assert picks[0][1] == 0           # baseline row
    assert picks[2][1] == 4           # highest kV
    assert picks[1][1] == 2           # nearest the 2.0 kV midpoint


def test_choose_indices_skips_frameless_and_finds_baseline_tag():
    # baseline not first; some rows have no frame file
    rows = _rows([('post-ramp', 1.0, False), ('baseline', 0.0, True),
                  ('post-ramp', 2.0, True), ('post-ramp', 5.0, True)])
    picks = st.choose_indices(rows)
    d = dict((l, i) for l, i in picks)
    assert d['baseline'] == 1
    assert d['late'] == 3
    # the frameless row 0 is never a content pick
    assert 0 not in [i for _, i in picks]


def test_choose_indices_unique_when_few_frames():
    rows = _rows([('baseline', 0.0, True), ('post-ramp', 4.0, True)])
    picks = st.choose_indices(rows)
    idxs = [i for _, i in picks]
    assert len(idxs) == len(set(idxs))        # no duplicate panels
    assert 0 in idxs and 1 in idxs


def test_choose_indices_missing_voltages_uses_median_index():
    rows = _rows([('baseline', None, True), ('post-ramp', None, True),
                  ('post-ramp', None, True), ('post-ramp', None, True)])
    picks = st.choose_indices(rows)
    assert len(picks) == 3
    assert picks[0][1] == 0
    # mid distinct from baseline and late
    assert len({i for _, i in picks}) == 3


def test_choose_indices_empty():
    assert st.choose_indices([]) == []


def _run():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith('test_') and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed")


if __name__ == '__main__':
    _run()
