"""Milestone 6: build the teacher's marks workbook from what 09_read_sheets.py read.

Usage:  .venv\\Scripts\\python.exe scripts\\10_fill_excel.py
Needs:  reports\\m5_sheets.csv, reports\\m5_cells.csv and the crops in data\\processed\\<scan_id>\\read\\ (from 09)
Writes: output\\SmartMark_marks_<date>_<time>.xlsx - a NEW file every run, so a workbook a teacher has
        already corrected is never overwritten.

Sheet 'Marks'  : one row per student (Section A and B covers merged by roll number).
                 Q1a..Q8d marks, question totals, section total = best 3 of 4 questions, final = A + B.
                 Totals are Excel formulas: correcting a cell updates every total.
                 Yellow cell = check it (the comment says why); Status column says how many to check.
Sheet 'Review' : every yellow cell with the cropped photo of what was written.
Sheet 'Covers' : every photo with its status and reasons.
"""
from __future__ import annotations

import csv
import re
import time
from collections import defaultdict

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from common import REPORT_DIR, ROOT, SECTION_QUESTIONS

OUT_DIR = ROOT / "output"
YELLOW = PatternFill("solid", fgColor="FFF2A8")
ORANGE = PatternFill("solid", fgColor="FBD7B5")
GREEN = PatternFill("solid", fgColor="D8EFD3")
HEAD = {"A": PatternFill("solid", fgColor="DCE9F7"), "B": PatternFill("solid", fgColor="E3F2DF")}
BOLD = Font(bold=True)


def read_csv(name):
    path = REPORT_DIR / name
    if not path.exists():
        raise SystemExit(f"{path.name} missing - run scripts\\09_read_sheets.py first")
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def layout():
    """Column number of every field on the Marks sheet."""
    col, n = {}, 1
    for name in ("roll", "status", "cover_A", "cover_B"):
        col[name] = n
        n += 1
    for sec in "AB":
        for q in SECTION_QUESTIONS[sec]:
            for c in "abcd":
                col[f"q{q}_{c}"] = n
                n += 1
        for q in SECTION_QUESTIONS[sec]:
            col[f"Q{q}"] = n
            n += 1
        col[f"best3_{sec}"] = n
        n += 1
    col["final"] = n
    col["notes"] = n + 1
    return col


