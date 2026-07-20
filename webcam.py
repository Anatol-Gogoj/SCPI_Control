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
import re
import select
import shutil
import subprocess

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


# -- V4L2 / Bayer support --------------------------------------------------
# Industrial UVC cameras expose raw Bayer only. The Imaging Source
# DFK 37BUX250 offers exactly RGGB8 and RG16 -- no YUYV, no MJPEG -- and
# OpenCV's V4L2 backend has no SRGGB8 support, so it refuses to open the
# device at all ("can't open camera by index", bench-verified 2026-07-20)
# even though v4l2-ctl streams from it happily. Those cameras are therefore
# driven through v4l2-ctl and debayered here.

# V4L2 fourcc -> OpenCV conversion attribute. NOTE the deliberate shift:
# OpenCV names a Bayer pattern after the 2x2 block starting at the SECOND
# row/column, so a V4L2 RGGB sensor is OpenCV's BayerBG. Getting this wrong
# swaps red and blue (it still looks like a picture, which is why it is easy
# to miss).
BAYER_CV_CODE = {
    'RGGB': 'COLOR_BayerBG2BGR',
    'BGGR': 'COLOR_BayerRG2BGR',
    'GRBG': 'COLOR_BayerGB2BGR',
    'GBRG': 'COLOR_BayerGR2BGR',
}


def v4l2_available():
    """True when the v4l2-ctl helper binary is installed."""
    return shutil.which('v4l2-ctl') is not None


def _v4l2(*args, device=None, timeout=10):
    """Run v4l2-ctl and return stdout ('' on any failure)."""
    cmd = ['v4l2-ctl']
    if device:
        cmd += ['-d', device]
    cmd += list(args)
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout)
        return out.stdout if out.returncode == 0 else ''
    except (OSError, subprocess.SubprocessError):
        return ''


def parse_formats(text):
    """'--list-formats' output -> list of fourcc strings, in order."""
    return re.findall(r"^\s*\[\d+\]:\s*'(\w{4})'", text, re.MULTILINE)


def parse_frame_sizes(text, fourcc):
    """'--list-formats-ext' output -> [(w, h), ...] for one fourcc."""
    sizes, active = [], False
    for line in text.splitlines():
        fmt = re.match(r"\s*\[\d+\]:\s*'(\w{4})'", line)
        if fmt:
            active = fmt.group(1) == fourcc
            continue
        if active:
            m = re.match(r"\s*Size:\s*Discrete\s+(\d+)x(\d+)", line)
            if m:
                sizes.append((int(m.group(1)), int(m.group(2))))
    return sizes


def device_formats(device):
    """Fourccs a /dev/videoN node offers (empty if it is not a capture node)."""
    return parse_formats(_v4l2('--list-formats', device=device))


def choose_bayer(formats):
    """Bayer fourcc to use when a device offers ONLY Bayer formats.

    A camera that also offers YUYV/MJPEG is left to OpenCV, which handles
    those natively and faster; None means "not our problem".
    """
    if not formats:
        return None
    bayer = [f for f in formats if f in BAYER_CV_CODE]
    ordinary = [f for f in formats if f not in BAYER_CV_CODE
                and not f.startswith('RG1')]      # RG16 = 16-bit Bayer
    return bayer[0] if bayer and not ordinary else None


def choose_size(sizes, max_width=1920):
    """Largest offered frame size at or below `max_width`.

    Full sensor resolution (2448x2048 on the DFK 37BUX250) costs USB
    bandwidth and debayer time for no benefit in a preview.
    """
    if not sizes:
        return None
    usable = [s for s in sizes if s[0] <= max_width] or sizes
    return max(usable, key=lambda s: s[0] * s[1])


def bayer_format(device):
    """The device's Bayer fourcc when it offers ONLY Bayer, else None."""
    return choose_bayer(device_formats(device))


def get_control(device, name):
    """Integer value of a V4L2 control, or None."""
    out = _v4l2(f'--get-ctrl={name}', device=device)
    m = re.search(r':\s*(-?\d+)', out)
    return int(m.group(1)) if m else None


def set_control(device, name, value):
    """Set a V4L2 control; returns True when it took."""
    _v4l2(f'--set-ctrl={name}={int(value)}', device=device)
    return get_control(device, name) == int(value)


def set_manual_exposure(device, exposure=None, gain=None):
    """Switch the camera to manual exposure and optionally set the values.

    Industrial cameras often ship with auto-exposure that never converges
    over UVC -- this one produced a pure black frame at its defaults while
    happily exposing under manual control.
    """
    set_control(device, 'auto_exposure', 1)          # 1 = manual (UVC)
    if exposure is not None:
        set_control(device, 'exposure_time_absolute', exposure)
    if gain is not None:
        set_control(device, 'gain', gain)


def grab_raw(device, fourcc, width, height, count=3):
    """Grab `count` frames and return the LAST one's raw bytes (or None).

    Short-lived capture used for exposure hunting; the first frames after a
    control change still carry the old settings, hence count > 1.
    """
    cmd = ['v4l2-ctl', '-d', device,
           f'--set-fmt-video=width={width},height={height},'
           f'pixelformat={fourcc}',
           '--stream-mmap', f'--stream-count={count}', '--stream-to=-']
    try:
        out = subprocess.run(cmd, capture_output=True,
                             timeout=30 + count * 5)
    except (OSError, subprocess.SubprocessError):
        return None
    n = width * height
    return out.stdout[-n:] if len(out.stdout) >= n else None


