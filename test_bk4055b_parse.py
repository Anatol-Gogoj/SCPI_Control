#!/usr/bin/env python3
"""Headless tests for the BK4055B response parsers (no instrument needed).

Feeds captured BSWV?/OUTP? response strings into the parsers and asserts the
resulting dicts. Run: .venv/bin/python test_bk4055b_parse.py
"""
from instruments import BK4055B


class FakeSG(BK4055B):
    """BK4055B with the transport stubbed out (skips VisaInstrument.__init__)."""
    def __init__(self, bswv=None, outp=None):
        self._bswv = bswv
        self._outp = outp

    def get_basic_wave(self, channel):
        return self._bswv

    def ask(self, command):
        return self._outp


def test_strip_unit():
    assert BK4055B._strip_unit('100HZ') == 100.0
    assert BK4055B._strip_unit('0.01S') == 0.01
    assert BK4055B._strip_unit('-1V') == -1.0
    assert BK4055B._strip_unit('1.41421Vrms') == 1.41421
    assert BK4055B._strip_unit('2.4e-07S') == 2.4e-07
    assert BK4055B._strip_unit('HZ') == 'HZ'  # no number -> passthrough


def test_basic_wave_sine():
    sg = FakeSG(bswv='C1:BSWV WVTP,SINE,FRQ,100HZ,PERI,0.01S,AMP,2V,'
                     'OFST,0V,HLEV,1V,LLEV,-1V,PHSE,0')
    d = sg.get_basic_wave_dict(1)
    assert d['WVTP'] == 'SINE'
    assert d['FRQ'] == 100.0
    assert d['AMP'] == 2.0
    assert d['OFST'] == 0.0
    assert d['PHSE'] == 0.0


def test_basic_wave_square_with_duty():
    sg = FakeSG(bswv='C2:BSWV WVTP,SQUARE,FRQ,1000HZ,AMP,3V,OFST,0V,DUTY,30')
    d = sg.get_basic_wave_dict(2)
    assert d['WVTP'] == 'SQUARE'
    assert d['DUTY'] == 30.0


def test_basic_wave_no_echo_prefix():
    # Tolerate a response without the 'C1:BSWV ' echo.
    sg = FakeSG(bswv='WVTP,RAMP,FRQ,50HZ,AMP,1V,SYM,50')
    d = sg.get_basic_wave_dict(1)
    assert d['WVTP'] == 'RAMP'
    assert d['SYM'] == 50.0


def test_output_dict():
    sg = FakeSG(outp='C1:OUTP ON,LOAD,HZ,PLRT,NOR')
    d = sg.get_output_dict(1)
    assert d == {'state': True, 'load': 'HZ', 'polarity': 'NOR'}

    sg = FakeSG(outp='C2:OUTP OFF,LOAD,50,PLRT,INVT')
    d = sg.get_output_dict(2)
    assert d == {'state': False, 'load': '50', 'polarity': 'INVT'}


def test_output_dict_minimal():
    # Some firmware returns just the state.
    sg = FakeSG(outp='C1:OUTP OFF')
    d = sg.get_output_dict(1)
    assert d['state'] is False
    assert d['load'] == 'HZ'        # default
    assert d['polarity'] == 'NOR'   # default


if __name__ == '__main__':
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\nAll {len(tests)} parser tests passed.")
