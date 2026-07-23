#!/usr/bin/env python3
"""Headless tests for the BK5493C DMM driver (no hardware).

A fake pyvisa-style instrument records queries and returns canned replies, so
we verify the function->SCPI mapping and reply parsing without a socket.

Run: .venv/bin/python tests/test_bk5493c_parse.py
"""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))

from instruments import BK5493C


class FakeInst:
    def __init__(self, replies=None):
        self.replies = replies or {}
        self.queries = []
        self.writes = []

    def query(self, cmd):
        self.queries.append(cmd)
        return self.replies.get(cmd, '0')

    def write(self, cmd):
        self.writes.append(cmd)

    def close(self):
        pass


def _dmm(replies=None):
    r = {'*IDN?': 'BK Precision,5493C,W117229033,V1.4.19'}
    r.update(replies or {})
    return BK5493C(transport=FakeInst(r))


def test_idn_read():
    d = _dmm()
    assert d.idn == 'BK Precision,5493C,W117229033,V1.4.19'


def test_function_to_scpi_and_parse():
    d = _dmm({'MEAS:VOLT:DC?': '1.674E-05', 'MEAS:RES?': '9.987E+02',
              'MEAS:FREQ?': '1.0000E+03'})
    assert abs(d.measure('DC Voltage') - 1.674e-5) < 1e-9
    assert d.inst.queries[-1] == 'MEAS:VOLT:DC?'
    assert abs(d.measure('2W Resistance') - 998.7) < 1e-3
    assert d.inst.queries[-1] == 'MEAS:RES?'
    assert d.measure('Frequency') == 1000.0


def test_units_and_labels():
    d = _dmm()
    assert d.unit('DC Voltage') == 'V'
    assert d.unit('2W Resistance') == 'ohm'
    labels = BK5493C.function_labels()
    assert 'DC Voltage' in labels and 'Frequency' in labels
    assert BK5493C.PORT == 45454


def test_overload_returns_none():
    d = _dmm({'MEAS:VOLT:DC?': '9.9E37 OVLD'})   # non-numeric -> None
    assert d.measure('DC Voltage') is None


def test_bad_function_raises():
    d = _dmm()
    try:
        d.measure('Inductance')
        raise AssertionError("unknown function must raise")
    except ValueError:
        pass


def test_go_local_writes_syst_loc():
    d = _dmm()
    d.go_local()
    assert 'SYST:LOC' in d.inst.writes


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed")


if __name__ == '__main__':
    _run()
