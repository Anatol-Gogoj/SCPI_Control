#!/usr/bin/env python3
"""
Signal-generator preset persistence (pure logic, no Tk, no instrument I/O).

A *preset* is a named snapshot of both channels' state. The reusable unit is a
ChannelState dict:

    {"waveform": "SINE", "freq_hz": 1000.0, "amp_vpp": 1.0, "offset_v": 0.0,
     "output": False, "load": "HZ", "polarity": "NOR"}

Presets are stored in a single JSON file (default presets/siggen_presets.json):

    {"version": 1,
     "presets": {
        "<name>": {"name": "<name>", "saved_utc": "...",
                   "channels": {"1": <ChannelState>, "2": <ChannelState>}}}}

The single-file layout keeps enumeration trivial (one read populates a dropdown)
and avoids preset-name -> filename sanitisation. The top-level ``version`` field
leaves room for the sequence/arb formats that later PRs add to this module.
"""
import os
import json
from datetime import datetime, timezone

from instruments import BK4055B

SCHEMA_VERSION = 1
DEFAULT_PATH = 'presets/siggen_presets.json'

# Canonical waveform list lives on the driver; mirror it here so validation has
# a single source of truth.
VALID_WAVEFORMS = BK4055B.WAVEFORMS

_CHANNEL_DEFAULTS = {
    'waveform': 'SINE',
    'freq_hz': 1000.0,
    'amp_vpp': 1.0,
    'offset_v': 0.0,
    'output': False,
    'load': 'HZ',
    'polarity': 'NOR',
}


def validate_channel_state(state):
    """Return a normalised copy of a ChannelState, or raise ValueError.

    Missing keys fall back to defaults; numerics are coerced to float; the
    waveform must be one of VALID_WAVEFORMS and polarity one of NOR/INVT.
    """
    if not isinstance(state, dict):
        raise ValueError(f"channel state must be a dict, got {type(state).__name__}")
    out = dict(_CHANNEL_DEFAULTS)
    out.update({k: state[k] for k in _CHANNEL_DEFAULTS if k in state})

    out['waveform'] = str(out['waveform']).upper()
    if out['waveform'] not in VALID_WAVEFORMS:
        raise ValueError(
            f"waveform {out['waveform']!r} must be one of {VALID_WAVEFORMS}")

    for key in ('freq_hz', 'amp_vpp', 'offset_v'):
        try:
            out[key] = float(out[key])
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a number, got {out[key]!r}")

    out['output'] = bool(out['output'])
    out['load'] = str(out['load'])
    out['polarity'] = str(out['polarity']).upper()
    if out['polarity'] not in ('NOR', 'INVT'):
        raise ValueError(f"polarity {out['polarity']!r} must be NOR or INVT")
    return out


class SignalGenPresetStore:
    """JSON-backed store of named signal-generator presets."""

    def __init__(self, path=DEFAULT_PATH):
        self.path = path
        self._data = {'version': SCHEMA_VERSION, 'presets': {}}
        self.load()

    # -- persistence -------------------------------------------------------
    def load(self):
        """(Re)load from disk. Missing file -> empty store; malformed file is
        moved aside to ``<path>.corrupt`` and an empty store is started."""
        if not os.path.exists(self.path):
            self._data = {'version': SCHEMA_VERSION, 'presets': {}}
            return self._data
        try:
            with open(self.path, 'r') as f:
                data = json.load(f)
            if not isinstance(data, dict) or not isinstance(data.get('presets'), dict):
                raise ValueError("missing 'presets' object")
            self._data = {
                'version': data.get('version', SCHEMA_VERSION),
                'presets': data['presets'],
            }
        except (json.JSONDecodeError, ValueError, OSError):
            try:
                os.replace(self.path, self.path + '.corrupt')
            except OSError:
                pass
            self._data = {'version': SCHEMA_VERSION, 'presets': {}}
        return self._data

    def _save(self):
        """Atomically persist: write a temp file then os.replace onto path."""
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = self.path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(self._data, f, indent=2)
        os.replace(tmp, self.path)

    # -- queries -----------------------------------------------------------
    def names(self):
        """Sorted list of preset names (for a combobox)."""
        return sorted(self._data['presets'])

    def get(self, name):
        """Return the full preset record. Raises KeyError if absent."""
        return self._data['presets'][name]

    # -- mutations ---------------------------------------------------------
    def save(self, name, channels):
        """Create or overwrite a preset from a {1|2 -> ChannelState} mapping.

        Channel keys may be ints or strings; they are normalised to '1'/'2'.
        Each channel state is validated. Returns the stored record.
        """
        name = str(name).strip()
        if not name:
            raise ValueError("preset name must not be empty")
        normalised = {}
        for ch, state in channels.items():
            normalised[str(ch)] = validate_channel_state(state)
        record = {
            'name': name,
            'saved_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'channels': normalised,
        }
        self._data['presets'][name] = record
        self._save()
        return record

    def delete(self, name):
        """Remove a preset. Returns True if it existed, False otherwise."""
        if name in self._data['presets']:
            del self._data['presets'][name]
            self._save()
            return True
        return False
