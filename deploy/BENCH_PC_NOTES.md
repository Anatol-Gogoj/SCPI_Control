# Bench PC operational notes (RHEL 9 box `hc18kx2`)

Notes for whoever maintains the lab machine. Application code is documented
in the top-level README; this file covers the **desktop/OS side**: how the
app gets launched, why start-up takes as long as it does, and the shared
kiosk-account behaviour.

## Launch chain

Desktop icon / app-grid entry → `/usr/local/bin/scpi-launch.sh` (installed by
`~/install_lab_launchers.sh`) which tries, in order:

1. **The share** — `/mnt/shareDrive/_software/launch_gui.sh`. Probed by
   *reading a byte* (`head -c 1`), not `test -r`: on 2026-07-20 `test -r`
   succeeded against a dead NAS and the launch then died with "Host is down"
   without ever reaching a fallback.
2. **GitHub** — if the share is down but the internet is up, pull the latest
   code and run it locally (`/usr/local/bin/scpi-from-github.sh`; canonical
   copy `deploy/scpi_from_github.sh`). Gated on a quick `git ls-remote` so a
   full internet outage falls through fast. This is the share-outage backup:
   GitHub is over the internet, not the LAN share host, so it works when the
   Win11 share box is down. Clones to `~/.cache/scpi_control_git` (local
   disk — git corrupts packfiles on CIFS), reuses the existing
   `~/.cache/scpi_control/pylibs` for deps (or builds a local venv if there
   is none), and runs with cwd on the local presets root so presets persist.
   Anyone can run it by hand any time: `bash ~/scpi_from_github.sh`.
3. **This user's local cache** — `~/.cache/scpi_control`, kept up to date by
   the share launcher (can be stale — hence GitHub is tried first). Run with
   the working directory at `~/.local/share/scpi_control`, so `presets/`
   resolves to the same folder the app itself falls back to (see
   `presets_path.py`) and work done during an outage is not lost.
4. **A developer clone** at `~/projects/SCPI_Control`, if one exists.
5. Otherwise a visible zenity/notify error — never a silent nothing.

`launch_gui.sh` itself (share-only file; reference copy: `launch_gui.sh.reference`)
mirrors the app to the local cache **only when `version.py`'s stamp changed**,
then runs Python from local disk while keeping the working directory on the
share so presets stay shared between users.

## Start-up time — where it actually goes

Measured on the bench PC:

| Phase | Cold | Warm |
|---|---|---|
| Python imports | 0.02 s | 0.02 s |
| Building the six tabs | 2.6 s | 0.8 s |
| Cache re-sync (only after a deploy) | ~25 s | – |
| Instrument auto-connect | background, non-blocking | – |

So: a normal launch is a couple of seconds; the *first* launch after a deploy
pays the sync. Both now report themselves — the app shows a splash with a live
phase line, and the launcher raises a desktop notification before a re-sync.

Historical note: before the local-cache design, a cold launch straight off the
CIFS share took **72 s**, nearly all of it `pyvisa` import I/O over SMB.

## Shared "kiosk" account (`robotincubator`)

**Auto-login at boot** is configured in `/etc/gdm/custom.conf`:

```
[daemon]
AutomaticLoginEnable=True
AutomaticLogin=robotincubator
```

**Known behaviour:** this applies *only at boot*. Every logout, "Switch User",
or screen-lock returns to the GDM greeter, which prompts for a password
normally. That is GDM working as designed, not a broken configuration — so
"I have to type the password again every time I switch users" is expected
unless the greeter itself is told otherwise.

### Making the switch password-free (operator decision)

If the lab wants that account selectable from the greeter without a password,
the mechanism is a PAM rule in the **graphical login stack only**
(`/etc/pam.d/gdm-password`) that succeeds immediately for that one username,
placed directly after the existing `pam_selinux_permit.so` line so it is
evaluated before the password substack:

```
auth     [success=done ignore=ignore default=bad] pam_succeed_if.so user = robotincubator quiet_success
```

Understand the trade-off before doing it:

- It covers the GDM greeter **and the GNOME unlock screen** for that account.
- It does **not** touch `ssh`, `sudo`, `su`, or `xrdp`, and no other account
  is affected.
- It does mean anyone at the keyboard can enter that account freely. On a
  machine that already auto-logs into it at boot the practical change is
  small, but it is a real one: do not use it for an account that owns
  anything sensitive.

Practical safety when editing PAM: **keep a root shell open on another VT**,
back the file up first, verify the file still contains its
`substack password-auth` line afterwards, and test "Switch User" *before*
logging out of everything. A broken `gdm-password` locks every account out of
the desktop; recovery is restoring the backup from a text console.

## Desktop icons and the "plugin to the panel" error

An **untrusted** `.desktop` file is *opened* rather than executed. The only
handler registered for `application/x-desktop` here was xfce4-panel's
`panel-desktop-handler.desktop` (`Exec=xfce4-panel --add=launcher %u`), which
fails in a GNOME session with

```
Failed to add a plugin to the panel
GDBus.Error:org.freedesktop.DBus.Error.ServiceUnknown: The name is not activatable
```

Two mitigations, both installed by `~/install_lab_launchers.sh`:

1. The login helper keeps each user's desktop icon marked
   `metadata::trusted true`, and now only re-copies the file when its content
   differs — a plain `cp -f` every login silently dropped that flag.
2. `/etc/xdg/mimeapps.list` removes the association. It **must** be the
   generic file: GIO warns and ignores `[Removed Associations]` in
   `gnome-mimeapps.list` ("only the non-desktop-specific mimeapps.list file
   may add or remove associations"), so the change cannot be scoped per
   desktop.

Escape hatch that never needs trust: **Activities → type "SCPI" → Enter.**

## Verifying GUI changes without a seat

`xorg-x11-server-Xvfb` is installed. For screenshots or timing runs when no
desktop session is reachable:

```
Xvfb :99 -screen 0 1400x900x24 &
DISPLAY=:99 .venv/bin/python gui.py
```
