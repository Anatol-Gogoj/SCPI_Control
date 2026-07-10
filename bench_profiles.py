#!/usr/bin/env python3
"""Named whole-bench profiles: LCR + scope + both sig-gen channels.

Presets used to exist for the signal generator only (siggen_presets.py);
everything else was retyped each session. A bench profile snapshots the
entire bench so either user can recall a colleague's full experiment in
one click (issue #47).

Profiles live in presets/bench_profiles.json -- the same relative
presets/ dir as the sig-gen presets, so on the shared-drive deployment
they are shared between users. Writes are atomic (tmp + os.replace) so
two users can't tear the file.

Headless self-test: .venv/bin/python test_bench_profiles.py
"""
import json
import os


class BenchProfileStore:
    def __init__(self, path=None):
        self.path = path or os.path.join('presets', 'bench_profiles.json')

    def _load_all(self):
        """The whole file as a dict; missing/corrupt files read as empty
        (a broken JSON must never brick every profile operation)."""
        try:
            with open(self.path) as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_all(self, data):
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = self.path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    def names(self):
        return sorted(self._load_all())

    def save(self, name, profile):
        """Store `profile` (a plain dict) under `name`; returns the
        stripped name. Overwrites silently -- the GUI confirms first."""
        name = (name or '').strip()
        if not name:
            raise ValueError("Profile name must not be empty")
        if not isinstance(profile, dict):
            raise ValueError("Profile must be a dict")
        data = self._load_all()
        data[name] = profile
        self._save_all(data)
        return name

    def load(self, name):
        data = self._load_all()
        if name not in data:
            raise KeyError(f"No bench profile named {name!r}")
        return data[name]

    def delete(self, name):
        data = self._load_all()
        if name in data:
            del data[name]
            self._save_all(data)
