#!/usr/bin/env python3
"""Headless tests for the LCR readout formatter (issue #38).

Run: .venv/bin/python test_lcr_format.py
"""
from lcr_format import MODE_UNITS, format_measurement, format_si


def test_si_scaling_capacitance():
    assert format_si(3.3e-9, 'F') == '3.3 nF'
    assert format_si(4.7e-4, 'F') == '470 µF'
    assert format_si(1.234e-12, 'F') == '1.234 pF'
    # below the smallest prefix, keep it rather than inventing femto
    assert format_si(5e-14, 'F') == '0.05 pF'


def test_si_scaling_inductance_and_ohms():
    assert format_si(2.2e-6, 'H') == '2.2 µH'
    assert format_si(0.15, 'H') == '150 mH'
    assert format_si(4700.0, 'Ω') == '4.7 kΩ'
    assert format_si(2.2e6, 'Ω') == '2.2 MΩ'
    assert format_si(0.05, 'Ω') == '50 mΩ'


def test_si_edge_cases():
    assert format_si(None, 'F') == '--', "None must never crash the readout"
    assert format_si(0, 'F') == '0 F'
    assert format_si(-3.3e-9, 'F') == '-3.3 nF'
    # dimensionless / angles: plain number, no prefix hunting
    assert format_si(0.00123, '') == '0.00123'
    assert format_si(57.3, '°') == '57.3 °'
    assert format_si(1.0, 'rad') == '1 rad'


def test_measurement_pairs():
    p, s = format_measurement('CPD', 3.3e-9, 0.0012)
    assert p == '3.3 nF' and s == 'D: 0.0012', (p, s)
    p, s = format_measurement('LSRS', 2.2e-6, 0.5)
    assert p == '2.2 µH' and s == 'Rs: 500 mΩ', (p, s)
    p, s = format_measurement('ZTD', 1.5e3, -89.9)
    assert p == '1.5 kΩ' and s == 'θ: -89.9 °', (p, s)
    p, s = format_measurement('RX', 50.0, -12.5)
    assert p == '50 Ω' and s == 'X: -12.5 Ω', (p, s)


def test_measurement_robustness():
    # None secondary was the crash in issue #38
    p, s = format_measurement('CPD', 3.3e-9, None)
    assert p == '3.3 nF' and s == 'D: --', (p, s)
    # unknown / empty mode falls back to bare numbers, no exception
    p, s = format_measurement('??', 1.0, 2.0)
    assert p == '1' and s == '2', (p, s)
    p, s = format_measurement(None, None, None)
    assert p == '--' and s == '--', (p, s)
    # lowercase mode accepted
    p, s = format_measurement('cpd', 1e-9, 0.1)
    assert p == '1 nF' and s.startswith('D:'), (p, s)


def test_mode_table_matches_driver():
    # every driver mode must have an entry so the GUI never falls back
    from instruments import BK894
    missing = set(BK894.MODES) - set(MODE_UNITS)
    assert not missing, f"modes missing units: {missing}"


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed")


if __name__ == '__main__':
    _run()
