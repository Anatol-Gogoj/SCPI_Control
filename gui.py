#!/usr/bin/env python3
"""
Lab Instrument Control GUI
Multi-instrument control with CSV data logging
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import argparse
import csv
import math
import os
import queue
import time
from datetime import datetime
from instruments import BK894, BK894Mock, TekMSO24
import threading

class InstrumentControlGUI:
    def __init__(self, root, use_mock=False):
        self.root = root
        self.root.title("Lab Instrument Control" + (" — MOCK" if use_mock else ""))
        self.root.geometry("1000x900")
        self.use_mock = use_mock

        # Instrument connections
        self.lcr = None
        self.scope = None
        self.recording = False
        self.record_thread = None

        # Sweep state
        self.sweeping = False
        self.sweep_thread = None
        self.sweep_queue = queue.Queue()
        
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
            self.lcr = BK894Mock() if self.use_mock else BK894("/dev/usbtmc0")
            self.lcr_status.config(text=f"Connected: {self.lcr.idn}", fg="green")
            self.update_lcr_config()
        except Exception as e:
            self.lcr_status.config(text=f"Not connected: {e}", fg="red")

        if self.use_mock:
            # No scope mock yet — leave disconnected.
            self.scope_status.config(text="Not connected (mock mode)", fg="orange")
            return

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
- USB connection appears as /dev/usbtmc0

CONFIGURATION:
- Mode: Select measurement type
  - CPD: Capacitance + Dissipation Factor (for capacitors)
  - LSRS: Inductance (series) + Resistance (for inductors)
  - RX: Resistance + Reactance (for general impedance)
- Frequency: Test frequency (100 Hz to 500 kHz)
  - Use 1 kHz for general capacitor testing
  - Use 100 Hz for electrolytics
  - Use 10 kHz+ for high-frequency components
- Voltage: Test signal level (-5.0 to 5.0 V)
  - 1.0V is standard for most AC measurements
  - Note: BK894 may reject negative AC levels at the front panel

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
        tab_frame = ttk.Frame(self.notebook)
        self.notebook.add(tab_frame, text="LCR Meter (BK 894)")

        # Vertical-scrollable container so the tab content can exceed the
        # window height. All existing widgets still go into `lcr_frame`
        # (now the inner Frame inside the canvas).
        canvas = tk.Canvas(tab_frame, highlightthickness=0)
        vbar = ttk.Scrollbar(tab_frame, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=vbar.set)
        canvas.pack(side='left', fill='both', expand=True)
        vbar.pack(side='right', fill='y')

        lcr_frame = ttk.Frame(canvas)
        window_id = canvas.create_window((0, 0), window=lcr_frame, anchor='nw')

        def _sync_scrollregion(_event=None):
            canvas.configure(scrollregion=canvas.bbox('all'))
        lcr_frame.bind('<Configure>', _sync_scrollregion)

        def _resize_inner(event):
            canvas.itemconfigure(window_id, width=event.width)
        canvas.bind('<Configure>', _resize_inner)

        # Mouse-wheel scrolling — bind globally but only act when the
        # cursor is actually over this canvas's screen area, so the wheel
        # still works over child widgets (entries, buttons) without
        # affecting other tabs.
        def _on_mousewheel(event):
            cx, cy = canvas.winfo_rootx(), canvas.winfo_rooty()
            cw, ch = canvas.winfo_width(), canvas.winfo_height()
            if not (cx <= event.x_root < cx + cw and cy <= event.y_root < cy + ch):
                return
            if event.num == 4:
                canvas.yview_scroll(-3, 'units')
            elif event.num == 5:
                canvas.yview_scroll(3, 'units')
            elif event.delta:
                canvas.yview_scroll(int(-event.delta / 40), 'units')
        self.root.bind_all('<MouseWheel>', _on_mousewheel, add='+')
        self.root.bind_all('<Button-4>', _on_mousewheel, add='+')
        self.root.bind_all('<Button-5>', _on_mousewheel, add='+')

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

        # Sweep section
        self._create_lcr_sweep_section(lcr_frame)

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
            self.lcr = BK894Mock() if self.use_mock else BK894("/dev/usbtmc0")
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
            if 'voltage' in config:
                self.lcr_volt.delete(0, tk.END)
                self.lcr_volt.insert(0, f"{config['voltage']:g}")
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

    # ---- Sweep ----------------------------------------------------------

    def _create_lcr_sweep_section(self, parent):
        """Build the Sweep section on the LCR tab.

        Layout: Frequency axis box and Voltage axis box side by side,
        then sweep-wide options (order, dwell, samples, inter-sample
        delay), output path, Start/Stop, progress bar, status line.
        """
        sweep_frame = ttk.LabelFrame(parent, text="Sweep (freq × voltage × dwell)", padding=10)
        sweep_frame.pack(fill='x', padx=10, pady=10)
        sweep_frame.columnconfigure(0, weight=1)
        sweep_frame.columnconfigure(1, weight=1)

        # Frequency axis
        self._build_sweep_axis(
            sweep_frame, axis='freq', title="Frequency axis",
            unit='Hz', default_start='100', default_stop='100000',
            default_points='11', default_scale='log',
            default_list='100, 1000, 10000, 100000', column=0,
        )

        # Voltage axis
        self._build_sweep_axis(
            sweep_frame, axis='volt', title="Voltage axis",
            unit='V', default_start='0.1', default_stop='1.0',
            default_points='5', default_scale='linear',
            default_list='0.1, 0.5, 1.0', column=1,
        )

        # Sweep-wide options
        opts = ttk.Frame(sweep_frame)
        opts.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(10, 4))

        ttk.Label(opts, text="Order:").grid(row=0, column=0, sticky='w')
        self.sw_order = ttk.Combobox(opts, width=24, state='readonly',
                                     values=['Freq outer, V inner',
                                             'V outer, Freq inner'])
        self.sw_order.set('Freq outer, V inner')
        self.sw_order.grid(row=0, column=1, padx=(4, 16))

        ttk.Label(opts, text="Dwell (s):").grid(row=0, column=2, sticky='w')
        self.sw_dwell = ttk.Entry(opts, width=8)
        self.sw_dwell.insert(0, '0.5')
        self.sw_dwell.grid(row=0, column=3, padx=(4, 16))

        ttk.Label(opts, text="Samples/point:").grid(row=0, column=4, sticky='w')
        self.sw_samples = ttk.Entry(opts, width=6)
        self.sw_samples.insert(0, '5')
        self.sw_samples.grid(row=0, column=5, padx=(4, 16))

        ttk.Label(opts, text="Inter-sample (s):").grid(row=0, column=6, sticky='w')
        self.sw_isd = ttk.Entry(opts, width=8)
        self.sw_isd.insert(0, '0.05')
        self.sw_isd.grid(row=0, column=7, padx=(4, 0))

        # Output path
        out = ttk.Frame(sweep_frame)
        out.grid(row=2, column=0, columnspan=2, sticky='ew', pady=4)
        out.columnconfigure(1, weight=1)
        ttk.Label(out, text="Output CSV:").grid(row=0, column=0, sticky='w')
        self.sw_output = tk.StringVar()
        ttk.Entry(out, textvariable=self.sw_output).grid(row=0, column=1, sticky='ew', padx=4)
        ttk.Button(out, text="Browse...", command=self._browse_sweep_output).grid(row=0, column=2)
        ttk.Label(out, text="(blank → <log dir>/sweep_<timestamp>.csv)",
                  foreground='gray').grid(row=1, column=1, sticky='w', padx=4)

        # Controls + progress
        ctrl = ttk.Frame(sweep_frame)
        ctrl.grid(row=3, column=0, columnspan=2, sticky='ew', pady=(8, 0))
        ctrl.columnconfigure(2, weight=1)
        self.sw_start_btn = ttk.Button(ctrl, text="Start Sweep", command=self.lcr_start_sweep)
        self.sw_start_btn.grid(row=0, column=0, padx=(0, 6))
        self.sw_stop_btn = ttk.Button(ctrl, text="Stop", command=self.lcr_stop_sweep, state='disabled')
        self.sw_stop_btn.grid(row=0, column=1, padx=(0, 12))
        self.sw_progress = ttk.Progressbar(ctrl, mode='determinate')
        self.sw_progress.grid(row=0, column=2, sticky='ew')

        self.sw_status = ttk.Label(sweep_frame, text="Idle.", foreground='gray')
        self.sw_status.grid(row=4, column=0, columnspan=2, sticky='w', pady=(4, 0))

        self._update_sweep_mode_state('freq')
        self._update_sweep_mode_state('volt')

    def _build_sweep_axis(self, parent, axis, title, unit, default_start, default_stop,
                          default_points, default_scale, default_list, column):
        """Build one axis (freq or volt) sub-LabelFrame inside the sweep frame."""
        box = ttk.LabelFrame(parent, text=title, padding=8)
        box.grid(row=0, column=column, sticky='nsew', padx=4)
        box.columnconfigure(1, weight=1)
        box.columnconfigure(3, weight=1)

        mode_var = tk.StringVar(value='range')
        scale_var = tk.StringVar(value=default_scale)
        list_var = tk.StringVar(value=default_list)
        # Trace so the enabled/disabled state follows mode_var no matter how
        # it changes (radio click, programmatic set, future load-config, etc.).
        mode_var.trace_add('write', lambda *_a, ax=axis: self._update_sweep_mode_state(ax))

        ttk.Radiobutton(box, text="Range", variable=mode_var, value='range'
                        ).grid(row=0, column=0, sticky='w')
        ttk.Radiobutton(box, text="List", variable=mode_var, value='list'
                        ).grid(row=0, column=1, sticky='w')

        ttk.Label(box, text=f"Start ({unit}):").grid(row=1, column=0, sticky='w', pady=2)
        start_entry = ttk.Entry(box, width=12)
        start_entry.insert(0, default_start)
        start_entry.grid(row=1, column=1, sticky='ew', padx=(4, 12), pady=2)

        ttk.Label(box, text=f"Stop ({unit}):").grid(row=1, column=2, sticky='w', pady=2)
        stop_entry = ttk.Entry(box, width=12)
        stop_entry.insert(0, default_stop)
        stop_entry.grid(row=1, column=3, sticky='ew', padx=(4, 0), pady=2)

        ttk.Label(box, text="Points:").grid(row=2, column=0, sticky='w', pady=2)
        points_entry = ttk.Entry(box, width=12)
        points_entry.insert(0, default_points)
        points_entry.grid(row=2, column=1, sticky='ew', padx=(4, 12), pady=2)

        ttk.Label(box, text="Scale:").grid(row=2, column=2, sticky='w', pady=2)
        scale_combo = ttk.Combobox(box, width=10, state='readonly',
                                   values=['linear', 'log'], textvariable=scale_var)
        scale_combo.grid(row=2, column=3, sticky='ew', padx=(4, 0), pady=2)

        ttk.Label(box, text=f"List ({unit}):").grid(row=3, column=0, sticky='w', pady=2)
        list_entry = ttk.Entry(box, textvariable=list_var)
        list_entry.grid(row=3, column=1, columnspan=3, sticky='ew', padx=(4, 0), pady=2)

        # Store handles under axis prefix
        setattr(self, f'sw_{axis}_mode', mode_var)
        setattr(self, f'sw_{axis}_scale', scale_var)
        setattr(self, f'sw_{axis}_list_var', list_var)
        setattr(self, f'sw_{axis}_range_widgets', [start_entry, stop_entry, points_entry, scale_combo])
        setattr(self, f'sw_{axis}_list_widget', list_entry)
        setattr(self, f'sw_{axis}_start_entry', start_entry)
        setattr(self, f'sw_{axis}_stop_entry', stop_entry)
        setattr(self, f'sw_{axis}_points_entry', points_entry)

    def _update_sweep_mode_state(self, axis):
        """Grey out the inactive set of inputs for the given axis."""
        mode = getattr(self, f'sw_{axis}_mode').get()
        range_widgets = getattr(self, f'sw_{axis}_range_widgets')
        list_widget = getattr(self, f'sw_{axis}_list_widget')
        if mode == 'range':
            for w in range_widgets:
                w.configure(state='normal' if not isinstance(w, ttk.Combobox) else 'readonly')
            list_widget.configure(state='disabled')
        else:
            for w in range_widgets:
                w.configure(state='disabled')
            list_widget.configure(state='normal')

    def _browse_sweep_output(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile=f"sweep_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        )
        if path:
            self.sw_output.set(path)

    def _default_sweep_path(self):
        log_dir = self.log_dir.get() if hasattr(self, 'log_dir') else './logs'
        os.makedirs(log_dir, exist_ok=True)
        return os.path.join(log_dir, f"sweep_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")

    def _parse_sweep_axis(self, axis, vmin, vmax, name):
        """Return a list[float] for the given axis, validated against [vmin, vmax]."""
        mode = getattr(self, f'sw_{axis}_mode').get()
        if mode == 'list':
            raw = getattr(self, f'sw_{axis}_list_var').get()
            try:
                values = [float(x) for x in raw.replace(';', ',').split(',') if x.strip()]
            except ValueError as e:
                raise ValueError(f"{name} list could not be parsed: {e}")
            if not values:
                raise ValueError(f"{name} list is empty")
        else:
            try:
                start = float(getattr(self, f'sw_{axis}_start_entry').get())
                stop = float(getattr(self, f'sw_{axis}_stop_entry').get())
                points = int(getattr(self, f'sw_{axis}_points_entry').get())
            except ValueError as e:
                raise ValueError(f"{name} range fields must be numeric: {e}")
            if points < 1:
                raise ValueError(f"{name} points must be ≥ 1")
            scale = getattr(self, f'sw_{axis}_scale').get()
            if points == 1:
                values = [start]
            elif scale == 'log':
                if start <= 0 or stop <= 0:
                    raise ValueError(f"{name} log sweep requires positive start and stop")
                ratio = (stop / start) ** (1.0 / (points - 1))
                values = [start * (ratio ** i) for i in range(points - 1)]
                values.append(stop)  # snap endpoint to avoid 100000.00000000003
            else:
                step = (stop - start) / (points - 1)
                values = [start + step * i for i in range(points - 1)]
                values.append(stop)
        for v in values:
            if not (vmin <= v <= vmax):
                raise ValueError(f"{name} value {v} is outside [{vmin}, {vmax}]")
        return values

    def _set_sweep_ui_state(self, running):
        """Enable/disable sweep controls while a sweep is in progress."""
        state = 'disabled' if running else 'normal'
        # Lock the per-axis inputs (re-running _update_sweep_mode_state will
        # restore proper enabled set when running=False).
        for axis in ('freq', 'volt'):
            for w in getattr(self, f'sw_{axis}_range_widgets'):
                w.configure(state='disabled')
            getattr(self, f'sw_{axis}_list_widget').configure(state='disabled')
        self.sw_dwell.configure(state=state)
        self.sw_samples.configure(state=state)
        self.sw_isd.configure(state=state)
        self.sw_order.configure(state='disabled' if running else 'readonly')
        self.sw_start_btn.configure(state='disabled' if running else 'normal')
        self.sw_stop_btn.configure(state='normal' if running else 'disabled')
        if not running:
            self._update_sweep_mode_state('freq')
            self._update_sweep_mode_state('volt')

    def lcr_start_sweep(self):
        if not self.lcr:
            messagebox.showerror("Error", "LCR meter not connected")
            return
        if self.sweeping:
            return
        try:
            freqs = self._parse_sweep_axis('freq', 100, 500000, 'Frequency')
            volts = self._parse_sweep_axis('volt', -5.0, 5.0, 'Voltage')
            dwell = float(self.sw_dwell.get())
            n_samples = int(self.sw_samples.get())
            isd = float(self.sw_isd.get())
            if dwell < 0 or isd < 0:
                raise ValueError("Dwell and inter-sample delay must be ≥ 0")
            if n_samples < 1:
                raise ValueError("Samples per point must be ≥ 1")
        except ValueError as e:
            messagebox.showerror("Invalid sweep parameters", str(e))
            return

        out_path = self.sw_output.get().strip() or self._default_sweep_path()
        self.sw_output.set(out_path)
        mode = self.lcr_mode.get()
        order = self.sw_order.get()

        total_samples = len(freqs) * len(volts) * n_samples
        # Crude estimate so user can sanity-check: per-sample ~0.2s + dwell once per point.
        est_seconds = total_samples * 0.2 + len(freqs) * len(volts) * dwell + total_samples * isd
        if est_seconds > 300:
            mins = est_seconds / 60.0
            if not messagebox.askyesno("Long sweep",
                                       f"Estimated run time: ~{mins:.1f} min "
                                       f"({total_samples} samples). Continue?"):
                return

        self.sweeping = True
        self._set_sweep_ui_state(running=True)
        self.sw_progress.configure(maximum=total_samples, value=0)
        self.sw_status.config(text=f"Starting sweep: {len(freqs)} freqs × {len(volts)} V × "
                                   f"{n_samples} samples → {out_path}", foreground='black')
        self.status_bar.config(text="LCR sweep running...")

        # Drain the queue in case anything stale is in there from a previous run.
        while not self.sweep_queue.empty():
            try:
                self.sweep_queue.get_nowait()
            except queue.Empty:
                break

        self.sweep_thread = threading.Thread(
            target=self._lcr_sweep_worker,
            args=(freqs, volts, mode, order, dwell, n_samples, isd, out_path, total_samples),
            daemon=True,
        )
        self.sweep_thread.start()
        self.root.after(50, self._drain_sweep_queue)

    def lcr_stop_sweep(self):
        if self.sweeping:
            self.sweeping = False
            self.sw_status.config(text="Stop requested — finishing current sample...",
                                  foreground='orange')

    def _interruptible_sleep(self, seconds, chunk=0.05):
        """Sleep in small chunks so cancellation stays snappy."""
        end = time.monotonic() + seconds
        while self.sweeping:
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(chunk, remaining))

    def _lcr_sweep_worker(self, freqs, volts, mode, order, dwell, n_samples, isd,
                          out_path, total_samples):
        """Background thread: run the sweep, write CSV, push events onto sweep_queue.

        Worker NEVER touches Tk directly — all UI changes go through the queue,
        which is drained on the main thread by _drain_sweep_queue. Calling
        root.after() from a worker thread is not safe in tkinter and crashes
        with "main thread is not in main loop".
        """
        sample_count = 0
        try:
            self.lcr.set_mode(mode)
            if order == 'Freq outer, V inner':
                outer_vals, inner_vals = freqs, volts
                outer_setter, inner_setter = self.lcr.set_frequency, self.lcr.set_voltage
            else:
                outer_vals, inner_vals = volts, freqs
                outer_setter, inner_setter = self.lcr.set_voltage, self.lcr.set_frequency

            with open(out_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['Timestamp', 'Mode', 'Frequency (Hz)', 'Voltage (V)',
                                 'Sample Index', 'Primary', 'Secondary', 'Status'])

                for outer_val in outer_vals:
                    if not self.sweeping:
                        break
                    outer_setter(outer_val)
                    for inner_val in inner_vals:
                        if not self.sweeping:
                            break
                        inner_setter(inner_val)
                        if order == 'Freq outer, V inner':
                            freq_val, volt_val = outer_val, inner_val
                        else:
                            volt_val, freq_val = outer_val, inner_val

                        if dwell > 0:
                            self._interruptible_sleep(dwell)
                        if not self.sweeping:
                            break

                        for s in range(n_samples):
                            if not self.sweeping:
                                break
                            primary, secondary, status = self.lcr.measure()
                            ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                            writer.writerow([ts, mode, freq_val, volt_val, s + 1,
                                             primary, secondary, status])
                            f.flush()
                            sample_count += 1
                            self.sweep_queue.put(('progress', sample_count, total_samples,
                                                  freq_val, volt_val, s + 1, n_samples))
                            if isd > 0 and s < n_samples - 1 and self.sweeping:
                                self._interruptible_sleep(isd)
        except Exception as e:
            self.sweep_queue.put(('error', str(e), out_path, sample_count))
            return

        cancelled = not self.sweeping
        self.sweep_queue.put(('done', out_path, sample_count, total_samples, cancelled))

    def _drain_sweep_queue(self):
        """Main-thread poller: drain events from the worker and update UI."""
        final = False
        try:
            while True:
                event = self.sweep_queue.get_nowait()
                kind = event[0]
                if kind == 'progress':
                    _, count, total, freq, volt, sample, n_samples = event
                    self.sw_progress.configure(value=count)
                    self.sw_status.config(
                        text=f"{count}/{total} samples — {freq:g} Hz, {volt:g} V, "
                             f"sample {sample}/{n_samples}",
                        foreground='black',
                    )
                elif kind == 'done':
                    _, out_path, written, total, cancelled = event
                    self._sweep_finished_ui(out_path, written, total, cancelled)
                    final = True
                elif kind == 'error':
                    _, err, out_path, written = event
                    self._sweep_failed_ui(err, out_path, written)
                    final = True
        except queue.Empty:
            pass
        if not final:
            self.root.after(50, self._drain_sweep_queue)

    def _sweep_finished_ui(self, out_path, samples_written, total_samples, cancelled):
        self.sweeping = False
        self._set_sweep_ui_state(running=False)
        if cancelled:
            msg = (f"Sweep cancelled after {samples_written}/{total_samples} samples. "
                   f"Partial data: {out_path}")
            self.sw_status.config(text=msg, foreground='orange')
        else:
            msg = f"Sweep complete: {samples_written} samples written to {out_path}"
            self.sw_status.config(text=msg, foreground='green')
        self.status_bar.config(text=msg)

    def _sweep_failed_ui(self, err, out_path, samples_written):
        self.sweeping = False
        self._set_sweep_ui_state(running=False)
        self.sw_status.config(text=f"Sweep failed after {samples_written} samples: {err}",
                              foreground='red')
        self.status_bar.config(text=f"Sweep failed: {err}")
        messagebox.showerror("Sweep error", f"{err}\n\nPartial CSV (if any): {out_path}")

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
    parser = argparse.ArgumentParser(description="Lab Instrument Control GUI")
    parser.add_argument('--mock', action='store_true',
                        help="Use in-memory mock BK894 (no hardware required)")
    args = parser.parse_args()

    root = tk.Tk()
    app = InstrumentControlGUI(root, use_mock=args.mock)
    root.mainloop()
