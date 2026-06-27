# SCPI_Control

Python tool for SCPI-based control of bench-top lab instruments. Targets a USB-TMC + USB-serial bench currently consisting of:

| Instrument | VID:PID | Class | Transport |
|---|---|---|---|
| B&K Precision 894 LCR meter | `0x0471:0x2827` | `BK894` | USB-TMC |
| Tektronix MSO24 oscilloscope | `0x0699:0x0105` | `TekMSO24` | USB-TMC |
| B&K Precision 4055B signal generator | `0xf4ec:0xee38` | `BK4055B` | USB-TMC |
| DC power supply (model TBD) behind CP2102 USB-UART | `0x10c4:0xea60` | `SerialDCSupply` (stub) | USB-serial (`/dev/ttyUSB0`) |

A digital multimeter is also on the bench but does not enumerate as of 2026-06-04; intentionally deferred.

## Architecture

USB-TMC instruments are accessed through `pyvisa.ResourceManager('@py')` (the pyvisa-py backend, which talks libusb via pyusb). The kernel `usbtmc` driver is **blacklisted** so libusb can claim the devices without driver contention. Instrument classes auto-discover by VID/PID via PyVISA's resource list -- callers do not pass device paths:

```python
from instruments import BK894, TekMSO24, BK4055B

lcr   = BK894()
scope = TekMSO24()
sg    = BK4055B()
```

Pass an explicit resource string only to disambiguate when multiple units of the same model are connected:

```python
lcr = BK894(resource='USB0::1137::10279::ABCDEF::0::INSTR')
```

The CP2102-connected DC supply is reached over `/dev/ttyUSB0` via pyserial (lazy import, since pyserial is not yet in `requirements.txt`).

## Setup

### System-level (Linux, root)

1. Blacklist the kernel `usbtmc` driver so pyvisa-py can claim USB-TMC devices via libusb:

   ```
   echo 'blacklist usbtmc' | sudo tee /etc/modprobe.d/blacklist-usbtmc.conf
   sudo modprobe -r usbtmc 2>/dev/null || true
   ```

2. Grant non-root access to the USB devices. Example `/etc/udev/rules.d/99-usbtmc.rules`:

   ```
   # B&K Precision 894 LCR (Philips/NXP-assigned VID)
   SUBSYSTEM=="usb", ATTR{idVendor}=="0471", MODE="0666"

   # Tektronix MSO24
   SUBSYSTEM=="usb", ATTR{idVendor}=="0699", MODE="0666"

   # B&K Precision 4055B sig gen (Siglent OEM VID)
   SUBSYSTEM=="usb", ATTR{idVendor}=="f4ec", MODE="0666"

   # CP2102 USB-UART (DC supply)
   SUBSYSTEM=="tty", ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea60", MODE="0666"
   ```

   **NOTE:** udev rules do not support inline trailing `#` comments. Keep `#` only at the start of a line.

   Reload and re-enumerate:

   ```
   sudo udevadm control --reload
   # Replug each instrument so the new rules apply (an `add` event re-evaluates MODE).
   ```

### Python environment

```
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

PyVISA `1.16+` requires Python `3.10+`, which is why this project uses Python 3.11 rather than RHEL9's default 3.9.

## Run

```
.venv/bin/python gui.py
```

The Tk GUI auto-connects on launch and exposes per-instrument tabs. Re-enumeration is one click per instrument.

For a headless smoke test of the transport stack:

```
.venv/bin/python instruments.py
```

This lists all PyVISA-visible USB-TMC resources, then instantiates each known class and prints its `*IDN?` response.

## Adding a new instrument

1. Look up its USB VID/PID (`lsusb` or `dmesg`).
2. Subclass `VisaInstrument`, set `VID`, `PID`, and (optionally) `TIMEOUT_MS`.
3. Add SCPI method wrappers as needed.
4. Add a corresponding line to the udev rules file (per VID) and reload udev.
5. Replug the instrument so udev re-applies the MODE.

## Known instrument quirks

- **BK 894 USB serial-number descriptor is malformed.** The meter reports non-ASCII garbage as its USB SerialNumber string descriptor; PyVISA passes it through verbatim, so resources show up with binary cruft in the serial field of the resource string. The true serial is reported correctly via `*IDN?`. `BK894.__init__` ignores the descriptor serial and addresses by VID/PID under the hood, so this is cosmetic only.
- **BK 894 AC test-level command is plain `:VOLT`.** Set with `:VOLT <v>` and read with `:VOLT?` (bench-verified 2026-06-16). Both `:LEV:VOLT` and the E4980A-style `:VOLTage:LEVel` (`:VOLT:LEV`) are **silently rejected** on this unit — the level just stays put. The meter clamps the level at **2.0 V** (so the valid range is 0.01–2.0 V) and the frequency at **500 kHz**.
- **BK 4055B SCPI uses Siglent SDG dialect.** B&K rebadged this from the Siglent SDG2000X series. Commands like `C1:BSWV WVTP,SINE` follow the Siglent SDG programming manual. Most commands match, but the BK-specific manual should be the authoritative reference for production work.
- **BK 4055B mis-parses decimal-suffixed numeric parameters.** `C1:BSWV DUTY,50.0` lands as **5%** duty (bench-verified 2026-06-11; the firmware appears to read the digits in 0.01% units). Always send plain decimals with no trailing `.0` and no scientific exponent — `BK4055B._fmt_param` does this for every numeric parameter; route new SCPI through it.
- **BK 4055B is slow to answer queries right after a multi-parameter write.** A `BSWV?` immediately after a chained `BSWV` set can exceed a 2 s VISA timeout (`VI_ERROR_TMO`). The driver uses a 5 s timeout and the GUI waits 0.2 s before read-back.
- **BK 4055B arbitrary waveforms: X-series `WVDT` form + TrueArb, short buffer.** Per the Siglent SDG Programming Guide (the 4055B is an OEM SDG2000X), the "X-series" upload omits `LENGTH` and `TYPE`: `C<n>:WVDT WVNM,<name>,FREQ,<f>,AMPL,<a>,OFST,<o>,PHASE,<p>,WAVEDATA,<int16 LE>` — sample bytes follow `WAVEDATA,` directly (not an IEEE block). The earlier `LENGTH,<n>B,TYPE,6` form was reverse-engineered around the corrupted USB transport, not the protocol. Play short custom arbs via **TrueArb** (`C<n>:SRATE MODE,TARB,VALUE,<Sa/s>`), where output frequency = sample_rate ÷ num_points — the box's dedicated short-arb path, avoiding the fixed 16384-point DDS table. `BK4055B.upload_arb` defaults to a **1024-point (~2 KB) buffer**; the old 32 KB DDS upload is what wedged the pyvisa-py USBTMC endpoint (issue #20). `SRATE` supports `MODE`/`VALUE` on the SDG2000X but **not** `INTER`. Self-test: `test_arb_scope.py` (sig gen → scope) and headless `test_arb_transport.py`.
- **Tektronix MSO24 frequency measurement needs `FREQUENCY`, not `FREQ`.** The `MEASUREMENT:IMMED:TYPE` token must be `FREQUENCY`; `FREQ` is accepted without error but returns a garbage ~2 Hz value (bench-verified 2026-06-16). The amplitude/mean/period/pk2pk tokens are fine. `TekMSO24.get_all_measurements` uses the correct token. Run `test_siggen_scope.py` (sig gen → scope BNC) to self-test the measurement path.
