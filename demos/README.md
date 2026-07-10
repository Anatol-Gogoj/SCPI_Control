# UI framework demos (issue #32)

The LCR page mocked up in three candidate frameworks so their looks and
code styles can be compared against the current tkinter GUI. **Fake data,
no instrument I/O** — these are appearance/ergonomics studies only.

| Demo | Framework | Run with |
|---|---|---|
| `lcr_ttkbootstrap.py` | ttkbootstrap ("darkly" theme) | `python lcr_ttkbootstrap.py` |
| `lcr_nicegui.py` | NiceGUI (browser UI) | `python lcr_nicegui.py`, then open http://localhost:8091 |
| `lcr_pyside6.py` | PySide6 (Fusion dark) + pyqtgraph | `python lcr_pyside6.py` |

Dependencies (keep them OUT of the app's `.venv` / `requirements.txt` —
use a throwaway venv):

```
python3.11 -m venv /tmp/demo_venv
/tmp/demo_venv/bin/pip install ttkbootstrap nicegui pyside6 pyqtgraph
```

Notes from building them (2026-07-10, full write-up on issue #32):

- **ttkbootstrap** — closest to the existing code; a migration is mostly
  theming plus one layout pass (it enables HiDPI scaling the current app
  doesn't, so every tab needs a size check).
- **NiceGUI** — Python stays the whole stack, UI renders in any browser →
  the GUI becomes reachable from other lab PCs. Async model replaces the
  `_run_bg` worker pattern.
- **PySide6 + pyqtgraph** — most professional native result and the
  strongest real-time plotting; the biggest rewrite. RHEL9 needs
  `xcb-util-cursor` installed for the Qt xcb platform plugin.
