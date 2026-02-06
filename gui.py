#!/usr/bin/env python3
"""
Lab Instrument Control GUI
Multi-instrument control with CSV data logging
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import csv
import time
from datetime import datetime
from instruments import BK894, TekMSO24
import threading

class InstrumentControlGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Lab Instrument Control")
        self.root.geometry("1000x750")
        
        # Instrument connections
        self.lcr = None
        self.scope = None
        self.recording = False
        self.record_thread = None
        
        # Create notebook (tabbed interface)
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill='both', expand=True, padx=10, pady=10)
        
        # Create tabs
        self.create_lcr_tab()
        self.create_scope_tab()
        self.create_logging_tab()
        
        # Status bar
        self.status_bar = tk.Label(root, text="Ready", bd=1, relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)
        
        # Auto-connect on startup
        self.root.after(100, self.auto_connect)
    
    def auto_connect(self):
        """Attempt to connect to instruments on startup"""
        try:
            self.lcr = BK894("/dev/usbtmc1")
            self.lcr_status.config(text=f"Connected: {self.lcr.idn}", fg="green")
            self.update_lcr_config()
        except Exception as e:
            self.lcr_status.config(text=f"Not connected: {e}", fg="red")
        
        try:
            self.scope = TekMSO24("/dev/usbtmc2")
            self.scope_status.config(text=f"Connected: {self.scope.idn}", fg="green")
        except Exception as e:
            self.scope_status.config(text=f"Not connected: {e}", fg="red")
    
    def show_lcr_tips(self):
        """Show LCR meter tips"""
        tips = """BK Precision 894 LCR Meter - Usage Tips

CONNECTION:
- Instrument must be powered on before connecting
- USB connection appears as /dev/usbtmc1

CONFIGURATION:
- Mode: Select measurement type
  - CPD: Capacitance + Dissipation Factor (for capacitors)
  - LSRS: Inductance (series) + Resistance (for inductors)
  - RX: Resistance + Reactance (for general impedance)
- Frequency: Test frequency (100 Hz to 200 kHz)
  - Use 1 kHz for general capacitor testing
  - Use 100 Hz for electrolytics
  - Use 10 kHz+ for high-frequency components
- Voltage: AC test signal amplitude (0.01 to 2.0 V)
  - 1.0V is standard for most measurements

MEASUREMENTS:
- Single Measurement: Take one reading
- Start Continuous: Update display every 200ms
- Stop: Halt continuous measurements
- Status = 0 means good measurement
- Very small values (~fF) indicate open circuit

BEST PRACTICES:
- Keep test leads short to minimize stray capacitance
- For low-impedance measurements, use 4-wire Kelvin clips
- Allow readings to settle (2-3 seconds) after changing frequency
- Zero/open/short compensation improves accuracy (see manual)"""
        
        messagebox.showinfo("LCR Meter Tips", tips)
    
    def show_scope_tips(self):
        """Show oscilloscope tips"""
        tips = """Tektronix MSO24 Oscilloscope - Usage Tips

CONNECTION:
- Scope must be powered on before connecting
- USB connection appears as /dev/usbtmc2

CHANNEL CONFIGURATION:
- Enable/Disable: Turn channels on/off for viewing
- Vertical (V/div): Voltage scale per division
  - Adjust so signal fills ~60-80% of screen height
- Coupling: 
  - DC: Show full signal including DC offset
  - AC: Block DC component, show AC only
  - GND: Ground input to check baseline
- Horizontal (s/div): Time scale per division
  - Adjust to show 2-3 cycles for periodic signals

TRIGGER:
- Source: Which channel to trigger from
- Level: Voltage threshold for triggering
- Slope: RISE (rising edge) or FALL (falling edge)
- Set trigger level to middle of signal for stable display

ACQUISITION MODES:
- Run: Continuous acquisition and display
- Single: Capture one triggered event then stop
- Stop: Freeze current display

AUTOMATED MEASUREMENTS:
- Get Measurements: Query all standard measurements
- Frequency/Period: Only valid for periodic signals
- Invalid measurements show as "No signal"
- RMS is true RMS, not peak/√2

