#!/usr/bin/env python3
"""USB webcam capture: device probing, focus metric, and capture planning.

Capture uses OpenCV (``cv2.VideoCapture``); the GUI displays frames via Pillow.
Everything that does NOT need the heavy deps (device probing, focus metric,
filename + voltage-step planning) is import-safe without cv2/numpy/PIL, so this
module loads on any bench and the GUI tab can degrade gracefully when the
capture deps or a camera are missing.

cv2/numpy are imported lazily inside the functions that need them.
"""
import glob
import math
import os

# Quiet OpenCV's noisy V4L2/FFMPEG probe warnings (read by cv2 at import time;
# we import cv2 lazily, after this is set).
os.environ.setdefault('OPENCV_LOG_LEVEL', 'ERROR')


# -- capture dependency probe ----------------------------------------------

def deps_available():
    """Return (ok, reason). ok=True iff cv2, numpy and PIL all import."""
    missing = []
    for mod in ('cv2', 'numpy', 'PIL'):
        try:
            __import__(mod)
        except Exception:
            missing.append('Pillow' if mod == 'PIL' else mod)
    if missing:
        return False, "missing: " + ", ".join(missing)
    return True, "ok"


def list_cameras(max_index=8):
    """Return a list of available camera indices.

    On Linux, V4L2 exposes cameras as /dev/videoN; we map those device numbers
    to indices. Falls back to probing cv2 indices when no /dev/video* exist.
    """
    devs = sorted(glob.glob('/dev/video*'))
    if devs:
        idxs = []
        for d in devs:
            tail = d.replace('/dev/video', '')
            if tail.isdigit():
                idxs.append(int(tail))
        # Even-numbered /dev/video nodes are usually the capture device on
        # multi-node UVC cameras, but return all so the user can pick.
        return sorted(set(idxs))
    # No sysfs nodes (non-Linux or unusual setup): probe cv2 indices.
    try:
        import cv2
    except Exception:
        return []
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i)
        if cap is not None and cap.isOpened():
            found.append(i)
            cap.release()
    return found


# -- focus / sharpness metric ----------------------------------------------

def focus_score(image):
    """Variance-of-Laplacian sharpness score (higher = sharper/in-focus).

    Accepts a 2-D grayscale or 3-D RGB/BGR array (or nested lists). Uses a
    4-neighbour discrete Laplacian over the interior; the variance of the
    result is a standard, cheap autofocus metric. Needs numpy.
    """
    import numpy as np
    a = np.asarray(image, dtype=np.float64)
    if a.ndim == 3:
        a = a.mean(axis=2)
    if a.ndim != 2 or a.shape[0] < 3 or a.shape[1] < 3:
        raise ValueError("image must be at least 3x3 grayscale/colour")
    lap = (-4.0 * a[1:-1, 1:-1]
           + a[:-2, 1:-1] + a[2:, 1:-1]
           + a[1:-1, :-2] + a[1:-1, 2:])
    return float(lap.var())


# -- capture planning (pure) -----------------------------------------------

def frange(start, stop, step):
    """Inclusive numeric range for stepped (e.g. voltage) capture.

    Returns [start, start+step, ..., stop] (last point included when it lands
    within a small tolerance). Works for ascending or descending ranges as long
    as ``step`` points from start toward stop. Raises on a zero or wrong-sign
    step.
    """
    start, stop, step = float(start), float(stop), float(step)
    if step == 0:
        raise ValueError("step must be non-zero")
    span = stop - start
    if span == 0:
        return [start]
    if (span > 0) != (step > 0):
        raise ValueError("step sign must move start toward stop")
    n = int(math.floor(span / step + 1e-9))
    return [start + i * step for i in range(n + 1)]


def capture_filename(prefix, index, value=None, ext='png', ts=None):
    """Build a capture filename: '<prefix>_<NNNN>[_<ts>][_<value>V].<ext>'.

    value (e.g. a step voltage) is rendered filename-safe (sign as 'm', decimal
    point as 'p'): -2.5 -> 'm2p5V'. ts is an optional datetime.
    """
    safe_prefix = ''.join(c if (c.isalnum() or c in '-_') else '_'
                          for c in str(prefix)) or 'cap'
    parts = [safe_prefix, f"{int(index):04d}"]
    if ts is not None:
        parts.append(ts.strftime('%Y%m%d-%H%M%S'))
    if value is not None:
        v = f"{float(value):g}V".replace('-', 'm').replace('.', 'p')
        parts.append(v)
    return "_".join(parts) + "." + str(ext).lstrip('.')


# -- camera wrapper --------------------------------------------------------

class Camera:
    """Thin cv2.VideoCapture wrapper. Lazy-imports cv2 on open().

    Not thread-safe for concurrent reads -- the GUI pauses live preview while a
    capture sequence owns the camera.
    """
    def __init__(self, index=0, width=None, height=None):
        self.index = index
        self.width = width
        self.height = height
        self._cap = None

    @property
    def is_open(self):
        return self._cap is not None and self._cap.isOpened()

    def open(self):
        import cv2
        self._cap = cv2.VideoCapture(self.index)
        if not self._cap.isOpened():
            self._cap = None
            raise RuntimeError(f"could not open camera index {self.index}")
        if self.width:
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        if self.height:
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        return self

    def read(self):
        """Return a BGR frame (numpy array) or None on failure."""
        if not self.is_open:
            return None
        ok, frame = self._cap.read()
        return frame if ok else None

    def read_rgb(self):
        """Return an RGB frame (numpy array) or None -- ready for PIL."""
        import cv2
        frame = self.read()
        if frame is None:
            return None
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    def save(self, path, frame=None):
        """Write a frame (or a freshly-read one) to ``path``. Returns bool."""
        import cv2
        if frame is None:
            frame = self.read()
        if frame is None:
            return False
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        return bool(cv2.imwrite(path, frame))

    def close(self):
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
