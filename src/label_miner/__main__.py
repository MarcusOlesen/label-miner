"""Entry point: ``python -m label_miner [photos or a zip] [--debug]``.

Photos or a zip given on the command line (for example dropped onto the
desktop icon) are processed as soon as the OCR models have loaded.
"""

import tkinter as tk

from .ui.app import LabelMinerApp
from .ui.theme import apply_theme, install_error_reporting

try:
    from tkinterdnd2 import TkinterDnD
except ImportError:  # optional: drag and drop
    TkinterDnD = None


def main() -> None:
    root = TkinterDnD.Tk() if TkinterDnD is not None else tk.Tk()
    install_error_reporting(root)
    theme_applied = apply_theme(root)
    app = LabelMinerApp(root, theme_applied=theme_applied)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
