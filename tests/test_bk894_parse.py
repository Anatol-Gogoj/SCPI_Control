#!/usr/bin/env python3
"""Headless tests for the BK894 driver additions (issue #44, no instrument).

Response formats were captured from the real meter (fw 1.0.5, 2026-07-10):
:BIAS:VOLT? -> '0.00000e+00', :BIAS:STAT? -> '0', :APER? -> 'MED,1',
:CORR:OPEN:STAT? -> '1'. Run: .venv/bin/python tests/test_bk894_parse.py
"""
# Runnable from anywhere: put the repo root (one level up) on sys.path
# so the app modules import when this file is executed directly.
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))
import types

from instruments import BK894


class FakeLCR(BK894):
    """BK894 with the transport stubbed (skips VisaInstrument.__init__)."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.sent = []
        self.inst = types.SimpleNamespace(timeout=2000)

    def ask(self, command):
        self.sent.append(command)
        return self.answers[command]

    def write(self, command):
        self.sent.append(command)


def test_parse_aperture():
    assert BK894.parse_aperture('MED,1') == ('MED', 1)
    assert BK894.parse_aperture('SLOW,32') == ('SLOW', 32)
    assert BK894.parse_aperture('fast,4') == ('FAST', 4)
    assert BK894.parse_aperture('MED') == ('MED', 1), "missing count -> 1"


def test_get_bias_parses_meter_format():
    lcr = FakeLCR({':BIAS:VOLT?': '0.00000e+00', ':BIAS:STAT?': '0'})
    assert lcr.get_bias() == {'volts': 0.0, 'on': False}
    lcr = FakeLCR({':BIAS:VOLT?': '1.50000e+00', ':BIAS:STAT?': '1'})
    assert lcr.get_bias() == {'volts': 1.5, 'on': True}


def test_set_bias_validation():
    lcr = FakeLCR()
    lcr.set_bias_voltage(-2.0)
    assert lcr.sent == [':BIAS:VOLT -2.0']
    for bad in (5.1, -6.0):
        try:
            lcr.set_bias_voltage(bad)
            assert False, f"{bad} must raise"
        except ValueError:
            pass
    lcr.set_bias_enabled(True)
    lcr.set_bias_enabled(False)
    assert lcr.sent[-2:] == [':BIAS:STAT 1', ':BIAS:STAT 0']


def test_set_aperture_validation():
    lcr = FakeLCR()
    lcr.set_aperture('slow', 16)
    assert lcr.sent == [':APER SLOW,16']
    for bad_call in (lambda: lcr.set_aperture('TURBO'),
                     lambda: lcr.set_aperture('MED', 0),
                     lambda: lcr.set_aperture('MED', 257)):
        try:
            bad_call()
            assert False, "must raise"
        except ValueError:
            pass


def test_correction_states():
    lcr = FakeLCR({':CORR:OPEN:STAT?': '1', ':CORR:SHOR:STAT?': '0'})
    assert lcr.get_correction_states() == {'open': True, 'short': False}


def test_run_correction_sequence_and_timeout_restore():
    lcr = FakeLCR({':CORR:OPEN:STAT?': '1'})
    lcr.run_correction('open')
    # sweep command, completion-blocking query, then enable
    assert lcr.sent == [':CORR:OPEN', ':CORR:OPEN:STAT?', ':CORR:OPEN:STAT 1']
    assert lcr.inst.timeout == 2000, "timeout must be restored"

    # timeout must be restored even when the sweep query fails
    class Boom(FakeLCR):
        def ask(self, command):
            raise RuntimeError("sweep timed out")
    lcr = Boom()
    try:
        lcr.run_correction('short')
        assert False, "must propagate"
    except RuntimeError:
        pass
    assert lcr.inst.timeout == 2000


def test_range_auto():
    lcr = FakeLCR()
    lcr.set_range_auto(True)
    lcr.set_range_auto(False)
    assert lcr.sent == [':FUNC:IMP:RANG:AUTO 1', ':FUNC:IMP:RANG:AUTO 0']


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed")


if __name__ == '__main__':
    _run()
