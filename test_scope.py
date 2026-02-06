#!/usr/bin/env python3
from instruments import TekMSO24
import time

scope = TekMSO24("/dev/usbtmc2")
print(f"Connected: {scope.idn}\n")

# Configure for a typical signal
print("Configuring scope...")
scope.set_channel_enable(1, True)
scope.set_vertical(1, scale=1.0, position=0, coupling='DC')
scope.set_horizontal(scale=1e-3)  # 1 ms/div
scope.set_trigger_edge(source='CH1', level=0, slope='RISE')

scope.run()
time.sleep(1)

# Get measurements
print("\nCH1 Measurements:")
measurements = scope.get_all_measurements(1)
for name, value in measurements.items():
    if value is not None:
        unit = 'Hz' if name == 'freq' else ('s' if name == 'period' else 'V')
        print(f"  {name.upper():10s}: {value:.6g} {unit}")
    else:
        print(f"  {name.upper():10s}: No signal")

# Acquire waveform
print("\nAcquiring waveform...")
scope.single()
time.sleep(0.5)

waveform = scope.get_waveform(1)
print(f"  Points: {waveform['npts']}")
print(f"  Time step: {waveform['dt']*1e6:.3f} µs")
print(f"  Duration: {waveform['npts']*waveform['dt']*1e3:.3f} ms")
print(f"  Voltage range: {min(waveform['v']):.3f} to {max(waveform['v']):.3f} V")

scope.close()