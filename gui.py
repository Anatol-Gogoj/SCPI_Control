#!/usr/bin/env python3
"""
Lab Instrument Control GUI.

Tabbed Tk interface for the bench instruments (auto-connects on launch; each
tab has a Reconnect button):
  - LCR Meter (BK 894)
  - Oscilloscope (Tektronix MSO24)
  - Signal Generator (BK 4055B): per-channel waveform / frequency / amplitude /
    offset with an Apply-then-Output workflow, live preview, and presets.
    Arbitrary waveforms are designed in the Waveform Editor and delivered
    to the box as a flash-drive .bin (front USB port); direct upload over
    the wire is LAN-only -- see issue #20.
  - Data Logging (CSV)

A version readout is shown in the footer. Instrument drivers live in
instruments.py; signal-gen presets in siggen_presets.py.
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext, simpledialog
import csv
import os
import queue
import subprocess
import sys
import time
from datetime import datetime
from bench_profiles import BenchProfileStore
import presets_path
from instruments import BK894, TekMSO24, BK4055B
import lcr_format
import scope_trace
from siggen_presets import SignalGenPresetStore
from ui_widgets import ScrollableTab, SplashScreen, add_tooltip
from arb_editor import ArbWaveformEditor
from waveform_render import unit_waveform, scale_waveform
from version import version_string
import webcam
import threading

# ---- Signal generator field rules -----------------------------------------
# Which fields exist, what they're called, which waveforms use them, and
# whether they're hidden behind the Advanced toggle.
SG_FIELD_ORDER = ('freq', 'amp', 'offset', 'stdev', 'mean', 'arb', 'duty',
                  'sym', 'phase', 'rise', 'fall', 'delay', 'load', 'polarity')

SG_FIELD_LABELS = {
    'freq': 'Frequency (Hz):', 'amp': 'Amplitude (Vpp):',
    'offset': 'DC Offset (V):', 'stdev': 'Noise Stdev (V):',
    'mean': 'Noise Mean (V):', 'arb': 'Arb Waveform:',
    'duty': 'Duty Cycle (%):',
    'sym': 'Symmetry (%):', 'phase': 'Phase (deg):',
    'rise': 'Rise Time (s):', 'fall': 'Fall Time (s):',
    'delay': 'Delay (s):', 'load': 'Load (Ω):', 'polarity': 'Polarity:',
}

SG_FIELD_DEFAULTS = {
    'freq': '1000', 'amp': '1.0', 'offset': '0.0', 'stdev': '0.2',
    'mean': '0.0', 'duty': '50',
    'sym': '50', 'phase': '0', 'rise': '1e-6', 'fall': '1e-6', 'delay': '0',
}

_PERIODIC = {'SINE', 'SQUARE', 'RAMP', 'PULSE', 'ARB'}
SG_FIELD_WAVEFORMS = {
    'freq': _PERIODIC,
    'amp': _PERIODIC,
    'offset': _PERIODIC | {'DC'},
    'stdev': {'NOISE'},     # issue #45: noise level was front-panel-only
    'mean': {'NOISE'},
    'arb': {'ARB'},
    'duty': {'SQUARE', 'PULSE'},
    'sym': {'RAMP'},
    'phase': {'SINE', 'SQUARE', 'RAMP', 'ARB'},
    'rise': {'PULSE'}, 'fall': {'PULSE'}, 'delay': {'PULSE'},
    'load': None,      # None = all waveforms
    'polarity': None,
}

SG_ADVANCED_FIELDS = {'phase', 'rise', 'fall', 'delay', 'load', 'polarity'}

# hover help per field (ui_widgets.add_tooltip); attached in the build loop
SG_FIELD_TOOLTIPS = {
    'freq': 'Output frequency (Hz). For ARB this is the repetition rate '
            'of the whole recalled waveform.',
    'amp': 'Peak-to-peak amplitude (V). Calibrated for the selected Load '
           '-- check Load before trusting the number.',
    'offset': 'DC level added to the waveform (V).',
    'stdev': 'Noise level: standard deviation of the gaussian noise (V).',
    'mean': 'Noise DC mean / center level (V).',
    'duty': 'High-time as a percentage of the period (0-100).',
    'sym': 'Ramp symmetry: 50% = triangle, 100% = rising sawtooth, '
           '0% = falling sawtooth.',
    'phase': 'Phase offset in degrees.',
    'rise': 'Pulse 10-90% rise time (s).',
    'fall': 'Pulse 90-10% fall time (s).',
    'delay': 'Pulse delay from the period start (s).',
    'load': 'What the output feeds: High-Z (e.g. scope 1 MOhm input) or '
            'ohms for a matched load. Amplitude is calibrated for this '
            'setting -- into open circuit the real voltage is ~2x the '
            '50 Ohm value.',
    'polarity': 'NOR = normal output, INVT = inverted.',
}

# field key -> BSWV SCPI parameter
SG_BSWV_KEYS = {'freq': 'FRQ', 'amp': 'AMP', 'offset': 'OFST',
                'stdev': 'STDEV', 'mean': 'MEAN',
                'duty': 'DUTY', 'sym': 'SYM', 'phase': 'PHSE',
                'rise': 'RISE', 'fall': 'FALL', 'delay': 'DLY'}

# field key -> preset ChannelState key
SG_STATE_KEYS = {'freq': 'freq_hz', 'amp': 'amp_vpp', 'offset': 'offset_v',
                 'stdev': 'stdev_v', 'mean': 'mean_v',
                 'duty': 'duty_pct', 'sym': 'sym_pct', 'phase': 'phase_deg',
                 'rise': 'rise_s', 'fall': 'fall_s', 'delay': 'delay_s'}

# Arbitrary waveforms are back on: the editor exports a flash-drive .bin
# that the 4055B recalls from its FRONT USB port, so no waveform data has to
# cross the USB wire. The driver still refuses upload_arb over USB (the
# 52-byte firmware cap wedges the box -- issue #20); Upload & Select needs
# LAN. Flip to False to hide the ARB waveform + Waveform Editor again.
SG_ARB_ENABLED = True

PREVIEW_MAX_HEIGHT = 520   # webcam preview height budget (px)

SG_LOAD_HIGHZ = 'High-Z'   # UI label for the SCPI 'HZ' (high impedance) token

# Instrument I/O works only on the Linux bench box (pyvisa-py drives the
# USB-TMC boxes via libusb with a udev/blacklist setup that exists only
# there). On Windows/macOS the app still runs: Battery Data and Webcam
# are fully functional, and every instrument tab stays editable so
# presets/bench profiles can be prepared -- only connecting is locked out.
INSTRUMENTS_SUPPORTED = sys.platform.startswith('linux')
NOT_LINUX_SHORT = "Linux bench only -- view/edit (presets OK)"
NOT_LINUX_NOTE = ("Instrument control needs the Linux bench PC "
                  f"(this is {sys.platform}). Battery Data and Webcam are "
                  "fully functional here, and instrument settings can "
                  "still be edited and saved as presets/profiles.")

class InstrumentControlGUI:
    def __init__(self, root, progress=None):
        # `progress` is an optional callable(str) used by the splash screen
        # to report what is being built (tab construction takes ~2.6 s).
        self._progress = progress or (lambda _text: None)
        self.root = root
        self.root.title(f"Lab Instrument Control  —  {version_string()}")
        # Wide enough for the LCR tab's right-hand column (bias/speed/
        # correction) and the footer version readout (issues #26/#27).
        self.root.geometry("1320x800")

        # Menu bar: Tools -> bench profiles + Update Software
        menubar = tk.Menu(self.root)
        tools_menu = tk.Menu(menubar, tearoff=0)
        tools_menu.add_command(label="Save Bench Profile…",
                               command=self.save_bench_profile)
        tools_menu.add_command(label="Load Bench Profile…",
                               command=self.load_bench_profile)
        tools_menu.add_command(label="Delete Bench Profile…",
                               command=self.delete_bench_profile)
        tools_menu.add_separator()
        tools_menu.add_command(
            label="Update Software…",
            command=self.open_update_software,
            state=('normal' if INSTRUMENTS_SUPPORTED else 'disabled'))
        menubar.add_cascade(label="Tools", menu=tools_menu)
        self.root.config(menu=menubar)
        self._updating = False

        # Instrument connections
        self.lcr = None
        self.scope = None
        self.sg = None
        self.recording = False
        self.record_thread = None
        # Keys of background instrument operations in flight (issue #40) --
        # guards against double-starting a connect/capture from the UI.
        self._bg_busy = set()

        # LCR sweep state (worker thread + thread-safe UI queue)
        self.sweeping = False
        self.sweep_thread = None
        self.sweep_queue = queue.Queue()

        # Signal-generator state
        self.sg_channel_widgets = {}
        self.sg_presets = SignalGenPresetStore()
        self.bench_profiles = BenchProfileStore()

        # Webcam state (worker thread + thread-safe UI queue, like the sweep)
        self.cam = None
        self.cam_previewing = False
        self.cam_preview_job = None
        self.cam_last_frame = None        # last RGB frame (numpy) for snapshots
        self.cam_photo = None             # keep a ref so Tk doesn't GC the image
        self.cam_interval_job = None
        self.cam_capture_index = 0
        self.cam_seq_running = False
        self.cam_seq_thread = None
        self.cam_seq_queue = queue.Queue()

        # Create notebook (tabbed interface)
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill='both', expand=True, padx=10, pady=10)
        
        # Create tabs (reporting each one; this is the slow part of start-up)
        for label, build in (("LCR meter", self.create_lcr_tab),
                             ("oscilloscope", self.create_scope_tab),
                             ("signal generator", self.create_sg_tab),
                             ("data logging", self.create_logging_tab),
                             ("battery data", self.create_battery_tab),
                             ("webcam", self.create_webcam_tab)):
            self._progress(f"Loading {label}...")
            build()
        
        # Footer: status bar (left, stretches) + version readout (right)
        footer = tk.Frame(root)
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_bar = tk.Label(footer, text="Ready", bd=1, relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.pack(side=tk.LEFT, fill=tk.X, expand=True)
        version_label = tk.Label(footer, text=version_string(), bd=1,
                                 relief=tk.SUNKEN, anchor=tk.E, padx=8)
        version_label.pack(side=tk.RIGHT)
        
        # Clean up the camera (and any capture loop) on window close.
        self.root.protocol('WM_DELETE_WINDOW', self._on_app_close)

        # Auto-connect on startup (in the background -- issue #40)
        self._progress("Looking for instruments...")
        self.root.after(100, self.auto_connect)

    def _on_app_close(self):
        """Safety shutdown on window close (user request 2026-07-20: the
        sig gen used to keep DRIVING after the app was closed). Best-effort
        per output -- a dead instrument must never block exit."""
        try:
            self.cam_seq_running = False
            self.cam_stop_preview()
            if self.cam is not None:
                self.cam.close()
        except Exception:
            pass
        if self.sg:
            for ch in (1, 2):
                try:
                    self.sg.set_output(ch, False)
                except Exception:
                    pass
        if self.lcr:
            try:
                self.lcr.set_bias_enabled(False)
            except Exception:
                pass
        self.root.destroy()

    # ---- Background instrument I/O (issue #40) ---------------------------
    # VISA opens and long transfers used to run on the Tk main thread, so a
    # powered-off or slow instrument froze the whole window. _run_bg moves
    # the blocking call to a daemon thread and marshals the completion back
    # onto the main thread; ALL Tk work stays in the `done` callback.

    def _run_bg(self, work, done, busy=None, quiet=False):
        """Run `work` (blocking, NO Tk calls) in a daemon thread, then call
        `done(result, error)` on the Tk main thread. `busy` is an optional
        exclusion key: while an operation with the same key is in flight,
        further requests are refused (with a status-bar note unless `quiet`
        -- periodic pollers pass quiet=True and just skip the tick).
        Returns True if the work was started."""
        if busy is not None:
            if busy in self._bg_busy:
                if not quiet:
                    self.status_bar.config(
                        text=f"Still working on the previous {busy} "
                             "operation...")
                return False
            self._bg_busy.add(busy)

        def runner():
            result, error = None, None
            try:
                result = work()
            except Exception as e:
                error = e

            def finish():
                if busy is not None:
                    self._bg_busy.discard(busy)
                done(result, error)
            self.root.after(0, finish)

        threading.Thread(target=runner, daemon=True).start()
        return True

    def _bg_simple(self, work, ok_text, busy, err_title="Error"):
        """_run_bg for fire-and-forget commands: status text on success,
        one error dialog on failure."""
        def done(_result, error):
            if error:
                messagebox.showerror(err_title, str(error))
            else:
                self.status_bar.config(text=ok_text)
        self._run_bg(work, done, busy=busy)

    @staticmethod
    def _preset_note(text):
        """Append 'the share was down, saved locally' to a status message
        when the last write fell back (presets_path)."""
        note = presets_path.fallback_note()
        if not note:
            return text
        presets_path.clear_note()
        return f"{text} -- {note}"

    # ---- Bench profiles (issue #47) --------------------------------------
    # A profile is a widget-level snapshot of the whole bench (LCR, scope,
    # both sig-gen channels). Entry values are stored as their raw strings
    # so Save never fails on a half-typed field; Load writes them back and
    # pushes the config to whichever instruments are connected (outputs
    # are never switched by a profile load).

    def _collect_bench_profile(self):
        scope_channels = {}
        for ch in range(1, 5):
            w = self.channel_widgets[ch]
            scope_channels[ch] = {
                'enable': w['enable'].get(),
                'vscale': w['vscale'].get(),
                'position': w['position'].get(),
                'coupling': w['coupling'].get(),
                'trigger': w['trigger'].get(),
            }
        return {
            'version': 1,
            'lcr': {
                'mode': self.lcr_mode.get(),
                'freq_hz': self.lcr_freq.get(),
                'volt_v': self.lcr_volt.get(),
                'bias_v': self.lcr_bias_volt.get(),
                'bias_on': self.lcr_bias_on.get(),
                'speed': self.lcr_speed.get(),
                'avg': self.lcr_avg.get(),
                'autorange': self.lcr_autorange.get(),
            },
            'scope': {
                'channels': scope_channels,
                'hscale': self.scope_hscale.get(),
                'trig_level': self.scope_trig_level.get(),
                'trig_slope': self.scope_trig_slope.get(),
            },
            'siggen': self._sg_collect_state(),
        }

    def _apply_bench_profile(self, p):
        lcr = p.get('lcr') or {}
        if lcr:
            if lcr.get('mode') in BK894.MODES:
                self.lcr_mode.set(lcr['mode'])
            for key, widget in (('freq_hz', self.lcr_freq),
                                ('volt_v', self.lcr_volt),
                                ('bias_v', self.lcr_bias_volt),
                                ('avg', self.lcr_avg)):
                if lcr.get(key) is not None:
                    self._set_entry(widget, lcr[key])
            if lcr.get('speed') in BK894.APERTURE_SPEEDS:
                self.lcr_speed.set(lcr['speed'])
            self.lcr_bias_on.set(bool(lcr.get('bias_on')))
            self.lcr_autorange.set(bool(lcr.get('autorange', True)))
        scope = p.get('scope') or {}
        chans = scope.get('channels') or {}
        for ch in range(1, 5):
            s = chans.get(str(ch)) or chans.get(ch)   # JSON stringifies keys
            if not s:
                continue
            w = self.channel_widgets[ch]
            w['enable'].set(bool(s.get('enable')))
            w['trigger'].set(bool(s.get('trigger')))
            for key in ('vscale', 'position'):
                if s.get(key) is not None:
                    self._set_entry(w[key], s[key])
            if s.get('coupling'):
                w['coupling'].set(s['coupling'])
        for key, widget in (('hscale', self.scope_hscale),
                            ('trig_level', self.scope_trig_level)):
            if scope.get(key) is not None:
                self._set_entry(widget, scope[key])
        if scope.get('trig_slope'):
            self.scope_trig_slope.set(scope['trig_slope'])
        # Push to whatever is connected; each apply guards itself and runs
        # in the background under its own busy key.
        if self.lcr and lcr:
            self.apply_lcr_config()
        if self.scope and scope:
            self.apply_all_scope_config()
        if p.get('siggen'):
            self._sg_apply_state(p['siggen'])

    def save_bench_profile(self):
        name = simpledialog.askstring("Save Bench Profile",
                                      "Profile name:", parent=self.root)
        if not name or not name.strip():
            return
        name = name.strip()
        if name in self.bench_profiles.names() and not messagebox.askyesno(
                "Save Bench Profile", f"Overwrite profile '{name}'?"):
            return
        try:
            self.bench_profiles.save(name, self._collect_bench_profile())
        except Exception as e:
            messagebox.showerror("Save Bench Profile", str(e))
            return
        self.status_bar.config(
            text=self._preset_note(f"Bench profile saved: {name}"))

    def load_bench_profile(self):
        def action(name):
            try:
                profile = self.bench_profiles.load(name)
            except Exception as e:
                messagebox.showerror("Load Bench Profile", str(e))
                return
            self._apply_bench_profile(profile)
            self.status_bar.config(
                text=f"Bench profile '{name}' loaded -- pushed to every "
                     "connected instrument (outputs untouched)")
        self._bench_profile_pick("Load Bench Profile", action)

    def delete_bench_profile(self):
        def action(name):
            if not messagebox.askyesno("Delete Bench Profile",
                                       f"Delete profile '{name}'?"):
                return
            self.bench_profiles.delete(name)
            self.status_bar.config(text=f"Bench profile deleted: {name}")
        self._bench_profile_pick("Delete Bench Profile", action)

    def _bench_profile_pick(self, title, action):
        names = self.bench_profiles.names()
        if not names:
            messagebox.showinfo(title, "No bench profiles saved yet -- use "
                                       "Tools > Save Bench Profile first.")
            return
        win = tk.Toplevel(self.root)
        win.title(title)
        win.transient(self.root)
        win.resizable(False, False)
        ttk.Label(win, text="Profile:").pack(padx=12, pady=(12, 4),
                                             anchor='w')
        var = tk.StringVar(value=names[0])
        ttk.Combobox(win, state='readonly', values=names, textvariable=var,
                     width=30).pack(padx=12, pady=4)
        btns = ttk.Frame(win)
        btns.pack(pady=10)

        def ok():
            win.destroy()
            action(var.get())
        ttk.Button(btns, text="OK", command=ok).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Cancel",
                   command=win.destroy).pack(side=tk.LEFT, padx=4)
        win.grab_set()

    def auto_connect(self):
        """Connect to all instruments in the background at startup."""
        if not INSTRUMENTS_SUPPORTED:
            for label in (self.lcr_status, self.scope_status,
                          self.sg_status):
                label.config(text=NOT_LINUX_SHORT, fg="#8a5a00")
            self.status_bar.config(
                text="Non-Linux platform: instrument tabs are view/edit "
                     "only; Battery Data and Webcam are fully available")
            return
        for label in (self.lcr_status, self.scope_status, self.sg_status):
            label.config(text="Connecting...", fg="#b36b00")

        def work():
            # One serial worker on purpose: the drivers share a process-wide
            # pyvisa-py ResourceManager, which is not re-entrant.
            results = {}
            for key, cls in (('lcr', BK894), ('scope', TekMSO24),
                             ('sg', BK4055B)):
                try:
                    results[key] = cls()
                except Exception as e:
                    results[key] = e
            return results

        self._run_bg(work, self._auto_connect_done, busy='connect')

    def _auto_connect_done(self, results, error):
        if error:   # work() catches per-instrument; this is belt-and-braces
            self.status_bar.config(text=f"Auto-connect failed: {error}")
            return
        for key, label, sync in (
                ('lcr', self.lcr_status, self.update_lcr_config),
                ('scope', self.scope_status, None),
                ('sg', self.sg_status, self.update_sg_config)):
            inst = results.get(key)
            if isinstance(inst, Exception) or inst is None:
                label.config(text=f"Not connected: {inst}", fg="red")
                continue
            setattr(self, key, inst)
            label.config(text=f"Connected: {inst.idn}", fg="green")
            if sync:
                sync()   # reads config; catches its own errors

    def _reconnect(self, key, cls, label, sync=None):
        """Close + reopen one instrument off the UI thread (issue #40)."""
        if not INSTRUMENTS_SUPPORTED:
            messagebox.showinfo("Linux only", NOT_LINUX_NOTE)
            return
        old = getattr(self, key)
        setattr(self, key, None)   # nothing may use the handle meanwhile
        label.config(text="Connecting...", fg="#b36b00")

        def work():
            if old:
                try:
                    old.close()
                except Exception:
                    pass
            return cls()

        def done(inst, error):
            if error:
                label.config(text=f"Error: {error}", fg="red")
                messagebox.showerror("Connection Error", str(error))
                return
            setattr(self, key, inst)
            label.config(text=f"Connected: {inst.idn}", fg="green")
            if sync:
                sync()

        self._run_bg(work, done, busy='connect')
    
    def show_lcr_tips(self):
        """Show LCR meter tips"""
        tips = """BK Precision 894 LCR Meter - Usage Tips

CONNECTION:
- Instrument must be powered on before connecting
- Auto-detected via PyVISA (USB VID 0x0471, PID 0x2827)

CONFIGURATION:
- Mode: which pair the meter reports (primary + secondary)
  - CPD / CPQ: capacitance + D (loss) or Q - the everyday capacitor
    modes; D = tan(delta), lower means a better dielectric
  - CPG / CPRP: capacitance + conductance / parallel resistance
  - CSD / CSQ / CSRS: SERIES-model capacitance - use for large C or
    low impedance (e.g. electrolytics at 100 Hz)
  - LSRS / LSRD / LPRS / LPRP: inductance (series/parallel model)
    + winding resistance
  - RX: resistance + reactance (general impedance parts)
  - ZTD / ZTR: impedance magnitude + phase (degrees / radians)
  - Rule of thumb: high impedance (>10 kΩ) -> parallel model
    (CP*/LP*); low impedance (<1 kΩ) -> series model (CS*/LS*)
- Frequency: Test frequency (100 Hz to 500 kHz)
  - Use 1 kHz for general capacitor testing
  - Use 100 Hz for electrolytics
  - Use 10 kHz+ for high-frequency components
- Voltage: AC test signal amplitude (0.01 to 2.0 V)
  - 1.0V is standard for most measurements

DC BIAS, SPEED & RANGE:
- DC Bias applies a steady voltage across the DUT during the AC test
  (C-vs-bias derating of class-II ceramics, etc.): set the volts, tick
  Bias ON, Apply. Untick + Apply to switch it off.
- Speed = the meter's APERTURE (integration time per reading):
  SLOW is most accurate with the quietest D readings, FAST updates
  quickest, MED is the everyday default
- Avg = how many raw readings the METER averages into each reported
  result (1-256); SLOW + Avg 8-16 gives the cleanest dissipation
  numbers on small capacitors
- Auto range OFF holds the current range: no mid-sweep range-hunting
  glitches on a fixed DUT (re-enable for unknown parts)

FIXTURE CORRECTION:
- Open Correction (fixture empty) / Short Correction (terminals
  shorted) sweep all test frequencies and de-embed the fixture from
  every later reading
- Redo the corrections whenever the fixture or leads change
- The sweep takes tens of seconds; the meter is busy while it runs

MEASUREMENTS:
- Single Measurement: Take one reading
- Start Continuous: Update display every 200ms
- Stop: Halt continuous measurements
- Status = 0 means good measurement
- Very small values (~fF) indicate open circuit

BEST PRACTICES:
- Keep test leads short to minimize stray capacitance
- For low-impedance measurements, use 4-wire Kelvin clips
- Allow readings to settle (2-3 seconds) after changing frequency
- Zero/open/short compensation improves accuracy (see manual)"""
        
        messagebox.showinfo("LCR Meter Tips", tips)
    
    def show_scope_tips(self):
        """Show oscilloscope tips"""
        tips = """Tektronix MSO24 Oscilloscope - Usage Tips

CONNECTION:
- Scope must be powered on before connecting
- Auto-detected via PyVISA (USB VID 0x0699, PID 0x0105)

CHANNEL CONFIGURATION:
- Enable/Disable: Turn channels on/off for viewing
- Vertical (V/div): Voltage scale per division
  - Adjust so signal fills ~60-80% of screen height
- Coupling: 
  - DC: Show full signal including DC offset
  - AC: Block DC component, show AC only
  - GND: Ground input to check baseline
- Horizontal (s/div): Time scale per division
  - Adjust to show 2-3 cycles for periodic signals

TRIGGER:
- Source: Which channel to trigger from
- Level: Voltage threshold for triggering
- Slope: RISE (rising edge) or FALL (falling edge)
- Set trigger level to middle of signal for stable display

ACQUISITION MODES:
- Run: Continuous acquisition and display
- Single: Capture one triggered event then stop
- Stop: Freeze current display

AUTOMATED MEASUREMENTS:
- Get Measurements: Query all standard measurements
- Frequency/Period: Only valid for periodic signals
- Invalid measurements show as "No signal"
- RMS is true RMS, not peak/√2

WAVEFORM CAPTURE:
- Transfers the acquired sample record (not a screenshot) and shows it
  in a plot window: min/max envelope, auto-scaled axes, pk-pk summary
- Save CSV from the plot window for offline analysis
- Narrow glitches survive the plot's decimation (min/max per column)

TIPS:
- Use AutoSet for quick setup on unknown signals
- Stop acquisition before changing major settings
- Ground unused channels to reduce noise"""
        
        messagebox.showinfo("Oscilloscope Tips", tips)

    def show_sg_tips(self):
        """Show signal generator tips"""
        tips = """B&K Precision 4055B Signal Generator - Usage Tips

CONNECTION:
- Generator must be powered on before connecting
- Auto-detected via PyVISA (USB VID 0xf4ec, PID 0xee38)
- Two independent output channels (CH1, CH2)

WAVEFORMS (fields adapt to the selected type):
- SINE: clean tones, frequency response, audio
- SQUARE: clocks, digital/logic stimulus; set Duty Cycle (%)
- RAMP: sawtooth/triangle, sweeps; set Symmetry (%)
  (50% = triangle, 100% = rising sawtooth, 0% = falling)
- PULSE: timing tests; Duty plus Rise/Fall/Delay (Advanced mode)
- NOISE: broadband stimulus; set Noise Stdev (level) and Mean (bias)
- DC: fixed level only (set with DC Offset)
- ARB: arbitrary waveform - design it in the Waveform Editor, export a
  .bin to a flash drive, recall it on the 4055B (see ARBITRARY WAVEFORMS)

BASIC vs ADVANCED:
- Advanced mode reveals Phase, pulse edge timing (Rise/Fall/Delay),
  Load and Polarity
- Apply always pushes the full configuration for the selected
  waveform, whether or not Advanced mode is showing the fields

APPLY vs OUTPUT:
- "Apply CH<n> Settings" pushes the configuration ONLY
- The Output button turns the physical output on/off (green = ON)
- Workflow: configure -> Apply -> check preview/applied -> Output ON

APPLIED READOUTS & PREVIEW:
- Gray values beside each field show what the instrument actually
  accepted (refreshed on connect and after Apply) - if they differ
  from your input, the box clamped or coerced your value
- The preview renders ~3 periods of the configured waveform; it is
  a drawing of your inputs, not measured data

FREQUENCY RANGE:
- Depends on waveform and model; check the front panel for the unit's
  rated maxima (sine reaches the highest; square/pulse/ramp are lower)
- Enter frequency in Hz (e.g. 1000 for 1 kHz, 1e6 for 1 MHz)

AMPLITUDE, OFFSET & LOAD:
- Amplitude is peak-to-peak (Vpp); Offset is the DC level (V)
- LOAD (Advanced) matters: amplitude is calibrated for the selected load
  - High-Z: what you see on a scope's 1 MOhm input
  - 50 (or any ohms): into a matched load; open-circuit V is then ~2x
  - Set LOAD to match how the output is actually terminated

PRESETS:
- Save Preset: store both channels' current settings under a name
- Load Preset: restore settings and push the CONFIGURATION to the
  instrument (output on/off state is NOT changed by a preset load)
- Delete Preset: remove a saved preset
- Presets are stored in presets/siggen_presets.json

ARBITRARY WAVEFORMS:
- Select ARB and click "Waveform Editor..." to design a custom shape
- USB cannot carry the waveform data (52-byte firmware cap), so use
  "Export .bin for 4055B flash drive...": save the .bin to a flash
  drive, plug it into the 4055B's FRONT USB port, and recall it via
  Store/Recall on the front panel
- The export pre-fills the channel (ARB + frequency/amplitude/offset);
  after recalling, click Apply to push those settings over USB (they
  are short commands and safe) - or dial them in on the front panel
- "Upload && Select" (direct upload) works over LAN only (issue #20)

BURST & SYNC:
- Burst emits exactly N cycles per trigger, then the output idles -
  bounded energy per shot, the safest way to drive the HV amplifier
- Trigger MAN: click Fire (or use the front panel); INT: auto-repeat
  every Interval seconds; EXT: edge on the rear Aux In
- Workflow: configure the wave, tick Burst, Apply, Output ON, Fire
- Sync out puts a trigger edge on the rear Sync BNC every waveform
  period - feed it to the scope's Aux In for rock-solid triggering
  on slow arbs

BEST PRACTICES:
- Confirm the load setting before trusting the amplitude reading
- Configure and Apply with output OFF, then switch Output ON
- Use DC waveform + offset for a steady bias voltage"""

        messagebox.showinfo("Signal Generator Tips", tips)

    def show_logging_tips(self):
        """Show data logging tips"""
        tips = """Data Logging - Usage Tips

CONFIGURATION:
- Log Directory: Where CSV files will be saved
  - Creates directory automatically if it doesn't exist
  - Default is ./logs in current working directory
- Sample Interval: Time between measurements (seconds)
  - 1.0s is good for slow processes
  - 0.1s for faster dynamics
  - Consider instrument settling time
- Log Instruments: Select which instruments to record

FILE FORMAT:
- Separate CSV file for each instrument
- Filename includes timestamp: lcr_20260206_143052.csv
- LCR columns: Timestamp, Mode, Frequency, Primary, Secondary, Status
- Scope columns: Timestamp, Frequency, Period, Mean, Pk-Pk, RMS, Amplitude
- SigGen columns: Timestamp, Waveform, Frequency, Amplitude, Offset,
  Stdev, Mean, Output - the STIMULUS that was driving the DUT, so a
  swept run records cause and effect in the same session
- A source that errors 5 times in a row is dropped from the run with
  one notice (no endless error spam); logging stops if all sources die

USAGE:
- Start Logging: Begin recording to CSV files
- Stop Logging: Close files and stop recording
- Files are flushed after each sample (safe if interrupted)
- Log status shows errors and warnings

TIPS:
- Configure instruments BEFORE starting logging
- For long tests, use longer sample intervals to reduce file size
- CSV files can be opened in Excel, Python, MATLAB, etc.
- Timestamps are in format: YYYY-MM-DD HH:MM:SS.mmm
- Check disk space for long-duration logging
- Stop logging before disconnecting instruments

ANALYSIS:
- Use pandas in Python: df = pd.read_csv('lcr_xxx.csv')
- Plot in Excel: Insert > Chart > Scatter
- For frequency sweeps: log at each frequency step"""
        
        messagebox.showinfo("Data Logging Tips", tips)
    
    def create_lcr_tab(self):
        """Create LCR meter control tab"""
        _tab = ScrollableTab(self.notebook)
        self.notebook.add(_tab, text="LCR Meter (BK 894)")
        lcr_frame = _tab.body
        
        # Connection status
        status_frame = ttk.LabelFrame(lcr_frame, text="Connection", padding=10)
        status_frame.pack(fill='x', padx=10, pady=10)
        
        self.lcr_status = tk.Label(status_frame, text="Not connected", fg="red")
        self.lcr_status.pack(side=tk.LEFT)
        
        ttk.Button(status_frame, text="Reconnect", command=self.reconnect_lcr).pack(side=tk.RIGHT)
        
        # Configuration
        config_frame = ttk.LabelFrame(lcr_frame, text="Configuration", padding=10)
        config_frame.pack(fill='x', padx=10, pady=10)
        
        # Measurement mode
        ttk.Label(config_frame, text="Mode:").grid(row=0, column=0, sticky='w', pady=5)
        self.lcr_mode = ttk.Combobox(config_frame, width=20, state='readonly')
        self.lcr_mode.grid(row=0, column=1, padx=10, pady=5)
        self.lcr_mode['values'] = list(BK894.MODES.keys())
        self.lcr_mode.set('CPD')
        add_tooltip(self.lcr_mode,
                    "Measurement pair (primary + secondary):\n"
                    + "\n".join(f"  {k}: {v}"
                                 for k, v in BK894.MODES.items())
                    + "\nCP/LP = parallel model (small C / large L);"
                    " CS/LS = series model (large C / low impedance).")
        
        # Frequency
        ttk.Label(config_frame, text="Frequency (Hz):").grid(row=1, column=0, sticky='w', pady=5)
        self.lcr_freq = ttk.Entry(config_frame, width=20)
        self.lcr_freq.grid(row=1, column=1, padx=10, pady=5)
        self.lcr_freq.insert(0, "1000")
        add_tooltip(self.lcr_freq,
                    "AC test frequency, 100 Hz to 500 kHz. 1 kHz is the "
                    "standard capacitor test; use 100 Hz for electrolytics.")
        
        # Voltage
        ttk.Label(config_frame, text="Voltage (V):").grid(row=2, column=0, sticky='w', pady=5)
        self.lcr_volt = ttk.Entry(config_frame, width=20)
        self.lcr_volt.grid(row=2, column=1, padx=10, pady=5)
        self.lcr_volt.insert(0, "1.0")
        add_tooltip(self.lcr_volt,
                    "AC test level, 0.01 to 2.0 V. 1.0 V is standard; the "
                    "meter clamps anything higher to 2.0 V.")

        # Bias / aperture / range / correction (issue #44) -- laid out to
        # the RIGHT of mode/freq/volt so the tab gets no taller (issue #26).
        ttk.Label(config_frame, text="DC Bias (V):").grid(
            row=0, column=2, sticky='e', padx=(24, 0))
        self.lcr_bias_volt = ttk.Entry(config_frame, width=8)
        self.lcr_bias_volt.grid(row=0, column=3, padx=6, sticky='w')
        self.lcr_bias_volt.insert(0, "0.0")
        add_tooltip(self.lcr_bias_volt,
                    "Steady DC voltage held across the DUT during the AC "
                    "test (for C-vs-bias curves). Takes effect only while "
                    "'Bias ON' is ticked, pushed on Apply.")
        self.lcr_bias_on = tk.BooleanVar(value=False)
        add_tooltip(ttk.Checkbutton(config_frame, text="Bias ON",
                                    variable=self.lcr_bias_on),
                    "Switch the internal DC bias source on/off "
                    "(applied on Apply Configuration).").grid(
            row=0, column=4, columnspan=2, sticky='w')

        ttk.Label(config_frame, text="Speed:").grid(
            row=1, column=2, sticky='e', padx=(24, 0))
        self.lcr_speed = ttk.Combobox(config_frame, width=6, state='readonly',
                                      values=list(BK894.APERTURE_SPEEDS))
        self.lcr_speed.set('MED')
        self.lcr_speed.grid(row=1, column=3, padx=6, sticky='w')
        add_tooltip(self.lcr_speed,
                    "Measurement aperture (integration time per reading): "
                    "SLOW = most accurate, quietest D readings; FAST = "
                    "quickest updates; MED is the everyday default.")
        ttk.Label(config_frame, text="Avg:").grid(row=1, column=4, sticky='e')
        self.lcr_avg = ttk.Entry(config_frame, width=5)
        self.lcr_avg.grid(row=1, column=5, padx=4, sticky='w')
        self.lcr_avg.insert(0, "1")
        add_tooltip(self.lcr_avg,
                    "Instrument-side averaging: the meter averages this "
                    "many raw measurements (1-256) into each reported "
                    "reading. Higher = smoother but slower.")

        self.lcr_autorange = tk.BooleanVar(value=True)
        add_tooltip(ttk.Checkbutton(config_frame, text="Auto range",
                                    variable=self.lcr_autorange),
                    "ON: the meter picks its range automatically. Turn OFF "
                    "to hold the current range during sweeps on a fixed "
                    "DUT (no mid-sweep range-change glitches).").grid(
            row=2, column=2, columnspan=2, sticky='w', padx=(24, 0))
        add_tooltip(
            ttk.Button(config_frame, text="Open Correction...",
                       command=lambda: self.lcr_run_correction('open')),
            "Fixture de-embedding: with NOTHING connected, sweep every "
            "test frequency and subtract the fixture's stray capacitance "
            "from all future readings. Takes tens of seconds.").grid(
            row=2, column=4, padx=2)
        add_tooltip(
            ttk.Button(config_frame, text="Short Correction...",
                       command=lambda: self.lcr_run_correction('short')),
            "Fixture de-embedding: with the terminals SHORTED, sweep and "
            "subtract lead resistance/inductance. Takes tens of "
            "seconds.").grid(
            row=2, column=5, padx=2)

        # Apply button
        ttk.Button(config_frame, text="Apply Configuration",
                   command=self.apply_lcr_config).grid(row=3, column=0, columnspan=2, pady=10)
        self.lcr_corr_label = ttk.Label(config_frame, text="Correction: --")
        self.lcr_corr_label.grid(row=3, column=2, columnspan=4, sticky='w',
                                 padx=(24, 0))
        
        # Measurement display
        meas_frame = ttk.LabelFrame(lcr_frame, text="Current Measurement", padding=10)
        meas_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        self.lcr_primary_label = tk.Label(meas_frame, text="Primary: --", font=("Arial", 16))
        self.lcr_primary_label.pack(pady=10)
        
        self.lcr_secondary_label = tk.Label(meas_frame, text="Secondary: --", font=("Arial", 16))
        self.lcr_secondary_label.pack(pady=10)
        
        self.lcr_status_label = tk.Label(meas_frame, text="Status: --")
        self.lcr_status_label.pack(pady=5)
        
        # Control buttons
        button_frame = ttk.Frame(meas_frame)
        button_frame.pack(pady=10)
        
        ttk.Button(button_frame, text="Single Measurement", 
                   command=self.lcr_single_measurement).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Start Continuous", 
                   command=self.lcr_start_continuous).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Stop",
                   command=self.lcr_stop_continuous).pack(side=tk.LEFT, padx=5)

        # Sweep section (freq × voltage × dwell)
        self._create_lcr_sweep_section(lcr_frame)

        # Tips button at bottom
        tips_frame = ttk.Frame(lcr_frame)
        tips_frame.pack(side=tk.BOTTOM, fill='x', padx=10, pady=5)
        ttk.Button(tips_frame, text="📖 Show Usage Tips",
                   command=self.show_lcr_tips).pack(side=tk.RIGHT)

        self.lcr_continuous = False
        # Mode the instrument is actually in (set on Apply / read-back);
        # readouts are labeled by THIS, not the dropdown (issue #38).
        self.lcr_applied_mode = None

    # ---- LCR Sweep ------------------------------------------------------

    def _create_lcr_sweep_section(self, parent):
        """Build the Sweep section on the LCR tab.

        Layout: Frequency axis box and Voltage axis box side by side,
        then sweep-wide options (order, dwell, samples, inter-sample
        delay), output path, Start/Stop, progress bar, status line.
        """
        sweep_frame = ttk.LabelFrame(parent, text="Sweep (freq × voltage × dwell)", padding=10)
        sweep_frame.pack(fill='x', padx=10, pady=10)
        sweep_frame.columnconfigure(0, weight=1)
        sweep_frame.columnconfigure(1, weight=1)

        # Frequency axis
        self._build_sweep_axis(
            sweep_frame, axis='freq', title="Frequency axis",
            unit='Hz', default_start='100', default_stop='100000',
            default_points='11', default_scale='log',
            default_list='100, 1000, 10000, 100000', column=0,
        )

        # Voltage axis
        self._build_sweep_axis(
            sweep_frame, axis='volt', title="Voltage axis",
            unit='V', default_start='0.1', default_stop='1.0',
            default_points='5', default_scale='linear',
            default_list='0.1, 0.5, 1.0', column=1,
        )

        # Sweep-wide options
        opts = ttk.Frame(sweep_frame)
        opts.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(10, 4))

        ttk.Label(opts, text="Order:").grid(row=0, column=0, sticky='w')
        self.sw_order = ttk.Combobox(opts, width=24, state='readonly',
                                     values=['Freq outer, V inner',
                                             'V outer, Freq inner'])
        self.sw_order.set('Freq outer, V inner')
        self.sw_order.grid(row=0, column=1, padx=(4, 16))

        ttk.Label(opts, text="Dwell (s):").grid(row=0, column=2, sticky='w')
        self.sw_dwell = ttk.Entry(opts, width=8)
        self.sw_dwell.insert(0, '0.5')
        self.sw_dwell.grid(row=0, column=3, padx=(4, 16))

        ttk.Label(opts, text="Samples/point:").grid(row=0, column=4, sticky='w')
        self.sw_samples = ttk.Entry(opts, width=6)
        self.sw_samples.insert(0, '5')
        self.sw_samples.grid(row=0, column=5, padx=(4, 16))

        ttk.Label(opts, text="Inter-sample (s):").grid(row=0, column=6, sticky='w')
        self.sw_isd = ttk.Entry(opts, width=8)
        self.sw_isd.insert(0, '0.05')
        self.sw_isd.grid(row=0, column=7, padx=(4, 0))

        # Output path
        out = ttk.Frame(sweep_frame)
        out.grid(row=2, column=0, columnspan=2, sticky='ew', pady=4)
        out.columnconfigure(1, weight=1)
        ttk.Label(out, text="Output CSV:").grid(row=0, column=0, sticky='w')
        self.sw_output = tk.StringVar()
        ttk.Entry(out, textvariable=self.sw_output).grid(row=0, column=1, sticky='ew', padx=4)
        ttk.Button(out, text="Browse...", command=self._browse_sweep_output).grid(row=0, column=2)
        ttk.Label(out, text="(blank → <log dir>/sweep_<timestamp>.csv)",
                  foreground='gray').grid(row=1, column=1, sticky='w', padx=4)

        # Controls + progress
        ctrl = ttk.Frame(sweep_frame)
        ctrl.grid(row=3, column=0, columnspan=2, sticky='ew', pady=(8, 0))
        ctrl.columnconfigure(2, weight=1)
        self.sw_start_btn = ttk.Button(ctrl, text="Start Sweep", command=self.lcr_start_sweep)
        self.sw_start_btn.grid(row=0, column=0, padx=(0, 6))
        self.sw_stop_btn = ttk.Button(ctrl, text="Stop", command=self.lcr_stop_sweep, state='disabled')
        self.sw_stop_btn.grid(row=0, column=1, padx=(0, 12))
        self.sw_progress = ttk.Progressbar(ctrl, mode='determinate')
        self.sw_progress.grid(row=0, column=2, sticky='ew')

        self.sw_status = ttk.Label(sweep_frame, text="Idle.", foreground='gray')
        self.sw_status.grid(row=4, column=0, columnspan=2, sticky='w', pady=(4, 0))

        self._update_sweep_mode_state('freq')
        self._update_sweep_mode_state('volt')

    def _build_sweep_axis(self, parent, axis, title, unit, default_start, default_stop,
                          default_points, default_scale, default_list, column):
        """Build one axis (freq or volt) sub-LabelFrame inside the sweep frame."""
        box = ttk.LabelFrame(parent, text=title, padding=8)
        box.grid(row=0, column=column, sticky='nsew', padx=4)
        box.columnconfigure(1, weight=1)
        box.columnconfigure(3, weight=1)

        mode_var = tk.StringVar(value='range')
        scale_var = tk.StringVar(value=default_scale)
        list_var = tk.StringVar(value=default_list)
        # Trace so the enabled/disabled state follows mode_var no matter how
        # it changes (radio click, programmatic set, future load-config, etc.).
        mode_var.trace_add('write', lambda *_a, ax=axis: self._update_sweep_mode_state(ax))

        ttk.Radiobutton(box, text="Range", variable=mode_var, value='range'
                        ).grid(row=0, column=0, sticky='w')
        ttk.Radiobutton(box, text="List", variable=mode_var, value='list'
                        ).grid(row=0, column=1, sticky='w')

        ttk.Label(box, text=f"Start ({unit}):").grid(row=1, column=0, sticky='w', pady=2)
        start_entry = ttk.Entry(box, width=12)
        start_entry.insert(0, default_start)
        start_entry.grid(row=1, column=1, sticky='ew', padx=(4, 12), pady=2)

        ttk.Label(box, text=f"Stop ({unit}):").grid(row=1, column=2, sticky='w', pady=2)
        stop_entry = ttk.Entry(box, width=12)
        stop_entry.insert(0, default_stop)
        stop_entry.grid(row=1, column=3, sticky='ew', padx=(4, 0), pady=2)

        ttk.Label(box, text="Points:").grid(row=2, column=0, sticky='w', pady=2)
        points_entry = ttk.Entry(box, width=12)
        points_entry.insert(0, default_points)
        points_entry.grid(row=2, column=1, sticky='ew', padx=(4, 12), pady=2)

        ttk.Label(box, text="Scale:").grid(row=2, column=2, sticky='w', pady=2)
        scale_combo = ttk.Combobox(box, width=10, state='readonly',
                                   values=['linear', 'log'], textvariable=scale_var)
        scale_combo.grid(row=2, column=3, sticky='ew', padx=(4, 0), pady=2)

        ttk.Label(box, text=f"List ({unit}):").grid(row=3, column=0, sticky='w', pady=2)
        list_entry = ttk.Entry(box, textvariable=list_var)
        list_entry.grid(row=3, column=1, columnspan=3, sticky='ew', padx=(4, 0), pady=2)

        # Store handles under axis prefix
        setattr(self, f'sw_{axis}_mode', mode_var)
        setattr(self, f'sw_{axis}_scale', scale_var)
        setattr(self, f'sw_{axis}_list_var', list_var)
        setattr(self, f'sw_{axis}_range_widgets', [start_entry, stop_entry, points_entry, scale_combo])
        setattr(self, f'sw_{axis}_list_widget', list_entry)
        setattr(self, f'sw_{axis}_start_entry', start_entry)
        setattr(self, f'sw_{axis}_stop_entry', stop_entry)
        setattr(self, f'sw_{axis}_points_entry', points_entry)

    def _update_sweep_mode_state(self, axis):
        """Grey out the inactive set of inputs for the given axis."""
        mode = getattr(self, f'sw_{axis}_mode').get()
        range_widgets = getattr(self, f'sw_{axis}_range_widgets')
        list_widget = getattr(self, f'sw_{axis}_list_widget')
        if mode == 'range':
            for w in range_widgets:
                w.configure(state='normal' if not isinstance(w, ttk.Combobox) else 'readonly')
            list_widget.configure(state='disabled')
        else:
            for w in range_widgets:
                w.configure(state='disabled')
            list_widget.configure(state='normal')

    def _browse_sweep_output(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile=f"sweep_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        )
        if path:
            self.sw_output.set(path)

    def _default_sweep_path(self):
        log_dir = self.log_dir.get() if hasattr(self, 'log_dir') else './logs'
        os.makedirs(log_dir, exist_ok=True)
        return os.path.join(log_dir, f"sweep_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")

    def _parse_sweep_axis(self, axis, vmin, vmax, name):
        """Return a list[float] for the given axis, validated against [vmin, vmax]."""
        mode = getattr(self, f'sw_{axis}_mode').get()
        if mode == 'list':
            raw = getattr(self, f'sw_{axis}_list_var').get()
            try:
                values = [float(x) for x in raw.replace(';', ',').split(',') if x.strip()]
            except ValueError as e:
                raise ValueError(f"{name} list could not be parsed: {e}")
            if not values:
                raise ValueError(f"{name} list is empty")
        else:
            try:
                start = float(getattr(self, f'sw_{axis}_start_entry').get())
                stop = float(getattr(self, f'sw_{axis}_stop_entry').get())
                points = int(getattr(self, f'sw_{axis}_points_entry').get())
            except ValueError as e:
                raise ValueError(f"{name} range fields must be numeric: {e}")
            if points < 1:
                raise ValueError(f"{name} points must be ≥ 1")
            scale = getattr(self, f'sw_{axis}_scale').get()
            if points == 1:
                values = [start]
            elif scale == 'log':
                if start <= 0 or stop <= 0:
                    raise ValueError(f"{name} log sweep requires positive start and stop")
                ratio = (stop / start) ** (1.0 / (points - 1))
                values = [start * (ratio ** i) for i in range(points - 1)]
                values.append(stop)  # snap endpoint to avoid 100000.00000000003
            else:
                step = (stop - start) / (points - 1)
                values = [start + step * i for i in range(points - 1)]
                values.append(stop)
        for v in values:
            if not (vmin <= v <= vmax):
                raise ValueError(f"{name} value {v} is outside [{vmin}, {vmax}]")
        return values

    def _set_sweep_ui_state(self, running):
        """Enable/disable sweep controls while a sweep is in progress."""
        state = 'disabled' if running else 'normal'
        # Lock the per-axis inputs (re-running _update_sweep_mode_state will
        # restore proper enabled set when running=False).
        for axis in ('freq', 'volt'):
            for w in getattr(self, f'sw_{axis}_range_widgets'):
                w.configure(state='disabled')
            getattr(self, f'sw_{axis}_list_widget').configure(state='disabled')
        self.sw_dwell.configure(state=state)
        self.sw_samples.configure(state=state)
        self.sw_isd.configure(state=state)
        self.sw_order.configure(state='disabled' if running else 'readonly')
        self.sw_start_btn.configure(state='disabled' if running else 'normal')
        self.sw_stop_btn.configure(state='normal' if running else 'disabled')
        if not running:
            self._update_sweep_mode_state('freq')
            self._update_sweep_mode_state('volt')

    def lcr_start_sweep(self):
        if not self.lcr:
            messagebox.showerror("Error", "LCR meter not connected")
            return
        if self.sweeping:
            return
        try:
            freqs = self._parse_sweep_axis('freq', 100, 500000, 'Frequency')
            volts = self._parse_sweep_axis('volt', 0.01, 2.0, 'Voltage')
            dwell = float(self.sw_dwell.get())
            n_samples = int(self.sw_samples.get())
            isd = float(self.sw_isd.get())
            if dwell < 0 or isd < 0:
                raise ValueError("Dwell and inter-sample delay must be ≥ 0")
            if n_samples < 1:
                raise ValueError("Samples per point must be ≥ 1")
        except ValueError as e:
            messagebox.showerror("Invalid sweep parameters", str(e))
            return

        out_path = self.sw_output.get().strip() or self._default_sweep_path()
        self.sw_output.set(out_path)
        mode = self.lcr_mode.get()
        order = self.sw_order.get()

        total_samples = len(freqs) * len(volts) * n_samples
        # Crude estimate so user can sanity-check: per-sample ~0.2s + dwell once per point.
        est_seconds = total_samples * 0.2 + len(freqs) * len(volts) * dwell + total_samples * isd
        if est_seconds > 300:
            mins = est_seconds / 60.0
            if not messagebox.askyesno("Long sweep",
                                       f"Estimated run time: ~{mins:.1f} min "
                                       f"({total_samples} samples). Continue?"):
                return

        self.sweeping = True
        self._set_sweep_ui_state(running=True)
        self.sw_progress.configure(maximum=total_samples, value=0)
        self.sw_status.config(text=f"Starting sweep: {len(freqs)} freqs × {len(volts)} V × "
                                   f"{n_samples} samples → {out_path}", foreground='black')
        self.status_bar.config(text="LCR sweep running...")

        # Drain the queue in case anything stale is in there from a previous run.
        while not self.sweep_queue.empty():
            try:
                self.sweep_queue.get_nowait()
            except queue.Empty:
                break

        self.sweep_thread = threading.Thread(
            target=self._lcr_sweep_worker,
            args=(freqs, volts, mode, order, dwell, n_samples, isd, out_path, total_samples),
            daemon=True,
        )
        self.sweep_thread.start()
        self.root.after(50, self._drain_sweep_queue)

    def lcr_stop_sweep(self):
        if self.sweeping:
            self.sweeping = False
            self.sw_status.config(text="Stop requested — finishing current sample...",
                                  foreground='orange')

    def _interruptible_sleep(self, seconds, chunk=0.05):
        """Sleep in small chunks so cancellation stays snappy."""
        end = time.monotonic() + seconds
        while self.sweeping:
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(chunk, remaining))

    def _lcr_sweep_worker(self, freqs, volts, mode, order, dwell, n_samples, isd,
                          out_path, total_samples):
        """Background thread: run the sweep, write CSV, push events onto sweep_queue.

        Worker NEVER touches Tk directly — all UI changes go through the queue,
        which is drained on the main thread by _drain_sweep_queue. Calling
        root.after() from a worker thread is not safe in tkinter and crashes
        with "main thread is not in main loop".
        """
        sample_count = 0
        try:
            self.lcr.set_mode(mode)
            if order == 'Freq outer, V inner':
                outer_vals, inner_vals = freqs, volts
                outer_setter, inner_setter = self.lcr.set_frequency, self.lcr.set_voltage
            else:
                outer_vals, inner_vals = volts, freqs
                outer_setter, inner_setter = self.lcr.set_voltage, self.lcr.set_frequency

            with open(out_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['Timestamp', 'Mode', 'Frequency (Hz)', 'Voltage (V)',
                                 'Sample Index', 'Primary', 'Secondary', 'Status'])

                for outer_val in outer_vals:
                    if not self.sweeping:
                        break
                    outer_setter(outer_val)
                    for inner_val in inner_vals:
                        if not self.sweeping:
                            break
                        inner_setter(inner_val)
                        if order == 'Freq outer, V inner':
                            freq_val, volt_val = outer_val, inner_val
                        else:
                            volt_val, freq_val = outer_val, inner_val

                        if dwell > 0:
                            self._interruptible_sleep(dwell)
                        if not self.sweeping:
                            break

                        for s in range(n_samples):
                            if not self.sweeping:
                                break
                            primary, secondary, status = self.lcr.measure()
                            ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                            writer.writerow([ts, mode, freq_val, volt_val, s + 1,
                                             primary, secondary, status])
                            f.flush()
                            sample_count += 1
                            self.sweep_queue.put(('progress', sample_count, total_samples,
                                                  freq_val, volt_val, s + 1, n_samples))
                            if isd > 0 and s < n_samples - 1 and self.sweeping:
                                self._interruptible_sleep(isd)
        except Exception as e:
            self.sweep_queue.put(('error', str(e), out_path, sample_count))
            return

        cancelled = not self.sweeping
        self.sweep_queue.put(('done', out_path, sample_count, total_samples, cancelled))

    def _drain_sweep_queue(self):
        """Main-thread poller: drain events from the worker and update UI."""
        final = False
        try:
            while True:
                event = self.sweep_queue.get_nowait()
                kind = event[0]
                if kind == 'progress':
                    _, count, total, freq, volt, sample, n_samples = event
                    self.sw_progress.configure(value=count)
                    self.sw_status.config(
                        text=f"{count}/{total} samples — {freq:g} Hz, {volt:g} V, "
                             f"sample {sample}/{n_samples}",
                        foreground='black',
                    )
                elif kind == 'done':
                    _, out_path, written, total, cancelled = event
                    self._sweep_finished_ui(out_path, written, total, cancelled)
                    final = True
                elif kind == 'error':
                    _, err, out_path, written = event
                    self._sweep_failed_ui(err, out_path, written)
                    final = True
        except queue.Empty:
            pass
        if not final:
            self.root.after(50, self._drain_sweep_queue)

    def _sweep_finished_ui(self, out_path, samples_written, total_samples, cancelled):
        self.sweeping = False
        self._set_sweep_ui_state(running=False)
        if cancelled:
            msg = (f"Sweep cancelled after {samples_written}/{total_samples} samples. "
                   f"Partial data: {out_path}")
            self.sw_status.config(text=msg, foreground='orange')
        else:
            msg = f"Sweep complete: {samples_written} samples written to {out_path}"
            self.sw_status.config(text=msg, foreground='green')
        self.status_bar.config(text=msg)

    def _sweep_failed_ui(self, err, out_path, samples_written):
        self.sweeping = False
        self._set_sweep_ui_state(running=False)
        self.sw_status.config(text=f"Sweep failed after {samples_written} samples: {err}",
                              foreground='red')
        self.status_bar.config(text=f"Sweep failed: {err}")
        messagebox.showerror("Sweep error", f"{err}\n\nPartial CSV (if any): {out_path}")

    # ---- Software update (git pull + redeploy to the shared drive) ---------
    def _find_update_script(self):
        """Locate update_software.sh (deployed beside the app on the share)."""
        here = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            os.path.normpath(os.path.join(here, os.pardir, 'update_software.sh')),
            '/mnt/shareDrive/_software/update_software.sh',
        ]
        for path in candidates:
            if os.path.isfile(path):
                return path
        return None

    def open_update_software(self):
        """Pull the latest code and redeploy to the shared drive, then offer to
        restart. Runs update_software.sh in a worker thread; output is streamed
        to a dialog (queue-marshalled, never touching Tk off the main thread)."""
        if self._updating:
            return
        script = self._find_update_script()
        if not script:
            messagebox.showerror(
                "Update Software",
                "Could not find update_software.sh.\n\nExpected it on the shared "
                "drive at /mnt/shareDrive/_software/update_software.sh.")
            return
        if not messagebox.askyesno(
                "Update Software",
                "This pulls the latest code from GitHub, redeploys it to the "
                "shared drive for all users, then offers to restart.\n\nContinue?"):
            return

        win = tk.Toplevel(self.root)
        win.title("Update Software")
        win.geometry("720x430")
        win.transient(self.root)
        txt = scrolledtext.ScrolledText(win, wrap='word', font='TkFixedFont')
        txt.pack(fill='both', expand=True, padx=8, pady=(8, 4))
        txt.configure(state='disabled')
        btns = tk.Frame(win)
        btns.pack(fill='x', padx=8, pady=(0, 8))
        close_btn = tk.Button(btns, text="Close", command=win.destroy, state='disabled')
        close_btn.pack(side='right')
        restart_btn = tk.Button(btns, text="Restart now", command=self._restart_app,
                                state='disabled')
        restart_btn.pack(side='right', padx=(0, 6))

        q = queue.Queue()
        self._updating = True
        self.status_bar.config(text="Updating software…")
        self._append_update_text(txt, f"$ bash {script}\n\n")
        threading.Thread(target=self._update_worker, args=(script, q),
                         daemon=True).start()
        self.root.after(50, self._drain_update_queue, q, txt, close_btn, restart_btn)

    def _update_worker(self, script, q):
        """Background thread: run update_software.sh, stream output to the queue."""
        try:
            proc = subprocess.Popen(
                ['bash', script], stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1)
            for line in proc.stdout:
                q.put(('line', line))
            proc.wait()
            q.put(('done', proc.returncode))
        except Exception as e:
            q.put(('line', f"\n[error launching update] {e}\n"))
            q.put(('done', 1))

    def _append_update_text(self, txt, s):
        txt.configure(state='normal')
        txt.insert('end', s)
        txt.see('end')
        txt.configure(state='disabled')

    def _drain_update_queue(self, q, txt, close_btn, restart_btn):
        """Main-thread poller: drain update output and update the dialog."""
        final = False
        rc = None
        try:
            while True:
                kind, payload = q.get_nowait()
                if kind == 'line':
                    self._append_update_text(txt, payload)
                elif kind == 'done':
                    rc = payload
                    final = True
        except queue.Empty:
            pass
        if not final:
            self.root.after(50, self._drain_update_queue, q, txt, close_btn, restart_btn)
            return
        self._updating = False
        close_btn.config(state='normal')
        if rc == 0:
            self._append_update_text(
                txt, "\n✓ Update complete. Restart to load the new version.\n")
            self.status_bar.config(text="Update complete — restart to apply")
            restart_btn.config(state='normal')
        else:
            self._append_update_text(
                txt, f"\n✗ Update failed (exit {rc}). See output above.\n")
            self.status_bar.config(text=f"Update failed (exit {rc})")

    def _restart_app(self):
        """Re-exec the GUI process to load freshly-updated code."""
        try:
            self.root.destroy()
        except Exception:
            pass
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def create_scope_tab(self):
        """Create oscilloscope control tab"""
        _tab = ScrollableTab(self.notebook)
        self.notebook.add(_tab, text="Oscilloscope (MSO24)")
        scope_frame = _tab.body
        
        # Connection status
        status_frame = ttk.LabelFrame(scope_frame, text="Connection", padding=10)
        status_frame.pack(fill='x', padx=10, pady=10)
        
        self.scope_status = tk.Label(status_frame, text="Not connected", fg="red")
        self.scope_status.pack(side=tk.LEFT)
        
        ttk.Button(status_frame, text="Reconnect", command=self.reconnect_scope).pack(side=tk.RIGHT)
        
        # Channel configuration - using notebook for each channel
        config_notebook = ttk.Notebook(scope_frame)
        config_notebook.pack(fill='x', padx=10, pady=10)
        
        # Store channel widgets
        self.channel_widgets = {}
        
        for ch in range(1, 5):
            ch_frame = ttk.Frame(config_notebook)
            config_notebook.add(ch_frame, text=f"Channel {ch}")
            
            # Enable checkbox
            enable_var = tk.BooleanVar(value=(ch == 1))
            ttk.Checkbutton(ch_frame, text="Enable Channel", 
                           variable=enable_var,
                           command=lambda c=ch, v=enable_var: self.toggle_channel(c, v)).grid(
                               row=0, column=0, columnspan=4, sticky='w', padx=10, pady=10)
            
            # Vertical scale
            ttk.Label(ch_frame, text="Vertical (V/div):").grid(row=1, column=0, sticky='w', padx=10, pady=5)
            vscale = ttk.Entry(ch_frame, width=12)
            vscale.grid(row=1, column=1, padx=5, pady=5)
            vscale.insert(0, "1.0")
            add_tooltip(vscale, "Vertical sensitivity: volts per grid "
                                "division for this channel.")
            
            # Coupling
            ttk.Label(ch_frame, text="Coupling:").grid(row=1, column=2, sticky='w', padx=(20,0), pady=5)
            coupling = ttk.Combobox(ch_frame, width=10, state='readonly')
            coupling.grid(row=1, column=3, padx=5, pady=5)
            coupling['values'] = ['DC', 'AC', 'GND']
            coupling.set('DC')
            add_tooltip(coupling, "DC = show everything; AC = block the DC "
                                  "level (ripple on a bias); GND = flat "
                                  "zero-reference.")
            
            # Position
            ttk.Label(ch_frame, text="Position (div):").grid(row=2, column=0, sticky='w', padx=10, pady=5)
            position = ttk.Entry(ch_frame, width=12)
            position.grid(row=2, column=1, padx=5, pady=5)
            position.insert(0, "0")
            add_tooltip(position, "Vertical position of the trace, in "
                                  "divisions from center.")
            
            # Trigger source option (only show on CH1-4)
            ttk.Label(ch_frame, text="Use as trigger:").grid(row=2, column=2, sticky='w', padx=(20,0), pady=5)
            trigger_var = tk.BooleanVar(value=(ch == 1))
            add_tooltip(ttk.Checkbutton(ch_frame, variable=trigger_var),
                        "Use this channel as the edge-trigger source. If "
                        "several are ticked the lowest-numbered wins; "
                        "takes effect on Apply All Settings.").grid(
                row=2, column=3, padx=5, pady=5)
            
            # Apply button for this channel
            ttk.Button(ch_frame, text=f"Apply CH{ch} Config", 
                      command=lambda c=ch: self.apply_channel_config(c)).grid(
                          row=3, column=0, columnspan=4, pady=10)
            
            # Store widgets
            self.channel_widgets[ch] = {
                'enable': enable_var,
                'vscale': vscale,
                'coupling': coupling,
                'position': position,
                'trigger': trigger_var
            }
        
        # Horizontal and trigger configuration
        control_frame = ttk.LabelFrame(scope_frame, text="Timebase & Trigger", padding=10)
        control_frame.pack(fill='x', padx=10, pady=10)
        
        # Horizontal scale
        ttk.Label(control_frame, text="Horizontal (s/div):").grid(row=0, column=0, sticky='w', pady=5)
        self.scope_hscale = ttk.Entry(control_frame, width=15)
        self.scope_hscale.grid(row=0, column=1, padx=10, pady=5)
        self.scope_hscale.insert(0, "0.001")
        add_tooltip(self.scope_hscale, "Timebase: seconds per grid "
                                       "division (whole screen = 10 div).")
        
        # Trigger level
        ttk.Label(control_frame, text="Trigger Level (V):").grid(row=0, column=2, sticky='w', pady=5, padx=(20,0))
        self.scope_trig_level = ttk.Entry(control_frame, width=10)
        self.scope_trig_level.grid(row=0, column=3, padx=10, pady=5)
        self.scope_trig_level.insert(0, "0")
        add_tooltip(self.scope_trig_level,
                    "Voltage the trigger-source signal must cross to "
                    "start an acquisition.")
        
        # Trigger slope
        ttk.Label(control_frame, text="Trigger Slope:").grid(row=1, column=0, sticky='w', pady=5)
        self.scope_trig_slope = ttk.Combobox(control_frame, width=12, state='readonly')
        self.scope_trig_slope.grid(row=1, column=1, padx=10, pady=5)
        self.scope_trig_slope['values'] = ['RISE', 'FALL']
        self.scope_trig_slope.set('RISE')
        add_tooltip(self.scope_trig_slope,
                    "Trigger on the rising or falling edge crossing the "
                    "trigger level.")
        
        # Apply all button
        ttk.Button(control_frame, text="Apply All Settings", 
                  command=self.apply_all_scope_config).grid(row=2, column=0, columnspan=4, pady=10)
        
        # Measurements display - tabbed by channel
        meas_notebook = ttk.Notebook(scope_frame)
        meas_notebook.pack(fill='both', expand=True, padx=10, pady=10)
        
        self.scope_meas_labels = {}
        
        for ch in range(1, 5):
            meas_frame = ttk.Frame(meas_notebook)
            meas_notebook.add(meas_frame, text=f"CH{ch} Measurements")
            
            # Create measurement labels for this channel
            ch_labels = {}
            row = 0
            for meas in ['Frequency', 'Period', 'Mean', 'Pk-Pk', 'RMS', 'Amplitude']:
                label = tk.Label(meas_frame, text=f"{meas}: --", font=("Arial", 12), anchor='w')
                label.grid(row=row//2, column=row%2, sticky='w', padx=20, pady=5)
                ch_labels[meas.lower().replace('-', '')] = label
                row += 1
            
            # Buttons for this channel
            button_frame = ttk.Frame(meas_frame)
            button_frame.grid(row=3, column=0, columnspan=2, pady=10)
            
            ttk.Button(button_frame, text=f"Get CH{ch} Measurements", 
                      command=lambda c=ch: self.scope_get_measurements(c)).pack(side=tk.LEFT, padx=5)
            ttk.Button(button_frame, text=f"Capture CH{ch} Waveform", 
                      command=lambda c=ch: self.scope_capture_waveform(c)).pack(side=tk.LEFT, padx=5)
            
            self.scope_meas_labels[ch] = ch_labels
        
        # Acquisition control buttons
        acq_frame = ttk.LabelFrame(scope_frame, text="Acquisition Control", padding=10)
        acq_frame.pack(fill='x', padx=10, pady=10)
        
        button_frame = ttk.Frame(acq_frame)
        button_frame.pack()
        
        ttk.Button(button_frame, text="Run", 
                  command=self.scope_run).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Stop", 
                  command=self.scope_stop).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Single", 
                  command=self.scope_single).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="AutoSet", 
                  command=self.scope_autoset).pack(side=tk.LEFT, padx=5)
        
        # Tips button at bottom
        tips_frame = ttk.Frame(scope_frame)
        tips_frame.pack(side=tk.BOTTOM, fill='x', padx=10, pady=5)
        ttk.Button(tips_frame, text="📖 Show Usage Tips", 
                   command=self.show_scope_tips).pack(side=tk.RIGHT)
    
    def create_sg_tab(self):
        """Create signal generator control tab"""
        _tab = ScrollableTab(self.notebook)
        self.notebook.add(_tab, text="Signal Gen (BK 4055B)")
        sg_frame = _tab.body

        # Connection status
        status_frame = ttk.LabelFrame(sg_frame, text="Connection", padding=10)
        status_frame.pack(fill='x', padx=10, pady=10)

        self.sg_status = tk.Label(status_frame, text="Not connected", fg="red")
        self.sg_status.pack(side=tk.LEFT)

        ttk.Button(status_frame, text="Reconnect", command=self.reconnect_sg).pack(side=tk.RIGHT)

        # Basic/Advanced toggle: advanced reveals load/polarity/phase and the
        # pulse edge-timing fields; basic keeps the form to the essentials.
        self.sg_advanced = tk.BooleanVar(value=False)
        ttk.Checkbutton(status_frame, text="Advanced mode",
                        variable=self.sg_advanced,
                        command=self._sg_advanced_toggled).pack(side=tk.RIGHT, padx=10)

        # Per-channel configuration - one inner tab per output
        config_notebook = ttk.Notebook(sg_frame)
        config_notebook.pack(fill='x', padx=10, pady=10)

        self.sg_channel_widgets = {}

        for ch in (1, 2):
            ch_frame = ttk.Frame(config_notebook)
            config_notebook.add(ch_frame, text=f"Channel {ch}")
            self._sg_build_channel_panel(ch_frame, ch)

        # Presets
        preset_frame = ttk.LabelFrame(sg_frame, text="Presets", padding=10)
        preset_frame.pack(fill='x', padx=10, pady=10)

        ttk.Label(preset_frame, text="Saved presets:").grid(row=0, column=0, sticky='w', pady=5)
        self.sg_preset_select = ttk.Combobox(preset_frame, width=22, state='readonly')
        self.sg_preset_select.grid(row=0, column=1, padx=10, pady=5)

        ttk.Button(preset_frame, text="Load Preset",
                   command=self.sg_load_preset).grid(row=0, column=2, padx=5)
        ttk.Button(preset_frame, text="Delete Preset",
                   command=self.sg_delete_preset).grid(row=0, column=3, padx=5)

        ttk.Label(preset_frame, text="Save current as:").grid(row=1, column=0, sticky='w', pady=5)
        self.sg_preset_name = ttk.Entry(preset_frame, width=24)
        self.sg_preset_name.grid(row=1, column=1, padx=10, pady=5)
        ttk.Button(preset_frame, text="Save Preset",
                   command=self.sg_save_preset).grid(row=1, column=2, padx=5)

        self.sg_refresh_presets()

        # Tips button at bottom
        tips_frame = ttk.Frame(sg_frame)
        tips_frame.pack(side=tk.BOTTOM, fill='x', padx=10, pady=5)
        ttk.Button(tips_frame, text="📖 Show Usage Tips",
                   command=self.show_sg_tips).pack(side=tk.RIGHT)

    def create_logging_tab(self):
        """Create data logging tab"""
        _tab = ScrollableTab(self.notebook)
        self.notebook.add(_tab, text="Data Logging")
        log_frame = _tab.body
        
        # Logging configuration
        config_frame = ttk.LabelFrame(log_frame, text="Logging Configuration", padding=10)
        config_frame.pack(fill='x', padx=10, pady=10)
        
        # Log file selection
        ttk.Label(config_frame, text="Log Directory:").grid(row=0, column=0, sticky='w', pady=5)
        self.log_dir = tk.StringVar(value="./logs")
        ttk.Entry(config_frame, textvariable=self.log_dir, width=40).grid(row=0, column=1, padx=10, pady=5)
        ttk.Button(config_frame, text="Browse", 
                   command=self.select_log_dir).grid(row=0, column=2, padx=5)
        
        # Sample rate
        ttk.Label(config_frame, text="Sample Interval (s):").grid(row=1, column=0, sticky='w', pady=5)
        self.log_interval = ttk.Entry(config_frame, width=15)
        add_tooltip(self.log_interval,
                    "Seconds between samples. All ticked sources are "
                    "sampled once per interval; each gets its own "
                    "timestamped CSV.")
        self.log_interval.grid(row=1, column=1, sticky='w', padx=10, pady=5)
        self.log_interval.insert(0, "1.0")
        
        # Instrument selection
        ttk.Label(config_frame, text="Log Instruments:").grid(row=2, column=0, sticky='nw', pady=5)
        instr_frame = ttk.Frame(config_frame)
        instr_frame.grid(row=2, column=1, sticky='w', padx=10)
        
        self.log_lcr = tk.BooleanVar(value=True)
        ttk.Checkbutton(instr_frame, text="LCR Meter", variable=self.log_lcr).pack(anchor='w')
        
        # Scope channel selection
        scope_frame = ttk.LabelFrame(instr_frame, text="Oscilloscope Channels", padding=5)
        scope_frame.pack(anchor='w', pady=5)
        
        self.log_scope_channels = {}
        for ch in range(1, 5):
            var = tk.BooleanVar(value=(ch == 1))
            ttk.Checkbutton(scope_frame, text=f"CH{ch}", variable=var).pack(anchor='w')
            self.log_scope_channels[ch] = var

        # Sig-gen stimulus channels (issue #46): record what was DRIVING
        # the DUT alongside the measured response.
        sg_frame = ttk.LabelFrame(instr_frame, text="Sig Gen (stimulus)",
                                  padding=5)
        sg_frame.pack(anchor='w', pady=5)
        self.log_sg_channels = {}
        for ch in (1, 2):
            var = tk.BooleanVar(value=False)
            ttk.Checkbutton(sg_frame, text=f"CH{ch}",
                            variable=var).pack(anchor='w')
            self.log_sg_channels[ch] = var
        
        # Control buttons
        button_frame = ttk.Frame(config_frame)
        button_frame.grid(row=3, column=0, columnspan=3, pady=20)
        
        self.log_start_btn = ttk.Button(button_frame, text="Start Logging", 
                                         command=self.start_logging)
        self.log_start_btn.pack(side=tk.LEFT, padx=5)
        
        self.log_stop_btn = ttk.Button(button_frame, text="Stop Logging", 
                                        command=self.stop_logging, state='disabled')
        self.log_stop_btn.pack(side=tk.LEFT, padx=5)
        
        # Log display
        display_frame = ttk.LabelFrame(log_frame, text="Log Status", padding=10)
        display_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        self.log_text = tk.Text(display_frame, height=12, state='disabled')
        self.log_text.pack(fill='both', expand=True)
        
        scrollbar = ttk.Scrollbar(display_frame, command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar.set)
        
        # Tips button at bottom
        tips_frame = ttk.Frame(log_frame)
        tips_frame.pack(side=tk.BOTTOM, fill='x', padx=10, pady=5)
        ttk.Button(tips_frame, text="📖 Show Usage Tips", 
                   command=self.show_logging_tips).pack(side=tk.RIGHT)
    
    # LCR methods
    def reconnect_lcr(self):
        self._reconnect('lcr', BK894, self.lcr_status,
                        sync=self.update_lcr_config)

    def update_lcr_config(self):
        """Read current config from instrument"""
        if not self.lcr:
            return
        def work():
            data = {'config': self.lcr.get_config()}
            # Bias / aperture / correction (issue #44) are best-effort so an
            # older firmware can't break the basic config sync.
            try:
                data['bias'] = self.lcr.get_bias()
                data['aperture'] = self.lcr.get_aperture()
                data['corr'] = self.lcr.get_correction_states()
            except Exception:
                pass
            return data

        def done(data, error):
            if error:
                self.status_bar.config(text=f"Error reading config: {error}")
                return
            config = data['config']
            self.lcr_mode.set(config['mode'].upper())
            self.lcr_applied_mode = config['mode'].upper()
            self._set_entry(self.lcr_freq, int(config['frequency']))
            if 'voltage' in config:
                self._set_entry(self.lcr_volt, config['voltage'])
            if 'bias' in data:
                self._set_entry(self.lcr_bias_volt, data['bias']['volts'])
                self.lcr_bias_on.set(data['bias']['on'])
                speed, avg = data['aperture']
                if speed in BK894.APERTURE_SPEEDS:
                    self.lcr_speed.set(speed)
                self._set_entry(self.lcr_avg, avg)
                self.lcr_corr_label.config(
                    text=self._lcr_corr_text(data['corr']))

        self._run_bg(work, done, busy='lcr-io', quiet=True)

    @staticmethod
    def _lcr_corr_text(corr):
        return (f"Correction: open {'ON' if corr['open'] else 'off'}, "
                f"short {'ON' if corr['short'] else 'off'}")

    def lcr_run_correction(self, kind):
        """Open/short fixture correction: sweeps every test frequency, takes
        tens of seconds -- runs off the UI thread (issues #44/#40)."""
        if not self.lcr:
            messagebox.showerror("Error", "LCR meter not connected")
            return
        prep = {
            'open': "Remove the DUT so the fixture is OPEN "
                    "(nothing connected).",
            'short': "SHORT the fixture terminals together "
                     "(shorting bar or thick wire).",
        }[kind]
        if not messagebox.askokcancel(
                f"{kind.capitalize()} correction",
                f"{prep}\n\nThe meter sweeps every test frequency -- this "
                "takes tens of seconds and the meter is busy meanwhile. "
                "Start now?"):
            return
        self.lcr_stop_continuous()
        self.status_bar.config(text=f"Running {kind} correction sweep...")

        def work():
            self.lcr.run_correction(kind)
            return self.lcr.get_correction_states()

        def done(corr, error):
            if error:
                messagebox.showerror("Correction failed", str(error))
                self.status_bar.config(text=f"{kind} correction failed")
                return
            self.lcr_corr_label.config(text=self._lcr_corr_text(corr))
            self.status_bar.config(
                text=f"{kind.capitalize()} correction swept and applied")

        self._run_bg(work, done, busy='lcr-corr')
    
    def apply_lcr_config(self):
        if not self.lcr:
            messagebox.showerror("Error", "LCR meter not connected")
            return
        
        # Widget reads + validation on the main thread; VISA in a worker
        try:
            mode = self.lcr_mode.get()
            freq = float(self.lcr_freq.get())
            volt = float(self.lcr_volt.get())
            bias_v = float(self.lcr_bias_volt.get())
            avg = int(self.lcr_avg.get())
        except (TypeError, ValueError) as e:
            messagebox.showerror("Configuration Error", str(e))
            return
        speed = self.lcr_speed.get() or 'MED'
        bias_on = self.lcr_bias_on.get()
        autorange = self.lcr_autorange.get()

        def work():
            self.lcr.set_mode(mode)
            self.lcr.set_frequency(freq)
            self.lcr.set_voltage(volt)
            self.lcr.set_aperture(speed, avg)
            self.lcr.set_range_auto(autorange)
            self.lcr.set_bias_voltage(bias_v)
            self.lcr.set_bias_enabled(bias_on)
            time.sleep(0.3)

        def done(_result, error):
            if error:
                messagebox.showerror("Configuration Error", str(error))
                return
            self.lcr_applied_mode = mode.upper()
            bias_note = f", bias {bias_v:g} V ON" if bias_on else ""
            self.status_bar.config(
                text=f"LCR configured: {mode}, {freq} Hz, {volt} V"
                     f"{bias_note}")

        self._run_bg(work, done, busy='lcr-io')
    
    def lcr_single_measurement(self, from_continuous=False):
        if not self.lcr:
            if not from_continuous:
                messagebox.showerror("Error", "LCR meter not connected")
            return

        def done(result, error):
            if error:
                # In continuous mode this used to re-raise every 200 ms and
                # stack a modal dialog per tick (issue #38): stop polling
                # FIRST so exactly one dialog appears.
                was_continuous = self.lcr_continuous
                self.lcr_stop_continuous()
                if was_continuous:
                    self.status_bar.config(
                        text=f"Continuous LCR read stopped: {error}")
                messagebox.showerror("Measurement Error", str(error))
                return
            primary, secondary, status = result
            # Label by the mode the instrument is actually in, not whatever
            # the dropdown was flipped to since the last Apply (issue #38).
            mode = self.lcr_applied_mode or self.lcr_mode.get()
            p_str, s_str = lcr_format.format_measurement(mode, primary,
                                                         secondary)
            self.lcr_primary_label.config(text=f"Primary: {p_str}")
            self.lcr_secondary_label.config(text=f"Secondary: {s_str}")
            self.lcr_status_label.config(
                text=f"Status: {'OK' if status == 0 else 'Error'}")

        # Continuous ticks skip silently while a previous read (or an LCR
        # apply/config read) is still in flight -- natural rate limiting.
        self._run_bg(lambda: self.lcr.measure(), done, busy='lcr-io',
                     quiet=from_continuous)

    def lcr_start_continuous(self):
        self.lcr_continuous = True
        self.lcr_continuous_measurement()

    def lcr_stop_continuous(self):
        self.lcr_continuous = False

    def lcr_continuous_measurement(self):
        if self.lcr_continuous and self.lcr:
            self.lcr_single_measurement(from_continuous=True)
            self.root.after(200, self.lcr_continuous_measurement)
    
    # Scope methods
    def reconnect_scope(self):
        self._reconnect('scope', TekMSO24, self.scope_status)
    
    def toggle_channel(self, channel, enable_var):
        """Enable/disable a channel"""
        if not self.scope:
            return
        on = enable_var.get()
        self._bg_simple(
            lambda: self.scope.set_channel_enable(channel, on),
            f"CH{channel} {'enabled' if on else 'disabled'}",
            busy='scope-io')

    def apply_channel_config(self, channel):
        """Apply configuration for a specific channel"""
        if not self.scope:
            messagebox.showerror("Error", "Oscilloscope not connected")
            return
        widgets = self.channel_widgets[channel]
        try:
            vscale = float(widgets['vscale'].get())
            position = float(widgets['position'].get())
        except (TypeError, ValueError) as e:
            messagebox.showerror("Configuration Error", str(e))
            return
        coupling = widgets['coupling'].get()
        enable = widgets['enable'].get()

        def work():
            self.scope.set_channel_enable(channel, enable)
            self.scope.set_vertical(channel, scale=vscale, position=position,
                                    coupling=coupling)

        self._bg_simple(
            work,
            f"CH{channel} configured: {vscale}V/div, {coupling} coupling",
            busy='scope-io', err_title="Configuration Error")

    # Signal generator methods
    def _sg_build_channel_panel(self, parent, ch):
        """Build one channel's form (left), preview canvas (right), and the
        Apply / Output buttons. Field rows show/hide per waveform + mode."""
        container = ttk.Frame(parent)
        container.pack(fill='x', padx=10, pady=5)

        form = ttk.Frame(container)
        form.grid(row=0, column=0, sticky='nw')

        widgets = {'rows': {}, 'applied': {}}

        # Waveform selector (always visible), with its applied readout
        wrow = ttk.Frame(form)
        wrow.pack(fill='x', pady=2)
        ttk.Label(wrow, text="Waveform:", width=16).pack(side=tk.LEFT)
        waveform = ttk.Combobox(wrow, width=10, state='readonly')
        waveform['values'] = [w for w in BK4055B.WAVEFORMS
                              if SG_ARB_ENABLED or w != 'ARB']
        waveform.set('SINE')
        waveform.pack(side=tk.LEFT, padx=4)
        waveform.bind('<<ComboboxSelected>>',
                      lambda e, c=ch: self._sg_waveform_changed(c))
        add_tooltip(waveform, "Waveform type; the fields below adapt to "
                              "it. ARB = custom waveform (design in the "
                              "Waveform Editor, deliver via flash-drive "
                              ".bin).")
        applied_wave = tk.Label(wrow, text='--', fg='gray', width=14, anchor='w')
        applied_wave.pack(side=tk.LEFT, padx=4)
        widgets['waveform'] = waveform
        widgets['applied']['waveform'] = applied_wave

        # Parameter rows; visibility managed by _sg_update_visibility
        widgets['arb_name_var'] = tk.StringVar(value='')
        widgets['arb_samples'] = None   # staged samples for the preview

        for key in SG_FIELD_ORDER:
            row = ttk.Frame(form)
            ttk.Label(row, text=SG_FIELD_LABELS[key], width=16).pack(side=tk.LEFT)
            if key == 'arb':
                # Button opens the waveform editor; label shows chosen arb.
                # The disabled branch survives so SG_ARB_ENABLED can gate
                # the feature off again if the bench needs it.
                if SG_ARB_ENABLED:
                    w = ttk.Button(row, text="Waveform Editor...",
                                   command=lambda c=ch: self.sg_open_arb_dialog(c))
                else:
                    w = ttk.Button(row, text="Waveform Editor (disabled)",
                                   state='disabled')
                name_lbl = tk.Label(row, textvariable=widgets['arb_name_var'],
                                    width=12, anchor='w')
                w.pack(side=tk.LEFT, padx=4)
                name_lbl.pack(side=tk.LEFT)
            elif key == 'load':
                # Editable: pick High-Z / common values or type any ohms
                w = ttk.Combobox(row, width=9)
                w['values'] = [SG_LOAD_HIGHZ, '50', '75', '600', '10000']
                w.set(SG_LOAD_HIGHZ)
                w.pack(side=tk.LEFT, padx=4)
            elif key == 'polarity':
                w = ttk.Combobox(row, width=9, state='readonly')
                w['values'] = ['NOR', 'INVT']
                w.set('NOR')
                w.pack(side=tk.LEFT, padx=4)
            else:
                w = ttk.Entry(row, width=11)
                w.insert(0, SG_FIELD_DEFAULTS[key])
                w.bind('<KeyRelease>', lambda e, c=ch: self._sg_redraw_preview(c))
                w.pack(side=tk.LEFT, padx=4)
            applied = tk.Label(row, text='--', fg='gray', width=14, anchor='w')
            applied.pack(side=tk.LEFT, padx=4)
            if key in SG_FIELD_TOOLTIPS:
                add_tooltip(w, SG_FIELD_TOOLTIPS[key])
            widgets[key] = w
            widgets['rows'][key] = row
            widgets['applied'][key] = applied

        # Waveform preview
        right = ttk.Frame(container)
        right.grid(row=0, column=1, sticky='ne', padx=(25, 0))
        ttk.Label(right, text="Preview (~3 periods)").pack(anchor='w')
        canvas = tk.Canvas(right, width=330, height=170, bg='white',
                           highlightthickness=1, highlightbackground='#999')
        canvas.pack()
        widgets['canvas'] = canvas

        # Apply pushes configuration only; Output gates the physical output
        btns = ttk.Frame(parent)
        btns.pack(fill='x', padx=10, pady=8)
        ttk.Button(btns, text=f"Apply CH{ch} Settings",
                   command=lambda c=ch: self.apply_sg_channel(c)).pack(side=tk.LEFT, padx=5)
        ttk.Button(btns, text="Read Instrument",
                   command=lambda c=ch: self.sg_read_instrument(c)).pack(side=tk.LEFT, padx=5)
        output_var = tk.BooleanVar(value=False)
        out_btn = tk.Button(btns, text="Output: OFF", width=12,
                            command=lambda c=ch: self.sg_toggle_output(c))
        out_btn.pack(side=tk.LEFT, padx=15)
        widgets['output'] = output_var
        widgets['out_btn'] = out_btn
        widgets['out_btn_bg'] = out_btn.cget('bg')

        # Burst & sync (issue #45): N-cycle shots are the HV-safe way to
        # drive the Trek amp -- bounded energy per trigger. All short
        # commands, USB-safe; pushed by Apply like everything else.
        burst = ttk.Frame(parent)
        burst.pack(fill='x', padx=10, pady=(0, 6))
        widgets['burst_on'] = tk.BooleanVar(value=False)
        add_tooltip(ttk.Checkbutton(burst, text="Burst",
                                    variable=widgets['burst_on']),
                    "Emit exactly N cycles per trigger, then idle -- "
                    "bounded energy per shot, the HV-safe way to drive "
                    "the Trek amplifier. Pushed on Apply.").pack(
            side=tk.LEFT)
        ttk.Label(burst, text="Cycles:").pack(side=tk.LEFT, padx=(8, 2))
        w = ttk.Entry(burst, width=6)
        w.insert(0, '1')
        w.pack(side=tk.LEFT)
        add_tooltip(w, "Cycles emitted per burst trigger (N >= 1).")
        widgets['burst_ncyc'] = w
        ttk.Label(burst, text="Trigger:").pack(side=tk.LEFT, padx=(8, 2))
        cb = ttk.Combobox(burst, width=5, state='readonly',
                          values=['MAN', 'INT', 'EXT'])
        cb.set('MAN')
        cb.pack(side=tk.LEFT)
        add_tooltip(cb, "Burst trigger source: MAN = the Fire button (or "
                        "front panel), INT = auto-repeat every Interval "
                        "seconds, EXT = edge on the rear Aux In.")
        widgets['burst_trsr'] = cb
        ttk.Label(burst, text="Interval (s):").pack(side=tk.LEFT, padx=(8, 2))
        w = ttk.Entry(burst, width=7)
        w.insert(0, '1.0')
        w.pack(side=tk.LEFT)
        add_tooltip(w, "Burst repeat interval in seconds (INT trigger "
                       "only).")
        widgets['burst_prd'] = w
        add_tooltip(ttk.Button(burst, text="Fire", width=5,
                               command=lambda c=ch: self.sg_fire_burst(c)),
                    "Send one manual burst trigger now (needs Burst ON, "
                    "trigger MAN, output ON).").pack(side=tk.LEFT, padx=8)
        widgets['sync_on'] = tk.BooleanVar(value=False)
        add_tooltip(ttk.Checkbutton(burst, text="Sync out",
                                    variable=widgets['sync_on']),
                    "Hardware trigger edge on the rear Sync BNC every "
                    "waveform period -- feed it to the scope's Aux In "
                    "for rock-solid triggering on slow arbs.").pack(
            side=tk.LEFT, padx=(12, 0))
        applied_burst = tk.Label(burst, text='--', fg='gray', anchor='w')
        applied_burst.pack(side=tk.LEFT, padx=8)
        widgets['applied']['burst'] = applied_burst

        self.sg_channel_widgets[ch] = widgets
        self._sg_update_visibility(ch)
        self._sg_redraw_preview(ch)

    def _sg_waveform_changed(self, ch):
        self._sg_update_visibility(ch)
        self._sg_redraw_preview(ch)

    def _sg_advanced_toggled(self):
        for ch in (1, 2):
            self._sg_update_visibility(ch)

    def _sg_update_visibility(self, ch):
        """Show only the fields relevant to the selected waveform and mode."""
        widgets = self.sg_channel_widgets[ch]
        wave = widgets['waveform'].get()
        advanced = self.sg_advanced.get()
        for key in SG_FIELD_ORDER:
            widgets['rows'][key].pack_forget()
        for key in SG_FIELD_ORDER:
            waves = SG_FIELD_WAVEFORMS[key]
            if waves is not None and wave not in waves:
                continue
            if key in SG_ADVANCED_FIELDS and not advanced:
                continue
            widgets['rows'][key].pack(fill='x', pady=2)

    def _sg_get_float(self, ch, key, default):
        """Float value of a channel entry, or default if unparsable."""
        try:
            return float(self.sg_channel_widgets[ch][key].get())
        except (TypeError, ValueError):
            return default

    def _sg_redraw_preview(self, ch):
        """Redraw the waveform preview canvas from the current inputs."""
        widgets = self.sg_channel_widgets[ch]
        canvas = widgets['canvas']
        wave = widgets['waveform'].get()
        freq = self._sg_get_float(ch, 'freq', 1000.0)
        amp = self._sg_get_float(ch, 'amp', 1.0)
        offset = self._sg_get_float(ch, 'offset', 0.0)
        duty = self._sg_get_float(ch, 'duty', 50.0)
        sym = self._sg_get_float(ch, 'sym', 50.0)
        phase = self._sg_get_float(ch, 'phase', 0.0)
        rise = self._sg_get_float(ch, 'rise', 0.0)
        fall = self._sg_get_float(ch, 'fall', 0.0)

        rise_frac = rise * freq if freq > 0 else 0.0
        fall_frac = fall * freq if freq > 0 else 0.0
        unit = unit_waveform(wave, n_periods=3, points_per_period=120,
                             duty_pct=duty, sym_pct=sym,
                             rise_frac=rise_frac, fall_frac=fall_frac,
                             phase_deg=phase,
                             samples=widgets.get('arb_samples'))
        volts = scale_waveform(unit, amp if wave != 'DC' else 0.0, offset)

        canvas.delete('all')
        w = int(canvas['width'])
        h = int(canvas['height'])
        pad = 8
        vmax = max(max(abs(v) for v in volts), 1e-9) * 1.15

        def ty(v):
            return h / 2.0 - (v / vmax) * (h / 2.0 - pad)

        canvas.create_line(0, ty(0), w, ty(0), fill='#bbb', dash=(3, 3))
        pts = []
        n = len(volts)
        for i, v in enumerate(volts):
            x = pad + i * (w - 2 * pad) / (n - 1)
            pts.extend((x, ty(v)))
        canvas.create_line(*pts, fill='#1565c0', width=2)

        if wave == 'DC':
            caption = f"DC  {offset:+g} V"
        elif wave == 'NOISE':
            caption = "NOISE (preview approximation)"
        else:
            caption = f"{wave}  {freq:g} Hz  {amp:g} Vpp  {offset:+g} V"
        canvas.create_text(6, h - 6, anchor='sw', text=caption,
                           fill='#666', font=('Arial', 8))

    @staticmethod
    def _set_entry(entry, value):
        """Replace the contents of an Entry, skipping None values."""
        if value is None:
            return
        entry.delete(0, tk.END)
        entry.insert(0, str(value))

    def _sg_load_to_wire(self, ch):
        """UI load value -> SCPI token ('HZ' or ohms). Raises on bad input."""
        ui = self.sg_channel_widgets[ch]['load'].get().strip()
        if ui.upper() in (SG_LOAD_HIGHZ.upper(), 'HZ', 'HIZ', 'HIGHZ'):
            return 'HZ'
        try:
            ohms = float(ui)
        except ValueError:
            raise ValueError(f"Load must be {SG_LOAD_HIGHZ} or a resistance "
                             f"in ohms, got {ui!r}")
        if ohms <= 0:
            raise ValueError("Load resistance must be positive")
        return f'{ohms:g}'

    def reconnect_sg(self):
        self._reconnect('sg', BK4055B, self.sg_status,
                        sync=self.update_sg_config)

    # The SG read paths are split into a VISA half (fetch -- worker thread)
    # and a widget half (populate/show -- main thread) so nothing blocks
    # the UI (issue #40).

    def _sg_fetch_channel(self, ch, bswv=None, outp=None):
        """VISA half: everything about one channel, from a worker thread."""
        data = {'bswv': bswv or self.sg.get_basic_wave_dict(ch),
                'outp': outp or self.sg.get_output_dict(ch),
                'burst': None, 'sync': None, 'arb': None}
        try:   # burst/sync best-effort on older firmware (issue #45)
            data['burst'] = self.sg.get_burst_dict(ch)
            data['sync'] = self.sg.get_sync(ch)
        except Exception:
            pass
        if data['bswv'].get('WVTP') == 'ARB':
            try:
                data['arb'] = self.sg.get_arb_dict(ch)['name']
            except Exception:
                pass
        return data

    def _sg_show_applied(self, ch, data):
        """Widget half of the applied readouts -- main thread only."""
        bswv, outp = data['bswv'], data['outp']
        widgets = self.sg_channel_widgets[ch]
        applied = widgets['applied']
        applied['waveform'].config(text=str(bswv.get('WVTP', '--')))
        for key, bk in SG_BSWV_KEYS.items():
            applied[key].config(text=str(bswv[bk]) if bk in bswv else '--')
        applied['arb'].config(text=data['arb'] or '--')
        applied['load'].config(text=SG_LOAD_HIGHZ if outp['load'] == 'HZ'
                               else outp['load'])
        applied['polarity'].config(text=outp['polarity'])
        widgets['output'].set(outp['state'])
        self._sg_set_output_button(ch, outp['state'])
        b = data['burst']
        if b is None:
            applied['burst'].config(text='--')
        else:
            if b.get('STATE', 'OFF').upper() == 'ON':
                ncyc = b.get('TIME')
                desc = (f"Burst ON: {ncyc:g} cyc" if isinstance(ncyc, float)
                        else "Burst ON")
                desc += f", {b.get('TRSR', '?')}"
            else:
                desc = "Burst off"
            if data['sync']:
                desc += " | Sync ON"
            applied['burst'].config(text=desc)

    def _sg_populate_inputs(self, ch, data):
        """Write fetched state into the INPUT widgets, then the readouts."""
        bswv, outp = data['bswv'], data['outp']
        widgets = self.sg_channel_widgets[ch]
        if bswv.get('WVTP') in BK4055B.WAVEFORMS:
            widgets['waveform'].set(bswv['WVTP'])
        for key, bk in SG_BSWV_KEYS.items():
            if bk in bswv:
                self._set_entry(widgets[key], bswv[bk])
        widgets['load'].set(SG_LOAD_HIGHZ if outp['load'] == 'HZ'
                            else outp['load'])
        if outp['polarity'] in widgets['polarity']['values']:
            widgets['polarity'].set(outp['polarity'])
        b = data['burst']
        if b is not None:
            widgets['burst_on'].set(b.get('STATE', 'OFF').upper() == 'ON')
            if isinstance(b.get('TIME'), float):
                self._set_entry(widgets['burst_ncyc'], int(b['TIME']))
            if b.get('TRSR') in ('MAN', 'INT', 'EXT'):
                widgets['burst_trsr'].set(b['TRSR'])
            if isinstance(b.get('PRD'), float):
                self._set_entry(widgets['burst_prd'], b['PRD'])
            widgets['sync_on'].set(bool(data['sync']))
        self._sg_update_visibility(ch)
        self._sg_redraw_preview(ch)
        self._sg_show_applied(ch, data)

    def update_sg_config(self):
        """Read both channels from the generator in the background."""
        if not self.sg:
            return

        def work():
            return {ch: self._sg_fetch_channel(ch) for ch in (1, 2)}

        def done(data, error):
            if error:
                self.status_bar.config(
                    text=f"Error reading SG config: {error}")
                return
            for ch, d in data.items():
                try:
                    self._sg_populate_inputs(ch, d)
                except Exception as e:
                    self.status_bar.config(
                        text=f"Error reading CH{ch} config: {e}")

        self._run_bg(work, done, busy='sg-io', quiet=True)

    def sg_read_instrument(self, ch):
        """Read Instrument button: sync the GUI to the box's actual state
        (use after changing settings on the front panel)."""
        if not self.sg:
            messagebox.showerror("Error", "Signal generator not connected")
            return

        def done(data, error):
            if error:
                messagebox.showerror("Read Error", str(error))
                return
            self._sg_populate_inputs(ch, data)
            self.status_bar.config(text=f"CH{ch} read from instrument")

        self._run_bg(lambda: self._sg_fetch_channel(ch), done, busy='sg-io')

    def _sg_refresh_applied(self, ch, bswv=None, outp=None):
        """Synchronous fetch+show; kept for callers already off the main
        thread's hot path (arb editor's LAN upload)."""
        if not self.sg:
            return
        self._sg_show_applied(ch, self._sg_fetch_channel(ch, bswv, outp))

    def _sg_set_output_button(self, ch, on):
        widgets = self.sg_channel_widgets[ch]
        if on:
            widgets['out_btn'].config(text="Output: ON", bg='#2e7d32',
                                      fg='white', activebackground='#1b5e20',
                                      activeforeground='white')
        else:
            widgets['out_btn'].config(text="Output: OFF",
                                      bg=widgets['out_btn_bg'], fg='black',
                                      activebackground=widgets['out_btn_bg'],
                                      activeforeground='black')

    def apply_sg_channel(self, channel, _then=None):
        """Push the channel CONFIGURATION (waveform parameters + load/polarity)
        to the instrument. Does NOT touch the output on/off state.

        Widget reads + validation happen here on the main thread; the VISA
        pushes and the read-back run in a worker (issue #40). `_then` is an
        optional main-thread callback fired when this apply finishes (used
        by preset loads to chain CH2 behind CH1 under the shared busy key).
        """
        if not self.sg:
            messagebox.showerror("Error", "Signal generator not connected")
            if _then:
                _then()
            return
        widgets = self.sg_channel_widgets[channel]
        wave = widgets['waveform'].get()
        try:
            params = {'WVTP': wave}
            for key, bk in SG_BSWV_KEYS.items():
                waves = SG_FIELD_WAVEFORMS[key]
                if waves is not None and wave not in waves:
                    continue
                value = float(widgets[key].get())
                if key in ('duty', 'sym') and not (0.0 <= value <= 100.0):
                    raise ValueError(f"{SG_FIELD_LABELS[key][:-1]} must be "
                                     f"between 0 and 100, got {value:g}")
                params[bk] = value
            burst_period = float(widgets['burst_prd'].get() or 0)
            burst_ncyc = int(float(widgets['burst_ncyc'].get() or 1))
        except Exception as e:
            messagebox.showerror("Configuration Error", str(e))
            if _then:
                _then()
            return
        arb = (widgets['arb_name_var'].get().strip()
               if wave == 'ARB' and SG_ARB_ENABLED else '')
        load = self._sg_load_to_wire(channel)
        polarity = widgets['polarity'].get()
        burst_on = widgets['burst_on'].get()
        burst_trsr = widgets['burst_trsr'].get() or 'MAN'
        sync_on = widgets['sync_on'].get()

        def work():
            self.sg.set_basic_wave(channel, **params)
            if arb:
                # Select by name; the waveform must already be in the
                # instrument (recalled from a flash-drive .bin on the
                # front USB port, or uploaded over LAN)
                self.sg.select_arb(channel, arb)
            self.sg.set_load_polarity(channel, load=load, polarity=polarity)
            # Burst + sync (issue #45)
            self.sg.set_burst(channel, burst_on, ncycles=burst_ncyc,
                              trigger=burst_trsr,
                              period_s=burst_period if burst_period > 0
                              else None)
            self.sg.set_sync(channel, sync_on)
            # Read-back is best-effort: the configuration above already
            # landed, so a slow query must not be reported as a config error.
            time.sleep(0.2)
            try:
                return self._sg_fetch_channel(channel)
            except Exception:
                return None

        def done(data, error):
            try:
                if error:
                    messagebox.showerror("Configuration Error", str(error))
                    return
                if data is not None:
                    self._sg_show_applied(channel, data)
                    self.status_bar.config(
                        text=f"CH{channel} configured: {wave} (output "
                             f"{'ON' if widgets['output'].get() else 'OFF'}"
                             " - use the Output button to switch)")
                else:
                    self.status_bar.config(
                        text=f"CH{channel} configured, but read-back failed"
                             " - use Read Instrument to refresh")
            finally:
                if _then:
                    _then()

        if not self._run_bg(work, done, busy='sg-io') and _then:
            _then()

    def sg_fire_burst(self, channel):
        """Fire one manual burst (needs Burst ON, trigger MAN, output ON)."""
        if not self.sg:
            messagebox.showerror("Error", "Signal generator not connected")
            return
        self._bg_simple(lambda: self.sg.burst_trigger(channel),
                        f"CH{channel}: burst fired", busy='sg-io',
                        err_title="Burst")

    def sg_toggle_output(self, channel):
        """Flip the channel output on/off (separate from Apply)."""
        if not self.sg:
            messagebox.showerror("Error", "Signal generator not connected")
            return
        widgets = self.sg_channel_widgets[channel]
        new_state = not widgets['output'].get()

        def done(_result, error):
            if error:
                messagebox.showerror("Error", str(error))
                return
            widgets['output'].set(new_state)
            self._sg_set_output_button(channel, new_state)
            self.status_bar.config(
                text=f"CH{channel} output {'ON' if new_state else 'OFF'}")

        self._run_bg(lambda: self.sg.set_output(channel, new_state), done,
                     busy='sg-io')

    def _sg_collect_state(self):
        """Read both channels' widgets into a {1|2 -> ChannelState} mapping."""
        channels = {}
        for ch in (1, 2):
            widgets = self.sg_channel_widgets[ch]
            wave = widgets['waveform'].get()
            state = {
                'waveform': wave,
                'freq_hz': self._sg_get_float(ch, 'freq', 1000.0),
                'amp_vpp': self._sg_get_float(ch, 'amp', 1.0),
                'offset_v': self._sg_get_float(ch, 'offset', 0.0),
                'output': widgets['output'].get(),
                'load': self._sg_load_to_wire(ch),
                'polarity': widgets['polarity'].get(),
            }
            for key, skey in SG_STATE_KEYS.items():
                if skey in ('freq_hz', 'amp_vpp', 'offset_v'):
                    continue  # already collected above
                waves = SG_FIELD_WAVEFORMS[key]
                if waves is None or wave in waves:
                    state[skey] = self._sg_get_float(
                        ch, key, float(SG_FIELD_DEFAULTS[key]))
            if wave == 'ARB':
                arb = widgets['arb_name_var'].get().strip()
                if arb:
                    state['arb_name'] = arb
            # Burst/sync (issue #45)
            state['burst_on'] = widgets['burst_on'].get()
            state['burst_ncycles'] = int(self._sg_get_float(
                ch, 'burst_ncyc', 1))
            state['burst_trigger'] = widgets['burst_trsr'].get()
            state['burst_period_s'] = self._sg_get_float(ch, 'burst_prd', 1.0)
            state['sync_on'] = widgets['sync_on'].get()
            channels[ch] = state
        return channels

    def _sg_apply_state(self, channels):
        """Write a {1|2 -> ChannelState} mapping into the widgets and, if
        connected, push the CONFIGURATION (not the output state)."""
        for ch in (1, 2):
            state = channels.get(str(ch)) or channels.get(ch)
            if not state:
                continue
            widgets = self.sg_channel_widgets[ch]
            if state.get('waveform') in BK4055B.WAVEFORMS:
                widgets['waveform'].set(state['waveform'])
            for key, skey in SG_STATE_KEYS.items():
                if skey in state:
                    self._set_entry(widgets[key], state[skey])
            if state.get('load'):
                widgets['load'].set(SG_LOAD_HIGHZ if state['load'] == 'HZ'
                                    else state['load'])
            if state.get('polarity') in widgets['polarity']['values']:
                widgets['polarity'].set(state['polarity'])
            if 'burst_on' in state:
                widgets['burst_on'].set(bool(state['burst_on']))
            if 'burst_ncycles' in state:
                self._set_entry(widgets['burst_ncyc'],
                                int(state['burst_ncycles']))
            if state.get('burst_trigger') in ('MAN', 'INT', 'EXT'):
                widgets['burst_trsr'].set(state['burst_trigger'])
            if 'burst_period_s' in state:
                self._set_entry(widgets['burst_prd'], state['burst_period_s'])
            if 'sync_on' in state:
                widgets['sync_on'].set(bool(state['sync_on']))
            if state.get('arb_name'):
                widgets['arb_name_var'].set(state['arb_name'])
                # Stage library samples for the preview (best-effort); the
                # instrument-side select happens in apply_sg_channel and
                # assumes the arb is already in instrument memory
                try:
                    widgets['arb_samples'] = self.sg_presets.load_arb(
                        state['arb_name'])
                except Exception:
                    widgets['arb_samples'] = None
            self._sg_update_visibility(ch)
            self._sg_redraw_preview(ch)
        # The applies run in the background under one shared busy key, so
        # firing both at once would drop CH2 -- chain it behind CH1 instead.
        if self.sg:
            to_apply = [ch for ch in (1, 2)
                        if channels.get(str(ch)) or channels.get(ch)]

            def chain(idx=0):
                if idx < len(to_apply):
                    self.apply_sg_channel(to_apply[idx],
                                          _then=lambda: chain(idx + 1))
            chain()

    def sg_open_arb_dialog(self, ch):
        """Open the interactive arbitrary-waveform editor for a channel."""
        if not SG_ARB_ENABLED:
            messagebox.showinfo(
                "Arbitrary waveform unavailable",
                "Arbitrary-waveform creation/upload is temporarily disabled "
                "while the BK 4055B USB upload path is being fixed (issue #20).\n\n"
                "The standard waveforms (sine, square, ramp, pulse, noise, DC) "
                "all work normally.")
            return
        ArbWaveformEditor(self, ch)

    def sg_refresh_presets(self):
        """Reload the preset list into the combobox."""
        names = self.sg_presets.names()
        self.sg_preset_select['values'] = names
        if names and self.sg_preset_select.get() not in names:
            self.sg_preset_select.set(names[0])
        elif not names:
            self.sg_preset_select.set('')

    def sg_save_preset(self):
        """Save both channels' current settings under the entered name."""
        name = self.sg_preset_name.get().strip()
        if not name:
            messagebox.showerror("Error", "Enter a preset name first")
            return
        try:
            self.sg_presets.save(name, self._sg_collect_state())
            self.sg_refresh_presets()
            self.sg_preset_select.set(name)
            self.status_bar.config(
                text=self._preset_note(f"Preset saved: {name}"))
        except Exception as e:
            messagebox.showerror("Save Error", str(e))

    def sg_load_preset(self):
        """Load the selected preset into the widgets and push to the box."""
        name = self.sg_preset_select.get()
        if not name:
            messagebox.showerror("Error", "Select a preset to load")
            return
        try:
            record = self.sg_presets.get(name)
            self._sg_apply_state(record['channels'])
            self.status_bar.config(text=f"Preset loaded: {name}")
        except Exception as e:
            messagebox.showerror("Load Error", str(e))

    def sg_delete_preset(self):
        """Delete the selected preset."""
        name = self.sg_preset_select.get()
        if not name:
            messagebox.showerror("Error", "Select a preset to delete")
            return
        if not messagebox.askyesno("Delete Preset", f"Delete preset '{name}'?"):
            return
        try:
            self.sg_presets.delete(name)
            self.sg_refresh_presets()
            self.status_bar.config(text=f"Preset deleted: {name}")
        except Exception as e:
            messagebox.showerror("Delete Error", str(e))

    def apply_all_scope_config(self):
        """Apply all scope settings at once"""
        if not self.scope:
            messagebox.showerror("Error", "Oscilloscope not connected")
            return
        
        # Collect + validate everything on the main thread first
        try:
            channels = []
            for ch in range(1, 5):
                widgets = self.channel_widgets[ch]
                channels.append((ch, widgets['enable'].get(),
                                 float(widgets['vscale'].get()),
                                 float(widgets['position'].get()),
                                 widgets['coupling'].get()))
            hscale = float(self.scope_hscale.get())
            trig_level = float(self.scope_trig_level.get())
        except (TypeError, ValueError) as e:
            messagebox.showerror("Configuration Error", str(e))
            return
        trig_source = 'CH1'
        for ch in range(1, 5):
            if self.channel_widgets[ch]['trigger'].get():
                trig_source = f'CH{ch}'
                break
        trig_slope = self.scope_trig_slope.get()

        def work():
            for ch, enable, vscale, position, coupling in channels:
                self.scope.set_channel_enable(ch, enable)
                self.scope.set_vertical(ch, scale=vscale, position=position,
                                        coupling=coupling)
            self.scope.set_horizontal(scale=hscale)
            self.scope.set_trigger_edge(source=trig_source, level=trig_level,
                                        slope=trig_slope)

        self._bg_simple(
            work,
            f"All settings applied. Trigger: {trig_source} @ {trig_level}V",
            busy='scope-io', err_title="Configuration Error")

    def scope_single(self):
        if not self.scope:
            messagebox.showerror("Error", "Oscilloscope not connected")
            return
        self._bg_simple(lambda: self.scope.single(),
                        "Single acquisition armed", busy='scope-io')

    def scope_run(self):
        if not self.scope:
            messagebox.showerror("Error", "Oscilloscope not connected")
            return
        self._bg_simple(lambda: self.scope.run(), "Scope running",
                        busy='scope-io')

    def scope_stop(self):
        if not self.scope:
            messagebox.showerror("Error", "Oscilloscope not connected")
            return
        self._bg_simple(lambda: self.scope.stop(), "Scope stopped",
                        busy='scope-io')
    
    def scope_autoset(self):
        if not self.scope:
            messagebox.showerror("Error", "Oscilloscope not connected")
            return
        self.status_bar.config(text="Running AutoSet...")

        def done(_result, error):
            if error:
                messagebox.showerror("Error", str(error))
                self.status_bar.config(text="AutoSet failed")
            else:
                self.status_bar.config(text="AutoSet complete")

        self._run_bg(self.scope.autoset, done, busy='scope-io')
    
    def scope_get_measurements(self, channel):
        """Get measurements for a specific channel"""
        if not self.scope:
            messagebox.showerror("Error", "Oscilloscope not connected")
            return
        
        def done(meas, error):
            if error:
                messagebox.showerror("Measurement Error", str(error))
                return
            # measurement dict key -> label widget key (they differ for
            # freq/pk2pk, which is why those never displayed before)
            label_map = {'freq': 'frequency', 'period': 'period', 'mean': 'mean',
                         'pk2pk': 'pkpk', 'rms': 'rms', 'amplitude': 'amplitude'}
            for key, value in meas.items():
                label_key = label_map.get(key)
                if label_key and label_key in self.scope_meas_labels[channel]:
                    if value is not None:
                        # Format appropriately
                        if key == 'freq':
                            if value < 1e3:
                                text = f"{value:.3f} Hz"
                            elif value < 1e6:
                                text = f"{value/1e3:.3f} kHz"
                            else:
                                text = f"{value/1e6:.3f} MHz"
                        elif key == 'period':
                            if value < 1e-6:
                                text = f"{value*1e9:.3f} ns"
                            elif value < 1e-3:
                                text = f"{value*1e6:.3f} µs"
                            else:
                                text = f"{value*1e3:.3f} ms"
                        else:
                            text = f"{value:.4f} V"
                        
                        self.scope_meas_labels[channel][label_key].config(
                            text=f"{key.capitalize()}: {text}"
                        )
                    else:
                        self.scope_meas_labels[channel][label_key].config(
                            text=f"{key.capitalize()}: No signal"
                        )

            self.status_bar.config(text=f"CH{channel} measurements updated")

        self._run_bg(lambda: self.scope.get_all_measurements(channel), done,
                     busy='scope-io')
    
    def scope_capture_waveform(self, channel):
        """Fetch a channel's sample record off the UI thread, then show it
        in a plot window -- Save CSV lives there (issues #40/#42)."""
        if not self.scope:
            messagebox.showerror("Error", "Oscilloscope not connected")
            return
        self.status_bar.config(text=f"Capturing CH{channel} waveform...")

        def done(waveform, error):
            if error:
                messagebox.showerror("Capture Error", str(error))
                self.status_bar.config(text=f"CH{channel} capture failed")
                return
            self.status_bar.config(
                text=f"CH{channel} waveform captured "
                     f"({waveform['npts']} points)")
            scope_trace.TraceWindow(self, channel, waveform)

        self._run_bg(lambda: self.scope.get_waveform(channel), done,
                     busy='scope-io')
    
    # Logging methods
    def select_log_dir(self):
        directory = filedialog.askdirectory()
        if directory:
            self.log_dir.set(directory)
    
    def start_logging(self):
        import os

        # Validate BEFORE latching the buttons: a bad interval or an empty
        # source list used to kill the worker thread silently while the UI
        # stayed stuck in the "logging" state (issue #39).
        raw = self.log_interval.get()
        try:
            interval = float(raw)
        except (TypeError, ValueError):
            messagebox.showerror(
                "Logging", f"Invalid interval: {raw!r} -- enter seconds "
                "(e.g. 1.0)")
            return
        if interval <= 0:
            messagebox.showerror("Logging",
                                 "Interval must be greater than 0 seconds")
            return

        selected, missing = [], []
        if self.log_lcr.get():
            (selected if self.lcr else missing).append('LCR')
        for ch in range(1, 5):
            if self.log_scope_channels[ch].get():
                (selected if self.scope else missing).append(f'Scope CH{ch}')
        for ch in (1, 2):
            if self.log_sg_channels[ch].get():
                (selected if self.sg else missing).append(f'SigGen CH{ch}')
        if missing:
            self.log_message("Skipping (not connected): " + ", ".join(missing))
        if not selected:
            messagebox.showerror(
                "Logging",
                "Nothing to log: tick at least one instrument that is "
                "connected (check the tab's connection status / Reconnect).")
            return

        log_path = self.log_dir.get()
        try:
            os.makedirs(log_path, exist_ok=True)
        except OSError as e:
            messagebox.showerror("Logging",
                                 f"Cannot create log directory:\n{e}")
            return

        self.recording = True
        self.log_start_btn.config(state='disabled')
        self.log_stop_btn.config(state='normal')

        # Start logging thread (interval passed in -- already validated)
        self.record_thread = threading.Thread(
            target=self._logging_worker, args=(interval,), daemon=True)
        self.record_thread.start()

        self.log_message(f"Logging started ({', '.join(selected)} "
                         f"every {interval:g} s)")

    def _logging_worker(self, interval):
        """Run logging_loop and never die silently: a fatal error reports to
        the log widget and resets the Start/Stop buttons (issue #39)."""
        try:
            self.logging_loop(interval)
        except Exception as e:
            self.log_message(f"Logging stopped by error: {e}")
            self.root.after(0, self._logging_failed)

    def _logging_failed(self):
        if self.recording:
            self.stop_logging()
    
    def stop_logging(self):
        self.recording = False
        self.log_start_btn.config(state='normal')
        self.log_stop_btn.config(state='disabled')
        self.log_message("Logging stopped")
    
    # consecutive per-source errors before that source is dropped from a run
    _LOG_MAX_FAILS = 5

    def logging_loop(self, interval):
        """Background logging thread. `interval` (s) is validated by
        start_logging before this thread launches.

        Every source carries a consecutive-failure counter (issue #46):
        after _LOG_MAX_FAILS misses in a row it is dropped from the run
        with a single notice instead of spamming one error per tick
        forever; when the last source dies, logging stops itself.
        """
        import os

        log_path = self.log_dir.get()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        files, writers, fails = {}, {}, {}

        def _open(key, filename, header):
            path = os.path.join(log_path, filename)
            files[key] = open(path, 'w', newline='')
            writers[key] = csv.writer(files[key])
            writers[key].writerow(header)
            fails[key] = 0
            self.log_message(f"{key} log: {path}")

        if self.log_lcr.get() and self.lcr:
            _open('LCR', f"lcr_{stamp}.csv",
                  ['Timestamp', 'Mode', 'Frequency (Hz)', 'Primary',
                   'Secondary', 'Status'])
        for ch in range(1, 5):
            if self.log_scope_channels[ch].get() and self.scope:
                _open(f'Scope CH{ch}', f"scope_ch{ch}_{stamp}.csv",
                      ['Timestamp', 'Frequency (Hz)', 'Period (s)',
                       'Mean (V)', 'Pk-Pk (V)', 'RMS (V)', 'Amplitude (V)'])
        # Sig-gen STIMULUS channels (issue #46): what was driving the DUT,
        # in the same run as the measured response. Short queries, USB-safe.
        for ch in (1, 2):
            if self.log_sg_channels[ch].get() and self.sg:
                _open(f'SigGen CH{ch}', f"siggen_ch{ch}_{stamp}.csv",
                      ['Timestamp', 'Waveform', 'Frequency (Hz)',
                       'Amplitude (Vpp)', 'Offset (V)', 'Stdev (V)',
                       'Mean (V)', 'Output'])

        def _sample(key, fn):
            """One source sample; drops the source after repeated errors."""
            if key not in writers:
                return
            try:
                fn(writers[key])
                files[key].flush()
                fails[key] = 0
            except Exception as e:
                fails[key] += 1
                if fails[key] >= self._LOG_MAX_FAILS:
                    self.log_message(
                        f"{key}: {fails[key]} consecutive errors ({e}) -- "
                        "source disabled for this run")
                    files.pop(key).close()
                    writers.pop(key)
                else:
                    self.log_message(f"{key} error: {e}")

        def _lcr_row(writer):
            config = self.lcr.get_config()
            primary, secondary, status = self.lcr.measure()
            writer.writerow([now, config['mode'], config['frequency'],
                             primary, secondary, status])

        while self.recording:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            _sample('LCR', _lcr_row)
            for ch in range(1, 5):
                def _scope_row(writer, c=ch):
                    meas = self.scope.get_all_measurements(c)
                    writer.writerow([now, meas.get('freq'),
                                     meas.get('period'), meas.get('mean'),
                                     meas.get('pk2pk'), meas.get('rms'),
                                     meas.get('amplitude')])
                _sample(f'Scope CH{ch}', _scope_row)
            for ch in (1, 2):
                def _sg_row(writer, c=ch):
                    bswv = self.sg.get_basic_wave_dict(c)
                    outp = self.sg.get_output_dict(c)
                    writer.writerow([now, bswv.get('WVTP'), bswv.get('FRQ'),
                                     bswv.get('AMP'), bswv.get('OFST'),
                                     bswv.get('STDEV'), bswv.get('MEAN'),
                                     'ON' if outp['state'] else 'OFF'])
                _sample(f'SigGen CH{ch}', _sg_row)
            if not writers:
                self.log_message("All logging sources failed -- stopping")
                self.root.after(0, self._logging_failed)
                break
            time.sleep(interval)

        # Close whatever survived to the end of the run
        for f in files.values():
            f.close()
    
    def log_message(self, message):
        """Thread-safe logging to text widget"""
        def update():
            self.log_text.config(state='normal')
            self.log_text.insert(tk.END, f"[{datetime.now().strftime('%H:%M:%S')}] {message}\n")
            self.log_text.see(tk.END)
            self.log_text.config(state='disabled')
        
        self.root.after(0, update)

    # ==================== Battery Data tab ====================
    def create_battery_tab(self):
        """Battery-cycler post-processing (issue #31): the lab's standalone
        BatteryProcessing tool embedded as a tab. Degrades to an install
        hint when pandas/matplotlib/openpyxl are missing (webcam pattern).
        Not an instrument -- pure file post-processing, no VISA."""
        _tab = ScrollableTab(self.notebook)
        self.notebook.add(_tab, text="Battery Data")
        import battery_process
        ok, reason = battery_process.deps_available()
        if not ok:
            tk.Label(_tab.body, text=(
                "Battery processing needs pandas, matplotlib and openpyxl.\n\n"
                f"({reason})\n\n"
                "Install on the bench, then relaunch:\n"
                "    .venv/bin/pip install pandas matplotlib openpyxl"),
                justify=tk.LEFT, fg="#8a5a00").pack(padx=20, pady=20,
                                                    anchor="w")
            return
        from battery_tab import BatteryPane
        BatteryPane(_tab.body).pack(fill="both", expand=True)

    # ==================== Webcam tab ====================
    def create_webcam_tab(self):
        """USB webcam: live preview, snapshot, interval + stepped capture."""
        _tab = ScrollableTab(self.notebook)
        self.notebook.add(_tab, text="Webcam")
        tab = _tab.body

        ok, reason = webcam.deps_available()
        if not ok:
            msg = ("Webcam capture needs OpenCV, Pillow and numpy.\n\n"
                   f"({reason})\n\n"
                   "Install on the bench, then relaunch:\n"
                   "    pip install opencv-python-headless Pillow numpy\n"
                   "or use Tools > Update Software (they're in requirements.txt).")
            tk.Label(tab, text=msg, justify=tk.LEFT, padx=20, pady=20,
                     fg='#a00').pack(anchor='w')
            return

        # --- device + preview controls ---
        top = ttk.Frame(tab, padding=8)
        top.pack(fill='x')
        ttk.Label(top, text="Camera:").pack(side=tk.LEFT)
        self.cam_index_var = tk.StringVar()
        self.cam_combo = ttk.Combobox(top, width=8, state='readonly',
                                      textvariable=self.cam_index_var)
        self.cam_combo.pack(side=tk.LEFT, padx=4)
        add_tooltip(self.cam_combo,
                    "Video device number (/dev/videoN). Industrial cameras "
                    "often expose two nodes -- the lower one is the image.")
        add_tooltip(ttk.Button(top, text="Refresh",
                               command=self.cam_refresh_devices),
                    "Rescan for cameras after plugging/unplugging."
                    ).pack(side=tk.LEFT)
        self.cam_preview_btn = ttk.Button(top, text="Start Preview",
                                          command=self.cam_toggle_preview)
        self.cam_preview_btn.pack(side=tk.LEFT, padx=8)
        add_tooltip(self.cam_preview_btn,
                    "Live view of the selected camera (~a few fps for the "
                    "industrial camera).")
        add_tooltip(ttk.Button(top, text="Snapshot",
                               command=self.cam_snapshot),
                    "Save the current preview frame as a timestamped PNG in "
                    "the folder below.").pack(side=tk.LEFT)
        self.cam_focus_var = tk.BooleanVar(value=False)
        add_tooltip(ttk.Checkbutton(top, text="Show focus score",
                                    variable=self.cam_focus_var),
                    "Overlay a live sharpness number on the preview -- "
                    "higher = better focus; peak it while turning the "
                    "lens.").pack(side=tk.LEFT, padx=8)

        # --- sensor controls (industrial cameras) ---
        # The DFK 37BUX250's own auto-exposure never converges over UVC: at
        # its defaults every frame is pure black, which reads as "the camera
        # is broken". These make the exposure reachable.
        sens = ttk.Frame(tab, padding=(8, 0))
        sens.pack(fill='x')
        ttk.Label(sens, text="Exposure:").pack(side=tk.LEFT)
        self.cam_exposure = ttk.Entry(sens, width=8)
        self.cam_exposure.pack(side=tk.LEFT, padx=4)
        add_tooltip(self.cam_exposure,
                    "Exposure time in 100 microsecond units (50 = 5 ms). "
                    "Longer = brighter but slower and more motion blur.")
        ttk.Label(sens, text="Gain:").pack(side=tk.LEFT, padx=(8, 0))
        self.cam_gain = ttk.Entry(sens, width=6)
        self.cam_gain.pack(side=tk.LEFT, padx=4)
        add_tooltip(self.cam_gain,
                    "Sensor gain. Raise it only after exposure runs out - "
                    "gain amplifies noise as well as signal.")
        add_tooltip(ttk.Button(sens, text="Apply",
                               command=self.cam_apply_controls),
                    "Send exposure/gain to the camera (switches it to "
                    "manual exposure).").pack(side=tk.LEFT, padx=4)
        add_tooltip(ttk.Button(sens, text="Auto-expose",
                               command=self.cam_auto_expose),
                    "Try a range of exposures and keep the one that gives a "
                    "mid-grey image. Takes a few seconds.").pack(side=tk.LEFT)
        self.cam_sensor_status = ttk.Label(sens, text="", foreground="gray")
        self.cam_sensor_status.pack(side=tk.LEFT, padx=10)

        # --- preview image ---
        self.cam_view = tk.Label(tab, bg='black', width=80, height=24,
                                 anchor='center',
                                 text="(preview off)", fg='gray')
        self.cam_view.pack(fill='both', expand=True, padx=8, pady=4)

        # --- output folder ---
        out = ttk.Frame(tab, padding=(8, 0))
        out.pack(fill='x')
        ttk.Label(out, text="Save to:").pack(side=tk.LEFT)
        default_dir = os.path.join(os.path.expanduser('~'), 'captures')
        self.cam_dir_var = tk.StringVar(value=default_dir)
        add_tooltip(ttk.Entry(out, textvariable=self.cam_dir_var, width=44),
                    "Folder every snapshot/interval/sweep image is saved "
                    "into.").pack(side=tk.LEFT, padx=4)
        ttk.Button(out, text="Browse", command=self._cam_browse_dir).pack(side=tk.LEFT)
        ttk.Label(out, text="Prefix:").pack(side=tk.LEFT, padx=(10, 0))
        self.cam_prefix_var = tk.StringVar(value='cap')
        add_tooltip(ttk.Entry(out, textvariable=self.cam_prefix_var,
                              width=12),
                    "Start of every saved filename, e.g. cap_0003_1.90V_"
                    "20260720-1030.png").pack(side=tk.LEFT, padx=4)

        # --- interval capture ---
        iv = ttk.LabelFrame(tab, text="Interval capture", padding=8)
        iv.pack(fill='x', padx=8, pady=4)
        ttk.Label(iv, text="Every").pack(side=tk.LEFT)
        self.cam_interval_var = tk.StringVar(value='5')
        add_tooltip(ttk.Entry(iv, textvariable=self.cam_interval_var,
                              width=7),
                    "Seconds between automatic snapshots.").pack(
            side=tk.LEFT, padx=4)
        ttk.Label(iv, text="s,  count (0 = until stopped):").pack(side=tk.LEFT)
        self.cam_count_var = tk.StringVar(value='0')
        add_tooltip(ttk.Entry(iv, textvariable=self.cam_count_var, width=7),
                    "Stop after this many shots; 0 keeps going until you "
                    "press Stop.").pack(side=tk.LEFT, padx=4)
        self.cam_interval_btn = ttk.Button(iv, text="Start interval",
                                           command=self.cam_toggle_interval)
        self.cam_interval_btn.pack(side=tk.LEFT, padx=8)

        # --- stepped (voltage) capture via the signal generator ---
        st = ttk.LabelFrame(
            tab, text="Stepped capture: set CH n to each voltage "
                      "\u2192 wait \u2192 photo", padding=8)
        st.pack(fill='x', padx=8, pady=4)
        ttk.Label(st, text="SG CH").grid(row=0, column=0, sticky='w')
        self.cam_sg_ch = ttk.Combobox(st, width=4, state='readonly', values=['1', '2'])
        self.cam_sg_ch.set('1')
        self.cam_sg_ch.grid(row=0, column=1, padx=2)
        add_tooltip(self.cam_sg_ch,
                    "Signal-generator channel driving the device under the "
                    "camera. Turn its Output ON first (Signal Gen tab).")
        ttk.Label(st, text="param").grid(row=0, column=2, sticky='w', padx=(8, 0))
        self.cam_sg_param = ttk.Combobox(st, width=12, state='readonly',
                                         values=['DC offset (V)', 'Amplitude (Vpp)'])
        self.cam_sg_param.set('DC offset (V)')
        self.cam_sg_param.grid(row=0, column=3, padx=2)
        add_tooltip(self.cam_sg_param,
                    "Which setting each level is written to. DC offset = a "
                    "software staircase of steady voltages (most level-vs-"
                    "image experiments); Amplitude = resize the running "
                    "waveform instead.")
        ttk.Label(st, text="start").grid(row=1, column=0, sticky='w')
        self.cam_step_start = tk.StringVar(value='0')
        add_tooltip(ttk.Entry(st, textvariable=self.cam_step_start, width=7),
                    "First level in volts.").grid(row=1, column=1)
        ttk.Label(st, text="stop").grid(row=1, column=2, sticky='w', padx=(8, 0))
        self.cam_step_stop = tk.StringVar(value='5')
        add_tooltip(ttk.Entry(st, textvariable=self.cam_step_stop, width=7),
                    "Last level in volts (included when it lands on a "
                    "step).").grid(row=1, column=3)
        ttk.Label(st, text="step").grid(row=1, column=4, sticky='w', padx=(8, 0))
        self.cam_step_step = tk.StringVar(value='1')
        add_tooltip(ttk.Entry(st, textvariable=self.cam_step_step, width=7),
                    "Increment between levels, e.g. 0.2 -> 0.2, 0.4, 0.6 V. "
                    "Negative steps sweep downward.").grid(row=1, column=5)
        ttk.Label(st, text="dwell s").grid(row=1, column=6, sticky='w', padx=(8, 0))
        self.cam_step_dwell = tk.StringVar(value='1.0')
        add_tooltip(ttk.Entry(st, textvariable=self.cam_step_dwell, width=7),
                    "Settle time in seconds between setting a level and "
                    "taking the photo (0.25 = quarter second).").grid(
            row=1, column=7)
        ttk.Label(st, text="levels").grid(row=2, column=0, sticky='w')
        self.cam_step_levels = tk.StringVar(value='')
        add_tooltip(ttk.Entry(st, textvariable=self.cam_step_levels, width=30),
                    "Optional explicit voltage list, e.g. 0.2, 0.4, 0.9, 1.9 "
                    "-- overrides start/stop/step when filled. Use it for "
                    "uneven spacing or a handful of specific levels.").grid(
            row=2, column=1, columnspan=5, sticky='w', padx=2, pady=(4, 0))
        ttk.Label(st, text="(overrides start/stop/step)",
                  foreground='gray').grid(row=2, column=6, columnspan=2,
                                          sticky='w', pady=(4, 0))
        self.cam_seq_focus = tk.BooleanVar(value=True)
        add_tooltip(ttk.Checkbutton(st, text="log focus CSV",
                                    variable=self.cam_seq_focus),
                    "Also write <prefix>_focus.csv: one row per level with "
                    "the image file and its sharpness score.").grid(
            row=0, column=4, columnspan=2, sticky='w', padx=(8, 0))
        self.cam_seq_btn = ttk.Button(st, text="Run sweep", command=self.cam_toggle_sequence)
        self.cam_seq_btn.grid(row=0, column=6, columnspan=2, padx=8)
        add_tooltip(self.cam_seq_btn,
                    "For each level: write it to the chosen channel, wait "
                    "the dwell, save a photo (filename carries the level). "
                    "Click again to stop.")
        self.cam_seq_status = ttk.Label(st, text="", foreground='gray')
        self.cam_seq_status.grid(row=3, column=0, columnspan=8, sticky='w', pady=(4, 0))

        # --- waveform-synced timed capture (Approach A) ---
        # Photograph at chosen DELAYS after a t=0 trigger, while the real
        # waveform runs -- for a slow ramp/hold arb where you want the DUT
        # imaged at specific points of the cycle. The delay IS the phase
        # knob (no waveform math, so nothing to bog down the GUI).
        tm = ttk.LabelFrame(
            tab, text="Waveform-synced capture: start \u2192 photo at each "
                      "delay (for slow ramp/hold runs)", padding=8)
        tm.pack(fill='x', padx=8, pady=4)
        ttk.Label(tm, text="t=0").grid(row=0, column=0, sticky='w')
        self.cam_tm_trigger = ttk.Combobox(
            tm, width=22, state='readonly',
            values=['on Start click', 'CH1 burst trigger',
                    'CH2 burst trigger'])
        self.cam_tm_trigger.set('on Start click')
        self.cam_tm_trigger.grid(row=0, column=1, columnspan=3, sticky='w',
                                 padx=2)
        add_tooltip(self.cam_tm_trigger,
                    "How the clock starts. 'on Start click' = the timer "
                    "begins when you press Start -- start your waveform at "
                    "the same moment. 'CHn burst trigger' = the GUI fires "
                    "that channel's manual burst to define t=0 precisely "
                    "(arm the channel in Burst / MAN first).")
        ttk.Label(tm, text="delays (s)").grid(row=1, column=0, sticky='w')
        self.cam_tm_delays = tk.StringVar(value='')
        add_tooltip(ttk.Entry(tm, textvariable=self.cam_tm_delays, width=34),
                    "Explicit list of seconds after t=0 to shoot, e.g. "
                    "150, 300, 450, 600. Overrides the regular schedule "
                    "below. Great for a ramp/hold run: one delay per hold."
                    ).grid(row=1, column=1, columnspan=5, sticky='w', padx=2)
        ttk.Label(tm, text="or every").grid(row=2, column=0, sticky='w')
        self.cam_tm_interval = tk.StringVar(value='60')
        add_tooltip(ttk.Entry(tm, textvariable=self.cam_tm_interval, width=7),
                    "Seconds between shots for the regular schedule.").grid(
            row=2, column=1, sticky='w')
        ttk.Label(tm, text="s,").grid(row=2, column=2, sticky='w')
        self.cam_tm_count = tk.StringVar(value='10')
        add_tooltip(ttk.Entry(tm, textvariable=self.cam_tm_count, width=6),
                    "How many shots in the regular schedule.").grid(
            row=2, column=3, sticky='w')
        ttk.Label(tm, text="shots, first at").grid(row=2, column=4, sticky='w')
        self.cam_tm_start = tk.StringVar(value='0')
        add_tooltip(ttk.Entry(tm, textvariable=self.cam_tm_start, width=6),
                    "Delay of the first shot (s).").grid(row=2, column=5,
                                                         sticky='w')
        ttk.Label(tm, text="s").grid(row=2, column=6, sticky='w')
        self.cam_tm_focus = tk.BooleanVar(value=True)
        add_tooltip(ttk.Checkbutton(tm, text="log focus CSV",
                                    variable=self.cam_tm_focus),
                    "Also write <prefix>_timed_focus.csv: one row per shot "
                    "with the delay, file and sharpness score.").grid(
            row=0, column=4, columnspan=2, sticky='w', padx=(8, 0))
        self.cam_tm_btn = ttk.Button(tm, text="Start timed capture",
                                     command=self.cam_toggle_timed)
        self.cam_tm_btn.grid(row=0, column=6, columnspan=2, padx=8)
        add_tooltip(self.cam_tm_btn,
                    "Begin: (optionally fire the burst trigger,) then save a "
                    "photo at each delay. Filenames carry the delay, e.g. "
                    "cap_0002_..._300s.png. Click again to stop.")
        tk.Label(tm, fg='gray', justify=tk.LEFT, wraplength=560,
                 text="The ~5 ms exposure is tiny next to a slow waveform, so "
                      "each shot effectively freezes the instant at its "
                      "delay. Alignment is as good as the trigger (a few tens "
                      "of ms) -- fine for multi-second holds.").grid(
            row=3, column=0, columnspan=8, sticky='w', pady=(4, 0))
        self.cam_tm_status = ttk.Label(tm, text="", foreground='gray')
        self.cam_tm_status.grid(row=4, column=0, columnspan=8, sticky='w',
                                pady=(2, 0))

        self.cam_refresh_devices()

    def _cam_browse_dir(self):
        d = filedialog.askdirectory(initialdir=self.cam_dir_var.get() or '.')
        if d:
            self.cam_dir_var.set(d)

    def cam_refresh_devices(self):
        idxs = webcam.list_cameras()
        vals = [str(i) for i in idxs]
        self.cam_combo['values'] = vals
        if vals and not self.cam_index_var.get():
            self.cam_index_var.set(vals[0])
        self.cam_sync_controls()
        # status_bar may not exist yet during initial tab construction.
        if hasattr(self, 'status_bar'):
            self.status_bar.config(
                text=f"Found {len(vals)} camera(s)" if vals else "No cameras found")

    def _cam_open_selected(self):
        """Open (or reopen) the camera at the selected index. Returns the Camera."""
        idx_str = self.cam_index_var.get()
        if not idx_str:
            raise RuntimeError("no camera selected (click Refresh)")
        idx = int(idx_str)
        if self.cam is not None and getattr(self.cam, 'index', None) == idx \
                and self.cam.is_open:
            return self.cam
        if self.cam is not None:
            self.cam.close()
        self.cam = webcam.Camera(idx).open()
        return self.cam

    def _cam_device(self):
        """/dev/videoN for the selected camera, or None."""
        idx = self.cam_index_var.get().strip()
        if not idx.isdigit():
            return None
        device = f"/dev/video{idx}"
        return device if os.path.exists(device) else None

    def cam_sync_controls(self):
        """Read exposure/gain off the camera into the entry boxes."""
        device = self._cam_device()
        if not device or not webcam.v4l2_available():
            return
        exp = webcam.get_control(device, 'exposure_time_absolute')
        gain = webcam.get_control(device, 'gain')
        if exp is not None:
            self._set_entry(self.cam_exposure, exp)
        if gain is not None:
            self._set_entry(self.cam_gain, gain)

    def cam_apply_controls(self):
        device = self._cam_device()
        if not device:
            messagebox.showerror("Camera", "Select a camera first.")
            return
        if not webcam.v4l2_available():
            messagebox.showerror(
                "Camera", "v4l2-ctl is not installed, so sensor controls "
                          "are unavailable.\n\nInstall with: "
                          "sudo dnf install v4l-utils")
            return
        try:
            exposure = int(float(self.cam_exposure.get()))
            gain = int(float(self.cam_gain.get()))
        except (TypeError, ValueError) as e:
            messagebox.showerror("Camera", f"Exposure/gain must be numbers: {e}")
            return

        def work():
            webcam.set_manual_exposure(device, exposure=exposure, gain=gain)
            return (webcam.get_control(device, 'exposure_time_absolute'),
                    webcam.get_control(device, 'gain'))

        def done(values, error):
            if error:
                messagebox.showerror("Camera", str(error))
                return
            exp, g = values
            self.cam_sensor_status.config(
                text=f"applied: exposure {exp}, gain {g}")
            self.status_bar.config(text=f"Camera: exposure {exp}, gain {g}")

        self._run_bg(work, done, busy='camera-ctrl')

    def cam_auto_expose(self):
        """Search for a usable exposure (the camera's own AE stays black)."""
        device = self._cam_device()
        if not device or not webcam.v4l2_available():
            messagebox.showerror("Camera", "No camera / v4l2-ctl available.")
            return
        fourcc = webcam.bayer_format(device)
        if not fourcc:
            messagebox.showinfo(
                "Auto-expose",
                "This camera is not a raw-Bayer device, so it is driven by "
                "OpenCV and exposes its own automatic exposure.")
            return
        was_previewing = self.cam_previewing
        # The search must OWN the device. Stopping the preview tick is not
        # enough: the streaming backend keeps /dev/videoN busy, every probe
        # grab then fails with EBUSY, and the search reported "found
        # nothing" while the preview made it look like it was still looking
        # (user report 2026-07-20).
        self.cam_stop_preview()
        if self.cam is not None:
            try:
                self.cam.close()
            except Exception:
                pass
            self.cam = None
        self.cam_sensor_status.config(text="auto exposure is checking...",
                                      foreground='#b36b00')

        def work():
            size = webcam.choose_size(webcam.parse_frame_sizes(
                webcam._v4l2('--list-formats-ext', device=device), fourcc))
            w, h = size or (640, 480)
            return webcam.auto_exposure(device, fourcc, w, h)

        def done(result, error):
            try:
                if error:
                    self.cam_sensor_status.config(text="auto-expose failed",
                                                  foreground='red')
                    messagebox.showerror("Auto-expose", str(error))
                    return
                exp, mean = result
                if exp is None:
                    self.cam_sensor_status.config(
                        text="auto-expose could not settle -- check lens cap"
                             " / lighting, or set exposure by hand",
                        foreground='red')
                else:
                    self._set_entry(self.cam_exposure, exp)
                    self.cam_sync_controls()
                    self.cam_sensor_status.config(
                        text=f"exposure {exp} (mean level {mean:.0f})",
                        foreground='gray')
            finally:
                if was_previewing:
                    self.cam_start_preview()

        self._run_bg(work, done, busy='camera-ctrl')

    def cam_toggle_preview(self):
        if self.cam_previewing:
            self.cam_stop_preview()
        else:
            self.cam_start_preview()

    def cam_start_preview(self):
        try:
            self._cam_open_selected()
        except Exception as e:
            messagebox.showerror("Webcam", f"Could not open camera:\n{e}")
            return
        self.cam_previewing = True
        self.cam_preview_btn.config(text="Stop Preview")
        self._cam_preview_tick()

    def cam_stop_preview(self):
        self.cam_previewing = False
        if self.cam_preview_job is not None:
            try:
                self.root.after_cancel(self.cam_preview_job)
            except Exception:
                pass
            self.cam_preview_job = None
        if hasattr(self, 'cam_preview_btn'):
            self.cam_preview_btn.config(text="Start Preview")

    def _cam_preview_tick(self):
        if not self.cam_previewing or self.cam is None:
            return
        frame = self.cam.read_rgb()
        if frame is not None:
            self.cam_last_frame = frame
            self._cam_show(frame)
        # ~20 fps; light enough for a lab tool
        self.cam_preview_job = self.root.after(50, self._cam_preview_tick)

    def _cam_show(self, rgb):
        """Render an RGB numpy frame into the preview label (with optional
        focus-score overlay)."""
        from PIL import Image, ImageTk
        img = Image.fromarray(rgb)
        # Fit the width of the view, but use a FIXED height budget: the
        # label sizes itself to whatever image we put in it, so deriving the
        # height from the label collapses the preview to a thin strip once
        # the tab became scrollable (the canvas grants no spare height).
        maxw = max(self.cam_view.winfo_width() - 4, 320)
        img.thumbnail((maxw, PREVIEW_MAX_HEIGHT))
        photo = ImageTk.PhotoImage(img)
        text = ''
        if self.cam_focus_var.get():
            try:
                text = f"focus {webcam.focus_score(rgb):.0f}"
            except Exception:
                text = ''
        # width/height on a Label are TEXT units while it shows text but
        # PIXELS once it shows an image -- the placeholder's height=24 was
        # therefore pinning the live preview to a 24 px strip. Zero means
        # "size to the image".
        self.cam_view.config(image=photo, text=text, compound='center',
                             fg='lime', width=0, height=0)
        self.cam_photo = photo            # keep a ref

    def _cam_save_frame(self, frame, value=None, unit='V',
                        folder=None, prefix=None):
        """Save a frame to the output folder; returns the path or None.

        folder/prefix may be passed in (captured on the main thread) so a
        worker thread never reads a Tk StringVar -- that raises "main thread
        is not in main loop". Main-thread callers can omit them.
        """
        import cv2
        if folder is None:
            folder = self.cam_dir_var.get().strip() or '.'
        if prefix is None:
            prefix = self.cam_prefix_var.get().strip() or 'cap'
        fname = webcam.capture_filename(prefix,
                                        self.cam_capture_index, value=value,
                                        unit=unit, ts=datetime.now())
        path = os.path.join(folder, fname)
        os.makedirs(folder, exist_ok=True)
        bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        if cv2.imwrite(path, bgr):
            self.cam_capture_index += 1
            return path
        return None

    def cam_snapshot(self):
        frame = self.cam_last_frame
        if frame is None and self.cam is not None:
            frame = self.cam.read_rgb()
        if frame is None:
            messagebox.showerror("Webcam", "No frame to save (start preview first)")
            return
        path = self._cam_save_frame(frame)
        self.status_bar.config(text=f"Saved {path}" if path else "Save failed")

    # ---- interval capture (main-thread timer, uses the preview stream) ----
    def cam_toggle_interval(self):
        if self.cam_interval_job is not None:
            self._cam_stop_interval()
        else:
            self._cam_start_interval()

    def _cam_start_interval(self):
        try:
            interval = float(self.cam_interval_var.get())
            count = int(self.cam_count_var.get())
            if interval <= 0:
                raise ValueError("interval must be > 0")
        except ValueError as e:
            messagebox.showerror("Interval capture", str(e))
            return
        if not self.cam_previewing:
            self.cam_start_preview()
        self._cam_interval_remaining = count if count > 0 else -1
        self.cam_interval_btn.config(text="Stop interval")
        self.status_bar.config(text="Interval capture running")
        self._cam_interval_tick(int(interval * 1000))

    # ---- waveform-synced timed capture (Approach A) ---------------------
    def cam_toggle_timed(self):
        if self.cam_seq_running:
            self.cam_seq_running = False
            self.cam_tm_status.config(text="Stopping\u2026", foreground='orange')
        else:
            self._cam_start_timed()

    def _cam_start_timed(self):
        if self.cam_seq_running:
            messagebox.showinfo("Timed capture",
                                "A capture is already running -- stop it first.")
            return
        try:
            delays = webcam.timed_delays(
                explicit=self.cam_tm_delays.get(),
                start=self.cam_tm_start.get(),
                interval=self.cam_tm_interval.get(),
                count=self.cam_tm_count.get())
        except ValueError as e:
            messagebox.showerror("Timed capture", str(e))
            return
        if not delays:
            messagebox.showerror("Timed capture", "No capture times given.")
            return

        trig = self.cam_tm_trigger.get()
        trigger_ch = None
        if trig.startswith('CH'):
            trigger_ch = int(trig[2])
            if not self.sg:
                messagebox.showerror(
                    "Timed capture",
                    "Signal generator not connected, so the burst trigger "
                    "can't fire. Pick 'on Start click' instead.")
                return
        idx = self.cam_index_var.get().strip()
        if not idx.isdigit():
            messagebox.showerror("Timed capture", "Select a camera first.")
            return
        # One-shot capture needs the device FREE (no preview stream open),
        # and gives a fresh, frame-aligned frame at each delay instead of a
        # stale one from a lazily-drained stream.
        self.cam_stop_preview()
        if self.cam is not None:
            try:
                self.cam.close()
            except Exception:
                pass
            self.cam = None
        spec = webcam.resolve_camera(int(idx))

        csv_path = None
        if self.cam_tm_focus.get():
            csv_path = os.path.join(
                self.cam_dir_var.get().strip() or '.',
                f"{self.cam_prefix_var.get().strip() or 'cap'}_timed_focus.csv")

        self.cam_seq_running = True
        self._cam_active_btn = self.cam_tm_btn
        self._cam_active_idle = "Start timed capture"
        self._cam_active_status = self.cam_tm_status
        self.cam_tm_btn.config(text="Stop")
        total = delays[-1]
        self.cam_tm_status.config(
            text=f"Running: {len(delays)} shots over {total:g} s\u2026",
            foreground='black')
        while not self.cam_seq_queue.empty():
            try:
                self.cam_seq_queue.get_nowait()
            except queue.Empty:
                break
        save_dir = self.cam_dir_var.get().strip() or '.'
        save_prefix = self.cam_prefix_var.get().strip() or 'cap'
        self.cam_seq_thread = threading.Thread(
            target=self._cam_timed_worker,
            args=(trigger_ch, delays, csv_path, save_dir, save_prefix, spec),
            daemon=True)
        self.cam_seq_thread.start()
        self.root.after(50, self._drain_cam_queue)

    def _cam_timed_worker(self, trigger_ch, delays, csv_path,
                          save_dir, save_prefix, spec):
        """Fire the camera at each delay after a t=0 trigger. No waveform
        math -- the delay list is the phase schedule (Approach A)."""
        rows = []
        try:
            if trigger_ch is not None:
                self.sg.burst_trigger(trigger_ch)
            t0 = time.monotonic()
            for n, d in enumerate(delays):
                target = t0 + d
                # sleep in small slices so Stop stays responsive over a long
                # run (delays can be many minutes apart)
                while self.cam_seq_running and time.monotonic() < target:
                    time.sleep(min(0.05, max(0, target - time.monotonic())))
                if not self.cam_seq_running:
                    break
                frame = webcam.oneshot_rgb(spec)
                path, score = None, None
                if frame is not None:
                    path = self._cam_save_frame(
                        frame, value=d, unit='s',
                        folder=save_dir, prefix=save_prefix)
                    if csv_path is not None:
                        try:
                            score = webcam.focus_score(frame)
                        except Exception:
                            score = None
                        rows.append((d, os.path.basename(path or ''), score))
                self.cam_seq_queue.put(
                    ('step', n + 1, len(delays), f"t={d:g} s", path, score))
        except Exception as e:
            self.cam_seq_queue.put(('error', str(e)))
            return
        if csv_path and rows:
            try:
                with open(csv_path, 'w', newline='') as f:
                    w = csv.writer(f)
                    w.writerow(['delay_s', 'file', 'focus_score'])
                    w.writerows(rows)
            except Exception:
                pass
        self.cam_seq_queue.put(('done', len(delays), csv_path))

    def _cam_stop_interval(self):
        if self.cam_interval_job is not None:
            try:
                self.root.after_cancel(self.cam_interval_job)
            except Exception:
                pass
            self.cam_interval_job = None
        if hasattr(self, 'cam_interval_btn'):
            self.cam_interval_btn.config(text="Start interval")

    def _cam_interval_tick(self, period_ms):
        frame = self.cam_last_frame
        if frame is not None:
            path = self._cam_save_frame(frame)
            if path:
                self.status_bar.config(text=f"Interval saved {os.path.basename(path)}")
            if self._cam_interval_remaining > 0:
                self._cam_interval_remaining -= 1
                if self._cam_interval_remaining == 0:
                    self._cam_stop_interval()
                    self.status_bar.config(text="Interval capture complete")
                    return
        self.cam_interval_job = self.root.after(
            period_ms, self._cam_interval_tick, period_ms)

    # ---- stepped capture: step a sig-gen param, capture at each value ----
    def cam_toggle_sequence(self):
        if self.cam_seq_running:
            self.cam_seq_running = False
            self.cam_seq_status.config(text="Stopping…", foreground='orange')
        else:
            self._cam_start_sequence()

    def _cam_start_sequence(self):
        if not self.sg:
            messagebox.showerror("Stepped capture", "Signal generator not connected")
            return
        try:
            values = webcam.parse_level_list(self.cam_step_levels.get())
            if values is None:
                values = webcam.frange(self.cam_step_start.get(),
                                       self.cam_step_stop.get(),
                                       self.cam_step_step.get())
            dwell = float(self.cam_step_dwell.get())
            if dwell < 0:
                raise ValueError("dwell must be >= 0")
        except ValueError as e:
            messagebox.showerror("Stepped capture", str(e))
            return
        idx = self.cam_index_var.get().strip()
        if not idx.isdigit():
            messagebox.showerror("Stepped capture", "Select a camera first.")
            return
        # Free the device for one-shot grabs (fresh frame per step, not a
        # stale one from a lazily-drained preview stream).
        self.cam_stop_preview()
        if self.cam is not None:
            try:
                self.cam.close()
            except Exception:
                pass
            self.cam = None
        spec = webcam.resolve_camera(int(idx))
        ch = int(self.cam_sg_ch.get())
        key = 'OFST' if self.cam_sg_param.get().startswith('DC') else 'AMP'
        csv_path = None
        if self.cam_seq_focus.get():
            csv_path = os.path.join(self.cam_dir_var.get().strip() or '.',
                                    f"{self.cam_prefix_var.get().strip() or 'cap'}"
                                    f"_focus.csv")
        self.cam_seq_running = True
        self._cam_active_btn = self.cam_seq_btn
        self._cam_active_idle = "Run sweep"
        self._cam_active_status = self.cam_seq_status
        self.cam_seq_btn.config(text="Stop sweep")
        self.cam_seq_status.config(text=f"Sweeping {len(values)} steps…",
                                   foreground='black')
        while not self.cam_seq_queue.empty():
            try:
                self.cam_seq_queue.get_nowait()
            except queue.Empty:
                break
        save_dir = self.cam_dir_var.get().strip() or '.'
        save_prefix = self.cam_prefix_var.get().strip() or 'cap'
        self.cam_seq_thread = threading.Thread(
            target=self._cam_seq_worker,
            args=(ch, key, values, dwell, csv_path, save_dir, save_prefix,
                  spec),
            daemon=True)
        self.cam_seq_thread.start()
        self.root.after(50, self._drain_cam_queue)

    def _cam_seq_worker(self, ch, key, values, dwell, csv_path,
                        save_dir, save_prefix, spec):
        """Background: set the sig-gen param, dwell, capture, optional focus."""
        rows = []
        try:
            for n, v in enumerate(values):
                if not self.cam_seq_running:
                    break
                self.sg.set_basic_wave(ch, **{key: float(v)})
                # dwell in small chunks so Stop stays responsive
                end = time.monotonic() + dwell
                while self.cam_seq_running and time.monotonic() < end:
                    time.sleep(min(0.05, max(0, end - time.monotonic())))
                if not self.cam_seq_running:
                    break
                frame = webcam.oneshot_rgb(spec)
                path, score = None, None
                if frame is not None:
                    path = self._cam_save_frame(
                        frame, value=v, folder=save_dir, prefix=save_prefix)
                    if csv_path is not None:
                        try:
                            score = webcam.focus_score(frame)
                        except Exception:
                            score = None
                        rows.append((v, os.path.basename(path or ''), score))
                self.cam_seq_queue.put(
                    ('step', n + 1, len(values), f"{v:g} V", path, score))
        except Exception as e:
            self.cam_seq_queue.put(('error', str(e)))
            return
        if csv_path and rows:
            try:
                with open(csv_path, 'w', newline='') as f:
                    w = csv.writer(f)
                    w.writerow(['value', 'file', 'focus_score'])
                    w.writerows(rows)
            except Exception:
                pass
        self.cam_seq_queue.put(('done', len(values), csv_path))

    def _drain_cam_queue(self):
        # Shared by the voltage sweep and the timed capture; the active
        # button + its idle label + the status widget are set at start.
        btn = getattr(self, '_cam_active_btn', self.cam_seq_btn)
        idle = getattr(self, '_cam_active_idle', "Run sweep")
        status = getattr(self, '_cam_active_status', self.cam_seq_status)
        final = False
        try:
            while True:
                evt = self.cam_seq_queue.get_nowait()
                kind = evt[0]
                if kind == 'step':
                    _, i, total, label, path, score = evt
                    extra = f", focus {score:.0f}" if score is not None else ""
                    status.config(
                        text=f"{i}/{total}: {label} -> "
                             f"{os.path.basename(path) if path else 'no frame'}{extra}",
                        foreground='black')
                elif kind == 'done':
                    _, total, csv_path = evt
                    self.cam_seq_running = False
                    btn.config(text=idle)
                    msg = f"Capture complete: {total} shots"
                    if csv_path:
                        msg += f"; focus -> {os.path.basename(csv_path)}"
                    status.config(text=msg, foreground='green')
                    self.status_bar.config(text=msg)
                    final = True
                elif kind == 'error':
                    self.cam_seq_running = False
                    btn.config(text=idle)
                    status.config(text=f"Capture failed: {evt[1]}",
                                  foreground='red')
                    final = True
        except queue.Empty:
            pass
        if not final and self.cam_seq_running:
            self.root.after(50, self._drain_cam_queue)
        elif not final:
            btn.config(text=idle)
            status.config(text="Capture stopped", foreground='orange')



if __name__ == "__main__":
    root = tk.Tk()
    # Show a splash immediately: building the tabs takes a couple of seconds
    # and the desktop icon gives no feedback of its own.
    root.withdraw()
    try:
        splash = SplashScreen(root, version_string())
    except tk.TclError:
        splash = None
    app = InstrumentControlGUI(
        root, progress=splash.set_status if splash else None)
    if splash:
        splash.close()
    root.deiconify()
    root.mainloop()