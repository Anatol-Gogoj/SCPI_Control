#!/usr/bin/env python3
"""Headless tests for SignalGenPresetStore (no instrument needed).

Round-trips presets through a temp file and checks validation/corruption
handling. Run: .venv/bin/python tests/test_siggen_presets.py
"""
# Runnable from anywhere: put the repo root (one level up) on sys.path
# so the app modules import when this file is executed directly.
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))
import os
import json
import tempfile

from siggen_presets import (SignalGenPresetStore, validate_channel_state,
                            arb_from_csv, write_arb_template,
                            sanitize_arb_name)


def _state(waveform='SINE', freq=1000.0, amp=1.0, offset=0.0,
           output=False, load='HZ', polarity='NOR'):
    return {'waveform': waveform, 'freq_hz': freq, 'amp_vpp': amp,
            'offset_v': offset, 'output': output, 'load': load,
            'polarity': polarity}


def test_validate_normalises():
    out = validate_channel_state({'waveform': 'sine', 'freq_hz': '1000',
                                  'amp_vpp': 2, 'polarity': 'nor'})
    assert out['waveform'] == 'SINE'
    assert out['freq_hz'] == 1000.0 and isinstance(out['freq_hz'], float)
    assert out['amp_vpp'] == 2.0
    assert out['polarity'] == 'NOR'
    assert out['offset_v'] == 0.0  # default filled in


def test_validate_optional_keys():
    # Waveform-specific keys are kept (coerced to float) when present...
    out = validate_channel_state({'waveform': 'SQUARE', 'duty_pct': '30'})
    assert out['duty_pct'] == 30.0
    out = validate_channel_state({'waveform': 'PULSE', 'rise_s': 1e-6,
                                  'fall_s': 2e-6, 'delay_s': 0})
    assert out['rise_s'] == 1e-6 and out['fall_s'] == 2e-6
    # ...absent when not given (old presets stay loadable)
    out = validate_channel_state({'waveform': 'SINE'})
    assert 'duty_pct' not in out and 'sym_pct' not in out
    # ...and rejected when non-numeric
    try:
        validate_channel_state({'waveform': 'RAMP', 'sym_pct': 'half'})
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for sym_pct='half'")


def test_validate_rejects_bad():
    for bad in ({'waveform': 'TRIANGLE'}, {'amp_vpp': 'abc'},
                {'polarity': 'SIDEWAYS'}):
        try:
            validate_channel_state(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad}")


def test_roundtrip(tmp_path):
    store = SignalGenPresetStore(path=tmp_path)
    store.save('tone', {1: _state(freq=440.0), 2: _state(waveform='SQUARE')})
    assert store.names() == ['tone']

    # Reload from disk in a fresh instance.
    store2 = SignalGenPresetStore(path=tmp_path)
    rec = store2.get('tone')
    assert rec['channels']['1']['freq_hz'] == 440.0
    assert rec['channels']['2']['waveform'] == 'SQUARE'
    assert 'saved_utc' in rec

    assert store2.delete('tone') is True
    assert store2.delete('tone') is False
    assert store2.names() == []


def test_overwrite(tmp_path):
    store = SignalGenPresetStore(path=tmp_path)
    store.save('p', {1: _state(amp=1.0), 2: _state()})
    store.save('p', {1: _state(amp=5.0), 2: _state()})
    assert store.get('p')['channels']['1']['amp_vpp'] == 5.0
    assert store.names() == ['p']


def test_empty_name_rejected(tmp_path):
    store = SignalGenPresetStore(path=tmp_path)
    try:
        store.save('   ', {1: _state(), 2: _state()})
    except ValueError:
        return
    raise AssertionError("expected ValueError for empty preset name")


def test_corrupt_file_recovered(tmp_path):
    with open(tmp_path, 'w') as f:
        f.write('{not valid json')
    store = SignalGenPresetStore(path=tmp_path)  # must not raise
    assert store.names() == []
    assert os.path.exists(tmp_path + '.corrupt')  # moved aside