def auto_exposure(device, fourcc, width, height, target=128,
                  candidates=(20, 50, 100, 150, 200, 400, 800, 1600, 3200)):
    """Hunt for an exposure whose average level lands near `target`.

    Returns (exposure, mean) for the best candidate, or (None, None).
    This exists because the camera's own auto-exposure does not converge
    over UVC on this unit -- it sits at a pure black frame -- so "the
    camera is broken" is usually just a bad exposure.
    """
    import numpy as np
    set_manual_exposure(device)
    best = (None, None, None)
    for exp in candidates:
        if not set_control(device, 'exposure_time_absolute', exp):
            continue
        data = grab_raw(device, fourcc, width, height)
        if not data:
            continue
        mean = float(np.frombuffer(data, dtype=np.uint8).mean())
        err = abs(mean - target)
        if best[2] is None or err < best[2]:
            best = (exp, mean, err)
    if best[0] is not None:
        set_control(device, 'exposure_time_absolute', best[0])
    return best[0], best[1]


class V4L2BayerCamera:
    """Streams raw Bayer frames from v4l2-ctl and debayers them to BGR.

    Same read()/read_rgb()/close() surface as Camera, so the GUI does not
    care which backend it got.
    """

    def __init__(self, device, fourcc, width=None, height=None, fps=15):
        self.device = device
        self.fourcc = fourcc
        self.fps = fps
        if width and height:
            self.width, self.height = width, height
        else:
            sizes = parse_frame_sizes(
                _v4l2('--list-formats-ext', device=device), fourcc)
            self.width, self.height = choose_size(sizes) or (640, 480)
        self._proc = None

    @property
    def frame_bytes(self):
        return self.width * self.height          # 8-bit Bayer: 1 byte/pixel

    @property
    def is_open(self):
        return self._proc is not None and self._proc.poll() is None

    def open(self):
        # Set the frame rate in a SEPARATE call: --set-parm prints
        # "Frame rate set to N fps" (29 bytes, an ODD count) on stdout, and
        # with --stream-to=- that lands in front of the pixel data. The
        # frames then sit at an odd byte offset, which shifts the Bayer
        # phase by one column -- the picture survives but comes out as a
        # magenta checkerboard. --silent does not suppress it.
        # (bench-diagnosed 2026-07-20)
        _v4l2(f'--set-parm={self.fps}', device=self.device)
        cmd = ['v4l2-ctl', '-d', self.device,
               f'--set-fmt-video=width={self.width},height={self.height},'
               f'pixelformat={self.fourcc}',
               '--stream-mmap', '--stream-count=0', '--stream-to=-']
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL,
                                      bufsize=0)
        return self

    def _read_exactly(self, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = self._proc.stdout.read(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return bytes(buf)

    def read(self):
        """Latest BGR frame, or None. Drains the pipe so preview stays live."""
        if not self.is_open:
            return None
        import cv2
        import numpy as np
        data = self._read_exactly(self.frame_bytes)
        if data is None:
            return None
        # Skip any frames already queued behind this one, else the preview
        # falls further and further behind the camera.
        while select.select([self._proc.stdout], [], [], 0)[0]:
            nxt = self._read_exactly(self.frame_bytes)
            if nxt is None:
                break
            data = nxt
        raw = np.frombuffer(data, dtype=np.uint8).reshape(self.height,
                                                          self.width)
        code = getattr(cv2, BAYER_CV_CODE.get(self.fourcc, 'COLOR_BayerBG2BGR'))
        return cv2.cvtColor(raw, code)

    def read_rgb(self):
        import cv2
        frame = self.read()
        return None if frame is None else cv2.cvtColor(frame,
                                                       cv2.COLOR_BGR2RGB)

    def save(self, path, frame=None):
        import cv2
        if frame is None:
            frame = self.read()
        return False if frame is None else bool(cv2.imwrite(path, frame))

    def close(self):
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None


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
        self._bayer = None          # V4L2BayerCamera when cv2 cannot cope

    @property
    def is_open(self):
        if self._bayer is not None:
            return self._bayer.is_open
        return self._cap is not None and self._cap.isOpened()

    def open(self):
        import cv2
        self._cap = cv2.VideoCapture(self.index)
        if not self._cap.isOpened():
            self._cap = None
            # OpenCV cannot open Bayer-only industrial cameras at all; use
            # the v4l2-ctl backend for those before giving up.
            device = f'/dev/video{self.index}'
            if v4l2_available() and os.path.exists(device):
                fourcc = bayer_format(device)
                if fourcc:
                    self._bayer = V4L2BayerCamera(
                        device, fourcc, self.width, self.height).open()
                    return self
            raise RuntimeError(f"could not open camera index {self.index}")
        if self.width:
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        if self.height:
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        return self

    def read(self):
        """Return a BGR frame (numpy array) or None on failure."""
        if self._bayer is not None:
            return self._bayer.read()
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
        if self._bayer is not None:
            self._bayer.close()
            self._bayer = None
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
