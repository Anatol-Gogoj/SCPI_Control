#!/usr/bin/env python3
"""
Lab instrument control library, PyVISA-py backed (USB-TMC + USB-serial).

Replaces the previous kernel-usbtmc transport (os.open('/dev/usbtmc*')) with
pyvisa.ResourceManager('@py'). On the RHEL9 lab box the kernel `usbtmc` module
is blacklisted; this module talks to USB-TMC instruments via libusb (pyusb)
through PyVISA's '@py' backend. Per-instrument udev rules in
/etc/udev/rules.d/99-usbtmc.rules grant mode 0666 to the matching USB device
nodes so a non-root user can claim them.

Instrument classes auto-discover by USB VID/PID via PyVISA's resource list:

    lcr   = BK894()
    scope = TekMSO24()
    sg    = BK4055B()

Pass an explicit resource string only to override discovery (e.g. when more
than one unit of the same model is connected):

    lcr = BK894(resource='USB0::1137::10279::SOMESERIAL::0::INSTR')

The DC power supply behind the CP2102 USB-UART bridge uses serial transport
via pyserial (NOT USB-TMC); see SerialDCSupply below.
"""
import re
import time
import struct
import threading


# --------------------------------------------------------------------------
# Shared PyVISA ResourceManager
# --------------------------------------------------------------------------

_RM = None
_RM_LOCK = threading.Lock()


def get_resource_manager():
    """Process-wide PyVISA ResourceManager (pyvisa-py backend).

    pyvisa is imported lazily so that merely importing this module (e.g. for the
    pure waveform/preset logic, or the hardware-free editor demo) does not
    require pyvisa to be installed -- it's only needed to actually open a device.
    """
    global _RM
    with _RM_LOCK:
        if _RM is None:
            import pyvisa
            _RM = pyvisa.ResourceManager('@py')
        return _RM


def list_usb_instruments():
    """Return [(resource_string, vid_int, pid_int)] for all USB-TMC resources."""
    rm = get_resource_manager()
    found = []
    for r in rm.list_resources():
        if not r.startswith('USB'):
            continue
        parts = r.split('::')
        try:
            found.append((r, int(parts[1]), int(parts[2])))
        except (IndexError, ValueError):
            continue
    return found


# --------------------------------------------------------------------------
# USB-TMC base class
# --------------------------------------------------------------------------

