#!/usr/bin/env python3
"""EasyWaveX CSV export (flash-drive workflow) for the BK 4055B.

The 4055B firmware hard-caps USB commands at 52 bytes, so arb waveforms can
NOT be uploaded from this app over USB (see the README quirks;
BK4055B.upload_arb refuses and explains). The lab workaround is EasyWaveX's
CSV import: build the waveform in the arb editor, export it in the EXACT
template format EasyWaveX expects, copy the file to a flash drive, and load
it into EasyWaveX on the Windows PC manually.

Template format (byte-verified against the lab's Wavetemplate.csv, kept in
this repo as easywavex_template.csv):

    data length,16384\r\n
    frequency,<%f>\r\n
    amp,<%f>\r\n
    offset,<%f>\r\n
    phase,<%f>\r\n
    <exactly 7 blank lines>\r\n
    xpos,value\r\n
    1,<%.6g>\r\n
    ...
    16384,<%.6g>\r\n

CRLF line endings throughout (EasyWaveX is a Windows app), xpos 1-based,
exactly 16384 data rows, header floats with 6 decimal places, data values in
6-significant-digit shortest form. EasyWaveX rejects any deviation from this
layout ("It has to be in the exact waveform template").

Lab conventions (Trek HV amplifier downstream -- 1 V in the file = 1 kV at
the Trek output):
    frequency = 1 / T_total   where T_total = timestep * 16384
    amp       = highest voltage value in the waveform
    offset    = amp / 2
    phase     = 0
`suggest_header` computes these defaults from the samples + duration.

Headless self-test: .venv/bin/python tests/test_easywave_export.py
"""

EASYWAVE_POINTS = 16384
_BLANK_LINES = 7


def resample_linear(values, points=EASYWAVE_POINTS):
    """Linearly resample a sequence to exactly `points` samples.

    Endpoints are preserved; intermediate samples are interpolated. A single
    input value is repeated. Raises ValueError on an empty input.
    """
    vals = [float(v) for v in values]
    if not vals:
        raise ValueError("no samples to resample")
    n = len(vals)
    if points < 1:
        raise ValueError(f"points must be >= 1, got {points}")
    if n == points:
        return vals
    if n == 1:
        return vals * points
    step = (n - 1) / (points - 1)
    out = []
    for i in range(points):
        pos = i * step
        lo = int(pos)
        if lo >= n - 1:
            out.append(vals[-1])
            continue
        frac = pos - lo
        out.append(vals[lo] * (1.0 - frac) + vals[lo + 1] * frac)
    return out


def suggest_header(values_v, duration_s):
    """Header defaults per the lab instructions (see module docstring)."""
    vals = [float(v) for v in values_v]
    if not vals:
        raise ValueError("no samples")
    amp = max(vals)
    return {
        'freq_hz': (1.0 / duration_s) if duration_s > 0 else 0.0,
        'amp_v': amp,
        'offset_v': amp / 2.0,
        'phase_deg': 0.0,
    }


def build_easywave_csv(values_v, freq_hz, amp_v, offset_v, phase_deg=0.0):
    """Return the EasyWaveX template CSV as bytes (CRLF line endings).

    `values_v` are real voltage values (lab convention: 1 V here = 1 kV at
    the Trek output); they are resampled to exactly 16384 points.
    """
    vals = resample_linear(values_v, EASYWAVE_POINTS)
    lines = [
        f'data length,{EASYWAVE_POINTS}',
        f'frequency,{float(freq_hz):f}',
        f'amp,{float(amp_v):f}',
        f'offset,{float(offset_v):f}',
        f'phase,{float(phase_deg):f}',
    ]
    lines += [''] * _BLANK_LINES
    lines.append('xpos,value')
    lines += [f'{i},{v:.6g}' for i, v in enumerate(vals, start=1)]
    return ('\r\n'.join(lines) + '\r\n').encode('ascii')


def write_easywave_csv(path, values_v, freq_hz, amp_v, offset_v,
                       phase_deg=0.0):
    """Build and write the CSV (binary mode -- CRLF must survive verbatim)."""
    blob = build_easywave_csv(values_v, freq_hz, amp_v, offset_v, phase_deg)
    with open(path, 'wb') as f:
        f.write(blob)
    return len(blob)


def parse_easywave_csv(blob):
    """Parse a template CSV (bytes) -> (header_dict, values list).

    Inverse of build_easywave_csv; used by the round-trip self-test and by
    CSV import of files that came back from EasyWaveX.
    """
    text = blob.decode('ascii')
    lines = text.split('\r\n')
    header = {}
    for ln in lines[:5]:
        key, _, val = ln.partition(',')
        header[key] = val
    try:
        idx = lines.index('xpos,value')
    except ValueError:
        raise ValueError("not an EasyWaveX template: no 'xpos,value' line")
    values = []
    for ln in lines[idx + 1:]:
        if not ln:
            continue
        _, _, val = ln.partition(',')
        values.append(float(val))
    return header, values
