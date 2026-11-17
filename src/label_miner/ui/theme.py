"""Look and feel shared by the windows, plus window-level helpers."""

import contextlib
import os
import sys
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import font as tkfont
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


def apply_theme(root, appearance="dark"):
    """Apply the sv-ttk sleek theme, dark or light (``appearance``), if it's
    installed, and set a baseline font/spacing that looks reasonable even
    without it. Returns True if sv-ttk was actually applied, so the caller
    can hint at the missing dependency instead of leaving the old look
    unexplained. Can be called again to switch between dark and light."""
    sv_ttk_applied = False
    if sv_ttk is not None:
        sv_ttk.set_theme(appearance)
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


def switch_style():
    """Style name for an on/off setting: sv-ttk's toggle switch, or a plain
    checkbox without sv-ttk."""
    return "Switch.TCheckbutton" if sv_ttk is not None else "TCheckbutton"


def icon_glyph(widget, fluent_code, fallback, size=15):
    """(text, font) for a Windows icon: the Segoe Fluent Icons glyph
    (Windows 11), the same code in Segoe MDL2 Assets (Windows 10), or the
    fallback character."""
    families = set(tkfont.families(widget))
    family = next((name for name in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if name in families), None)
    if family:
        return fluent_code, (family, size)
    return fallback, ("Segoe UI Symbol", size + 2)


# sv-ttk's colours (-bg, -fg in its theme files): its themes don't report
# them through ttk.Style.lookup
SUN_VALLEY_COLORS = {"sun-valley-dark": ("#1c1c1c", "#fafafa"), "sun-valley-light": ("#fafafa", "#1c1c1c")}


def theme_colors(widget):
    """(background, text) of the active theme, as "#rrggbb" - for the plain
    Tk widgets (icons, cards, the photo's backdrop) that don't follow the
    theme by themselves."""
    style = ttk.Style(widget)
    if style.theme_use() in SUN_VALLEY_COLORS:
        return SUN_VALLEY_COLORS[style.theme_use()]
    background = resolve_tk_color(widget, style.lookup("TFrame", "background") or "#1c1c1c")
    text = resolve_tk_color(widget, style.lookup("TLabel", "foreground") or "#ffffff", "#ffffff")
    return background, text


class Tooltip:
    """A small box of text shown while the mouse rests on a widget, or right
    away when it is clicked."""

    DELAY = 300  # ms
    WIDTH = 340  # px the text wraps at

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        self.pending = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<Button-1>", self.show, add="+")

    def _schedule(self, event=None):
        self._cancel()
        self.pending = self.widget.after(self.DELAY, self.show)

    def _cancel(self):
        if self.pending is not None:
            self.widget.after_cancel(self.pending)
            self.pending = None

    def show(self, event=None):
        self._cancel()
        if self.tip is not None:
            return
        background, text_color = theme_colors(self.widget)
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.attributes("-topmost", True)
        border = tk.Frame(tip, background=shade_color(background, 0.3), padx=1, pady=1)
        border.pack()
        tk.Label(
            border,
            text=self.text,
            font=DEFAULT_FONT,
            background=shade_color(background, 0.1),
            foreground=text_color,
            justify="left",
            wraplength=self.WIDTH,
            padx=12,
            pady=10,
        ).pack()
        tip.update_idletasks()
        # Right of the widget, or left of it when that would leave the screen
        x = self.widget.winfo_rootx() + self.widget.winfo_width() + 8
        if x + tip.winfo_width() > self.widget.winfo_screenwidth():
            x = self.widget.winfo_rootx() - tip.winfo_width() - 8
        y = self.widget.winfo_rooty() - 4
        tip.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.tip = tip

    def hide(self, event=None):
        self._cancel()
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None
