#!/usr/bin/env python3
"""Headless tests for SignalGenPresetStore (no instrument needed).

Round-trips presets through a temp file and checks validation/corruption
handling. Run: .venv/bin/python test_siggen_presets.py
"""
import os
import json
import tempfile

from siggen_presets import SignalGenPresetStore, validate_channel_state


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
