#!/usr/bin/env python3
import os
import time

class USBTMC:
    def __init__(self, device):
        self.device = device
        self.file = os.open(device, os.O_RDWR)
    
    def write(self, command):
        if not command.endswith('\n'):
            command += '\n'
        os.write(self.file, command.encode('ascii'))
    
    def read(self, length=4000):
        return os.read(self.file, length).decode('ascii').strip()
    
    def ask(self, command):
        self.write(command)
        time.sleep(0.05)
        return self.read()
    
    def close(self):
        os.close(self.file)

# Connect to BK 894
bk894 = USBTMC("/dev/usbtmc1")
print(f"Connected: {bk894.ask('*IDN?')}\n")

# Configure for capacitance measurement at 1 kHz
print("Configuring: C-D mode, 1 kHz, 1V")
bk894.write(':FUNC:IMP CPD')   # Capacitance + Dissipation
bk894.write(':FREQ 1000')       # 1 kHz test frequency
bk894.write(':LEV:VOLT 1.0')    # 1V AC test signal
time.sleep(0.5)

# Verify configuration
func = bk894.ask(':FUNC:IMP?')
freq = bk894.ask(':FREQ?')
print(f"Mode: {func}, Frequency: {freq} Hz\n")

# Take measurements
print("Measurements:")
for i in range(10):
    result = bk894.ask(':FETC?')
    C, D, status = result.split(',')
    C_val = float(C)
    D_val = float(D)
    
    # Convert capacitance to appropriate units
    if abs(C_val) < 1e-9:
        C_str = f"{C_val*1e12:.3f} pF"
    elif abs(C_val) < 1e-6:
        C_str = f"{C_val*1e9:.3f} nF"
    elif abs(C_val) < 1e-3:
        C_str = f"{C_val*1e6:.3f} µF"
    else:
        C_str = f"{C_val*1e3:.3f} mF"
    
    print(f"  {i+1}: C = {C_str:>12s}, D = {D_val:.5f}, Status = {status}")
    time.sleep(0.2)

bk894.close()