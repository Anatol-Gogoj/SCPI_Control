#!/usr/bin/env python3
"""
Interactive arbitrary-waveform editor (EasyWaveX-style), Tk front end.

A waveform is a coordinate timeline of breakpoints (x, y) spliced by base-
waveform segments (one per interval). Breakpoints are draggable dots on the
canvas and editable by exact coordinate in the left sidebar; each interval's
shape (LINE/HOLD/SINE/SQUARE/RAMP/PULSE/EXP) connects its two endpoints.

All model math lives in arb_build; this module is the Tk shell only. It reuses
the instrument upload backend (BK4055B.upload_arb/select_arb) and the arb
library (SignalGenPresetStore.save_arb/load_arb/...).
"""
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import arb_build as ab
import easywave_export as ew
from siggen_presets import (arb_from_csv, write_arb_template, sanitize_arb_name)
from instruments import BK4055B


_DOT_R = 4          # breakpoint dot radius (px)
_HIT_R = 8          # click hit radius (px)
_PAD = 12           # canvas inner padding (px)
_MIN_SPP = 1 / 8    # min samples-per-pixel (max zoom: 8 px/sample)
_UNDO_DEPTH = 50

# X-axis time units (factor to seconds). The breakpoint X is real time in the
# selected unit; the waveform's total span is one period, so the channel
# frequency is derived as 1/(span * factor) on upload.
_UNIT_FACTORS = {'µs': 1e-6, 'ms': 1e-3, 's': 1.0}

# Segment params edited in volts (shown x y_scale, stored normalized) so they
# match the breakpoint Y / Y-axis units. Everything else (cycles, duty, sym,
# phase, rise/fall) keeps its own unit.
_VOLT_PARAMS = ('amp', 'offset')


