#!/usr/bin/env python3
"""SLDEA Edge Review -- offline edge-detection GUI for SLDEA runs.

The companion program to the Digital Multitool's SLDEA tab: point it at a run
directory (SLDEA_<ts>/), it traces the active-area disc in every frame
(difference-imaging vs the 0 kV baseline + a Hough candidate), auto-accepts
confident detections, and queues the shaky ones for a human pick -- each
candidate outline is drawn over the photo and the user chooses A/B/C or
Reject. Breakdown heuristics (current spike, area collapse) annotate suspect
steps. Results are written back to the run's data.csv only after an explicit
prompt (a .bak is kept), together with an area-vs-voltage plot and outline
overlays for audit.

    python sldea_edge_gui.py [run-or-parent-dir] [--auto]

With --auto (used by the SLDEA tab's "auto process"), detection starts
immediately on launch. Keyboard: 1/2/3 pick a candidate, R reject,
Left/Right navigate, Enter accept + next.
"""
import os
import sys
import threading
import queue as _queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import sldea_edge as se

DEFAULT_PARENT = os.environ.get('SCPI_SLDEA_DIR',
                                '/mnt/shareDrive/robot_incubator/SLDEA_data')
CAND_COLORS = ['#00c853', '#2196f3', '#ff9100']     # A green, B blue, C orange
CAND_KEYS = ['A', 'B', 'C']
VIEW_W = 780


