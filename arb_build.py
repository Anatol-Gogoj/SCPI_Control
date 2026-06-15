#!/usr/bin/env python3
"""
Arbitrary-waveform model + render math (pure, no Tk, no instrument).

The editor's data model is a *recipe*: an ordered list of breakpoints (x, y)
spliced by base-waveform segments, one segment per interval between consecutive
breakpoints. Segments connect their two endpoints, so the pieces continue off
each other (e.g. a LINE from (0,0)->(2,0.25) then a HOLD from (2,0.25)->(3,0.25)).

    recipe = {
        "version": 1,
        "total_points": 4096,
        "breakpoints": [[x0, y0], [x1, y1], ...],   # x strictly increasing
        "segments": [{"type": "LINE", "params": {}}, ...],  # len == len(bp) - 1
    }

render_piecewise() turns a recipe into a flat sample list in [-1, 1] of length
total_points, ready for BK4055B.upload_arb. All edit helpers return a NEW recipe
(the editor keeps an undo stack of these).
"""
import math
import json

from waveform_render import _pulse_sample

SCHEMA_VERSION = 1
DEFAULT_POINTS = 4096

# Ordered for the editor's type dropdown.
SEGMENT_TYPES = ('LINE', 'HOLD', 'SINE', 'SQUARE', 'RAMP', 'PULSE', 'EXP')

# Per-type editable params: (key, label, default). Empty = no params.
SEGMENT_PARAMS = {
    'LINE': [],
    'HOLD': [],
    'SINE': [('cycles', 'Cycles', 1.0), ('amp', 'Amplitude', 1.0),
             ('phase', 'Phase (deg)', 0.0)],
    'SQUARE': [('cycles', 'Cycles', 1.0), ('amp', 'Amplitude', 1.0),
               ('duty', 'Duty %', 50.0)],
    'RAMP': [('cycles', 'Cycles', 1.0), ('amp', 'Amplitude', 1.0),
             ('sym', 'Symmetry %', 50.0)],
    'PULSE': [('amp', 'Amplitude', 1.0), ('rise', 'Rise frac', 0.1),
              ('fall', 'Fall frac', 0.1), ('duty', 'Duty %', 50.0)],
    'EXP': [('tau', 'Tau', 0.3)],
}


def _clamp(v, lo=-1.0, hi=1.0):
    return lo if v < lo else hi if v > hi else v


def _param(params, type_, key):
    """Param value with the type's default fallback."""
    for k, _label, default in SEGMENT_PARAMS.get(type_, []):
        if k == key:
            return float(params.get(key, default))
    return 0.0


def default_recipe(total_points=DEFAULT_POINTS):
    """A minimal valid recipe: a flat line from (0,0) to (1,0)."""
    return {
        'version': SCHEMA_VERSION,
        'total_points': int(total_points),
        'breakpoints': [[0.0, 0.0], [1.0, 0.0]],
        'segments': [{'type': 'LINE', 'params': {}}],
    }


# --------------------------------------------------------------------------
# Segment rendering
# --------------------------------------------------------------------------

