#!/usr/bin/env python3
"""Mock LCR page in PySide6 (Fusion dark) + a pyqtgraph C-V plot."""
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets


app = QtWidgets.QApplication([])
app.setStyle('Fusion')
pal = QtGui.QPalette()
pal.setColor(QtGui.QPalette.Window, QtGui.QColor(45, 47, 51))
pal.setColor(QtGui.QPalette.WindowText, QtGui.QColor(225, 225, 225))
pal.setColor(QtGui.QPalette.Base, QtGui.QColor(35, 37, 41))
pal.setColor(QtGui.QPalette.Text, QtGui.QColor(225, 225, 225))
pal.setColor(QtGui.QPalette.Button, QtGui.QColor(58, 61, 66))
pal.setColor(QtGui.QPalette.ButtonText, QtGui.QColor(225, 225, 225))
pal.setColor(QtGui.QPalette.Highlight, QtGui.QColor(42, 130, 218))
app.setPalette(pal)

win = QtWidgets.QWidget()
win.setWindowTitle('LCR Meter (BK 894) — PySide6 + pyqtgraph')
main = QtWidgets.QVBoxLayout(win)

conn = QtWidgets.QGroupBox('Connection')
cl = QtWidgets.QHBoxLayout(conn)
ok = QtWidgets.QLabel('●  Connected: B&K Precision, 894,479C21102,1.0.5,6.0')
ok.setStyleSheet('color: #4caf50; font-weight: 600;')
cl.addWidget(ok)
cl.addStretch()
cl.addWidget(QtWidgets.QPushButton('Reconnect'))
main.addWidget(conn)

cfg = QtWidgets.QGroupBox('Configuration')
g = QtWidgets.QGridLayout(cfg)
g.addWidget(QtWidgets.QLabel('Mode:'), 0, 0)
mode = QtWidgets.QComboBox()
mode.addItems(['CPD', 'CPQ', 'CSRS', 'LSRS', 'RX', 'ZTD'])
g.addWidget(mode, 0, 1)
g.addWidget(QtWidgets.QLabel('Frequency (Hz):'), 1, 0)
freq = QtWidgets.QDoubleSpinBox(maximum=500000, decimals=0)
freq.setValue(1000)
g.addWidget(freq, 1, 1)
g.addWidget(QtWidgets.QLabel('Voltage (V):'), 2, 0)
volt = QtWidgets.QDoubleSpinBox(maximum=2.0, singleStep=0.1)
volt.setValue(1.0)
g.addWidget(volt, 2, 1)
g.addWidget(QtWidgets.QLabel('DC Bias (V):'), 0, 2)
bias = QtWidgets.QDoubleSpinBox(minimum=-5, maximum=5, singleStep=0.1)
g.addWidget(bias, 0, 3)
g.addWidget(QtWidgets.QCheckBox('Bias ON'), 0, 4)
g.addWidget(QtWidgets.QLabel('Speed:'), 1, 2)
speed = QtWidgets.QComboBox()
speed.addItems(['SLOW', 'MED', 'FAST'])
speed.setCurrentText('MED')
g.addWidget(speed, 1, 3)
avg = QtWidgets.QSpinBox(minimum=1, maximum=256)
g.addWidget(QtWidgets.QLabel('Avg:'), 1, 4)
g.addWidget(avg, 1, 5)
auto = QtWidgets.QCheckBox('Auto range')
auto.setChecked(True)
g.addWidget(auto, 2, 2, 1, 2)
g.addWidget(QtWidgets.QPushButton('Open Corr…'), 2, 4)
g.addWidget(QtWidgets.QPushButton('Short Corr…'), 2, 5)
apply_btn = QtWidgets.QPushButton('Apply Configuration')
apply_btn.setStyleSheet(
    'background: #2a82da; color: white; font-weight: 600; padding: 6px 14px;')
g.addWidget(apply_btn, 3, 0, 1, 2)
corr = QtWidgets.QLabel('Correction: open ON, short ON')
corr.setStyleSheet('color: #9e9e9e;')
g.addWidget(corr, 3, 2, 1, 4)
main.addWidget(cfg)

meas = QtWidgets.QGroupBox('Current Measurement')
ml = QtWidgets.QHBoxLayout(meas)
left = QtWidgets.QVBoxLayout()
big = QtWidgets.QLabel('3.300 nF')
big.setStyleSheet('font-size: 34px; font-weight: 700;')
sec = QtWidgets.QLabel('D: 0.0012')
sec.setStyleSheet('font-size: 20px; color: #b0bec5;')
st = QtWidgets.QLabel('Status: OK')
st.setStyleSheet('color: #4caf50;')
for wdg in (big, sec, st):
    left.addWidget(wdg)
row = QtWidgets.QHBoxLayout()
for name in ('Single Measurement', 'Start Continuous', 'Stop'):
    row.addWidget(QtWidgets.QPushButton(name))
left.addLayout(row)
left.addStretch()
ml.addLayout(left, 1)

pg.setConfigOptions(background=(35, 37, 41), foreground='w', antialias=True)
plot = pg.PlotWidget(title='C vs DC bias (live)')
plot.setLabel('bottom', 'Bias', units='V')
plot.setLabel('left', 'Capacitance', units='F')
plot.showGrid(x=True, y=True, alpha=0.25)
xs = [0.25 * i for i in range(9)]
ys = [3.30e-9, 3.28e-9, 3.22e-9, 3.11e-9, 2.96e-9,
      2.78e-9, 2.58e-9, 2.38e-9, 2.19e-9]
plot.plot(xs, ys, pen=pg.mkPen('#2a82da', width=2),
          symbol='o', symbolBrush='#2a82da', symbolSize=6)
ml.addWidget(plot, 1)
main.addWidget(meas, 1)

win.resize(880, 640)
win.show()


app.exec()
