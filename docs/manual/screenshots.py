"""Takes the screenshots for the Label Miner manual by driving the real app.

Starts Label Miner on a demo batch of the fixture photos, opens the review
window on each kind of question and captures the windows. OCR and webshop
answers are replayed from the test fixtures (see demo_data.py), so no Docling
is needed and the result is the same every time; only the product photos in
the review window are fetched live. Numbered callouts are positioned from the
live widget geometry, so they stay correct when the layout changes. Used by
make_manual.py; can also be run on its own:

    python docs/manual/screenshots.py

Windows only. Nothing outside its own temp folder is written or deleted (the
app's clean-up of old temp folders is switched off here).
"""

import ctypes
import shutil
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import demo_data  # noqa: E402  (sets up the import paths)
import demo_sheet  # noqa: E402
from capture import annotate, grab_window  # noqa: E402

from label_miner.ocr import load_label_photo  # noqa: E402
from label_miner.ui import app as app_module  # noqa: E402
from label_miner.ui import review_window  # noqa: E402
from label_miner.ui.theme import apply_theme  # noqa: E402

SHOTS = demo_data.BUILD / "manual-screenshots"

# The demo batch: every kind of review question once, plus labels that are
# approved on their own. (file, what it shows)
DEMO = [
    ("24-9/IMG_0302.HEIC", "number and name disagree"),
    ("24-9/IMG_0276.HEIC", "auto"),
    ("24-9/IMG_0283.HEIC", "part of an item"),
    ("15-9/IMG_9161.HEIC", "auto"),
    ("24-9/IMG_0292.HEIC", "pick the right variant"),
    ("24-9/IMG_0268.HEIC", "auto"),
    ("24-9/IMG_0296.HEIC", "not on the webshop"),
    ("24-9/IMG_0289.HEIC", "auto"),
    ("24-9/IMG_0275.HEIC", "found via the name"),
    ("24-9/IMG_0280.HEIC", "auto"),
    ("24-9/IMG_0286.HEIC", "STAND missing"),
    ("24-9/IMG_0267.HEIC", "auto"),
    ("15-9/IMG_9176.HEIC", "odd STAND"),
    ("24-9/IMG_0285.HEIC", "sideways, part of an item"),
]


class Session:
    def __init__(self):
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        converter = demo_data.converter()
        app_module.LabelMinerApp.load_docling = lambda app: setattr(app, "converter", converter)
        app_module.clean_up_old_extract_dirs = lambda *args: []  # leave %TEMP% alone
        for name in ("askyesno", "askyesnocancel", "askokcancel"):
            setattr(review_window.messagebox, name, lambda *args, **kwargs: True)

        self.root = app_module.TkinterDnD.Tk() if app_module.TkinterDnD is not None else tk.Tk()
        self.app = app_module.LabelMinerApp(self.root, theme_applied=apply_theme(self.root))
        self.app.http_session = demo_data.ReplaySession()
        self.scale = self.root.winfo_fpixels("1i") / 96
        self.root.geometry(f"{round(1000 * self.scale)}x{round(640 * self.scale)}+30+30")
        self.batch_dir = Path(tempfile.mkdtemp(prefix="label_miner_manual_")) / "24-9"
        self.batch_dir.mkdir()

    def shot(self, window=None):
        img, self.origin = grab_window(window or self.root)
        return img

    def box(self, widget, pad=4):
        """The widget's box in the last screenshot, with some padding."""
        x = widget.winfo_rootx() - self.origin[0]
        y = widget.winfo_rooty() - self.origin[1]
        p = round(pad * self.scale)
        return (x - p, y - p, x + widget.winfo_width() + p, y + widget.winfo_height() + p)

    @staticmethod
    def find(root, text):
        """First widget whose text is ``text``."""
        todo = [root]
        while todo:
            widget = todo.pop(0)
            try:
                if widget.cget("text") == text:
                    return widget
            except tk.TclError:
                pass
            todo.extend(widget.winfo_children())
        raise LookupError(text)

    def close(self):
        self.root.destroy()
        shutil.rmtree(self.batch_dir.parent, ignore_errors=True)  # only our own temp folder


