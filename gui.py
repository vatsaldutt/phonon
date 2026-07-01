#!/usr/bin/env python3
"""
gui.py — Ceiling Fan Control Panel

Run:
    python gui.py

Requires the HA_TOKEN environment variable (and optionally FAN_ENTITY /
LIGHT_ENTITY / HA_URL) to be set before launching.  See SETUP.md.
"""

import sys
import threading
import tkinter as tk
from tkinter import ttk

import fan_api

# ── platform font ─────────────────────────────────────────────────────────────
if sys.platform == "darwin":
    UI_FONT = "Helvetica Neue"
elif sys.platform == "win32":
    UI_FONT = "Segoe UI"
else:
    UI_FONT = "DejaVu Sans"

def F(size: int, weight: str = "normal") -> tuple:
    return (UI_FONT, size, weight)

# ── palette ───────────────────────────────────────────────────────────────────
BG       = "#0f1117"   # app background
CARD     = "#1a1d27"   # card background
BORDER   = "#2d3148"   # subtle card border / inactive button
TEXT     = "#e8eaf0"   # primary text
DIM      = "#565878"   # secondary / disabled text
ACCENT   = "#4f7ef8"   # blue highlight
GREEN    = "#22c980"   # "on" state
RED_DIM  = "#c0392b"   # unused currently, kept for future
WARM_HEX = "#ffc04d"   # warm white swatch anchor
COOL_HEX = "#c5e8ff"   # cool white swatch anchor

WARM_K   = 2700
COOL_K   = 6500
POLL_MS  = 4_000       # poll HA state every 4 s


# ── helpers ───────────────────────────────────────────────────────────────────

def kelvin_to_hex(k: int) -> str:
    """Interpolate between warm amber and cool blue-white for a visual swatch."""
    t = max(0.0, min(1.0, (k - WARM_K) / (COOL_K - WARM_K)))
    r = int(0xff + (0xc5 - 0xff) * t)
    g = int(0xc0 + (0xe8 - 0xc0) * t)
    b = int(0x4d + (0xff - 0x4d) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def run_in_thread(fn, *args, **kwargs) -> None:
    threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True).start()


# ── main application ──────────────────────────────────────────────────────────

class FanControlApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Ceiling Fan Control")
        self.configure(bg=BG)
        self.resizable(False, False)

        self._build_styles()
        self._build_ui()

        # kick off first fetch after the window appears
        self.after(200, self._refresh)
        self.after(POLL_MS, self._poll_loop)

    # ── ttk style ─────────────────────────────────────────────────────────────

    def _build_styles(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(
            "Dark.TScale",
            background=CARD,
            troughcolor=BORDER,
            sliderlength=22,
            sliderrelief="flat",
            borderwidth=0,
        )
        s.map("Dark.TScale",
              background=[("active", CARD)],
              troughcolor=[("active", BORDER)])

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        # ── header ────────────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=BG)
        hdr.pack(fill="x", padx=22, pady=(22, 6))

        tk.Label(hdr, text="Ceiling Fan", bg=BG, fg=TEXT,
                 font=F(18, "bold")).pack(side="left")

        self._status_dot = tk.Label(hdr, text="●", bg=BG, fg=DIM, font=F(14))
        self._status_dot.pack(side="right")
        self._status_lbl = tk.Label(hdr, text="connecting…", bg=BG, fg=DIM,
                                     font=F(11))
        self._status_lbl.pack(side="right", padx=(0, 6))

        # ── separator ─────────────────────────────────────────────────────────
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=22, pady=(0, 10))

        # ── FAN CARD ──────────────────────────────────────────────────────────
        fan_card = self._card("FAN")

        # on/off buttons
        btn_row = tk.Frame(fan_card, bg=CARD)
        btn_row.pack(fill="x", pady=(10, 0))
        self._fan_on_btn  = self._power_btn(btn_row, "  On  ", self._cmd_fan_on)
        self._fan_off_btn = self._power_btn(btn_row, "  Off  ", self._cmd_fan_off)

        # speed slider
        self._spd_val  = self._slider_row(fan_card, "Fan Speed", suffix="%")
        self._spd_var  = tk.DoubleVar(value=50)
        self._spd_slider = ttk.Scale(
            fan_card, from_=0, to=100, orient="horizontal",
            variable=self._spd_var,
            command=self._on_spd_move,
        )
        self._spd_slider.pack(fill="x", pady=(4, 6))

        # ── LIGHT CARD ────────────────────────────────────────────────────────
        lt_card = self._card("LIGHT")

        btn_row2 = tk.Frame(lt_card, bg=CARD)
        btn_row2.pack(fill="x", pady=(10, 0))
        self._lt_on_btn  = self._power_btn(btn_row2, "  On  ", self._cmd_lt_on)
        self._lt_off_btn = self._power_btn(btn_row2, "  Off  ", self._cmd_lt_off)

        # brightness
        self._bri_val = self._slider_row(lt_card, "Brightness", suffix="%")
        self._bri_var = tk.DoubleVar(value=100)
        ttk.Scale(
            lt_card, from_=1, to=100, orient="horizontal",
            variable=self._bri_var,
            command=self._on_bri_move,
        ).pack(fill="x", pady=(4, 6))

        # colour temperature
        self._ct_val = self._slider_row(lt_card, "Color Temp", suffix=" K")

        ct_labels = tk.Frame(lt_card, bg=CARD)
        ct_labels.pack(fill="x")
        tk.Label(ct_labels, text="● Warm", bg=CARD, fg=WARM_HEX,
                 font=F(10)).pack(side="left")
        tk.Label(ct_labels, text="Cool ●", bg=CARD, fg=COOL_HEX,
                 font=F(10)).pack(side="right")

        self._ct_var = tk.DoubleVar(value=WARM_K)
        ttk.Scale(
            lt_card, from_=WARM_K, to=COOL_K, orient="horizontal",
            variable=self._ct_var,
            command=self._on_ct_move,
        ).pack(fill="x", pady=(3, 6))

        # colour swatch — shows a rough visual of the current colour temp
        self._swatch = tk.Frame(lt_card, bg=kelvin_to_hex(WARM_K), height=10)
        self._swatch.pack(fill="x", pady=(0, 4))

        # ── footer ────────────────────────────────────────────────────────────
        foot = tk.Frame(self, bg=BG)
        foot.pack(fill="x", padx=22, pady=(4, 20))
        tk.Button(
            foot, text="↻  Refresh", command=self._refresh,
            bg=BORDER, fg=TEXT, activebackground=ACCENT, activeforeground=TEXT,
            relief="flat", bd=0, font=F(11), cursor="hand2",
            padx=16, pady=7,
        ).pack(side="right")

        self._err_lbl = tk.Label(foot, text="", bg=BG, fg="#e74c3c", font=F(10))
        self._err_lbl.pack(side="left")

    # ── widget helpers ────────────────────────────────────────────────────────

    def _card(self, title: str) -> tk.Frame:
        """Return the inner Frame of a titled card widget."""
        wrapper = tk.Frame(self, bg=BORDER)
        wrapper.pack(fill="x", padx=22, pady=6)
        inner = tk.Frame(wrapper, bg=CARD, padx=16, pady=14)
        inner.pack(fill="both", padx=1, pady=1)
        tk.Label(inner, text=title, bg=CARD, fg=ACCENT,
                 font=F(10, "bold")).pack(anchor="w")
        return inner

    def _power_btn(self, parent: tk.Frame, label: str, cmd) -> tk.Button:
        btn = tk.Button(
            parent, text=label, command=cmd,
            bg=BORDER, fg=TEXT, activebackground=ACCENT, activeforeground=TEXT,
            relief="flat", bd=0, font=F(12), cursor="hand2", pady=7,
        )
        btn.pack(side="left", padx=(0, 10))
        return btn

    def _slider_row(self, parent: tk.Frame, label: str, suffix: str = "") -> tk.Label:
        """Row with a label on the left and a live value Label on the right.
        Returns the value Label so the caller can update it."""
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x", pady=(14, 0))
        tk.Label(row, text=label, bg=CARD, fg=DIM, font=F(11)).pack(side="left")
        val = tk.Label(row, text="—", bg=CARD, fg=TEXT,
                       font=F(11, "bold"), width=7, anchor="e")
        val.pack(side="right")
        return val

    # ── debounced slider callbacks ─────────────────────────────────────────────
    # We update the label immediately but wait 400 ms of stillness before
    # sending to HA so we don't flood it while the user drags.

    _spd_job: str | None = None
    _bri_job: str | None = None
    _ct_job:  str | None = None

    def _on_spd_move(self, _=None):
        v = int(self._spd_var.get())
        self._spd_val.config(text=f"{v}%")
        if self._spd_job:
            self.after_cancel(self._spd_job)
        self._spd_job = self.after(400, lambda: run_in_thread(fan_api.set_speed, v))

    def _on_bri_move(self, _=None):
        v = int(self._bri_var.get())
        self._bri_val.config(text=f"{v}%")
        if self._bri_job:
            self.after_cancel(self._bri_job)
        self._bri_job = self.after(400, lambda: run_in_thread(fan_api.light_on, brightness_pct=v))

    def _on_ct_move(self, _=None):
        v = int(self._ct_var.get())
        self._ct_val.config(text=f"{v} K")
        self._swatch.config(bg=kelvin_to_hex(v))
        if self._ct_job:
            self.after_cancel(self._ct_job)
        self._ct_job = self.after(400, lambda: run_in_thread(fan_api.light_on, color_temp_kelvin=v))

    # ── button commands ───────────────────────────────────────────────────────

    def _cmd_fan_on(self):   run_in_thread(fan_api.turn_on)
    def _cmd_fan_off(self):  run_in_thread(fan_api.turn_off)
    def _cmd_lt_on(self):    run_in_thread(fan_api.light_on)
    def _cmd_lt_off(self):   run_in_thread(fan_api.light_off)

    # ── state polling ─────────────────────────────────────────────────────────

    def _poll_loop(self):
        self._refresh()
        self.after(POLL_MS, self._poll_loop)

    def _refresh(self):
        def _fetch():
            try:
                fs = fan_api.fan_state()
                ls = fan_api.light_state()
                self.after(0, self._apply_state, fs, ls, None)
            except Exception as exc:   # noqa: BLE001
                self.after(0, self._apply_state, None, None, exc)

        run_in_thread(_fetch)

    def _apply_state(self, fs, ls, err):
        if err:
            self._status_dot.config(fg="#e74c3c")
            self._status_lbl.config(text="error", fg="#e74c3c")
            self._err_lbl.config(text=f"⚠  {err}")
            return

        self._err_lbl.config(text="")
        self._status_dot.config(fg=GREEN)
        self._status_lbl.config(text="connected", fg=DIM)

        # ── fan state ─────────────────────────────────────────────────────────
        f_on = fs.get("state") == "on"
        pct  = int((fs.get("attributes") or {}).get("percentage") or 0)
        self._fan_on_btn.config(bg=GREEN   if f_on else BORDER,
                                 fg=BG     if f_on else TEXT)
        self._fan_off_btn.config(bg=BORDER if f_on else "#4a2030",
                                  fg=TEXT)
        # only update sliders when the user isn't dragging (no pending job)
        if self._spd_job is None:
            self._spd_var.set(pct)
            self._spd_val.config(text=f"{pct}%")

        # ── light state ───────────────────────────────────────────────────────
        l_on  = ls.get("state") == "on"
        attrs = ls.get("attributes") or {}
        self._lt_on_btn.config(bg=GREEN   if l_on else BORDER,
                                fg=BG     if l_on else TEXT)
        self._lt_off_btn.config(bg=BORDER if l_on else "#4a2030",
                                 fg=TEXT)

        bri_raw = attrs.get("brightness")   # 0–255 from HA
        ct_k    = attrs.get("color_temp_kelvin")

        if bri_raw is not None and self._bri_job is None:
            bri_pct = max(1, round(bri_raw / 255 * 100))
            self._bri_var.set(bri_pct)
            self._bri_val.config(text=f"{bri_pct}%")

        if ct_k is not None and self._ct_job is None:
            k = max(WARM_K, min(COOL_K, int(ct_k)))
            self._ct_var.set(k)
            self._ct_val.config(text=f"{k} K")
            self._swatch.config(bg=kelvin_to_hex(k))


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # import os
    # if "HA_TOKEN" not in os.environ:
    #     print(
    #         "\nERROR: HA_TOKEN environment variable is not set.\n"
    #         "Export it before running:\n\n"
    #         "  export HA_TOKEN='your_token_here'\n"
    #     )
    #     sys.exit(1)

    app = FanControlApp()
    app.mainloop()
