#!/usr/bin/env python3
"""Headless tests for sldea_edge (synthetic frames, no camera/instruments).

Run: .venv/bin/python tests/test_sldea_edge.py
"""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))
import csv
import os
import shutil
import tempfile

import numpy as np

import sldea_edge as se


def _disc_frame(r, level=40.0, size=240, base=100.0):
    """Synthetic frame: uniform background `base` + a disc `level` brighter."""
    img = np.full((size, size), base, np.float32)
    yy, xx = np.mgrid[0:size, 0:size]
    img[(xx - size / 2) ** 2 + (yy - size / 2) ** 2 <= r * r] += level
    return img


def _fake_run(d, rows):
    os.makedirs(os.path.join(d, 'frames'), exist_ok=True)
    cols = ['snapshot', 'step', 'tag', 'nominal_kV', 'control_V',
            'measured_kV', 'measured_uA', 't_planned_s', 'timestamp',
            'frame_file', 'active_area_px', 'active_area_mm2',
            'active_diam_mm', 'notes']
    with open(os.path.join(d, 'data.csv'), 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({**{c: '' for c in cols}, **r})


def test_candidates_find_synthetic_disc():
    base = _disc_frame(0, level=0)              # flat baseline
    img = _disc_frame(40)                        # r=40 disc
    cands = se.candidates(base, img, dict(se.DEFAULT_SETTINGS))
    assert cands, "no candidates on a clean synthetic disc"
    best = cands[0]
    true_area = np.pi * 40 * 40
    assert abs(best['area_px'] - true_area) / true_area < 0.10, best
    assert best['solidity'] > 0.9
    assert best['conf'] > 0.75
    assert not se.needs_review(cands, se.DEFAULT_SETTINGS)
    assert all(c['method'].startswith('diff') for c in cands), \
        "hough must be gone (it fabricated circles on real frames)"


def test_oblong_shape_scores_like_a_circle():
    # An ellipse (2:1) must NOT be punished -- the DEA expansion can be oblong
    base = _disc_frame(0, level=0)
    img = np.full((240, 240), 100.0, np.float32)
    yy, xx = np.mgrid[0:240, 0:240]
    img[((xx - 120) / 60.0) ** 2 + ((yy - 120) / 30.0) ** 2 <= 1] += 40
    cands = se.candidates(base, img, dict(se.DEFAULT_SETTINGS))
    assert cands, "no candidates on the ellipse"
    best = cands[0]
    true_area = np.pi * 60 * 30
    assert abs(best['area_px'] - true_area) / true_area < 0.10, best
    assert best['solidity'] > 0.9, "solidity must not punish oblong shapes"
    assert best['conf'] > 0.75


def test_no_change_gate_returns_empty():
    # Identical frame (plus faint noise) => no candidates, not a fabricated
    # outline -- this was the 'randomly placed circle' failure mode.
    rng = np.random.default_rng(3)
    base = np.clip(rng.normal(100, 2, (240, 240)), 0,
                   255).astype(np.float32)
    img = np.clip(base + rng.normal(0, 1.5, base.shape), 0,
                  255).astype(np.float32)
    assert se.candidates(base, img, dict(se.DEFAULT_SETTINGS)) == []


def test_candidates_downscaled_frame_rescales_to_full_res():
    # A 1280-wide frame is detected at DETECT_MAX_W but must report
    # full-resolution px quantities.
    base = _disc_frame(0, level=0, size=1280)
    img = _disc_frame(200, size=1280)
    cands = se.candidates(base, img, dict(se.DEFAULT_SETTINGS))
    assert cands, "no candidates on the large synthetic disc"
    best = cands[0]
    true_area = np.pi * 200 * 200
    assert abs(best['area_px'] - true_area) / true_area < 0.10, best['area_px']
    assert abs(best['diam_px'] - 400) / 400 < 0.06, best['diam_px']
    # contour points are in full-res coordinates too
    xs = [p[0] for p in best['contour']]
    assert max(xs) > se.DETECT_MAX_W, "contour still in downscaled coords"


def test_mark_breakdown_files_renames_from_first_flag():
    d = tempfile.mkdtemp(prefix='edge_bd_')
    try:
        names = ['b.png', 'f1.png', 'f2.png', 'f3.png']
        _fake_run(d, [{'snapshot': i + 1, 'step': i,
                       'tag': 'baseline' if i == 0 else 'post',
                       'nominal_kV': str(float(i)), 'frame_file': n}
                      for i, n in enumerate(names)])
        for n in names:
            open(os.path.join(d, 'frames', n), 'wb').write(b'x')
        run = se.load_run(d)
        renamed = se.mark_breakdown_files(run, {2: 'breakdown? I=90uA'})
        assert renamed == 2                       # f2 + f3, not b/f1
        frames = sorted(os.listdir(os.path.join(d, 'frames')))
        assert 'f2_BREAKDOWN.png' in frames and 'f3_BREAKDOWN.png' in frames
        assert 'f1.png' in frames                 # pre-breakdown untouched
        assert run['rows'][2]['frame_file'] == 'f2_BREAKDOWN.png'
        assert 'post-breakdown' in run['rows'][3]['notes']
        assert 'post-breakdown' not in (run['rows'][1].get('notes') or '')
        # idempotent: nothing further to rename
        assert se.mark_breakdown_files(run, {2: 'x'}) == 0
        # nothing at all without flags
        assert se.mark_breakdown_files(run, {}) == 0
    finally:
        shutil.rmtree(d)


def test_needs_review_on_weak_or_empty():
    assert se.needs_review([], se.DEFAULT_SETTINGS)
    weak = [{'conf': 0.4, 'spread_pct': 5.0}]
    assert se.needs_review(weak, se.DEFAULT_SETTINGS)
    disagree = [{'conf': 0.9, 'spread_pct': 40.0}]
    assert se.needs_review(disagree, se.DEFAULT_SETTINGS)


def test_settings_roundtrip_and_diam_from_setup():
    d = tempfile.mkdtemp(prefix='edge_')
    try:
        with open(os.path.join(d, 'setup.txt'), 'w') as f:
            f.write("SLDEA Test -- x\nDEA nominal diameter: 12.5 mm\n")
        s = se.load_settings(d)
        assert s['diam_mm'] == 12.5              # picked up from the run header
        s['breakdown_ua'] = 75.0
        s['blur_px'] = 7
        se.save_settings(d, s)
        se.save_settings(d, s)                   # idempotent (section replaced)
        text = open(os.path.join(d, 'setup.txt')).read()
        assert text.count(se.EDGE_HDR) == 1
        assert 'DEA nominal diameter' in text    # original header kept
        s2 = se.load_settings(d)
        assert s2['breakdown_ua'] == 75.0 and s2['blur_px'] == 7
        assert isinstance(s2['blur_px'], int)
    finally:
        shutil.rmtree(d)


def test_breakdown_flags_current_and_collapse():
    rows = [{'nominal_kV': '1', 'measured_uA': '2'},
            {'nominal_kV': '2', 'measured_uA': '120'},     # current spike
            {'nominal_kV': '3', 'measured_uA': '3'},
            {'nominal_kV': '4', 'measured_uA': '4'}]       # area collapse
    areas = {0: 1000.0, 2: 1050.0, 3: 300.0}
    flags = se.breakdown_flags(rows, areas, se.DEFAULT_SETTINGS)
    assert 1 in flags and 'uA' in flags[1]
    assert 3 in flags and 'collapse' in flags[3]
    assert 0 not in flags and 2 not in flags


def test_scale_apply_and_write_back():
    d = tempfile.mkdtemp(prefix='edge_')
    try:
        _fake_run(d, [
            {'snapshot': 1, 'step': 0, 'tag': 'baseline', 'nominal_kV': '0.0',
             'frame_file': 'b.png'},
            {'snapshot': 2, 'step': 1, 'tag': 'post', 'nominal_kV': '1.0',
             'frame_file': 'f1.png'}])
        run = se.load_run(d)
        # baseline detected at diam 100 px; nominal diam 16 mm -> 0.16 mm/px
        results = {0: {'area_px': np.pi * 50 * 50, 'diam_px': 100.0,
                       'circ': 0.9, 'conf': 0.9, 'method': 'diff-otsu'},
                   1: {'area_px': np.pi * 60 * 60, 'diam_px': 120.0,
                       'circ': 0.9, 'conf': 0.8, 'method': 'diff-otsu',
                       'chosen_by': 'user'}}
        s = dict(se.DEFAULT_SETTINGS)
        scale = se.mm_per_px(results, run['rows'], s)
        assert abs(scale - 0.16) < 1e-9
        se.apply_results(run['rows'], results, scale,
                         {1: 'breakdown? I=90uA > 50uA'})
        assert run['rows'][0]['active_area_px'] == f"{np.pi*50*50:.0f}"
        assert abs(float(run['rows'][1]['active_diam_mm']) - 19.2) < 1e-6
        assert 'user' in run['rows'][1]['notes']
        assert 'breakdown?' in run['rows'][1]['notes']
        se.write_back(d, run)
        assert os.path.exists(os.path.join(d, 'data.csv.bak'))
        with open(os.path.join(d, 'data.csv')) as f:
            rows2 = list(csv.DictReader(f))
        assert rows2[1]['active_area_mm2'] != ''
    finally:
        shutil.rmtree(d)


def test_rejected_row_marked():
    rows = [{'tag': 'post', 'nominal_kV': '1'}]
    se.apply_results(rows, {0: None}, None, {})
    assert rows[0]['notes'] == 'rejected (no reliable edge)'


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed")


if __name__ == '__main__':
    _run()
