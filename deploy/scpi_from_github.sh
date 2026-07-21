#!/usr/bin/env bash
# ============================================================================
#  Digital Multitool -- GitHub backup launcher.
#
#  Runs the app straight from GitHub, entirely on the local machine, for when
#  the ShareDrive is unreachable (e.g. the Win11 host that serves it is down).
#  GitHub is reached over the internet, NOT the LAN share, so this works
#  during a share outage. It fetches the latest code into a LOCAL directory
#  (never onto CIFS -- git corrupts packfiles there) and runs it with the
#  existing local dependency cache, or a self-built venv if there isn't one.
#
#  Run it any time:   bash ~/scpi_from_github.sh
#  It is also wired in as the last-resort fallback of scpi-launch.sh.
# ============================================================================
set -uo pipefail

REPO="${SCPI_GITHUB_REPO:-https://github.com/Anatol-Gogoj/SCPI_Control}"
GHDIR="${SCPI_GITHUB_DIR:-$HOME/.cache/scpi_control_git}"
CACHE_LIBS="$HOME/.cache/scpi_control/pylibs"
# Presets live here (outside the git checkout, so a git reset can't wipe
# them, and it matches the app's own share-down fallback dir).
PRESETS_ROOT="$HOME/.local/share/scpi_control"
PY="${SCPI_PYTHON:-/usr/bin/python3.11}"
command -v "$PY" >/dev/null 2>&1 || PY=python3.11

say()  { echo "Digital Multitool: $*"; \
         notify-send "Digital Multitool" "$*" 2>/dev/null || true; }
fail() { zenity --error --title "Digital Multitool" --text "$1" 2>/dev/null \
           || notify-send -u critical "Digital Multitool" "$1" 2>/dev/null; \
         echo "ERROR: $1" >&2; exit 1; }

command -v git >/dev/null 2>&1 || fail "git is not installed -- cannot pull from GitHub."

# 1) get the latest code onto local disk (fresh clone, or fetch + hard reset)
if [ -d "$GHDIR/.git" ]; then
    say "updating from GitHub..."
    if timeout 120 git -C "$GHDIR" fetch --depth 1 origin main 2>/dev/null; then
        git -C "$GHDIR" reset --hard FETCH_HEAD >/dev/null 2>&1 || true
    else
        say "GitHub update failed -- using the last-downloaded copy."
    fi
else
    say "downloading from GitHub (first time)..."
    rm -rf "$GHDIR"
    timeout 180 git clone --depth 1 "$REPO" "$GHDIR" 2>/dev/null \
        || fail "Could not download from GitHub. Check the internet connection and try again."
fi
[ -f "$GHDIR/gui.py" ] || fail "GitHub copy is incomplete (no gui.py) -- try again."

ver="$(grep -m1 '^__version__' "$GHDIR/version.py" 2>/dev/null | cut -d'"' -f2)"
mkdir -p "$PRESETS_ROOT"
cd "$PRESETS_ROOT"          # relative presets/ resolves to a stable local dir

# 2) run it. Prefer the existing local dependency cache (already present from a
#    past share launch, so no install needed); else build a self-contained venv.
if [ -d "$CACHE_LIBS" ]; then
    say "running ${ver:-latest} from GitHub (local copy; presets saved locally until the drive is back)."
    exec env PYTHONPATH="$CACHE_LIBS" "$PY" "$GHDIR/gui.py" "$@"
fi
if [ ! -x "$GHDIR/.venv/bin/python" ]; then
    say "first-time setup: installing Python packages (a few minutes, needs internet)..."
    "$PY" -m venv "$GHDIR/.venv" || fail "Could not create a Python environment."
    "$GHDIR/.venv/bin/python" -m pip install --quiet --upgrade pip setuptools wheel
    "$GHDIR/.venv/bin/python" -m pip install --quiet -r "$GHDIR/requirements.txt" \
        || fail "Package install failed (no internet?). Try again once online."
fi
say "running ${ver:-latest} from GitHub (local copy; presets saved locally until the drive is back)."
exec "$GHDIR/.venv/bin/python" "$GHDIR/gui.py" "$@"
