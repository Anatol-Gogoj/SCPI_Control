#!/usr/bin/env python3
"""Mock LCR page in ttkbootstrap ('darkly' theme) -- theming layer over ttk."""
import ttkbootstrap as tb
from ttkbootstrap.constants import (BOTH, X, LEFT, RIGHT, W, E,
                                    SUCCESS, INFO, SECONDARY, DANGER,
                                    OUTLINE, PRIMARY)


app = tb.Window(themename='darkly',
                title='LCR Meter (BK 894) — ttkbootstrap')
app.geometry('1560x1080')

conn = tb.Labelframe(app, text='Connection', padding=10)
conn.pack(fill=X, padx=12, pady=(12, 6))
tb.Label(conn, text='●  Connected: B&K Precision, 894,479C21102,1.0.5,6.0',
         bootstyle=SUCCESS).pack(side=LEFT)
tb.Button(conn, text='Reconnect',
          bootstyle=(OUTLINE, SECONDARY)).pack(side=RIGHT)

cfg = tb.Labelframe(app, text='Configuration', padding=12)
cfg.pack(fill=X, padx=12, pady=6)
tb.Label(cfg, text='Mode:').grid(row=0, column=0, sticky=W, pady=4)
mode = tb.Combobox(cfg, values=['CPD', 'CPQ', 'CSRS', 'LSRS', 'RX', 'ZTD'],
                   width=18)
mode.set('CPD')
mode.grid(row=0, column=1, padx=8, pady=4)
tb.Label(cfg, text='Frequency (Hz):').grid(row=1, column=0, sticky=W, pady=4)
f = tb.Entry(cfg, width=20)
f.insert(0, '1000')
f.grid(row=1, column=1, padx=8, pady=4)
tb.Label(cfg, text='Voltage (V):').grid(row=2, column=0, sticky=W, pady=4)
v = tb.Entry(cfg, width=20)
v.insert(0, '1.0')
v.grid(row=2, column=1, padx=8, pady=4)

tb.Label(cfg, text='DC Bias (V):').grid(row=0, column=2, sticky=E,
                                        padx=(30, 4))
b = tb.Entry(cfg, width=8)
b.insert(0, '0.0')
b.grid(row=0, column=3, sticky=W)
tb.Checkbutton(cfg, text='Bias ON',
               bootstyle='success-round-toggle').grid(row=0, column=4,
                                                      padx=10)
tb.Label(cfg, text='Speed:').grid(row=1, column=2, sticky=E, padx=(30, 4))
sp = tb.Combobox(cfg, values=['SLOW', 'MED', 'FAST'], width=6)
sp.set('MED')
sp.grid(row=1, column=3, sticky=W)
tb.Label(cfg, text='Avg:').grid(row=1, column=4, sticky=E)
av = tb.Entry(cfg, width=5)
av.insert(0, '1')
av.grid(row=1, column=5, sticky=W, padx=4)
auto = tb.Checkbutton(cfg, text='Auto range',
                      bootstyle='info-round-toggle')
auto.grid(row=2, column=2, columnspan=2, sticky=W, padx=(30, 0))
auto.invoke()
tb.Button(cfg, text='Open Corr…',
          bootstyle=(OUTLINE, INFO)).grid(row=2, column=4, padx=3)
tb.Button(cfg, text='Short Corr…',
          bootstyle=(OUTLINE, INFO)).grid(row=2, column=5, padx=3)
tb.Button(cfg, text='Apply Configuration',
          bootstyle=SUCCESS).grid(row=3, column=0, columnspan=2, pady=10)
tb.Label(cfg, text='Correction: open ON, short ON',
         bootstyle=SECONDARY).grid(row=3, column=2, columnspan=4, sticky=W,
                                   padx=(30, 0))

meas = tb.Labelframe(app, text='Current Measurement', padding=12)
meas.pack(fill=BOTH, expand=True, padx=12, pady=6)
row = tb.Frame(meas)
row.pack(fill=X)
left = tb.Frame(row)
left.pack(side=LEFT, expand=True)
tb.Label(left, text='Primary:  3.300 nF',
         font=('TkDefaultFont', 22, 'bold')).pack(pady=8, anchor=W)
tb.Label(left, text='Secondary:  D: 0.0012',
         font=('TkDefaultFont', 16)).pack(pady=4, anchor=W)
tb.Label(left, text='Status: OK', bootstyle=SUCCESS).pack(pady=4, anchor=W)
meter = tb.Meter(row, metersize=300, amounttotal=10, amountused=3.3,
                 subtext='Capacitance', textright='nF', bootstyle=INFO,
                 stripethickness=6)
meter.pack(side=RIGHT, padx=20)
btns = tb.Frame(meas)
btns.pack(pady=12)
tb.Button(btns, text='Single Measurement',
          bootstyle=PRIMARY).pack(side=LEFT, padx=5)
tb.Button(btns, text='Start Continuous',
          bootstyle=(OUTLINE, SUCCESS)).pack(side=LEFT, padx=5)
tb.Button(btns, text='Stop', bootstyle=(OUTLINE, DANGER)).pack(side=LEFT,
                                                               padx=5)


app.mainloop()
