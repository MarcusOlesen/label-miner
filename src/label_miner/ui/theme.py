"""Look and feel shared by the windows, plus window-level helpers."""

import contextlib
import os
import sys
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import messagebox, ttk

try:
    import sv_ttk
except ImportError:  # optional: the dark theme
    sv_ttk = None

from ..config import DEFAULT_FONT, DEFAULT_FONT_FAMILY, MANUAL_NAME
from ..util import REPO_ROOT, debug_log, resource_path


def open_manual(parent=None):
    """Open the PDF manual: next to the .exe when installed, in build/ (made
    by docs/manual/make_manual.py) when run from source."""
    for folder in (Path(sys.executable).parent, REPO_ROOT / "build"):
        manual = folder / MANUAL_NAME
        if manual.exists():
            os.startfile(manual)
            return
    messagebox.showinfo("Label Miner", f"Vejledningen ({MANUAL_NAME}) blev ikke fundet.", parent=parent)


def set_window_icon(window):
    icon_path = resource_path("label_miner.ico")
    if not icon_path.exists():
        return
    with contextlib.suppress(tk.TclError):  # .ico icons aren't supported everywhere (e.g. Linux)
        window.iconbitmap(str(icon_path))


def resolve_tk_color(widget, color, fallback="#202020"):
    """Resolve a Tk color to a "#rrggbb" string that PIL can actually use
    (e.g. as a rotate() fillcolor). Tk themes - especially Windows' native
    "vista" theme, which is what you get without sv-ttk installed - often
    report a widget's background as a *symbolic* system color name like
    "SystemButtonFace" rather than a hex value. Tk itself understands
    those, but PIL does not, and PIL.Image.rotate(fillcolor=...) raises on
    one instead of falling back to anything. Because that raise happened
    inside a slider callback, it silently aborted the redraw before the
    image ever updated - which looked like "rotation does nothing" - and
    left the rotation angle set, so *every* later redraw (mirror, crop,
    reset) hit the same crash. Going through widget.winfo_rgb() resolves
    any Tk color name (symbolic or not) to real RGB first, so this can
    never hand PIL something it can't parse."""
    try:
        r, g, b = widget.winfo_rgb(color)
        return f"#{r >> 8:02x}{g >> 8:02x}{b >> 8:02x}"
    except tk.TclError:
        return fallback


def apply_theme(root):
    """Apply the sv-ttk sleek light/dark theme if it's installed, and set a
    baseline font/spacing that looks reasonable even without it. Returns
    True if sv-ttk was actually applied, so the caller can hint at the
    missing dependency instead of leaving the old look unexplained."""
    sv_ttk_applied = False
    if sv_ttk is not None:
        sv_ttk.set_theme("dark")
        sv_ttk_applied = True

    # Stock ttk (no theme package installed) defaults to a small system
    # font that reads as dated - set a consistent, larger one regardless
    # of whether sv-ttk is available, so the app never looks like this
    # step silently did nothing.
    root.option_add("*Font", DEFAULT_FONT)
    style = ttk.Style(root)
    style.configure(".", font=DEFAULT_FONT)
    style.configure("Treeview", font=DEFAULT_FONT, rowheight=28)
    style.configure("Treeview.Heading", font=(DEFAULT_FONT_FAMILY, 10, "bold"))
    return sv_ttk_applied


def shade_color(hex_color, amount):
    """A "#rrggbb" color moved `amount` (0-1) towards white on a dark
    color, or towards black on a light one - for card backgrounds and
    borders that stand out slightly from the theme's own background."""
    red, green, blue = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    target = 255 if (red + green + blue) / 3 < 128 else 0
    return "#" + "".join(
        f"{round(channel + (target - channel) * amount):02x}" for channel in (red, green, blue)
    )


def accent_button_style():
    """Style name for the app's one 'primary action' button. sv-ttk ships
    an Accent.TButton variant; without sv-ttk installed we fall back to a
    plain button rather than referencing a style that doesn't exist."""
    return "Accent.TButton" if sv_ttk is not None else "TButton"


def install_error_reporting(root):
    """Show errors from Tk callbacks in a dialog.

    Tkinter only prints a traceback when a callback (a button, a slider, a
    canvas event) raises - which nobody sees in the windowed .exe, where
    there is no console. The button would just seem to do nothing, and the
    window could be left half-updated. The error is also written to the
    debug log.
    """

    def report(exc, value, tb):
        debug_log("".join(traceback.format_exception(exc, value, tb)))
        with contextlib.suppress(Exception):  # the dialog itself must never raise
            messagebox.showerror(
                "Uventet fejl",
                f"Der opstod en uventet fejl:\n\n{value}",
                parent=root,
            )

    root.report_callback_exception = report