class EdgeReviewApp:
    def __init__(self, root, path=None, auto=False):
        self.root = root
        root.title("SLDEA Edge Review — Digital Multitool")
        root.geometry("1150x760")
        self.settings = dict(se.DEFAULT_SETTINGS)
        self.run = None
        self.rundir = None
        self.cands_all = {}     # frame row index -> candidate list
        self.results = {}       # row index -> chosen candidate | None=rejected
        self.auto_idx = set()   # auto-accepted row indices
        self.frame_rows = []    # row indices that have a frame file
        self.pos = 0
        self.flags = {}
        self._photo = None
        self._detq = _queue.Queue()
        self._build_ui()
        start = path or DEFAULT_PARENT
        self._populate_runs(start)
        if auto and self.rundir:
            root.after(300, self.detect)

    # ---------------- UI scaffolding ----------------
    def _build_ui(self):
        top = ttk.Frame(self.root, padding=6)
        top.pack(fill='x')
        ttk.Label(top, text="Run:").pack(side=tk.LEFT)
        self.run_box = ttk.Combobox(top, width=44, state='readonly')
        self.run_box.pack(side=tk.LEFT, padx=6)
        self.run_box.bind('<<ComboboxSelected>>', lambda _e: self._pick_run())
        ttk.Button(top, text="Browse…",
                   command=self._browse).pack(side=tk.LEFT)
        self.detect_btn = ttk.Button(top, text="▶ Detect Edges",
                                     command=self.detect)
        self.detect_btn.pack(side=tk.LEFT, padx=10)
        ttk.Button(top, text="Advanced…",
                   command=self._advanced).pack(side=tk.LEFT)
        self.save_btn = ttk.Button(top, text="💾 Save to data.csv…",
                                   command=self.save, state='disabled')
        self.save_btn.pack(side=tk.RIGHT)

        mid = ttk.Frame(self.root)
        mid.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(mid, width=VIEW_W, height=560, bg='#222',
                                highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, fill='both', expand=True,
                         padx=(6, 0), pady=4)

        side = ttk.Frame(mid, padding=8, width=330)
        side.pack(side=tk.RIGHT, fill='y')
        self.info = tk.Label(side, text="pick a run and Detect", justify='left',
                             anchor='nw', font=('TkDefaultFont', 10))
        self.info.pack(fill='x', pady=(0, 6))
        self.cand_var = tk.IntVar(value=0)
        self.cand_frame = ttk.LabelFrame(side, text="Candidates", padding=6)
        self.cand_frame.pack(fill='x')
        self.cand_radios = []
        for k in range(3):
            rb = tk.Radiobutton(
                self.cand_frame, text=f"{CAND_KEYS[k]}: —", anchor='w',
                variable=self.cand_var, value=k, fg=CAND_COLORS[k],
                activeforeground=CAND_COLORS[k],
                command=self._choose_current)
            rb.pack(fill='x')
            self.cand_radios.append(rb)
        bt = ttk.Frame(side)
        bt.pack(fill='x', pady=6)
        ttk.Button(bt, text="✔ Accept (Enter)",
                   command=self._accept_next).pack(side=tk.LEFT)
        ttk.Button(bt, text="✘ Reject (R)",
                   command=self._reject).pack(side=tk.LEFT, padx=6)
        nav = ttk.Frame(side)
        nav.pack(fill='x')
        ttk.Button(nav, text="◀ Prev",
                   command=lambda: self._step(-1)).pack(side=tk.LEFT)
        ttk.Button(nav, text="Next ▶",
                   command=lambda: self._step(+1)).pack(side=tk.LEFT, padx=6)
        ttk.Button(nav, text="Next unreviewed",
                   command=self._next_unreviewed).pack(side=tk.LEFT)
        self.queue_lbl = tk.Label(side, text="", fg='#8a5a00', anchor='w',
                                  justify='left')
        self.queue_lbl.pack(fill='x', pady=(8, 0))
        self.status = tk.Label(self.root, text="idle", bd=1, relief=tk.SUNKEN,
                               anchor='w')
        self.status.pack(side=tk.BOTTOM, fill='x')

        for key, fn in (('<Key-1>', lambda e: self._pick_k(0)),
                        ('<Key-2>', lambda e: self._pick_k(1)),
                        ('<Key-3>', lambda e: self._pick_k(2)),
                        ('<Key-r>', lambda e: self._reject()),
                        ('<Return>', lambda e: self._accept_next()),
                        ('<Left>', lambda e: self._step(-1)),
                        ('<Right>', lambda e: self._step(+1))):
            self.root.bind(key, fn)

    # ---------------- run selection ----------------
    def _list_runs(self, parent):
        try:
            names = sorted((n for n in os.listdir(parent)
                            if n.startswith('SLDEA_') and
                            os.path.isdir(os.path.join(parent, n))),
                           reverse=True)
        except OSError:
            return []
        out = []
        for n in names:
            done = ''
            try:
                with open(os.path.join(parent, n, 'data.csv')) as f:
                    if 'active_area_px' in f.readline() and any(
                            line.split(',')[10:11] != ['']
                            and line.split(',')[10].strip()
                            for line in f):
                        done = '  ✓ processed'
            except OSError:
                pass
            out.append(n + done)
        return out

    def _populate_runs(self, path):
        """Accept a run dir (has data.csv) or a parent full of runs."""
        path = os.path.abspath(path)
        if os.path.isfile(os.path.join(path, 'data.csv')):
            self.parent = os.path.dirname(path)
            preselect = os.path.basename(path)
        else:
            self.parent = path
            preselect = None
        runs = self._list_runs(self.parent)
        self.run_box['values'] = runs
        if runs:
            want = 0
            if preselect:
                for i, r in enumerate(runs):
                    if r.split('  ')[0] == preselect:
                        want = i
                        break
            self.run_box.current(want)
            self._pick_run()
        else:
            self.status.config(text=f"no SLDEA_* runs in {self.parent}")

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self.parent or DEFAULT_PARENT)
        if d:
            self._populate_runs(d)

    def _pick_run(self):
        name = (self.run_box.get() or '').split('  ')[0]
        if not name:
            return
        self.rundir = os.path.join(self.parent, name)
        try:
            self.run = se.load_run(self.rundir)
        except Exception as e:
            messagebox.showerror("Run", f"Cannot read {name}: {e}")
            self.run = None
            return
        self.settings = se.load_settings(self.rundir)
        self.frame_rows = [i for i, r in enumerate(self.run['rows'])
                           if (r.get('frame_file') or '').strip()]
        self.cands_all, self.results, self.flags = {}, {}, {}
        self.auto_idx = set()
        self.pos = 0
        self.save_btn.config(state='disabled')
        n = len(self.frame_rows)
        self.status.config(
            text=f"{name}: {len(self.run['rows'])} snapshots, {n} frames "
                 f"on disk — Detect to trace edges "
                 f"(diam {self.settings['diam_mm']:g} mm)")
        self.canvas.delete('all')
        self.info.config(text=f"{name}\n{n} frames ready")

    # ---------------- detection ----------------
    def detect(self):
        if not self.run:
            messagebox.showinfo("Detect", "Pick a run first")
            return
        if not self.frame_rows:
            messagebox.showinfo(
                "Detect", "This run has no frames on disk (the camera was "
                "busy or dry-run frames were skipped).")
            return
        self.detect_btn.config(state='disabled')
        self.status.config(text="detecting…")
        threading.Thread(target=self._detect_worker, daemon=True).start()
        self.root.after(100, self._poll_detect)

    def _base_gray(self):
        for i in self.frame_rows:
            if self.run['rows'][i].get('tag') == 'baseline':
                return se.load_gray(se.frame_path(self.run,
                                                  self.run['rows'][i]))
        return None

    def _detect_worker(self):
        base = self._base_gray()
        for i in self.frame_rows:
            img = se.load_gray(se.frame_path(self.run, self.run['rows'][i]))
            cands = [] if img is None else se.candidates(
                base, img, self.settings)
            self._detq.put((i, cands))
        self._detq.put(None)

    def _poll_detect(self):
        done = False
        while True:
            try:
                item = self._detq.get_nowait()
            except _queue.Empty:
                break
            if item is None:
                done = True
                break
            i, cands = item
            self.cands_all[i] = cands
            self.status.config(
                text=f"detecting… {len(self.cands_all)}/{len(self.frame_rows)}")
        if done:
            self._finish_detect()
        else:
            self.root.after(100, self._poll_detect)

    def detect_all_sync(self):
        """Synchronous detection (used by --auto tests and headless runs)."""
        base = self._base_gray()
        for i in self.frame_rows:
            img = se.load_gray(se.frame_path(self.run, self.run['rows'][i]))
            self.cands_all[i] = [] if img is None else se.candidates(
                base, img, self.settings)
        self._finish_detect()

    def _finish_detect(self):
        for i in self.frame_rows:
            cands = self.cands_all.get(i, [])
            if cands and not se.needs_review(cands, self.settings):
                self.results[i] = dict(cands[0])
                self.auto_idx.add(i)
        self._recount()
        self.detect_btn.config(state='normal')
        self.save_btn.config(state='normal')
        q = self._queue_list()
        self.status.config(
            text=f"detected {len(self.frame_rows)} frames: "
                 f"{len(self.auto_idx)} auto-accepted, {len(q)} need review")
        self.pos = self.frame_rows.index(q[0]) if q else 0
        self._show()

    def _queue_list(self):
        return [i for i in self.frame_rows if i not in self.results]

    def _recount(self):
        areas = {i: r['area_px'] for i, r in self.results.items() if r}
        self.flags = se.breakdown_flags(self.run['rows'], areas, self.settings)

    # ---------------- review ----------------
    def _current(self):
        return self.frame_rows[self.pos] if self.frame_rows else None

    def _show(self):
        i = self._current()
        if i is None:
            return
        row = self.run['rows'][i]
        cands = self.cands_all.get(i, [])
        chosen = self.results.get(i)
        # info panel
        state = ('auto-accepted' if i in self.auto_idx else
                 'accepted' if chosen else
                 'REJECTED' if i in self.results else 'needs review')
        txt = (f"frame {self.pos+1}/{len(self.frame_rows)}   step "
               f"{row.get('step')} [{row.get('tag')}]\n"
               f"nominal {row.get('nominal_kV')} kV   "
               f"measured {row.get('measured_kV') or '—'} kV   "
               f"{row.get('measured_uA') or '—'} µA\n"
               f"state: {state}")
        if i in self.flags:
            txt += f"\n⚠ {self.flags[i]}"
        self.info.config(text=txt)
        for k in range(3):
            if k < len(cands):
                c = cands[k]
                self.cand_radios[k].config(
                    text=f"{CAND_KEYS[k]}: {c['method']}  "
                         f"{c['area_px']:.0f} px²  conf {c['conf']:.2f}",
                    state='normal')
            else:
                self.cand_radios[k].config(text=f"{CAND_KEYS[k]}: —",
                                           state='disabled')
        sel = 0
        if chosen:
            for k, c in enumerate(cands):
                if c['method'] == chosen['method']:
                    sel = k
                    break
        self.cand_var.set(sel)
        q = self._queue_list()
        self.queue_lbl.config(
            text=f"review queue: {len(q)} frame(s) left"
                 + (f"\nbreakdown-flagged: {len(self.flags)}" if self.flags
                    else ""))
        self._draw(i, cands, chosen)

    def _draw(self, i, cands, chosen):
        from PIL import Image, ImageDraw, ImageTk
        import numpy as np
        path = se.frame_path(self.run, self.run['rows'][i])
        try:
            img = Image.open(path).convert('RGB')
        except Exception:
            self.canvas.delete('all')
            return
        scale = VIEW_W / img.width
        img = img.resize((VIEW_W, int(img.height * scale)))
        dr = ImageDraw.Draw(img)
        for k, c in enumerate(cands):
            pts = [(float(x) * scale, float(y) * scale)
                   for x, y in np.asarray(c['contour'])]
            wdt = 4 if (chosen and c['method'] == chosen['method']) else 2
            if len(pts) > 2:
                dr.line(pts + [pts[0]], fill=CAND_COLORS[k], width=wdt)
        self._photo = ImageTk.PhotoImage(img)
        self.canvas.delete('all')
        self.canvas.config(width=img.width, height=img.height)
        self.canvas.create_image(0, 0, anchor='nw', image=self._photo)

    def _pick_k(self, k):
        i = self._current()
        if i is None or k >= len(self.cands_all.get(i, [])):
            return
        self.cand_var.set(k)
        self._choose_current()

    def _choose_current(self):
        i = self._current()
        cands = self.cands_all.get(i, [])
        k = self.cand_var.get()
        if i is None or k >= len(cands):
            return
        chosen = dict(cands[k])
        chosen['chosen_by'] = 'user'
        self.results[i] = chosen
        self.auto_idx.discard(i)
        self._recount()
        self._show()

    def _accept_next(self):
        i = self._current()
        if i is not None and i not in self.results:
            self._choose_current()      # accept the selected (default best)
        self._next_unreviewed()

    def _reject(self):
        i = self._current()
        if i is None:
            return
        self.results[i] = None
        self.auto_idx.discard(i)
        self._recount()
        self._next_unreviewed()

    def _step(self, d):
        if not self.frame_rows:
            return
        self.pos = max(0, min(len(self.frame_rows) - 1, self.pos + d))
        self._show()

    def _next_unreviewed(self):
        q = self._queue_list()
        if q:
            self.pos = self.frame_rows.index(q[0])
            self._show()
        else:
            self._show()
            self.status.config(text="review complete — Save to data.csv when "
                                    "ready")

    # ---------------- save ----------------
    def save(self):
        if not self.run:
            return
        q = self._queue_list()
        accepted = sum(1 for r in self.results.values() if r)
        rejected = sum(1 for r in self.results.values() if r is None)
        msg = (f"Write results into this run's data.csv?\n\n"
               f"accepted: {accepted}  (auto {len(self.auto_idx)})\n"
               f"rejected: {rejected}\n"
               f"unreviewed (left blank): {len(q)}\n"
               f"breakdown-flagged: {len(self.flags)}\n\n"
               f"A backup is kept as data.csv.bak; an area-vs-voltage plot "
               f"and outline overlays are saved beside it.")
        if not messagebox.askyesno("Save results", msg):
            return
        scale = se.mm_per_px(self.results, self.run['rows'], self.settings)
        se.apply_results(self.run['rows'], self.results, scale, self.flags)
        se.write_back(self.rundir, self.run)
        try:
            self._save_plot(scale)
            self._save_overlays()
        except Exception as e:
            self.status.config(text=f"saved CSV; plot/overlays failed: {e}")
            return
        self.status.config(
            text=f"saved — data.csv updated (scale "
                 f"{scale:.5f} mm/px)" if scale else
                 "saved — data.csv updated (no mm scale: no baseline accept)")

    def _save_plot(self, scale):
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        xs, ys, tags = [], [], []
        for i, r in self.results.items():
            if not r:
                continue
            row = self.run['rows'][i]
            try:
                kv = float(row.get('nominal_kV') or '')
            except ValueError:
                continue
            area = r['area_px'] * (scale * scale) if scale else r['area_px']
            xs.append(kv)
            ys.append(area)
            tags.append(row.get('tag'))
        if not xs:
            return
        fig, ax = plt.subplots(figsize=(8, 5))
        for tag, mk in (('post', 'o'), ('pre', 's'), ('baseline', '^')):
            px = [x for x, t in zip(xs, tags) if t == tag]
            py = [y for y, t in zip(ys, tags) if t == tag]
            if px:
                ax.plot(px, py, mk, label=tag, alpha=0.8)
        ax.set_xlabel('nominal voltage (kV)')
        ax.set_ylabel('active area (mm²)' if scale else 'active area (px²)')
        ax.set_title(os.path.basename(self.rundir) + ' — active area vs voltage')
        ax.grid(alpha=0.3)
        ax.legend()
        fig.tight_layout()
        fig.savefig(os.path.join(self.rundir, 'area_vs_voltage.png'), dpi=110)
        plt.close(fig)

    def _save_overlays(self):
        import cv2
        import numpy as np
        outdir = os.path.join(self.rundir, 'overlays')
        os.makedirs(outdir, exist_ok=True)
        for i, r in self.results.items():
            if not r:
                continue
            row = self.run['rows'][i]
            path = se.frame_path(self.run, row)
            img = cv2.imread(path)
            if img is None:
                continue
            cv2.polylines(img, [np.asarray(r['contour'], np.int32)], True,
                          (80, 200, 0), 2)
            cv2.imwrite(os.path.join(outdir, os.path.basename(path)), img)

    # ---------------- advanced settings ----------------
    def _advanced(self):
        win = tk.Toplevel(self.root)
        win.title("Edge detection settings")
        entries = {}
        tips = {'diam_mm': "DEA resting active-area diameter (mm) — sets the "
                           "px→mm scale via the baseline detection",
                'blur_px': "Gaussian blur kernel (odd px)",
                'diff_thresh': "fixed diff threshold; 0 = auto (Otsu)",
                'min_circ': "drop candidates less circular than this (0–1)",
                'accept_conf': "auto-accept at/above this confidence (0–1)",
                'spread_pct': "candidate area disagreement (%) forcing review",
                'breakdown_ua': "flag breakdown above this Trek current (µA)",
                'area_jump_pct': "flag breakdown on area collapse (%) while "
                                 "voltage rises"}
        for r, key in enumerate(se.DEFAULT_SETTINGS):
            ttk.Label(win, text=f"{key}:").grid(row=r, column=0, sticky='e',
                                                padx=6, pady=3)
            e = ttk.Entry(win, width=10)
            e.insert(0, f"{self.settings[key]:g}")
            e.grid(row=r, column=1, padx=6)
            ttk.Label(win, text=tips.get(key, ''), foreground='#666',
                      wraplength=380, justify='left').grid(
                row=r, column=2, sticky='w', padx=6)
            entries[key] = e

        def apply(save=False):
            try:
                for key, e in entries.items():
                    cast = type(se.DEFAULT_SETTINGS[key])
                    self.settings[key] = cast(float(e.get()))
            except ValueError as err:
                messagebox.showerror("Settings", str(err), parent=win)
                return
            if save and self.rundir:
                se.save_settings(self.rundir, self.settings)
            win.destroy()
            self.status.config(
                text="settings applied" + (" + saved to setup.txt" if save
                                           else "") + " — re-run Detect")

        bf = ttk.Frame(win)
        bf.grid(row=len(se.DEFAULT_SETTINGS), column=0, columnspan=3, pady=8)
        ttk.Button(bf, text="Apply", command=apply).pack(side=tk.LEFT, padx=4)
        ttk.Button(bf, text="Apply + Save to setup.txt",
                   command=lambda: apply(True)).pack(side=tk.LEFT, padx=4)


def main():
    args = [a for a in sys.argv[1:]]
    auto = '--auto' in args
    path = next((a for a in args if not a.startswith('--')), None)
    root = tk.Tk()
    EdgeReviewApp(root, path=path, auto=auto)
    root.mainloop()


if __name__ == '__main__':
    main()
