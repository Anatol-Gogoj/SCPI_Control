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
- **BK 4055B USB commands are hard-capped at 52 bytes (firmware 1.01.01.33R3).** Bench-verified 2026-07-02, three ways: (1) any USBTMC Bulk-OUT transfer spanning more than one 64-byte USB packet — even a whitespace-padded pure-ASCII `*IDN?` — **hard-wedges the firmware**: writes are accepted into the void, every read times out, and only a **front-panel power cycle** recovers it (USBTMC `INITIATE_CLEAR`, `*RST`, USB reset, and a VBUS hub-port power-cycle all fail; the instrument is self-powered). This is why EasyWaveX freezes the box. (2) Chaining a command across multiple single-packet USBTMC messages (EOM only on the last) does **not** wedge but does **not reassemble** either — the first message is processed as the whole command and the continuations are silently dropped (`WVDT?` readback showed a 2 KB upload stored as its first 24 data bytes; the waveform *name* registers, which makes truncation easy to miss). (3) Bulk-IN is unaffected — multi-KB query responses read back fine. Net: every USB command must fit 52 bytes (64-byte packet minus the 12-byte USBTMC header), newline included. The driver enforces this: `BK4055B.write` raises on oversized commands instead of wedging the box, and `set_basic_wave` auto-splits long `BSWV` chains (a full `WVTP,SQUARE,...,DUTY,50,PHSE,0` chain is 56 bytes — over the cap).
- **BK 4055B arbitrary waveforms: LAN only, minimal `WVDT` header, TrueArb.** Because of the 52-byte USB cap, `upload_arb` **refuses over USB** — connect the box via Ethernet and open it as `BK4055B(resource='TCPIP0::<ip>::INSTR')` (the tinylabs SDG2000X library demonstrates WVDT works over LAN). The 4055B also **silently rejects** the official Siglent 16-bit-arb app-note header (`FREQ,<f>,TYPE,8,AMPL,<a>,OFST,<o>,PHASE,<p>`) — alive but nothing stored (bench-verified 2026-07-02). Only the **minimal form** stores: `C<n>:WVDT WVNM,<name>,WAVEDATA,<int16 LE>` — sample bytes follow `WAVEDATA,` directly (not an IEEE block), no `LENGTH`, no trailing terminator. Set level/offset afterwards via `BSWV` and play short custom arbs via **TrueArb** (`C<n>:SRATE MODE,TARB,VALUE,<Sa/s>`), where output frequency = sample_rate ÷ num_points. `BK4055B.upload_arb` defaults to a **1024-point (~2 KB) buffer**. Self-tests: `test_arb_chained.py --resource 'TCPIP0::<ip>::INSTR'` (upload + `WVDT?` readback byte-compare + TrueArb round-trip; readback is the *only* proof of storage), `test_arb_scope.py` (sig gen → scope), `test_arb_upload_only.py` / `test_arb_usb_probe.py` (historical wedge isolation — the probe deliberately wedges the box), and headless `test_arb_transport.py`.
- **No LAN? Use the EasyWaveX flash-drive workflow.** The arb editor's **"Export for EasyWaveX (flash drive)…"** button writes the waveform in the exact CSV template EasyWaveX requires (`easywave_export.py`; byte-verified against the lab's template, kept as `easywavex_template.csv`): CRLF line endings, `data length,16384` + `frequency`/`amp`/`offset`/`phase` header, 7 blank lines, `xpos,value`, exactly 16,384 1-based rows. Defaults follow the lab rules — frequency = 1/T_total, amp = highest voltage value (**1 V in the file = 1 kV at the Trek output**), offset = amp/2, phase 0. Nothing is sent to the instrument: copy the CSV to a flash drive and import it in EasyWaveX manually. Headless self-test `test_easywave_export.py` rebuilds the lab template byte-identically.
- **Tektronix MSO24 frequency measurement needs `FREQUENCY`, not `FREQ`.** The `MEASUREMENT:IMMED:TYPE` token must be `FREQUENCY`; `FREQ` is accepted without error but returns a garbage ~2 Hz value (bench-verified 2026-06-16). The amplitude/mean/period/pk2pk tokens are fine. `TekMSO24.get_all_measurements` uses the correct token. Run `test_siggen_scope.py` (sig gen → scope BNC) to self-test the measurement path.
