#!/usr/bin/env python3
"""
Standalone, hardware-free demo of the arbitrary-waveform editor.

Lets you try the editor's UI/UX on any machine with Python 3.8+ and Tkinter --
no instruments, no pyvisa, no other packages required. It launches the REAL
ArbWaveformEditor against a mock signal generator, so compose/draw/zoom/save all
work; "Upload & Select" just succeeds against the mock (nothing is sent).

Run:  python3 arb_demo.py
"""
# Runnable from anywhere: put the repo root (one level up) on sys.path
# so the app modules import when this file is executed directly.
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))
import os
import re
import tempfile
import tkinter as tk
from tkinter import ttk

from version import version_string
from siggen_presets import SignalGenPresetStore
from arb_editor import ArbWaveformEditor


class _MockSG:
    """Stand-in for BK4055B: accepts uploads, sends nothing."""
    idn = "DEMO,4055B,MOCK,0"

    def upload_arb(self, channel, name, samples, freq_hz, amp_vpp=1.0,
                   offset_v=0.0, phase_deg=0.0):
        return re.sub(r'[^A-Za-z0-9_]', '_', str(name))[:16] or 'wave'

    def select_arb(self, channel, name):
        pass


class _DemoApp:
    """Minimal stand-in for InstrumentControlGUI providing just what the editor
    touches: a channel's arb widgets, freq/amp/offset, an arb library in a temp
    dir, a status bar, and the _sg_* hooks (no-ops here)."""

    def __init__(self, root, status_var):
        self.root = root
        self.sg = _MockSG()
        self._status_var = status_var

        tmp = tempfile.mkdtemp(prefix='arbdemo_')
        self.sg_presets = SignalGenPresetStore(path=os.path.join(tmp, 'presets.json'))
        self.sg_presets.arb_dir = os.path.join(tmp, 'arb')

        self.sg_channel_widgets = {
            ch: {
                'arb_name_var': tk.StringVar(value=''),
                'arb_samples': None,
                'waveform': tk.StringVar(value='ARB'),
                'freq': tk.StringVar(value='1000'),
                'amp': tk.StringVar(value='1.0'),
                'offset': tk.StringVar(value='0.0'),
            }
            for ch in (1, 2)
        }
        # status_bar just needs .config(text=...)
        self.status_bar = _StatusProxy(status_var)

    def _sg_get_float(self, ch, key, default):
        try:
            return float(self.sg_channel_widgets[ch][key].get())
        except (KeyError, ValueError, AttributeError):
            return default

    def _sg_update_visibility(self, ch):
        pass

    def _sg_redraw_preview(self, ch):
        pass

    def _sg_refresh_applied(self, ch):
        pass


class _StatusProxy:
    def __init__(self, var):
        self._var = var

    def config(self, text=None, **_kw):
        if text is not None:
            self._var.set(text)


def main():
    root = tk.Tk()
    root.title("Waveform Tool - DEMO (no hardware)")
    root.geometry("460x230")

    status_var = tk.StringVar(value="Demo ready - mock signal generator")
    app = _DemoApp(root, status_var)

    body = ttk.Frame(root, padding=20)
    body.pack(fill='both', expand=True)
    ttk.Label(body, text="Arbitrary Waveform Editor - Demo",
              font=("Arial", 14)).pack(anchor='w')
    ttk.Label(body, justify=tk.LEFT, foreground='#555', wraplength=410,
              text=("Standalone UI/UX preview - no instruments connected.\n"
                    "Open the editor to compose points/segments, draw, zoom, "
                    "save to a temp library, and try Upload (mock).")).pack(
                        anchor='w', pady=10)
    ttk.Button(body, text="Open Waveform Editor",
               command=lambda: ArbWaveformEditor(app, 1)).pack(anchor='w', pady=6)

    # Footer: status (left) + version (right) -- same as the real app
    footer = tk.Frame(root)
    footer.pack(side=tk.BOTTOM, fill=tk.X)
    tk.Label(footer, textvariable=status_var, bd=1, relief=tk.SUNKEN,
             anchor=tk.W).pack(side=tk.LEFT, fill=tk.X, expand=True)
    tk.Label(footer, text=version_string(), bd=1, relief=tk.SUNKEN,
             anchor=tk.E, padx=8).pack(side=tk.RIGHT)

    root.mainloop()


if __name__ == '__main__':
    main()