def left_of(box, distance):
    return (box[0] - distance, (box[1] + box[3]) // 2)


def right_of(box, distance):
    return (box[2] + distance, (box[1] + box[3]) // 2)


def above(box, distance):
    return ((box[0] + box[2]) // 2, box[1] - distance)


def below(box, distance):
    return ((box[0] + box[2]) // 2, box[3] + distance)


def drive(root, steps, timeout=300):
    """Run a generator of steps inside Tk's main loop (the app's worker
    threads report back with root.after, which needs a running main loop).
    A step yields either milliseconds to wait or a condition to wait for."""
    errors = []

    def fail(*exc):
        errors.append(exc[1])
        root.quit()

    root.report_callback_exception = fail

    def step():
        try:
            wait = next(steps)
        except StopIteration:
            root.quit()
            return
        if callable(wait):
            started = time.time()

            def poll():
                if wait():
                    step()
                elif time.time() - started > timeout:
                    fail(None, TimeoutError("the app didn't get there in time"), None)
                else:
                    root.after(50, poll)

            poll()
        else:
            root.after(int(wait), step)

    root.after(100, step)
    root.mainloop()
    if errors:
        raise errors[0]


def take_all():
    """Capture every screenshot; returns ({name: Path}, export rows)."""
    SHOTS.mkdir(parents=True, exist_ok=True)
    out = {}
    result = {}
    s = Session()
    try:
        drive(s.root, steps(s, out, result))
    finally:
        s.close()
    return out, result["rows"]


def steps(s, out, result):
    app, sc = s.app, s.scale

    def d(value):
        return round(value * sc)

    def save(name, img):
        path = SHOTS / f"{name}.png"
        img.save(path)
        out[name] = path

    app.docling_ready()
    yield 800

    # 1. Start screen ---------------------------------------------------------
    img = s.shot()
    button = s.box(app.select_button)
    hint = s.box(s.find(app.select_frame, "...eller træk billeder eller en ZIP-fil ind i vinduet"))
    help_button = s.box(s.find(app.select_frame, "?  Hjælp"))
    save(
        "01_start",
        annotate(
            img,
            scale=sc,
            boxes=[button],
            circles=[
                (1, left_of(button, d(30))),
                (2, left_of(hint, d(30))),
                (3, left_of(help_button, d(26))),
            ],
        ),
    )

    # 2. Reading: taken while the batch is still being worked on ---------------
    for name, _ in DEMO:
        shutil.copy(demo_data.PHOTOS / name, s.batch_dir / Path(name).name)
    app.begin_processing(sorted(s.batch_dir.iterdir()))
    yield lambda: app.work_completed >= 5
    img = s.shot()
    progress = s.box(app.progress_frame)
    tree = s.box(app.results_tree, pad=0)
    save(
        "02_laeser",
        annotate(
            img,
            scale=sc,
            boxes=[progress],
            circles=[(1, right_of(progress, d(-30))), (2, (tree[2] - d(60), tree[1] + d(90)))],
        ),
    )

    # 3. Batch read, reviews waiting -------------------------------------------
    yield lambda: not app.processing and app.work_completed >= len(DEMO)
    yield 800
    img = s.shot()
    save("readme_main_window", img)  # without callouts, for the README
    tree = s.box(app.results_tree, pad=0)
    review_button = s.box(app.review_button)
    status_col_x = tree[0] + d(250)
    help_button = s.box(s.find(app.work_frame, "?  Hjælp"))
    review_hint = s.box(s.find(app.review_bar, "Nogle labels skal tjekkes, før de kan eksporteres."))
    save(
        "03_liste",
        annotate(
            img,
            scale=sc,
            boxes=[review_button],
            circles=[
                (1, (tree[2] - d(60), tree[1] + d(120))),
                (2, (status_col_x, tree[1] + d(10))),
                (3, right_of(review_hint, d(26))),
                (4, below(help_button, d(20))),
            ],
        ),
    )

    # 4-10. The review window on each kind of question ---------------------------
    app.open_review()
    yield 300
    window = app.review_window
    window.window.geometry(f"{d(1180)}x{d(780)}+40+40")
    records = {record["path"].name: record for record in app.records}
    shots = {}

    def review(name, select=None, tools=False):
        window.load_record(records[name])
        if tools != window.tools_visible:
            window.toggle_tools()
        yield 2500  # the product photos on the cards load in the background
        if select is not None:
            window.select_candidate(select)
            yield 300
        shots["img"] = s.shot(window.window)

    def panel(img):
        """Only the right-hand panel (question, choices, fields, buttons) -
        readable at manual size, unlike the whole window. Leaves room on the
        left for the callout circles."""
        x0 = max(0, s.box(window.question_frame)[0] - d(50))
        return img.crop((x0, 0, img.width, img.height))

    yield from review("IMG_0302.HEIC")
    img = shots["img"]
    save("readme_review_window", img)
    question = s.box(window.question_frame)
    cards = s.box(window.choices_frame)
    fields = s.box(window.entry_labels["mk_varenummer"][0])
    approve = s.box(s.find(window.window, "✓  Godkend og næste"))
    skip = s.box(s.find(window.window, "Spring over – ret senere"))
    web = s.box(s.find(window.window, "Åbn hjemmeside"))
    photo = s.box(window.image_canvas, pad=0)
    photo_buttons = s.box(window.zoom_button.master)
    save(
        "04_gennemse",
        annotate(
            img,
            scale=sc,
            boxes=[question, cards, approve],
            circles=[
                (1, left_of(question, d(22))),
                (2, left_of(cards, d(22))),
                (3, left_of(fields, d(22))),
                (4, left_of(approve, d(22))),
                (5, left_of(skip, d(22))),
                (6, right_of(web, d(-26))),
                (7, (photo[0] + d(40), photo[1] + d(40))),
                (8, above(photo_buttons, d(18))),
            ],
        ),
    )

    yield from review("IMG_0292.HEIC", select=2)
    # Scroll so the chosen card and the price field are both in view
    content = window.content_canvas
    total = content.bbox("all")[3]
    price = window.entries["price"]
    price_bottom = price.winfo_rooty() + price.winfo_height() - content.winfo_rooty() + content.canvasy(0)
    content.yview_moveto(max(0.0, (price_bottom + d(12) - content.winfo_height()) / total))
    yield 300
    img = s.shot(window.window)
    chosen = s.box(window.candidate_cards[2]["frame"])
    name_field = s.box(window.entries["varenavn"])
    price_field = s.box(window.entries["price"])
    save(
        "05_valgt",
        panel(
            annotate(
                img,
                scale=sc,
                boxes=[chosen, name_field, price_field],
                circles=[
                    (1, left_of(chosen, d(22))),
                    (2, left_of(name_field, d(22))),
                    (3, left_of(price_field, d(22))),
                ],
            )
        ),
    )

    yield from review("IMG_0283.HEIC")
    img = shots["img"]
    question = s.box(window.question_frame)
    card = s.box(window.candidate_cards[0]["frame"])
    note = s.box(window.entries["price"])
    save(
        "06_delvis",
        panel(
            annotate(
                img,
                scale=sc,
                boxes=[question, card, note],
                circles=[(1, left_of(question, d(22))), (2, left_of(card, d(22))), (3, left_of(note, d(22)))],
            )
        ),
    )

    yield from review("IMG_0296.HEIC")
    img = shots["img"]
    question = s.box(window.question_frame)
    price_label = s.box(window.entry_labels["price"][0])
    save(
        "07_ikke_fundet",
        panel(
            annotate(
                img,
                scale=sc,
                boxes=[question],
                circles=[(1, left_of(question, d(22))), (2, left_of(price_label, d(22)))],
            )
        ),
    )

    yield from review("IMG_9176.HEIC")
    img = shots["img"]
    question = s.box(window.question_frame)
    stand_field = s.box(window.entries["stand"])
    save(
        "08_stand",
        panel(
            annotate(
                img,
                scale=sc,
                boxes=[question, stand_field],
                circles=[(1, left_of(question, d(22))), (2, left_of(stand_field, d(22)))],
            )
        ),
    )

    yield from review("IMG_0275.HEIC")
    img = shots["img"]
    question = s.box(window.question_frame)
    card = s.box(window.candidate_cards[0]["frame"])
    save(
        "09_via_navn",
        panel(
            annotate(
                img,
                scale=sc,
                boxes=[question, card],
                circles=[(1, left_of(question, d(22))), (2, left_of(card, d(22)))],
            )
        ),
    )

    yield from review("IMG_0285.HEIC", tools=True)
    img = shots["img"]
    rotate = s.box(s.find(window.window, "⟲ Drej"))
    zoom = s.box(window.zoom_button)
    reread = s.box(s.find(window.window, "↻ Læs billedet igen"))
    slider = s.box(window.rotation_slider)
    mirror = s.box(s.find(window.window, "Spejl"))
    raw = s.box(window.raw_text_widget)
    save(
        "10_billede",
        annotate(
            img,
            scale=sc,
            boxes=[rotate, zoom, reread],
            circles=[
                (1, above(rotate, d(18))),
                (2, above(zoom, d(18))),
                (3, above(reread, d(18))),
                (4, above(slider, d(10))),
                (5, left_of(mirror, d(-30))),
                (6, right_of(raw, d(-30))),
            ],
        ),
    )
    window.toggle_tools()

    # 11. All reviewed -> exported ----------------------------------------------
    for record in app.records:
        if record.get("review_pending"):
            record["warnings"] = []
            app.review_finished(record, record)
    window.hide()
    yield lambda: app.exported
    yield 800
    img = s.shot()
    ready = s.box(app.export_ready_label)
    copy = s.box(app.copy_button)
    save(
        "11_eksport",
        annotate(
            img,
            scale=sc,
            boxes=[ready, copy],
            circles=[(1, right_of(ready, d(30))), (2, right_of(copy, d(30)))],
        ),
    )
    export_path = app.export_path

    # 12. Double-click a row: "Rediger label" -------------------------------------
    editor = review_window.ReviewWindow(app, records["IMG_0276.HEIC"], standalone=True)
    editor.window.geometry(f"{d(1180)}x{d(780)}+60+60")
    yield 2500
    img = s.shot(editor.window)
    save_button = s.box(s.find(editor.window, "✓  Gem ændringer"))
    close = s.box(s.find(editor.window, "Luk"))
    save(
        "12_rediger",
        annotate(
            img,
            scale=sc,
            boxes=[save_button],
            circles=[(1, left_of(save_button, d(22))), (2, left_of(close, d(22)))],
        ),
    )
    editor.window.destroy()

    # 13. A label photo for the front page -----------------------------------------
    photo = load_label_photo(demo_data.PHOTOS / "24-9" / "IMG_0302.HEIC")
    width, height = photo.size
    front = photo.crop((0, round(height * 0.12), width, round(height * 0.72)))
    front.thumbnail((1400, 1400), Image.Resampling.LANCZOS)
    save("13_label", front)

    # 14. The shop's sheet after pasting: drawn from demo rows (see demo_sheet.py).
    # Label Miner leaves Varenummer (A) and Pris pr stk (I) empty.
    rows = [("", *row[1:7], "") for row in demo_sheet.DEMO_ROWS]
    sheet = demo_sheet.render(rows, batch="24-9", selected=False, red_without_price=False, batch_price="")
    last_row = 2 + len(rows)
    batch_row = sheet.cell(0, 2)
    filled = sheet.cell(1, 3, 7, last_row)
    column_a = sheet.cell(0, 3, 0, last_row)
    column_i = sheet.cell(8, 3, 8, last_row)
    save(
        "14_google_sheet",
        annotate(
            sheet.image,
            scale=1.1,
            boxes=[batch_row, filled, column_a, column_i],
            circles=[
                (1, right_of(batch_row, 22)),
                (2, (filled[2] - 40, filled[3] - 20)),
                (3, ((column_a[0] + column_a[2]) // 2, column_a[1] + 40)),
                (3, ((column_i[0] + column_i[2]) // 2, column_i[1] + 40)),
            ],
        ),
    )

    # The exported spreadsheet, for the example table in the manual
    from openpyxl import load_workbook

    result["rows"] = [list(row) for row in load_workbook(export_path).active.iter_rows(values_only=True)]


if __name__ == "__main__":
    shots, rows = take_all()
    for name, path in shots.items():
        print(name, path)
    print(len(rows), "export rows")
