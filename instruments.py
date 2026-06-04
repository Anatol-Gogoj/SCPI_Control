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
import time
import struct
import threading
import pyvisa


# --------------------------------------------------------------------------
# Shared PyVISA ResourceManager
# --------------------------------------------------------------------------

_RM = None
_RM_LOCK = threading.Lock()


def get_resource_manager():
    """Process-wide PyVISA ResourceManager (pyvisa-py backend)."""
    global _RM
    with _RM_LOCK:
        if _RM is None:
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
        """Set test frequency (100 Hz to 200 kHz)."""
        if not 100 <= freq_hz <= 200000:
            raise ValueError("Frequency must be 100 Hz to 200 kHz")
        self.write(f':FREQ {freq_hz}')

    def set_voltage(self, voltage):
        """Set AC test voltage (0.01 to 2.0 V)."""
        if not 0.01 <= voltage <= 2.0:
            raise ValueError("Voltage must be 0.01 to 2.0 V")
        self.write(f':LEV:VOLT {voltage}')

    def measure(self):
        """Return (primary, secondary, status). status 0 = good."""
        result = self.ask(':FETC?')
        primary, secondary, status = result.split(',')
        return float(primary), float(secondary), int(status)

    def get_config(self):
        return {
            'mode': self.ask(':FUNC:IMP?'),
            'frequency': float(self.ask(':FREQ?')),
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
        return {
            meas.lower(): self.measure(meas, channel)
            for meas in ('FREQ', 'PERIOD', 'MEAN', 'PK2PK', 'RMS', 'AMPLITUDE')
        }

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

        # IEEE 488.2 definite-length block: '#<N><length><data>'
        header_len = 2 + int(chr(raw_data[1]))
        data_bytes = raw_data[header_len:-1]

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
    TIMEOUT_MS = 2000

    WAVEFORMS = ('SINE', 'SQUARE', 'RAMP', 'PULSE', 'NOISE', 'ARB', 'DC')

    def set_waveform(self, channel, wave):
        if wave.upper() not in self.WAVEFORMS:
            raise ValueError(f"Waveform must be one of {self.WAVEFORMS}")
        self.write(f'C{channel}:BSWV WVTP,{wave.upper()}')

    def set_frequency(self, channel, freq_hz):
        self.write(f'C{channel}:BSWV FRQ,{freq_hz}')

    def set_amplitude_vpp(self, channel, amp_vpp):
        self.write(f'C{channel}:BSWV AMP,{amp_vpp}')

    def set_offset(self, channel, offset_v):
        self.write(f'C{channel}:BSWV OFST,{offset_v}')

    def set_output(self, channel, on):
        self.write(f'C{channel}:OUTP {"ON" if on else "OFF"}')

    def get_basic_wave(self, channel):
        """Raw query of the current basic-wave settings on a channel."""
        return self.ask(f'C{channel}:BSWV?')


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
            inst.close()
        except Exception as e:
            print(f"  {type(e).__name__}: {e}")
