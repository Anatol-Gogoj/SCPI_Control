#!/usr/bin/env python3
"""Small reusable Tk UI helpers: hover tooltips and scrollable tabs.

Both address long-standing GUI complaints (2026-07-10): content taller
than the window was simply CUT OFF with no scrollbar, and none of the
controls explained themselves (e.g. the LCR Speed/Avg fields).
"""
import tkinter as tk
from tkinter import ttk


class Tooltip:
    """Show `text` in a small popup after hovering `widget` for `delay` ms.

    Tk has no built-in tooltip; this is the standard Toplevel +
    overrideredirect pattern. Hides on leave/click/destroy.
    """

    def __init__(self, widget, text, delay=650, wraplength=340):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.wraplength = wraplength
        self._after_id = None
        self._tip = None
        widget.bind('<Enter>', self._schedule, add='+')
        widget.bind('<Leave>', self._hide, add='+')
        widget.bind('<ButtonPress>', self._hide, add='+')
        widget.bind('<Destroy>', self._hide, add='+')

    def _schedule(self, _event=None):
        self._cancel()
        self._after_id = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self):
        if self._tip is not None or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 14
            y = (self.widget.winfo_rooty()
                 + self.widget.winfo_height() + 6)
        except tk.TclError:      # widget died while the timer was pending
            return
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f'+{x}+{y}')
        tk.Label(tip, text=self.text, justify='left',
                 wraplength=self.wraplength, bg='#ffffe0', fg='black',
                 relief='solid', borderwidth=1, padx=7, pady=5).pack()
        self._tip = tip

    def _hide(self, _event=None):
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None


def add_tooltip(widget, text):
    """Attach a hover tooltip; returns the widget for chaining."""
    Tooltip(widget, text)
    return widget


class ScrollableTab(ttk.Frame):
    """Notebook tab with a vertical scrollbar when content doesn't fit.

    Build the tab's content into `.body` instead of the tab itself.
    The scrollbar only matters when the window is shorter than the
    content -- exactly the cut-off case it fixes.
    """

    def __init__(self, notebook):
        super().__init__(notebook)
        bg = ttk.Style().lookup('TFrame', 'background') or None
        self._canvas = tk.Canvas(self, highlightthickness=0,
                                 **({'bg': bg} if bg else {}))
        vbar = ttk.Scrollbar(self, orient='vertical',
                             command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=vbar.set)
        vbar.pack(side=tk.RIGHT, fill='y')
        self._canvas.pack(side=tk.LEFT, fill='both', expand=True)
        self.body = ttk.Frame(self._canvas)
        self._win = self._canvas.create_window((0, 0), window=self.body,
                                               anchor='nw')
        self.body.bind('<Configure>', self._on_body_configure)
        self._canvas.bind('<Configure>', self._on_canvas_configure)
        # Mouse wheel scrolls whichever tab the pointer is over. bind_all
        # is grabbed on Enter and released on Leave so tabs don't fight.
        self.bind('<Enter>', self._bind_wheel)
        self.bind('<Leave>', self._unbind_wheel)

    def _on_body_configure(self, _event):
        self._canvas.configure(scrollregion=self._canvas.bbox('all'))

    def _on_canvas_configure(self, event):
        self._canvas.itemconfigure(self._win, width=event.width)

    def _wheel(self, event):
        if event.num == 4 or event.delta > 0:
            self._canvas.yview_scroll(-2, 'units')
        elif event.num == 5 or event.delta < 0:
            self._canvas.yview_scroll(2, 'units')

    def _bind_wheel(self, _event=None):
        self._canvas.bind_all('<Button-4>', self._wheel)     # X11 up
        self._canvas.bind_all('<Button-5>', self._wheel)     # X11 down
        self._canvas.bind_all('<MouseWheel>', self._wheel)   # other OSes

    def _unbind_wheel(self, _event=None):
        for seq in ('<Button-4>', '<Button-5>', '<MouseWheel>'):
            self._canvas.unbind_all(seq)
