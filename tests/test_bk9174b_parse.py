#!/usr/bin/env python3
"""Headless tests for the BK9174B DC-supply driver (no hardware).

A fake serial captures the bytes written and feeds canned replies, so we can
verify SCPI command formatting, response parsing, and the dual-range safety
clamp without opening /dev/ttyUSB0.

Channel model (9170B/9180B manual section 4.2, verified on the unit
2026-07-22): NO channel-select command -- CH1 is the bare token, CH2 is the
'2'-suffixed token (VOLT/VOLT2, MEAS:VOLT?/MEAS:VOLT2?, OUT/OUT2,
PROT:OVP:LEV/PROT:OVP2:LEV).

Run: .venv/bin/python tests/test_bk9174b_parse.py
"""
# Runnable from anywhere: put the repo root (one level up) on sys.path.
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))

from instruments import BK9174B


class FakeSerial:
    """Records writes as decoded strings and returns queued replies (each
    auto-terminated with CR+LF unless already bytes)."""

    def __init__(self, replies=None):
        self.written = []
        self._replies = list(replies or [])

    def write(self, data):
        self.written.append(data.decode('ascii'))
        return len(data)

    def readline(self):
        if self._replies:
            r = self._replies.pop(0)
            return r if isinstance(r, bytes) else (r + '\r\n').encode('ascii')
        return b''

    def close(self):
        pass


def _psu(replies=None):
    return BK9174B(transport=FakeSerial(replies), identify=False)


def test_ch1_bare_ch2_suffixed_no_select():
    p = _psu()
    p.set_voltage(1, 12)
    p.set_voltage(2, 7)
    assert p.ser.written == ['VOLT 12\r\n', 'VOLT2 7\r\n'], p.ser.written
    # crucially: NO channel-select command is ever emitted
    assert not any('INST' in c or 'SEL' in c for c in p.ser.written)


def test_set_current_suffix():
    p = _psu()
    p.set_current(1, 3)
    p.set_current(2, 0.3)
    assert p.ser.written == ['CURR 3\r\n', 'CURR2 0.3\r\n'], p.ser.written


def test_set_output_tokens():
    p = _psu()
    p.set_output(1, True)
    p.set_output(2, False)
    assert p.ser.written == ['OUT ON\r\n', 'OUT2 OFF\r\n'], p.ser.written


def test_fmt_plain_decimal():
    assert BK9174B._fmt(12.0) == '12'
    assert BK9174B._fmt(0.3) == '0.3'
    assert BK9174B._fmt(1.5) == '1.5'
    assert BK9174B._fmt(0.0001) == '0.0001'      # mA-scale, no exponent
    assert BK9174B._fmt(0) == '0'


def test_measure_addresses_right_channel():
    p = _psu(['12.006V'])
    assert abs(p.measure_voltage(1) - 12.006) < 1e-6
    assert p.ser.written == ['MEAS:VOLT?\r\n'], p.ser.written
    p = _psu(['4.998V'])
    assert abs(p.measure_voltage(2) - 4.998) < 1e-6
    assert p.ser.written == ['MEAS:VOLT2?\r\n'], p.ser.written
    p = _psu(['0.2998A'])
    assert abs(p.measure_current(2) - 0.2998) < 1e-6
    assert p.ser.written == ['MEAS:CURR2?\r\n'], p.ser.written


def test_output_query_truthiness():
    assert _psu(['ON']).get_output(1) is True
    assert _psu(['OFF']).get_output(2) is False
    p = _psu(['ON'])
    p.get_output(2)
    assert p.ser.written == ['OUT2?\r\n'], p.ser.written


def test_read_channel_ch2_uses_suffix_and_computes_power():
    # replies in order: VOLT2?, MEAS:VOLT2?, MEAS:CURR2?
    p = _psu(['5.000', '4.998', '1.0000'])
    r = p.read_channel(2)
    assert r['channel'] == 2
    assert abs(r['set_voltage_v'] - 5.0) < 1e-6
    assert abs(r['meas_voltage_v'] - 4.998) < 1e-6
    assert abs(r['power_w'] - 4.998 * 1.0) < 1e-4
    assert p.ser.written == ['VOLT2?\r\n', 'MEAS:VOLT2?\r\n',
                             'MEAS:CURR2?\r\n'], p.ser.written


def test_apply_writes_v_then_i_then_optional_output():
    p = _psu()
    p.apply(1, 5, 1.0)
    assert p.ser.written == ['VOLT 5\r\n', 'CURR 1\r\n'], p.ser.written
    p = _psu()
    p.apply(2, 5, 1.0, output=True)
    assert p.ser.written == ['VOLT2 5\r\n', 'CURR2 1\r\n', 'OUT2 ON\r\n'], \
        p.ser.written


def test_protection_level_and_arm():
    p = _psu()
    p.set_ovp(1, 20)
    assert p.ser.written == ['PROT:OVP:LEV 20\r\n', 'PROT:OVP ON\r\n'], \
        p.ser.written
    p = _psu()
    p.set_ocp(2, 1.5)
    assert p.ser.written == ['PROT:OCP2:LEV 1.5\r\n', 'PROT:OCP2 ON\r\n'], \
        p.ser.written


def test_dual_range_envelope_clamp():
    p = _psu()
    p.apply(1, 30, 3.0)                 # low range: 3 A ok
    p.apply(1, 70, 1.5)                 # high range: 1.5 A ok
    for v, a, why in ((40, 3.0, '3 A above 35 V'),
                      (70, 2.0, '2 A above 35 V'),
                      (80, 0.1, '80 V over max')):
        try:
            p.apply(1, v, a)
            raise AssertionError(f"apply({v},{a}) must raise ({why})")
        except ValueError:
            pass


def test_bounds_and_bad_channel():
    p = _psu()
    for ch in (0, 3, 'x', None):
        try:
            p.set_voltage(ch, 5)
            raise AssertionError(f"channel {ch!r} must raise")
        except ValueError:
            pass
    try:
        p.set_voltage(1, 80)
        raise AssertionError("80 V must raise")
    except ValueError:
        pass
    try:
        p.set_current(1, 5)
        raise AssertionError("5 A must raise")
    except ValueError:
        pass


def test_get_error_parses_code():
    assert _psu(['0,No Error']).get_error()[0] == 0
    assert _psu(['1,Command Error']).get_error()[0] == 1
    assert _psu(['4,Input Range Error']).get_error()[0] == 4


def test_non_numeric_reply_raises():
    p = _psu(['ERR'])
    try:
        p.measure_voltage(1)
        raise AssertionError("non-numeric reply must raise")
    except ValueError:
        pass


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed")


if __name__ == '__main__':
    _run()
