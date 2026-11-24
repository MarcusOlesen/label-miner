"""The main window: loading, the batch of photos, the result list and export."""

import json
import os
import queue
import shutil
import sys
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import requests
from requests.adapters import HTTPAdapter

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:  # optional: drag and drop
    DND_FILES = None
    TkinterDnD = None
from ..batch import (
    clean_up_old_extract_dirs,
    derive_source_folder,
    excel_rows,
    export_directory,
    format_duration,
    load_images_from_zip,
    unique_export_path,
)
from ..choices import load_choices, remember_choice, save_choices
from ..config import (
    APP_VERSION,
    COLOR_ERROR,
    COLOR_MUTED,
    COLOR_OK,
    COLOR_WARNING,
    COPY_COLUMNS,
    DEFAULT_FONT_FAMILY,
    EXCEL_COLUMNS,
    OCR_WORKERS,
    SUPPORTED_EXTENSIONS,
)
from ..ocr import create_ocr_engine, remove_temporary_files, save_temporary_png
from ..pipeline import extract_label
from ..records import Record
from ..review import warning_messages
from ..settings import load_settings, save_settings
from ..shop import prefetch_catalogue
from ..util import debug_log
from .review_window import ReviewWindow
from .theme import (
    Tooltip,
    accent_button_style,
    apply_theme,
    icon_glyph,
    open_manual,
    set_window_icon,
    switch_style,
    theme_colors,
)


