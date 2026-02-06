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
        self.root.geometry("900x700")
        
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
        
        # Channel configuration
        config_frame = ttk.LabelFrame(scope_frame, text="Channel 1 Configuration", padding=10)
        config_frame.pack(fill='x', padx=10, pady=10)
        
        # Vertical scale
        ttk.Label(config_frame, text="Vertical (V/div):").grid(row=0, column=0, sticky='w', pady=5)
        self.scope_vscale = ttk.Entry(config_frame, width=15)
        self.scope_vscale.grid(row=0, column=1, padx=10, pady=5)
        self.scope_vscale.insert(0, "1.0")
        
        # Coupling
        ttk.Label(config_frame, text="Coupling:").grid(row=0, column=2, sticky='w', pady=5, padx=(20,0))
        self.scope_coupling = ttk.Combobox(config_frame, width=10, state='readonly')
        self.scope_coupling.grid(row=0, column=3, padx=10, pady=5)
        self.scope_coupling['values'] = ['DC', 'AC', 'GND']
        self.scope_coupling.set('DC')
        
        # Horizontal scale
        ttk.Label(config_frame, text="Horizontal (s/div):").grid(row=1, column=0, sticky='w', pady=5)
        self.scope_hscale = ttk.Entry(config_frame, width=15)
        self.scope_hscale.grid(row=1, column=1, padx=10, pady=5)
        self.scope_hscale.insert(0, "0.001")
        
        # Trigger level
        ttk.Label(config_frame, text="Trigger Level (V):").grid(row=1, column=2, sticky='w', pady=5, padx=(20,0))
        self.scope_trig_level = ttk.Entry(config_frame, width=10)
        self.scope_trig_level.grid(row=1, column=3, padx=10, pady=5)
        self.scope_trig_level.insert(0, "0")
        
        # Apply button
        ttk.Button(config_frame, text="Apply Configuration", 
                   command=self.apply_scope_config).grid(row=2, column=0, columnspan=4, pady=10)
        
        # Measurements display
        meas_frame = ttk.LabelFrame(scope_frame, text="Automated Measurements (CH1)", padding=10)
        meas_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        # Create measurement labels
        self.scope_meas_labels = {}
        row = 0
        for meas in ['Frequency', 'Period', 'Mean', 'Pk-Pk', 'RMS', 'Amplitude']:
            label = tk.Label(meas_frame, text=f"{meas}: --", font=("Arial", 12), anchor='w')
            label.grid(row=row//2, column=row%2, sticky='w', padx=20, pady=5)
            self.scope_meas_labels[meas.lower().replace('-', '')] = label
            row += 1
        
        # Control buttons
        button_frame = ttk.Frame(meas_frame)
        button_frame.grid(row=3, column=0, columnspan=2, pady=10)
        
        ttk.Button(button_frame, text="Single Acquisition", 
                   command=self.scope_single).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Run", 
                   command=self.scope_run).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Stop", 
                   command=self.scope_stop).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Get Measurements", 
                   command=self.scope_get_measurements).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Capture Waveform", 
                   command=self.scope_capture_waveform).pack(side=tk.LEFT, padx=5)
    
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
        self.log_lcr = tk.BooleanVar(value=True)
        self.log_scope = tk.BooleanVar(value=False)
        ttk.Checkbutton(config_frame, text="LCR Meter", variable=self.log_lcr).grid(row=2, column=1, sticky='w', padx=10)
        ttk.Checkbutton(config_frame, text="Oscilloscope", variable=self.log_scope).grid(row=3, column=1, sticky='w', padx=10)
        
        # Control buttons
        button_frame = ttk.Frame(config_frame)
        button_frame.grid(row=4, column=0, columnspan=3, pady=20)
        
        self.log_start_btn = ttk.Button(button_frame, text="Start Logging", 
                                         command=self.start_logging)
        self.log_start_btn.pack(side=tk.LEFT, padx=5)
        
        self.log_stop_btn = ttk.Button(button_frame, text="Stop Logging", 
                                        command=self.stop_logging, state='disabled')
        self.log_stop_btn.pack(side=tk.LEFT, padx=5)
        
        # Log display
        display_frame = ttk.LabelFrame(log_frame, text="Log Status", padding=10)
        display_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        self.log_text = tk.Text(display_frame, height=15, state='disabled')
        self.log_text.pack(fill='both', expand=True)
        
        scrollbar = ttk.Scrollbar(display_frame, command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar.set)
    
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
    
    def apply_scope_config(self):
        if not self.scope:
            messagebox.showerror("Error", "Oscilloscope not connected")
            return
        
        try:
            vscale = float(self.scope_vscale.get())
            hscale = float(self.scope_hscale.get())
            coupling = self.scope_coupling.get()
            trig_level = float(self.scope_trig_level.get())
            
            self.scope.set_channel_enable(1, True)
            self.scope.set_vertical(1, scale=vscale, coupling=coupling)
            self.scope.set_horizontal(scale=hscale)
            self.scope.set_trigger_edge(source='CH1', level=trig_level)
            
            self.status_bar.config(text=f"Scope configured: {vscale}V/div, {hscale}s/div")
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
    
    def scope_get_measurements(self):
        if not self.scope:
            messagebox.showerror("Error", "Oscilloscope not connected")
            return
        
        try:
            meas = self.scope.get_all_measurements(1)
            
            for key, value in meas.items():
                label_key = key.replace('_', '')
                if label_key in self.scope_meas_labels:
                    if value is not None:
                        # Format appropriately
                        if key == 'freq':
                            text = f"{value:.3f} Hz" if value < 1e3 else f"{value/1e3:.3f} kHz"
                        elif key == 'period':
                            text = f"{value*1e6:.3f} µs" if value < 1e-3 else f"{value*1e3:.3f} ms"
                        else:
                            text = f"{value:.4f} V"
                        
                        self.scope_meas_labels[label_key].config(
                            text=f"{key.capitalize()}: {text}"
                        )
                    else:
                        self.scope_meas_labels[label_key].config(
                            text=f"{key.capitalize()}: No signal"
                        )
        except Exception as e:
            messagebox.showerror("Measurement Error", str(e))
    
    def scope_capture_waveform(self):
        if not self.scope:
            messagebox.showerror("Error", "Oscilloscope not connected")
            return
        
        try:
            filename = filedialog.asksaveasfilename(
                defaultextension=".csv",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
            )
            
            if not filename:
                return
            
            self.status_bar.config(text="Capturing waveform...")
            self.root.update()
            
            waveform = self.scope.get_waveform(1)
            
            with open(filename, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['Time (s)', 'Voltage (V)'])
                for t, v in zip(waveform['t'], waveform['v']):
                    writer.writerow([t, v])
            
            self.status_bar.config(text=f"Waveform saved: {filename}")
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
        scope_file = None
        lcr_writer = None
        scope_writer = None
        
        if self.log_lcr.get() and self.lcr:
            lcr_filename = os.path.join(log_path, f"lcr_{timestamp}.csv")
            lcr_file = open(lcr_filename, 'w', newline='')
            lcr_writer = csv.writer(lcr_file)
            lcr_writer.writerow(['Timestamp', 'Mode', 'Frequency (Hz)', 'Primary', 'Secondary', 'Status'])
            self.log_message(f"LCR log: {lcr_filename}")
        
        if self.log_scope.get() and self.scope:
            scope_filename = os.path.join(log_path, f"scope_{timestamp}.csv")
            scope_file = open(scope_filename, 'w', newline='')
            scope_writer = csv.writer(scope_file)
            scope_writer.writerow(['Timestamp', 'Frequency (Hz)', 'Period (s)', 'Mean (V)', 
                                  'Pk-Pk (V)', 'RMS (V)', 'Amplitude (V)'])
            self.log_message(f"Scope log: {scope_filename}")
        
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
            
            # Log scope data
            if scope_writer and self.scope:
                try:
                    meas = self.scope.get_all_measurements(1)
                    scope_writer.writerow([timestamp, meas.get('freq'), meas.get('period'),
                                          meas.get('mean'), meas.get('pk2pk'), 
                                          meas.get('rms'), meas.get('amplitude')])
                    scope_file.flush()
                except Exception as e:
                    self.log_message(f"Scope error: {e}")
            
            time.sleep(interval)
        
        # Close files
        if lcr_file:
            lcr_file.close()
        if scope_file:
            scope_file.close()
    
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