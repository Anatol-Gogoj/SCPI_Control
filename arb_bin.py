#!/usr/bin/env python3
"""Direct .bin flash-drive export for the BK 4055B (bypasses EasyWaveX).

The 4055B recalls arb waveforms straight from a FAT flash drive in the front
USB port (Store/Recall menu). The file format was reverse-engineered from a
known-good lab file (committed as arb_bin_reference_9step.bin, a 9-step
staircase EasyWaveX generated):

    exactly 16384 samples, little-endian int16, full scale +/-32767
    NO header, NO footer, NO metadata -- 32768 bytes total

The file carries the SHAPE ONLY. Frequency, amplitude and offset are set on
the front panel after recalling (output = OFST + AMP/2 * sample/32767).

This replaces the whole editor -> CSV -> EasyWaveX -> .bin -> flash-drive
chain with editor -> .bin -> flash drive. Same scaling as the (LAN-only)
WVDT upload path, so a wave plays identically whichever route it takes.

Headless self-test: .venv/bin/python test_arb_bin.py
"""
import getpass
import os
import struct

from easywave_export import resample_linear
from instruments import BK4055B

BIN_POINTS = 16384
FULL_SCALE = 32767


def build_arb_bin(samples, points=BIN_POINTS):
    """Normalized samples -> headerless int16 LE blob (resampled to `points`).

    Uses the same +/-32767 scaling as BK4055B.samples_to_int16 (values are
    normalized to full scale if any |sample| > 1), so the flash-drive file
    and the WVDT upload payload are bit-compatible.
    """
    vals = resample_linear(samples, points)
    return BK4055B.samples_to_int16(vals)


def parse_arb_bin(blob):
    """Headerless int16 LE blob -> list of normalized floats in [-1, 1]."""
    if not blob:
        raise ValueError("empty .bin")
    if len(blob) % 2:
        raise ValueError(f".bin length {len(blob)} is odd -- not int16 data")
    n = len(blob) // 2
    return [v / FULL_SCALE for v in struct.unpack(f'<{n}h', blob)]


def write_arb_bin(path, samples, points=BIN_POINTS):
    """Build and write the .bin; returns the byte count (2 * points).

    fsync's the file so it survives the stick being yanked right after.
    """
    blob = build_arb_bin(samples, points)
    with open(path, 'wb') as f:
        f.write(blob)
        f.flush()
        os.fsync(f.fileno())
    return len(blob)


def find_flash_drives(roots=None, require_mount=True):
    """Return mounted, writable removable-media directories.

    GNOME automounts sticks under /run/media/<user>/<label> (RHEL 9); /media
    variants are checked for good measure. `roots`/`require_mount` exist for
    the headless test, which cannot create real mount points.
    """
    if roots is None:
        user = getpass.getuser()
        roots = [f'/run/media/{user}', f'/media/{user}', '/media']
    drives = []
    for root in roots:
        try:
            names = sorted(os.listdir(root))
        except OSError:
            continue
        for name in names:
            path = os.path.join(root, name)
            if not os.path.isdir(path) or not os.access(path, os.W_OK):
                continue
            if require_mount and not os.path.ismount(path):
                continue
            drives.append(path)
    return drives
