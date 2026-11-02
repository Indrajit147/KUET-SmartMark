r"""SmartMark - teacher app (Milestone 7).

    .venv\Scripts\python.exe scripts\smartmark_app.py      (or SmartMark.exe after build_exe.bat)

1  Scan photos     choose the folder of cover photos, press Scan (about 1 s per photo); live progress and counts.
2  Check flagged   each doubtful item: the photo of what was written, what was read, how sure, and why.
                   Type the right value + Enter, or Keep / Blank. Up/Down = previous/next; click any item to jump.
3  All covers      every cover with its status; View shows the whole page with what was read.
4  Export Excel    applies the answers and writes a new workbook in output\ (Open workbook / Open folder).
Answers are saved as you go (reports\review_answers.json), so closing the app loses nothing.
Every correction is logged in reports\teacher_corrections.csv for retraining the model later.
"""
from __future__ import annotations

import csv
import json
import os
import queue
import re
import threading
import time
from importlib import import_module
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from PIL import Image

from common import PREVIEW_DIR, REPORT_DIR, ROOT

reader = import_module("09_read_sheets")
excel = import_module("10_fill_excel")

PROCESSED = ROOT / "data" / "processed"
ANSWERS = REPORT_DIR / "review_answers.json"
CORRECTIONS = REPORT_DIR / "teacher_corrections.csv"
MAX_MARK = 35


# ------------------------------------------------------------------ review logic (no GUI - tested on its own)
def _read(name):
    with (REPORT_DIR / name).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _write(name, rows, fields):
    with (REPORT_DIR / name).open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def reason_kind(reason: str) -> str:
    return "roll" if "roll" in reason else ("section" if "section letter" in reason else "sheet")


def load_items() -> list[dict]:
    """Everything the teacher must look at: flagged cells, unsure roll numbers / section letters, odd pages."""
    items = []
    for s in _read("m5_sheets.csv"):
        if s["status"] == "fail":
            continue  # cannot be fixed on screen - listed as 'retake' in the summary
        read_dir = PROCESSED / s["scan_id"] / "read"
        for kind in ("roll", "section", "sheet"):
            why = [r for r in s["reasons"].split("; ") if r and reason_kind(r) == kind]
            if not why:
                continue
            images = {"roll": [read_dir / "roll_row.png", read_dir / "header_roll.png"],
                      "section": [read_dir / "letter.png"],
                      "sheet": [PREVIEW_DIR / "m5" / f"{s['scan_id']}.jpg"]}[kind]
            read = {"roll": s["roll_no"], "section": s["section"], "sheet": ""}[kind]
            items.append({"kind": kind, "scan_id": s["scan_id"], "cell": kind, "read": read, "conf": "",
                          "why": "; ".join(why), "images": [str(p) for p in images], "answer": None})
    for c in _read("m5_cells.csv"):
        if c["flag"] and c["cell"].startswith("q"):
            items.append({"kind": "cell", "scan_id": c["scan_id"], "cell": c["cell"], "read": c["value"],
                          "conf": c["confidence"], "why": c["flag"],
                          "images": [str(PROCESSED / c["scan_id"] / "read" / f"{c['cell']}.png")], "answer": None})
    saved = json.loads(ANSWERS.read_text(encoding="utf-8")) if ANSWERS.exists() else {}
    for it in items:
        it["answer"] = saved.get(f"{it['scan_id']}|{it['cell']}|{it['read']}")
    return items


def save_answers(items: list[dict]) -> None:
    ANSWERS.write_text(json.dumps({f"{i['scan_id']}|{i['cell']}|{i['read']}": i["answer"] for i in items
                                   if i["answer"] is not None}, indent=1), encoding="utf-8")


