#!/usr/bin/env python3
import os
import time

class USBTMC:
    """Simple USB-TMC interface using direct file I/O"""
    def __init__(self, device):
        self.device = device
        self.file = os.open(device, os.O_RDWR)
    
    def write(self, command):
        """Send SCPI command"""
        if not command.endswith('\n'):
            command += '\n'
        os.write(self.file, command.encode('ascii'))
    
    def read(self, length=4000):
        """Read response"""
        return os.read(self.file, length).decode('ascii').strip()
    
    def ask(self, command):
        """Send command and read response"""
        self.write(command)
        time.sleep(0.1)  # Small delay for instrument to process
        return self.read()
    
    def close(self):
        os.close(self.file)

# Test script
print("Scanning USB-TMC devices...\n")

for i in range(4):
    dev_path = f"/dev/usbtmc{i}"
    try:
        instr = USBTMC(dev_path)
        idn = instr.ask("*IDN?")
        print(f"{dev_path}:")
        print(f"  {idn}\n")
        instr.close()
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"{dev_path}: Error - {e}")