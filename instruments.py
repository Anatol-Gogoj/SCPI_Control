#!/usr/bin/env python3
"""
Lab instrument control library for USB-TMC devices
"""
import os
import time
import struct
import errno

class USBTMC:
    """Low-level USB-TMC communication"""
    def __init__(self, device):
        self.device = device
        self.file = os.open(device, os.O_RDWR)
        self.timeout = 2.0  # Default timeout in seconds
    
    def write(self, command):
        if not command.endswith('\n'):
            command += '\n'
        os.write(self.file, command.encode('ascii'))
    
    def read(self, length=4000):
        """Read with basic retry logic"""
        end_time = time.time() + self.timeout
        data = b''
        
        while time.time() < end_time:
            try:
                chunk = os.read(self.file, length)
                if chunk:
                    data += chunk
                    # Check if we got a complete response (ends with newline)
                    if b'\n' in chunk:
                        break
            except OSError as e:
                if e.errno == errno.EAGAIN:
                    time.sleep(0.01)
                    continue
                raise
            time.sleep(0.01)
        
        if not data:
            raise TimeoutError(f"Read timeout after {self.timeout}s")
        
        return data.decode('ascii').strip()
    
    def read_raw(self, length=1000000):
        """Read raw binary data (for waveforms)"""
        end_time = time.time() + self.timeout
        
        while time.time() < end_time:
            try:
                data = os.read(self.file, length)
                if data:
                    return data
            except OSError as e:
                if e.errno == errno.EAGAIN:
                    time.sleep(0.01)
                    continue
                raise
            time.sleep(0.01)
        
        raise TimeoutError(f"Read timeout after {self.timeout}s")
    
    def ask(self, command):
        self.write(command)
        time.sleep(0.1)  # Give instrument time to process
        return self.read()
    
    def close(self):
        os.close(self.file)


class BK894(USBTMC):
    """B&K Precision 894 LCR Meter"""
    
    MODES = {
        'CPD': 'Capacitance + Dissipation',
        'CPQ': 'Capacitance + Q Factor',
        'CPG': 'Capacitance + Conductance',
        'CPRP': 'Capacitance + Parallel Resistance',
        'LSRS': 'Inductance (series) + Resistance (series)',
        'LSRD': 'Inductance (series) + Resistance (DC)',
        'LPRS': 'Inductance (parallel) + Resistance (series)',
        'LPRP': 'Inductance (parallel) + Resistance (parallel)',
        'RX': 'Resistance + Reactance',
        'ZTD': 'Impedance + Phase (degrees)',
        'ZTR': 'Impedance + Phase (radians)',
    }
    
    def __init__(self, device):
        super().__init__(device)
        self.idn = self.ask('*IDN?')
    
    def set_mode(self, mode):
        """Set measurement mode (e.g., 'CPD', 'LSRS')"""
        if mode.upper() not in self.MODES:
            raise ValueError(f"Invalid mode. Choose from: {list(self.MODES.keys())}")
        self.write(f':FUNC:IMP {mode.upper()}')
    
    def set_frequency(self, freq_hz):
        """Set test frequency (100 Hz to 200 kHz)"""
        if not 100 <= freq_hz <= 200000:
            raise ValueError("Frequency must be 100 Hz to 200 kHz")
        self.write(f':FREQ {freq_hz}')
    
    def set_voltage(self, voltage):
        """Set AC test voltage (0.01 to 2.0 V)"""
        if not 0.01 <= voltage <= 2.0:
            raise ValueError("Voltage must be 0.01 to 2.0 V")
        self.write(f':LEV:VOLT {voltage}')
    
    def measure(self):
        """
        Take a measurement and return (primary, secondary, status)
        Status codes: 0 = good, non-zero = error/warning
        """
        result = self.ask(':FETC?')
        primary, secondary, status = result.split(',')
        return float(primary), float(secondary), int(status)
    
    def get_config(self):
        """Get current measurement configuration"""
        mode = self.ask(':FUNC:IMP?')
        freq = float(self.ask(':FREQ?'))
        return {'mode': mode, 'frequency': freq}


