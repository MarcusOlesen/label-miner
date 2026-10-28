"""The window for checking labels that couldn't be approved automatically."""

import io
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk
from urllib.parse import quote

from PIL import Image, ImageOps, ImageTk

from ..config import COLOR_APPROVE, COLOR_APPROVE_ACTIVE, COLOR_MUTED, COLOR_OK, COLOR_WARNING, SHOP_URL
from ..parsing import VALID_STANDS, item_part, part_note
from ..review import review_question
from ..shop import SHOP_WARNING_CATEGORIES, format_combined_price, look_up_product
from ..util import debug_log
from .theme import open_manual, resolve_tk_color, set_window_icon, shade_color


class ReviewWindow:
    """The window the user checks a label in when it couldn't be approved
    automatically. Built to be hard to get wrong:

    - one plain question at the top says what to check (review_question),
      and the fields it concerns are marked
    - webshop suggestions are picture cards, not buttons that look like
      text fields, and picking one only fills in the fields - nothing is
      saved until "Godkend"
    - "Godkend" is the only green button; "Læs billedet igen" sits under
      the photo with the other photo tools, so it can't be mistaken for it
    - leaving with unsaved edits asks first
    """

    THUMBNAIL_SIZE = 72

    def __init__(self, app, record, standalone=False):
        """standalone=True is reopen_record_review's path back into an
        already-processed record: same window/UI, but confirm/skip/retry
        never touch review_completed/review_total/review_queue (see those
        methods below) - only the live batch-review flow (the queue-driven
        popup tracked as app.review_window) owns that bookkeeping."""
        self.app = app
        self.standalone = standalone
        self.record = None
        self.window = tk.Toplevel(app.root)
        self.window.title("Rediger label" if standalone else "Label-gennemgang")
        # Centred, and never bigger than the screen (laptops, 125-150 %
        # display scaling)
        screen_width = self.window.winfo_screenwidth()
        screen_height = self.window.winfo_screenheight()
        width = min(1180, screen_width - 60)
        height = min(800, screen_height - 100)
        x = max((screen_width - width) // 2, 0)
        y = max((screen_height - height) // 2 - 20, 0)
        self.window.geometry(f"{width}x{height}+{x}+{y}")
        self.window.minsize(min(980, width), min(640, height))
        # ✕ on the batch review just closes the window - the item stays
        # where it was (edits included) and "Gennemse labels" on the main
        # window brings it back. On a reopened row it means "Luk".
        self.window.protocol("WM_DELETE_WINDOW", self.skip if standalone else self.close_window)
        set_window_icon(self.window)

        # Rotation fill and canvas backdrop share this color so the border
        # added around a rotated image blends into the background instead
        # of standing out as a bright frame. Pulled from the active ttk
        # theme so it tracks light/dark mode instead of being hardcoded -
        # resolved to a real hex value (see resolve_tk_color) since PIL
        # can't parse the symbolic color names ttk themes sometimes report.
        style = ttk.Style(self.window)
        theme_background = style.lookup("TFrame", "background")
        self.canvas_background = resolve_tk_color(self.window, theme_background or "#202020")
        self.text_color = resolve_tk_color(
            self.window, style.lookup("TLabel", "foreground") or "#ffffff", "#ffffff"
        )
        self.card_background = shade_color(self.canvas_background, 0.08)
        self.card_border = shade_color(self.canvas_background, 0.25)
        # Disclosure toggles ("▶ Flere værktøjer") read as plain text links
        style.configure("Candidate.TButton", anchor="w")

        self.window.columnconfigure(0, weight=3, minsize=480)
        self.window.columnconfigure(1, weight=2, minsize=420)
        self.window.rowconfigure(0, weight=1, minsize=420)

        # ---- Left: the photo, and everything that acts on the photo -----
        self.image_frame = ttk.Frame(self.window, padding=12)
        self.image_frame.grid(row=0, column=0, sticky="nsew")
        self.image_frame.columnconfigure(0, weight=1)
        self.image_frame.rowconfigure(0, weight=1)

        # Has to be set before the rotation slider below: its initial
        # .set(0) fires _on_rotation_slider synchronously, which calls
        # resize_image(), which reads self.original_image.
        self.original_image = None
        self.working_image = None
        self.crop_drag_start = None
        self.pending_crop_fraction = None
        self.image_bounds = None
        self.rotation_angle = 0.0
        # The label's area as fractions of the photo (found during OCR,
        # see extract_label) - shown zoomed in on by default
        self.zoom_box = None
        self.zoomed = False

        self.image_canvas = tk.Canvas(
            self.image_frame,
            background=self.canvas_background,
            highlightthickness=0,
        )
        self.image_canvas.grid(row=0, column=0, sticky="nsew")
        self.image_canvas.bind("<Configure>", self.resize_image)
        self.image_canvas.bind("<ButtonPress-1>", self.crop_start_event)
        self.image_canvas.bind("<B1-Motion>", self.crop_drag_event)
        self.image_canvas.bind("<ButtonRelease-1>", self.crop_release_event)

        image_actions = ttk.Frame(self.image_frame)
        image_actions.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        for column in range(4):
            image_actions.columnconfigure(column, weight=1)
        ttk.Button(image_actions, text="⟲ Drej", command=lambda: self.rotate_quarter_turn(-90)).grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        ttk.Button(image_actions, text="⟳ Drej", command=lambda: self.rotate_quarter_turn(90)).grid(
            row=0, column=1, sticky="ew", padx=4
        )
        self.zoom_button = ttk.Button(image_actions, text="Vis hele billedet", command=self.toggle_zoom)
        self.zoom_button.grid(row=0, column=2, sticky="ew", padx=4)
        ttk.Button(image_actions, text="↻ Læs billedet igen", command=self.retry).grid(
            row=0, column=3, sticky="ew", padx=(4, 0)
        )

        self.tools_visible = False
        self.tools_toggle = ttk.Button(
            self.image_frame,
            text="▶ Flere værktøjer",
            command=self.toggle_tools,
            style="Candidate.TButton",
        )
        self.tools_toggle.grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.tools_frame = ttk.Frame(self.image_frame)
        self.tools_frame.grid(row=3, column=0, sticky="ew")
        self.tools_frame.columnconfigure(1, weight=1)

        ttk.Label(self.tools_frame, text="Rotation").grid(row=0, column=0, padx=(0, 8))
        # The label has to exist before the slider: the slider's initial
        # .set(0) fires its -command callback immediately, and that
        # callback updates this label.
        self.rotation_label = ttk.Label(self.tools_frame, text="0°", width=5, anchor="e")
        self.rotation_slider = ttk.Scale(
            self.tools_frame,
            from_=-180,
            to=180,
            orient="horizontal",
            command=self._on_rotation_slider,
        )
        self.rotation_slider.set(0)
        self.rotation_slider.grid(row=0, column=1, sticky="ew")
        self.rotation_label.grid(row=0, column=2, padx=(8, 0))
        tool_buttons = ttk.Frame(self.tools_frame)
        tool_buttons.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        tool_buttons.columnconfigure(0, weight=1)
        tool_buttons.columnconfigure(1, weight=1)
        ttk.Button(tool_buttons, text="Spejl", command=self.mirror).grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        ttk.Button(tool_buttons, text="Nulstil billede", command=self.reset_image).grid(
            row=0, column=1, sticky="ew", padx=(4, 0)
        )
        ttk.Label(
            self.tools_frame,
            text='Træk på billedet for at beskære det, og tryk så "Læs billedet igen".',
            foreground=COLOR_MUTED,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(self.tools_frame, text="Tekst læst fra labelen:", foreground=COLOR_MUTED).grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(8, 2)
        )
        self.raw_text_widget = tk.Text(self.tools_frame, height=3, wrap="word")
        self.raw_text_widget.configure(state="disabled")
        self.raw_text_widget.grid(row=4, column=0, columnspan=3, sticky="ew")
        self.tools_frame.grid_remove()

        # ---- Right: what to check, the choices, the fields, the verdict --
        panel = ttk.Frame(self.window, padding=(18, 18, 18, 0))
        panel.grid(row=0, column=1, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(0, weight=1)
        panel.rowconfigure(1, weight=0)

        # Scrollable middle: the question/choices/fields can grow taller
        # than the window without ever pushing the footer buttons off-screen.
        scroll_area = ttk.Frame(panel)
        scroll_area.grid(row=0, column=0, sticky="nsew")
        scroll_area.columnconfigure(0, weight=1)
        scroll_area.rowconfigure(0, weight=1)

        self.content_canvas = tk.Canvas(scroll_area, highlightthickness=0)
        self.content_canvas.grid(row=0, column=0, sticky="nsew")
        content_scrollbar = ttk.Scrollbar(scroll_area, orient="vertical", command=self.content_canvas.yview)
        content_scrollbar.grid(row=0, column=1, sticky="ns")
        self.content_canvas.configure(yscrollcommand=content_scrollbar.set)

        content = ttk.Frame(self.content_canvas)
        content.columnconfigure(0, weight=1)
        self.content_window = self.content_canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind(
            "<Configure>",
            lambda event: self.content_canvas.configure(scrollregion=self.content_canvas.bbox("all")),
        )
        self.content_canvas.bind("<Configure>", self._on_content_resize)
        self.content_canvas.bind(
            "<Enter>",
            lambda event: self.content_canvas.bind_all("<MouseWheel>", self._on_content_mousewheel),
        )
        self.content_canvas.bind("<Leave>", lambda event: self.content_canvas.unbind_all("<MouseWheel>"))

        header = ttk.Frame(content)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header,
            text="Rediger label" if standalone else "Gennemse label",
            font=("Segoe UI", 16, "bold"),
        ).grid(row=0, column=0, sticky="w")
        self.review_count = tk.StringVar()
        if not standalone:
            # A live-batch-queue position - meaningless for a record
            # reopened after the fact
            ttk.Label(header, textvariable=self.review_count, foreground=COLOR_MUTED).grid(
                row=0, column=1, sticky="e"
            )
        ttk.Button(header, text="?  Hjælp", command=lambda: open_manual(self.window)).grid(
            row=0, column=2, sticky="e", padx=(12, 0)
        )

        # The question: a coloured box, the first thing the eye lands on
        self.question_frame = tk.Frame(
            content,
            background=self.card_background,
            highlightthickness=2,
            highlightbackground=COLOR_WARNING,
            padx=12,
            pady=10,
        )
        self.question_frame.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self.question_frame.columnconfigure(0, weight=1)
        self.question_title = tk.Label(
            self.question_frame,
            font=("Segoe UI", 13, "bold"),
            background=self.card_background,
            foreground=COLOR_WARNING,
            justify="left",
            anchor="w",
        )
        self.question_title.grid(row=0, column=0, sticky="ew")
        self.question_detail = tk.Label(
            self.question_frame,
            font=("Segoe UI", 10),
            background=self.card_background,
            foreground=self.text_color,
            justify="left",
            anchor="w",
        )
        self.question_detail.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.question_others = tk.Label(
            self.question_frame,
            font=("Segoe UI", 9),
            background=self.card_background,
            foreground=COLOR_MUTED,
            justify="left",
            anchor="w",
        )
        self.question_others.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        self.wrapping_labels = [self.question_title, self.question_detail, self.question_others]

        # Webshop suggestions as picture cards (see show_candidates)
        self.candidates = []
        self.candidate_cards = []
        self.selected_candidate = None
        self.blank_thumbnail = tk.PhotoImage(width=self.THUMBNAIL_SIZE, height=self.THUMBNAIL_SIZE)
        self.choices_frame = ttk.Frame(content)
        self.choices_frame.grid(row=2, column=0, sticky="ew", pady=(16, 0))
        self.choices_frame.columnconfigure(0, weight=1)

        fields = ttk.Frame(content)
        fields.grid(row=3, column=0, sticky="ew", pady=(16, 0))
        fields.columnconfigure(0, weight=1)

        # One free-text price field rather than a price + compare-at price
        # pair: the export has a single comments column for it, and a
        # discount is already written as "1234 kr (4321 kr)"
        # (see shop.format_combined_price).
        self.entries = {}
        self.entry_labels = {}
        self.add_entry(fields, 0, "MK Varenummer", "mk_varenummer")
        self.add_entry(fields, 1, "Varenavn", "varenavn")
        self.add_entry(fields, 2, "MK ordrenr", "mk_ordrenr")
        self.add_entry(fields, 3, "Stand", "stand")
        self.add_entry(fields, 4, "Pris / kommentar", "price")
        self.loaded_fields = {}

        # Re-runs just the webshop lookup with whatever's currently typed
        # above - for a corrected number/name, without redoing OCR.
        self.price_refresh_in_progress = False
        price_actions = ttk.Frame(content)
        price_actions.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        self.price_refresh_button = ttk.Button(
            price_actions, text="🔍  Søg på hjemmesiden igen", command=self.refresh_price
        )
        self.price_refresh_button.pack(side="left")
        self.price_refresh_status = ttk.Label(price_actions, text="", foreground=COLOR_MUTED)
        self.price_refresh_status.pack(side="left", padx=(8, 0))

        # Pinned footer: always visible regardless of how tall the
        # scrollable content above ends up being.
        footer = ttk.Frame(panel, padding=(0, 12, 0, 18))
        footer.grid(row=1, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        footer.columnconfigure(1, weight=1)
        # A plain tk.Button, not ttk: ttk themes don't allow a custom fill
        # colour, and this has to be unmistakably *the* button to press
        tk.Button(
            footer,
            text="✓  Gem ændringer" if standalone else "✓  Godkend og næste",
            command=self.confirm,
            font=("Segoe UI", 12, "bold"),
            background=COLOR_APPROVE,
            foreground="#ffffff",
            activebackground=COLOR_APPROVE_ACTIVE,
            activeforeground="#ffffff",
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            pady=9,
        ).grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Button(
            footer,
            text="Luk" if standalone else "Spring over – ret senere",
            command=self.skip,
        ).grid(row=1, column=0, sticky="ew", padx=(0, 4), pady=(8, 0))
        ttk.Button(footer, text="Åbn hjemmeside", command=self.open_website).grid(
            row=1, column=1, sticky="ew", padx=(4, 0), pady=(8, 0)
        )

        self.load_record(record)

    def add_entry(self, parent, row, label, key):
        label_widget = ttk.Label(parent, text=label)
        label_widget.grid(row=row * 2, column=0, sticky="w", pady=(0, 2))
        entry = ttk.Entry(parent)
        entry.grid(row=row * 2 + 1, column=0, sticky="ew", pady=(0, 9))
        self.entries[key] = entry
        self.entry_labels[key] = (label_widget, label)

    def mark_fields(self, fields):
        """Flag the entry fields the current question is about."""
        for key, (label_widget, text) in self.entry_labels.items():
            if key in fields:
                label_widget.configure(text=f"⚠  {text} – tjek", foreground=COLOR_WARNING)
            else:
                # "" = the theme's own label colour
                label_widget.configure(text=text, foreground="")

    def _on_content_resize(self, event):
        self.content_canvas.itemconfigure(self.content_window, width=event.width)
        # Wrap long texts to the panel's width instead of a fixed guess
        for label in self.wrapping_labels:
            label.configure(wraplength=max(event.width - 40, 200))

    def _on_content_mousewheel(self, event):
        self.content_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def show_question(self, warnings):
        question = review_question(warnings, (self.record or {}).get("parsed", {}).get("part", ""))
        if question is None:
            color = COLOR_OK
            title, detail, others, fields = (
                "Ingen problemer fundet",
                "Ret felterne hvis noget er forkert.",
                [],
                [],
            )
        else:
            color = question["color"]
            title, detail = question["title"], question["detail"]
            others, fields = question["others"], question["fields"]
        self.question_frame.configure(highlightbackground=color)
        self.question_title.configure(text=title, foreground=color)
        self.question_detail.configure(text=detail)
        if others:
            self.question_others.configure(text="Også: " + " · ".join(others))
            self.question_others.grid()
        else:
            self.question_others.grid_remove()
        self.mark_fields(fields)
        return fields

    def show_candidates(self, candidates):
        """Webshop suggestions as picture cards: product photo, name, price
        and whether it was found via the number or the name. Clicking a
        card selects it (see select_candidate)."""
        for widget in self.choices_frame.winfo_children():
            widget.destroy()
        self.candidates = list(candidates)
        self.candidate_cards = []
        self.selected_candidate = None
        if not self.candidates:
            self.choices_frame.grid_remove()
            return
        self.choices_frame.grid()
        ttk.Label(self.choices_frame, text="Hvilken vare er det?", font=("Segoe UI", 11, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            self.choices_frame,
            text="Klik på den vare der passer til labelen. Klik igen for at fortryde.",
            foreground=COLOR_MUTED,
        ).grid(row=1, column=0, sticky="w", pady=(0, 6))
        is_part = bool(self.record and self.record["parsed"].get("part"))
        for index, candidate in enumerate(self.candidates):
            card = tk.Frame(
                self.choices_frame,
                background=self.card_background,
                highlightthickness=2,
                highlightbackground=self.card_border,
                cursor="hand2",
            )
            card.grid(row=index + 2, column=0, sticky="ew", pady=(0, 6))
            card.columnconfigure(1, weight=1)
            thumbnail = tk.Label(card, image=self.blank_thumbnail, background=self.card_background)
            thumbnail.grid(row=0, column=0, rowspan=2, padx=8, pady=8)
            name = tk.Label(
                card,
                text=candidate["name"],
                font=("Segoe UI", 10, "bold"),
                background=self.card_background,
                foreground=self.text_color,
                justify="left",
                anchor="w",
                wraplength=260,
            )
            name.grid(row=0, column=1, sticky="sw", pady=(8, 0))
            price = candidate["price"] or "ingen pris"
            if is_part:
                price = "delvis vare – ingen pris"
            found_by = candidate.get("found_by")
            details = price + (f"   ·   fundet via {found_by}" if found_by else "")
            meta = tk.Label(
                card, text=details, background=self.card_background, foreground=COLOR_MUTED, anchor="w"
            )
            meta.grid(row=1, column=1, sticky="nw", pady=(0, 8))
            check = tk.Label(
                card,
                text="",
                font=("Segoe UI", 16, "bold"),
                background=self.card_background,
                foreground=COLOR_OK,
                width=2,
            )
            check.grid(row=0, column=2, rowspan=2, padx=(0, 10))
            for widget in (card, thumbnail, name, meta, check):
                widget.bind("<Button-1>", lambda event, i=index: self.select_candidate(i))
            self.candidate_cards.append({"frame": card, "thumbnail": thumbnail, "check": check})
        threading.Thread(
            target=self._fetch_thumbnails,
            args=(self.record, [candidate.get("image", "") for candidate in self.candidates]),
            daemon=True,
        ).start()

    def _fetch_thumbnails(self, record, image_urls):
        """Background thread: download the shop's product photos for the
        cards. Cached on the app, so the same product isn't fetched twice."""
        for index, url in enumerate(image_urls):
            if not url:
                continue
            image = self.app.thumbnail_cache.get(url)
            if image is None:
                try:
                    separator = "&" if "?" in url else "?"
                    response = self.app.http_session.get(
                        f"{url}{separator}width=200", headers={"User-Agent": "Mozilla/5.0"}, timeout=10
                    )
                    response.raise_for_status()
                    image = Image.open(io.BytesIO(response.content)).convert("RGB")
                    image.thumbnail((self.THUMBNAIL_SIZE, self.THUMBNAIL_SIZE), Image.Resampling.LANCZOS)
                    self.app.thumbnail_cache[url] = image
                except Exception as error:
                    debug_log(f"Kunne ikke hente produktbillede {url}: {error}")
                    continue
            try:
                self.window.after(0, self._show_thumbnail, record, index, image)
            except (RuntimeError, tk.TclError):
                return  # window closed meanwhile

    def _show_thumbnail(self, record, index, image):
        if self.record is not record or index >= len(self.candidate_cards):
            return
        photo = ImageTk.PhotoImage(image)
        card = self.candidate_cards[index]
        card["thumbnail"].configure(image=photo)
        card["photo"] = photo  # Tk only shows it while a reference exists

    def select_candidate(self, index):
        """A card was clicked: fill the name and price fields from it and
        mark it chosen - or, if it was already chosen, undo that and put
        back what the label said. Nothing is written to the record until
        "Godkend" (see confirm) - skipping after picking leaves the record
        as it was. A part of an item (1/2 etc.) never gets a price, not even
        here: its comment field keeps the "OBS: delvis vare" note."""
        candidate = self.candidates[index]
        if candidate is self.selected_candidate:
            self.selected_candidate = None
            for key in ("varenavn", "price"):
                self.entries[key].delete(0, tk.END)
                self.entries[key].insert(0, self.loaded_fields.get(key, ""))
        else:
            # Checked before the name is replaced: the part marker may only
            # be in the label's name ("2/2 Vega ..."), never in the shop's
            fields = self.read_fields()
            part = item_part(fields["mk_varenummer"], fields["varenavn"]) or (
                self.record["parsed"].get("part", "") if self.record is not None else ""
            )
            self.entries["varenavn"].delete(0, tk.END)
            self.entries["varenavn"].insert(0, candidate["name"])
            self.entries["price"].delete(0, tk.END)
            if part:
                self.entries["price"].insert(0, part_note(part))
            else:
                self.entries["price"].insert(
                    0, format_combined_price(candidate["price"], candidate["compare_price"])
                )
            self.selected_candidate = candidate
        for card, shown in zip(self.candidate_cards, self.candidates, strict=True):
            chosen = shown is self.selected_candidate
            card["frame"].configure(highlightbackground=COLOR_OK if chosen else self.card_border)
            card["check"].configure(text="✓" if chosen else "")

    def toggle_tools(self):
        self.tools_visible = not self.tools_visible
        if self.tools_visible:
            self.tools_frame.grid()
            self.tools_toggle.configure(text="▼ Færre værktøjer")
        else:
            self.tools_frame.grid_remove()
            self.tools_toggle.configure(text="▶ Flere værktøjer")

    def toggle_zoom(self):
        self.zoomed = not self.zoomed and self.zoom_box is not None
        self.pending_crop_fraction = None
        self.update_zoom_button()
        self.resize_image()

    def update_zoom_button(self):
        if self.zoom_box is None:
            self.zoom_button.configure(text="Vis hele billedet", state="disabled")
        else:
            self.zoom_button.configure(
                text="Vis hele billedet" if self.zoomed else "Zoom ind på labelen", state="normal"
            )

    def fields_changed(self):
        return self.record is not None and self.read_fields() != self.loaded_fields

    def refresh_price(self):
        """Re-run just the webshop lookup using whatever's currently typed
        in the fields - for fixing a typo without redoing OCR on the image."""
        if self.price_refresh_in_progress or self.record is None:
            return
        fields = self.read_fields()
        target_record = self.record
        self.price_refresh_in_progress = True
        self.price_refresh_button.configure(state="disabled", text="Søger...")
        self.price_refresh_status.configure(text="")
        threading.Thread(
            target=self._refresh_price_in_background,
            args=(target_record, fields),
            daemon=True,
        ).start()

    def _refresh_price_in_background(self, target_record, fields):
        shop_result, shop_warnings = look_up_product(
            self.app.http_session,
            self.app.price_cache,
            self.app.price_cache_lock,
            fields["mk_varenummer"],
            fields["varenavn"],
            item_part(fields["mk_varenummer"], fields["varenavn"]),
        )
        self.window.after(0, self._apply_price_refresh, target_record, shop_result, shop_warnings)

    def _apply_price_refresh(self, target_record, shop_result, shop_warnings):
        self.price_refresh_in_progress = False
        self.price_refresh_button.configure(state="normal", text="🔄  Genindlæs pris")

        # Always correct the underlying record, even if the review window
        # has already moved on to a different item by the time this
        # returns - see the identical concern in retry_review_in_background.
        if shop_result["varenavn"]:
            target_record["parsed"]["varenavn"] = shop_result["varenavn"]
        target_record["parsed"]["price"] = format_combined_price(
            shop_result["price"], shop_result["compare_price"]
        )
        part = item_part(
            target_record["parsed"].get("mk_varenummer", ""), target_record["parsed"].get("varenavn", "")
        )
        part = part or target_record["parsed"].get("part", "")
        if part:
            target_record["parsed"]["price"] = part_note(part)
        target_record["parsed"]["compare_price"] = shop_result["compare_price"]
        target_record["parsed"]["url"] = shop_result["url"]
        target_record["candidates"] = shop_result["candidates"]
        target_record["warnings"] = [
            w for w in target_record["warnings"] if w[0] not in SHOP_WARNING_CATEGORIES
        ] + shop_warnings

        if self.record is not target_record:
            return

        for key in ("varenavn", "price"):
            self.entries[key].delete(0, tk.END)
            self.entries[key].insert(0, target_record["parsed"].get(key, ""))
            # Now the record's own values - not unsaved edits
            self.loaded_fields[key] = self.entries[key].get().strip()
        self.show_question(target_record["warnings"])
        self.show_candidates(target_record["candidates"])
        if shop_warnings:
            self.price_refresh_status.configure(
                text="Intet præcist match - vælg en vare herover eller ret felterne.",
                foreground=COLOR_WARNING,
            )
        else:
            self.price_refresh_status.configure(text="Pris opdateret.", foreground=COLOR_OK)

    def load_record(self, record):
        """Populate the (already-built) window with a new record in place,
        instead of destroying/recreating the window for each review item."""
        self.record = record
        self.crop_drag_start = None
        self.pending_crop_fraction = None
        self.rotation_angle = 0.0
        self.rotation_slider.set(0)
        self.rotation_label.configure(text="0°")
        self.zoom_box = record["parsed"].get("label_box")
        self.zoomed = self.zoom_box is not None
        self.update_zoom_button()

        # Reset any in-flight-refresh visuals from whatever record was open
        # before - a late result for that old record is a no-op anyway
        # (see _apply_price_refresh's identity check), but the button/status
        # itself needs to reflect *this* record's idle state regardless.
        self.price_refresh_in_progress = False
        self.price_refresh_button.configure(state="normal", text="🔍  Søg på hjemmesiden igen")
        self.price_refresh_status.configure(text="")

        self.raw_text_widget.configure(state="normal")
        self.raw_text_widget.delete("1.0", tk.END)
        self.raw_text_widget.insert("1.0", record.get("raw_text", ""))
        self.raw_text_widget.configure(state="disabled")

        for key, entry in self.entries.items():
            entry.delete(0, tk.END)
            entry.insert(0, record["parsed"].get(key, ""))
        self.loaded_fields = self.read_fields()

        attention = self.show_question(record["warnings"])
        self.show_candidates(record.get("candidates", []))

        self.window.deiconify()
        self.window.lift()
        self.content_canvas.yview_moveto(0)
        if attention:
            entry = self.entries[attention[0]]
            entry.focus_set()
            entry.icursor(tk.END)

        self.show_image()
        self.update_review_count()

    def hide(self):
        self.record = None
        self.window.withdraw()

    def close_window(self):
        """✕ on the batch review: hide, but keep the current item and any
        typed edits, to continue later via "Gennemse labels"."""
        self.window.withdraw()

    def show(self):
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def open_website(self):
        # `url` is set as soon as a product was found at all - matched
        # exactly or not - so this only falls back to a text search when
        # nothing was found on the shop, and does so using the varenavn
        # (the OCR'd/human-corrected name), not the MK-varenummer.
        parsed = self.record["parsed"]
        url = (self.selected_candidate or {}).get("url") or parsed.get("url", "")
        if not url:
            search_value = (
                parsed.get("varenavn") or parsed.get("mk_varenummer") or parsed.get("mk_ordrenr") or ""
            )
            url = f"{SHOP_URL}/search?q={quote(search_value)}" if search_value else SHOP_URL
        webbrowser.open(url)

    def update_review_count(self):
        if self.standalone:
            return
        current = min(self.app.review_completed + 1, self.app.review_total)
        self.review_count.set(f"Gennemgang: {current}/{self.app.review_total}")

    def show_image(self):
        try:
            with Image.open(self.record["path"]) as opened:
                # Phone photos (HEIC/JPEG) commonly carry an EXIF
                # orientation tag rather than storing pixels upright -
                # without correcting for it here, the review window can
                # show (and crop/rotate) the image sideways relative to
                # how the photo actually looks.
                corrected = ImageOps.exif_transpose(opened)
                self.original_image = corrected.convert("RGB")
            # A sideways label was read turned upright (see extract_label) -
            # show it that way, which is also what label_box refers to
            turn = self.record["parsed"].get("photo_turn", 0)
            if turn:
                self.original_image = self.original_image.rotate(turn, expand=True)
            self.working_image = self.record.get("working_image")
            self.resize_image()
        except Exception as error:
            self.image_canvas.delete("all")
            self.image_canvas.create_text(
                20,
                20,
                anchor="nw",
                text=f"Kunne ikke vise billede:\n{error}",
                fill="white",
            )

    def resize_image(self, event=None):
        if self.original_image is None:
            # <Configure> fires as soon as the canvas is created/resized -
            # including before show_image() has loaded anything into
            # original_image (e.g. the initial layout pass right after this
            # window opens). Nothing to draw yet.
            return
        try:
            width = max(self.image_frame.winfo_width() - 24, 100)
            height = max(self.image_frame.winfo_height() - 24, 100)
            image = self.display_base().copy()
            if self.rotation_angle:
                # Shrink the thumbnail target first so the live-rotated
                # preview (whose bounding box grows once it's off a right
                # angle) still comfortably fits the canvas. This is only
                # ever a preview - see build_edited_image for the one place
                # the full-resolution image actually gets rotated.
                margin = 0.75
                image.thumbnail((int(width * margin), int(height * margin)), Image.Resampling.LANCZOS)
                image = image.rotate(
                    -self.rotation_angle,
                    expand=True,
                    resample=Image.BICUBIC,
                    fillcolor=self.canvas_background,
                )
            else:
                image.thumbnail((width, height), Image.Resampling.LANCZOS)
            self.preview = ImageTk.PhotoImage(image)
            canvas_width = self.image_canvas.winfo_width()
            canvas_height = self.image_canvas.winfo_height()
            left = max((canvas_width - image.width) // 2, 0)
            top = max((canvas_height - image.height) // 2, 0)
            self.image_bounds = (left, top, image.width, image.height)
            self.image_canvas.delete("all")
            self.image_canvas.create_image(left, top, image=self.preview, anchor="nw")
            if self.pending_crop_fraction:
                self.show_committed_crop_overlay()
        except Exception as error:
            # Tk swallows exceptions raised in widget callbacks, so a failed
            # redraw would look like a button that does nothing. Show it on
            # the canvas and in the status bar instead.
            debug_log(f"resize_image fejlede: {error!r}")
            self.image_canvas.delete("all")
            self.image_canvas.create_text(
                20,
                20,
                anchor="nw",
                text=f"Kunne ikke opdatere billedvisningen:\n{error}",
                fill="white",
            )

    def display_base(self):
        """The image as shown before rotation: the photo (or its edited
        copy), cut down to the label area while zoomed in."""
        image = self.working_image or self.original_image
        if self.zoomed and self.zoom_box:
            width, height = image.size
            left, top, right, bottom = self.zoom_box
            image = image.crop(
                (round(left * width), round(top * height), round(right * width), round(bottom * height))
            )
        return image

    def read_fields(self):
        values = {}
        for key, widget in self.entries.items():
            if isinstance(widget, tk.Text):
                values[key] = widget.get("1.0", tk.END).strip()
            else:
                values[key] = widget.get().strip()
        return values

    def confirm(self):
        """Save the fields (and a picked suggestion's link) and move on.
        Asks first when the answer still looks unfinished."""
        if self.record is None:
            return
        fields = self.read_fields()
        fields["stand"] = fields["stand"].upper()
        if fields["stand"] not in VALID_STANDS and not messagebox.askyesno(
            "Tjek STAND",
            (f'STAND står som "{fields["stand"]}".' if fields["stand"] else "Der står ingen STAND.")
            + " Den skal normalt være A, B eller C.\n\nGodkend alligevel?",
            parent=self.window,
        ):
            return
        is_part = bool(
            item_part(fields["mk_varenummer"], fields["varenavn"]) or self.record["parsed"].get("part")
        )
        if (
            self.candidates
            and self.selected_candidate is None
            and not fields["price"]
            and not is_part
            and not messagebox.askyesno(
                "Ingen vare valgt",
                "Du har ikke valgt en vare fra hjemmesiden, så der er ingen pris.\n\nGodkend alligevel?",
                parent=self.window,
            )
        ):
            return
        self.record["parsed"].update(fields)
        if self.selected_candidate is not None:
            self.record["parsed"]["url"] = self.selected_candidate["url"]
        self.record["candidates"] = []
        self.record["warnings"] = []
        record = self.record
        self.record = None
        if self.standalone:
            self.app.upsert_result_row(
                record["path"], record["parsed"], record["warnings"], record.get("raw_text", "")
            )
            # A standalone edit doesn't go through review_finished, which
            # is what would otherwise trigger a re-export via
            # maybe_enable_export_controls - so if a file's already been
            # saved once, refresh it here under the same name rather than
            # leaving the correction out of it.
            if self.app.exported:
                self.app.auto_export_excel()
            self.app.open_standalone_windows.pop(id(record), None)
            self.window.destroy()
            return
        self.app.review_finished(record, record)

    def ensure_working_image(self):
        if self.working_image is None:
            self.working_image = self.original_image.copy()
        self.record["working_image"] = self.working_image

    def mirror(self):
        self.ensure_working_image()
        self.working_image = self.working_image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        self.record["working_image"] = self.working_image
        self.pending_crop_fraction = None
        # The label area no longer matches the mirrored photo
        self.zoom_box = None
        self.zoomed = False
        self.update_zoom_button()
        self.resize_image()

    def build_edited_image(self):
        """The image actually sent for re-OCR: base image, rotated to the
        slider's angle and cropped to any pending selection. This is the
        only place a real full-resolution rotate happens - everything
        interactive (dragging the slider, dragging a crop box) works off a
        cheap thumbnail and scale-independent fractions instead."""
        # A crop drawn while zoomed in is relative to the zoomed view; with
        # no crop, the whole photo is re-read (the zoom is only for viewing)
        image = (
            self.display_base() if self.pending_crop_fraction else (self.working_image or self.original_image)
        )
        if self.rotation_angle:
            image = image.rotate(
                -self.rotation_angle,
                expand=True,
                resample=Image.BICUBIC,
                fillcolor=self.canvas_background,
            )
        if self.pending_crop_fraction:
            width, height = image.size
            fx1, fy1, fx2, fy2 = self.pending_crop_fraction
            image = image.crop(
                (
                    round(fx1 * width),
                    round(fy1 * height),
                    round(fx2 * width),
                    round(fy2 * height),
                )
            )
        return image

    def _on_rotation_slider(self, value):
        self.rotation_angle = float(value)
        self.pending_crop_fraction = None
        self.rotation_label.configure(text=f"{round(self.rotation_angle)}°")
        self.resize_image()

    def rotate_quarter_turn(self, delta):
        """Nudge the rotation by a quick +/-90 degrees, wrapping into the
        slider's -180..180 range. Routed through the slider's own .set()
        so it stays perfectly in sync with the slider position and reuses
        the exact same update path (_on_rotation_slider) as a manual drag."""
        new_angle = ((self.rotation_angle + delta + 180) % 360) - 180
        self.rotation_slider.set(new_angle)

    def crop_start_event(self, event):
        if not self.image_bounds:
            return
        self.crop_drag_start = (event.x, event.y)
        self.show_crop_overlay(event.x, event.y, event.x, event.y)

    def crop_drag_event(self, event):
        if self.crop_drag_start:
            self.show_crop_overlay(
                self.crop_drag_start[0],
                self.crop_drag_start[1],
                event.x,
                event.y,
            )

    def crop_release_event(self, event):
        if not self.crop_drag_start or not self.image_bounds:
            self.crop_drag_start = None
            return
        start_x, start_y = self.crop_drag_start
        end_x, end_y = event.x, event.y
        self.crop_drag_start = None
        left, top, display_width, display_height = self.image_bounds
        x1 = max(min(start_x, end_x), left)
        y1 = max(min(start_y, end_y), top)
        x2 = min(max(start_x, end_x), left + display_width)
        y2 = min(max(start_y, end_y), top + display_height)
        if x2 - x1 > 5 and y2 - y1 > 5:
            # Stored as fractions (0-1) of the currently displayed image
            # rather than absolute pixels: a fraction of the rotated
            # bounding box means exactly the same region whether it's
            # measured on the small preview or the full-resolution photo,
            # so there's no scale-factor math (and no chance of it being
            # wrong) between drawing the crop and actually applying it in
            # build_edited_image.
            self.pending_crop_fraction = (
                (x1 - left) / display_width,
                (y1 - top) / display_height,
                (x2 - left) / display_width,
                (y2 - top) / display_height,
            )
        self.resize_image()

    def show_committed_crop_overlay(self):
        left, top, display_width, display_height = self.image_bounds
        fx1, fy1, fx2, fy2 = self.pending_crop_fraction
        self.show_crop_overlay(
            left + fx1 * display_width,
            top + fy1 * display_height,
            left + fx2 * display_width,
            top + fy2 * display_height,
        )

    def show_crop_overlay(self, x1=None, y1=None, x2=None, y2=None):
        if not self.image_bounds:
            return
        left, top, width, height = self.image_bounds
        if x1 is None:
            x1, y1, x2, y2 = left, top, left + width, top + height
        x1, x2 = sorted((max(left, min(x1, left + width)), max(left, min(x2, left + width))))
        y1, y2 = sorted((max(top, min(y1, top + height)), max(top, min(y2, top + height))))
        self.image_canvas.delete("crop-overlay")
        self.image_canvas.create_rectangle(
            left, top, left + width, y1, fill="#000000", stipple="gray50", outline="", tags="crop-overlay"
        )
        self.image_canvas.create_rectangle(
            left,
            y2,
            left + width,
            top + height,
            fill="#000000",
            stipple="gray50",
            outline="",
            tags="crop-overlay",
        )
        self.image_canvas.create_rectangle(
            left, y1, x1, y2, fill="#000000", stipple="gray50", outline="", tags="crop-overlay"
        )
        self.image_canvas.create_rectangle(
            x2, y1, left + width, y2, fill="#000000", stipple="gray50", outline="", tags="crop-overlay"
        )
        self.image_canvas.create_rectangle(x1, y1, x2, y2, outline="#ffffff", width=2, tags="crop-overlay")

    def reset_image(self):
        self.working_image = None
        self.record.pop("working_image", None)
        self.pending_crop_fraction = None
        self.zoom_box = self.record["parsed"].get("label_box")
        self.zoomed = self.zoom_box is not None
        self.update_zoom_button()
        self.rotation_angle = 0.0
        self.rotation_slider.set(0)
        self.rotation_label.configure(text="0°")
        self.resize_image()

    def retry(self):
        if self.record is None:
            return
        if self.fields_changed() and not messagebox.askokcancel(
            "Læs billedet igen?",
            "Dine rettelser i felterne bliver overskrevet, når billedet læses igen.",
            parent=self.window,
        ):
            return
        record = self.record
        working_image = self.build_edited_image().copy()
        self.record = None
        self.pending_crop_fraction = None
        standalone = self.standalone
        if standalone:
            # Fire-and-forget, same as the live-queue path below: OCR
            # keeps running in the background and the row updates in
            # place once it's done (see retry_review/show_retry_result) -
            # there's no "next item" to move on to here, so just close.
            self.window.destroy()
        self.app.retry_review(record, working_image, standalone=standalone)

    def skip(self):
        """Move on without saving - but never silently throw away edits:
        if any field was changed, ask whether to keep them."""
        if self.fields_changed():
            answer = messagebox.askyesnocancel(
                "Gem ændringerne?",
                "Du har rettet i felterne. Vil du gemme ændringerne?",
                parent=self.window,
            )
            if answer is None:
                return
            if answer:
                self.confirm()
                return
        record = self.record
        self.record = None
        if self.standalone:
            self.app.open_standalone_windows.pop(id(record), None)
            self.window.destroy()
            return
        self.app.review_finished(record, None)