WAVEFORM CAPTURE:
- Saves time and voltage arrays to CSV
- Useful for offline analysis, FFT, etc.
- Captures current screen contents

TIPS:
- Use AutoSet for quick setup on unknown signals
- Stop acquisition before changing major settings
- For low-frequency signals, increase memory depth
- Ground unused channels to reduce noise"""
        
        messagebox.showinfo("Oscilloscope Tips", tips)
    
    def show_logging_tips(self):
        """Show data logging tips"""
        tips = """Data Logging - Usage Tips

CONFIGURATION:
- Log Directory: Where CSV files will be saved
  - Creates directory automatically if it doesn't exist
  - Default is ./logs in current working directory
- Sample Interval: Time between measurements (seconds)
  - 1.0s is good for slow processes
  - 0.1s for faster dynamics
  - Consider instrument settling time
- Log Instruments: Select which instruments to record

FILE FORMAT:
- Separate CSV file for each instrument
- Filename includes timestamp: lcr_20260206_143052.csv
- LCR columns: Timestamp, Mode, Frequency, Primary, Secondary, Status
- Scope columns: Timestamp, Frequency, Period, Mean, Pk-Pk, RMS, Amplitude

USAGE:
- Start Logging: Begin recording to CSV files
- Stop Logging: Close files and stop recording
- Files are flushed after each sample (safe if interrupted)
- Log status shows errors and warnings

TIPS:
- Configure instruments BEFORE starting logging
- For long tests, use longer sample intervals to reduce file size
- CSV files can be opened in Excel, Python, MATLAB, etc.
- Timestamps are in format: YYYY-MM-DD HH:MM:SS.mmm
- Check disk space for long-duration logging
- Stop logging before disconnecting instruments

