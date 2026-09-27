"""The main window: loading, the batch of photos, the result list and export."""

import json
import queue
import shutil
import sys
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    format_duration,
    load_images_from_zip,
    unique_export_path,
)
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
from ..ocr import create_converter, remove_temporary_files, save_temporary_png
from ..pipeline import extract_label
from ..review import warning_messages
from ..util import debug_log
from .review_window import ReviewWindow
from .theme import accent_button_style, open_manual, set_window_icon


class LabelMinerApp:
    def __init__(self, root, theme_applied=True):
        self.root = root
        self.root.title(f"Label Miner {APP_VERSION}")
        self.center_window(860, 600)
        self.root.minsize(700, 480)
        set_window_icon(self.root)

        self.theme_applied = theme_applied
        self.files = []
        self.records = []
        self.converter = None
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
        # the background, see retry()/retry_finished) bring the existing
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

        self.status = tk.StringVar(value="Indlæser Docling og dets modeller...")
        self.selected_count = tk.StringVar(value="Valgte billeder: 0")

        # The window shows one of three "screens" at a time: a boot screen
        # while Docling's models load, a select screen with one big button,
        # and the work screen (results, export) while photos are processed -
        # rather than one crowded window with unexplained disabled controls.
        self.screen_container = ttk.Frame(root, padding=24)
        self.screen_container.pack(fill="both", expand=True)

        self.build_boot_screen()
        self.build_select_screen()
        self.build_work_screen()
        self.boot_frame.pack(fill="both", expand=True)

        self.root.after(100, self.check_review_queue)
        self.boot_progress.start(50)
        threading.Thread(target=self.load_docling, daemon=True).start()
        threading.Thread(target=self._clean_up_old_extract_dirs, daemon=True).start()

    def _clean_up_old_extract_dirs(self):
        try:
            for folder in clean_up_old_extract_dirs():
                debug_log(f"Removed leftover temp folder {folder}")
        except Exception as error:
            debug_log(f"Cleaning up old temp folders failed: {error!r}")

    def center_window(self, width, height):
        self.root.update_idletasks()
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = max((screen_width - width) // 2, 0)
        y = max((screen_height - height) // 2, 0)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def build_boot_screen(self):
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

        # Only shown on a load failure (see loading_failed/retry_load_docling)
        # so a broken Docling install doesn't leave the user stuck staring at
        # a spinner that will never finish.
        self.boot_retry_button = ttk.Button(center, text="Prøv igen", command=self.retry_load_docling)

    def build_select_screen(self):
        self.select_frame = ttk.Frame(self.screen_container)
        center = ttk.Frame(self.select_frame)
        center.pack(expand=True)

        ttk.Label(center, text="Label Miner", font=("Segoe UI", 22, "bold")).pack()
        ttk.Label(
            center,
            text="Vælg label-billeder eller en ZIP-mappe, og få dem læst automatisk.",
            foreground=COLOR_MUTED,
        ).pack(pady=(4, 24))

        big_style = "Big." + accent_button_style()
        style = ttk.Style(self.root)
        style.configure(big_style, font=(DEFAULT_FONT_FAMILY, 14, "bold"), padding=(28, 18))
        self.select_button = ttk.Button(
            center,
            text="📂  Vælg billeder eller ZIP",
            command=self.select_images,
            style=big_style,
        )
        self.select_button.pack()
        ttk.Button(self.select_frame, text="?  Hjælp", command=lambda: open_manual(self.root)).place(
            relx=1.0, rely=0.0, anchor="ne"
        )

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
                widget.dnd_bind("<<Drop>>", self.handle_dropped_files)
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

    def build_work_screen(self):
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
        # happening - built here but not packed until update_progress_info
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
        # up in the middle of something else (see check_review_queue).
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
        # enable_export_controls() fills it in - so they don't exist on
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
            command=self.copy_result,
            style=accent_button_style(),
        )
        self.copy_button.pack(side="left")

        ttk.Separator(self.work_frame, orient="horizontal").pack(fill="x", pady=(12, 6))
        ttk.Label(self.work_frame, textvariable=self.status, foreground=COLOR_MUTED).pack(anchor="w")

    def load_docling(self):
        """Background thread: load the OCR models (takes a few seconds)."""
        try:
            self.converter = create_converter()
        except Exception as error:
            self.root.after(0, self.loading_failed, error)
            return
        self.root.after(0, self.docling_ready)

    def docling_ready(self):
        self.loading = False
        self.boot_progress.stop()
        self.boot_frame.pack_forget()
        self.select_frame.pack(fill="both", expand=True)
        self.status.set("Docling er klar. Vælg billeder for at starte.")
        # Photos or a zip dropped onto the program's desktop icon (or
        # opened with it) arrive as command-line arguments - start on them
        # right away, as if they'd been picked in the file dialog
        startup_paths = [arg for arg in sys.argv[1:] if not arg.startswith("--") and Path(arg).exists()]
        if startup_paths:
            self.begin_processing(startup_paths)

    def loading_failed(self, error):
        self.loading = False
        self.boot_progress.stop()
        self.status.set("Docling kunne ikke indlæses.")
        self.boot_retry_button.pack(pady=(16, 0))
        messagebox.showerror(
            "Docling ikke tilgængelig",
            f"Installer Docling med: pip install docling pillow-heif\n\nDetaljer: {error}",
            parent=self.root,
        )

    def retry_load_docling(self):
        self.loading = True
        self.boot_retry_button.pack_forget()
        self.status.set("Indlæser Docling og dets modeller...")
        self.boot_progress.start(50)
        threading.Thread(target=self.load_docling, daemon=True).start()

    def select_images(self):
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

    def handle_dropped_files(self, event):
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
        self.select_frame.pack_forget()
        self.work_frame.pack(fill="both", expand=True)
        self.start_processing()

    def start_processing(self):
        if not self.files or self.converter is None or self.processing:
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
        self.update_progress_info()
        self.start_busy_indicator()
        threading.Thread(target=self.process_in_background, daemon=True).start()

    def _extract_one(self, path):
        """Runs on a worker thread (see process_in_background). Exceptions
        are caught here rather than left in the future, so one bad photo
        can't stop the rest of the batch."""
        started_at = time.perf_counter()
        try:
            raw_text, parsed_label, warnings, candidates = extract_label(
                self.converter,
                path,
                self.http_session,
                self.price_cache,
                self.price_cache_lock,
            )
        except Exception as error:
            raw_text = ""
            parsed_label = {}
            warnings = [("error", f"Behandling af billedet fejlede: {error}")]
            candidates = []
        duration = time.perf_counter() - started_at
        return raw_text, parsed_label, warnings, candidates, duration

    def process_in_background(self):
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
                    raw_text, parsed_label, warnings, candidates, duration = future.result()
                    completed_count += 1
                    debug_log(f"\n===== {path.name} =====")
                    debug_log("RAW TEXT")
                    debug_log(raw_text or "<empty>")
                    debug_log("PARSED LABEL")
                    debug_log(json.dumps(parsed_label, indent=2, ensure_ascii=False))
                    for warning in warning_messages(warnings):
                        debug_log(f"Advarsel: {warning}")
                    record = {
                        "path": path,
                        "raw_text": raw_text,
                        "parsed": parsed_label,
                        "warnings": warnings,
                        "candidates": candidates,
                        # True while a record is sitting in review_queue
                        # waiting for (or actively going through) the live
                        # review popup - reopen_record_review refuses to
                        # open a standalone editor for one of these, so a
                        # row can't be resolved twice (once through each
                        # window) and double-count review_completed.
                        "review_pending": bool(warnings),
                    }
                    self.records[position] = record
                    if warnings:
                        self.review_total += 1
                        self.review_queue.put(record)
                    self.root.after(
                        0,
                        self.show_result,
                        path,
                        raw_text,
                        parsed_label,
                        warnings,
                        completed_count,
                        duration,
                    )
        except Exception as error:
            self.root.after(0, self.processing_failed, error)
        else:
            self.root.after(0, self.processing_finished)

    def show_result(self, path, raw_text, parsed_label, warnings, index, duration):
        if index == 1:
            self.stop_busy_indicator()
        self.upsert_result_row(path, parsed_label, warnings, raw_text)
        self.complete_work_unit(duration)
        self.status.set(f"Behandlet {index} af {len(self.files)} billeder.")

    @staticmethod
    def _status_for_warnings(warnings):
        if not warnings:
            return "OK", "ok"
        if any(category == "error" for category, _ in warnings):
            return "Fejl", "error"
        return "Gennemse", "warning"

    @staticmethod
    def _summary_for_record(parsed_label, raw_text):
        varenavn = parsed_label.get("varenavn", "")
        mk_varenummer = parsed_label.get("mk_varenummer", "")
        stand = parsed_label.get("stand", "")
        parts = [value for value in (varenavn, mk_varenummer, f"Stand {stand}" if stand else "") if value]
        if parts:
            return " · ".join(parts)
        text = (raw_text or "").strip()
        if text:
            return (text[:80] + "…") if len(text) > 80 else text
        return "Ingen tekst genkendt"

    def upsert_result_row(self, path, parsed_label, warnings, raw_text):
        """Add (or, on a retry, update in place) this image's row in the
        result list - filename, a status tag driving its color, and a short
        human-readable summary. The full raw OCR text/JSON never appears
        here; it's only shown in the review window or via --debug."""
        status_text, tag = self._status_for_warnings(warnings)
        summary = self._summary_for_record(parsed_label, raw_text)
        iid = str(path)
        values = (path.name, status_text, summary)
        # No .see() - the list must not scroll away from whatever row is
        # being read while new results come in
        if self.results_tree.exists(iid):
            self.results_tree.item(iid, values=values, tags=(tag,))
        else:
            self.results_tree.insert("", tk.END, iid=iid, values=values, tags=(tag,))

    def complete_work_unit(self, duration):
        self.work_completed += 1
        self.work_durations.append(duration)
        self.progress.configure(
            value=self.work_completed,
            maximum=max(self.work_total, self.work_completed),
        )
        self.update_progress_info()

    def update_progress_info(self):
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

    def processing_finished(self):
        self.processing = False
        if self.review_total:
            self.status.set('Behandling færdig. Tryk "Gennemse labels" for at tjekke de sidste labels.')
            self.maybe_enable_export_controls()
        else:
            self.status.set("Færdig. Klar til eksport.")
            self.enable_export_controls()

    def processing_failed(self, error):
        self.stop_busy_indicator()
        self.processing = False
        self.status.set("Behandlingen fejlede.")
        messagebox.showerror("Kunne ikke behandle billederne", str(error), parent=self.root)
        if self.records:
            self.enable_export_controls()

    def start_busy_indicator(self):
        """Switch the progress bar to an indeterminate 'something is
        happening' animation - used while waiting for the first image of a
        batch to come back. A slow step interval (Tk's own default is 50ms)
        keeps this a smooth back-and-forth slide instead of a rapid,
        flickery re-draw."""
        self.stop_busy_indicator()
        self.progress.configure(mode="indeterminate")
        self.progress.start(50)

    def stop_busy_indicator(self):
        self.progress.stop()
        self.progress.configure(mode="determinate")

    def check_review_queue(self):
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
            self.root.after(200, self.check_review_queue)

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

    def advance_review_window(self):
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
            # Same reasoning as check_review_queue's finally block: don't
            # let a record vanish from the queue just because showing it
            # blew up - put it back so the next poll retries it instead of
            # leaving review_total stuck one ahead of review_completed.
            self.review_queue.put(record)
            raise

    def reopen_record_review(self, event):
        """Double-click handler on results_tree: reopens the editor for an
        already-processed row, so a mistake spotted after the fact (or
        just after the whole batch is done) doesn't require re-running
        anything. Refuses to open one for a record still sitting in
        review_queue (record["review_pending"]) - that one is already
        going through the live review popup, and opening a second editor
        for the same record could let both resolve it and double-count
        review_completed."""
        row_id = self.results_tree.identify_row(event.y)
        if not row_id:
            return
        record = next(
            (r for r in self.records if r is not None and str(r["path"]) == row_id),
            None,
        )
        if record is None:
            return
        if record.get("review_pending"):
            # Still waiting in the batch review - open that instead of a
            # second editor on the same record
            self.open_review()
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
                # running in the background (see retry()/retry_finished) -
                # the record isn't settled yet, so don't race it with a
                # second editor; the row will update on its own once the
                # retry completes.
                self.status.set("Genlæser billedet i baggrunden - vent til det er færdigt.")
            return
        self.open_standalone_windows[id(record)] = ReviewWindow(self, record, standalone=True)

    def review_finished(self, record, updated_record):
        record["review_pending"] = False
        if updated_record is not None:
            debug_log("REVIEWED LABEL")
            debug_log(json.dumps(updated_record["parsed"], indent=2, ensure_ascii=False))
            self.upsert_result_row(
                updated_record["path"],
                updated_record["parsed"],
                updated_record["warnings"],
                updated_record.get("raw_text", ""),
            )
        self.review_completed += 1
        self.status.set(f"Gennemgang fuldført: {self.review_completed} af {self.review_total}.")
        self.maybe_enable_export_controls()
        self.advance_review_window()

    def maybe_enable_export_controls(self):
        if not self.processing and self.review_completed >= self.review_total:
            self.status.set("Færdig. Klar til eksport.")
            self.enable_export_controls()

    def enable_export_controls(self):
        self.auto_export_excel()
        self.export_ready_label.pack(anchor="w")
        self.actions_buttons.pack(anchor="w", pady=(6, 0))

    def auto_export_excel(self):
        """Save the spreadsheet as soon as nothing is left to review - no
        dialog, no button - and again whenever a row changes afterwards
        (see reopen_record_review), so the saved file never goes out of date.

        The file name is picked once (unique_export_path never overwrites an
        existing file) and then reused, so later saves update the same file
        instead of creating "24-9 (1).xlsx", "24-9 (2).xlsx", ..."""
        if not self.records:
            return
        try:
            from openpyxl import Workbook

            workbook = Workbook()
            worksheet = workbook.active
            worksheet.title = "Label Miner"
            rows = excel_rows(self.records, self.source_folder_name)
            worksheet.append(rows[0])
            worksheet.append(EXCEL_COLUMNS)
            for row in rows[1:]:
                worksheet.append(row)
            if self.export_path is None:
                self.export_path = unique_export_path(self.export_dir, self.source_folder_name)
            workbook.save(self.export_path)
        except Exception as error:
            self.export_ready_label.configure(text="⚠ Excel-eksport fejlede", foreground=COLOR_ERROR)
            messagebox.showerror("Excel eksport fejlede", str(error), parent=self.root)
            return
        self.exported = True
        self.status.set(f"Færdig. Excel gemt: {self.export_path.name}")
        self.export_ready_label.configure(text=f"✅ Excel gemt: {self.export_path.name}", foreground=COLOR_OK)

    def copy_result(self):
        rows = excel_rows(self.records, self.source_folder_name)
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
        self.update_progress_info()
        self.status.set("Genlæser label i baggrunden...")
        # A retry launched from a standalone-reopened record (see
        # reopen_record_review) must never touch the live review popup -
        # that record isn't in review_queue/review_total's bookkeeping, so
        # advancing/hiding the shared window here would yank it out from
        # under an unrelated, actually-in-progress batch review.
        if not standalone:
            self.advance_review_window()
        threading.Thread(
            target=self.retry_review_in_background,
            args=(record, working_image),
            daemon=True,
        ).start()

    def retry_review_in_background(self, record, working_image):
        """Run the OCR again on a worker thread and hand the result to the
        main thread. The review counters (review_queue, review_total,
        review_pending) are only ever changed in retry_finished, on the main
        thread, so two retries finishing at once can't race on them."""
        temporary_paths = []
        started_at = time.perf_counter()
        try:
            image_path = save_temporary_png(working_image, temporary_paths)
            raw_text, parsed_label, warnings, candidates = extract_label(
                self.converter,
                image_path,
                self.http_session,
                self.price_cache,
                self.price_cache_lock,
            )
            record.update(
                raw_text=raw_text,
                parsed=parsed_label,
                warnings=warnings,
                candidates=candidates,
                working_image=working_image,
            )
            debug_log(f"\n===== {record['path'].name} RETRY =====")
            debug_log("RAW TEXT")
            debug_log(raw_text or "<empty>")
            debug_log("PARSED LABEL")
            debug_log(json.dumps(parsed_label, indent=2, ensure_ascii=False))
            for warning in warning_messages(warnings):
                debug_log(f"Advarsel: {warning}")
            duration = time.perf_counter() - started_at
            self.root.after(0, self.show_retry_result, record, warnings, duration)
            self.root.after(0, self.retry_finished, record, warnings)
        except Exception as error:
            record["warnings"] = [("error", f"Genlæsning fejlede: {error}")]
            duration = time.perf_counter() - started_at
            self.root.after(0, self.show_retry_result, record, record["warnings"], duration)
            self.root.after(0, self.retry_finished, record, record["warnings"])
        finally:
            remove_temporary_files(temporary_paths)

    def retry_finished(self, record, warnings):
        """A rescan that still turns up warnings always goes back up for
        review - whether it started from the live review queue or a
        standalone-reopened row - rather than silently leaving a
        half-read label with no way back into the review flow. Records
        reopened standalone start out not counted in review_total (see
        reopen_record_review's review_pending guard), so a fresh set of
        warnings here counts as newly pending rather than assuming it was
        already counted."""
        was_pending = record.get("review_pending", False)
        if warnings:
            record["review_pending"] = True
            if not was_pending:
                self.review_total += 1
            self.review_queue.put(record)
            self.status.set("Genlæsning kræver stadig gennemgang.")
        else:
            if was_pending:
                record["review_pending"] = False
                self.review_completed += 1
            self.status.set("Genlæsning lykkedes.")
        # Safe no-op if this retry didn't come from a standalone editor
        # (see reopen_record_review/open_standalone_windows) - now that the
        # background OCR is done and the record dict is settled, it's safe
        # for a double-click to open a fresh editor on it again.
        self.open_standalone_windows.pop(id(record), None)
        self.maybe_enable_export_controls()

    def show_retry_result(self, record, warnings, duration):
        self.upsert_result_row(record["path"], record["parsed"], warnings, record.get("raw_text", ""))
        self.complete_work_unit(duration)