def build() -> "Path":
    """Write a new workbook from the reports and return its path."""
    sheets = read_csv("m5_sheets.csv")
    cells = defaultdict(dict)
    for r in read_csv("m5_cells.csv"):
        cells[r["scan_id"]][r["cell"]] = r

    # group covers into students: key = roll number; covers with no usable roll get their own row
    students: dict[str, dict] = {}
    for s in sheets:
        if s["status"] == "fail" or s["section"] not in ("A", "B"):
            continue
        roll = s["roll_no"] if re.fullmatch(r"\d{7}", s["roll_no"]) else ""
        key = roll or f"? ({s['scan_id']})"
        while key in students and s["section"] in students[key]:
            key += " #2"  # same roll + section twice: keep both, flagged
        students.setdefault(key, {"roll": roll})[s["section"]] = s

    col = layout()
    wb = Workbook()
    ws = wb.active
    ws.title = "Marks"
    heads = {"roll": "Roll No.", "status": "Status", "cover_A": "A cover", "cover_B": "B cover", "final": "Final",
             "notes": "Notes"}
    for name, c in col.items():
        title = heads.get(name) or (name.replace("_", "").upper() if name.startswith("q") else
                                    (f"Q{name[1]} total" if name.startswith("Q") else f"Sec {name[-1]} best 3"))
        cell = ws.cell(1, c, title)
        cell.font = BOLD
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
        sec = "A" if name.endswith("_A") else "B" if name.endswith("_B") else None
        if name.startswith(("q", "Q")):
            sec = "A" if int(name[1]) <= 4 else "B"
        if sec:
            cell.fill = HEAD[sec]

    review = []  # (roll, cover, cell, read, conf, why, [image paths])
    n_yellow = 0
    for r, (key, st) in enumerate(sorted(students.items()), start=2):
        ws.cell(r, col["roll"], int(st["roll"]) if st["roll"] else key)
        to_check, notes = 0, []
        for sec in "AB":
            s = st.get(sec)
            if not s:
                notes.append(f"Section {sec} cover missing")
                continue
            ws.cell(r, col[f"cover_{sec}"], s["scan_id"])
            if s["reasons"]:
                notes.append(f"{s['scan_id']}: {s['reasons']}")
                to_check += 1
                read_dir = ROOT / "data" / "processed" / s["scan_id"] / "read"
                if "roll" in s["reasons"]:
                    review.append((key, s["scan_id"], "roll number", s["roll_no"] or "(none)", "", s["reasons"],
                                   [read_dir / "roll_row.png", read_dir / "header_roll.png"]))
            for name, info in cells[s["scan_id"]].items():
                if not name.startswith("q"):
                    continue
                q = int(name[1])
                if q not in SECTION_QUESTIONS[sec]:
                    if info["value"] or info["flag"]:  # writing in the other section's rows
                        review.append((key, s["scan_id"], name.upper().replace("_", ""), info["value"],
                                       info["confidence"], info["flag"],
                                       [ROOT / "data" / "processed" / s["scan_id"] / "read" / f"{name}.png"]))
                        to_check += 1
                    continue
                c = ws.cell(r, col[name], int(info["value"]) if info["value"] else None)
                c.alignment = Alignment(horizontal="center")
                if info["flag"]:
                    c.fill = YELLOW
                    c.comment = Comment(f"Read: {info['value'] or 'blank'} (confidence {info['confidence']})\n"
                                        f"Why: {info['flag']}\nPhoto: {s['file']}", "SmartMark")
                    n_yellow += 1
                    to_check += 1
                    review.append((key, s["scan_id"], name.upper().replace("_", ""), info["value"], info["confidence"],
                                   info["flag"], [ROOT / "data" / "processed" / s["scan_id"] / "read" / f"{name}.png"]))
                elif info["note"]:
                    c.comment = Comment(f"{info['note']} - read as {info['value'] or 'empty'}", "SmartMark")
        # totals as formulas so teacher corrections flow through
        for sec in "AB":
            qcols = []
            for q in SECTION_QUESTIONS[sec]:
                a, d = get_column_letter(col[f"q{q}_a"]), get_column_letter(col[f"q{q}_d"])
                ws.cell(r, col[f"Q{q}"], f'=IF(COUNT({a}{r}:{d}{r})=0,"",SUM({a}{r}:{d}{r}))')
                qcols.append(get_column_letter(col[f"Q{q}"]))
            rng = f"{qcols[0]}{r}:{qcols[-1]}{r}"
            ws.cell(r, col[f"best3_{sec}"], "=" + "+".join(f"IFERROR(LARGE({rng},{k}),0)" for k in (1, 2, 3))).font = BOLD
        ws.cell(r, col["final"], f"={get_column_letter(col['best3_A'])}{r}+{get_column_letter(col['best3_B'])}{r}").font = BOLD
        status = ws.cell(r, col["status"], "OK" if not to_check and not notes else
                         (f"CHECK {to_check}" if to_check else "CHECK"))
        status.fill = GREEN if status.value == "OK" else ORANGE
        ws.cell(r, col["notes"], "; ".join(notes))

    for name, c in col.items():
        ws.column_dimensions[get_column_letter(c)].width = 5.5 if name.startswith("q") else \
            (40 if name == "notes" else 10)
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = ws.cell(2, col["q1_a"])

    rv = wb.create_sheet("Review")
    for i, h in enumerate(["Roll / row", "Cover", "Cell", "Read as", "Confidence", "Why it needs a look",
                           "Photo of the cell", "Cover roll line"], start=1):
        rv.cell(1, i, h).font = BOLD
    for i, (key, sid, cell, val, conf, why, imgs) in enumerate(review, start=2):
        for j, v in enumerate([key, sid, cell, val, conf, why], start=1):
            rv.cell(i, j, v).alignment = Alignment(vertical="center", wrap_text=True)
        rv.row_dimensions[i].height = 60
        for k, p in enumerate(imgs):
            if p.exists():
                im = XLImage(str(p))
                fit = min(72 / im.height, 340 / im.width)  # fit the cell: wide roll rows must not cover the next picture
                im.height, im.width = round(im.height * fit), round(im.width * fit)
                rv.add_image(im, f"{get_column_letter(7 + k)}{i}")
    for letter, w in zip("ABCDEFGH", (14, 8, 12, 9, 11, 42, 50, 50)):
        rv.column_dimensions[letter].width = w

    cv = wb.create_sheet("Covers")
    for j, h in enumerate(["Cover", "Photo", "Status", "Section", "Roll read", "Reasons"], start=1):
        cv.cell(1, j, h).font = BOLD
    for i, s in enumerate(sheets, start=2):
        for j, k in enumerate(["scan_id", "file", "status", "section", "roll_no", "reasons"], start=1):
            cv.cell(i, j, s[k])
    for letter, w in zip("ABCDEF", (10, 40, 9, 8, 11, 70)):
        cv.column_dimensions[letter].width = w

    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / time.strftime("SmartMark_marks_%Y%m%d_%H%M%S.xlsx")
    wb.save(out)

    # self-check: reopen and count what was written
    chk = load_workbook(out)["Marks"]
    rows = chk.max_row - 1
    yellow = sum(1 for row in chk.iter_rows(min_row=2) for c in row if c.fill.fgColor.rgb in ("00FFF2A8", "FFFFF2A8"))
    assert rows == len(students) and yellow == n_yellow, (rows, len(students), yellow, n_yellow)
    print(f"Students: {rows} | yellow cells to check: {n_yellow} | review rows: {len(review)}")
    return out


def main() -> None:
    print(f"Saved: {build().relative_to(ROOT)}")


if __name__ == "__main__":
    main()
