# SCPI_Control

A Tk-based Python GUI for controlling SCPI lab instruments over USB-TMC. Currently supports the **BK Precision 894 LCR meter** and the **Tektronix MSO24 oscilloscope**, with CSV logging.

## Layout

- `gui.py` — single-file Tk GUI (`InstrumentControlGUI`). Three tabs: LCR Meter, Oscilloscope, Data Logging.
- `instruments.py` — instrument abstraction. `USBTMC` (raw `/dev/usbtmcN` I/O) → `BK894`, `TekMSO24`.
- `test_bk894_measurements.py`, `test_instruments.py`, `test_scope.py` — **runnable smoke scripts, not pytest tests**. They each open a real USB-TMC device and will fail without hardware attached.
- `requirements.txt` — `python-usbtmc`, `pyusb`, `PyVISA`, `PyVISA-py`, `typing_extensions`. (PyVISA is in the deps but the current code uses raw `os.open` on `/dev/usbtmcN` directly.)

## Run

```
source venv/bin/activate
python3 gui.py
```

The GUI auto-connects to `/dev/usbtmc1` (BK894) and `/dev/usbtmc2` (MSO24) on startup; failures show as red status text rather than blocking the UI.

## Instrument constraints (BK894)

These are enforced inside `BK894`:

- **Frequency:** 100 Hz – 200 kHz (`set_frequency` raises outside this range).
- **Voltage:** 0.01 – 2.0 V AC (`set_voltage` raises outside this range).
- **Modes:** see `BK894.MODES` (CPD, CPQ, CPG, CPRP, LSRS, LSRD, LPRS, LPRP, RX, ZTD, ZTR).
- `measure()` returns `(primary, secondary, status)` — `status == 0` means good. Non-zero is an error/warning from the instrument and should be surfaced, not silently swallowed.
- Every `ask()` call sleeps 100 ms after `write` before reading, so a single `measure()` is roughly 150–200 ms minimum. Budget accordingly when designing sweeps.

## GUI conventions to follow

- **Long-running work runs off the Tk main loop.** Pattern is in `logging_loop` (`gui.py:776`): a `threading.Thread(daemon=True)`, a boolean flag (e.g. `self.recording`) checked each iteration for cancellation, and `self.root.after(0, fn)` for any UI updates from the worker thread. Do not call `Label.config(...)` / `Entry.delete(...)` directly from a background thread.
- For fast, UI-only periodic updates the existing code uses `self.root.after(ms, self.method)` re-entry — see `lcr_continuous_measurement` (`gui.py:548`). That pattern is fine for ≤200 ms tick loops, but is not appropriate for sweeps that include dwell time.
- CSV files: open with `newline=''`, write a header row, `flush()` after each write so the file is safe if the user kills the app mid-run.
- Errors from instruments go to `messagebox.showerror(...)` if they're user-actionable, and to `self.status_bar.config(...)` for transient info.

## Current work

Branch `lcr-sweep`: adding a frequency × voltage × dwell-time sweep to the LCR tab. Each (freq, V) point waits `dwell_s` after applying settings, then captures N samples with a small inter-sample gap. Output is one row per sample into `sweep_YYYYMMDD_HHMMSS.csv` under the logging directory. The sweep runs on a worker thread following the `logging_loop` pattern above.

A `BK894Mock` is being added to `instruments.py` so UI/threading logic can be exercised without the bench instrument.