class TekMSO24(USBTMC):
    """Tektronix MSO24 Oscilloscope"""
    
    def __init__(self, device):
        super().__init__(device)
        self.timeout = 5.0  # Longer timeout for scope operations
        self.idn = self.ask('*IDN?')
        # Set waveform transfer to binary for speed
        self.write('DATA:ENCDG RIBINARY')
        self.write('DATA:WIDTH 2')
    
    def reset(self):
        """Reset to default settings"""
        self.write('*RST')
        time.sleep(1)
    
    def autoset(self):
        """Run autoset"""
        self.write('AUTOSET EXECUTE')
        time.sleep(2)
    
    # Channel control
    def set_channel_enable(self, channel, enable=True):
        """Enable/disable a channel (1-4)"""
        state = 'ON' if enable else 'OFF'
        self.write(f'SELECT:CH{channel} {state}')
    
    def set_vertical(self, channel, scale, position=0, coupling='DC'):
        """
        Configure vertical settings
        channel: 1-4
        scale: volts/div (e.g., 1.0 for 1V/div)
        position: vertical position in divisions
        coupling: 'AC', 'DC', or 'GND'
        """
        self.write(f'CH{channel}:SCALE {scale}')
        self.write(f'CH{channel}:POSITION {position}')
        self.write(f'CH{channel}:COUPLING {coupling}')
    
    def set_horizontal(self, scale, position=0):
        """
        Configure horizontal (time base)
        scale: seconds/div (e.g., 1e-3 for 1ms/div)
        position: horizontal position in seconds
        """
        self.write(f'HORIZONTAL:SCALE {scale}')
        self.write(f'HORIZONTAL:POSITION {position}')
    
    # Triggering
    def set_trigger_edge(self, source='CH1', level=0, slope='RISE'):
        """
        Configure edge trigger
        source: 'CH1', 'CH2', etc.
        level: trigger level in volts
        slope: 'RISE' or 'FALL'
        """
        self.write(f'TRIGGER:A:TYPE EDGE')
        self.write(f'TRIGGER:A:EDGE:SOURCE {source}')
        self.write(f'TRIGGER:A:LEVEL:{source} {level}')
        self.write(f'TRIGGER:A:EDGE:SLOPE {slope}')
    
    def single(self):
        """Arm single acquisition"""
        self.write('ACQUIRE:STOPAFTER SEQUENCE')
        self.write('ACQUIRE:STATE RUN')
    
    def run(self):
        """Start continuous acquisition"""
        self.write('ACQUIRE:STOPAFTER RUNSTOP')
        self.write('ACQUIRE:STATE RUN')
    
    def stop(self):
        """Stop acquisition"""
        self.write('ACQUIRE:STATE STOP')
    
    # Measurements
    def measure(self, meas_type, channel):
        """
        Take an automated measurement
        meas_type: 'FREQ', 'PERIOD', 'MEAN', 'PK2PK', 'RMS', 'AMPLITUDE', 
                   'RISE', 'FALL', 'PWIDTH', 'NWIDTH', etc.
        channel: 1-4
        Returns: measurement value (float) or None if invalid
        """
        self.write(f'MEASUREMENT:IMMED:TYPE {meas_type}')
        self.write(f'MEASUREMENT:IMMED:SOURCE CH{channel}')
        result = self.ask('MEASUREMENT:IMMED:VALUE?')
        try:
            val = float(result)
            # Check for invalid measurements (Tek returns huge numbers)
            if abs(val) > 1e30:
                return None
            return val
        except:
            return None
    
    def get_all_measurements(self, channel):
        """Get common measurements for a channel"""
        measurements = {}
        for meas in ['FREQ', 'PERIOD', 'MEAN', 'PK2PK', 'RMS', 'AMPLITUDE']:
            measurements[meas.lower()] = self.measure(meas, channel)
        return measurements
    
    # Waveform acquisition
    def get_waveform(self, channel):
        """
        Acquire waveform data from a channel
        Returns: dict with 't' (time array) and 'v' (voltage array)
        """
        # Set data source
        self.write(f'DATA:SOURCE CH{channel}')
        time.sleep(0.1)
        
        # Get waveform preamble parameters individually
        nr_pt = int(self.ask('WFMPRE:NR_PT?'))
        xincr = float(self.ask('WFMPRE:XINCR?'))
        pt_off = int(self.ask('WFMPRE:PT_OFF?'))
        xzero = float(self.ask('WFMPRE:XZERO?'))
        ymult = float(self.ask('WFMPRE:YMULT?'))
        yoff = float(self.ask('WFMPRE:YOFF?'))
        yzero = float(self.ask('WFMPRE:YZERO?'))
        
        # Request waveform data
        self.write('CURVE?')
        time.sleep(0.2)
        
        # Read binary waveform data
        raw_data = self.read_raw()
        
        # Parse TMC header: #<N><length><data>
        header_len = 2 + int(chr(raw_data[1]))
        data_bytes = raw_data[header_len:-1]
        
        # Unpack binary data (signed 16-bit integers, big-endian)
        samples = struct.unpack(f'>{len(data_bytes)//2}h', data_bytes)
        
        # Convert to voltage
        voltages = [(s - yoff) * ymult + yzero for s in samples]
        
        # Generate time array
        times = [xzero + (i * xincr) for i in range(len(voltages))]
        
        return {'t': times, 'v': voltages, 'dt': xincr, 'npts': len(voltages)}


# Example usage
if __name__ == "__main__":
    # LCR Meter example
    print("="*60)
    print("BK Precision 894 LCR Meter")
    print("="*60)
    
    try:
        lcr = BK894("/dev/usbtmc1")
        print(f"Connected: {lcr.idn}")
        
        lcr.set_mode('CPD')
        lcr.set_frequency(1000)
        lcr.set_voltage(1.0)
        time.sleep(0.5)
        
        config = lcr.get_config()
        print(f"Configuration: {config}\n")
        
        print("Measurements:")
        for i in range(5):
            C, D, status = lcr.measure()
            print(f"  {i+1}: C = {C*1e9:.3f} nF, D = {D:.5f}")
            time.sleep(0.2)
        
        lcr.close()
    except Exception as e:
        print(f"LCR Error: {e}")
    
    # Oscilloscope example
    print("\n" + "="*60)
    print("Tektronix MSO24 Oscilloscope")
    print("="*60)
    
    try:
        scope = TekMSO24("/dev/usbtmc2")
        print(f"Connected: {scope.idn}\n")
        
        # Configure scope
        scope.set_channel_enable(1, True)
        scope.set_vertical(1, scale=1.0, coupling='DC')
        scope.set_horizontal(scale=1e-3)
        scope.set_trigger_edge(source='CH1', level=0, slope='RISE')
        
        # Take measurements
        print("Automated measurements on CH1:")
        meas = scope.get_all_measurements(1)
        for name, value in meas.items():
            if value is not None:
                unit = 'Hz' if name == 'freq' else ('s' if name == 'period' else 'V')
                print(f"  {name.upper():10s}: {value:.6g} {unit}")
            else:
                print(f"  {name.upper():10s}: No signal")
        
        scope.close()
    except Exception as e:
        print(f"Scope Error: {e}")