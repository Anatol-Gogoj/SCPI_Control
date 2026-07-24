#!/usr/bin/env python3
"""Single-Layer DEA (SLDEA) test profile: staircase drive, snapshot schedule,
and run layout. Pure logic -- no hardware, no Tk -- so it is unit-testable and
drives both the GUI preview and the run executor.

Drive chain: the signal generator outputs a DC *control* voltage into a Trek
HV amplifier. Gain is 1 V(control) = 1 kV(Trek); the Trek maxes at 10 kV, so
the control voltage is clamped to 10 V. There is no DMM here -- the Trek's own
monitor BNCs are read on the oscilloscope:
    V_Out : 10 V on the scope = 10 kV on the Trek   -> 1 kV per scope-volt
    I_Out : 10 V on the scope = 2000 uA on the Trek -> 200 uA per scope-volt

A run captures a webcam frame near the end of the ramp (settled) and again
just before the next step, at every landing, plus a 0 kV baseline -- for the
later edge-detection pass that traces the active DEA area vs voltage.

Headless self-test: .venv/bin/python tests/test_sldea_profile.py
"""

HV_GAIN_KV_PER_V = 1.0      # 1 V control -> 1 kV Trek output
TREK_MAX_KV = 10.0          # Trek amplifier ceiling
VMON_KV_PER_V = 1.0         # scope V_Out: 1 scope-volt -> 1 kV
IMON_UA_PER_V = 200.0       # scope I_Out: 1 scope-volt -> 200 uA (10 V = 2000 uA)


def control_v_for_kv(kv):
    """SG control voltage for a desired Trek output (kV)."""
    return kv / HV_GAIN_KV_PER_V


def measured_kv(vmon_scope_v):
    """Trek output (kV) from the scope's V_Out reading (volts)."""
    return vmon_scope_v * VMON_KV_PER_V


def measured_ua(imon_scope_v):
    """Trek current (uA) from the scope's I_Out reading (volts)."""
    return imon_scope_v * IMON_UA_PER_V


def compute_levels(start_kv, end_kv, step_kv=None, n_steps=None):
    """Ordered list of landing voltages (kV).

    With step_kv: exact `step_kv` increments from start, last <= end.
    With n_steps: linspace with exact endpoints (start..end, n_steps levels).
    """
    if step_kv is not None and step_kv > 0:
        span = abs(end_kv - start_kv)
        n = int(span / step_kv + 1e-9) + 1
        sgn = 1.0 if end_kv >= start_kv else -1.0
        return [round(start_kv + sgn * i * step_kv, 6) for i in range(n)]
    if n_steps is not None and int(n_steps) >= 1:
        n = int(n_steps)
        if n == 1:
            return [end_kv]
        return [round(start_kv + (end_kv - start_kv) * i / (n - 1), 6)
                for i in range(n)]
    raise ValueError("give a positive step_kv or n_steps >= 1")


def fmt_duration(seconds):
    s = int(round(seconds))
    return f"{s // 3600:d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


