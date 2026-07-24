#!/usr/bin/env python3
"""Interactive live tuner for the SLDEA edge-detection parameters.

A low-weight slider screen: it picks three frames from a run -- baseline,
mid-run and late -- and redraws the current algorithm's outlines live as you
drag the thresholds. When the outlines look right, Save writes the values
into the run's setup.txt so Edge Review (and any later auto-process) uses
exactly what you tuned.

It reuses sldea_edge.candidates() unchanged, so the outlines here ARE what
the pipeline produces -- this is a viewfinder on the real algorithm, not a
reimplementation.

    python sldea_tuner.py [RUNDIR]              # or newest under the default
    python sldea_tuner.py --selftest OUT.png    # headless render, no window

Also doubles as the labelling front-end for the ML route: tune until the
masks are right, then the saved outlines are weak labels to correct/export.
"""
import os
import sys

import numpy as np

import sldea_edge as se

DEFAULT_DIR = os.environ.get(
    'SCPI_SLDEA_DIR', '/mnt/shareDrive/robot_incubator/SLDEA_data')

# (key, label, lo, hi, resolution, is_int) -- the image-affecting settings
SLIDERS = [
    ('blur_px',       'Blur (px, odd)',            1,   21,  2,    True),
    ('diff_thresh',   'Diff thresh (0=auto Otsu)', 0,   60,  1,    True),
    ('min_diff',      'No-change gate (diff p99)', 0,   40,  1,    False),
    ('min_solidity',  'Min fill of outline',       0.0, 1.0, 0.01, False),
    ('roi_frac',      'Search ROI fraction',       0.3, 1.0, 0.01, False),
    ('electrode_lum', 'Electrode mask (0=off)',    0,   255, 1,    False),
    ('wrinkle_ratio', 'Wrinkle-mode ratio',        1.0, 3.0, 0.05, False),
]
PANEL_COLORS = ['#00e676', '#40c4ff', '#ff9100']   # best, 2nd, 3rd


def _fkv(row):
    try:
        return float(row.get('nominal_kV') or row.get('nominal_kv') or 'nan')
    except (TypeError, ValueError):
        return float('nan')


