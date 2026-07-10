#!/usr/bin/env python3
"""Battery Tester tab: post-process cycler exports inside the multitool.

GUI half of issue #31, adapted with minimal changes from the lab's
standalone tool (Pingwinos40/BatteryProcessing, battery_gui.py v1.1.0)
so it produces IDENTICAL output: same processed CSV, same 300-dpi batch
plots, same custom-plot behavior. Processing logic lives in
battery_process.py (pure, headless-tested).

Import this module only after battery_process.deps_available() says the
heavy deps (pandas/matplotlib/openpyxl) are importable -- gui.py shows
an install hint otherwise, exactly like the webcam tab.
"""
import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt                                  # noqa: E402
from matplotlib.backends.backend_tkagg import (                  # noqa: E402
    FigureCanvasTkAgg, NavigationToolbar2Tk)
from matplotlib.figure import Figure                             # noqa: E402
import pandas as pd                                              # noqa: E402

from battery_process import (STATUS_COLORS, TIME_COLUMNS, TIME_UNITS,
                             axis_label, load_and_process,
                             parse_cycle_selection)


class BatteryPane(ttk.Frame):
    """The whole battery tool as an embeddable pane."""

    def __init__(self, parent, status_bar=None):
        super().__init__(parent)
        self.status_bar = status_bar
        self.df = None
        self.source_path = None
        self._build_ui()

    # -- UI construction ---------------------------------------------------

    def _build_ui(self):
        top = ttk.Frame(self, padding=5)
        top.pack(fill=tk.X)
        ttk.Button(top, text="Load File…",
                   command=self._load_file).pack(side=tk.LEFT)
        self.lbl_file = ttk.Label(top, text="No file loaded",
                                  foreground="gray")
        self.lbl_file.pack(side=tk.LEFT, padx=10)
        ttk.Button(top, text="Export Processed CSV…",
                   command=self._export_csv).pack(side=tk.RIGHT)

        self.plot_nb = ttk.Notebook(self)
        self.plot_nb.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self._build_batch_tab()
        self._build_custom_tab()

    # -- Load / export -----------------------------------------------------

    def _load_file(self):
        path = filedialog.askopenfilename(
            title="Select Battery Tester File",
            filetypes=[("Excel files", "*.xls *.xlsx"),
                       ("All files", "*.*")])
        if not path:
            return
        fname = os.path.basename(path)
        self.lbl_file.config(text=f"Loading {fname}…", foreground="blue")
        self.config(cursor="watch")
        self.update_idletasks()

        def _do_load():
            try:
                df = load_and_process(path)
                self.after(0, lambda: self._on_load_success(df, path, fname))
            except Exception as e:
                self.after(0, lambda: self._on_load_error(e))

        threading.Thread(target=_do_load, daemon=True).start()

    def _on_load_success(self, df, path, fname):
        self.df = df
        self.source_path = path
        self.config(cursor="")
        self.lbl_file.config(
            text=f"{fname}  —  {len(df):,} rows × {len(df.columns)} cols",
            foreground="black")
        self._populate_custom_tab()
        self._populate_batch_tab()

    def _on_load_error(self, error):
        self.config(cursor="")
        self.lbl_file.config(text="Load failed", foreground="red")
        messagebox.showerror("Load Error", str(error))

    def _export_csv(self):
        if self.df is None:
            messagebox.showwarning("No Data", "Load a file first.")
            return
        default_name = ""
        if self.source_path:
            base = os.path.splitext(os.path.basename(self.source_path))[0]
            default_name = f"{base}_processed.csv"
        path = filedialog.asksaveasfilename(
            title="Save Processed CSV", defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        self.df.to_csv(path, index=False)
        messagebox.showinfo("Saved",
                            f"Exported {len(self.df):,} rows to:\n{path}")

    # -- Batch plot tab ------------------------------------------------------

    def _build_batch_tab(self):
        self.batch_frame = ttk.Frame(self.plot_nb, padding=10)
        self.plot_nb.add(self.batch_frame, text="Batch Plot")

        ctrl = ttk.Frame(self.batch_frame)
        ctrl.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(ctrl, text="Plot style:").pack(side=tk.LEFT)
        self.batch_style = tk.StringVar(value="line")
        ttk.Radiobutton(ctrl, text="Line", variable=self.batch_style,
                        value="line").pack(side=tk.LEFT, padx=(5, 0))
        ttk.Radiobutton(ctrl, text="Scatter", variable=self.batch_style,
                        value="scatter").pack(side=tk.LEFT, padx=(5, 0))
        ttk.Separator(ctrl, orient=tk.VERTICAL).pack(side=tk.LEFT,
                                                     fill=tk.Y, padx=10)
        ttk.Label(ctrl, text="Time unit:").pack(side=tk.LEFT)
        self.batch_time_unit = tk.StringVar(value="seconds")
        ttk.Combobox(ctrl, textvariable=self.batch_time_unit,
                     values=list(TIME_UNITS.keys()), state="readonly",
                     width=10).pack(side=tk.LEFT, padx=(5, 0))
        ttk.Separator(ctrl, orient=tk.VERTICAL).pack(side=tk.LEFT,
                                                     fill=tk.Y, padx=10)
        ttk.Button(ctrl, text="Generate All Cycle Plots",
                   command=self._run_batch_plot).pack(side=tk.LEFT)
        self.batch_status = ttk.Label(ctrl, text="", foreground="gray")
        self.batch_status.pack(side=tk.LEFT, padx=10)

        self.batch_progress = ttk.Progressbar(self.batch_frame,
                                              mode="determinate")
        self.batch_progress.pack(fill=tk.X, pady=(0, 5))

    def _populate_batch_tab(self):
        if self.df is not None and "cycle" in self.df.columns:
            self.batch_status.config(
                text=f"{self.df['cycle'].nunique()} cycles detected")
        else:
            self.batch_status.config(text="No cycle data found")

    def _run_batch_plot(self):
        if self.df is None:
            messagebox.showwarning("No Data", "Load a file first.")
            return
        if "cycle" not in self.df.columns:
            messagebox.showwarning("No Cycles", "No 'cycle' column in data.")
            return
        out_dir = filedialog.askdirectory(
            title="Select Output Folder for Cycle Plots")
        if not out_dir:
            return

        style = self.batch_style.get()
        time_factor, time_label = TIME_UNITS.get(
            self.batch_time_unit.get(), (1.0, "s"))
        cycles = sorted(self.df["cycle"].dropna().unique())
        self.batch_progress["maximum"] = len(cycles) * 2
        self.batch_progress["value"] = 0
        count = 0

        for cyc in cycles:
            cyc_df = self.df[self.df["cycle"] == cyc].copy()
            if cyc_df.empty:
                continue
            cyc_int = int(cyc)
            if "relative_time_s" in cyc_df.columns:
                t0 = cyc_df["relative_time_s"].min()
                cyc_df["cycle_time_s"] = ((cyc_df["relative_time_s"] - t0)
                                          * time_factor)

            if ("cycle_time_s" in cyc_df.columns
                    and "voltage_V" in cyc_df.columns):
                fig, ax = plt.subplots(figsize=(10, 5))
                self._plot_by_status(ax, cyc_df, "cycle_time_s",
                                     "voltage_V", style)
                ax.set_xlabel(f"Time from cycle start ({time_label})")
                ax.set_ylabel("Voltage (V)")
                ax.set_title(f"Cycle {cyc_int} — Voltage vs Time")
                ax.legend(loc="best")
                ax.grid(True, alpha=0.3)
                fig.tight_layout()
                fig.savefig(os.path.join(
                    out_dir, f"cycle_{cyc_int:03d}_voltage_vs_time.png"),
                    dpi=300)
                plt.close(fig)
            count += 1
            self.batch_progress["value"] = count
            self.update_idletasks()

            if ("capacity_mAh" in cyc_df.columns
                    and "voltage_V" in cyc_df.columns):
                fig, ax = plt.subplots(figsize=(10, 5))
                self._plot_by_status(ax, cyc_df, "capacity_mAh",
                                     "voltage_V", style)
                ax.set_xlabel("Capacity (mAh)")
                ax.set_ylabel("Voltage (V)")
                ax.set_title(f"Cycle {cyc_int} — Voltage vs Capacity")
                ax.legend(loc="best")
                ax.grid(True, alpha=0.3)
                fig.tight_layout()
                fig.savefig(os.path.join(
                    out_dir, f"cycle_{cyc_int:03d}_voltage_vs_capacity.png"),
                    dpi=300)
                plt.close(fig)
            count += 1
            self.batch_progress["value"] = count
            self.update_idletasks()

        self.batch_status.config(
            text=f"Done — {count} plots saved to {out_dir}")
        messagebox.showinfo("Batch Plot Complete",
                            f"Saved {count} plots (300 dpi) to:\n{out_dir}")

    @staticmethod
    def _plot_by_status(ax, df, x_col, y_col, style="line"):
        """Plot data colored by status, skipping rest states."""
        if "status" in df.columns:
            for status, group in df.groupby("status"):
                if status == "rest":
                    continue
                color = STATUS_COLORS.get(status, "#333333")
                if style == "line":
                    ax.plot(group[x_col], group[y_col], linewidth=0.8,
                            alpha=0.8, color=color, label=status)
                else:
                    ax.scatter(group[x_col], group[y_col], s=2, alpha=0.5,
                               color=color, label=status)
        else:
            if style == "line":
                ax.plot(df[x_col], df[y_col], linewidth=0.8, alpha=0.8,
                        color="#1f77b4")
            else:
                ax.scatter(df[x_col], df[y_col], s=2, alpha=0.5,
                           color="#1f77b4")

    # -- Custom plot tab -----------------------------------------------------

    def _build_custom_tab(self):
        self.custom_frame = ttk.Frame(self.plot_nb, padding=10)
        self.plot_nb.add(self.custom_frame, text="Custom Plot")

        left = ttk.LabelFrame(self.custom_frame, text="Plot Settings",
                              padding=10)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 5))

        ttk.Label(left, text="X Axis:").grid(row=0, column=0,
                                             sticky=tk.W, pady=2)
        self.custom_x = ttk.Combobox(left, state="readonly", width=22)
        self.custom_x.grid(row=0, column=1, pady=2, padx=5)
        ttk.Label(left, text="Y Axis:").grid(row=1, column=0,
                                             sticky=tk.W, pady=2)
        self.custom_y = ttk.Combobox(left, state="readonly", width=22)
        self.custom_y.grid(row=1, column=1, pady=2, padx=5)

        ttk.Label(left, text="Style:").grid(row=2, column=0,
                                            sticky=tk.W, pady=2)
        style_frame = ttk.Frame(left)
        style_frame.grid(row=2, column=1, sticky=tk.W, pady=2)
        self.custom_style = tk.StringVar(value="line")
        ttk.Radiobutton(style_frame, text="Line",
                        variable=self.custom_style,
                        value="line").pack(side=tk.LEFT)
        ttk.Radiobutton(style_frame, text="Scatter",
                        variable=self.custom_style,
                        value="scatter").pack(side=tk.LEFT, padx=(5, 0))

        self.custom_color_status = tk.BooleanVar(value=True)
        ttk.Checkbutton(left, text="Color by status",
                        variable=self.custom_color_status).grid(
            row=3, column=0, columnspan=2, sticky=tk.W, pady=2)

        ttk.Label(left, text="Time unit:").grid(row=4, column=0,
                                                sticky=tk.W, pady=2)
        self.custom_time_unit = tk.StringVar(value="seconds")
        ttk.Combobox(left, textvariable=self.custom_time_unit,
                     values=list(TIME_UNITS.keys()), state="readonly",
                     width=20).grid(row=4, column=1, pady=2, padx=5)

        ttk.Separator(left, orient=tk.HORIZONTAL).grid(
            row=5, column=0, columnspan=2, sticky=tk.EW, pady=8)
        ttk.Label(left, text="Cycles:").grid(row=6, column=0,
                                             sticky=tk.W, pady=2)
        self.cycle_mode = tk.StringVar(value="all")
        ttk.Radiobutton(left, text="All cycles", variable=self.cycle_mode,
                        value="all", command=self._toggle_cycle_entry).grid(
            row=6, column=1, sticky=tk.W, pady=2)
        ttk.Radiobutton(left, text="Selected:", variable=self.cycle_mode,
                        value="selected",
                        command=self._toggle_cycle_entry).grid(
            row=7, column=0, sticky=tk.W, pady=2)
        self.cycle_entry = ttk.Entry(left, width=22)
        self.cycle_entry.grid(row=7, column=1, pady=2, padx=5)
        self.cycle_entry.insert(0, "e.g. 1,2,3 or 1-5")
        self.cycle_entry.config(state="disabled")

        ttk.Separator(left, orient=tk.HORIZONTAL).grid(
            row=8, column=0, columnspan=2, sticky=tk.EW, pady=8)
        ttk.Button(left, text="Preview Plot",
                   command=self._preview_plot).grid(
            row=9, column=0, columnspan=2, sticky=tk.EW, pady=2)
        ttk.Button(left, text="Save as PNG (300 dpi)…",
                   command=self._save_custom_plot).grid(
            row=10, column=0, columnspan=2, sticky=tk.EW, pady=2)

        right = ttk.Frame(self.custom_frame)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.custom_fig = Figure(figsize=(8, 5), dpi=100)
        self.custom_ax = self.custom_fig.add_subplot(111)
        self.custom_canvas = FigureCanvasTkAgg(self.custom_fig, master=right)
        self.custom_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        toolbar = NavigationToolbar2Tk(self.custom_canvas, right)
        toolbar.update()
        toolbar.pack(fill=tk.X)

    def _toggle_cycle_entry(self):
        if self.cycle_mode.get() == "selected":
            self.cycle_entry.config(state="normal")
            if self.cycle_entry.get().startswith("e.g."):
                self.cycle_entry.delete(0, tk.END)
        else:
            self.cycle_entry.config(state="disabled")

    def _populate_custom_tab(self):
        if self.df is None:
            return
        numeric_cols = [c for c in self.df.columns
                        if self.df[c].dtype.kind in ("i", "f")]
        self.custom_x["values"] = numeric_cols
        self.custom_y["values"] = numeric_cols
        if "relative_time_s" in numeric_cols:
            self.custom_x.set("relative_time_s")
        elif numeric_cols:
            self.custom_x.set(numeric_cols[0])
        if "voltage_V" in numeric_cols:
            self.custom_y.set("voltage_V")
        elif len(numeric_cols) > 1:
            self.custom_y.set(numeric_cols[1])

    def _get_filtered_df(self):
        if self.df is None:
            return pd.DataFrame()
        selected = parse_cycle_selection(
            self.cycle_entry.get() if self.cycle_mode.get() == "selected"
            else "")
        if selected is None or "cycle" not in self.df.columns:
            return self.df
        return self.df[self.df["cycle"].isin(selected)]

    def _draw_custom_plot(self):
        self.custom_ax.clear()
        x_col = self.custom_x.get()
        y_col = self.custom_y.get()
        if not x_col or not y_col:
            messagebox.showwarning("Select Axes",
                                   "Pick both X and Y columns.")
            return False
        plot_df = self._get_filtered_df()
        if plot_df.empty:
            messagebox.showwarning("No Data",
                                   "No data matches the current selection.")
            return False

        style = self.custom_style.get()
        color_by = self.custom_color_status.get()
        time_factor, time_label = TIME_UNITS.get(
            self.custom_time_unit.get(), (1.0, "s"))
        plot_df = plot_df.copy()
        for col in (x_col, y_col):
            if col in TIME_COLUMNS and col in plot_df.columns:
                plot_df[col] = plot_df[col] * time_factor

        if color_by and "status" in plot_df.columns:
            for status, group in plot_df.groupby("status"):
                color = STATUS_COLORS.get(status, "#333333")
                if style == "line":
                    self.custom_ax.plot(group[x_col], group[y_col],
                                        linewidth=0.8, alpha=0.8,
                                        color=color, label=status)
                else:
                    self.custom_ax.scatter(group[x_col], group[y_col],
                                           s=2, alpha=0.5, color=color,
                                           label=status)
            self.custom_ax.legend(loc="best")
        else:
            if style == "line":
                self.custom_ax.plot(plot_df[x_col], plot_df[y_col],
                                    linewidth=0.8, alpha=0.8,
                                    color="#1f77b4")
            else:
                self.custom_ax.scatter(plot_df[x_col], plot_df[y_col],
                                       s=2, alpha=0.5, color="#1f77b4")

        selected = parse_cycle_selection(
            self.cycle_entry.get() if self.cycle_mode.get() == "selected"
            else "")
        title_suffix = ""
        if selected:
            if len(selected) <= 5:
                title_suffix = ("  (Cycles "
                                f"{', '.join(str(c) for c in selected)})")
            else:
                title_suffix = (f"  (Cycles {selected[0]}–{selected[-1]}, "
                                f"n={len(selected)})")
        x_label = axis_label(x_col, time_label)
        y_label = axis_label(y_col, time_label)
        self.custom_ax.set_xlabel(x_label)
        self.custom_ax.set_ylabel(y_label)
        self.custom_ax.set_title(f"{y_label} vs {x_label}{title_suffix}")
        self.custom_ax.grid(True, alpha=0.3)
        self.custom_fig.tight_layout()
        self.custom_canvas.draw()
        return True

    def _preview_plot(self):
        if self.df is None:
            messagebox.showwarning("No Data", "Load a file first.")
            return
        self._draw_custom_plot()

    def _save_custom_plot(self):
        if self.df is None:
            messagebox.showwarning("No Data", "Load a file first.")
            return
        if not self._draw_custom_plot():
            return
        path = filedialog.asksaveasfilename(
            title="Save Plot", defaultextension=".png",
            initialfile="custom_plot.png",
            filetypes=[("PNG files", "*.png"), ("All files", "*.*")])
        if not path:
            return
        self.custom_fig.savefig(path, dpi=300)
        messagebox.showinfo("Saved", f"Plot saved at 300 dpi:\n{path}")
