#!/usr/bin/env python3
"""Headless tests for the BK4055B response parsers (no instrument needed).

Feeds captured BSWV?/OUTP? response strings into the parsers and asserts the
resulting dicts/bytes. Run: .venv/bin/python test_bk4055b_parse.py
"""
import struct
import types

from instruments import BK4055B


class FakeSG(BK4055B):
    """BK4055B with the transport stubbed out (skips VisaInstrument.__init__)."""
    def __init__(self, bswv=None, outp=None):
        self._bswv = bswv
        self._outp = outp
        self.sent = []
        self.sent_raw = []
        self.inst = types.SimpleNamespace(timeout=2000)   # upload_arb tweaks this

    def get_basic_wave(self, channel):
        return self._bswv

    def ask(self, command):
        return self._outp

    def write(self, command):
        self.sent.append(command)

    def write_raw(self, data):
        self.sent_raw.append(data)

    def write_raw_oneshot(self, data):
        self.sent_raw.append(data)


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


def test_set_basic_wave_number_formatting():
    # Bench-found bug (2026-06-11): 'DUTY,50.0' lands as 5% duty on the
    # 4055B. Floats must be sent without a trailing '.0' and without
    # scientific notation.
    sg = FakeSG()
    sg.set_basic_wave(1, WVTP='SQUARE', FRQ=1000.0, AMP=2.0, OFST=0.0,
                      DUTY=50.0, PHSE=0.0)
    assert sg.sent[0] == ('C1:BSWV WVTP,SQUARE,FRQ,1000,AMP,2,OFST,0,'
                          'DUTY,50,PHSE,0')

    sg.set_basic_wave(2, DUTY=12.5, RISE=1.68e-8)
    assert sg.sent[1] == 'C2:BSWV DUTY,12.5,RISE,0.0000000168'
    assert 'e' not in sg.sent[1].split('RISE,')[1]   # no exponent form

    sg.set_frequency(1, 2500.0)
    assert sg.sent[2] == 'C1:BSWV FRQ,2500'


def test_fmt_param():
    f = BK4055B._fmt_param
    assert f(50.0) == '50'
    assert f(0.0) == '0'
    assert f(12.5) == '12.5'
    assert f(1e-6) == '0.000001'
    assert f(2e6) == '2000000'
    assert f(50) == '50'          # ints pass through
    assert f('HZ') == 'HZ'        # strings pass through


def test_output_dict_minimal():
    # Some firmware returns just the state.
    sg = FakeSG(outp='C1:OUTP OFF')
    d = sg.get_output_dict(1)
    assert d['state'] is False
    assert d['load'] == 'HZ'        # default
    assert d['polarity'] == 'NOR'   # default


def test_samples_to_int16():
    data = BK4055B.samples_to_int16([0.0, 1.0, -1.0, 0.5])
    vals = struct.unpack('<4h', data)
    assert vals == (0, 32767, -32767, 16384)   # 0.5 * 32767 rounds to 16384

    # |samples| > 1 are normalised to full scale
    data = BK4055B.samples_to_int16([2.0, -2.0, 1.0])
    vals = struct.unpack('<3h', data)
    assert vals == (32767, -32767, 16384)      # 1.0/2.0 -> half scale

    try:
        BK4055B.samples_to_int16([])
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for empty samples")


def test_upload_arb_message():
    sg = FakeSG()
    samples = [0.0, 1.0, 0.0, -1.0] * 8        # 32 points
    clean = sg.upload_arb(1, 'my wave!', samples, points=32)
    assert clean == 'my_wave_'                 # sanitised name returned
    assert len(sg.sent_raw) == 1
    msg = sg.sent_raw[0]
    # Full official header (Siglent 16-bit-arb app note): every field always
    # present, TYPE,8, no LENGTH. Minimal headers wedge the box over USBTMC.
    header = (b'C1:WVDT WVNM,my_wave_,FREQ,1000,TYPE,8,'
              b'AMPL,2,OFST,0,PHASE,0,WAVEDATA,')
    assert msg.startswith(header)
    assert b'LENGTH' not in msg
    payload = msg[len(header):]
    assert len(payload) == 32 * 2
    assert struct.unpack('<4h', payload[:8]) == (0, 32767, 0, -32767)


def test_upload_arb_defaults_to_short_buffer():
    # No explicit points -> short ARB_DEFAULT_POINTS buffer (not 16384).
    sg = FakeSG()
    sg.upload_arb(1, 'short', [0.0, 1.0, 0.0, -1.0] * 1000)
    payload = sg.sent_raw[0].split(b'WAVEDATA,', 1)[1]
    assert len(payload) == BK4055B.ARB_DEFAULT_POINTS * 2


def test_select_arb_and_arwv_parse():
    sg = FakeSG(outp='C1:ARWV INDEX,2,NAME,wave1')
    sg.select_arb(1, 'wave1')
    assert sg.sent == ['C1:BSWV WVTP,ARB', 'C1:ARWV NAME,wave1']
    assert sg.get_arb_dict(1) == {'index': 2, 'name': 'wave1'}

    sg = FakeSG(outp='C2:ARWV NAME,stairs')    # INDEX missing
    assert sg.get_arb_dict(2) == {'index': None, 'name': 'stairs'}


if __name__ == '__main__':
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\nAll {len(tests)} parser tests passed.")