def choose_indices(rows):
    """Pick (label, row_index) for baseline / mid-run / late frames.

    Returns 1-3 unique pairs, in that order. Baseline is the tagged baseline
    row (else row 0); late is the highest-nominal-kV content frame; mid-run
    is the content frame nearest the voltage midpoint (or the median index
    when voltages are missing)."""
    n = len(rows)
    if n == 0:
        return []
    base = next((i for i, r in enumerate(rows)
                 if (r.get('tag') or '') == 'baseline'), 0)
    content = [i for i, r in enumerate(rows)
               if i != base and (r.get('frame_file') or '').strip()]
    if not content:
        content = [i for i in range(n) if i != base] or [base]

    def kv_key(i):
        kv = _fkv(rows[i])
        return (-1e9 if np.isnan(kv) else kv, i)

    late = max(content, key=kv_key)
    kv_b, kv_l = _fkv(rows[base]), _fkv(rows[late])
    if not np.isnan(kv_b) and not np.isnan(kv_l) and kv_l > kv_b:
        target = 0.5 * (kv_b + kv_l)
        mid = min(content, key=lambda i: abs(_fkv(rows[i]) - target)
                  if not np.isnan(_fkv(rows[i])) else 1e9)
    else:
        mid = content[len(content) // 2]
    # if mid collided, try the median-index content frame not already used
    if mid in (base, late):
        spare = [i for i in content if i not in (base, late)]
        if spare:
            mid = spare[len(spare) // 2]

    out = []
    for label, idx in (('baseline', base), ('mid-run', mid), ('late', late)):
        if idx not in [o[1] for o in out]:
            out.append((label, idx))
    return out


def load_panels(run, picks):
    """[(label, idx)] -> [{label, idx, row, gray}] with full-res gray frames
    actually loadable (skips ones whose image is missing)."""
    panels = []
    for label, idx in picks:
        row = run['rows'][idx]
        gray = se.load_gray(se.frame_path(run, row))
        if gray is not None:
            panels.append({'label': label, 'idx': idx, 'row': row,
                           'gray': gray})
    return panels


def detect_panels(panels, base_gray, settings, rows):
    """Run candidates() for each panel; return (results_by_idx, cands_by_idx,
    mm_scale). results holds the best candidate (or None) per row index so
    the shared mm_per_px picks the same reference as the real pipeline."""
    results, cands = {}, {}
    for p in panels:
        cl = se.candidates(base_gray, p['gray'], settings)
        cands[p['idx']] = cl
        results[p['idx']] = cl[0] if cl else None
    scale = se.mm_per_px(results, rows, settings)
    return results, cands, scale


def _panel_title(panel, cands, scale):
    row = panel['row']
    kv = row.get('nominal_kV') or '?'
    head = f"{panel['label']}  ·  {kv} kV"
    if not cands:
        return head + "\nno change (gated)"
    c = cands[0]
    area = c['area_px']
    mm2 = f"{area * scale * scale:.1f} mm²  ·  " if scale else ""
    rev = "REVIEW" if se.needs_review(cands, {**se.DEFAULT_SETTINGS}) else "ok"
    return (head + f"  ·  {c['method']}\n{mm2}{area:.0f} px²  ·  "
            f"fill {c['solidity']:.2f}  ·  wrinkle {c.get('wrinkle', 0):.2f}"
            f"  ·  conf {c['conf']:.2f}  ·  {rev}")


def render(ax, panel, cands, scale, fill=True):
    """Draw one panel: the frame + candidate outlines (best thick), optional
    translucent fill of the chosen region, and a stats title. Reuses a
    persistent imshow so live updates only touch the overlays."""
    im = ax._tuner_im if hasattr(ax, '_tuner_im') else None
    if im is None:
        ax._tuner_im = ax.imshow(panel['gray'], cmap='gray', vmin=0, vmax=255)
        ax.set_xticks([])
        ax.set_yticks([])
    else:
        im.set_data(panel['gray'])
    # clear previous overlay artists
    for art in list(ax.lines) + list(ax.collections) + list(ax.patches):
        art.remove()
    for k, c in enumerate(cands):
        pts = np.asarray(c['contour'], float)
        if len(pts) < 3:
            continue
        xs = np.append(pts[:, 0], pts[0, 0])
        ys = np.append(pts[:, 1], pts[0, 1])
        col = PANEL_COLORS[min(k, len(PANEL_COLORS) - 1)]
        ax.plot(xs, ys, color=col, lw=2.0 if k == 0 else 1.0)
        if k == 0 and fill:
            ax.fill(xs, ys, color=col, alpha=0.18)
    ax.set_title(_panel_title(panel, cands, scale), fontsize=8.5,
                 loc='left')


def build_settings(rundir):
    return se.load_settings(rundir)


# ---------------------------------------------------------------------------
# headless self-test: synthesise a tiny run, render, save a PNG (no window)
# ---------------------------------------------------------------------------

def _selftest(out_png):
    import csv
    import tempfile

    import cv2
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    d = tempfile.mkdtemp(prefix='tuner_selftest_')
    frames = os.path.join(d, 'frames')
    os.makedirs(frames)
    cols = ['snapshot', 'step', 'tag', 'nominal_kV', 'control_V',
            'measured_kV', 'measured_uA', 't_planned_s', 'timestamp',
            'frame_file', 'active_area_px', 'active_area_mm2',
            'active_diam_mm', 'notes']

    def disc(r, level, texture=False):
        img = np.full((240, 320), 90.0, np.float32)
        yy, xx = np.mgrid[0:240, 0:320]
        m = (xx - 160) ** 2 + (yy - 120) ** 2 <= r * r
        img[m] += level + (30 * ((xx[m] // 4) % 2) if texture else 0)
        return np.clip(img, 0, 255).astype(np.uint8)

    rows = []
    specs = [('baseline', 0.0, disc(0, 0)),
             ('post-ramp', 3.0, disc(45, 30)),
             ('post-ramp', 6.0, disc(70, 40, texture=True))]
    for k, (tag, kv, im) in enumerate(specs):
        fn = f'SLDEA_s{k:02d}_{kv:05.2f}kV_{tag}.png'
        cv2.imwrite(os.path.join(frames, fn), im)
        rows.append({**{c: '' for c in cols}, 'tag': tag, 'nominal_kV': kv,
                     'frame_file': fn, 'step': k})
    with open(os.path.join(d, 'data.csv'), 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    run = se.load_run(d)
    picks = choose_indices(run['rows'])
    assert [p[0] for p in picks] == ['baseline', 'mid-run', 'late'], picks
    panels = load_panels(run, picks)
    assert len(panels) == 3, panels
    settings = build_settings(d)
    base_gray = panels[0]['gray']
    _, cands, scale = detect_panels(panels, base_gray, settings,
                                    run['rows'])
    assert cands[panels[2]['idx']], "late frame should detect a region"

    fig, axs = plt.subplots(1, 3, figsize=(15, 5))
    for ax, p in zip(axs, panels):
        render(ax, p, cands[p['idx']], scale)
    fig.tight_layout()
    fig.savefig(out_png, dpi=90)
    print(f"selftest OK -> {out_png}  (picks {[(l, i) for l, i in picks]}, "
          f"late area {cands[panels[2]['idx']][0]['area_px']:.0f} px)")


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

def _newest_run(root):
    try:
        subs = [os.path.join(root, n) for n in os.listdir(root)
                if n.startswith('SLDEA_') and
                os.path.isdir(os.path.join(root, n))]
    except OSError:
        return None
    subs = [s for s in subs if os.path.exists(os.path.join(s, 'data.csv'))]
    return max(subs, key=os.path.getmtime) if subs else None


def main(argv):
    args = [a for a in argv if not a.startswith('--')]
    if '--selftest' in argv:
        _selftest(args[0] if args else 'tuner_selftest.png')
        return 0

    rundir = args[0] if args else _newest_run(DEFAULT_DIR)
    if not rundir or not os.path.exists(os.path.join(rundir, 'data.csv')):
        print(f"no run found (looked in {DEFAULT_DIR}); pass a RUNDIR")
        return 2

    import tkinter as tk
    from tkinter import messagebox, ttk
    import matplotlib
    matplotlib.use('TkAgg')
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

    run = se.load_run(rundir)
    panels = load_panels(run, choose_indices(run['rows']))
    if not panels:
        print("no loadable frames in", rundir)
        return 2
    settings = build_settings(rundir)
    base_gray = panels[0]['gray']

    root = tk.Tk()
    root.title(f"SLDEA edge tuner — {os.path.basename(rundir)}")
    fig, axs = plt.subplots(1, len(panels), figsize=(5 * len(panels), 5))
    if len(panels) == 1:
        axs = [axs]
    canvas = FigureCanvasTkAgg(fig, master=root)
    canvas.get_tk_widget().pack(fill='both', expand=True)

    ctl = ttk.Frame(root, padding=8)
    ctl.pack(fill='x')
    scales, vallabels = {}, {}
    fill_var = tk.BooleanVar(value=True)
    norm_var = tk.BooleanVar(value=bool(settings.get('norm_bg', 1)))
    job = {'id': None}

    def recompute():
        job['id'] = None
        settings['norm_bg'] = 1 if norm_var.get() else 0
        _, cands, scale = detect_panels(panels, base_gray, settings,
                                        run['rows'])
        for ax, p in zip(axs, panels):
            render(ax, p, cands[p['idx']], scale, fill=fill_var.get())
        fig.tight_layout()
        canvas.draw_idle()

    def schedule(*_):
        # debounce: candidates() on 3 frames is ~0.2 s, so coalesce drags
        if job['id'] is not None:
            root.after_cancel(job['id'])
        job['id'] = root.after(120, recompute)

    def on_slider(key, is_int, lbl):
        def cb(v):
            val = int(round(float(v))) if is_int else round(float(v), 3)
            if key == 'blur_px':
                val = val | 1                     # keep odd
            settings[key] = val
            lbl.config(text=(f"{val}" if is_int else f"{val:g}"))
            schedule()
        return cb

    for r, (key, label, lo, hi, res, is_int) in enumerate(SLIDERS):
        ttk.Label(ctl, text=label, width=24, anchor='e').grid(
            row=r, column=0, sticky='e', padx=(0, 6), pady=1)
        cur = float(settings.get(key, se.DEFAULT_SETTINGS.get(key, lo)))
        vlab = ttk.Label(ctl, width=6,
                         text=(f"{int(cur)}" if is_int else f"{cur:g}"))
        vlab.grid(row=r, column=2, padx=6)
        sc = tk.Scale(ctl, from_=lo, to=hi, resolution=res,
                      orient='horizontal', showvalue=False, length=360,
                      command=on_slider(key, is_int, vlab))
        sc.set(cur)
        sc.grid(row=r, column=1, sticky='ew')
        scales[key] = sc
        vallabels[key] = vlab
    ctl.columnconfigure(1, weight=1)

    opts = ttk.Frame(root, padding=(8, 0))
    opts.pack(fill='x')
    ttk.Checkbutton(opts, text="Normalize brightness to baseline (norm_bg)",
                    variable=norm_var, command=schedule).pack(side='left')
    ttk.Checkbutton(opts, text="Shade detected region", variable=fill_var,
                    command=schedule).pack(side='left', padx=12)

    def do_save():
        se.save_settings(rundir, settings)
        messagebox.showinfo(
            "Saved", "Tuned settings written to setup.txt.\n\nEdge Review "
            "and auto-process on this run will now use them.")

    def do_reset():
        for key, _, lo, hi, res, is_int in SLIDERS:
            dv = float(se.DEFAULT_SETTINGS.get(key, lo))
            settings[key] = int(dv) if is_int else dv
            scales[key].set(dv)
            vallabels[key].config(text=(f"{int(dv)}" if is_int else f"{dv:g}"))
        norm_var.set(bool(se.DEFAULT_SETTINGS.get('norm_bg', 1)))
        schedule()

    bar = ttk.Frame(root, padding=8)
    bar.pack(fill='x')
    tk.Button(bar, text="💾 Save to setup.txt", command=do_save,
              font=('TkDefaultFont', 9, 'bold')).pack(side='left')
    ttk.Button(bar, text="Reset to defaults", command=do_reset).pack(
        side='left', padx=8)
    ttk.Label(bar, foreground='#666',
              text="outlines here = exactly what Edge Review will produce"
              ).pack(side='right')

    recompute()
    root.mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
