#!/usr/bin/env python3
"""SLDEA edge detection: trace the active-area disc in run frames.

Pure analysis for the offline review GUI (sldea_edge_gui.py) -- no Tk in
here, so everything is unit-testable on synthetic images. A "run" is the
directory the Digital Multitool's SLDEA tab wrote:

    SLDEA_<ts>/setup.txt + data.csv + frames/SLDEA_sNN_XX.XXkV_tag.png

Approach: difference-imaging against the 0 kV baseline frame (robust for the
low-contrast grey disc), plus an independent Hough-circle candidate. Each
frame yields up to 3 candidate outlines with a confidence score; frames whose
best candidate is weak (or whose candidates disagree) are queued for the
human-in-the-loop pass in the GUI. Breakdown heuristics flag suspect steps:
a Trek current spike and an area collapse while voltage rises.

Areas are stored in px^2 and converted to mm^2 using the DEA's nominal
resting diameter (default 16 mm) against the baseline detection.

Headless self-test: .venv/bin/python tests/test_sldea_edge.py
"""
import csv
import os
import re
import shutil

import numpy as np

# Settings persisted per-run in setup.txt (section replaced on save).
EDGE_HDR = '--- Edge Detection settings (SLDEA Edge Review) ---'
DEFAULT_SETTINGS = {
    'diam_mm': 16.0,        # DEA nominal resting active-area diameter
    'blur_px': 5,           # Gaussian blur kernel (odd)
    'diff_thresh': 0,       # fixed diff threshold; 0 = auto (Otsu)
    'min_circ': 0.55,       # candidates below this circularity are dropped
    'accept_conf': 0.75,    # auto-accept at/above this confidence
    'spread_pct': 12.0,     # candidate area disagreement that forces review
    'breakdown_ua': 50.0,   # Trek current above this flags breakdown
    'area_jump_pct': 35.0,  # area collapse (V rising) that flags breakdown
}
_NUM = r'[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?'


# ---------------------------------------------------------------------------
# settings <-> setup.txt
# ---------------------------------------------------------------------------

def load_settings(rundir):
    """DEFAULT_SETTINGS overlaid with any saved section in setup.txt; also
    picks up the run's 'DEA nominal diameter: X mm' line for diam_mm."""
    s = dict(DEFAULT_SETTINGS)
    path = os.path.join(rundir, 'setup.txt')
    try:
        text = open(path).read()
    except OSError:
        return s
    m = re.search(r'DEA nominal diameter:\s*(' + _NUM + r')\s*mm', text)
    if m:
        s['diam_mm'] = float(m.group(1))
    if EDGE_HDR in text:
        for line in text.split(EDGE_HDR, 1)[1].splitlines():
            mm = re.match(r'\s*([a-z_]+)\s*:\s*(' + _NUM + r')\s*$', line)
            if mm and mm.group(1) in s:
                s[mm.group(1)] = type(DEFAULT_SETTINGS[mm.group(1)])(
                    float(mm.group(2)))
    return s


def save_settings(rundir, settings):
    """Append/replace the edge-settings section in the run's setup.txt."""
    path = os.path.join(rundir, 'setup.txt')
    try:
        text = open(path).read()
    except OSError:
        text = ''
    if EDGE_HDR in text:
        text = text.split(EDGE_HDR, 1)[0].rstrip() + '\n'
    lines = [EDGE_HDR] + [f"{k}: {settings[k]:g}" for k in DEFAULT_SETTINGS]
    with open(path, 'w') as f:
        f.write(text.rstrip() + '\n\n' + '\n'.join(lines) + '\n')
    return path


# ---------------------------------------------------------------------------
# run loading
# ---------------------------------------------------------------------------

def load_run(rundir):
    """-> {'rows': [dict...], 'columns': [...], 'frames_dir': path}.
    Rows are data.csv rows in order (all columns kept as strings)."""
    csv_path = os.path.join(rundir, 'data.csv')
    with open(csv_path, newline='') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        columns = list(reader.fieldnames or [])
    return {'rows': rows, 'columns': columns,
            'frames_dir': os.path.join(rundir, 'frames')}


def frame_path(run, row):
    name = (row.get('frame_file') or '').strip()
    return os.path.join(run['frames_dir'], name) if name else None


def load_gray(path):
    """Frame as float32 grayscale (None if unreadable)."""
    import cv2
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    return None if img is None else img.astype(np.float32)


# ---------------------------------------------------------------------------
# candidate detection
# ---------------------------------------------------------------------------

