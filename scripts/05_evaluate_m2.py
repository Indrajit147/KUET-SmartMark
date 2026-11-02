"""Milestone 2 exit-gate check: compare scripts\\04_align_and_crop.py output with the verified labels.

Usage:  .venv\\Scripts\\python.exe scripts\\05_evaluate_m2.py
Needs:  reports\\m2_alignment.csv, reports\\m2_cells.csv (from 04) and annotations\\sheet_labels.csv

Exit gate (project plan, M2):
  - every scan aligned (none failed),
  - printed A/B letter matches the labeled section on every scan,
  - blank-vs-written matches the labels on >= 99% of mark cells.
Cells a note in the labels says hold a non-number mark ("Q8 crossed out", "Q4a has a red dash")
are reported separately: they DO contain ink, so 'written' is the right M2 answer; telling a
scribble from a number is Milestone 4's job.
"""
from __future__ import annotations

import csv
import re

from common import LABELS_CSV, REPORT_DIR, ROOT

GATE_BLANK = 0.99


def noted_cells(note: str) -> set[str]:
    """'Q8 crossed out (Q8a, Q8b scribbled)' -> {'q8_a','q8_b'}; 'Q1 crossed out' -> all four Q1 cells."""
    found: dict[str, set[str]] = {}
    for q, part in re.findall(r"\bQ([1-8])([a-d])?\b", note, flags=re.IGNORECASE):
        found.setdefault(q, set())
        if part:
            found[q].add(part.lower())
    return {f"q{q}_{p}" for q, parts in found.items() for p in (parts or "abcd")}


def main() -> None:
    def read(p):
        with p.open(encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    try:
        align = read(REPORT_DIR / "m2_alignment.csv")
        cells = read(REPORT_DIR / "m2_cells.csv")
    except FileNotFoundError:
        raise SystemExit("Run scripts\\04_align_and_crop.py first")
    labels = {r["scan_id"]: r for r in read(LABELS_CSV) if r["label_status"].strip().lower() == "verified"}

    failed = [r["scan_id"] for r in align if r["status"] == "fail"]
    review = [(r["scan_id"], r["reason"]) for r in align if r["status"] == "review"]
    letter_bad = [(r["scan_id"], r["section_letter"], labels[r["scan_id"]]["section"]) for r in align
                  if r["scan_id"] in labels and r["section_letter"] != labels[r["scan_id"]]["section"].strip().upper()]

    lines, ok, total, special, wrong = [], 0, 0, [], []
    roll_ok = roll_total = 0
    roll_notes = []
    for c in cells:
        lab = labels.get(c["scan_id"])
        if not lab:
            continue
        written = c["filled"] == "1"
        if c["crop"].startswith("roll_"):
            if "roll boxes left empty" in lab["notes"].lower():
                roll_notes.append(c["scan_id"])
                continue
            roll_total += 1
            roll_ok += written  # every roll box should hold a digit
            if not written:
                wrong.append((c["scan_id"], c["crop"], "roll digit missed", c["ink_score"]))
            continue
        should = lab.get(c["crop"], "").strip() != ""
        if c["crop"] in noted_cells(lab["notes"]):
            special.append((c["scan_id"], c["crop"], "written" if written else "blank", c["ink_score"]))
            continue
        total += 1
        if written == should:
            ok += 1
        else:
            wrong.append((c["scan_id"], c["crop"], "missed writing" if should else "ink in a blank cell", c["ink_score"]))

    acc = ok / total if total else 0
    gate = {
        "all scans aligned": not failed,
        "A/B letter matches label on every scan": not letter_bad,
        f"blank-vs-written >= {GATE_BLANK:.0%}": acc >= GATE_BLANK,
    }
    out = []
    out.append(f"Scans: {len(align)} | aligned: {len(align) - len(failed)} | flagged for review: {len(review)}")
    for sid, why in review:
        out.append(f"  review {sid}: {why}")
    out.append(f"Section letter: {len(align) - len(failed) - len(letter_bad)}/{len(align) - len(failed)} match the labels")
    out.append(f"Mark cells blank-vs-written: {ok}/{total} = {acc:.2%}")
    out.append(f"Roll boxes detected as written: {roll_ok}/{roll_total}"
               + (f" (+ {len(set(roll_notes))} sheet(s) noted as empty roll boxes: {', '.join(sorted(set(roll_notes)))})" if roll_notes else ""))
    if special:
        out.append(f"Noted non-number marks (crossed out / dash): {len(special)} cells, "
                   f"{sum(s[2] == 'written' for s in special)} detected as ink (expected)")
    for w in wrong:
        out.append(f"  MISMATCH {w[0]} {w[1]}: {w[2]} (ink score {w[3]})")
    out.append("")
    for k, v in gate.items():
        out.append(f"[{'PASS' if v else 'FAIL'}] {k}")
    out.append("M2 exit gate: " + ("PASSED" if all(gate.values()) else "NOT passed")
               + " (plus the visual check of data\\preview\\m2\\)")
    text = "\n".join(out)
    print(text)
    (REPORT_DIR / "m2_evaluation.txt").write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