class SldeaProfile:
    """A full staircase test built from the tab's input fields."""

    CSV_COLUMNS = [
        'snapshot', 'step', 'tag', 'nominal_kV', 'control_V',
        'measured_kV', 'measured_uA', 't_planned_s', 'timestamp', 'frame_file',
        # left empty at capture time; filled by the edge-detection pass later:
        'active_area_px', 'active_area_mm2', 'active_diam_mm', 'notes',
    ]

    def __init__(self, start_kv=0.0, end_kv=10.0, step_kv=0.25, n_steps=None,
                 ramp_s=5.0, landing_s=60.0, settle_s=2.0, snap_lead_s=1.0,
                 repeat=1, updown=False, baseline=True,
                 snap_post=True, snap_pre=True):
        for name, val in (('start_kv', start_kv), ('end_kv', end_kv)):
            if not 0.0 <= val <= TREK_MAX_KV:
                raise ValueError(f"{name}={val} kV out of range 0..{TREK_MAX_KV}")
        if ramp_s < 0:
            raise ValueError("ramp_s must be >= 0")
        if landing_s <= 0:
            raise ValueError("landing_s must be > 0")
        if settle_s < 0 or snap_lead_s < 0:
            raise ValueError("settle_s and snap_lead_s must be >= 0")
        if not (snap_post or snap_pre or baseline):
            raise ValueError("no snapshots requested")
        if snap_post and settle_s >= landing_s:
            raise ValueError("settle_s must be < landing_s")
        if snap_pre and snap_lead_s >= landing_s:
            raise ValueError("snap_lead_s must be < landing_s")
        if snap_post and snap_pre and settle_s + snap_lead_s >= landing_s:
            raise ValueError("settle_s + snap_lead_s must be < landing_s")

        self.levels = compute_levels(start_kv, end_kv, step_kv, n_steps)
        # 0 kV is captured as the baseline, not held as a landing -- a 0 kV
        # hold does nothing to a DEA, so drop a leading zero level.
        if self.levels and abs(self.levels[0]) < 1e-9:
            self.levels = self.levels[1:]
        if not self.levels:
            raise ValueError("no non-zero landing levels to run")
        if max(self.levels) > TREK_MAX_KV + 1e-9:
            raise ValueError(f"a level exceeds the Trek max {TREK_MAX_KV} kV")

        self.start_kv, self.end_kv = start_kv, end_kv
        self.step_kv, self.n_steps_req = step_kv, n_steps
        self.ramp_s, self.landing_s = float(ramp_s), float(landing_s)
        self.settle_s, self.snap_lead_s = float(settle_s), float(snap_lead_s)
        self.repeat = max(1, int(repeat))
        self.updown, self.baseline = bool(updown), bool(baseline)
        self.snap_post, self.snap_pre = bool(snap_post), bool(snap_pre)
        self._build()

    def sequence(self):
        """The full ordered list of landing voltages (with up/down + repeat)."""
        seq = list(self.levels)
        if self.updown and len(self.levels) > 1:
            seq = seq + list(reversed(self.levels[:-1]))
        return seq * self.repeat

    def _build(self):
        self.segments = []    # (kind, t0, t1, from_kv, to_kv), kind ramp|hold
        self.snapshots = []   # {t, step, nominal_kv, tag}
        t = 0.0
        if self.baseline:
            self.snapshots.append(
                {'t': 0.0, 'step': 0, 'nominal_kv': 0.0, 'tag': 'baseline'})
        prev = 0.0
        for step, lvl in enumerate(self.sequence(), start=1):
            self.segments.append(('ramp', t, t + self.ramp_s, prev, lvl))
            t_ramp_end = t + self.ramp_s
            t_hold_end = t_ramp_end + self.landing_s
            self.segments.append(('hold', t_ramp_end, t_hold_end, lvl, lvl))
            if self.snap_post:
                self.snapshots.append(
                    {'t': t_ramp_end + self.settle_s, 'step': step,
                     'nominal_kv': lvl, 'tag': 'post'})
            if self.snap_pre:
                self.snapshots.append(
                    {'t': t_hold_end - self.snap_lead_s, 'step': step,
                     'nominal_kv': lvl, 'tag': 'pre'})
            prev = lvl
            t = t_hold_end
        self.total_duration_s = t
        self.n_levels = len(self.sequence())
        self.n_frames = len(self.snapshots)

    def kv_at(self, t):
        """Target Trek voltage (kV) at time t -- for the runner's ramp and the
        preview curve."""
        if t <= 0:
            return 0.0
        for kind, t0, t1, a, b in self.segments:
            if t0 <= t <= t1:
                if kind == 'hold' or t1 == t0:
                    return b
                return a + (b - a) * (t - t0) / (t1 - t0)
        return self.segments[-1][4] if self.segments else 0.0

    # ---- run layout / naming -------------------------------------------
    @staticmethod
    def run_dirname(dt):
        """Auto directory name from the run start datetime."""
        return dt.strftime("SLDEA_%Y%m%d_%H%M%S")

    @staticmethod
    def frame_filename(step, nominal_kv, tag):
        return f"SLDEA_s{int(step):02d}_{float(nominal_kv):05.2f}kV_{tag}.png"

    def summary(self):
        return (f"{len(self.levels)} levels "
                f"{self.start_kv:g}->{self.end_kv:g} kV"
                f"{' (up/down)' if self.updown else ''}"
                f"{f' x{self.repeat}' if self.repeat > 1 else ''}: "
                f"{self.n_levels} landings, {self.n_frames} frames, "
                f"total {fmt_duration(self.total_duration_s)}")

    def setup_text(self, run_name, started_iso, sg_ch, vmon_ch, imon_ch,
                   dry_run, cam_info='', dea_diam_mm=None):
        step_desc = (f"{self.step_kv:g} kV/step" if self.step_kv
                     else f"{self.n_steps_req} steps")
        return "\n".join([
            f"SLDEA Test  --  {run_name}",
            f"Started: {started_iso}",
            "MODE: *** DRY RUN (HV output OFF) ***" if dry_run
            else "MODE: LIVE (HV energized)",
            "",
            "--- Drive ---",
            f"HV gain: {HV_GAIN_KV_PER_V:g} V(control) = "
            f"{HV_GAIN_KV_PER_V:g} kV(Trek);  Trek max {TREK_MAX_KV:g} kV",
            f"SG: CH{sg_ch} DC control voltage (High-Z)",
            f"Sweep: {self.start_kv:g} -> {self.end_kv:g} kV, {step_desc}, "
            f"{len(self.levels)} levels"
            f"{', up/down' if self.updown else ''}"
            f"{f', repeat x{self.repeat}' if self.repeat > 1 else ''}",
            f"Ramp {self.ramp_s:g}s | Landing {self.landing_s:g}s | "
            f"Settle {self.settle_s:g}s | Snap-lead {self.snap_lead_s:g}s",
            f"Total: {fmt_duration(self.total_duration_s)}  "
            f"({self.n_levels} landings, {self.n_frames} frames)",
            "",
            "--- Measurement (Trek monitors on scope) ---",
            f"V_Out: scope CH{vmon_ch}  ({VMON_KV_PER_V:g} kV per scope-volt)",
            f"I_Out: scope CH{imon_ch}  ({IMON_UA_PER_V:g} uA per scope-volt; "
            f"10 V = 2000 uA)",
            "",
            "--- Camera ---",
            cam_info or "(settings not recorded)",
            ""] + ([f"DEA nominal diameter: {dea_diam_mm:g} mm", ""]
                   if dea_diam_mm else []) + [
            "--- Snapshots ---",
            "baseline @ 0 kV" if self.baseline else "(no baseline)",
            "per landing: "
            + ", ".join(([f"post (ramp-end + {self.settle_s:g}s)"]
                         if self.snap_post else [])
                        + ([f"pre (landing-end - {self.snap_lead_s:g}s)"]
                           if self.snap_pre else [])),
        ]) + "\n"
