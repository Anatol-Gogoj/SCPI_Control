#!/usr/bin/env python3
"""Headless tests for the BK9174B DC-supply driver (no hardware).

A fake serial captures the bytes written and feeds canned replies, so we can
verify SCPI command formatting, response parsing, and the dual-range safety
clamp without opening /dev/ttyUSB0.

Run: .venv/bin/python tests/test_bk9174b_parse.py
"""
# Runnable from anywhere: put the repo root (one level up) on sys.path.
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))

from instruments import BK9174B


class FakeSerial:
    """Minimal serial stand-in: records writes as decoded strings and returns
    queued replies (each auto-terminated with CR+LF unless already bytes)."""

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


def test_terminator_and_channel_select():
    p = _psu()
    p.select_channel(2)
    assert p.ser.written == ['INST:SEL CH2\r\n'], p.ser.written


def test_set_voltage_selects_then_sets():
    p = _psu()
    p.set_voltage(1, 12)
    assert p.ser.written == ['INST:SEL CH1\r\n', 'VOLT 12\r\n'], p.ser.written


def test_set_current_selects_then_sets():
    p = _psu()
    p.set_current(2, 0.3)
    assert p.ser.written == ['INST:SEL CH2\r\n', 'CURR 0.3\r\n'], p.ser.written


def test_fmt_plain_decimal():
    assert BK9174B._fmt(12.0) == '12'
    assert BK9174B._fmt(0.3) == '0.3'
    assert BK9174B._fmt(1.5) == '1.5'
    assert BK9174B._fmt(0.0001) == '0.0001'      # mA-scale, no exponent
    assert BK9174B._fmt(0) == '0'


def test_measure_parses_unit_suffix():
    p = _psu(['12.003V', '0.2998A'])
    assert abs(p.measure_voltage(1) - 12.003) < 1e-6
    assert abs(p.measure_current(1) - 0.2998) < 1e-6


def test_output_query_truthiness():
    assert _psu(['ON']).get_output(1) is True
    assert _psu(['1']).get_output(1) is True
    assert _psu(['OFF']).get_output(1) is False
    assert _psu(['0']).get_output(1) is False


def test_read_channel_batches_and_computes_power():
    # replies in order: VOLT?, MEAS:VOLT?, MEAS:CURR?
    p = _psu(['12.000', '11.998', '0.3000'])
    r = p.read_channel(1)
    assert r['channel'] == 1
    assert abs(r['set_voltage_v'] - 12.0) < 1e-6
    assert abs(r['meas_voltage_v'] - 11.998) < 1e-6
    assert abs(r['meas_current_a'] - 0.3) < 1e-6
    assert abs(r['power_w'] - 11.998 * 0.3) < 1e-4
    # the whole poll selects the channel exactly once
    assert p.ser.written.count('INST:SEL CH1\r\n') == 1, p.ser.written


def test_apply_writes_v_then_i_leaves_output():
    p = _psu()
    p.apply(1, 5, 1.0)
    assert p.ser.written == ['INST:SEL CH1\r\n', 'VOLT 5\r\n', 'CURR 1\r\n'], \
        p.ser.written
    p = _psu()
    p.apply(2, 5, 1.0, output=True)
    assert p.ser.written[-1] == 'OUTP ON\r\n', p.ser.written


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
            p.select_channel(ch)
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