class LabelMinerApp:
    def __init__(self, root, theme_applied=True):
        self.root = root
        self.root.title(f"Label Miner {APP_VERSION}")
        self._center_window(860, 600)
        self.root.minsize(700, 480)
        set_window_icon(self.root)

        self.theme_applied = theme_applied
        self.files = []
        self.records = []
        self.ocr_engine = None
        self.http_session = requests.Session()
        # Room for every OCR worker's webshop requests at once; requests'
        # default pool of 10 connections would quietly make them wait.
        adapter = HTTPAdapter(pool_maxsize=max(OCR_WORKERS * 2, 10))
        self.http_session.mount("https://", adapter)
        self.http_session.mount("http://", adapter)
        self.price_cache = {}
        self.price_cache_lock = threading.Lock()
        # Product photos for the review window's suggestion cards, by URL
        self.thumbnail_cache = {}
        self.loading = True
        self.processing = False
        self.source_folder_name = ""
        self.export_dir = None
        self.exported = False
        self.export_path = None
        self.temp_extract_dirs = []
        self.review_queue = queue.Queue()
        self.review_window = None
        # Tracks standalone editors opened via reopen_record_review, keyed
        # by id(record) - lets a second double-click on the same row (e.g.
        # while a "Prøv igen" retry it just kicked off is still running in
        # the background, see _retry()/_retry_finished) bring the existing
        # editor forward instead of opening a second one that could race
        # the first (or the in-flight retry thread) writing to the same
        # record dict.
        self.open_standalone_windows = {}
        self.review_total = 0
        self.review_completed = 0
        self.work_total = 0
        self.work_completed = 0
        self.work_durations = []
        self.progress_info = tk.StringVar(value="")

        self.status = tk.StringVar(value="Indlæser OCR-modellerne...")
        self.selected_count = tk.StringVar(value="Valgte billeder: 0")
        # Kept between runs (see settings.py and choices.py)
        self.settings = load_settings()
        self.choices = load_choices()
        # The settings window's switches (see settings.DEFAULTS, _save_settings)
        self.stk_from_antal = tk.BooleanVar(value=self.settings["stk_from_antal"])
        self.shop_lookup = tk.BooleanVar(value=self.settings["shop_lookup"])
        self.save_excel = tk.BooleanVar(value=self.settings["save_excel"])
        self.export_folder = tk.StringVar(value=self.settings["export_folder"])
        self.open_excel = tk.BooleanVar(value=self.settings["open_excel"])
        self.remember_choices = tk.BooleanVar(value=self.settings["remember_choices"])
        self.dark_appearance = tk.BooleanVar(value=self.settings["appearance"] == "dark")
        # The plain Tk icons, recoloured when the look changes (see _icon)
        self.icons = []

        # The window shows one of three "screens" at a time: a boot screen
        # while the OCR models load, a select screen with one big button,
        # and the work screen (results, export) while photos are processed -
        # rather than one crowded window with unexplained disabled controls.
        self.screen_container = ttk.Frame(root, padding=24)
        self.screen_container.pack(fill="both", expand=True)

        self._build_boot_screen()
        self._build_select_screen()
        self._build_work_screen()
        self.boot_frame.pack(fill="both", expand=True)

        self.root.after(100, self._check_review_queue)
        self.boot_progress.start(50)
        threading.Thread(target=self.load_ocr_engine, daemon=True).start()
        threading.Thread(target=self._clean_up_old_extract_dirs, daemon=True).start()

    def _clean_up_old_extract_dirs(self):
        try:
            for folder in clean_up_old_extract_dirs():
                debug_log(f"Removed leftover temp folder {folder}")
        except Exception as error:
            debug_log(f"Cleaning up old temp folders failed: {error!r}")

    def _center_window(self, width, height):
        self.root.update_idletasks()
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = max((screen_width - width) // 2, 0)
        y = max((screen_height - height) // 2, 0)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _build_boot_screen(self):
        self.boot_frame = ttk.Frame(self.screen_container)
        center = ttk.Frame(self.boot_frame)
        center.pack(expand=True)

        ttk.Label(center, text="Label Miner", font=("Segoe UI", 22, "bold")).pack()
        ttk.Label(
            center,
            text="Klargør OCR-motoren...",
            foreground=COLOR_MUTED,
        ).pack(pady=(4, 24))

        if not self.theme_applied:
            ttk.Label(
                center,
                text=(
                    'Tip: kør "pip install sv-ttk" og genstart for det '
                    "moderne udseende - det er ikke installeret lige nu."
                ),
                foreground=COLOR_WARNING,
                wraplength=380,
                justify="center",
            ).pack(pady=(0, 16))

        self.boot_progress = ttk.Progressbar(center, mode="indeterminate", length=280)
        self.boot_progress.pack()
        ttk.Label(center, textvariable=self.status, foreground=COLOR_MUTED).pack(pady=(12, 0))

        # Only shown on a load failure (see _loading_failed/_retry_load_ocr_engine)
        # so a broken OCR install doesn't leave the user stuck staring at
        # a spinner that will never finish.
        self.boot_retry_button = ttk.Button(center, text="Prøv igen", command=self._retry_load_ocr_engine)

    def _build_select_screen(self):
        self.select_frame = ttk.Frame(self.screen_container)
        center = ttk.Frame(self.select_frame)
        center.pack(expand=True)

        ttk.Label(center, text="Label Miner", font=("Segoe UI", 22, "bold")).pack()
        ttk.Label(
            center,
            text="Vælg label-billeder eller en ZIP-mappe, og få dem læst automatisk.",
            foreground=COLOR_MUTED,
        ).pack(pady=(4, 24))

        self._configure_styles()
        self.select_button = ttk.Button(
            center,
            text="📂  Vælg billeder eller ZIP",
            command=self._select_images,
            style="Big." + accent_button_style(),
        )
        self.select_button.pack()
        ttk.Button(self.select_frame, text="?  Hjælp", command=lambda: open_manual(self.root)).place(
            relx=1.0, rely=0.0, anchor="ne"
        )
        # The settings, chosen before a batch starts: only on this screen, as
        # the export is made from them (see open_settings)
        self.settings_button = self._settings_icon(self.select_frame)
        self.settings_button.place(relx=0.0, rely=0.0, anchor="nw")
        self.settings_frame = None

        drop_targets = [self.select_frame, center, self.select_button]
        if DND_FILES is not None:
            hint = ttk.Label(
                center,
                text="...eller træk billeder eller en ZIP-fil ind i vinduet",
                foreground=COLOR_MUTED,
            )
            hint.pack(pady=(12, 0))
            drop_targets.append(hint)
            for widget in drop_targets:
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._handle_dropped_files)
        else:
            ttk.Label(
                center,
                text=(
                    'Tip: kør "pip install tkinterdnd2" og genstart for at '
                    "kunne trække billeder ind i vinduet."
                ),
                foreground=COLOR_WARNING,
                wraplength=380,
                justify="center",
            ).pack(pady=(12, 0))

    # What the settings do, behind the ? next to them
    HELP = {
        "stk_from_antal": (
            "De nye labels har feltet Antal: hvor mange stk. labelen gælder for.\n\n"
            "Slået til: hvert billede tæller som labelens Antal. Et billede af en label med "
            "»Antal: 4« giver STK 4 – tag ét billede pr. label.\n\n"
            "Slået fra: hvert billede tæller som 1 stk. – tag ét billede pr. vare.\n\n"
            "Labels uden Antal (det gamle design) tæller altid som 1."
        ),
        "save_excel": (
            "Når alle labels er gennemgået, gemmes resultatet som en Excel-fil, og filen gemmes "
            "igen, hver gang du retter en label bagefter.\n\n"
            "Slået fra: der gemmes ingen fil. Brug »Kopiér resultat«, og sæt rækkerne ind i "
            "Google Sheets."
        ),
        "shop_lookup": (
            "Mens billederne læses, finder programmet hver labels vare på hjemmesiden og "
            "udfylder pris og link.\n\n"
            "Slået fra: hjemmesiden bruges ikke. Labels læses kun, uden pris, og kommer kun til "
            "gennemgang, hvis noget på selve labelen mangler. I gennemgangen kan du stadig trykke "
            "»Søg på hjemmesiden igen«."
        ),
        "remember_choices": (
            "Når du vælger en vare fra hjemmesiden til en label, husker programmet valget. Næste "
            "gang en label med samme nummer og navn skal gennemgås, står varen øverst og er valgt "
            "på forhånd – du godkender stadig selv.\n\n"
            "Slået fra: intet huskes, og intet foreslås ud fra tidligere valg."
        ),
    }

    def _icon(self, parent, fluent_code, fallback, size, command=None):
        """A clickable Windows icon with no button around it, lighting up
        under the mouse (see theme.icon_glyph)."""
        text, font = icon_glyph(self.root, fluent_code, fallback, size)
        icon = tk.Label(
            parent,
            text=text,
            font=font,
            background=theme_colors(self.root)[0],
            foreground=COLOR_MUTED,
            cursor="hand2",
            padx=4,
            pady=2,
        )
        if command is not None:
            icon.bind("<Button-1>", lambda event: command(), add="+")
        icon.bind("<Enter>", lambda event: icon.configure(foreground=theme_colors(icon)[1]), add="+")
        icon.bind("<Leave>", lambda event: icon.configure(foreground=COLOR_MUTED), add="+")
        self.icons.append(icon)
        return icon

    def _settings_icon(self, parent):
        """The ⚙ that opens the settings: Windows' own settings icon."""
        return self._icon(parent, "\ue713", "⚙", 15, command=self.open_settings)

    def open_settings(self):
        """The ⚙ button: the settings as a page of the main window, in place
        of the start screen, with a back arrow (or Esc) to return - like the
        settings of a Windows app, not a window of their own. Each setting is
        saved as soon as it is changed (see settings.py). Returns the page."""
        if self.settings_frame is None:
            self._build_settings_page()
        self.select_frame.pack_forget()
        self.settings_frame.pack(fill="both", expand=True)
        self._update_settings_window()
        self.settings_frame.focus_set()
        self.root.bind("<Escape>", lambda event: self.close_settings(), add="+")
        return self.settings_frame

    def close_settings(self):
        """Back from the settings page to the start screen."""
        if self.settings_frame is None or not self.settings_frame.winfo_ismapped():
            return
        self.settings_frame.pack_forget()
        self.select_frame.pack(fill="both", expand=True)

    @property
    def settings_open(self):
        return self.settings_frame is not None and self.settings_frame.winfo_ismapped()

    def _build_settings_page(self):
        page = ttk.Frame(self.screen_container)
        self.settings_frame = page
        header = ttk.Frame(page)
        header.pack(fill="x")
        back = self._icon(header, "\ue72b", "←", 15, command=self.close_settings)
        back.pack(side="left")
        Tooltip(back, "Tilbage (Esc)")
        self.settings_back = back
        ttk.Label(header, text="Indstillinger", font=(DEFAULT_FONT_FAMILY, 18, "bold")).pack(
            side="left", padx=(10, 0)
        )
        # One column of settings, as wide as it needs - centred, rather than
        # stretched across a wide window
        frame = ttk.Frame(page, padding=(0, 6, 0, 0))
        frame.pack(anchor="n", pady=(4, 0))
        frame.columnconfigure(0, minsize=300)
        frame.columnconfigure(2, weight=1, minsize=40)
        self.settings_rows = {}
        self.settings_tooltips = {}
        rows = iter(range(0, 100))

        def section(title):
            ttk.Label(
                frame, text=title.upper(), font=(DEFAULT_FONT_FAMILY, 9, "bold"), foreground=COLOR_MUTED
            ).grid(row=next(rows), column=0, columnspan=4, sticky="w", pady=(16, 2))

        def setting(key, text, variable):
            """One setting: its name, a ? with what it does (see HELP), and a
            switch. Clicking the name works like clicking the switch."""
            row = next(rows)
            name = ttk.Label(frame, text=text, cursor="hand2")
            name.grid(row=row, column=0, sticky="w", pady=5)
            if key in self.HELP:
                help_icon = self._icon(frame, "\ue9ce", "?", 11)
                help_icon.grid(row=row, column=1, sticky="w", padx=(2, 0), pady=5)
                self.settings_tooltips[key] = Tooltip(help_icon, self.HELP[key])
            switch = ttk.Checkbutton(
                frame, variable=variable, command=self._settings_changed, style=switch_style()
            )
            switch.grid(row=row, column=3, sticky="e", pady=5)

            def toggle(event):
                if not switch.instate(["disabled"]):
                    variable.set(not variable.get())
                    self._settings_changed()

            name.bind("<Button-1>", toggle)
            self.settings_rows[key] = (name, switch)

        section("Eksport")
        setting("save_excel", "Gem Excel-filen automatisk", self.save_excel)
        row = next(rows)
        folder_label = ttk.Label(frame, text="Gem i")
        folder_label.grid(row=row, column=0, sticky="w", padx=(18, 0), pady=5)
        folder_buttons = ttk.Frame(frame)
        folder_buttons.grid(row=row, column=1, columnspan=3, sticky="e", pady=5)
        self.folder_text = ttk.Label(folder_buttons, foreground=COLOR_MUTED)
        self.folder_text.pack(side="left", padx=(12, 8))
        self.folder_reset = self._icon(folder_buttons, "\ue711", "×", 10, command=self._reset_export_folder)
        self.folder_reset.pack(side="left", padx=(0, 6))
        Tooltip(self.folder_reset, "Gem ved billederne igen")
        self.folder_button = ttk.Button(
            folder_buttons, text="Vælg mappe…", command=self._choose_export_folder
        )
        self.folder_button.pack(side="left")
        self.settings_rows["export_folder"] = (folder_label, self.folder_button)
        setting("open_excel", "Åbn Excel-filen, når den er gemt", self.open_excel)
        setting("stk_from_antal", "Brug labelens Antal som STK", self.stk_from_antal)

        section("Gennemgang")
        setting("shop_lookup", "Slå varer op på hjemmesiden automatisk", self.shop_lookup)
        setting("remember_choices", "Husk valgte varer", self.remember_choices)
        row = next(rows)
        self.choices_count = ttk.Label(frame, foreground=COLOR_MUTED)
        self.choices_count.grid(row=row, column=0, columnspan=3, sticky="w", padx=(18, 0), pady=5)
        self.forget_button = ttk.Button(frame, text="Glem alle", command=self._forget_choices)
        self.forget_button.grid(row=row, column=3, sticky="e", pady=5)

        section("Udseende")
        setting("appearance", "Mørkt udseende", self.dark_appearance)

    def _update_settings_window(self):
        """Show the settings window's details as the settings are now: the
        chosen folder, how many products are remembered, and which rows
        don't apply (no folder or opening without saving)."""
        folder = self.settings["export_folder"]
        if folder:
            shown = folder if len(folder) <= 34 else "…" + folder[-33:]
            self.folder_text.configure(text=shown)
            self.folder_reset.pack(side="left", padx=(0, 6), before=self.folder_button)
        else:
            self.folder_text.configure(text="Ved billederne")
            self.folder_reset.pack_forget()
        saving = self.settings["save_excel"]
        for key in ("export_folder", "open_excel"):
            name, control = self.settings_rows[key]
            name.configure(foreground="" if saving else COLOR_MUTED)
            control.state(["!disabled"] if saving else ["disabled"])
        count = len(self.choices)
        self.choices_count.configure(
            text=f"{count} {'vare' if count == 1 else 'varer'} husket" if count else "Ingen varer husket"
        )
        self.forget_button.state(["!disabled"] if count else ["disabled"])

    def _settings_changed(self):
        """A setting was changed in the settings window: save it, and show
        the new look right away."""
        dark = self.dark_appearance.get()
        appearance_changed = dark != (self.settings["appearance"] == "dark")
        self._save_settings()
        if appearance_changed:
            self._apply_appearance()
        if self.settings_frame is not None:
            self._update_settings_window()

    def _apply_appearance(self):
        """Switch between the dark and the light look. ttk widgets follow
        the theme by themselves; the plain Tk icons are recoloured here. The
        review window picks its colours up when it opens."""
        apply_theme(self.root, self.settings["appearance"])
        self._configure_styles()
        self.icons = [icon for icon in self.icons if icon.winfo_exists()]
        background = theme_colors(self.root)[0]
        for icon in self.icons:
            icon.configure(background=background)

    def _configure_styles(self):
        """The app's own styles - kept per theme by ttk, so set again after
        the theme changes."""
        ttk.Style(self.root).configure(
            "Big." + accent_button_style(), font=(DEFAULT_FONT_FAMILY, 14, "bold"), padding=(28, 18)
        )

    def _choose_export_folder(self):
        folder = filedialog.askdirectory(parent=self.root, title="Gem Excel-filen i", mustexist=True)
        if folder:
            self.export_folder.set(str(Path(folder)))
            self._settings_changed()

    def _reset_export_folder(self):
        self.export_folder.set("")
        self._settings_changed()

    def _forget_choices(self):
        count = len(self.choices)
        if count and messagebox.askyesno(
            "Glem husket valg?",
            f"Programmet har husket {count} {'vare' if count == 1 else 'varer'} til labels. "
            "Vil du glemme dem alle? Det kan ikke fortrydes.",
            parent=self.root,
        ):
            self.choices.clear()
            save_choices(self.choices)
            self._update_settings_window()

    @property
    def active_choices(self):
        """The remembered choices to suggest from - none while "Husk valgte
        varer" is off. Read by the worker threads, so from the settings dict,
        not a Tk variable."""
        return self.choices if self.settings["remember_choices"] else None

    def remember_choice(self, mk_varenummer, varenavn, candidate):
        """The user approved a label with this webshop product picked: offer
        it first next time (see choices.py) - unless "Husk valgte varer" is
        off. Runs on the main thread; the workers only read the dict."""
        if not self.settings["remember_choices"]:
            return
        if remember_choice(self.choices, mk_varenummer, varenavn, candidate):
            save_choices(self.choices)

    def _save_settings(self):
        self.settings["stk_from_antal"] = self.stk_from_antal.get()
        self.settings["shop_lookup"] = self.shop_lookup.get()
        self.settings["save_excel"] = self.save_excel.get()
        self.settings["export_folder"] = self.export_folder.get()
        self.settings["open_excel"] = self.open_excel.get()
        self.settings["remember_choices"] = self.remember_choices.get()
        self.settings["appearance"] = "dark" if self.dark_appearance.get() else "light"
        save_settings(self.settings)

    def _build_work_screen(self):
        self.work_frame = ttk.Frame(self.screen_container)

        header = ttk.Frame(self.work_frame)
        header.pack(fill="x")
        ttk.Label(header, text="Label Miner", font=("Segoe UI", 18, "bold")).pack(side="left")
        ttk.Button(header, text="?  Hjælp", command=lambda: open_manual(self.root)).pack(side="right")
        ttk.Label(
            header,
            textvariable=self.selected_count,
            font=("Segoe UI", 11, "bold"),
        ).pack(side="right", padx=(0, 16))

        # Progress row (bar + ETA text) is only shown while work is actually
        # happening - built here but not packed until _update_progress_info
        # decides there's something to show, instead of permanently
        # occupying space with an idle "Ingen behandling i gang" message.
        self.progress_frame = ttk.Frame(self.work_frame)
        self.progress = ttk.Progressbar(self.progress_frame, mode="determinate")
        self.progress.pack(fill="x")
        ttk.Label(
            self.progress_frame,
            textvariable=self.progress_info,
            foreground=COLOR_MUTED,
        ).pack(anchor="w", pady=(4, 0))

        # Shown whenever there are labels waiting for review. The review
        # window only opens when the user asks for it, rather than popping
        # up in the middle of something else (see _check_review_queue).
        self.review_bar = ttk.Frame(self.work_frame)
        self.review_button = ttk.Button(
            self.review_bar,
            text="🔍  Gennemse labels",
            command=self.open_review,
            style=accent_button_style(),
        )
        self.review_button.pack(side="left")
        ttk.Label(
            self.review_bar,
            text="Nogle labels skal tjekkes, før de kan eksporteres.",
            foreground=COLOR_WARNING,
        ).pack(side="left", padx=(12, 0))
        self.review_bar_visible = False

        # Result list: one row per processed image (filename / status /
        # short summary) instead of a raw text+JSON dump - the full raw
        # OCR text and parsed JSON are only ever shown where a decision is
        # actually needed (the review window) or via --debug.
        self.results_frame = ttk.Frame(self.work_frame)
        self.results_frame.pack(fill="both", expand=True, pady=(12, 0))
        self.results_frame.columnconfigure(0, weight=1)
        self.results_frame.rowconfigure(0, weight=1)

        self.results_tree = ttk.Treeview(
            self.results_frame,
            columns=("file", "status", "summary"),
            show="headings",
            selectmode="browse",
        )
        self.results_tree.heading("file", text="Fil")
        self.results_tree.heading("status", text="Status")
        self.results_tree.heading("summary", text="Resultat")
        self.results_tree.column("file", width=190, anchor="w", stretch=False)
        self.results_tree.column("status", width=100, anchor="w", stretch=False)
        self.results_tree.column("summary", width=380, anchor="w")
        self.results_tree.grid(row=0, column=0, sticky="nsew")
        results_scroll = ttk.Scrollbar(self.results_frame, orient="vertical", command=self.results_tree.yview)
        results_scroll.grid(row=0, column=1, sticky="ns")
        self.results_tree.configure(yscrollcommand=results_scroll.set)
        self.results_tree.tag_configure("ok", foreground=COLOR_OK)
        self.results_tree.tag_configure("warning", foreground=COLOR_WARNING)
        self.results_tree.tag_configure("error", foreground=COLOR_ERROR)
        # Double-click any already-processed row to reopen it for editing -
        # see reopen_record_review.
        self.results_tree.bind("<Double-1>", self.reopen_record_review)

        # Export/copy live in their own frame, packed but left empty until
        # _enable_export_controls() fills it in - so they don't exist on
        # screen at all (not even greyed out) until there's actually
        # something finished to export, including anything sent through a
        # review-window retry.
        self.actions_frame = ttk.Frame(self.work_frame)
        self.actions_frame.pack(fill="x", pady=(12, 0))
        self.export_ready_label = ttk.Label(
            self.actions_frame,
            text="✅ Færdig — klar til eksport",
            font=(DEFAULT_FONT_FAMILY, 10, "bold"),
            foreground=COLOR_OK,
        )
        self.actions_buttons = ttk.Frame(self.actions_frame)
        self.copy_button = ttk.Button(
            self.actions_buttons,
            text="📋  Kopiér resultat",
            command=self._copy_result,
            style=accent_button_style(),
        )
        self.copy_button.pack(side="left")

        ttk.Separator(self.work_frame, orient="horizontal").pack(fill="x", pady=(12, 6))
        ttk.Label(self.work_frame, textvariable=self.status, foreground=COLOR_MUTED).pack(anchor="w")

    def load_ocr_engine(self):
        """Background thread: load the OCR models (takes a few seconds)."""
        try:
            self.ocr_engine = create_ocr_engine()
        except Exception as error:
            self.root.after(0, self._loading_failed, error)
            return
        self.root.after(0, self.ocr_engine_ready)

    def ocr_engine_ready(self):
        self.loading = False
        self.boot_progress.stop()
        self.boot_frame.pack_forget()
        self.select_frame.pack(fill="both", expand=True)
        self.status.set("Klar. Vælg billeder for at starte.")
        # Photos or a zip dropped onto the program's desktop icon (or
        # opened with it) arrive as command-line arguments - start on them
        # right away, as if they'd been picked in the file dialog
        startup_paths = [arg for arg in sys.argv[1:] if not arg.startswith("--") and Path(arg).exists()]
        if startup_paths:
            self.begin_processing(startup_paths)

    def _loading_failed(self, error):
        self.loading = False
        self.boot_progress.stop()
        self.status.set("OCR-modellerne kunne ikke indlæses.")
        self.boot_retry_button.pack(pady=(16, 0))
        messagebox.showerror(
            "OCR ikke tilgængelig",
            f"Installer OCR med: pip install rapidocr onnxruntime\n\nDetaljer: {error}",
            parent=self.root,
        )

    def _retry_load_ocr_engine(self):
        self.loading = True
        self.boot_retry_button.pack_forget()
        self.status.set("Indlæser OCR-modellerne...")
        self.boot_progress.start(50)
        threading.Thread(target=self.load_ocr_engine, daemon=True).start()

    def _select_images(self):
        image_patterns = [f"*{ext}" for ext in sorted(SUPPORTED_EXTENSIONS)]
        selected = filedialog.askopenfilenames(
            parent=self.root,
            title="Vælg billeder eller en ZIP-fil",
            filetypes=[
                ("Billeder og ZIP", " ".join(image_patterns + ["*.zip"])),
                ("ZIP-fil", "*.zip"),
                ("Billeder", " ".join(image_patterns)),
                ("Alle filer", "*.*"),
            ],
        )
        if not selected:
            return
        self.begin_processing(selected)

    def _handle_dropped_files(self, event):
        self.begin_processing(self.root.tk.splitlist(event.data))

    def begin_processing(self, paths):
        """Entry point shared by the file dialog and drag-and-drop. Only a
        non-empty, actually-supported selection consumes the select screen -
        cancelling the dialog or dropping something unsupported leaves the
        one-shot select button right where it was."""
        paths = [Path(p) for p in paths]
        zip_paths = [path for path in paths if path.suffix.lower() == ".zip"]

        if zip_paths:
            if len(zip_paths) > 1:
                messagebox.showwarning(
                    "Flere ZIP-filer",
                    "Der blev valgt flere ZIP-filer - kun den første bruges.",
                    parent=self.root,
                )
            zip_path = zip_paths[0]
            self.status.set("Udpakker ZIP-fil...")
            self.root.update_idletasks()
            try:
                files, extract_dir = load_images_from_zip(zip_path)
            except Exception as error:
                messagebox.showerror("Kunne ikke åbne ZIP-filen", str(error), parent=self.root)
                return
            self.temp_extract_dirs.append(extract_dir)
            if not files:
                messagebox.showwarning(
                    "Ingen billeder fundet",
                    "ZIP-filen indeholder ingen understøttede billedformater.",
                    parent=self.root,
                )
                return
            folder_name, export_dir = derive_source_folder(
                files, extraction_root=extract_dir, zip_path=zip_path
            )
        else:
            files = [path for path in paths if path.suffix.lower() in SUPPORTED_EXTENSIONS]
            if not files:
                if paths:
                    messagebox.showwarning(
                        "Ingen billeder fundet",
                        "Ingen af filerne er understøttede billedformater.",
                        parent=self.root,
                    )
                return
            folder_name, export_dir = derive_source_folder(files)

        self.files = files
        self.source_folder_name = folder_name
        self.export_dir = export_dir
        self.exported = False
        self.export_path = None
        self.selected_count.set(f"Valgte billeder: {len(self.files)}")
        # The select screen is consumed for good here - this app processes
        # one batch per launch, so the button never needs to come back.
        # Nor do the settings: the batch is made with them as they are now.
        if self.settings_frame is not None:
            self.settings_frame.pack_forget()
        self.select_frame.pack_forget()
        self.work_frame.pack(fill="both", expand=True)
        self._start_processing()

    def _start_processing(self):
        if not self.files or self.ocr_engine is None or self.processing:
            return
        self.processing = True
        self.progress.configure(value=0, maximum=len(self.files))
        self.progress.configure(mode="determinate")
        self.results_tree.delete(*self.results_tree.get_children())
        self.records = []
        self.review_total = 0
        self.review_completed = 0
        self.work_total = len(self.files)
        self.work_completed = 0
        self.work_durations = []
        self._update_progress_info()
        self._start_busy_indicator()
        # The webshop's catalogue is downloaded alongside the first photos'
        # OCR - its own thread, which gets there first and says how it goes
        if self.settings["shop_lookup"]:
            threading.Thread(target=self._prefetch_catalogue, daemon=True).start()
        threading.Thread(target=self._process_in_background, daemon=True).start()

    def _prefetch_catalogue(self):
        """Background thread: download the webshop's products (see
        shop.prefetch_catalogue), showing in the status line how it goes -
        a busy shop can take a minute, and the window must not look frozen
        meanwhile."""

        def report(text):
            self.root.after(0, self.status.set, text)

        if prefetch_catalogue(self.http_session, self.price_cache, self.price_cache_lock, report):
            report("Hjemmesidens varer er hentet.")
        else:
            report("Kunne ikke hente hjemmesidens varer – labels uden pris kommer til gennemgang.")

    def _extract_one(self, path):
        """Runs on a worker thread (see _process_in_background). Exceptions
        are caught here rather than left in the future, so one bad photo
        can't stop the rest of the batch."""
        started_at = time.perf_counter()
        try:
            record = extract_label(
                self.ocr_engine,
                path,
                self.http_session,
                self.price_cache,
                self.price_cache_lock,
                self.active_choices,
                self.settings["shop_lookup"],
                self.settings["stk_from_antal"],
            )
        except Exception as error:
            record = Record(path, warnings=[("error", f"Behandling af billedet fejlede: {error}")])
        duration = time.perf_counter() - started_at
        return record, duration

    @staticmethod
    def _debug_log_record(record, title):
        debug_log(f"\n===== {title} =====")
        debug_log("RAW TEXT")
        debug_log(record.raw_text or "<empty>")
        debug_log("PARSED LABEL")
        fields = {**asdict(record.label), "price": record.price, "url": record.url}
        debug_log(json.dumps(fields, indent=2, ensure_ascii=False))
        for warning in warning_messages(record.warnings):
            debug_log(f"Advarsel: {warning}")

    def _process_in_background(self):
        """Runs up to OCR_WORKERS images through _extract_one at once
        (see that constant's comment for why this is safe/worthwhile).
        This method itself stays the single thread that ever appends to
        self.records / self.review_queue or increments review_total, so
        those aren't touched concurrently even though the OCR/lookup work
        behind each future is. self.records is pre-sized and filled by
        original file position rather than completion order, so a retry
        or the exported Excel still reflect the batch in the order the
        photos were selected, regardless of which finished OCR first."""
        try:
            self.records = [None] * len(self.files)
            completed_count = 0
            worker_count = max(1, min(OCR_WORKERS, len(self.files)))
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = {
                    executor.submit(self._extract_one, path): (position, path)
                    for position, path in enumerate(self.files)
                }
                for future in as_completed(futures):
                    position, path = futures[future]
                    record, duration = future.result()
                    completed_count += 1
                    self._debug_log_record(record, path.name)
                    # True while a record is sitting in review_queue
                    # waiting for (or actively going through) the live
                    # review popup - reopen_record_review refuses to
                    # open a standalone editor for one of these, so a
                    # row can't be resolved twice (once through each
                    # window) and double-count review_completed.
                    record.review_pending = bool(record.warnings)
                    self.records[position] = record
                    if record.warnings:
                        self.review_total += 1
                        self.review_queue.put(record)
                    self.root.after(0, self._show_result, record, completed_count, duration)
        except Exception as error:
            self.root.after(0, self._processing_failed, error)
        else:
            self.root.after(0, self._processing_finished)

    def _show_result(self, record, index, duration):
        if index == 1:
            self._stop_busy_indicator()
        self.upsert_result_row(record)
        self._complete_work_unit(duration)
        self.status.set(f"Behandlet {index} af {len(self.files)} billeder.")

    @staticmethod
    def _status_for_warnings(warnings):
        if not warnings:
            return "OK", "ok"
        if any(category == "error" for category, _ in warnings):
            return "Fejl", "error"
        return "Gennemse", "warning"

    @staticmethod
    def _summary_for_record(record):
        label = record.label
        stand = f"Stand {label.stand}" if label.stand else ""
        parts = [value for value in (label.varenavn, label.mk_varenummer, stand) if value]
        if parts:
            return " · ".join(parts)
        text = record.raw_text.strip()
        if text:
            return (text[:80] + "…") if len(text) > 80 else text
        return "Ingen tekst genkendt"

    def upsert_result_row(self, record):
        """Add (or, on a retry, update in place) this image's row in the
        result list - filename, a status tag driving its color, and a short
        human-readable summary. The full raw OCR text/JSON never appears
        here; it's only shown in the review window or via --debug."""
        status_text, tag = self._status_for_warnings(record.warnings)
        summary = self._summary_for_record(record)
        iid = str(record.path)
        values = (record.path.name, status_text, summary)
        # No .see() - the list must not scroll away from whatever row is
        # being read while new results come in
        if self.results_tree.exists(iid):
            self.results_tree.item(iid, values=values, tags=(tag,))
        else:
            self.results_tree.insert("", tk.END, iid=iid, values=values, tags=(tag,))

    def _complete_work_unit(self, duration):
        self.work_completed += 1
        self.work_durations.append(duration)
        self.progress.configure(
            value=self.work_completed,
            maximum=max(self.work_total, self.work_completed),
        )
        self._update_progress_info()

    def _update_progress_info(self):
        if not self.work_total:
            # Nothing going on - hide the whole progress row rather than
            # showing an idle "Ingen behandling i gang" message.
            self.progress_frame.pack_forget()
            self.progress_info.set("")
            return
        self.progress_frame.pack(fill="x", pady=(0, 12), before=self.results_frame)
        completed = min(self.work_completed, self.work_total)
        if completed >= self.work_total:
            self.progress_info.set(f"Færdig: {completed}/{self.work_total}")
            return
        if not self.work_durations:
            self.progress_info.set(f"Starter behandling: {completed}/{self.work_total}")
            return
        average = sum(self.work_durations) / len(self.work_durations)
        remaining_seconds = average * (self.work_total - completed)
        eta = format_duration(remaining_seconds)
        percentage = completed / self.work_total * 100
        self.progress_info.set(f"{completed}/{self.work_total} ({percentage:.0f} %) | Ca. {eta} tilbage")

    def _processing_finished(self):
        self.processing = False
        if self.review_total:
            self.status.set('Behandling færdig. Tryk "Gennemse labels" for at tjekke de sidste labels.')
            self._maybe_enable_export_controls()
        else:
            self.status.set("Færdig. Klar til eksport.")
            self._enable_export_controls()

    def _processing_failed(self, error):
        self._stop_busy_indicator()
        self.processing = False
        self.status.set("Behandlingen fejlede.")
        messagebox.showerror("Kunne ikke behandle billederne", str(error), parent=self.root)
        if self.records:
            self._enable_export_controls()

    def _start_busy_indicator(self):
        """Switch the progress bar to an indeterminate 'something is
        happening' animation - used while waiting for the first image of a
        batch to come back. A slow step interval (Tk's own default is 50ms)
        keeps this a smooth back-and-forth slide instead of a rapid,
        flickery re-draw."""
        self._stop_busy_indicator()
        self.progress.configure(mode="indeterminate")
        self.progress.start(50)

    def _stop_busy_indicator(self):
        self.progress.stop()
        self.progress.configure(mode="determinate")

    def _check_review_queue(self):
        """Runs every 200 ms for the app's whole lifetime: keeps the
        "Gennemse labels (N)" button in step with how many labels are
        waiting. It never opens the review window by itself - that only
        happens when the button is pressed (open_review). Wrapped in
        try/finally so one error can't stop the polling for the rest of
        the session."""
        try:
            pending = self.review_total - self.review_completed
            if pending > 0:
                self.review_button.configure(text=f"🔍  Gennemse labels ({pending})")
                if not self.review_bar_visible:
                    self.review_bar.pack(fill="x", pady=(0, 12), before=self.results_frame)
                    self.review_bar_visible = True
            elif self.review_bar_visible:
                self.review_bar.pack_forget()
                self.review_bar_visible = False
            if self.review_window is not None and self.review_window.record is not None:
                self.review_window.update_review_count()
        finally:
            self.root.after(200, self._check_review_queue)

    def open_review(self):
        """ "Gennemse labels": show the review window - on the item it was
        closed on, if any, otherwise on the next one in the queue."""
        if self.review_window is not None and self.review_window.record is not None:
            self.review_window.show()
            return
        record = self._pop_next_review_record()
        if record is None:
            self.status.set("Ingen labels venter lige nu - de dukker op, når de er læst.")
            return
        try:
            if self.review_window is None:
                self.review_window = ReviewWindow(self, record)
            else:
                self.review_window.load_record(record)
            self.review_window.show()
        except Exception:
            # Don't let a record vanish from the queue just because showing
            # it blew up - put it back so the button can try again, instead
            # of leaving review_total stuck one ahead of review_completed.
            self.review_queue.put(record)
            raise

    def _pop_next_review_record(self):
        try:
            return self.review_queue.get_nowait()
        except queue.Empty:
            return None

    def _advance_review_window(self):
        """Move the (persistent) review window on to the next queued item in
        place, instead of hiding it and waiting for the next poll to open a
        new one - keeps back-to-back reviews from flickering."""
        if self.review_window is None:
            return
        record = self._pop_next_review_record()
        if record is None:
            self.review_window.hide()
            return
        try:
            self.review_window.load_record(record)
        except Exception:
            # Same reasoning as _check_review_queue's finally block: don't
            # let a record vanish from the queue just because showing it
            # blew up - put it back so the next poll retries it instead of
            # leaving review_total stuck one ahead of review_completed.
            self.review_queue.put(record)
            raise

    def _open_review_for(self, record):
        """Show the batch review on this waiting record (a double-clicked
        row) rather than on whichever one is next. The record that was open
        goes back to the front of the queue, so it comes up again next."""
        window = self.review_window
        if window is not None and window.record is record:
            window.show()
            return
        with self.review_queue.mutex:
            waiting = any(queued is record for queued in self.review_queue.queue)
        if not waiting:
            # Neither open nor queued: its "Prøv igen" is still reading it
            # (see retry_review); _retry_finished queues it again if needed.
            self.status.set("Genlæser billedet i baggrunden - vent til det er færdigt.")
            return
        if (
            window is not None
            and window.fields_changed()
            and not messagebox.askokcancel(
                "Skift label?",
                "Dine rettelser på den label, der er åben nu, bliver ikke gemt.",
                parent=window.window,
            )
        ):
            window.show()
            return
        with self.review_queue.mutex:
            others = [queued for queued in self.review_queue.queue if queued is not record]
            if window is not None and window.record is not None:
                others.insert(0, window.record)
            self.review_queue.queue.clear()
            self.review_queue.queue.extend(others)
        if window is None:
            self.review_window = ReviewWindow(self, record)
        else:
            window.load_record(record)
        self.review_window.show()

    def reopen_record_review(self, event):
        """Double-click handler on results_tree: reopens the editor for an
        already-processed row, so a mistake spotted after the fact (or
        just after the whole batch is done) doesn't require re-running
        anything. Refuses to open one for a record still sitting in
        review_queue (record.review_pending) - that one is already
        going through the live review popup, and opening a second editor
        for the same record could let both resolve it and double-count
        review_completed."""
        row_id = self.results_tree.identify_row(event.y)
        if not row_id:
            return
        record = next(
            (r for r in self.records if r is not None and str(r.path) == row_id),
            None,
        )
        if record is None:
            return
        if record.review_pending:
            # Still waiting in the batch review - open it there instead of
            # a second editor on the same record
            self._open_review_for(record)
            return
        if self.review_window is not None and self.review_window.record is record:
            self.review_window.window.deiconify()
            self.review_window.window.lift()
            return
        existing = self.open_standalone_windows.get(id(record))
        if existing is not None:
            if existing.window.winfo_exists():
                # Already has its own editor open - bring that one forward
                # instead of opening a second editor on the same record.
                existing.window.deiconify()
                existing.window.lift()
            else:
                # Its "Prøv igen" retry closed the editor and is still
                # running in the background (see _retry()/_retry_finished) -
                # the record isn't settled yet, so don't race it with a
                # second editor; the row will update on its own once the
                # retry completes.
                self.status.set("Genlæser billedet i baggrunden - vent til det er færdigt.")
            return
        self.open_standalone_windows[id(record)] = ReviewWindow(self, record, standalone=True)

    def review_finished(self, record, updated_record):
        record.review_pending = False
        if updated_record is not None:
            debug_log("REVIEWED LABEL")
            debug_log(json.dumps(updated_record.editable_fields(), indent=2, ensure_ascii=False))
            self.upsert_result_row(updated_record)
        self.review_completed += 1
        self.status.set(f"Gennemgang fuldført: {self.review_completed} af {self.review_total}.")
        self._maybe_enable_export_controls()
        self._advance_review_window()

    def _maybe_enable_export_controls(self):
        if not self.processing and self.review_completed >= self.review_total:
            self.status.set("Færdig. Klar til eksport.")
            self._enable_export_controls()

    def _enable_export_controls(self):
        self.auto_export_excel()
        self.export_ready_label.pack(anchor="w")
        self.actions_buttons.pack(anchor="w", pady=(6, 0))

    def auto_export_excel(self):
        """Save the spreadsheet as soon as nothing is left to review - no
        dialog, no button - and again whenever a row changes afterwards
        (see reopen_record_review), so the saved file never goes out of date.
        Not at all while "Gem Excel-filen automatisk" is off: then the result
        is only copied (see _copy_result).

        The file name is picked once (unique_export_path never overwrites an
        existing file) and then reused, so later saves update the same file
        instead of creating "24-9 (1).xlsx", "24-9 (2).xlsx", ... It is
        saved in the folder chosen in the settings, or next to the ZIP file
        or the photos (see batch.export_directory)."""
        if not self.records:
            return
        if not self.settings["save_excel"]:
            self.status.set("Færdig. Kopiér resultatet, og sæt det ind i Google Sheets.")
            self.export_ready_label.configure(text="✅ Færdig — klar til at kopiere", foreground=COLOR_OK)
            return
        note = ""
        try:
            from openpyxl import Workbook

            workbook = Workbook()
            worksheet = workbook.active
            worksheet.title = "Label Miner"
            rows = excel_rows(self.records, self.source_folder_name, self.stk_from_antal.get())
            worksheet.append(rows[0])
            worksheet.append(EXCEL_COLUMNS)
            for row in rows[1:]:
                worksheet.append(row)
            if self.export_path is None:
                chosen = self.settings["export_folder"]
                folder = export_directory(self.export_dir, chosen)
                if chosen and folder != Path(chosen):
                    note = f" (mappen {chosen} findes ikke, så den er gemt ved billederne)"
                self.export_path = unique_export_path(folder, self.source_folder_name)
            workbook.save(self.export_path)
        except PermissionError:
            # Most often the file is open in Excel, which locks it
            self.export_ready_label.configure(text="⚠ Excel-filen er ikke opdateret", foreground=COLOR_ERROR)
            messagebox.showerror(
                "Excel-filen kunne ikke gemmes",
                f"{self.export_path.name} er åben i et andet program, fx Excel. Luk den, og ret "
                "labelen igen – så gemmes filen.",
                parent=self.root,
            )
            return
        except Exception as error:
            self.export_ready_label.configure(text="⚠ Excel-eksport fejlede", foreground=COLOR_ERROR)
            messagebox.showerror("Excel eksport fejlede", str(error), parent=self.root)
            return
        first_save = not self.exported
        self.exported = True
        self.status.set(f"Færdig. Excel gemt: {self.export_path.name}{note}")
        self.export_ready_label.configure(text=f"✅ Excel gemt: {self.export_path.name}", foreground=COLOR_OK)
        if first_save and self.settings["open_excel"]:
            # Only the first time: a later save after an edit would reopen it
            try:
                os.startfile(self.export_path)
            except OSError as error:
                debug_log(f"Could not open {self.export_path}: {error}")

    def _copy_result(self):
        rows = excel_rows(self.records, self.source_folder_name, self.stk_from_antal.get())
        values = ["\t".join(str(value) for value in row[: len(COPY_COLUMNS)]) for row in rows]
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(values))
        self.root.update()
        self.status.set("Resultat kopieret uden kolonnenavne, URL og billednavn.")

    def on_close(self):
        """Clean up any zip extraction temp dirs (see load_images_from_zip)
        before the window closes, so they don't pile up in %TEMP% run
        after run."""
        for extract_dir in self.temp_extract_dirs:
            shutil.rmtree(extract_dir, ignore_errors=True)
        self.root.destroy()

    def retry_review(self, record, working_image, standalone=False):
        self.work_total += 1
        self.progress.configure(maximum=self.work_total)
        self._update_progress_info()
        self.status.set("Genlæser label i baggrunden...")
        # A retry launched from a standalone-reopened record (see
        # reopen_record_review) must never touch the live review popup -
        # that record isn't in review_queue/review_total's bookkeeping, so
        # advancing/hiding the shared window here would yank it out from
        # under an unrelated, actually-in-progress batch review.
        if not standalone:
            self._advance_review_window()
        threading.Thread(
            target=self._retry_review_in_background,
            args=(record, working_image),
            daemon=True,
        ).start()

    def _retry_review_in_background(self, record, working_image):
        """Run the OCR again on a worker thread and hand the result to the
        main thread. The review counters (review_queue, review_total,
        review_pending) are only ever changed in _retry_finished, on the main
        thread, so two retries finishing at once can't race on them."""
        temporary_paths = []
        started_at = time.perf_counter()
        try:
            image_path = save_temporary_png(working_image, temporary_paths)
            reading = extract_label(
                self.ocr_engine,
                image_path,
                self.http_session,
                self.price_cache,
                self.price_cache_lock,
                self.active_choices,
                self.settings["shop_lookup"],
                self.settings["stk_from_antal"],
            )
            record.take_reading(reading)
            record.working_image = working_image
            self._debug_log_record(record, f"{record.path.name} RETRY")
        except Exception as error:
            record.warnings = [("error", f"Genlæsning fejlede: {error}")]
        duration = time.perf_counter() - started_at
        try:
            self.root.after(0, self._show_retry_result, record, duration)
            self.root.after(0, self._retry_finished, record, record.warnings)
        finally:
            remove_temporary_files(temporary_paths)

    def _retry_finished(self, record, warnings):
        """A rescan that still turns up warnings always goes back up for
        review - whether it started from the live review queue or a
        standalone-reopened row - rather than silently leaving a
        half-read label with no way back into the review flow. Records
        reopened standalone start out not counted in review_total (see
        reopen_record_review's review_pending guard), so a fresh set of
        warnings here counts as newly pending rather than assuming it was
        already counted."""
        was_pending = record.review_pending
        if warnings:
            record.review_pending = True
            if not was_pending:
                self.review_total += 1
            self.review_queue.put(record)
            self.status.set("Genlæsning kræver stadig gennemgang.")
        else:
            if was_pending:
                record.review_pending = False
                self.review_completed += 1
            self.status.set("Genlæsning lykkedes.")
        # Safe no-op if this retry didn't come from a standalone editor
        # (see reopen_record_review/open_standalone_windows) - now that the
        # background OCR is done and the record dict is settled, it's safe
        # for a double-click to open a fresh editor on it again.
        self.open_standalone_windows.pop(id(record), None)
        self._maybe_enable_export_controls()

    def _show_retry_result(self, record, duration):
        self.upsert_result_row(record)
        self._complete_work_unit(duration)