class ArbWaveformEditor(tk.Toplevel):
    def __init__(self, app, channel):
        super().__init__(app.root)
        self.app = app
        self.channel = channel
        self.title(f"Arbitrary Waveform Editor - CH{channel}")
        self.transient(app.root)

        # Model + history
        self.recipe = self._initial_recipe()
        self.samples = ab.render_recipe(self.recipe)
        self._undo = []
        self._redo = []

        # Canvas view state
        self.view_start = 0.0          # first visible sample index
        self.samples_per_px = 1.0      # zoom
        self.n_periods = 1
        self.time_unit = 'ms'         # X axis is real time in this unit
        self.y_scale = 10.0            # full-scale +/- volts (4055B max 20 Vpp HiZ)
        self._drag_idx = None          # breakpoint being dragged
        self._sel = None               # selected breakpoint index
        self._dirty = False            # unsaved edits since last save/upload

        self._build_ui()
        self.bind('<Control-z>', lambda e: self.undo())
        self.bind('<Control-y>', lambda e: self.redo())
        self.bind('<Control-Z>', lambda e: self.redo())   # ctrl-shift-z
        self.tree.bind('<Delete>', self._delete_selected)
        self.canvas.bind('<Delete>', self._delete_selected)
        self.protocol('WM_DELETE_WINDOW', self._on_close)
        self.after(50, self._fit_all)     # fit once geometry is realised

    # -- initial state -----------------------------------------------------
    def _initial_recipe(self):
        """Reopen the channel's current arb (with its recipe if saved),
        else approximate its staged samples, else a fresh default."""
        widgets = self.app.sg_channel_widgets[self.channel]
        name = widgets['arb_name_var'].get().strip()
        if name:
            try:
                rec = self.app.sg_presets.load_arb_recipe(name)
                if rec:
                    return ab.recipe_from_json(ab.recipe_to_json(rec))
            except Exception:
                pass
        samp = widgets.get('arb_samples')
        if samp:
            return ab.samples_to_recipe(samp, total_points=len(samp))
        return ab.default_recipe(ab.DEFAULT_POINTS)

    # -- UI construction ---------------------------------------------------
    def _build_ui(self):
        # Header
        header = ttk.Frame(self, padding=(10, 8))
        header.pack(fill='x')
        ttk.Label(header, text="Points:").pack(side=tk.LEFT)
        self.points_var = tk.StringVar(value=str(self.recipe['total_points']))
        pe = ttk.Entry(header, width=8, textvariable=self.points_var)
        pe.pack(side=tk.LEFT, padx=4)
        pe.bind('<Return>', lambda e: self._apply_points())
        ttk.Button(header, text="Set", command=self._apply_points).pack(side=tk.LEFT)
        ttk.Label(header, text=f"samples (max {BK4055B.ARB_MAX_POINTS})").pack(side=tk.LEFT, padx=6)
        ttk.Label(header, text="Full-scale ±V:").pack(side=tk.LEFT, padx=(12, 0))
        self.yscale_var = tk.StringVar(value=f'{self.y_scale:g}')
        ye = ttk.Entry(header, width=6, textvariable=self.yscale_var)
        ye.pack(side=tk.LEFT, padx=4)
        ye.bind('<Return>', lambda e: self._apply_yscale())
        ttk.Button(header, text="Set", command=self._apply_yscale).pack(side=tk.LEFT)
        self.dt_label = ttk.Label(header, text="")
        self.dt_label.pack(side=tk.LEFT, padx=12)

        # Main row: sidebar (left) + canvas (right)
        main = ttk.Frame(self)
        main.pack(fill='both', expand=True, padx=10)

        side = ttk.LabelFrame(main, text="Points", padding=6)
        side.pack(side=tk.LEFT, fill='y')
        self.tree = ttk.Treeview(side, columns=('x', 'y', 'type'),
                                 show='headings', height=12, selectmode='browse')
        for col, txt, w in (('x', 'X (ms)', 70), ('y', 'Y (V)', 70), ('type', 'To next', 80)):
            self.tree.heading(col, text=txt)
            self.tree.column(col, width=w, anchor='center')
        self.tree.pack(fill='y')
        self.tree.bind('<<TreeviewSelect>>', self._on_tree_select)
        self.tree.bind('<Double-1>', self._on_tree_double)

        btns = ttk.Frame(side)
        btns.pack(fill='x', pady=4)
        ttk.Button(btns, text="Add", width=6, command=self._add_point_btn).pack(side=tk.LEFT)
        ttk.Button(btns, text="Del", width=6, command=self._del_point_btn).pack(side=tk.LEFT)

        # Per-interval segment editor
        self.seg_frame = ttk.LabelFrame(side, text="Segment after point", padding=6)
        self.seg_frame.pack(fill='x', pady=4)
        ttk.Label(self.seg_frame, text="Type:").grid(row=0, column=0, sticky='w')
        self.seg_type = ttk.Combobox(self.seg_frame, width=10, state='readonly',
                                     values=list(ab.SEGMENT_TYPES))
        self.seg_type.grid(row=0, column=1, pady=2)
        self.seg_type.bind('<<ComboboxSelected>>', lambda e: self._on_type_change())
        self.adv_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.seg_frame, text="Advanced (offset, phase, edges)",
                        variable=self.adv_var,
                        command=self._load_segment_form).grid(
                            row=1, column=0, columnspan=2, sticky='w', pady=(4, 0))
        self.seg_param_frame = ttk.Frame(self.seg_frame)
        self.seg_param_frame.grid(row=2, column=0, columnspan=2, sticky='w')
        self.seg_param_vars = {}
        tk.Label(self.seg_frame, fg='gray', justify=tk.LEFT, wraplength=190,
                 text="Vpk = peak (±) about the baseline. "
                      "Sine: Vpp = 2×Vpk, Vrms ≈ 0.707×Vpk.").grid(
                          row=3, column=0, columnspan=2, sticky='w', pady=(4, 0))

        # Canvas
        right = ttk.Frame(main)
        right.pack(side=tk.LEFT, fill='both', expand=True, padx=(10, 0))
        self.canvas = tk.Canvas(right, width=620, height=300, bg='white',
                                highlightthickness=1, highlightbackground='#999')
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Button-1>', self._on_canvas_press)
        self.canvas.bind('<B1-Motion>', self._on_canvas_drag)
        self.canvas.bind('<ButtonRelease-1>', self._on_canvas_release)
        self.canvas.bind('<Button-3>', self._on_canvas_right)
        self.canvas.bind('<Configure>', lambda e: self._redraw())
        self.readout = ttk.Label(right, text="")
        self.readout.pack(anchor='w')

        # View controls
        view = ttk.Frame(self, padding=(10, 4))
        view.pack(fill='x')
        ttk.Button(view, text="Fit All", command=self._fit_all).pack(side=tk.LEFT)
        ttk.Button(view, text="Zoom -", command=lambda: self._zoom(0.5)).pack(side=tk.LEFT, padx=2)
        ttk.Button(view, text="Zoom +", command=lambda: self._zoom(2.0)).pack(side=tk.LEFT, padx=2)
        ttk.Label(view, text="Periods:").pack(side=tk.LEFT, padx=(12, 2))
        self.periods_var = tk.StringVar(value="1")
        pc = ttk.Combobox(view, width=3, state='readonly', textvariable=self.periods_var,
                          values=['1', '2', '3'])
        pc.pack(side=tk.LEFT)
        pc.bind('<<ComboboxSelected>>', lambda e: self._set_periods())
        ttk.Label(view, text="Time unit:").pack(side=tk.LEFT, padx=(12, 2))
        self.unit_var = tk.StringVar(value=self.time_unit)
        uc = ttk.Combobox(view, width=4, state='readonly', textvariable=self.unit_var,
                          values=list(_UNIT_FACTORS.keys()))
        uc.pack(side=tk.LEFT)
        uc.bind('<<ComboboxSelected>>', lambda e: self._set_unit())
        ttk.Button(view, text="Undo", command=self.undo).pack(side=tk.RIGHT, padx=2)
        ttk.Button(view, text="Redo", command=self.redo).pack(side=tk.RIGHT, padx=2)
        self.hbar = ttk.Scrollbar(self, orient='horizontal', command=self._on_scroll)
        self.hbar.pack(fill='x', padx=10)

        # Library + save/upload
        lib = ttk.LabelFrame(self, text="Library & Upload", padding=8)
        lib.pack(fill='x', padx=10, pady=8)
        ttk.Label(lib, text="Name:").grid(row=0, column=0, sticky='w')
        self.name_entry = ttk.Entry(lib, width=16)
        self.name_entry.grid(row=0, column=1, padx=4)
        self.name_entry.insert(0, self.app.sg_channel_widgets[self.channel]['arb_name_var'].get())
        ttk.Button(lib, text="Save to Library", command=self.save_to_library).grid(row=0, column=2, padx=4)
        self.lib_select = ttk.Combobox(lib, width=16, state='readonly')
        self.lib_select.grid(row=0, column=3, padx=(12, 4))
        ttk.Button(lib, text="Load", command=self.load_from_library).grid(row=0, column=4)
        ttk.Button(lib, text="Delete", command=self.delete_from_library).grid(row=0, column=5, padx=4)

        ttk.Button(lib, text="Import CSV...", command=self.import_csv).grid(row=1, column=0, columnspan=2, pady=6, sticky='w')
        ttk.Button(lib, text="Export CSV...", command=self.export_csv).grid(row=1, column=2, pady=6)
        ttk.Button(lib, text="Save Template...", command=self.save_template).grid(row=1, column=3, pady=6)
        ttk.Button(lib, text="Export for EasyWaveX (flash drive)...",
                   command=self.export_easywavex).grid(row=1, column=4, columnspan=2,
                                                       pady=6, padx=(12, 0), sticky='w')
        ttk.Label(lib, text="Send to CH:").grid(row=2, column=0, sticky='e', pady=6)
        self.target_var = tk.StringVar(value=str(self.channel))
        ttk.Combobox(lib, width=4, state='readonly', textvariable=self.target_var,
                     values=['1', '2']).grid(row=2, column=1, sticky='w', pady=6)
        ttk.Button(lib, text="Upload && Select",
                   command=self.upload).grid(row=2, column=2, columnspan=2,
                                             pady=6, padx=4, sticky='w')

        hint = ("Click canvas to add a point - drag a dot to move it - right-click a dot to delete. "
                "Edit exact X/Y in the table (double-click a cell). Ctrl+Z / Ctrl+Y to undo/redo.")
        tk.Label(self, text=hint, fg='gray', anchor='w', justify=tk.LEFT,
                 wraplength=820).pack(fill='x', padx=12, pady=(0, 8))

        self._lib_refresh()
        self._refresh_tree()

    # -- model commit / history -------------------------------------------
    def _commit(self, new_recipe):
        """Apply an edit, pushing the prior state onto the undo stack."""
        self._undo.append(ab._copy(self.recipe))
        if len(self._undo) > _UNDO_DEPTH:
            self._undo.pop(0)
        self._redo.clear()
        self._dirty = True
        self._apply(new_recipe)

    def _apply(self, recipe):
        self.recipe = recipe
        try:
            self.samples = ab.render_recipe(recipe)
        except Exception as e:
            messagebox.showerror("Render error", str(e), parent=self)
            return
        self._refresh_tree()
        self._redraw()

    def undo(self):
        if self._undo:
            self._redo.append(ab._copy(self.recipe))
            self._dirty = True
            self._apply(self._undo.pop())

    def redo(self):
        if self._redo:
            self._undo.append(ab._copy(self.recipe))
            self._dirty = True
            self._apply(self._redo.pop())

    # -- sidebar -----------------------------------------------------------
    def _refresh_tree(self):
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        bps = self.recipe['breakpoints']
        segs = self.recipe['segments']
        for i, (x, y) in enumerate(bps):
            seg = segs[i]['type'] if i < len(segs) else '-'
            self.tree.insert('', 'end', iid=str(i),
                             values=(f'{x:.3f}', f'{y * self.y_scale:.3f}', seg))
        if sel and sel[0] in self.tree.get_children():
            self.tree.selection_set(sel[0])
        self._update_dt_label()

    def _on_tree_select(self, _evt):
        rows = self.tree.selection()
        self._sel = int(rows[0]) if rows else None
        self._load_segment_form()
        self._redraw()

    def _load_segment_form(self):
        """Populate the per-interval segment type + params for the selected row."""
        for w in self.seg_param_frame.winfo_children():
            w.destroy()
        self.seg_param_vars = {}
        i = self._sel
        segs = self.recipe['segments']
        if i is None or i >= len(segs):       # last point has no "next" segment
            self.seg_type.set('')
            self.seg_type.config(state='disabled')
            return
        self.seg_type.config(state='readonly')
        self.seg_type.set(segs[i]['type'])
        params = segs[i].get('params') or {}
        adv = self.adv_var.get()
        row = 0
        for key, label, default, advanced in ab.SEGMENT_PARAMS.get(segs[i]['type'], []):
            if advanced and not adv:
                continue
            ttk.Label(self.seg_param_frame, text=f"{label}:").grid(row=row, column=0, sticky='w')
            raw = params.get(key, default)
            shown = raw * self.y_scale if key in _VOLT_PARAMS else raw
            var = tk.StringVar(value=f'{shown:g}')
            ent = ttk.Entry(self.seg_param_frame, width=8, textvariable=var)
            ent.grid(row=row, column=1, pady=1)
            ent.bind('<Return>', lambda e: self._apply_segment_params())
            ent.bind('<FocusOut>', lambda e: self._apply_segment_params())
            self.seg_param_vars[key] = var
            row += 1

    def _on_type_change(self):
        if self._sel is None or self._sel >= len(self.recipe['segments']):
            return
        self._commit(ab.set_segment_type(self.recipe, self._sel, self.seg_type.get()))
        self._load_segment_form()

    def _apply_segment_params(self):
        if self._sel is None or self._sel >= len(self.recipe['segments']):
            return
        try:
            params = {}
            for k, v in self.seg_param_vars.items():
                val = float(v.get())
                if k in _VOLT_PARAMS:        # volts -> normalized
                    val = val / self.y_scale
                params[k] = val
        except ValueError:
            return
        if params != (self.recipe['segments'][self._sel].get('params') or {}):
            self._commit(ab.set_segment_params(self.recipe, self._sel, params))

    def _on_tree_double(self, evt):
        """Inline-edit the X or Y cell of a breakpoint."""
        region = self.tree.identify('region', evt.x, evt.y)
        if region != 'cell':
            return
        col = self.tree.identify_column(evt.x)   # '#1'=x, '#2'=y, '#3'=type
        row = self.tree.identify_row(evt.y)
        if not row or col not in ('#1', '#2'):
            return
        i = int(row)
        x0, y0, w, h = self.tree.bbox(row, col)
        var = tk.StringVar(value=self.tree.set(row, 'x' if col == '#1' else 'y'))
        ent = ttk.Entry(self.tree, textvariable=var)
        ent.place(x=x0, y=y0, width=w, height=h)
        ent.focus_set()
        ent.select_range(0, tk.END)

        def commit(_e=None):
            try:
                val = float(var.get())
            except ValueError:
                ent.destroy()
                return
            bp = self.recipe['breakpoints'][i]
            # the Y column is shown/typed in volts; store normalized
            nx, ny = (val, bp[1]) if col == '#1' else (bp[0], val / self.y_scale)
            ent.destroy()
            self._commit(ab.move_point(self.recipe, i, nx, ny))
        ent.bind('<Return>', commit)
        ent.bind('<FocusOut>', commit)
        ent.bind('<Escape>', lambda e: ent.destroy())

    def _add_point_btn(self):
        """Add a point midway through the currently selected interval."""
        bps = self.recipe['breakpoints']
        i = self._sel if self._sel is not None else 0
        j = min(i + 1, len(bps) - 1)
        x = (bps[i][0] + bps[j][0]) / 2 if j != i else bps[-1][0] - 1e-3
        y = (bps[i][1] + bps[j][1]) / 2
        try:
            self._commit(ab.add_point(self.recipe, x, y))
        except ValueError as e:
            messagebox.showerror("Add point", str(e), parent=self)

    def _del_point_btn(self):
        self._delete_selected()

    def _delete_selected(self, _evt=None):
        """Delete the highlighted breakpoint (Del key or the Del button)."""
        if self._sel is None:
            return None
        try:
            self._commit(ab.delete_point(self.recipe, self._sel))
            self._sel = None
        except ValueError as e:
            messagebox.showerror("Delete point", str(e), parent=self)
        return 'break'

    def _on_close(self):
        if self._dirty and not messagebox.askyesno(
                "Discard changes?",
                "Discard unsaved changes and close the waveform editor?",
                parent=self):
            return
        self.destroy()

    # -- points / view -----------------------------------------------------
    def _apply_points(self):
        try:
            n = int(float(self.points_var.get()))
        except ValueError:
            return
        if n < 2 or n > BK4055B.ARB_MAX_POINTS:
            messagebox.showerror("Points",
                                 f"Points must be 2..{BK4055B.ARB_MAX_POINTS}", parent=self)
            self.points_var.set(str(self.recipe['total_points']))
            return
        self._commit(ab.set_total_points(self.recipe, n))

    def _apply_yscale(self):
        """Set the full-scale +/- voltage the Y axis represents (max 10 V HiZ)."""
        try:
            v = float(self.yscale_var.get())
        except ValueError:
            self.yscale_var.set(f'{self.y_scale:g}')
            return
        if v <= 0:
            self.yscale_var.set(f'{self.y_scale:g}')
            return
        if v > 10.0:
            v = 10.0  # 4055B max is 20 Vpp into HiZ (+/-10 V)
            self.app.status_bar.config(text="Full-scale capped at 10 V (4055B max)")
        self.y_scale = v
        self.yscale_var.set(f'{v:g}')
        self._refresh_tree()
        self._load_segment_form()      # re-show amp/offset in the new volts
        self._redraw()

    def _x_max(self):
        return self.recipe['breakpoints'][-1][0]

    def _x0(self):
        return self.recipe['breakpoints'][0][0]

    def _xspan(self):
        span = self._x_max() - self._x0()
        return span if span > 0 else 1.0

    def _duration_s(self):
        """Played period in seconds = X span in the selected time unit."""
        return self._xspan() * _UNIT_FACTORS[self.time_unit]

    def _update_dt_label(self):
        # The X span IS the played period; the channel frequency is derived
        # from it on upload. Points = total samples (resolution).
        n = self.recipe['total_points']
        dur_s = self._duration_s()
        freq = 1.0 / dur_s if dur_s > 0 else 0.0
        self.dt_label.config(
            text=f"{n} samples  ·  period {self._xspan():g} {self.time_unit} "
                 f"= {freq:g} Hz  ·  {n * freq / 1e6:.3g} MSa/s")

    def _fit_all(self):
        w = self._cw()
        n = len(self.samples) * self.n_periods
        self.samples_per_px = max(n / w, _MIN_SPP)
        self.view_start = 0.0
        self._redraw()

    def _zoom(self, factor):
        n = len(self.samples) * self.n_periods
        center = self.view_start + (self._cw() / 2) * self.samples_per_px
        self.samples_per_px = max(self.samples_per_px / factor, _MIN_SPP)
        self.view_start = center - (self._cw() / 2) * self.samples_per_px
        self._clamp_view()
        self._redraw()

    def _set_periods(self):
        self.n_periods = int(self.periods_var.get())
        self._fit_all()

    def _set_unit(self):
        self.time_unit = self.unit_var.get()
        self.tree.heading('x', text=f'X ({self.time_unit})')
        self._refresh_tree()
        self._redraw()

    def _on_scroll(self, *args):
        n = len(self.samples) * self.n_periods
        if args[0] == 'moveto':
            self.view_start = float(args[1]) * n
        elif args[0] == 'scroll':
            self.view_start += float(args[1]) * self._cw() * self.samples_per_px * 0.2
        self._clamp_view()
        self._redraw()

    def _clamp_view(self):
        n = len(self.samples) * self.n_periods
        max_start = max(0.0, n - self._cw() * self.samples_per_px)
        self.view_start = min(max(self.view_start, 0.0), max_start)

    # -- canvas geometry helpers ------------------------------------------
    def _cw(self):
        return max(self.canvas.winfo_width() - 2 * _PAD, 50)

    def _ch(self):
        return max(self.canvas.winfo_height() - 2 * _PAD, 50)

    def _px(self, sample_idx):
        return _PAD + (sample_idx - self.view_start) / self.samples_per_px

    def _py(self, value):
        h = self._ch()
        return _PAD + h / 2 - value * (h / 2)

    def _px_to_sample(self, xp):
        return self.view_start + (xp - _PAD) * self.samples_per_px

    def _py_to_value(self, yp):
        h = self._ch()
        return ab._clamp((_PAD + h / 2 - yp) / (h / 2))

    def _bp_pixel(self, x_coord, y):
        """Pixel position of a breakpoint (drawn in the first period)."""
        idx = (x_coord - self._x0()) / self._xspan() * len(self.samples)
        return self._px(idx), self._py(y)

    def _sample_to_xcoord(self, idx):
        """Map a sample index (within one period) to its X time coordinate."""
        return self._x0() + (idx / len(self.samples)) * self._xspan()

    # -- canvas drawing ----------------------------------------------------
    def _redraw(self):
        c = self.canvas
        c.delete('all')
        w_full = self.canvas.winfo_width()
        h = self._ch()
        n = len(self.samples)
        nv = n * self.n_periods

        # zero line + frame
        c.create_line(_PAD, self._py(0), _PAD + self._cw(), self._py(0),
                      fill='#ccc', dash=(3, 3))

        # decimated min/max envelope across visible pixel columns
        prev = None
        for xp in range(int(self._cw()) + 1):
            i0 = self._px_to_sample(_PAD + xp)
            i1 = self._px_to_sample(_PAD + xp + 1)
            a, b = int(i0), max(int(i0) + 1, int(i1))
            vals = [self.samples[k % n] for k in range(a, b) if 0 <= k % n < n and 0 <= k < nv]
            if not vals:
                continue
            lo, hi = min(vals), max(vals)
            x = _PAD + xp
            c.create_line(x, self._py(hi), x, self._py(lo), fill='#1565c0')
            mid = self._py((lo + hi) / 2)
            if prev is not None:
                c.create_line(prev[0], prev[1], x, mid, fill='#1565c0')
            prev = (x, mid)

        # breakpoint dots (first period only)
        for i, (x_coord, y) in enumerate(self.recipe['breakpoints']):
            px, py = self._bp_pixel(x_coord, y)
            if px < _PAD - _DOT_R or px > _PAD + self._cw() + _DOT_R:
                continue
            color = '#d32f2f' if i == self._sel else '#333'
            c.create_oval(px - _DOT_R, py - _DOT_R, px + _DOT_R, py + _DOT_R,
                          fill=color, outline='white')

        # Y axis labels (volts at full scale)
        c.create_text(_PAD + 2, _PAD, anchor='nw', fill='#999',
                      font=('Arial', 8), text=f'+{self.y_scale:g} V')
        c.create_text(_PAD + 2, _PAD + h, anchor='sw', fill='#999',
                      font=('Arial', 8), text=f'-{self.y_scale:g} V')

        # X axis labels, anchored to the bottom edge so they aren't clipped
        left_i = self._px_to_sample(_PAD)
        right_i = self._px_to_sample(_PAD + self._cw())
        yb = self.canvas.winfo_height() - 2
        c.create_text(_PAD, yb, anchor='sw', fill='#666',
                      font=('Arial', 8), text=self._axis_text(left_i))
        c.create_text(_PAD + self._cw(), yb, anchor='se', fill='#666',
                      font=('Arial', 8), text=self._axis_text(right_i))

        # scrollbar thumb
        if nv > 0:
            lo = self.view_start / nv
            hi = (self.view_start + self._cw() * self.samples_per_px) / nv
            self.hbar.set(max(0.0, lo), min(1.0, hi))

    def _axis_text(self, sample_idx):
        return f"{self._sample_to_xcoord(sample_idx):g} {self.time_unit}"

    # -- canvas interaction ------------------------------------------------
    def _hit_breakpoint(self, xp, yp):
        for i, (x_coord, y) in enumerate(self.recipe['breakpoints']):
            px, py = self._bp_pixel(x_coord, y)
            if abs(px - xp) <= _HIT_R and abs(py - yp) <= _HIT_R:
                return i
        return None

    def _on_canvas_press(self, evt):
        self.canvas.focus_set()        # so the Delete key targets a point here
        i = self._hit_breakpoint(evt.x, evt.y)
        if i is not None:
            self._drag_idx = i
            self._sel = i
            self.tree.selection_set(str(i))
            self._undo.append(ab._copy(self.recipe))   # one undo entry per drag
            self._redo.clear()
            return
        # add a point at the clicked coordinate (within the period)
        idx = self._px_to_sample(evt.x) % len(self.samples)
        x_coord = self._sample_to_xcoord(idx)
        y = self._py_to_value(evt.y)
        try:
            self._commit(ab.add_point(self.recipe, x_coord, y))
        except ValueError:
            pass  # duplicate x, ignore

    def _on_canvas_drag(self, evt):
        if self._drag_idx is None:
            return
        idx = self._px_to_sample(evt.x) % len(self.samples)
        x_coord = self._sample_to_xcoord(idx)
        y = self._py_to_value(evt.y)
        # move without pushing another undo entry (snapshot taken on press)
        self.recipe = ab.move_point(self.recipe, self._drag_idx, x_coord, y)
        self.samples = ab.render_recipe(self.recipe)
        self._dirty = True
        self.readout.config(
            text=f"point {self._drag_idx}:  x={x_coord:.3f} {self.time_unit}"
                 f"   y={y * self.y_scale:.3f} V")
        self._refresh_tree()
        self.tree.selection_set(str(self._drag_idx))
        self._redraw()

    def _on_canvas_release(self, _evt):
        self._drag_idx = None

    def _on_canvas_right(self, evt):
        i = self._hit_breakpoint(evt.x, evt.y)
        if i is None:
            return
        try:
            self._commit(ab.delete_point(self.recipe, i))
            self._sel = None
        except ValueError as e:
            messagebox.showerror("Delete point", str(e), parent=self)

    # -- library / CSV / upload -------------------------------------------
    def _lib_refresh(self):
        names = self.app.sg_presets.arb_names()
        self.lib_select['values'] = names
        if names and self.lib_select.get() not in names:
            self.lib_select.set(names[0])

    def save_to_library(self):
        name = self.name_entry.get().strip()
        if not name:
            messagebox.showerror("Save", "Enter a name first", parent=self)
            return
        try:
            clean = self.app.sg_presets.save_arb(name, self.samples, recipe=self.recipe)
            self._lib_refresh()
            self.lib_select.set(clean)
            self._dirty = False
            self.app.status_bar.config(text=f"Arb saved to library: {clean}")
        except Exception as e:
            messagebox.showerror("Save error", str(e), parent=self)

    def load_from_library(self):
        name = self.lib_select.get()
        if not name:
            return
        try:
            rec = self.app.sg_presets.load_arb_recipe(name)
            if rec:
                recipe = ab.recipe_from_json(ab.recipe_to_json(rec))
            else:
                recipe = ab.samples_to_recipe(self.app.sg_presets.load_arb(name))
        except Exception as e:
            messagebox.showerror("Load error", str(e), parent=self)
            return
        self.name_entry.delete(0, tk.END)
        self.name_entry.insert(0, name)
        self.points_var.set(str(recipe['total_points']))
        self._commit(recipe)
        self._fit_all()
        self._dirty = False        # just loaded from the library = saved state

    def delete_from_library(self):
        name = self.lib_select.get()
        if not name:
            return
        if not messagebox.askyesno("Delete", f"Delete library waveform '{name}'?", parent=self):
            return
        self.app.sg_presets.delete_arb(name)
        self._lib_refresh()

    def import_csv(self):
        path = filedialog.askopenfilename(
            parent=self, title="Import waveform CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        try:
            samples = arb_from_csv(path)
            recipe = ab.samples_to_recipe(samples, total_points=len(samples))
        except Exception as e:
            messagebox.showerror("CSV error", str(e), parent=self)
            return
        self.name_entry.delete(0, tk.END)
        self.name_entry.insert(0, os.path.splitext(os.path.basename(path))[0])
        self.points_var.set(str(recipe['total_points']))
        self._commit(recipe)
        self._fit_all()

    def export_csv(self):
        path = filedialog.asksaveasfilename(
            parent=self, title="Export waveform CSV", defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")])
        if not path:
            return
        try:
            import csv
            with open(path, 'w', newline='') as f:
                w = csv.writer(f)
                w.writerow(['value'])
                for s in self.samples:
                    w.writerow([f'{s:.6g}'])
            self.app.status_bar.config(text=f"Exported: {path}")
        except Exception as e:
            messagebox.showerror("Export error", str(e), parent=self)

    def save_template(self):
        path = filedialog.asksaveasfilename(
            parent=self, title="Save CSV template", defaultextension=".csv",
            initialfile="arb_template.csv", filetypes=[("CSV files", "*.csv")])
        if not path:
            return
        try:
            write_arb_template(path)
            self.app.status_bar.config(text=f"Template written: {path}")
        except Exception as e:
            messagebox.showerror("Template error", str(e), parent=self)

    def export_easywavex(self):
        """Flash-drive workaround for the 4055B's no-arb-over-USB firmware."""
        EasyWaveXExportDialog(self)

    def _set_channel_value(self, ch, key, val):
        """Write a value into a channel widget, handling both Entry (real GUI)
        and StringVar (demo) without caring which it is."""
        w = self.app.sg_channel_widgets[ch].get(key)
        if w is None:
            return
        try:
            if hasattr(w, 'set'):          # StringVar / Combobox
                w.set(f'{val:g}')
            else:                          # ttk.Entry
                w.delete(0, tk.END)
                w.insert(0, f'{val:g}')
        except Exception:
            pass

    def upload(self):
        app = self.app
        ch = int(self.target_var.get())
        if not app.sg:
            messagebox.showerror("Upload", "Signal generator not connected", parent=self)
            return
        name = self.name_entry.get().strip()
        if not name:
            messagebox.showerror("Upload", "Enter a name first", parent=self)
            return
        try:
            # The X time span is the played period -> derive channel frequency.
            dur_s = self._duration_s()
            freq = 1.0 / dur_s if dur_s > 0 else 1000.0
            # Editor Y is full-scale +/-y_scale volts, so the channel amplitude
            # is 2*y_scale Vpp (offset 0); the normalized arb carries the shape.
            amp = 2.0 * self.y_scale
            offset = 0.0
            # Upload a SHORT buffer and play it via TrueArb: the box outputs the
            # exact npts points at sample_rate = freq*npts, so output period ==
            # the X span. Short buffers avoid the 32 KB DDS upload that wedges
            # the USBTMC endpoint (issue #20).
            npts = getattr(app.sg, 'ARB_DEFAULT_POINTS', 1024)
            clean = app.sg.upload_arb(ch, name, self.samples, points=npts,
                                      freq_hz=freq, amp_vpp=amp, offset_v=offset)
            app.sg.select_arb(ch, clean)
            if hasattr(app.sg, 'set_sample_rate'):
                app.sg.set_sample_rate(ch, mode='TARB', value=freq * npts)
            # Amplitude/offset are mode-independent; frequency comes from SRATE
            # in TrueArb, so don't push FRQ (it would flip the box back to DDS).
            if hasattr(app.sg, 'set_basic_wave'):
                app.sg.set_basic_wave(ch, AMP=amp, OFST=offset)
        except Exception as e:
            msg = str(e)
            if 'over USB' in msg:
                msg += ("\n\nWorkaround: use 'Export for EasyWaveX (flash "
                        "drive)...' -- save the CSV, copy it to a flash "
                        "drive, and load it in EasyWaveX manually.")
            messagebox.showerror("Upload error", msg, parent=self)
            return
        # persist the recipe too, so the uploaded arb stays re-editable
        try:
            self.app.sg_presets.save_arb(clean, self.samples, recipe=self.recipe)
        except Exception:
            pass
        widgets = app.sg_channel_widgets[ch]
        widgets['arb_name_var'].set(clean)
        widgets['arb_samples'] = list(self.samples)
        widgets['waveform'].set('ARB')
        self._set_channel_value(ch, 'freq', freq)
        self._set_channel_value(ch, 'amp', amp)
        self._set_channel_value(ch, 'offset', offset)
        app._sg_update_visibility(ch)
        app._sg_redraw_preview(ch)
        try:
            app._sg_refresh_applied(ch)
        except Exception:
            pass
        app.status_bar.config(
            text=f"CH{ch}: uploaded '{clean}' ({len(self.samples)} pts) @ {freq:g} Hz")
        self._lib_refresh()
        self._dirty = False


class EasyWaveXExportDialog(tk.Toplevel):
    """Export the current waveform as an EasyWaveX template CSV.

    This is the USB workaround: the 4055B firmware cannot take arb uploads
    over USB (52-byte command cap -- see README quirks), so the file must
    travel by FLASH DRIVE and be loaded into EasyWaveX manually. The dialog
    says so, loudly, so nobody mistakes it for a direct upload.

    Field defaults follow the lab instructions (easywave_export docstring):
    frequency = 1/T_total, amp = highest voltage value, offset = amp/2,
    phase = 0. All editable. 1 V in the file = 1 kV at the Trek output.
    """

    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.title("Export for EasyWaveX (flash drive)")
        self.transient(editor)
        self.resizable(False, False)

        # Real voltage values: editor samples are normalized to +/-1 at
        # +/-y_scale volts full scale.
        self.values_v = [s * editor.y_scale for s in editor.samples]
        defaults = ew.suggest_header(self.values_v, editor._duration_s())

        tk.Label(self,
                 text=("This does NOT send anything to the 4055B.\n"
                       "The instrument cannot accept waveform uploads over USB "
                       "(firmware limit).\n"
                       "Save the CSV, copy it to a FLASH DRIVE, and load it in "
                       "EasyWaveX manually."),
                 bg='#7a4a00', fg='white', justify=tk.LEFT,
                 font=('TkDefaultFont', 10, 'bold'),
                 padx=12, pady=10, anchor='w').pack(fill='x')

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill='both', expand=True)
        self._vars = {}
        rows = [
            ('frequency', 'Frequency (Hz):', defaults['freq_hz'],
             '= 1 / T_total (waveform period from the editor X span)'),
            ('amp', 'Amp (V):', defaults['amp_v'],
             'highest voltage value in the waveform'),
            ('offset', 'Offset (V):', defaults['offset_v'], 'amp / 2'),
            ('phase', 'Phase (deg):', defaults['phase_deg'], 'leave 0'),
        ]
        for r, (key, label, val, hint) in enumerate(rows):
            ttk.Label(frm, text=label).grid(row=r, column=0, sticky='e', pady=2)
            var = tk.StringVar(value=f'{val:g}')
            self._vars[key] = var
            ttk.Entry(frm, width=14, textvariable=var).grid(
                row=r, column=1, sticky='w', padx=6, pady=2)
            ttk.Label(frm, text=hint, foreground='gray').grid(
                row=r, column=2, sticky='w', pady=2)

        n_src = len(self.values_v)
        ttk.Label(frm, text=(
            f"Waveform is resampled from {n_src} to exactly "
            f"{ew.EASYWAVE_POINTS} points (EasyWaveX requirement).\n"
            "Trek convention: 1 V in the file = 1 kV at the Trek output."),
            justify=tk.LEFT).grid(row=4, column=0, columnspan=3,
                                  sticky='w', pady=(10, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=5, column=0, columnspan=3, sticky='e', pady=(12, 0))
        ttk.Button(btns, text="Save CSV...", command=self._save).pack(
            side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side=tk.LEFT)

    def _save(self):
        try:
            freq = float(self._vars['frequency'].get())
            amp = float(self._vars['amp'].get())
            offset = float(self._vars['offset'].get())
            phase = float(self._vars['phase'].get())
        except ValueError:
            messagebox.showerror("EasyWaveX export",
                                 "All fields must be numbers", parent=self)
            return
        name = self.editor.name_entry.get().strip() or 'waveform'
        path = filedialog.asksaveasfilename(
            parent=self, title="Save EasyWaveX CSV (goes on a flash drive)",
            defaultextension=".csv",
            initialfile=f"{sanitize_arb_name(name)}.csv",
            filetypes=[("CSV files", "*.csv")])
        if not path:
            return
        try:
            ew.write_easywave_csv(path, self.values_v, freq, amp, offset,
                                  phase)
        except Exception as e:
            messagebox.showerror("EasyWaveX export", str(e), parent=self)
            return
        self.editor.app.status_bar.config(text=f"EasyWaveX CSV written: {path}")
        messagebox.showinfo(
            "EasyWaveX export",
            f"Saved:\n{path}\n\n"
            "Nothing was sent to the 4055B. Next steps:\n"
            "1. Copy the file to a flash drive.\n"
            "2. Take the drive to the EasyWaveX PC.\n"
            "3. In EasyWaveX, import the CSV to generate the waveform.",
            parent=self)
        self.destroy()
