#!/usr/bin/env python3
"""Mock LCR page in NiceGUI -- Python backend, browser frontend."""
from nicegui import ui

MODES = ['CPD', 'CPQ', 'CSRS', 'LSRS', 'RX', 'ZTD']

with ui.column().classes('w-[860px] mx-auto gap-4 p-4'):
    ui.label('LCR Meter (BK 894)').classes('text-2xl font-bold')

    with ui.card().classes('w-full'):
        with ui.row().classes('items-center w-full'):
            ui.icon('check_circle', color='green', size='sm')
            ui.label('Connected: B&K Precision, 894,479C21102,1.0.5,6.0') \
                .classes('text-green-700 font-medium')
            ui.space()
            ui.button('Reconnect', icon='refresh').props('outline')

    with ui.card().classes('w-full'):
        ui.label('Configuration').classes('text-lg font-semibold')
        with ui.grid(columns=3).classes('w-full gap-2 items-center'):
            ui.select(MODES, value='CPD', label='Mode').classes('w-44')
            ui.number('Frequency (Hz)', value=1000).classes('w-44')
            ui.number('Voltage (V)', value=1.0).classes('w-44')
            ui.number('DC Bias (V)', value=0.0).classes('w-44')
            with ui.row().classes('items-center gap-6'):
                ui.switch('Bias ON')
                ui.switch('Auto range', value=True)
            with ui.row().classes('items-center gap-2'):
                ui.select(['SLOW', 'MED', 'FAST'], value='MED',
                          label='Speed').classes('w-24')
                ui.number('Avg', value=1).classes('w-16')
        with ui.row().classes('items-center gap-2 pt-2'):
            ui.button('Apply Configuration', icon='done', color='primary')
            ui.button('Open Corr…').props('outline color=teal')
            ui.button('Short Corr…').props('outline color=teal')
            ui.space()
            ui.label('Correction: open ON, short ON') \
                .classes('text-gray-500 text-sm')

    with ui.card().classes('w-full'):
        ui.label('Current Measurement').classes('text-lg font-semibold')
        with ui.row().classes('w-full items-center'):
            with ui.column().classes('gap-1'):
                ui.label('3.300 nF').classes('text-4xl font-bold')
                ui.label('D: 0.0012').classes('text-xl text-gray-600')
                with ui.row().classes('items-center gap-1'):
                    ui.icon('check_circle', color='green', size='xs')
                    ui.label('Status: OK').classes('text-green-700 text-sm')
            ui.space()
            chart = ui.echart({
                'xAxis': {'type': 'category', 'name': 'bias (V)',
                          'data': [round(0.25 * i, 2) for i in range(9)]},
                'yAxis': {'type': 'value', 'name': 'C (nF)',
                          'min': 2.0, 'max': 3.5},
                'animation': False,
                'series': [{'type': 'line', 'smooth': True,
                            'areaStyle': {'opacity': 0.15},
                            'data': [3.30, 3.28, 3.22, 3.11, 2.96,
                                     2.78, 2.58, 2.38, 2.19]}],
                'grid': {'left': 50, 'right': 20, 'top': 30, 'bottom': 40},
            }).classes('w-[420px] h-56')
        with ui.row().classes('gap-2 pt-2'):
            ui.button('Single Measurement', color='primary')
            ui.button('Start Continuous', color='positive').props('outline')
            ui.button('Stop', color='negative').props('outline')

ui.run(port=8091, show=False, reload=False, title='LCR — NiceGUI')