class VisaInstrument:
    """Base class for USB-TMC instruments accessed through pyvisa-py.

    Subclasses set:
      VID, PID    int   USB vendor/product IDs (hex literals OK)
      TIMEOUT_MS  int   default PyVISA I/O timeout in milliseconds
    """
    VID = None
    PID = None
    TIMEOUT_MS = 2000

    def __init__(self, resource=None, rm=None):
        rm = rm if rm is not None else get_resource_manager()
        if resource is None:
            resource = self._discover(rm)
            if resource is None:
                raise RuntimeError(
                    f"{self.__class__.__name__}: no device found with "
                    f"VID=0x{self.VID:04x} PID=0x{self.PID:04x}. "
                    f"Check that the instrument is powered and enumerated "
                    f"(lsusb | grep -i {self.VID:04x}:{self.PID:04x})."
                )
        self.resource = resource
        self.inst = rm.open_resource(resource)
        self.inst.timeout = self.TIMEOUT_MS
        self.inst.read_termination = '\n'
        self.inst.write_termination = '\n'
        # Flush any stale device output (e.g. an unread binary CURVE? response
        # from a previous run) so the first query doesn't read garbage.
        try:
            self.inst.clear()
        except Exception:
            pass
        self.idn = self.inst.query('*IDN?').strip()
        self._post_open()

    def _post_open(self):
        """Subclass hook for additional setup after open + IDN."""
        pass

    @classmethod
    def _discover(cls, rm):
        """Return the first USB resource string matching cls.VID/cls.PID, or None."""
        for r, vid, pid in list_usb_instruments():
            if vid == cls.VID and pid == cls.PID:
                return r
        return None

    def write(self, command):
        self.inst.write(command)

    def read(self):
        return self.inst.read().strip()

    def read_raw(self):
        return self.inst.read_raw()

    def write_raw(self, data):
        """Write raw bytes verbatim (no termination/encoding munging).

        Needed for commands that carry binary payloads, e.g. the 4055B's
        WVDT arbitrary-waveform upload where sample bytes follow the ASCII
        header directly.
        """
        return self.inst.write_raw(data)

    def write_raw_oneshot(self, data):
        """Write a large binary payload as ONE USBTMC Bulk-OUT message.

        pyvisa-py's USBTMC writer wraps every wMaxPacketSize-sized slice
        (64 bytes on this bench) in its own BulkOutMessage header, which
        corrupts large binary writes -- a 32 KB arb upload gets hundreds of
        12-byte headers injected mid-data, so the waveform stores as garbage
        and the endpoint can wedge. Temporarily raise the endpoint's
        wMaxPacketSize so the whole payload goes out as a single USBTMC
        message (libusb still packetizes at the USB layer). Falls back to a
        plain write_raw if the backend internals differ.
        """
        try:
            sess = self.inst.visalib.sessions[self.inst.session]
            ep = sess.interface.usb_send_ep
        except (AttributeError, KeyError, TypeError):
            return self.inst.write_raw(data)
        old = ep.wMaxPacketSize
        ep.wMaxPacketSize = max(old, len(data) + 64)
        try:
            return self.inst.write_raw(data)
        finally:
            ep.wMaxPacketSize = old

    def ask(self, command):
        """Write a command and return its response (newline-stripped)."""
        return self.inst.query(command).strip()

    # Alias matching pyvisa convention; both ask() and query() are used here.
    query = ask

    def close(self):
        try:
            self.inst.close()
        except Exception:
            pass


# --------------------------------------------------------------------------
# B&K Precision 894 LCR Meter (USB-TMC)
# --------------------------------------------------------------------------

class BK894(VisaInstrument):
    """B&K Precision 894 LCR Meter.

    NOTE: this meter's USB serial-number descriptor is malformed (returns
    non-ASCII garbage). PyVISA enumerates it anyway since pyvisa-py addresses
    by bus/device under the hood. The true serial number is exposed only
    through `*IDN?`.
    """
    VID = 0x0471
    PID = 0x2827
    TIMEOUT_MS = 2000

    MODES = {
        'CPD':  'Capacitance + Dissipation',
        'CPQ':  'Capacitance + Q Factor',
        'CPG':  'Capacitance + Conductance',
        'CPRP': 'Capacitance + Parallel Resistance',
        'LSRS': 'Inductance (series) + Resistance (series)',
        'LSRD': 'Inductance (series) + Resistance (DC)',
        'LPRS': 'Inductance (parallel) + Resistance (series)',
        'LPRP': 'Inductance (parallel) + Resistance (parallel)',
        'RX':   'Resistance + Reactance',
        'ZTD':  'Impedance + Phase (degrees)',
        'ZTR':  'Impedance + Phase (radians)',
    }

    def set_mode(self, mode):
        """Set measurement mode (e.g. 'CPD', 'LSRS'). See MODES dict."""
        if mode.upper() not in self.MODES:
            raise ValueError(f"Invalid mode. Choose from: {list(self.MODES.keys())}")
        self.write(f':FUNC:IMP {mode.upper()}')

    def set_frequency(self, freq_hz):
        """Set test frequency (100 Hz to 500 kHz)."""
        if not 100 <= freq_hz <= 500000:
            raise ValueError("Frequency must be 100 Hz to 500 kHz")
        self.write(f':FREQ {freq_hz}')

    def set_voltage(self, voltage):
        """Set AC test voltage (0.01 to 2.0 V).

        The BK 894's AC test-level command is plain ``:VOLT`` (set and query),
        bench-verified 2026-06-16 -- NOT ``:LEV:VOLT`` nor the E4980A-style
        ``:VOLT:LEV``; both are silently rejected on this unit. The meter
        clamps the level at 2.0 V, so higher values are out of range.
        """
        if not 0.01 <= voltage <= 2.0:
            raise ValueError("Voltage must be 0.01 to 2.0 V")
        self.write(f':VOLT {voltage}')

    def measure(self):
        """Return (primary, secondary, status). status 0 = good."""
        result = self.ask(':FETC?')
        primary, secondary, status = result.split(',')
        return float(primary), float(secondary), int(status)

    def get_config(self):
        return {
            'mode': self.ask(':FUNC:IMP?'),
            'frequency': float(self.ask(':FREQ?')),
            'voltage': float(self.ask(':VOLT?')),
        }