ANALYSIS:
- Use pandas in Python: df = pd.read_csv('lcr_xxx.csv')
- Plot in Excel: Insert > Chart > Scatter
- For frequency sweeps: log at each frequency step"""
        
        messagebox.showinfo("Data Logging Tips", tips)
    
    def create_lcr_tab(self):
        """Create LCR meter control tab"""
        lcr_frame = ttk.Frame(self.notebook)
        self.notebook.add(lcr_frame, text="LCR Meter (BK 894)")
        
        # Connection status
        status_frame = ttk.LabelFrame(lcr_frame, text="Connection", padding=10)
        status_frame.pack(fill='x', padx=10, pady=10)
        
        self.lcr_status = tk.Label(status_frame, text="Not connected", fg="red")
        self.lcr_status.pack(side=tk.LEFT)
        
        ttk.Button(status_frame, text="Reconnect", command=self.reconnect_lcr).pack(side=tk.RIGHT)
        
        # Configuration
        config_frame = ttk.LabelFrame(lcr_frame, text="Configuration", padding=10)
        config_frame.pack(fill='x', padx=10, pady=10)
        
        # Measurement mode
        ttk.Label(config_frame, text="Mode:").grid(row=0, column=0, sticky='w', pady=5)
        self.lcr_mode = ttk.Combobox(config_frame, width=20, state='readonly')
        self.lcr_mode.grid(row=0, column=1, padx=10, pady=5)
        self.lcr_mode['values'] = list(BK894.MODES.keys())
        self.lcr_mode.set('CPD')
        
        # Frequency
        ttk.Label(config_frame, text="Frequency (Hz):").grid(row=1, column=0, sticky='w', pady=5)
        self.lcr_freq = ttk.Entry(config_frame, width=20)
        self.lcr_freq.grid(row=1, column=1, padx=10, pady=5)
        self.lcr_freq.insert(0, "1000")
        
        # Voltage
        ttk.Label(config_frame, text="Voltage (V):").grid(row=2, column=0, sticky='w', pady=5)
        self.lcr_volt = ttk.Entry(config_frame, width=20)
        self.lcr_volt.grid(row=2, column=1, padx=10, pady=5)
        self.lcr_volt.insert(0, "1.0")
        
        # Apply button
        ttk.Button(config_frame, text="Apply Configuration", 
                   command=self.apply_lcr_config).grid(row=3, column=0, columnspan=2, pady=10)
        
        # Measurement display
        meas_frame = ttk.LabelFrame(lcr_frame, text="Current Measurement", padding=10)
        meas_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        self.lcr_primary_label = tk.Label(meas_frame, text="Primary: --", font=("Arial", 16))
        self.lcr_primary_label.pack(pady=10)
        
        self.lcr_secondary_label = tk.Label(meas_frame, text="Secondary: --", font=("Arial", 16))
        self.lcr_secondary_label.pack(pady=10)
        
        self.lcr_status_label = tk.Label(meas_frame, text="Status: --")
        self.lcr_status_label.pack(pady=5)
        
        # Control buttons
        button_frame = ttk.Frame(meas_frame)
        button_frame.pack(pady=10)
        
        ttk.Button(button_frame, text="Single Measurement", 
                   command=self.lcr_single_measurement).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Start Continuous", 
                   command=self.lcr_start_continuous).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Stop", 
                   command=self.lcr_stop_continuous).pack(side=tk.LEFT, padx=5)
        
        # Tips button at bottom
        tips_frame = ttk.Frame(lcr_frame)
        tips_frame.pack(side=tk.BOTTOM, fill='x', padx=10, pady=5)
        ttk.Button(tips_frame, text="📖 Show Usage Tips", 
                   command=self.show_lcr_tips).pack(side=tk.RIGHT)
        
        self.lcr_continuous = False
    
    def create_scope_tab(self):
        """Create oscilloscope control tab"""
        scope_frame = ttk.Frame(self.notebook)
        self.notebook.add(scope_frame, text="Oscilloscope (MSO24)")
        
        # Connection status
        status_frame = ttk.LabelFrame(scope_frame, text="Connection", padding=10)
        status_frame.pack(fill='x', padx=10, pady=10)
        
        self.scope_status = tk.Label(status_frame, text="Not connected", fg="red")
        self.scope_status.pack(side=tk.LEFT)
        
        ttk.Button(status_frame, text="Reconnect", command=self.reconnect_scope).pack(side=tk.RIGHT)
        
        # Channel configuration - using notebook for each channel
        config_notebook = ttk.Notebook(scope_frame)
        config_notebook.pack(fill='x', padx=10, pady=10)
        
        # Store channel widgets
        self.channel_widgets = {}
        
        for ch in range(1, 5):
            ch_frame = ttk.Frame(config_notebook)
            config_notebook.add(ch_frame, text=f"Channel {ch}")
            
            # Enable checkbox
            enable_var = tk.BooleanVar(value=(ch == 1))
            ttk.Checkbutton(ch_frame, text="Enable Channel", 
                           variable=enable_var,
                           command=lambda c=ch, v=enable_var: self.toggle_channel(c, v)).grid(
                               row=0, column=0, columnspan=4, sticky='w', padx=10, pady=10)
            
            # Vertical scale
            ttk.Label(ch_frame, text="Vertical (V/div):").grid(row=1, column=0, sticky='w', padx=10, pady=5)
            vscale = ttk.Entry(ch_frame, width=12)
            vscale.grid(row=1, column=1, padx=5, pady=5)
            vscale.insert(0, "1.0")
            
            # Coupling
            ttk.Label(ch_frame, text="Coupling:").grid(row=1, column=2, sticky='w', padx=(20,0), pady=5)
            coupling = ttk.Combobox(ch_frame, width=10, state='readonly')
            coupling.grid(row=1, column=3, padx=5, pady=5)
            coupling['values'] = ['DC', 'AC', 'GND']
            coupling.set('DC')
            
            # Position
            ttk.Label(ch_frame, text="Position (div):").grid(row=2, column=0, sticky='w', padx=10, pady=5)
            position = ttk.Entry(ch_frame, width=12)
            position.grid(row=2, column=1, padx=5, pady=5)
            position.insert(0, "0")
            
            # Trigger source option (only show on CH1-4)
            ttk.Label(ch_frame, text="Use as trigger:").grid(row=2, column=2, sticky='w', padx=(20,0), pady=5)
            trigger_var = tk.BooleanVar(value=(ch == 1))
            ttk.Checkbutton(ch_frame, variable=trigger_var).grid(row=2, column=3, padx=5, pady=5)
            
            # Apply button for this channel
            ttk.Button(ch_frame, text=f"Apply CH{ch} Config", 
                      command=lambda c=ch: self.apply_channel_config(c)).grid(
                          row=3, column=0, columnspan=4, pady=10)
            
            # Store widgets
            self.channel_widgets[ch] = {
                'enable': enable_var,
                'vscale': vscale,
                'coupling': coupling,
                'position': position,
                'trigger': trigger_var
            }
        
        # Horizontal and trigger configuration
        control_frame = ttk.LabelFrame(scope_frame, text="Timebase & Trigger", padding=10)
        control_frame.pack(fill='x', padx=10, pady=10)
        
        # Horizontal scale
        ttk.Label(control_frame, text="Horizontal (s/div):").grid(row=0, column=0, sticky='w', pady=5)
        self.scope_hscale = ttk.Entry(control_frame, width=15)
        self.scope_hscale.grid(row=0, column=1, padx=10, pady=5)
        self.scope_hscale.insert(0, "0.001")
        
        # Trigger level
        ttk.Label(control_frame, text="Trigger Level (V):").grid(row=0, column=2, sticky='w', pady=5, padx=(20,0))
        self.scope_trig_level = ttk.Entry(control_frame, width=10)
        self.scope_trig_level.grid(row=0, column=3, padx=10, pady=5)
        self.scope_trig_level.insert(0, "0")
        
        # Trigger slope
        ttk.Label(control_frame, text="Trigger Slope:").grid(row=1, column=0, sticky='w', pady=5)
        self.scope_trig_slope = ttk.Combobox(control_frame, width=12, state='readonly')
        self.scope_trig_slope.grid(row=1, column=1, padx=10, pady=5)
        self.scope_trig_slope['values'] = ['RISE', 'FALL']
        self.scope_trig_slope.set('RISE')
        
        # Apply all button
        ttk.Button(control_frame, text="Apply All Settings", 
                  command=self.apply_all_scope_config).grid(row=2, column=0, columnspan=4, pady=10)
        
        # Measurements display - tabbed by channel
        meas_notebook = ttk.Notebook(scope_frame)
        meas_notebook.pack(fill='both', expand=True, padx=10, pady=10)
        
        self.scope_meas_labels = {}
        
        for ch in range(1, 5):
            meas_frame = ttk.Frame(meas_notebook)
            meas_notebook.add(meas_frame, text=f"CH{ch} Measurements")
            
            # Create measurement labels for this channel
            ch_labels = {}
            row = 0
            for meas in ['Frequency', 'Period', 'Mean', 'Pk-Pk', 'RMS', 'Amplitude']:
                label = tk.Label(meas_frame, text=f"{meas}: --", font=("Arial", 12), anchor='w')
                label.grid(row=row//2, column=row%2, sticky='w', padx=20, pady=5)
                ch_labels[meas.lower().replace('-', '')] = label
                row += 1
            
            # Buttons for this channel
            button_frame = ttk.Frame(meas_frame)
            button_frame.grid(row=3, column=0, columnspan=2, pady=10)
            
            ttk.Button(button_frame, text=f"Get CH{ch} Measurements", 
                      command=lambda c=ch: self.scope_get_measurements(c)).pack(side=tk.LEFT, padx=5)
            ttk.Button(button_frame, text=f"Capture CH{ch} Waveform", 
                      command=lambda c=ch: self.scope_capture_waveform(c)).pack(side=tk.LEFT, padx=5)
            
            self.scope_meas_labels[ch] = ch_labels
        
        # Acquisition control buttons
        acq_frame = ttk.LabelFrame(scope_frame, text="Acquisition Control", padding=10)
        acq_frame.pack(fill='x', padx=10, pady=10)
        
        button_frame = ttk.Frame(acq_frame)
        button_frame.pack()
        
        ttk.Button(button_frame, text="Run", 
                  command=self.scope_run).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Stop", 
                  command=self.scope_stop).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Single", 
                  command=self.scope_single).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="AutoSet", 
                  command=self.scope_autoset).pack(side=tk.LEFT, padx=5)
        
        # Tips button at bottom
        tips_frame = ttk.Frame(scope_frame)
        tips_frame.pack(side=tk.BOTTOM, fill='x', padx=10, pady=5)
        ttk.Button(tips_frame, text="📖 Show Usage Tips", 
                   command=self.show_scope_tips).pack(side=tk.RIGHT)
    
    def create_logging_tab(self):
        """Create data logging tab"""
        log_frame = ttk.Frame(self.notebook)
        self.notebook.add(log_frame, text="Data Logging")
        
        # Logging configuration
        config_frame = ttk.LabelFrame(log_frame, text="Logging Configuration", padding=10)
        config_frame.pack(fill='x', padx=10, pady=10)
        
        # Log file selection
        ttk.Label(config_frame, text="Log Directory:").grid(row=0, column=0, sticky='w', pady=5)
        self.log_dir = tk.StringVar(value="./logs")
        ttk.Entry(config_frame, textvariable=self.log_dir, width=40).grid(row=0, column=1, padx=10, pady=5)
        ttk.Button(config_frame, text="Browse", 
                   command=self.select_log_dir).grid(row=0, column=2, padx=5)
        
        # Sample rate
        ttk.Label(config_frame, text="Sample Interval (s):").grid(row=1, column=0, sticky='w', pady=5)
        self.log_interval = ttk.Entry(config_frame, width=15)
        self.log_interval.grid(row=1, column=1, sticky='w', padx=10, pady=5)
        self.log_interval.insert(0, "1.0")
        
        # Instrument selection
        ttk.Label(config_frame, text="Log Instruments:").grid(row=2, column=0, sticky='nw', pady=5)
        instr_frame = ttk.Frame(config_frame)
        instr_frame.grid(row=2, column=1, sticky='w', padx=10)
        
        self.log_lcr = tk.BooleanVar(value=True)
        ttk.Checkbutton(instr_frame, text="LCR Meter", variable=self.log_lcr).pack(anchor='w')
        
        # Scope channel selection
        scope_frame = ttk.LabelFrame(instr_frame, text="Oscilloscope Channels", padding=5)
        scope_frame.pack(anchor='w', pady=5)
        
        self.log_scope_channels = {}
        for ch in range(1, 5):
            var = tk.BooleanVar(value=(ch == 1))
            ttk.Checkbutton(scope_frame, text=f"CH{ch}", variable=var).pack(anchor='w')
            self.log_scope_channels[ch] = var
        
        # Control buttons
        button_frame = ttk.Frame(config_frame)
        button_frame.grid(row=3, column=0, columnspan=3, pady=20)
        
        self.log_start_btn = ttk.Button(button_frame, text="Start Logging", 
                                         command=self.start_logging)
        self.log_start_btn.pack(side=tk.LEFT, padx=5)
        
        self.log_stop_btn = ttk.Button(button_frame, text="Stop Logging", 
                                        command=self.stop_logging, state='disabled')
        self.log_stop_btn.pack(side=tk.LEFT, padx=5)
        
        # Log display
        display_frame = ttk.LabelFrame(log_frame, text="Log Status", padding=10)
        display_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        self.log_text = tk.Text(display_frame, height=12, state='disabled')
        self.log_text.pack(fill='both', expand=True)
        
        scrollbar = ttk.Scrollbar(display_frame, command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar.set)
        
        # Tips button at bottom
        tips_frame = ttk.Frame(log_frame)
        tips_frame.pack(side=tk.BOTTOM, fill='x', padx=10, pady=5)
        ttk.Button(tips_frame, text="📖 Show Usage Tips", 
                   command=self.show_logging_tips).pack(side=tk.RIGHT)
    
    # LCR methods
    def reconnect_lcr(self):
        try:
            if self.lcr:
                self.lcr.close()
            self.lcr = BK894("/dev/usbtmc1")
            self.lcr_status.config(text=f"Connected: {self.lcr.idn}", fg="green")
            self.update_lcr_config()
        except Exception as e:
            self.lcr_status.config(text=f"Error: {e}", fg="red")
            messagebox.showerror("Connection Error", str(e))
    
    def update_lcr_config(self):
        """Read current config from instrument"""
        if not self.lcr:
            return
        try:
            config = self.lcr.get_config()
            self.lcr_mode.set(config['mode'].upper())
            self.lcr_freq.delete(0, tk.END)
            self.lcr_freq.insert(0, str(int(config['frequency'])))
        except Exception as e:
            self.status_bar.config(text=f"Error reading config: {e}")
    
    def apply_lcr_config(self):
        if not self.lcr:
            messagebox.showerror("Error", "LCR meter not connected")
            return
        
        try:
            mode = self.lcr_mode.get()
            freq = float(self.lcr_freq.get())
            volt = float(self.lcr_volt.get())
            
            self.lcr.set_mode(mode)
            self.lcr.set_frequency(freq)
            self.lcr.set_voltage(volt)
            
            time.sleep(0.3)
            self.status_bar.config(text=f"LCR configured: {mode}, {freq} Hz, {volt} V")
        except Exception as e:
            messagebox.showerror("Configuration Error", str(e))
    
    def lcr_single_measurement(self):
        if not self.lcr:
            messagebox.showerror("Error", "LCR meter not connected")
            return
        
        try:
            primary, secondary, status = self.lcr.measure()
            mode = self.lcr_mode.get()
            
            # Format based on mode
            if 'C' in mode:
                p_str = f"{primary*1e9:.3f} nF"
            elif 'L' in mode:
                p_str = f"{primary*1e6:.3f} µH"
            elif 'Z' in mode:
                p_str = f"{primary:.3f} Ω"
            else:
                p_str = f"{primary:.6g}"
            
            self.lcr_primary_label.config(text=f"Primary: {p_str}")
            self.lcr_secondary_label.config(text=f"Secondary: {secondary:.6g}")
            self.lcr_status_label.config(text=f"Status: {'OK' if status == 0 else 'Error'}")
        except Exception as e:
            messagebox.showerror("Measurement Error", str(e))
    
    def lcr_start_continuous(self):
        self.lcr_continuous = True
        self.lcr_continuous_measurement()
    
    def lcr_stop_continuous(self):
        self.lcr_continuous = False
    
    def lcr_continuous_measurement(self):
        if self.lcr_continuous and self.lcr:
            self.lcr_single_measurement()
            self.root.after(200, self.lcr_continuous_measurement)
    
    # Scope methods
    def reconnect_scope(self):
        try:
            if self.scope:
                self.scope.close()
            self.scope = TekMSO24("/dev/usbtmc2")
            self.scope_status.config(text=f"Connected: {self.scope.idn}", fg="green")
        except Exception as e:
            self.scope_status.config(text=f"Error: {e}", fg="red")
            messagebox.showerror("Connection Error", str(e))
    
    def toggle_channel(self, channel, enable_var):
        """Enable/disable a channel"""
        if not self.scope:
            return
        try:
            self.scope.set_channel_enable(channel, enable_var.get())
            state = "enabled" if enable_var.get() else "disabled"
            self.status_bar.config(text=f"CH{channel} {state}")
        except Exception as e:
            messagebox.showerror("Error", str(e))
    
    def apply_channel_config(self, channel):
        """Apply configuration for a specific channel"""
        if not self.scope:
            messagebox.showerror("Error", "Oscilloscope not connected")
            return
        
        try:
            widgets = self.channel_widgets[channel]
            vscale = float(widgets['vscale'].get())
            coupling = widgets['coupling'].get()
            position = float(widgets['position'].get())
            
            self.scope.set_channel_enable(channel, widgets['enable'].get())
            self.scope.set_vertical(channel, scale=vscale, position=position, coupling=coupling)
            
            self.status_bar.config(text=f"CH{channel} configured: {vscale}V/div, {coupling} coupling")
        except Exception as e:
            messagebox.showerror("Configuration Error", str(e))
    
    def apply_all_scope_config(self):
        """Apply all scope settings at once"""
        if not self.scope:
            messagebox.showerror("Error", "Oscilloscope not connected")
            return
        
        try:
            # Apply all channel configs
            for ch in range(1, 5):
                widgets = self.channel_widgets[ch]
                vscale = float(widgets['vscale'].get())
                coupling = widgets['coupling'].get()
                position = float(widgets['position'].get())
                
                self.scope.set_channel_enable(ch, widgets['enable'].get())
                self.scope.set_vertical(ch, scale=vscale, position=position, coupling=coupling)
            
            # Apply horizontal
            hscale = float(self.scope_hscale.get())
            self.scope.set_horizontal(scale=hscale)
            
            # Apply trigger (find which channel is selected as trigger)
            trig_source = 'CH1'
            for ch in range(1, 5):
                if self.channel_widgets[ch]['trigger'].get():
                    trig_source = f'CH{ch}'
                    break
            
            trig_level = float(self.scope_trig_level.get())
            trig_slope = self.scope_trig_slope.get()
            self.scope.set_trigger_edge(source=trig_source, level=trig_level, slope=trig_slope)
            
            self.status_bar.config(text=f"All settings applied. Trigger: {trig_source} @ {trig_level}V")
        except Exception as e:
            messagebox.showerror("Configuration Error", str(e))
    
    def scope_single(self):
        if not self.scope:
            messagebox.showerror("Error", "Oscilloscope not connected")
            return
        try:
            self.scope.single()
            self.status_bar.config(text="Single acquisition armed")
        except Exception as e:
            messagebox.showerror("Error", str(e))
    
    def scope_run(self):
        if not self.scope:
            messagebox.showerror("Error", "Oscilloscope not connected")
            return
        try:
            self.scope.run()
            self.status_bar.config(text="Scope running")
        except Exception as e:
            messagebox.showerror("Error", str(e))
    
    def scope_stop(self):
        if not self.scope:
            messagebox.showerror("Error", "Oscilloscope not connected")
            return
        try:
            self.scope.stop()
            self.status_bar.config(text="Scope stopped")
        except Exception as e:
            messagebox.showerror("Error", str(e))
    
    def scope_autoset(self):
        if not self.scope:
            messagebox.showerror("Error", "Oscilloscope not connected")
            return
        try:
            self.status_bar.config(text="Running AutoSet...")
            self.root.update()
            self.scope.autoset()
            self.status_bar.config(text="AutoSet complete")
        except Exception as e:
            messagebox.showerror("Error", str(e))
    
    def scope_get_measurements(self, channel):
        """Get measurements for a specific channel"""
        if not self.scope:
            messagebox.showerror("Error", "Oscilloscope not connected")
            return
        
        try:
            meas = self.scope.get_all_measurements(channel)
            
            for key, value in meas.items():
                label_key = key.replace('_', '')
                if label_key in self.scope_meas_labels[channel]:
                    if value is not None:
                        # Format appropriately
                        if key == 'freq':
                            if value < 1e3:
                                text = f"{value:.3f} Hz"
                            elif value < 1e6:
                                text = f"{value/1e3:.3f} kHz"
                            else:
                                text = f"{value/1e6:.3f} MHz"
                        elif key == 'period':
                            if value < 1e-6:
                                text = f"{value*1e9:.3f} ns"
                            elif value < 1e-3:
                                text = f"{value*1e6:.3f} µs"
                            else:
                                text = f"{value*1e3:.3f} ms"
                        else:
                            text = f"{value:.4f} V"
                        
                        self.scope_meas_labels[channel][label_key].config(
                            text=f"{key.capitalize()}: {text}"
                        )
                    else:
                        self.scope_meas_labels[channel][label_key].config(
                            text=f"{key.capitalize()}: No signal"
                        )
            
            self.status_bar.config(text=f"CH{channel} measurements updated")
        except Exception as e:
            messagebox.showerror("Measurement Error", str(e))
    
    def scope_capture_waveform(self, channel):
        """Capture waveform for a specific channel"""
        if not self.scope:
            messagebox.showerror("Error", "Oscilloscope not connected")
            return
        
        try:
            filename = filedialog.asksaveasfilename(
                defaultextension=".csv",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
                initialfile=f"waveform_ch{channel}.csv"
            )
            
            if not filename:
                return
            
            self.status_bar.config(text=f"Capturing CH{channel} waveform...")
            self.root.update()
            
            waveform = self.scope.get_waveform(channel)
            
            with open(filename, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['Time (s)', 'Voltage (V)'])
                for t, v in zip(waveform['t'], waveform['v']):
                    writer.writerow([t, v])
            
            self.status_bar.config(text=f"CH{channel} waveform saved: {filename}")
            messagebox.showinfo("Success", f"Waveform saved to {filename}")
        except Exception as e:
            messagebox.showerror("Capture Error", str(e))
    
    # Logging methods
    def select_log_dir(self):
        directory = filedialog.askdirectory()
        if directory:
            self.log_dir.set(directory)
    
    def start_logging(self):
        import os
        
        # Create log directory if needed
        log_path = self.log_dir.get()
        os.makedirs(log_path, exist_ok=True)
        
        self.recording = True
        self.log_start_btn.config(state='disabled')
        self.log_stop_btn.config(state='normal')
        
        # Start logging thread
        self.record_thread = threading.Thread(target=self.logging_loop, daemon=True)
        self.record_thread.start()
        
        self.log_message("Logging started")
    
    def stop_logging(self):
        self.recording = False
        self.log_start_btn.config(state='normal')
        self.log_stop_btn.config(state='disabled')
        self.log_message("Logging stopped")
    
    def logging_loop(self):
        """Background logging thread"""
        import os
        
        log_path = self.log_dir.get()
        interval = float(self.log_interval.get())
        
        # Create CSV files with timestamps
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        lcr_file = None
        lcr_writer = None
        
        scope_files = {}
        scope_writers = {}
        
        if self.log_lcr.get() and self.lcr:
            lcr_filename = os.path.join(log_path, f"lcr_{timestamp}.csv")
            lcr_file = open(lcr_filename, 'w', newline='')
            lcr_writer = csv.writer(lcr_file)
            lcr_writer.writerow(['Timestamp', 'Mode', 'Frequency (Hz)', 'Primary', 'Secondary', 'Status'])
            self.log_message(f"LCR log: {lcr_filename}")
        
        # Create separate file for each scope channel
        for ch in range(1, 5):
            if self.log_scope_channels[ch].get() and self.scope:
                scope_filename = os.path.join(log_path, f"scope_ch{ch}_{timestamp}.csv")
                scope_files[ch] = open(scope_filename, 'w', newline='')
                scope_writers[ch] = csv.writer(scope_files[ch])
                scope_writers[ch].writerow(['Timestamp', 'Frequency (Hz)', 'Period (s)', 'Mean (V)', 
                                           'Pk-Pk (V)', 'RMS (V)', 'Amplitude (V)'])
                self.log_message(f"Scope CH{ch} log: {scope_filename}")
        
        while self.recording:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            
            # Log LCR data
            if lcr_writer and self.lcr:
                try:
                    config = self.lcr.get_config()
                    primary, secondary, status = self.lcr.measure()
                    lcr_writer.writerow([timestamp, config['mode'], config['frequency'], 
                                        primary, secondary, status])
                    lcr_file.flush()
                except Exception as e:
                    self.log_message(f"LCR error: {e}")
            
            # Log scope data for each enabled channel
            for ch, writer in scope_writers.items():
                if self.scope:
                    try:
                        meas = self.scope.get_all_measurements(ch)
                        writer.writerow([timestamp, meas.get('freq'), meas.get('period'),
                                        meas.get('mean'), meas.get('pk2pk'), 
                                        meas.get('rms'), meas.get('amplitude')])
                        scope_files[ch].flush()
                    except Exception as e:
                        self.log_message(f"Scope CH{ch} error: {e}")
            
            time.sleep(interval)
        
        # Close files
        if lcr_file:
            lcr_file.close()
        for f in scope_files.values():
            f.close()
    
    def log_message(self, message):
        """Thread-safe logging to text widget"""
        def update():
            self.log_text.config(state='normal')
            self.log_text.insert(tk.END, f"[{datetime.now().strftime('%H:%M:%S')}] {message}\n")
            self.log_text.see(tk.END)
            self.log_text.config(state='disabled')
        
        self.root.after(0, update)


if __name__ == "__main__":
    root = tk.Tk()
    app = InstrumentControlGUI(root)
    root.mainloop()