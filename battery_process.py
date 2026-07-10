#!/usr/bin/env python3
"""Battery-cycler data processing: translate + concatenate tester exports.

Adapted from the lab's standalone tool (Pingwinos40/BatteryProcessing,
battery_gui.py v1.1.0) for issue #31 -- this module is the PURE half
(no Tk, no matplotlib): loading, Mandarin->English translation, cycle
selection parsing, and time-unit helpers. The GUI half is battery_tab.py.

The tester exports .xls/.xlsx workbooks: first sheet = Info, last =
Cycle summary, everything between = Detail data with Mandarin headers.
Status labels may arrive as proper UTF-8 or as GBK bytes mis-decoded as
Latin-1 (garbled); both are translated.

pandas/openpyxl are imported lazily so the app runs without them (the
tab degrades to an install hint, like the webcam tab).

Headless self-test: .venv/bin/python tests/test_battery_process.py
"""

HEADER_MAP = {
    "状态":        "status",
    "跳转":        "jump",
    "循环":        "cycle",
    "步次":        "step",
    "电流(mA)":    "current_mA",
    "电压(mV)":    "voltage_mV",
    "容量(mAH)":   "capacity_mAh",
    "容量(mAh)":   "capacity_mAh",
    "能量(mWH)":   "energy_mWh",
    "能量(mWh)":   "energy_mWh",
    "相对时间(秒)": "relative_time_s",
    "绝对时间":     "absolute_time",
}

STATUS_MAP_UTF8 = {
    "恒流充电":   "CC_charge",
    "恒流放电":   "CC_discharge",
    "恒压充电":   "CV_charge",
    "恒压放电":   "CV_discharge",
    "搁置":       "rest",
    "静置":       "rest",
}

# garbled variants: UTF-8 keys encoded to GBK then mis-decoded as Latin-1
STATUS_MAP_GARBLED = {}
for _zh, _en in STATUS_MAP_UTF8.items():
    try:
        STATUS_MAP_GARBLED[_zh.encode("gbk").decode("latin-1")] = _en
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass

STATUS_MAP = {**STATUS_MAP_UTF8, **STATUS_MAP_GARBLED}

STATUS_COLORS = {
    "CC_charge":    "#1f77b4",
    "CC_discharge": "#d62728",
    "CV_charge":    "#2ca02c",
    "CV_discharge": "#ff7f0e",
    "rest":         "#7f7f7f",
}

TIME_UNITS = {
    "seconds": (1.0, "s"),
    "minutes": (1.0 / 60.0, "min"),
    "hours":   (1.0 / 3600.0, "h"),
}

TIME_COLUMNS = {"relative_time_s", "cycle_time_s"}

NUMERIC_COLS = ["current_mA", "voltage_mV", "capacity_mAh",
                "energy_mWh", "relative_time_s", "cycle", "step", "jump"]


def deps_available():
    """(ok, reason) -- True when pandas + openpyxl + matplotlib import."""
    try:
        import pandas          # noqa: F401
        import openpyxl        # noqa: F401
        import matplotlib      # noqa: F401
        return True, ""
    except ImportError as e:
        return False, str(e)


def load_and_process(filepath):
    """Load a battery-tester .xls/.xlsx into a translated DataFrame.

    Skips the first (Info) and last (Cycle summary) sheets, concatenates
    the Detail sheets in between, translates headers + status labels,
    coerces numerics, parses absolute_time, derives voltage_V.
    Raises ValueError on files that don't look like tester exports.
    """
    import pandas as pd

    xls = pd.ExcelFile(filepath)
    sheet_names = xls.sheet_names
    if len(sheet_names) < 3:
        xls.close()
        raise ValueError(
            f"Expected at least 3 sheets (Info, Detail(s), Cycle), "
            f"got {len(sheet_names)}: {sheet_names}")

    frames = []
    for name in sheet_names[1:-1]:
        df_sheet = pd.read_excel(xls, sheet_name=name)
        if not df_sheet.empty:
            frames.append(df_sheet)
    xls.close()
    if not frames:
        raise ValueError("No data found in detail sheets.")

    df = pd.concat(frames, ignore_index=True)
    df.rename(columns=HEADER_MAP, inplace=True)
    if "status" in df.columns:
        df["status"] = df["status"].map(lambda s: STATUS_MAP.get(s, s))
    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "absolute_time" in df.columns:
        df["absolute_time"] = pd.to_datetime(df["absolute_time"],
                                             errors="coerce")
    if "voltage_mV" in df.columns:
        df["voltage_V"] = df["voltage_mV"] / 1000.0
    return df


def parse_cycle_selection(text):
    """'1,3,5-8' -> [1, 3, 5, 6, 7, 8]; junk is skipped; empty/placeholder
    input returns None (= all cycles)."""
    text = (text or "").strip()
    if not text or text.startswith("e.g."):
        return None
    cycles = set()
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            try:
                lo, hi = part.split("-", 1)
                cycles.update(range(int(lo), int(hi) + 1))
            except ValueError:
                pass
        else:
            try:
                cycles.add(int(part))
            except ValueError:
                pass
    return sorted(cycles) if cycles else None


def axis_label(col, time_label="s"):
    """Column name -> axis label, renaming time columns to the active
    unit ('relative_time_s' + 'min' -> 'relative_time (min)')."""
    if col in TIME_COLUMNS:
        base = col[:-2] if col.endswith("_s") else col
        return f"{base} ({time_label})"
    return col
