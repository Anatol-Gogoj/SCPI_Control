#!/usr/bin/env python3
"""SLDEA edge detection: trace the active-area disc in run frames.

Pure analysis for the offline review GUI (sldea_edge_gui.py) -- no Tk in
here, so everything is unit-testable on synthetic images. A "run" is the
directory the Digital Multitool's SLDEA tab wrote:

    SLDEA_<ts>/setup.txt + data.csv + frames/SLDEA_sNN_XX.XXkV_tag.png

Approach: difference-imaging against the 0 kV baseline frame at three
threshold tiers; each tier's outline is the convex hull of the significant
changed patches (DEA activation reads as patchy wrinkling, and the shape may
be oblong -- no circle prior). Up to 3 candidates per frame with a confidence
score; frames whose best candidate is weak (or whose candidates disagree) are
queued for the human-in-the-loop pass in the GUI. Breakdown heuristics flag
suspect steps: a Trek current spike and an area collapse while voltage rises.

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
    'min_diff': 10.0,       # diff p99 below this = "no change vs baseline"
    'min_solidity': 0.35,   # min FILL of the traced outline (changed/hull)
    'roi_frac': 0.85,       # central search window (frame fraction)
    'accept_conf': 0.75,    # auto-accept at/above this confidence
    'spread_pct': 12.0,     # candidate area disagreement that forces review
    'breakdown_ua': 50.0,   # Trek current above this flags breakdown
    'area_jump_pct': 35.0,  # area collapse (V rising) that flags breakdown
    'wrinkle_ratio': 1.4,   # wrinkle index >= this = wrinkle-mode (active)
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

def _region_candidate(mask, method, offset=(0, 0)):
    """Changed-region outline for one threshold tier -> candidate dict.

    The DEA's activated area often reads as a PATCHY texture change
    (wrinkling/buckling under field), so the outline is the CONVEX HULL of
    all significant changed components -- not the largest blob alone (bench
    2026-07-23: wrinkled frames fragmented into low-solidity crumbs and every
    candidate was dropped). The hull is also naturally oblong-friendly.

    'solidity' here = FILL: changed px inside the hull / hull area. A real
    activation fills a decent fraction of its outline; scattered noise dots
    span a huge hull with near-zero fill."""
    import cv2
    kernel3 = np.ones((3, 3), np.uint8)
    kernel7 = np.ones((7, 7), np.uint8)
    m = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN, kernel3)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, kernel7)
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    areas = [float(cv2.contourArea(c)) for c in cnts]
    amax = max(areas)
    if amax < 80:
        return None
    keep = [c for c, a in zip(cnts, areas) if a >= max(60.0, 0.05 * amax)]
    union = float(sum(a for a in areas if a >= max(60.0, 0.05 * amax)))
    hull = cv2.convexHull(np.vstack([c.reshape(-1, 2) for c in keep]))
    hull_area = float(cv2.contourArea(hull)) or 1.0
    fill = min(1.0, union / hull_area)
    per = float(cv2.arcLength(hull, True)) or 1.0
    circ = min(1.0, 4.0 * np.pi * hull_area / (per * per))
    mom = cv2.moments(hull)
    cx = mom['m10'] / mom['m00'] if mom['m00'] else 0.0
    cy = mom['m01'] / mom['m00'] if mom['m00'] else 0.0
    pts = hull.reshape(-1, 2) + np.asarray(offset)
    return {'method': method, 'area_px': hull_area, 'circ': circ,
            'solidity': fill,
            'cx': float(cx + offset[0]), 'cy': float(cy + offset[1]),
            'diam_px': 2.0 * np.sqrt(hull_area / np.pi), 'contour': pts}


# Detection runs on a downscaled copy: full-res HoughCircles on a 1080p
# frame takes ~minutes (accumulator blow-up on noise) while the disc needs
# nowhere near that resolution. Results are rescaled to full-res px.
DETECT_MAX_W = 640


def _wrinkle_ratio(base_full, img_full, contour):
    """Wrinkle index for one outline, at FULL resolution.

    Denoise-then-texture: Gaussian blur (sigma 2.5) kills the sensor grain,
    then mean |Laplacian| inside the outline, frame vs the same region of the
    baseline. Wrinkling/buckling under field is the activated DEA state --
    the lab defines the wrinkled region AS the active area. The pre-blur is
    essential on the bench camera: raw Laplacian is dominated by pixel noise
    (which the baseline has everywhere) while the wrinkle ridges are
    multi-pixel and survive the blur (bench 2026-07-23: raw full-res ratio
    read ~0.9 on obviously wrinkled frames; blurred reads cleanly > 1).
    Cropped to the outline's bounding box, so it stays cheap."""
    import cv2
    if base_full is None:
        return 1.0
    pts = np.asarray(contour, np.int32)
    h, w = img_full.shape
    x0 = max(int(pts[:, 0].min()) - 8, 0)
    x1 = min(int(pts[:, 0].max()) + 8, w)
    y0 = max(int(pts[:, 1].min()) - 8, 0)
    y1 = min(int(pts[:, 1].max()) + 8, h)
    if x1 - x0 < 16 or y1 - y0 < 16:
        return 1.0
    mask = np.zeros((y1 - y0, x1 - x0), np.uint8)
    cv2.drawContours(mask, [pts - np.array([x0, y0])], -1, 255, -1)
    if not (mask > 0).any():
        return 1.0
    bi = cv2.GaussianBlur(img_full[y0:y1, x0:x1], (0, 0), 2.5)
    bb = cv2.GaussianBlur(base_full[y0:y1, x0:x1], (0, 0), 2.5)
    e_img = float(np.abs(cv2.Laplacian(bi, cv2.CV_32F,
                                       ksize=3))[mask > 0].mean())
    e_base = float(np.abs(cv2.Laplacian(bb, cv2.CV_32F,
                                        ksize=3))[mask > 0].mean())
    return round(min(e_img / max(e_base, 1e-3), 9.99), 2)