def _contour_candidate(mask, method):
    """Largest external contour of a binary mask -> candidate dict or None."""
    import cv2
    cnts, _ = cv2.findContours(mask.astype(np.uint8),
                               cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    c = max(cnts, key=cv2.contourArea)
    area = float(cv2.contourArea(c))
    if area < 50:
        return None
    per = float(cv2.arcLength(c, True)) or 1.0
    circ = min(1.0, 4.0 * np.pi * area / (per * per))
    (cx, cy), _r = cv2.minEnclosingCircle(c)
    return {'method': method, 'area_px': area, 'circ': circ,
            'cx': float(cx), 'cy': float(cy),
            'diam_px': 2.0 * np.sqrt(area / np.pi),
            'contour': c.reshape(-1, 2)}


# Detection runs on a downscaled copy: full-res HoughCircles on a 1080p
# frame takes ~minutes (accumulator blow-up on noise) while the disc needs
# nowhere near that resolution. Results are rescaled to full-res px.
DETECT_MAX_W = 640


def candidates(base_gray, img_gray, settings):
    """Up to 3 candidate outlines for the active area, best first.

    a) diff-otsu  : |img - baseline| -> blur -> Otsu threshold -> contour
    b) diff-fixed : same, fixed threshold (diff_thresh, or 0.6*Otsu when auto)
    c) hough      : CLAHE(img) -> HoughCircles, circle nearest frame centre

    Confidence = 0.6*circularity + 0.4*cross-method area agreement. Frames
    wider than DETECT_MAX_W are detected at reduced scale (bench: 1080p
    full-res Hough took ~100 s/frame; downscaled is sub-second) and every
    px quantity is scaled back to full resolution.
    """
    import cv2
    h0, w0 = img_gray.shape
    f = 1.0
    if w0 > DETECT_MAX_W:
        f = DETECT_MAX_W / float(w0)
        size = (DETECT_MAX_W, max(1, int(round(h0 * f))))
        img_gray = cv2.resize(img_gray, size, interpolation=cv2.INTER_AREA)
        if base_gray is not None:
            base_gray = cv2.resize(base_gray, size,
                                   interpolation=cv2.INTER_AREA)
    k = int(settings.get('blur_px', 5)) | 1
    diff = cv2.absdiff(img_gray, base_gray).astype(np.uint8) \
        if base_gray is not None else img_gray.astype(np.uint8)
    diff = cv2.GaussianBlur(diff, (k, k), 0)
    otsu_t, otsu_mask = cv2.threshold(diff, 0, 255,
                                      cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    out = []
    c1 = _contour_candidate(otsu_mask, 'diff-otsu')
    if c1:
        out.append(c1)
    fixed = float(settings.get('diff_thresh') or 0) or max(4.0, 0.6 * otsu_t)
    _t, m2 = cv2.threshold(diff, fixed, 255, cv2.THRESH_BINARY)
    c2 = _contour_candidate(m2, 'diff-fixed')
    if c2:
        out.append(c2)
    try:
        h, w = img_gray.shape
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)) \
            .apply(img_gray.astype(np.uint8))
        cir = cv2.HoughCircles(cv2.GaussianBlur(clahe, (k, k), 0),
                               cv2.HOUGH_GRADIENT, dp=1.5, minDist=w,
                               param1=80, param2=35,
                               minRadius=int(0.05 * min(h, w)),
                               maxRadius=int(0.48 * min(h, w)))
        if cir is not None:
            best = min(cir[0], key=lambda c: (c[0] - w / 2) ** 2
                       + (c[1] - h / 2) ** 2)
            cx, cy, r = [float(v) for v in best]
            theta = np.linspace(0, 2 * np.pi, 90)
            ring = np.stack([cx + r * np.cos(theta),
                             cy + r * np.sin(theta)], axis=1).astype(int)
            out.append({'method': 'hough', 'area_px': float(np.pi * r * r),
                        'circ': 0.85,       # by construction; weight it below
                        'cx': cx, 'cy': cy, 'diam_px': 2 * r, 'contour': ring})
    except Exception:
        pass
    out = [c for c in out if c['circ'] >= float(settings.get('min_circ', 0))]
    if not out:
        return []
    if f != 1.0:                    # rescale every px quantity to full-res
        inv = 1.0 / f
        for c in out:
            c['area_px'] *= inv * inv
            c['diam_px'] *= inv
            c['cx'] *= inv
            c['cy'] *= inv
            c['contour'] = (np.asarray(c['contour'], float) * inv).astype(int)
    areas = np.array([c['area_px'] for c in out], float)
    spread = float(areas.std() / areas.mean()) if len(out) > 1 else 0.0
    agreement = max(0.0, 1.0 - spread)
    for c in out:
        c['conf'] = round(0.6 * c['circ'] + 0.4 * agreement, 3)
        c['spread_pct'] = round(100 * spread, 1)
    out.sort(key=lambda c: c['conf'], reverse=True)
    return out[:3]


