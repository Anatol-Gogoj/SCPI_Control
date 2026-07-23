#!/usr/bin/env python3
"""Headless tests for sldea_profile (no hardware).

Run: .venv/bin/python tests/test_sldea_profile.py
"""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))

from sldea_profile import (SldeaProfile, compute_levels, control_v_for_kv,
                           measured_kv, measured_ua, TREK_MAX_KV)


def test_scaling():
    assert control_v_for_kv(8.0) == 8.0            # 1 kV per control-volt
    assert measured_kv(7.5) == 7.5                 # 1 kV per scope-volt
    assert measured_ua(10.0) == 2000.0             # 10 V -> 2000 uA
    assert measured_ua(1.0) == 200.0


def test_levels_from_step_and_n():
    assert compute_levels(0, 10, step_kv=2.0) == [0, 2, 4, 6, 8, 10]
    # non-divisible step: exact increments, last <= end
    lv = compute_levels(0, 10, step_kv=3.0)
    assert lv == [0, 3, 6, 9]
    # n_steps: exact endpoints
    lv = compute_levels(0, 10, n_steps=5)
    assert lv == [0, 2.5, 5.0, 7.5, 10.0]


def test_timeline_simple_up():
    p = SldeaProfile(start_kv=0, end_kv=10, step_kv=2.0, ramp_s=5,
                     landing_s=60, settle_s=2, snap_lead_s=1)
    assert p.levels == [2, 4, 6, 8, 10]            # 0 kV is baseline, not held
    assert p.n_levels == 5
    assert p.total_duration_s == 5 * (5 + 60)
    assert p.n_frames == 1 + 2 * 5                  # baseline + 2 per landing


def test_snapshot_times_and_tags():
    p = SldeaProfile(start_kv=0, end_kv=4, step_kv=2.0, ramp_s=5,
                     landing_s=60, settle_s=2, snap_lead_s=1)
    tags = [s['tag'] for s in p.snapshots]
    assert tags[0] == 'baseline'
    assert p.snapshots[0]['t'] == 0.0
    # first landing is 2 kV (0 kV is baseline): ramp 0->5, hold 5..65
    post = next(s for s in p.snapshots if s['step'] == 1 and s['tag'] == 'post')
    pre = next(s for s in p.snapshots if s['step'] == 1 and s['tag'] == 'pre')
    assert post['t'] == 5 + 2 and post['nominal_kv'] == 2   # ramp_end + settle
    assert pre['t'] == 65 - 1                                # hold_end - lead
    # second landing (4 kV): starts at t=65
    post2 = next(s for s in p.snapshots if s['step'] == 2 and s['tag'] == 'post')
    assert post2['t'] == 65 + 5 + 2
    assert post2['nominal_kv'] == 4


def test_updown_and_repeat():
    p = SldeaProfile(start_kv=0, end_kv=6, step_kv=2.0, updown=True,
                     landing_s=10, ramp_s=1, settle_s=1, snap_lead_s=1)
    # up 2,4,6 then back down 4,2  (0 kV is baseline; peak held once)
    assert p.sequence() == [2, 4, 6, 4, 2]
    p2 = SldeaProfile(start_kv=0, end_kv=4, step_kv=2.0, repeat=3,
                      landing_s=10, ramp_s=1, settle_s=1, snap_lead_s=1)
    assert p2.sequence() == [2, 4] * 3
    assert p2.n_levels == 6


def test_kv_at_interpolates_ramp_and_hold():
    p = SldeaProfile(start_kv=0, end_kv=10, step_kv=10.0, ramp_s=10,
                     landing_s=20, settle_s=1, snap_lead_s=1, baseline=False)
    # single landing: ramp 0->10 over 0..10, hold 10..30
    assert p.kv_at(0) == 0.0
    assert p.kv_at(5) == 5.0                         # mid-ramp
    assert p.kv_at(10) == 10.0
    assert p.kv_at(20) == 10.0                        # mid-hold


def test_validation():
    for kw in (dict(start_kv=0, end_kv=12, step_kv=1),         # > Trek max
               dict(start_kv=-1, end_kv=5, step_kv=1),          # < 0
               dict(start_kv=0, end_kv=5, step_kv=1, landing_s=2,
                    settle_s=1, snap_lead_s=2),                 # settle+lead>=landing
               dict(start_kv=0, end_kv=5, landing_s=0, step_kv=1)):  # bad landing
        try:
            SldeaProfile(**kw)
            raise AssertionError(f"expected ValueError for {kw}")
        except ValueError:
            pass


def test_naming_and_csv_columns():
    import datetime
    dt = datetime.datetime(2026, 7, 23, 14, 5, 9)
    assert SldeaProfile.run_dirname(dt) == 'SLDEA_20260723_140509'
    assert SldeaProfile.frame_filename(7, 1.6, 'pre') == \
        'SLDEA_s07_01.60kV_pre.png'
    assert SldeaProfile.frame_filename(0, 0.0, 'baseline') == \
        'SLDEA_s00_00.00kV_baseline.png'
    # the edge-detection columns exist but are meant to start empty
    for col in ('active_area_px', 'active_area_mm2', 'active_diam_mm', 'notes'):
        assert col in SldeaProfile.CSV_COLUMNS


def test_setup_text_covers_key_facts():
    p = SldeaProfile(start_kv=0, end_kv=8, step_kv=0.2, ramp_s=5, landing_s=60)
    txt = p.setup_text('SLDEA_x', '2026-07-23T14:00', sg_ch=2, vmon_ch=1,
                       imon_ch=2, dry_run=True, cam_info='exp 6, WB off')
    assert 'DRY RUN' in txt
    assert 'CH2' in txt and 'CH1' in txt
    assert '2000 uA' in txt
    assert 'exp 6, WB off' in txt


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed")


if __name__ == '__main__':
    _run()