def _render_segment(type_, n, y0, y1, params):
    """Render n samples for one interval connecting y0 -> y1."""
    type_ = (type_ or 'LINE').upper()
    if n <= 0:
        return []
    base = [y0 + (k / n) * (y1 - y0) for k in range(n)]  # linear baseline

    if type_ == 'LINE':
        return base
    if type_ == 'HOLD':
        return [y0] * n
    if type_ == 'SINE':
        cyc = _param(params, 'SINE', 'cycles')
        amp = _param(params, 'SINE', 'amp')
        ph = math.radians(_param(params, 'SINE', 'phase'))
        return [base[k] + amp * math.sin(2 * math.pi * cyc * (k / n) + ph)
                for k in range(n)]
    if type_ == 'SQUARE':
        cyc = _param(params, 'SQUARE', 'cycles')
        amp = _param(params, 'SQUARE', 'amp')
        duty = min(max(_param(params, 'SQUARE', 'duty'), 0.0), 100.0) / 100.0
        out = []
        for k in range(n):
            frac = (cyc * (k / n)) % 1.0
            out.append(base[k] + (amp if frac < duty else -amp))
        return out
    if type_ == 'RAMP':
        cyc = _param(params, 'RAMP', 'cycles')
        amp = _param(params, 'RAMP', 'amp')
        sym = min(max(_param(params, 'RAMP', 'sym'), 0.0), 100.0) / 100.0
        out = []
        for k in range(n):
            frac = (cyc * (k / n)) % 1.0
            if sym <= 0.0:
                r = 1.0 - 2.0 * frac
            elif sym >= 1.0:
                r = -1.0 + 2.0 * frac
            elif frac < sym:
                r = -1.0 + 2.0 * (frac / sym)
            else:
                r = 1.0 - 2.0 * ((frac - sym) / (1.0 - sym))
            out.append(base[k] + amp * r)
        return out
    if type_ == 'PULSE':
        amp = _param(params, 'PULSE', 'amp')
        rise = _param(params, 'PULSE', 'rise')
        fall = _param(params, 'PULSE', 'fall')
        duty = min(max(_param(params, 'PULSE', 'duty'), 0.0), 100.0) / 100.0
        return [base[k] + amp * _pulse_sample(k / n, duty, rise, fall)
                for k in range(n)]
    if type_ == 'EXP':
        tau = _param(params, 'EXP', 'tau') or 0.3
        denom = 1.0 - math.exp(-1.0 / tau)
        if abs(denom) < 1e-12:
            return base
        return [y0 + (y1 - y0) * (1.0 - math.exp(-(k / n) / tau)) / denom
                for k in range(n)]
    return base  # unknown type -> linear


def _breakpoint_indices(xs, n):
    """Map strictly-increasing breakpoint x values onto sample indices [0, n],
    forcing strict monotonicity so every interval gets >= 1 sample."""
    x0, xn = xs[0], xs[-1]
    span = xn - x0
    idx = [round((x - x0) / span * n) for x in xs]
    idx[0] = 0
    idx[-1] = n
    for i in range(1, len(idx) - 1):           # push right if collided
        if idx[i] <= idx[i - 1]:
            idx[i] = idx[i - 1] + 1
    for i in range(len(idx) - 2, 0, -1):       # pull left to leave room
        if idx[i] >= idx[i + 1]:
            idx[i] = idx[i + 1] - 1
    if any(idx[i] <= idx[i - 1] for i in range(1, len(idx))):
        raise ValueError("too many breakpoints for total_points")
    return idx


def render_piecewise(breakpoints, segments, total_points):
    """Render a breakpoint/segment recipe into total_points samples in [-1, 1]."""
    bps = [[float(x), float(y)] for x, y in breakpoints]
    if len(bps) < 2:
        raise ValueError("need at least 2 breakpoints")
    if len(segments) != len(bps) - 1:
        raise ValueError(f"segments ({len(segments)}) must be one fewer than "
                         f"breakpoints ({len(bps)})")
    xs = [p[0] for p in bps]
    if any(b <= a for a, b in zip(xs, xs[1:])):
        raise ValueError("breakpoint x values must be strictly increasing")
    n = int(total_points)
    if n < 2:
        raise ValueError("total_points must be >= 2")

    idx = _breakpoint_indices(xs, n)
    out = []
    for i, seg in enumerate(segments):
        ni = idx[i + 1] - idx[i]
        out.extend(_render_segment(seg.get('type', 'LINE'), ni,
                                   bps[i][1], bps[i + 1][1],
                                   seg.get('params') or {}))
    if len(out) != n:  # belt and suspenders; should already match
        out = (out + [out[-1] if out else 0.0] * n)[:n]
    return [_clamp(v) for v in out]


def render_recipe(recipe):
    """Convenience: render a whole recipe dict."""
    return render_piecewise(recipe['breakpoints'], recipe['segments'],
                            recipe.get('total_points', DEFAULT_POINTS))


# --------------------------------------------------------------------------
# Edit helpers (each returns a NEW recipe)
# --------------------------------------------------------------------------

def _copy(recipe):
    return {
        'version': SCHEMA_VERSION,
        'total_points': int(recipe.get('total_points', DEFAULT_POINTS)),
        'breakpoints': [[float(x), float(y)] for x, y in recipe['breakpoints']],
        'segments': [{'type': s.get('type', 'LINE'),
                      'params': dict(s.get('params') or {})}
                     for s in recipe['segments']],
    }