# --------------------------------------------------------------------------
# Tektronix MSO24 Oscilloscope (USB-TMC)
# --------------------------------------------------------------------------

class TekMSO24(VisaInstrument):
    """Tektronix MSO24 Oscilloscope."""
    VID = 0x0699
    PID = 0x0105
    TIMEOUT_MS = 5000

    def _post_open(self):
        # Binary waveform transfer for speed
        self.write('DATA:ENCDG RIBINARY')
        self.write('DATA:WIDTH 2')

    def reset(self):
        self.write('*RST')
        time.sleep(1)

    def autoset(self):
        self.write('AUTOSET EXECUTE')
        time.sleep(2)

    def set_channel_enable(self, channel, enable=True):
        state = 'ON' if enable else 'OFF'
        self.write(f'SELECT:CH{channel} {state}')

    def set_vertical(self, channel, scale, position=0, coupling='DC'):
        """scale = volts/div, position in divisions, coupling 'AC'|'DC'|'GND'."""
        self.write(f'CH{channel}:SCALE {scale}')
        self.write(f'CH{channel}:POSITION {position}')
        self.write(f'CH{channel}:COUPLING {coupling}')

    def set_horizontal(self, scale, position=0):
        """scale = seconds/div, position in seconds."""
        self.write(f'HORIZONTAL:SCALE {scale}')
        self.write(f'HORIZONTAL:POSITION {position}')

    def set_trigger_edge(self, source='CH1', level=0, slope='RISE'):
        self.write('TRIGGER:A:TYPE EDGE')
        self.write(f'TRIGGER:A:EDGE:SOURCE {source}')
        self.write(f'TRIGGER:A:LEVEL:{source} {level}')
        self.write(f'TRIGGER:A:EDGE:SLOPE {slope}')

    def single(self):
        self.write('ACQUIRE:STOPAFTER SEQUENCE')
        self.write('ACQUIRE:STATE RUN')

    def run(self):
        self.write('ACQUIRE:STOPAFTER RUNSTOP')
        self.write('ACQUIRE:STATE RUN')

    def stop(self):
        self.write('ACQUIRE:STATE STOP')

    def measure(self, meas_type, channel):
        """Automated measurement. Returns float or None for invalid signals."""
        self.write(f'MEASUREMENT:IMMED:TYPE {meas_type}')
        self.write(f'MEASUREMENT:IMMED:SOURCE CH{channel}')
        result = self.ask('MEASUREMENT:IMMED:VALUE?')
        try:
            val = float(result)
            if abs(val) > 1e30:
                return None
            return val
        except ValueError:
            return None

    def get_all_measurements(self, channel):
        # The MSO24 frequency measurement token is FREQUENCY -- 'FREQ' is
        # silently accepted but returns a garbage value (~2 Hz). Keys stay
        # short for callers. (Root cause of the GUI's frequency issue.)
        types = (('freq', 'FREQUENCY'), ('period', 'PERIOD'), ('mean', 'MEAN'),
                 ('pk2pk', 'PK2PK'), ('rms', 'RMS'), ('amplitude', 'AMPLITUDE'))
        return {key: self.measure(scpi, channel) for key, scpi in types}

    def get_waveform(self, channel):
        """Acquire a channel waveform. Returns {'t', 'v', 'dt', 'npts'}."""
        self.write(f'DATA:SOURCE CH{channel}')
        time.sleep(0.1)

        nr_pt = int(self.ask('WFMPRE:NR_PT?'))
        xincr = float(self.ask('WFMPRE:XINCR?'))
        pt_off = int(self.ask('WFMPRE:PT_OFF?'))
        xzero = float(self.ask('WFMPRE:XZERO?'))
        ymult = float(self.ask('WFMPRE:YMULT?'))
        yoff = float(self.ask('WFMPRE:YOFF?'))
        yzero = float(self.ask('WFMPRE:YZERO?'))

        self.write('CURVE?')
        time.sleep(0.2)
        raw_data = self.read_raw()

        # IEEE 488.2 definite-length block: '#<N><N-digit byte count><data>'.
        # Parse the declared length (robust to a trailing newline or none) and
        # complete the read if the block spans more than one chunk.
        ndig = int(chr(raw_data[1]))
        nbytes = int(raw_data[2:2 + ndig])
        start = 2 + ndig
        while len(raw_data) < start + nbytes:
            more = self.read_raw()
            if not more:
                break
            raw_data += more
        data_bytes = raw_data[start:start + nbytes]
        data_bytes = data_bytes[:len(data_bytes) // 2 * 2]   # whole int16 samples

        samples = struct.unpack(f'>{len(data_bytes)//2}h', data_bytes)
        voltages = [(s - yoff) * ymult + yzero for s in samples]
        times = [xzero + (i * xincr) for i in range(len(voltages))]

        return {'t': times, 'v': voltages, 'dt': xincr, 'npts': len(voltages)}


# --------------------------------------------------------------------------
# B&K Precision 4055B Signal Generator (USB-TMC, Siglent SDG-class OEM)
# --------------------------------------------------------------------------

class BK4055B(VisaInstrument):
    """B&K Precision 4055B Signal Generator.

    SCPI syntax matches the Siglent SDG2000X series (which the 4055B is OEM-
    rebadged from). Channel index in SCPI is 1 or 2.

    NOTE: command syntax is conservatively chosen to match the Siglent SDG
    programming manual; verify against the BK 4055B-specific manual before
    relying on edge-case behavior.
    """
    VID = 0xf4ec
    PID = 0xee38
    # 2 s proved too tight for BSWV? read-back right after a multi-parameter
    # write (VI_ERROR_TMO seen on the bench, 2026-06-11).
    TIMEOUT_MS = 5000

    WAVEFORMS = ('SINE', 'SQUARE', 'RAMP', 'PULSE', 'NOISE', 'ARB', 'DC')

    # BSWV keys whose values are numeric (carry a unit suffix on read-back).
    # WVTP and any other key are kept as raw strings by get_basic_wave_dict.
    _BSWV_NUMERIC = (
        'FRQ', 'PERI', 'AMP', 'AMPVRMS', 'OFST', 'HLEV', 'LLEV',
        'PHSE', 'DUTY', 'SYM', 'RISE', 'FALL', 'DLY', 'WIDTH',
    )

    @staticmethod
    def _fmt_param(value):
        """Format a numeric SCPI parameter the firmware parses reliably.

        The 4055B mis-parses some decimal-suffixed values: 'DUTY,50.0' lands
        as 5% duty (bench-verified 2026-06-11; the box appears to read the
        digits in 0.01% units). Send plain decimal notation with no trailing
        '.0' and no scientific exponent: 50.0 -> '50', 1.68e-08 ->
        '0.0000000168'. Non-floats pass through unchanged.
        """
        if isinstance(value, float):
            return f'{value:.12f}'.rstrip('0').rstrip('.') or '0'
        return str(value)

    @staticmethod
    def _strip_unit(value):
        """Parse a BSWV/OUTP numeric value, dropping any unit suffix.

        Values come back like '100HZ', '0.01S', '2V', '1.41421Vrms'. Leading
        sign, digits, decimal point and exponent form the number; everything
        after is the unit. Returns a float, or the original string if it does
        not start with a number.
        """
        m = re.match(r'[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?', value.strip())
        return float(m.group()) if m else value

    def set_waveform(self, channel, wave):
        if wave.upper() not in self.WAVEFORMS:
            raise ValueError(f"Waveform must be one of {self.WAVEFORMS}")
        self.write(f'C{channel}:BSWV WVTP,{wave.upper()}')

    def set_frequency(self, channel, freq_hz):
        self.write(f'C{channel}:BSWV FRQ,{self._fmt_param(freq_hz)}')

    def set_amplitude_vpp(self, channel, amp_vpp):
        self.write(f'C{channel}:BSWV AMP,{self._fmt_param(amp_vpp)}')

    def set_offset(self, channel, offset_v):
        self.write(f'C{channel}:BSWV OFST,{self._fmt_param(offset_v)}')

    def set_basic_wave(self, channel, **params):
        """Set several basic-wave parameters in one command.

        Keys are BSWV parameter names (case-insensitive), e.g.
        set_basic_wave(1, WVTP='SINE', FRQ=1000, AMP=2, OFST=0). Pushing them
        together is one bus round-trip rather than one per setter.
        """
        if not params:
            return
        if 'WVTP' in {k.upper() for k in params}:
            wave = next(v for k, v in params.items() if k.upper() == 'WVTP')
            if str(wave).upper() not in self.WAVEFORMS:
                raise ValueError(f"Waveform must be one of {self.WAVEFORMS}")
        body = ','.join(f'{k.upper()},{self._fmt_param(v)}'
                        for k, v in params.items())
        self.write(f'C{channel}:BSWV {body}')

    def set_output(self, channel, on):
        self.write(f'C{channel}:OUTP {"ON" if on else "OFF"}')

    def set_output_full(self, channel, on, load=None, polarity=None):
        """Set output state plus optional load and polarity in one command.

        load: a resistance in ohms (e.g. 50) or 'HZ' for high impedance.
        polarity: 'NOR' (normal) or 'INVT' (inverted).
        """
        parts = ['ON' if on else 'OFF']
        if load is not None:
            parts.append(f'LOAD,{load}')
        if polarity is not None:
            parts.append(f'PLRT,{polarity.upper()}')
        self.write(f'C{channel}:OUTP {",".join(parts)}')

    def set_load_polarity(self, channel, load=None, polarity=None):
        """Set output load and/or polarity WITHOUT changing the on/off state.

        The SDG dialect accepts OUTP with only LOAD/PLRT arguments, leaving the
        output state untouched (e.g. 'C1:OUTP LOAD,50'). Used by the GUI so
        Apply can configure the channel while a separate button gates the
        output.
        """
        parts = []
        if load is not None:
            parts.append(f'LOAD,{load}')
        if polarity is not None:
            parts.append(f'PLRT,{polarity.upper()}')
        if parts:
            self.write(f'C{channel}:OUTP {",".join(parts)}')

    def get_basic_wave(self, channel):
        """Raw query of the current basic-wave settings on a channel."""
        return self.ask(f'C{channel}:BSWV?')

    def get_basic_wave_dict(self, channel):
        """Parse the current basic-wave settings into a dict.

        The instrument echoes e.g.
            C1:BSWV WVTP,SINE,FRQ,100HZ,PERI,0.01S,AMP,2V,OFST,0V,...
        The leading 'C1:BSWV ' echo is stripped, the remainder split into
        KEY,VALUE pairs; numeric values (see _BSWV_NUMERIC) are converted to
        float with their unit suffix removed. WVTP and unknown keys are kept as
        raw strings. Tolerant of the variable, wave-type-dependent key list.
        """
        raw = self.get_basic_wave(channel)
        body = raw.split(' ', 1)[1] if ' ' in raw else raw
        tokens = [t for t in body.split(',') if t != '']
        out = {}
        for key, value in zip(tokens[0::2], tokens[1::2]):
            key = key.upper()
            out[key] = self._strip_unit(value) if key in self._BSWV_NUMERIC else value
        return out

    def get_output_dict(self, channel):
        """Parse output state into {'state': bool, 'load': str, 'polarity': str}.

        The instrument echoes e.g. 'C1:OUTP ON,LOAD,HZ,PLRT,NOR'. Load is kept
        as a string ('HZ' or a numeric ohm value like '50').
        """
        raw = self.ask(f'C{channel}:OUTP?')
        body = raw.split(' ', 1)[1] if ' ' in raw else raw
        tokens = [t for t in body.split(',') if t != '']
        state = tokens[0].upper() == 'ON' if tokens else False
        pairs = dict(zip(tokens[1::2], tokens[2::2]))
        return {
            'state': state,
            'load': pairs.get('LOAD', 'HZ'),
            'polarity': pairs.get('PLRT', 'NOR'),
        }

    # -- arbitrary waveforms -------------------------------------------------

    ARB_MAX_POINTS = 16384      # editor design cap / DDS table depth
    ARB_POINTS = 16384          # legacy fixed DDS buffer length (TrueArb does
                                # NOT need this -- kept for set_legacy callers)
    ARB_DEFAULT_POINTS = 1024   # default TrueArb upload depth: a short buffer
                                # (~2 KB) uploads fast and is far less likely to
                                # wedge the USBTMC endpoint than a 32 KB DDS one.

    @staticmethod
    def _resample(samples, n):
        """Circularly resample a periodic waveform to n points (linear)."""
        m = len(samples)
        if m == n:
            return list(samples)
        if m == 0:
            return [0.0] * n
        if m == 1:
            return [float(samples[0])] * n
        out = []
        for i in range(n):
            x = i * m / n              # phase in input samples [0, m)
            lo = int(x) % m
            frac = x - int(x)
            hi = (lo + 1) % m
            out.append(samples[lo] * (1 - frac) + samples[hi] * frac)
        return out

    @staticmethod
    def samples_to_int16(samples):
        """Normalise float samples to full-scale signed 16-bit LE bytes.

        Values are scaled so the largest |sample| maps to full scale when any
        |sample| exceeds 1.0; otherwise [-1, 1] maps directly. Full scale is
        +/-32767 (symmetric), little-endian two's complement per the Siglent
        SDG arb format.
        """
        if not samples:
            raise ValueError("samples must be a non-empty sequence")
        peak = max(abs(float(s)) for s in samples)
        scale = 32767.0 / peak if peak > 1.0 else 32767.0
        out = bytearray()
        for s in samples:
            v = int(round(float(s) * scale))
            v = max(-32768, min(32767, v))
            out += struct.pack('<h', v)
        return bytes(out)

    def build_wvdt(self, channel, name, samples, points=None, freq_hz=None,
                   amp_vpp=None, offset_v=None, phase_deg=None):
        """Build the raw ``WVDT`` upload bytes (no I/O). Returns (clean, blob).

        Uses the official SDG2000X/SDG6000X 16-bit-arb form (Siglent app note
        "Building an Arb with 16-bit steps"), with EVERY header field present::

            C<n>:WVDT WVNM,<name>,FREQ,<f>,TYPE,8,AMPL,<a>,OFST,<o>,PHASE,<p>,WAVEDATA,<int16 LE>

        The full field set is NOT optional over USBTMC: a minimal header
        (``WVNM,<name>,WAVEDATA,``) hard-wedges the 4055B's SCPI parser
        (bench-verified 2026-06-28 and 2026-07-02 -- the box stops answering
        reads until a front-panel power cycle; the tinylabs SDG2000X library
        gets away with the minimal form only over LAN). ``TYPE,8`` marks
        16-bit sample data. Sample bytes follow ``WAVEDATA,`` directly (not an
        IEEE block); no trailing terminator.

        Split out from upload_arb so it can be unit-tested headless and verified
        with ``test_arb_scope.py --readback`` without driving the bus.
        """
        clean = re.sub(r'[^A-Za-z0-9_]', '_', str(name))[:16]
        if not clean:
            raise ValueError(f"unusable arb name {name!r}")
        if not samples:
            raise ValueError("samples must be a non-empty sequence")
        n = points if points is not None else min(len(samples),
                                                  self.ARB_DEFAULT_POINTS)
        n = max(8, min(int(n), self.ARB_MAX_POINTS))
        data = self.samples_to_int16(self._resample(samples, n))
        # Field order matches the Siglent example; all fields always sent.
        fields = [
            f'WVNM,{clean}',
            f'FREQ,{self._fmt_param(freq_hz if freq_hz is not None else 1000)}',
            'TYPE,8',
            f'AMPL,{self._fmt_param(amp_vpp if amp_vpp is not None else 2.0)}',
            f'OFST,{self._fmt_param(offset_v if offset_v is not None else 0)}',
            f'PHASE,{self._fmt_param(phase_deg if phase_deg is not None else 0)}',
        ]
        header = f'C{channel}:WVDT ' + ','.join(fields) + ',WAVEDATA,'
        return clean, header.encode('latin1') + data

    def upload_arb(self, channel, name, samples, freq_hz=None, amp_vpp=None,
                   offset_v=None, phase_deg=None, points=None):
        """Upload an arbitrary waveform to the instrument's user memory.

        Defaults to a SHORT buffer (``ARB_DEFAULT_POINTS`` = 1024 points, ~2 KB)
        in the spec-correct X-series WVDT form -- both to fit the box's dedicated
        short-arb (TrueArb) path and because small uploads are far less likely to
        wedge the pyvisa-py USBTMC endpoint than a 32 KB DDS upload (issue #20).
        Pass ``points`` to override the resample depth (capped to ARB_MAX_POINTS).

        Pair with ``set_sample_rate(channel, 'TARB', samples*freq)`` so the box
        plays the exact buffer at the right rate, then ``select_arb``. Output
        level can also be set via ``set_basic_wave``.

        name is sanitised to [A-Za-z0-9_], max 16 chars; returns the clean name.
        """
        clean, blob = self.build_wvdt(channel, name, samples, points=points,
                                      freq_hz=freq_hz, amp_vpp=amp_vpp,
                                      offset_v=offset_v, phase_deg=phase_deg)
        # Give the box headroom to finish storing before the next command, or
        # its USBTMC endpoint can wedge.
        old_to = self.inst.timeout
        self.inst.timeout = max(old_to or 0, 20000)
        try:
            self.write_raw_oneshot(blob)
        finally:
            self.inst.timeout = old_to
        return clean

    def set_sample_rate(self, channel, mode=None, value=None):
        """Set the arb playback mode (DDS vs TrueArb) and/or sample rate.

        ``C<n>:SRATE MODE,<DDS|TARB>,VALUE,<Sa/s>``. In TrueArb mode the box
        plays the uploaded buffer point-by-point at <value> Sa/s, so the output
        frequency = sample_rate / num_points -- this is the SDG2000X/4055B's
        dedicated path for short custom arbs and avoids the DDS 16k resample.
        The SDG2000X supports MODE and VALUE but not the INTER (interpolation)
        parameter, so it is intentionally not exposed here.
        """
        parts = []
        if mode is not None:
            parts.append(f'MODE,{mode}')
        if value is not None:
            parts.append(f'VALUE,{self._fmt_param(value)}')
        if not parts:
            raise ValueError("set_sample_rate needs a mode and/or value")
        self.write(f'C{channel}:SRATE ' + ','.join(parts))

    def get_sample_rate_dict(self, channel):
        """Parse 'C1:SRATE MODE,TARB,VALUE,1000000Sa/s,...' into a dict.

        Returns {'mode': 'TARB'|'DDS'|None, 'value': float|None}.
        """
        raw = self.ask(f'C{channel}:SRATE?')
        body = raw.split(' ', 1)[1] if ' ' in raw else raw
        tokens = [t for t in body.split(',') if t != '']
        pairs = {k.upper(): v for k, v in zip(tokens[0::2], tokens[1::2])}
        value = pairs.get('VALUE')
        if value is not None:
            try:
                value = self._strip_unit(value)
            except Exception:
                pass
        return {'mode': pairs.get('MODE'), 'value': value}

    def select_arb(self, channel, name):
        """Put a channel in ARB mode playing the named user waveform."""
        self.write(f'C{channel}:BSWV WVTP,ARB')
        self.write(f'C{channel}:ARWV NAME,{name}')

    def get_arb_dict(self, channel):
        """Parse 'C1:ARWV INDEX,2,NAME,wave1' -> {'index': 2, 'name': 'wave1'}.

        Either key may be missing depending on firmware; absent keys are
        returned as None.
        """
        raw = self.ask(f'C{channel}:ARWV?')
        body = raw.split(' ', 1)[1] if ' ' in raw else raw
        tokens = [t for t in body.split(',') if t != '']
        pairs = {k.upper(): v for k, v in zip(tokens[0::2], tokens[1::2])}
        index = pairs.get('INDEX')
        try:
            index = int(index) if index is not None else None
        except ValueError:
            pass
        return {'index': index, 'name': pairs.get('NAME')}


# --------------------------------------------------------------------------
# Serial-attached DC power supply (CP2102 USB-UART bridge)
# --------------------------------------------------------------------------

class SerialDCSupply:
    """Placeholder wrapper for the CP2102-connected DC power supply.

    The CP2102 USB-UART bridge (VID 0x10c4, PID 0xea60, s/n 503B22108)
    enumerates as /dev/ttyUSB0. The supply behind it has NOT been identified
    yet -- model, baudrate, and wire protocol are unknown. This stub provides
    a minimal write/read/query surface and a place to wire in specifics once
    the supply is identified.

    Requires pyserial (`pip install pyserial`); imported lazily so the rest of
    this module loads without it.
    """
    DEFAULT_PORT = '/dev/ttyUSB0'
    DEFAULT_BAUD = 9600   # PLACEHOLDER: confirm against the actual supply's spec

    def __init__(self, port=None, baud=None, timeout=1.0):
        import serial  # lazy import
        self.port = port or self.DEFAULT_PORT
        self.baud = baud or self.DEFAULT_BAUD
        self.ser = serial.Serial(self.port, baudrate=self.baud, timeout=timeout)
        self.idn = ''  # populate once protocol known

    def write(self, command):
        if not command.endswith('\n'):
            command += '\n'
        self.ser.write(command.encode('ascii'))

    def read(self, length=1024):
        return self.ser.read(length).decode('ascii', errors='replace').strip()

    def query(self, command):
        self.write(command)
        return self.read()

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass


# --------------------------------------------------------------------------
# Smoke test
# --------------------------------------------------------------------------

if __name__ == '__main__':
    print("--- Enumerated USB-TMC resources ---")
    for r, vid, pid in list_usb_instruments():
        print(f"  {r}  (VID=0x{vid:04x} PID=0x{pid:04x})")

    print()
    for cls in (BK894, TekMSO24, BK4055B):
        print(f"--- {cls.__name__} ---")
        try:
            inst = cls()
            print(f"  IDN: {inst.idn}")
            if isinstance(inst, BK4055B):
                for ch in (1, 2):
                    print(f"  CH{ch} BSWV: {inst.get_basic_wave_dict(ch)}")
            inst.close()
        except Exception as e:
            print(f"  {type(e).__name__}: {e}")
