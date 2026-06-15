#!/usr/bin/env python3
"""Headless tests for arb_build (no Tk, no instrument).

Run: .venv/bin/python test_arb_build.py
"""
import arb_build as ab


def test_render_length():
    r = ab.default_recipe(total_points=1000)
    assert len(ab.render_recipe(r)) == 1000


def test_worked_example_ramp_then_dc():
    # LINE (0,0)->(2,0.25), then HOLD (2,0.25)->(3,0.25)
    recipe = {
        'version': 1, 'total_points': 300,
        'breakpoints': [[0, 0.0], [2, 0.25], [3, 0.25]],
        'segments': [{'type': 'LINE', 'params': {}},
                     {'type': 'HOLD', 'params': {}}],
    }
    s = ab.render_recipe(recipe)
    assert len(s) == 300
    # x=2 maps to index 200; first 200 are a rising ramp 0 -> ~0.25
    assert abs(s[0]) < 1e-9
    assert s[100] > 0.10 and s[100] < 0.15      # midway up the ramp
    assert all(s[i] <= s[i + 1] + 1e-9 for i in range(199))  # monotonic rise
    # the HOLD tail sits flat at 0.25
    assert all(abs(v - 0.25) < 1e-9 for v in s[200:])


def test_breakpoint_indices_hit_values():
    recipe = {
        'version': 1, 'total_points': 400,
        'breakpoints': [[0, 0.0], [1, 0.5], [2, -0.5], [4, 0.0]],
        'segments': [{'type': 'LINE'}, {'type': 'LINE'}, {'type': 'LINE'}],
    }
    s = ab.render_recipe(recipe)
    # each interior breakpoint x maps to an index that holds its y exactly
    assert abs(s[0] - 0.0) < 1e-9     # x=0 -> idx 0
    assert abs(s[100] - 0.5) < 1e-9   # x=1 -> idx 100
    assert abs(s[200] + 0.5) < 1e-9   # x=2 -> idx 200


def test_sine_segment_returns_to_baseline():
    recipe = {
        'version': 1, 'total_points': 360,
        'breakpoints': [[0, 0.0], [1, 0.0]],
        'segments': [{'type': 'SINE', 'params': {'cycles': 1, 'amp': 0.8}}],
    }
    s = ab.render_recipe(recipe)
    assert abs(s[0]) < 1e-9               # starts at baseline
    assert abs(max(s) - 0.8) < 0.02       # peak ~ amp
    assert abs(min(s) + 0.8) < 0.02


def test_clamp_to_unit():
    recipe = {
        'version': 1, 'total_points': 100,
        'breakpoints': [[0, 0.8], [1, 0.8]],
        'segments': [{'type': 'SINE', 'params': {'amp': 1.0}}],  # 0.8+1 > 1
    }
    s = ab.render_recipe(recipe)
    assert max(s) <= 1.0 and min(s) >= -1.0


def test_render_validation():
    for bad in (
        {'total_points': 100, 'breakpoints': [[0, 0]], 'segments': []},
        {'total_points': 100, 'breakpoints': [[0, 0], [1, 0]],
         'segments': [{'type': 'LINE'}, {'type': 'LINE'}]},   # seg count
        {'total_points': 100, 'breakpoints': [[0, 0], [0, 0.5]],
         'segments': [{'type': 'LINE'}]},                     # non-increasing
    ):
        try:
            ab.render_piecewise(bad['breakpoints'], bad['segments'],
                                bad['total_points'])
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad}")


def test_add_point_splits_interval():
    r = ab.default_recipe()              # 2 bp, 1 seg
    r2 = ab.add_point(r, 0.5, 0.3)
    assert len(r2['breakpoints']) == 3
    assert len(r2['segments']) == 2     # invariant: segs == bps - 1
    assert r2['breakpoints'][1] == [0.5, 0.3]
    # original unchanged (helpers return new recipes)
    assert len(r['breakpoints']) == 2


def test_add_point_rejects_duplicate_x():
    r = ab.default_recipe()
    try:
        ab.add_point(r, 0.0, 0.5)       # x=0 already exists
    except ValueError:
        return
    raise AssertionError("expected ValueError for duplicate x")


def test_move_point_clamps():
    r = ab.add_point(ab.default_recipe(), 0.5, 0.0)  # bps at x=0,0.5,1
    moved = ab.move_point(r, 1, 5.0, 9.0)            # x beyond neighbor, y>1
    assert moved['breakpoints'][1][0] < 1.0          # clamped below x=1
    assert moved['breakpoints'][1][0] > 0.0
    assert moved['breakpoints'][1][1] == 1.0         # y clamped to 1


def test_delete_point():
    r = ab.add_point(ab.default_recipe(), 0.5, 0.2)  # 3 bp, 2 seg
    d = ab.delete_point(r, 1)
    assert len(d['breakpoints']) == 2
    assert len(d['segments']) == 1
    try:
        ab.delete_point(d, 0)                         # would drop below 2
    except ValueError:
        return
    raise AssertionError("expected ValueError deleting below 2 breakpoints")


def test_set_segment_type_and_params():
    r = ab.default_recipe()
    r = ab.set_segment_type(r, 0, 'sine')
    assert r['segments'][0]['type'] == 'SINE'
    r = ab.set_segment_params(r, 0, {'cycles': 3, 'amp': 0.5})
    assert r['segments'][0]['params'] == {'cycles': 3.0, 'amp': 0.5}
    try:
        ab.set_segment_type(r, 0, 'bogus')
    except ValueError:
        return
    raise AssertionError("expected ValueError for unknown type")


def test_smooth():
    spike = [0.0] * 10
    spike[5] = 1.0
    out = ab.smooth(spike, 1)
    assert out[5] < 1.0 and out[5] > 0.0      # peak reduced
    assert ab.smooth(spike, 0) == spike       # radius 0 = identity


def test_samples_to_recipe():
    samples = [i / 99 for i in range(100)]    # ramp 0..~1
    r = ab.samples_to_recipe(samples, n_anchors=11)
    assert len(r['breakpoints']) == 11
    assert len(r['segments']) == 10
    assert abs(r['breakpoints'][0][1] - 0.0) < 1e-9
    assert r['breakpoints'][-1][1] <= 1.0


def test_recipe_round_trip():
    r = ab.add_point(ab.default_recipe(), 0.5, 0.4, seg_type='SINE')
    r = ab.set_segment_params(r, 0, {'cycles': 2, 'amp': 0.7, 'phase': 0})
    r2 = ab.recipe_from_json(ab.recipe_to_json(r))
    assert r2 == r
    assert ab.render_recipe(r2) == ab.render_recipe(r)   # deterministic


if __name__ == '__main__':
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\nAll {len(tests)} arb_build tests passed.")