def add_point(recipe, x, y, seg_type='LINE'):
    """Insert a breakpoint at (x, y), splitting/extending segments as needed."""
    r = _copy(recipe)
    bps, segs = r['breakpoints'], r['segments']
    x = float(x)
    y = _clamp(float(y))
    pos = 0
    while pos < len(bps) and bps[pos][0] < x:
        pos += 1
    if pos < len(bps) and bps[pos][0] == x:
        raise ValueError(f"a breakpoint already exists at x={x}")
    bps.insert(pos, [x, y])
    if pos == 0:
        segs.insert(0, {'type': seg_type, 'params': {}})
    elif pos == len(bps) - 1:
        segs.append({'type': seg_type, 'params': {}})
    else:                                      # split the interval at pos-1
        twin = {'type': segs[pos - 1]['type'],
                'params': dict(segs[pos - 1].get('params') or {})}
        segs.insert(pos, twin)
    return r


def move_point(recipe, i, x, y):
    """Move breakpoint i to (x, y), clamping x strictly between neighbors and
    y into [-1, 1]."""
    r = _copy(recipe)
    bps = r['breakpoints']
    if not 0 <= i < len(bps):
        raise IndexError(i)
    eps = 1e-9
    lo = bps[i - 1][0] + eps if i > 0 else float('-inf')
    hi = bps[i + 1][0] - eps if i < len(bps) - 1 else float('inf')
    bps[i][0] = min(max(float(x), lo), hi)
    bps[i][1] = _clamp(float(y))
    return r


def delete_point(recipe, i):
    """Remove breakpoint i (merging its intervals). Keeps >= 2 breakpoints."""
    r = _copy(recipe)
    bps, segs = r['breakpoints'], r['segments']
    if len(bps) <= 2:
        raise ValueError("cannot delete: need at least 2 breakpoints")
    if not 0 <= i < len(bps):
        raise IndexError(i)
    bps.pop(i)
    segs.pop(i if i < len(segs) else len(segs) - 1)  # drop one adjacent segment
    return r


def set_segment_type(recipe, i, seg_type):
    r = _copy(recipe)
    if not 0 <= i < len(r['segments']):
        raise IndexError(i)
    if seg_type.upper() not in SEGMENT_TYPES:
        raise ValueError(f"unknown segment type {seg_type!r}")
    r['segments'][i]['type'] = seg_type.upper()
    return r


def set_segment_params(recipe, i, params):
    r = _copy(recipe)
    if not 0 <= i < len(r['segments']):
        raise IndexError(i)
    r['segments'][i]['params'] = {k: float(v) for k, v in params.items()}
    return r


def set_total_points(recipe, n):
    r = _copy(recipe)
    r['total_points'] = max(2, int(n))
    return r


def smooth(samples, radius):
    """Box-kernel moving average over a sample array (edge-clamped)."""
    radius = int(radius)
    if radius <= 0:
        return list(samples)
    n = len(samples)
    out = []
    for i in range(n):
        lo = max(0, i - radius)
        hi = min(n - 1, i + radius)
        out.append(sum(samples[lo:hi + 1]) / (hi - lo + 1))
    return out


def samples_to_recipe(samples, n_anchors=32, total_points=None):
    """Approximate a raw sample array as an editable LINE-anchored recipe.

    Used when importing a bare CSV (no recipe sidecar): pick n_anchors evenly
    spaced points and connect them with LINE segments so the curve becomes
    editable. x is the sample index.
    """
    n = len(samples)
    if n < 2:
        raise ValueError("need at least 2 samples")
    n_anchors = max(2, min(int(n_anchors), n))
    idxs = sorted(set(round(k * (n - 1) / (n_anchors - 1))
                      for k in range(n_anchors)))
    bps = [[float(j), _clamp(float(samples[j]))] for j in idxs]
    segs = [{'type': 'LINE', 'params': {}} for _ in range(len(bps) - 1)]
    return {
        'version': SCHEMA_VERSION,
        'total_points': int(total_points or n),
        'breakpoints': bps,
        'segments': segs,
    }


# --------------------------------------------------------------------------
# Serialization
# --------------------------------------------------------------------------

def recipe_to_json(recipe):
    return json.dumps(_copy(recipe), indent=2)


def recipe_from_json(text):
    data = json.loads(text)
    if not isinstance(data, dict) or 'breakpoints' not in data:
        raise ValueError("not a valid arb recipe")
    r = _copy(data)
    # Validate by rendering once (raises on bad structure).
    render_recipe(r)
    return r