def test_atomic_write_is_valid_json(tmp_path):
    store = SignalGenPresetStore(path=tmp_path)
    store.save('x', {1: _state(), 2: _state()})
    with open(tmp_path) as f:
        data = json.load(f)
    assert data['version'] == 1
    assert 'x' in data['presets']
    assert not os.path.exists(tmp_path + '.tmp')  # temp cleaned up


def test_arb_csv_value_only(tmp_path):
    path = os.path.join(os.path.dirname(tmp_path), 'wave.csv')
    with open(path, 'w') as f:
        f.write("value\n0.0\n0.5\n\n-0.5\n")     # incl. a blank line
    assert arb_from_csv(path) == [0.0, 0.5, -0.5]


def test_arb_csv_time_value(tmp_path):
    path = os.path.join(os.path.dirname(tmp_path), 'wave2.csv')
    with open(path, 'w') as f:
        f.write("time,value\n0,0.1\n1e-3,0.2\n2e-3,0.3\n")
    assert arb_from_csv(path) == [0.1, 0.2, 0.3]


def test_arb_csv_rejects_bad(tmp_path):
    base = os.path.dirname(tmp_path)
    no_header = os.path.join(base, 'bad1.csv')
    with open(no_header, 'w') as f:
        f.write("volts\n1\n2\n")                 # wrong header
    bad_row = os.path.join(base, 'bad2.csv')
    with open(bad_row, 'w') as f:
        f.write("value\n0.1\noops\n")
    for path, fragment in ((no_header, "'value'"), (bad_row, 'line 3')):
        try:
            arb_from_csv(path)
        except ValueError as e:
            assert fragment in str(e)
        else:
            raise AssertionError(f"expected ValueError for {path}")


def test_arb_template_roundtrip(tmp_path):
    path = os.path.join(os.path.dirname(tmp_path), 'template.csv')
    write_arb_template(path)
    samples = arb_from_csv(path)
    assert len(samples) == 32                    # one sine period
    assert max(samples) <= 1.0 and min(samples) >= -1.0
    assert abs(samples[0]) < 1e-6                # sin(0)


def test_arb_library_roundtrip(tmp_path):
    store = SignalGenPresetStore(path=tmp_path)
    store.arb_dir = os.path.join(os.path.dirname(tmp_path), 'arb')
    assert store.arb_names() == []
    clean = store.save_arb('stair case', [0.0, 0.5, 1.0])
    assert clean == 'stair_case'                 # sanitised
    assert store.arb_names() == ['stair_case']
    assert store.load_arb('stair_case') == [0.0, 0.5, 1.0]
    assert store.delete_arb('stair_case') is True
    assert store.delete_arb('stair_case') is False
    assert store.arb_names() == []


def test_arb_recipe_sidecar(tmp_path):
    store = SignalGenPresetStore(path=tmp_path)
    store.arb_dir = os.path.join(os.path.dirname(tmp_path), 'arb')
    recipe = {'version': 1, 'total_points': 8,
              'breakpoints': [[0, 0.0], [1, 0.5]],
              'segments': [{'type': 'LINE', 'params': {}}]}
    store.save_arb('rec', [0.0, 0.25, 0.5], recipe=recipe)
    assert store.load_arb_recipe('rec') == recipe
    # saving again without a recipe drops the now-stale sidecar
    store.save_arb('rec', [0.1, 0.2, 0.3])
    assert store.load_arb_recipe('rec') is None
    assert store.delete_arb('rec') is True
    assert store.arb_names() == []


def test_arb_name_in_channel_state():
    out = validate_channel_state({'waveform': 'ARB', 'arb_name': 'my wave!'})
    assert out['arb_name'] == 'my_wave_'         # sanitised
    out = validate_channel_state({'waveform': 'SINE'})
    assert 'arb_name' not in out                 # absent when not given
    assert sanitize_arb_name('x' * 40) == 'x' * 16   # length cap


if __name__ == '__main__':
    import inspect
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    passed = 0
    for t in tests:
        if 'tmp_path' in inspect.signature(t).parameters:
            with tempfile.TemporaryDirectory() as d:
                t(os.path.join(d, 'presets.json'))
        else:
            t()
        print(f"PASS {t.__name__}")
        passed += 1
    print(f"\nAll {passed} preset-store tests passed.")