def validate(kind: str, text: str) -> tuple[str | None, str]:
    """Teacher input -> (clean value, '') or (None, error message)."""
    t = text.strip()
    if kind == "cell":
        if t == "":
            return "", ""
        if t.isdigit() and int(t) <= MAX_MARK:
            return str(int(t)), ""
        return None, f"Type a mark from 0 to {MAX_MARK}, or leave it empty for a blank cell."
    if kind == "roll":
        return (t, "") if re.fullmatch(r"\d{7}", t) else (None, "A roll number has exactly 7 digits.")
    if kind == "section":
        return (t.upper(), "") if t.upper() in ("A", "B") else (None, "Type A or B.")
    return "ok", ""  # 'sheet': looking at it and pressing Enter confirms the page was read properly


def apply_answers(items: list[dict]) -> int:
    """Write the teacher's answers into the reports (checked items stop being flagged). Returns how many."""
    sheets, cells = _read("m5_sheets.csv"), _read("m5_cells.csv")
    by_sheet = {s["scan_id"]: s for s in sheets}
    by_cell = {(c["scan_id"], c["cell"]): c for c in cells}
    log, done = [], 0
    for it in items:
        if it["answer"] is None:
            continue
        done += 1
        s = by_sheet[it["scan_id"]]
        if it["kind"] == "cell":
            c = by_cell[(it["scan_id"], it["cell"])]
            c.update(value=it["answer"], confidence="1.000", flag="", note="checked by teacher")
            s[it["cell"]] = it["answer"]
        else:
            if it["kind"] == "roll":
                s["roll_no"] = it["answer"]
            elif it["kind"] == "section":
                s["section"] = it["answer"]
            s["reasons"] = "; ".join(r for r in s["reasons"].split("; ") if r and reason_kind(r) != it["kind"])
        if it["answer"] != it["read"] and it["kind"] != "sheet":
            log.append({"time": time.strftime("%Y-%m-%d %H:%M"), "scan_id": it["scan_id"], "item": it["cell"],
                        "read": it["read"], "corrected": it["answer"], "crop": it["images"][0]})
    for s in sheets:
        s["flagged_cells"] = " ".join(c["cell"] for c in cells if c["scan_id"] == s["scan_id"] and c["flag"])
        if s["status"] != "fail":
            s["status"] = "review" if s["flagged_cells"] or s["reasons"] else "ok"
    _write("m5_sheets.csv", sheets, list(sheets[0]))
    _write("m5_cells.csv", cells, list(cells[0]))
    if log:
        new = not CORRECTIONS.exists()
        with CORRECTIONS.open("a", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(log[0]))
            if new:
                w.writeheader()
            w.writerows(log)
    return done


# ------------------------------------------------------------------ window
OK, REVIEW, FAIL = "#2e9d5b", "#d98e04", "#d64545"
CHIP = {"ok": OK, "review": REVIEW, "fail": FAIL}


def conf_colour(conf: str) -> str:
    try:
        c = float(conf)
    except ValueError:
        return REVIEW
    return OK if c >= 0.8 else (REVIEW if c >= 0.5 else FAIL)


def ctk_image(path: str, box: tuple[int, int], upscale: float = 4.0):
    """A CTkImage scaled to fit `box` (small crops are enlarged up to `upscale` x)."""
    im = Image.open(path)
    k = min(box[0] / im.width, box[1] / im.height, upscale)
    return ctk.CTkImage(light_image=im, dark_image=im, size=(max(1, int(im.width * k)), max(1, int(im.height * k))))


class PageViewer(ctk.CTkToplevel):
    """Pop-up showing a whole cover with what was read (orange boxes = flagged)."""
    def __init__(self, master, sid: str):
        super().__init__(master)
        self.title(f"Cover {sid} - what SmartMark read")
        self.geometry("760x900")
        path = PREVIEW_DIR / "m5" / f"{sid}.jpg"
        if path.exists():
            ctk.CTkLabel(self, text="", image=ctk_image(str(path), (720, 840), 1.0)).pack(padx=16, pady=16)
        else:
            ctk.CTkLabel(self, text="No preview for this cover (the photo was not recognised).").pack(padx=30, pady=30)
        self.after(100, self.lift)


class SmartMarkApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("SmartMark - KUET answer-script reader")
        self.geometry("1280x800")
        self.minsize(1100, 700)
        self.q: queue.Queue = queue.Queue()
        self.items: list[dict] = []
        self.i = 0
        self.book: Path | None = None
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.f_big = ctk.CTkFont(size=28, weight="bold")
        self.f_title = ctk.CTkFont(size=22, weight="bold")
        self.f_head = ctk.CTkFont(size=15, weight="bold")

        side = ctk.CTkFrame(self, width=230, corner_radius=0)
        side.grid(row=0, column=0, sticky="nsw")
        side.grid_rowconfigure(6, weight=1)
        ctk.CTkLabel(side, text="SmartMark", font=ctk.CTkFont(size=26, weight="bold")).grid(row=0, column=0, padx=24, pady=(28, 0), sticky="w")
        ctk.CTkLabel(side, text="Marks from cover photos\nstraight into Excel", justify="left",
                     text_color=("gray35", "gray70")).grid(row=1, column=0, padx=24, pady=(2, 24), sticky="w")
        self.nav = {}
        for r, (key, label) in enumerate([("scan", "1   Scan photos"), ("review", "2   Check flagged"),
                                          ("covers", "3   All covers"), ("export", "4   Export Excel")], start=2):
            b = ctk.CTkButton(side, text=label, anchor="w", height=40, corner_radius=8, fg_color="transparent",
                              text_color=("gray10", "gray90"), hover_color=("gray80", "gray28"),
                              font=ctk.CTkFont(size=14), command=lambda k=key: self.show(k))
            b.grid(row=r, column=0, padx=14, pady=3, sticky="ew")
            self.nav[key] = b
        ctk.CTkLabel(side, text="Appearance").grid(row=7, column=0, padx=24, sticky="w")
        ctk.CTkOptionMenu(side, values=["System", "Light", "Dark"], width=180,
                          command=lambda m: ctk.set_appearance_mode(m)).grid(row=8, column=0, padx=24, pady=(4, 24))

        self.pages = {k: ctk.CTkFrame(self, fg_color="transparent") for k in self.nav}
        for p in self.pages.values():
            p.grid(row=0, column=1, sticky="nsew", padx=28, pady=24)
        self._build_scan()
        self._build_review()
        self._build_covers()
        self._build_export()
        self.bind("<Return>", lambda e: self.save() if self.current == "review" else None)
        self.bind("<Up>", lambda e: self.move(-1) if self.current == "review" else None)
        self.bind("<Down>", lambda e: self.move(1) if self.current == "review" else None)
        self.current = ""
        self.show("scan")
        self.after(200, self.load_last)

    # ---------------------------------------------------------------- navigation
    def show(self, key: str):
        self.pages[key].tkraise()
        self.current = key
        for k, b in self.nav.items():
            b.configure(fg_color=(("gray78", "gray25") if k == key else "transparent"))
        if key == "review":
            self.render_item()
        elif key == "covers":
            self.render_covers()
        elif key == "export":
            self.render_export()

    def stat_card(self, parent, col, title, colour):
        card = ctk.CTkFrame(parent, corner_radius=14)
        card.grid(row=0, column=col, padx=(0 if col == 0 else 12, 0), sticky="ew")
        parent.grid_columnconfigure(col, weight=1)
        num = ctk.CTkLabel(card, text="-", font=ctk.CTkFont(size=34, weight="bold"), text_color=colour)
        num.pack(anchor="w", padx=18, pady=(14, 0))
        ctk.CTkLabel(card, text=title, text_color=("gray35", "gray70")).pack(anchor="w", padx=18, pady=(0, 14))
        return num

    # ---------------------------------------------------------------- 1. scan
    def _build_scan(self):
        p = self.pages["scan"]
        ctk.CTkLabel(p, text="Scan cover photos", font=self.f_title).pack(anchor="w")
        ctk.CTkLabel(p, text="Choose the folder with the photos of the answer-script covers, then press Scan.",
                     text_color=("gray35", "gray70")).pack(anchor="w", pady=(2, 16))
        card = ctk.CTkFrame(p, corner_radius=14)
        card.pack(fill="x")
        card.grid_columnconfigure(0, weight=1)
        raw = ROOT / "data" / "raw"
        self.folder = ctk.StringVar(value=str(raw if raw.exists() else Path.home() / "Pictures"))
        ctk.CTkEntry(card, textvariable=self.folder, height=38).grid(row=0, column=0, padx=(18, 8), pady=18, sticky="ew")
        ctk.CTkButton(card, text="Browse...", width=110, height=38, fg_color=("gray70", "gray30"),
                      hover_color=("gray60", "gray35"), text_color=("gray10", "gray90"),
                      command=self.browse).grid(row=0, column=1, padx=4)
        self.scan_btn = ctk.CTkButton(card, text="Scan", width=150, height=38, font=self.f_head, command=self.scan)
        self.scan_btn.grid(row=0, column=2, padx=(4, 18))
        self.bar = ctk.CTkProgressBar(p, height=12)
        self.bar.set(0)
        self.bar.pack(fill="x", pady=(18, 4))
        self.scan_msg = ctk.StringVar(value="Ready.")
        ctk.CTkLabel(p, textvariable=self.scan_msg, text_color=("gray35", "gray70")).pack(anchor="w")
        cards = ctk.CTkFrame(p, fg_color="transparent")
        cards.pack(fill="x", pady=16)
        self.n_total = self.stat_card(cards, 0, "covers scanned", ("gray10", "gray90"))
        self.n_ok = self.stat_card(cards, 1, "read cleanly", OK)
        self.n_check = self.stat_card(cards, 2, "items to check", REVIEW)
        self.n_fail = self.stat_card(cards, 3, "photos to retake", FAIL)
        self.log = ctk.CTkTextbox(p, corner_radius=14, font=ctk.CTkFont(family="Consolas", size=12))
        self.log.pack(fill="both", expand=True)
        for tag, colour in CHIP.items():
            self.log.tag_config(tag, foreground=colour)
        self.next_btn = ctk.CTkButton(p, text="Check flagged items  >", height=40, font=self.f_head,
                                      command=lambda: self.show("review"))

    def browse(self):
        d = filedialog.askdirectory(initialdir=self.folder.get())
        if d:
            self.folder.set(d)

    def scan(self):
        files, ids = reader.collect([self.folder.get()])
        if not files:
            messagebox.showwarning("SmartMark", "No photos (JPG / PNG / ...) in that folder.")
            return
        ANSWERS.unlink(missing_ok=True)  # a new batch starts with no answers
        self.scan_btn.configure(state="disabled", text="Scanning...")
        self.log.delete("1.0", "end")
        self.next_btn.pack_forget()
        self.bar.set(0)
        threading.Thread(target=self._scan_worker, args=(files, ids), daemon=True).start()
        self.after(150, self._poll)

    def _scan_worker(self, files, ids):
        try:
            reader.run(files, ids, progress=lambda i, n, line: self.q.put(("step", i, n, line)))
            self.q.put(("done",))
        except BaseException as exc:  # shown to the teacher instead of a silent crash
            self.q.put(("error", f"{type(exc).__name__}: {exc}"))

    def _poll(self):
        while not self.q.empty():
            msg = self.q.get()
            if msg[0] == "step":
                _, i, n, line = msg
                self.bar.set(i / n)
                self.scan_msg.set(f"Reading photo {i} of {n} ...")
                status = line.split()[1] if len(line.split()) > 1 else ""
                self.log.insert("end", line.split("(")[0].rstrip() + "\n", status if status in CHIP else "")
                self.log.see("end")
                continue
            self.scan_btn.configure(state="normal", text="Scan")
            if msg[0] == "error":
                self.scan_msg.set("Scan stopped.")
                messagebox.showerror("SmartMark", f"Scan stopped:\n{msg[1]}")
                return
            self.load_last(after_scan=True)
            return
        self.after(150, self._poll)

    def load_last(self, after_scan: bool = False):
        """Fill the dashboard and review list from the last scan's reports."""
        try:
            sheets = _read("m5_sheets.csv")
            self.items = load_items()
        except FileNotFoundError:
            return
        self.n_total.configure(text=str(len(sheets)))
        self.n_ok.configure(text=str(sum(s["status"] == "ok" for s in sheets)))
        self.n_check.configure(text=str(sum(i["answer"] is None for i in self.items)))
        self.n_fail.configure(text=str(sum(s["status"] == "fail" for s in sheets)))
        self.bar.set(1)
        left = sum(i["answer"] is None for i in self.items)
        self.scan_msg.set(("Scan finished. " if after_scan else "Last scan loaded. ")
                          + (f"{left} item(s) need a look." if left else "Nothing to check - go to Export."))
        if not self.log.get("1.0", "end").strip():
            for s in sheets:
                line = f"{s['scan_id']}: {s['status']:6s} section {s['section'] or '?'} roll {s['roll_no'] or '?':7s} {s['reasons']}"
                self.log.insert("end", line + "\n", s["status"])
        self.next_btn.pack(anchor="e", pady=(12, 0))
        self.i = next((k for k, it in enumerate(self.items) if it["answer"] is None), 0)
        self.refresh_list()

    # ---------------------------------------------------------------- 2. review
    def _build_review(self):
        p = self.pages["review"]
        p.grid_columnconfigure(0, weight=3)
        p.grid_columnconfigure(1, weight=2)
        p.grid_rowconfigure(2, weight=1)
        head = ctk.CTkFrame(p, fg_color="transparent")
        head.grid(row=0, column=0, columnspan=2, sticky="ew")
        ctk.CTkLabel(head, text="Check flagged items", font=self.f_title).pack(side="left")
        self.done_lbl = ctk.CTkLabel(head, text="", text_color=("gray35", "gray70"))
        self.done_lbl.pack(side="right")
        self.done_bar = ctk.CTkProgressBar(p, height=10, progress_color=OK)
        self.done_bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 16))
        self.done_bar.set(0)

        left = ctk.CTkFrame(p, corner_radius=14)
        left.grid(row=2, column=0, sticky="nsew", padx=(0, 16))
        self.crop_box = ctk.CTkFrame(left, fg_color="transparent")
        self.crop_box.pack(fill="both", expand=True, padx=18, pady=18)
        self.page_btn = ctk.CTkButton(left, text="Show the whole cover", fg_color="transparent", border_width=1,
                                      text_color=("gray10", "gray90"), command=self.open_page)
        self.page_btn.pack(anchor="w", padx=18, pady=(0, 18))

        right = ctk.CTkFrame(p, fg_color="transparent")
        right.grid(row=2, column=1, sticky="nsew")
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)
        info = ctk.CTkFrame(right, corner_radius=14)
        info.grid(row=0, column=0, sticky="ew")
        self.where = ctk.CTkLabel(info, text="", font=self.f_head, anchor="w")
        self.where.pack(fill="x", padx=18, pady=(16, 2))
        row = ctk.CTkFrame(info, fg_color="transparent")
        row.pack(fill="x", padx=18)
        ctk.CTkLabel(row, text="Read as", text_color=("gray35", "gray70")).pack(side="left")
        self.read_lbl = ctk.CTkLabel(row, text="", font=self.f_big)
        self.read_lbl.pack(side="left", padx=12)
        self.conf_chip = ctk.CTkLabel(row, text="", corner_radius=10, text_color="white", width=110)
        self.conf_chip.pack(side="left")
        self.why = ctk.CTkLabel(info, text="", wraplength=430, justify="left", anchor="w", text_color=REVIEW)
        self.why.pack(fill="x", padx=18, pady=(4, 8))
        self.ask = ctk.CTkLabel(info, text="", anchor="w")
        self.ask.pack(fill="x", padx=18)
        self.entry = ctk.CTkEntry(info, height=52, font=self.f_big, justify="center")
        self.entry.pack(fill="x", padx=18, pady=6)
        self.err = ctk.CTkLabel(info, text="", text_color=FAIL, wraplength=430, anchor="w", justify="left")
        self.err.pack(fill="x", padx=18)
        btns = ctk.CTkFrame(info, fg_color="transparent")
        btns.pack(fill="x", padx=18, pady=(6, 4))
        btns.grid_columnconfigure((0, 1, 2), weight=1, uniform="btn")  # equal widths whatever the labels
        self.keep_btn = ctk.CTkButton(btns, text="Keep", fg_color=OK, hover_color="#26834c", command=self.keep)
        self.keep_btn.grid(row=0, column=0, sticky="ew")
        self.blank_btn = ctk.CTkButton(btns, text="Blank", fg_color=("gray60", "gray35"), command=self.blank)
        self.blank_btn.grid(row=0, column=1, sticky="ew", padx=6)
        ctk.CTkButton(btns, text="Save  (Enter)", command=self.save).grid(row=0, column=2, sticky="ew")
        nav = ctk.CTkFrame(info, fg_color="transparent")
        nav.pack(fill="x", padx=18, pady=(4, 16))
        ctk.CTkButton(nav, text="<  Previous", width=120, fg_color="transparent", border_width=1,
                      text_color=("gray10", "gray90"), command=lambda: self.move(-1)).pack(side="left")
        ctk.CTkButton(nav, text="Next  >", width=120, fg_color="transparent", border_width=1,
                      text_color=("gray10", "gray90"), command=lambda: self.move(1)).pack(side="right")
        ctk.CTkLabel(right, text="All items  (click to jump)", anchor="w", font=self.f_head).grid(row=1, column=0, sticky="nw", pady=(14, 0))
        self.list = ctk.CTkScrollableFrame(right, corner_radius=14)
        self.list.grid(row=1, column=0, sticky="nsew", pady=(44, 0))
        self.list_btns: list = []

    def refresh_list(self):
        for b in self.list_btns:
            b.destroy()
        self.list_btns = []
        for k, it in enumerate(self.items):
            done = it["answer"] is not None
            label = {"cell": it["cell"].upper().replace("_", ""), "roll": "Roll no.", "section": "Section",
                     "sheet": "Page"}[it["kind"]]
            shown = it["answer"] if done else "?"
            b = ctk.CTkButton(self.list, anchor="w", height=30, corner_radius=6,
                              text=f"{'✓' if done else '•'}  {it['scan_id']}   {label:9s}  {it['read'] or '-'} -> {shown or 'blank'}",
                              fg_color=(("gray75", "gray30") if k == self.i else "transparent"),
                              text_color=(OK if done else ("gray10", "gray90")), hover_color=("gray80", "gray28"),
                              command=lambda k=k: self.jump(k))
            b.pack(fill="x", pady=1)
            self.list_btns.append(b)
        done = sum(i["answer"] is not None for i in self.items)
        self.done_bar.set(done / len(self.items) if self.items else 1)
        self.done_lbl.configure(text=f"{done} of {len(self.items)} checked" if self.items else "")
        self.n_check.configure(text=str(len(self.items) - done))

    def render_item(self):
        for w in self.crop_box.winfo_children():
            w.destroy()
        self.err.configure(text="")
        if not self.items:
            ctk.CTkLabel(self.crop_box, text="Nothing to check.\nEvery value was read confidently - go to Export.",
                         font=self.f_head).pack(expand=True)
            for w in (self.where, self.read_lbl, self.why, self.ask):
                w.configure(text="")
            self.conf_chip.configure(text="", fg_color="transparent")
            self.entry.delete(0, "end")
            self.keep_btn.configure(text="Keep")
            return
        it = self.items[self.i]
        label = {"cell": f"Mark {it['cell'].upper().replace('_', '')}", "roll": "Roll number",
                 "section": "Section letter", "sheet": "Whole page"}[it["kind"]]
        self.where.configure(text=f"Cover {it['scan_id']}  ·  {label}      ({self.i + 1} / {len(self.items)})")
        self.read_lbl.configure(text=it["read"] or ("(blank)" if it["kind"] != "sheet" else ""))
        if it["conf"]:
            self.conf_chip.configure(text=f"sure {float(it['conf']):.0%}", fg_color=conf_colour(it["conf"]))
        else:
            self.conf_chip.configure(text="", fg_color="transparent")
        self.why.configure(text=f"Why: {it['why']}")
        self.ask.configure(text={"cell": "Correct mark (empty = blank cell)", "roll": "Correct roll number (7 digits)",
                                 "section": "Section (A or B)", "sheet": "Page read correctly? Press Enter / Keep"}[it["kind"]])
        self.keep_btn.configure(text=f"Keep  {it['read']}" if it["read"] else "Keep")
        self.blank_btn.configure(state="normal" if it["kind"] == "cell" else "disabled")
        for p in it["images"]:
            if Path(p).exists():
                box = (620, 520) if it["kind"] == "sheet" else (620, 240)
                ctk.CTkLabel(self.crop_box, text="", image=ctk_image(p, box)).pack(anchor="center", pady=6)
        self.entry.delete(0, "end")
        self.entry.insert(0, it["answer"] if it["answer"] is not None else it["read"])
        self.entry.select_range(0, "end")
        self.entry.focus_set()

    def _store(self, text: str):
        if not self.items:
            return
        it = self.items[self.i]
        value, err = validate(it["kind"], text)
        if value is None:
            self.err.configure(text=err)
            self.entry.focus_set()
            return
        it["answer"] = value
        save_answers(self.items)
        nxt = next((k for k in list(range(self.i + 1, len(self.items))) + list(range(self.i))
                    if self.items[k]["answer"] is None), None)
        if nxt is not None:
            self.i = nxt
        self.refresh_list()
        self.render_item()
        if nxt is None:
            self.where.configure(text="All items checked  ✓   Go to Export Excel.")

    def save(self):
        self._store(self.entry.get())

    def keep(self):
        self._store(self.items[self.i]["read"] if self.items else "")

    def blank(self):
        self._store("")

    def move(self, step):
        if self.items:
            self.i = (self.i + step) % len(self.items)
            self.refresh_list()
            self.render_item()
        return "break"

    def jump(self, k):
        self.i = k
        self.refresh_list()
        self.render_item()

    def open_page(self):
        if self.items:
            PageViewer(self, self.items[self.i]["scan_id"])

    # ---------------------------------------------------------------- 3. covers
    def _build_covers(self):
        p = self.pages["covers"]
        head = ctk.CTkFrame(p, fg_color="transparent")
        head.pack(fill="x")
        ctk.CTkLabel(head, text="All covers", font=self.f_title).pack(side="left")
        self.filter = ctk.CTkSegmentedButton(head, values=["All", "To check", "OK", "Retake"],
                                             command=lambda v: self.render_covers())
        self.filter.set("All")
        self.filter.pack(side="right")
        ctk.CTkLabel(p, text="Click a cover to see the whole page with what was read (orange = flagged).",
                     text_color=("gray35", "gray70")).pack(anchor="w", pady=(2, 12))
        self.cover_list = ctk.CTkScrollableFrame(p, corner_radius=14)
        self.cover_list.pack(fill="both", expand=True)

    def render_covers(self):
        for w in self.cover_list.winfo_children():
            w.destroy()
        try:
            sheets = _read("m5_sheets.csv")
        except FileNotFoundError:
            ctk.CTkLabel(self.cover_list, text="Nothing scanned yet.").pack(pady=20)
            return
        want = {"All": None, "To check": "review", "OK": "ok", "Retake": "fail"}[self.filter.get()]
        for s in sheets:
            if want and s["status"] != want:
                continue
            row = ctk.CTkFrame(self.cover_list, corner_radius=10)
            row.pack(fill="x", pady=3, padx=4)
            ctk.CTkLabel(row, text={"ok": "OK", "review": "CHECK", "fail": "RETAKE"}[s["status"]], width=74,
                         corner_radius=8, fg_color=CHIP[s["status"]], text_color="white").pack(side="left", padx=10, pady=8)
            ctk.CTkLabel(row, text=s["scan_id"], font=self.f_head, width=70, anchor="w").pack(side="left")
            ctk.CTkLabel(row, text=f"Section {s['section'] or '?'}    Roll {s['roll_no'] or '?'}", width=230,
                         anchor="w").pack(side="left")
            flagged = len(s["flagged_cells"].split())
            note = "; ".join(filter(None, [f"{flagged} mark(s) to check" if flagged else "", s["reasons"]]))
            ctk.CTkLabel(row, text=note, anchor="w", text_color=("gray35", "gray70")).pack(side="left", fill="x", expand=True)
            ctk.CTkButton(row, text="View", width=70, command=lambda sid=s["scan_id"]: PageViewer(self, sid)).pack(side="right", padx=10)

    # ---------------------------------------------------------------- 4. export
    def _build_export(self):
        p = self.pages["export"]
        ctk.CTkLabel(p, text="Export to Excel", font=self.f_title).pack(anchor="w")
        ctk.CTkLabel(p, text="One row per student (Section A and B covers merged by roll number), each section = best 3 "
                             "of its 4 questions, Final = A + B. Totals are live formulas.",
                     text_color=("gray35", "gray70"), wraplength=900, justify="left").pack(anchor="w", pady=(2, 16))
        card = ctk.CTkFrame(p, corner_radius=14)
        card.pack(fill="x")
        self.export_info = ctk.CTkLabel(card, text="", font=self.f_head, anchor="w", justify="left")
        self.export_info.pack(fill="x", padx=18, pady=(18, 6))
        ctk.CTkButton(card, text="Export Excel workbook", height=46, font=self.f_head,
                      command=self.export).pack(anchor="w", padx=18, pady=(6, 18))
        self.result = ctk.CTkFrame(p, corner_radius=14)
        self.result_lbl = ctk.CTkLabel(self.result, text="", anchor="w", justify="left", wraplength=900)
        self.result_lbl.pack(fill="x", padx=18, pady=(18, 8))
        rb = ctk.CTkFrame(self.result, fg_color="transparent")
        rb.pack(fill="x", padx=18, pady=(0, 18))
        ctk.CTkButton(rb, text="Open workbook", fg_color=OK, hover_color="#26834c", command=self.open_book).pack(side="left")
        ctk.CTkButton(rb, text="Open folder", fg_color="transparent", border_width=1, text_color=("gray10", "gray90"),
                      command=lambda: self.open_path(self.book.parent if self.book else None)).pack(side="left", padx=8)

    def render_export(self):
        left = sum(i["answer"] is None for i in self.items)
        self.export_info.configure(
            text=("All flagged items are checked - ready to export." if not left else
                  f"{left} item(s) not checked yet. They will be yellow in the workbook (you can still export)."),
            text_color=(OK if not left else REVIEW))

    def export(self):
        try:
            self.items = self.items or load_items()
        except FileNotFoundError:
            messagebox.showinfo("SmartMark", "Nothing scanned yet - scan some photos first.")
            return
        left = sum(x["answer"] is None for x in self.items)
        if left and not messagebox.askyesno("SmartMark", f"{left} item(s) not checked yet. They will stay yellow "
                                                         "in the workbook. Export anyway?"):
            return
        apply_answers(self.items)
        ANSWERS.unlink(missing_ok=True)
        self.book = excel.build()
        self.items = load_items()  # whatever is still flagged after the corrections
        self.i = 0
        self.refresh_list()
        self.result_lbl.configure(text=f"Saved:  {self.book}\n"
                                       f"{'Every value checked.' if not self.items else f'{len(self.items)} cell(s) left yellow to check in Excel.'}")
        self.result.pack(fill="x", pady=16)
        self.render_export()

    def open_book(self):
        self.open_path(self.book)

    @staticmethod
    def open_path(path):
        if path and hasattr(os, "startfile"):
            os.startfile(path)  # Windows: opens Excel / Explorer


def main() -> None:
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("green")
    SmartMarkApp().mainloop()


if __name__ == "__main__":
    main()
