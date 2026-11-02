"""KUET SmartMark - teacher app (Milestone 7).

    .venv\\Scripts\\python.exe scripts\\smartmark_app.py

1. Choose the folder of cover photos and press Scan (about 1 s per photo).
2. Check every doubtful item: the photo of what was written is shown next to what was read.
   Enter = save and go to the next item, Up/Down = previous/next, an empty box = blank cell.
3. Export Excel: corrections are applied and a new workbook is written to output\\.
Answers are saved as you go (reports\\review_answers.json), so closing the app loses nothing.
Every correction is also logged in reports\\teacher_corrections.csv for retraining the model later.
"""
from __future__ import annotations

import csv
import json
import os
import queue
import re
import threading
import time
import tkinter as tk
from importlib import import_module
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

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
class App:
    def __init__(self, root: tk.Tk):
        self.root, self.items, self.i, self.photos = root, [], 0, []
        self.q: queue.Queue = queue.Queue()
        root.title("KUET SmartMark")
        root.geometry("1100x720")
        big = ("Segoe UI", 20, "bold")

        top = ttk.Frame(root, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="Photos folder:").pack(side="left")
        self.folder = tk.StringVar(value=str(ROOT / "data" / "raw"))
        ttk.Entry(top, textvariable=self.folder, width=70).pack(side="left", padx=6)
        ttk.Button(top, text="Browse...", command=self.browse).pack(side="left")
        self.scan_btn = ttk.Button(top, text="Scan", command=self.scan)
        self.scan_btn.pack(side="left", padx=10)

        bar = ttk.Frame(root, padding=(10, 0))
        bar.pack(fill="x")
        self.progress = ttk.Progressbar(bar, mode="determinate")
        self.progress.pack(fill="x")
        self.status = tk.StringVar(value="Choose the folder of cover photos and press Scan "
                                         "(or press Review to continue the last scan).")
        ttk.Label(bar, textvariable=self.status, wraplength=1050).pack(anchor="w", pady=4)

        body = ttk.Frame(root, padding=10)
        body.pack(fill="both", expand=True)
        self.pics = ttk.Frame(body, width=600)
        self.pics.pack(side="left", fill="both", expand=True)
        side = ttk.Frame(body, padding=(16, 0))
        side.pack(side="left", fill="y")
        self.counter = tk.StringVar()
        ttk.Label(side, textvariable=self.counter, font=("Segoe UI", 11)).pack(anchor="w")
        self.where = tk.StringVar()
        ttk.Label(side, textvariable=self.where, font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(8, 0))
        self.readas = tk.StringVar()
        ttk.Label(side, textvariable=self.readas, font=("Segoe UI", 12)).pack(anchor="w", pady=4)
        self.why = tk.StringVar()
        ttk.Label(side, textvariable=self.why, wraplength=380, foreground="#9a4d00").pack(anchor="w", pady=4)
        self.ask = tk.StringVar(value="Correct value:")
        ttk.Label(side, textvariable=self.ask).pack(anchor="w", pady=(12, 0))
        self.entry = ttk.Entry(side, font=big, width=10)
        self.entry.pack(anchor="w", pady=4)
        self.entry.bind("<Return>", lambda e: self.save())
        self.entry.bind("<Up>", lambda e: self.move(-1))
        self.entry.bind("<Down>", lambda e: self.move(1))
        self.error = tk.StringVar()
        ttk.Label(side, textvariable=self.error, foreground="#b00020", wraplength=380).pack(anchor="w")
        row = ttk.Frame(side)
        row.pack(anchor="w", pady=8)
        ttk.Button(row, text="< Previous", command=lambda: self.move(-1)).pack(side="left")
        ttk.Button(row, text="Save and next  (Enter)", command=self.save).pack(side="left", padx=6)
        ttk.Button(row, text="Next >", command=lambda: self.move(1)).pack(side="left")
        ttk.Label(side, text="Enter = save and next   Up/Down = previous/next\n"
                             "A blank cell: clear the box and press Enter.", foreground="#555").pack(anchor="w")

        foot = ttk.Frame(root, padding=10)
        foot.pack(fill="x", side="bottom")
        ttk.Button(foot, text="Review last scan", command=self.start_review).pack(side="left")
        self.export_btn = ttk.Button(foot, text="Export Excel", command=self.export)
        self.export_btn.pack(side="left", padx=10)
        self.saved = tk.StringVar()
        ttk.Label(foot, textvariable=self.saved).pack(side="left")
        self.open_btn = ttk.Button(foot, text="Open workbook", command=self.open_book, state="disabled")
        self.open_btn.pack(side="right")
        self.book = None

    # -- scanning
    def browse(self):
        d = filedialog.askdirectory(initialdir=self.folder.get())
        if d:
            self.folder.set(d)

    def scan(self):
        files, ids = reader.collect([self.folder.get()])
        if not files:
            messagebox.showwarning("SmartMark", "No photos (JPG/PNG/...) in that folder.")
            return
        ANSWERS.unlink(missing_ok=True)  # a new batch starts with no answers
        self.scan_btn.state(["disabled"])
        self.progress.configure(maximum=len(files), value=0)
        threading.Thread(target=self._scan_worker, args=(files, ids), daemon=True).start()
        self.root.after(200, self._poll)

    def _scan_worker(self, files, ids):
        try:
            summary = reader.run(files, ids, progress=lambda i, n, line: self.q.put(("step", i, n, line)))
            self.q.put(("done", summary[0]))
        except BaseException as exc:  # shown to the teacher instead of a silent crash
            self.q.put(("error", f"{type(exc).__name__}: {exc}"))

    def _poll(self):
        while not self.q.empty():
            msg = self.q.get()
            if msg[0] == "step":
                self.progress.configure(value=msg[1])
                self.status.set(f"Reading photo {msg[1]} of {msg[2]}:  {msg[3].split('(')[0]}")
            else:
                self.scan_btn.state(["!disabled"])
                if msg[0] == "error":
                    messagebox.showerror("SmartMark", f"Scan stopped: {msg[1]}")
                    return
                self.status.set(msg[1])
                self.start_review()
                return
        self.root.after(200, self._poll)

    # -- reviewing
    def start_review(self):
        try:
            self.items = load_items()
        except FileNotFoundError:
            messagebox.showinfo("SmartMark", "Nothing scanned yet - press Scan first.")
            return
        fails = [s["scan_id"] for s in _read("m5_sheets.csv") if s["status"] == "fail"]
        msg = f"{len(self.items)} item(s) to check."
        if fails:
            msg += f"  Photos not recognised (retake them): {', '.join(fails)}."
        self.status.set(self.status.get().split("  |")[0] + "  |  " + msg)
        self.i = next((k for k, it in enumerate(self.items) if it["answer"] is None), 0)
        self.show()

    def show(self):
        for w in self.pics.winfo_children():
            w.destroy()
        self.photos = []  # Tk shows an image only while a Python reference to it exists
        self.error.set("")
        if not self.items:
            self.counter.set("Nothing to check - every value was read confidently.")
            self.where.set(""), self.readas.set(""), self.why.set("")
            return
        it = self.items[self.i]
        left = sum(x["answer"] is None for x in self.items)
        self.counter.set(f"Item {self.i + 1} of {len(self.items)}   ({left} still to check)"
                         + ("   - checked" if it["answer"] is not None else ""))
        label = {"cell": it["cell"].upper().replace("_", ""), "roll": "Roll number", "section": "Section letter",
                 "sheet": "Whole page"}[it["kind"]]
        self.where.set(f"Cover {it['scan_id']}  -  {label}")
        self.readas.set(f"Read as:  {it['read'] or '(blank)'}" + (f"   (confidence {it['conf']})" if it["conf"] else "")
                        if it["kind"] != "sheet" else "Check the page preview: are the values right?")
        self.why.set(f"Why: {it['why']}")
        self.ask.set({"cell": "Correct mark (empty = blank):", "roll": "Correct roll number (7 digits):",
                      "section": "Section (A or B):", "sheet": "Press Enter if the page was read correctly:"}[it["kind"]])
        for p in it["images"]:
            if Path(p).exists():
                im = Image.open(p)
                box = (580, 560) if it["kind"] == "sheet" else (560, 260)
                k = min(box[0] / im.width, box[1] / im.height, 4.0)
                im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))))
                ph = ImageTk.PhotoImage(im)
                self.photos.append(ph)
                ttk.Label(self.pics, image=ph).pack(anchor="w", pady=4)
        self.entry.delete(0, "end")
        self.entry.insert(0, it["answer"] if it["answer"] is not None else it["read"])
        self.entry.select_range(0, "end")
        self.entry.focus_set()

    def save(self):
        if not self.items:
            return
        it = self.items[self.i]
        value, err = validate(it["kind"], self.entry.get())
        if value is None:
            self.error.set(err)
            return
        it["answer"] = value
        save_answers(self.items)
        nxt = next((k for k in list(range(self.i + 1, len(self.items))) + list(range(self.i))
                    if self.items[k]["answer"] is None), None)
        if nxt is None:
            self.status.set("All items checked - press Export Excel.")
            self.show()
            return
        self.i = nxt
        self.show()

    def move(self, step):
        if self.items:
            self.i = (self.i + step) % len(self.items)
            self.show()
        return "break"

    # -- export
    def export(self):
        try:
            self.items = self.items or load_items()
        except FileNotFoundError:
            messagebox.showinfo("SmartMark", "Nothing scanned yet - press Scan first.")
            return
        left = sum(x["answer"] is None for x in self.items)
        if left and not messagebox.askyesno("SmartMark", f"{left} item(s) not checked yet. They will stay yellow "
                                                         "in the workbook. Export anyway?"):
            return
        apply_answers(self.items)
        ANSWERS.unlink(missing_ok=True)
        self.book = excel.build()
        self.saved.set(f"Saved: {self.book}")
        self.open_btn.state(["!disabled"])
        self.items = load_items()  # what is still flagged after the corrections
        self.i = 0
        self.show()

    def open_book(self):
        if self.book and hasattr(os, "startfile"):
            os.startfile(self.book)  # Windows: opens in Excel


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
