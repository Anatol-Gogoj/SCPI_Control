#!/usr/bin/env python3
"""Headless tests for the BK4055B arb transport (X-series WVDT + TrueArb SRATE).

No instrument needed -- a FakeSG captures the bytes/commands the driver would
send. Verifies the spec-correct WVDT framing (no LENGTH/TYPE), short-buffer
default, int16 LE payload, and the SRATE (TrueArb) command. Run:
    .venv/bin/python test_arb_transport.py
"""
import struct
import types

from instruments import BK4055B


class FakeSG(BK4055B):
    """BK4055B with the transport stubbed (skips VisaInstrument.__init__)."""
    def __init__(self):
        self.sent = []
        self.sent_raw = []
        self.inst = types.SimpleNamespace(timeout=2000)

    def write(self, command):
        self.sent.append(command)

    def write_raw(self, data):
        self.sent_raw.append(data)

    def write_raw_oneshot(self, data):
        self.sent_raw.append(data)


def _split_wvdt(blob):
    """Return (ascii_header_without_trailing_comma, payload_bytes)."""
    marker = b'WAVEDATA,'
    idx = blob.index(marker) + len(marker)
    return blob[:idx].decode('latin1'), blob[idx:]


def test_wvdt_has_no_length_or_type():
    sg = FakeSG()
    _, blob = sg.build_wvdt(1, 'wave1', [0.0, 1.0, 0.0, -1.0], points=4)
    header, _ = _split_wvdt(blob)
    assert header.startswith('C1:WVDT WVNM,wave1'), header
    assert 'LENGTH' not in header, "X-series WVDT must omit LENGTH"
    assert 'TYPE' not in header, "X-series WVDT must omit TYPE"
    assert header.endswith('WAVEDATA,'), header


def test_wvdt_optional_fields():
    sg = FakeSG()
    _, blob = sg.build_wvdt(2, 'w', [0.0, 1.0], points=2,
                            freq_hz=1000.0, amp_vpp=2.0, offset_v=0.0,
                            phase_deg=0.0)
    header, _ = _split_wvdt(blob)
    assert header.startswith('C2:WVDT WVNM,w,')
    # _fmt_param strips trailing .0
    assert 'FREQ,1000' in header and 'AMPL,2' in header
    assert 'OFST,0' in header and 'PHASE,0' in header


def test_payload_is_int16_le_fullscale():
    sg = FakeSG()
    _, blob = sg.build_wvdt(1, 'w', [0.0, 1.0, -1.0, 0.0] * 2, points=8)
    _, payload = _split_wvdt(blob)
    assert len(payload) == 8 * 2, "8 points -> 16 bytes int16"
    vals = struct.unpack('<8h', payload)
    assert vals[1] == 32767 and vals[2] == -32767, vals  # +/- full scale
    assert vals[0] == 0 and vals[3] == 0, vals


def test_default_points_is_short():
    sg = FakeSG()
    big = [0.0] * 8000
    _, blob = sg.build_wvdt(1, 'w', big)          # no points -> default
    _, payload = _split_wvdt(blob)
    assert len(payload) == BK4055B.ARB_DEFAULT_POINTS * 2 == 2048, len(payload)


def test_points_override_and_cap():
    sg = FakeSG()
    _, blob = sg.build_wvdt(1, 'w', [0.0, 1.0], points=999999)
    _, payload = _split_wvdt(blob)
    assert len(payload) == BK4055B.ARB_MAX_POINTS * 2  # capped


def test_name_sanitised_and_truncated():
    sg = FakeSG()
    clean, blob = sg.build_wvdt(1, 'my wave/#!longlonglongname', [0.0, 1.0],
                                points=2)
    assert clean == 'my_wave___longlo'[:16] or len(clean) == 16
    assert all(c.isalnum() or c == '_' for c in clean)


def test_empty_and_bad_name_raise():
    sg = FakeSG()
    for bad in ([], None):
        try:
            sg.build_wvdt(1, 'w', bad, points=4)
            assert False, "empty samples should raise"
        except (ValueError, TypeError):
            pass
    try:
        sg.build_wvdt(1, '', [0.0, 1.0], points=2)
        assert False, "empty name should raise"
    except ValueError:
        pass


def test_upload_arb_sends_one_raw_blob():
    sg = FakeSG()
    name = sg.upload_arb(1, 'wave1', [0.0, 1.0, 0.0, -1.0], points=4,
                         amp_vpp=2.0)
    assert name == 'wave1'
    assert len(sg.sent_raw) == 1, "upload should be a single raw write"
    assert sg.sent_raw[0].startswith(b'C1:WVDT WVNM,wave1')


def test_set_sample_rate_truearb():
    sg = FakeSG()
    sg.set_sample_rate(1, mode='TARB', value=1_024_000)
    assert sg.sent == ['C1:SRATE MODE,TARB,VALUE,1024000'], sg.sent
    sg.sent.clear()
    sg.set_sample_rate(2, mode='DDS')
    assert sg.sent == ['C2:SRATE MODE,DDS'], sg.sent
    sg.sent.clear()
    sg.set_sample_rate(1, value=500000.0)
    assert sg.sent == ['C1:SRATE VALUE,500000'], sg.sent


def test_set_sample_rate_needs_arg():
    sg = FakeSG()
    try:
        sg.set_sample_rate(1)
        assert False, "no args should raise"
    except ValueError:
        pass


def test_get_sample_rate_dict():
    sg = FakeSG()
    sg.ask = lambda cmd: 'C1:SRATE MODE,TARB,VALUE,1000000Sa/s,INTER,LINE'
    d = sg.get_sample_rate_dict(1)
    assert d['mode'] == 'TARB', d
    assert abs(d['value'] - 1_000_000) < 1e-6, d


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed")


if __name__ == '__main__':
    _run()