def needs_review(cands, settings):
    """True when the human should choose (weak/absent/disagreeing edges)."""
    if not cands:
        return True
    if cands[0]['conf'] < float(settings['accept_conf']):
        return True
    return cands[0]['spread_pct'] > float(settings['spread_pct'])


# ---------------------------------------------------------------------------
# breakdown heuristics
# ---------------------------------------------------------------------------

def breakdown_flags(rows, accepted_areas, settings):
    """-> {row_index: reason}. Two heuristics:
    1) current spike: measured_uA > breakdown_ua (dielectric breakdown draws
       real current through the DEA);
    2) area collapse: accepted area drops > area_jump_pct vs the previous
       accepted frame while nominal_kV did NOT decrease."""
    flags = {}
    ua_lim = float(settings['breakdown_ua'])
    jump = float(settings['area_jump_pct'])
    prev_area = prev_kv = None
    for i, row in enumerate(rows):
        try:
            ua = float(row.get('measured_uA') or '')
            if abs(ua) > ua_lim:
                flags[i] = f"breakdown? I={ua:.0f}uA > {ua_lim:g}uA"
        except ValueError:
            pass
        area = accepted_areas.get(i)
        try:
            kv = float(row.get('nominal_kV') or '')
        except ValueError:
            kv = None
        if (area and prev_area and kv is not None and prev_kv is not None
                and kv >= prev_kv
                and area < prev_area * (1.0 - jump / 100.0)):
            flags.setdefault(
                i, f"breakdown? area collapsed {100*(1-area/prev_area):.0f}%")
        if area:
            prev_area, prev_kv = area, kv
    return flags


# ---------------------------------------------------------------------------
# scale + write-back
# ---------------------------------------------------------------------------

def mm_per_px(results, rows, settings):
    """Scale from the DEA's nominal resting diameter vs the baseline (or
    first accepted) detection. None when nothing is accepted."""
    ref = None
    for i, row in enumerate(rows):
        if results.get(i) and (row.get('tag') == 'baseline'):
            ref = results[i]
            break
    if ref is None:
        for i in sorted(results):
            if results[i]:
                ref = results[i]
                break
    if not ref or not ref.get('diam_px'):
        return None
    return float(settings['diam_mm']) / float(ref['diam_px'])


def apply_results(rows, results, scale, flags):
    """Fill the active_area_* / notes columns in `rows` (in place)."""
    for i, row in enumerate(rows):
        r = results.get(i)
        if r:
            row['active_area_px'] = f"{r['area_px']:.0f}"
            if scale:
                row['active_area_mm2'] = f"{r['area_px'] * scale * scale:.3f}"
                row['active_diam_mm'] = f"{r['diam_px'] * scale:.3f}"
            note = f"edge:{r['method']} conf {r['conf']:.2f}"
            if r.get('chosen_by'):
                note += f" ({r['chosen_by']})"
        elif i in results:                 # explicitly reviewed + rejected
            row['active_area_px'] = ''
            note = 'rejected (no reliable edge)'
        else:
            note = row.get('notes') or ''
        if i in flags:
            note = (note + '; ' if note else '') + flags[i]
        row['notes'] = note
    return rows


def mark_breakdown_files(run, flags):
    """Rename every frame at/after the FIRST breakdown flag with a
    '_BREAKDOWN' suffix so the frames/ listing shows at a glance which images
    are of a broken-down DEA. Files are renamed, never deleted (they stay
    useful, e.g. as ML training data); frame_file in the rows is updated to
    match, and rows after the flag gain a 'post-breakdown' note. Idempotent.
    Returns the number of files renamed."""
    if not flags:
        return 0
    start = min(flags)
    renamed = 0
    for i, row in enumerate(run['rows']):
        if i < start:
            continue
        if i not in flags:
            note = row.get('notes') or ''
            if 'post-breakdown' not in note:
                row['notes'] = (note + '; ' if note else '') + 'post-breakdown'
        name = (row.get('frame_file') or '').strip()
        if not name or '_BREAKDOWN' in name:
            continue
        base, ext = os.path.splitext(name)
        new = base + '_BREAKDOWN' + ext
        src = os.path.join(run['frames_dir'], name)
        dst = os.path.join(run['frames_dir'], new)
        if os.path.exists(src):
            os.replace(src, dst)
            renamed += 1
        row['frame_file'] = new     # keep the CSV link valid either way
    return renamed


def write_back(rundir, run):
    """Rewrite data.csv (backup first) with the filled columns."""
    csv_path = os.path.join(rundir, 'data.csv')
    shutil.copy2(csv_path, csv_path + '.bak')
    with open(csv_path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=run['columns'])
        w.writeheader()
        for row in run['rows']:
            w.writerow(row)
    return csv_path
