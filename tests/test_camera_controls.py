#!/usr/bin/env python3
"""Headless tests for the camera-control lock layer (no camera).

Run: .venv/bin/python tests/test_camera_controls.py
"""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))
import os
import shutil
import tempfile

import webcam

# the exact --list-ctrls-menus output of the bench DFK 37BUX250 (2026-07-24)
SAMPLE = """
User Controls

                     brightness 0x00980900 (int)    : min=0 max=4095 step=1 default=240 value=240
        white_balance_automatic 0x0098090c (bool)   : default=1 value=0
                    red_balance 0x0098090e (int)    : min=0 max=255 step=1 default=64 value=204
                   blue_balance 0x0098090f (int)    : min=0 max=255 step=1 default=64 value=104
                           gain 0x00980913 (int)    : min=0 max=480 step=1 default=0 value=44

Camera Controls

                  auto_exposure 0x009a0901 (menu)   : min=0 max=3 default=3 value=1
\t\t\t\t1: Manual Mode
\t\t\t\t3: Aperture Priority Mode
         exposure_time_absolute 0x009a0902 (int)    : min=1 max=40000 step=1 default=3 value=20
"""


def test_parse_controls_real_output():
    ctrls = webcam.parse_controls(SAMPLE)
    byname = {c['name']: c for c in ctrls}
    assert set(byname) == {'brightness', 'white_balance_automatic',
                           'red_balance', 'blue_balance', 'gain',
                           'auto_exposure', 'exposure_time_absolute'}
    assert byname['brightness']['min'] == 0
    assert byname['brightness']['max'] == 4095
    assert byname['gain']['value'] == 44
    assert byname['white_balance_automatic']['type'] == 'bool'
    assert byname['auto_exposure']['type'] == 'menu'
    assert byname['auto_exposure']['menu'] == {1: 'Manual Mode',
                                               3: 'Aperture Priority Mode'}
    assert byname['exposure_time_absolute']['max'] == 40000


def test_apply_locked_orders_autos_first():
    calls = []
    orig = webcam.set_control
    webcam.set_control = lambda dev, name, val: calls.append((name, val))
    try:
        webcam.set_locked({'red_balance': 92, 'auto_exposure': 1,
                           'exposure_time_absolute': 20,
                           'white_balance_automatic': 0})
        n = webcam.apply_locked('/dev/videoX')
        assert n == 4
        names = [c[0] for c in calls]
        # both autos land before any dependent value
        assert names.index('auto_exposure') < names.index(
            'exposure_time_absolute')
        assert names.index('white_balance_automatic') < names.index(
            'red_balance')
    finally:
        webcam.set_control = orig
        webcam.set_locked({})


def test_apply_locked_noop_when_unlocked():
    webcam.set_locked({})
    assert webcam.apply_locked('/dev/videoX') == 0


def test_apply_locked_can_exclude_gain():
    # The live-preview re-stamp skips 'gain' (firmware AGC overrides it and
    # re-writing just flickers) but must still hold everything else.
    calls = []
    orig = webcam.set_control
    webcam.set_control = lambda dev, name, val: calls.append(name)
    try:
        webcam.set_locked({'gain': 44, 'exposure_time_absolute': 20,
                           'white_balance_automatic': 0, 'red_balance': 92})
        n = webcam.apply_locked('/dev/videoX', exclude={'gain'})
        assert 'gain' not in calls
        assert 'exposure_time_absolute' in calls and 'red_balance' in calls
        assert n == 3
    finally:
        webcam.set_control = orig
        webcam.set_locked({})


def test_settings_persist_roundtrip():
    d = tempfile.mkdtemp(prefix='camctl_')
    try:
        path = os.path.join(d, 'sub', 'camera_controls.json')
        webcam.save_camera_settings({'gain': 44, 'red_balance': 92},
                                    path=path)
        back = webcam.load_camera_settings(path=path)
        assert back == {'gain': 44, 'red_balance': 92}
        assert webcam.load_camera_settings(
            path=os.path.join(d, 'missing.json')) == {}
    finally:
        shutil.rmtree(d)


def test_save_falls_back_when_primary_unwritable():
    # A root-owned ~/.local/share/scpi_control (installer artefact) must not
    # break persistence: save lands in the fallback, load finds it there.
    d = tempfile.mkdtemp(prefix='camctl_fb_')
    ro = os.path.join(d, 'ro')
    os.makedirs(ro)
    os.chmod(ro, 0o555)                       # unwritable "primary" parent
    prim_bak = webcam.CAMERA_SETTINGS_PATH
    fall_bak = webcam.CAMERA_SETTINGS_FALLBACK
    webcam.CAMERA_SETTINGS_PATH = os.path.join(ro, 'sub', 'cam.json')
    webcam.CAMERA_SETTINGS_FALLBACK = os.path.join(d, 'cache', 'cam.json')
    try:
        saved = webcam.save_camera_settings({'gain': 7})
        assert saved == webcam.CAMERA_SETTINGS_FALLBACK, saved
        assert webcam.load_camera_settings() == {'gain': 7}
    finally:
        webcam.CAMERA_SETTINGS_PATH = prim_bak
        webcam.CAMERA_SETTINGS_FALLBACK = fall_bak
        os.chmod(ro, 0o755)
        shutil.rmtree(d)


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed")


if __name__ == '__main__':
    _run()