def candidates(base_gray, img_gray, settings):
    """Up to 3 candidate outlines for the active area, best first.

    Difference-imaging only (bench 2026-07-23: the old HoughCircles candidate
    fabricated a confident circle on EVERY frame -- it teleported around the
    image with a hard-coded high score -- and the DEA expansion is not
    necessarily circular anyway, so the circle prior is wrong):

      |img - baseline| -> blur -> central ROI -> three thresholds
      (0.6*Otsu / Otsu / 1.5*Otsu, or the fixed diff_thresh) -> morphological
      open+close -> largest contour each.

    Honest no-change gate: if the ROI diff's 99th percentile is below
    min_diff, the frame shows no detectable change vs baseline and NO
    candidates are returned (low-kV frames really look identical -- inventing
    an outline there was the old failure mode).

    Confidence = 0.4*solidity + 0.3*boundary-contrast + 0.3*cross-method
    agreement; solidity (not circularity) so slightly oblong expansions score
    fully. Frames wider than DETECT_MAX_W are detected at reduced scale and
    every px quantity is rescaled to full resolution.
    """
    import cv2
    img_full, base_full = img_gray, base_gray   # full-res kept for wrinkle
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
    # central ROI: the DEA sits mid-frame; electrode glare lives at the edges
    h, w = diff.shape
    rf = min(1.0, max(0.2, float(settings.get('roi_frac', 0.85))))
    x0 = int(w * (1 - rf) / 2)
    y0 = int(h * (1 - rf) / 2)
    sub = diff[y0:h - y0 or h, x0:w - x0 or w]
    if float(np.percentile(sub, 99)) < float(settings.get('min_diff', 10)):
        return []                       # no detectable change vs baseline
    otsu_t, _m = cv2.threshold(sub, 0, 255,
                               cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    fixed = float(settings.get('diff_thresh') or 0)
    threshes = ([('diff-fixed', fixed)] if fixed else []) + \
        [('diff-lo', max(3.0, 0.6 * otsu_t)),
         ('diff-otsu', max(3.0, otsu_t)),
         ('diff-hi', max(4.0, 1.5 * otsu_t))]
    out = []
    for method, t in threshes[:3]:
        _t, m = cv2.threshold(sub, t, 255, cv2.THRESH_BINARY)
        c = _region_candidate(m, method, offset=(x0, y0))
        if c and c['solidity'] >= float(settings.get('min_solidity', 0)):
            c['_thresh'] = t
            out.append(c)
    if not out:
        return []
    # boundary contrast (downscaled): diff level inside vs just outside
    ker = np.ones((15, 15), np.uint8)
    for c in out:
        m = np.zeros_like(sub)
        cv2.drawContours(m, [np.asarray(c['contour'] -
                                        np.array([x0, y0]), np.int32)],
                         -1, 255, -1)
        ring = cv2.dilate(m, ker) & ~m
        inside = float(sub[m > 0].mean()) if (m > 0).any() else 0.0
        outside = float(sub[ring > 0].mean()) if (ring > 0).any() else 0.0
        c['contrast'] = max(0.0, min(1.0, (inside - outside) / 20.0))
        c.pop('mask_local', None)
        c.pop('_thresh', None)
    if f != 1.0:                    # rescale every px quantity to full-res
        inv = 1.0 / f
        for c in out:
            c['area_px'] *= inv * inv
            c['diam_px'] *= inv
            c['cx'] *= inv
            c['cy'] *= inv
            c['contour'] = (np.asarray(c['contour'], float) * inv).astype(int)
    # wrinkle index at full resolution (contours are full-res now)
    for c in out:
        c['wrinkle'] = _wrinkle_ratio(base_full, img_full, c['contour'])
    areas = np.array([c['area_px'] for c in out], float)
    spread = float(areas.std() / areas.mean()) if len(out) > 1 else 0.0
    agreement = max(0.0, 1.0 - spread)
    for c in out:
        # wrinkle bonus: the lab defines the wrinkled region AS the active
        # area, so a candidate whose interior is wrinkle-textured outranks a
        # bigger smooth one.
        wbonus = max(0.0, min(1.0, (c['wrinkle'] - 1.0) / 1.5))
        c['conf'] = round(0.3 * c['solidity'] + 0.3 * c['contrast']
                          + 0.2 * agreement + 0.2 * wbonus, 3)
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


def wrinkle_onset(rows, results, settings):
    """Wrinkle-MODE detection over the accepted results.

    The wrinkle index (stored on each candidate by `candidates`) compares
    high-frequency texture inside the outline to the same region of the
    baseline. At/above `wrinkle_ratio` the DEA is in its wrinkled (activated)
    state. Returns (onset_row_index_or_None, {row_index: annotation}):
    the first wrinkled row is annotated 'wrinkle-mode onset', later ones
    'wrinkle-mode'. These are informational notes -- NOT breakdown flags, so
    they never trigger the _BREAKDOWN file renaming."""
    lim = float(settings.get('wrinkle_ratio', 1.6))
    onset = None
    annos = {}
    for i in range(len(rows)):
        r = results.get(i)
        if not r:
            continue
        w = float(r.get('wrinkle') or 0)
        if w >= lim:
            if onset is None:
                onset = i
                annos[i] = f"wrinkle-mode onset (idx {w:g})"
            else:
                annos[i] = f"wrinkle-mode (idx {w:g})"
    return onset, annos


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


def apply_results(rows, results, scale, flags, annos=None):
    """Fill the active_area_* / wrinkle_idx / notes columns in `rows`
    (in place). `annos` are informational notes (e.g. wrinkle-mode) appended
    alongside the breakdown flags but never treated as breakdown."""
    annos = annos or {}
    for i, row in enumerate(rows):
        r = results.get(i)
        if r:
            row['active_area_px'] = f"{r['area_px']:.0f}"
            if scale:
                row['active_area_mm2'] = f"{r['area_px'] * scale * scale:.3f}"
                row['active_diam_mm'] = f"{r['diam_px'] * scale:.3f}"
            if r.get('wrinkle') is not None:
                row['wrinkle_idx'] = f"{float(r['wrinkle']):.2f}"
            note = f"edge:{r['method']} conf {r['conf']:.2f}"
            if r.get('chosen_by'):
                note += f" ({r['chosen_by']})"
        elif i in results:                 # explicitly reviewed + rejected
            row['active_area_px'] = ''
            note = 'rejected (no reliable edge)'
        else:
            note = row.get('notes') or ''
        for extra in (flags.get(i), annos.get(i)):
            if extra:
                note = (note + '; ' if note else '') + extra
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
