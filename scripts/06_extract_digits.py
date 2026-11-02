"""Milestone 3: split every written cell / roll box into single digits and label them from the spreadsheet.

Usage:  .venv\\Scripts\\python.exe scripts\\06_extract_digits.py
Needs:  data\\processed\\ (from 04_align_and_crop.py) and annotations\\sheet_labels.csv

Each digit is saved MNIST-style (28x28, white digit on black, centred by mass) so it can be
mixed with public datasets. Class 'x' = scribble / dash / crossed-out ink (read as EMPTY).
Writes:
  data\\digits\\<split>\\<class>\\<scan_id>_<crop>_<k>.png     split = train | test (test = held-out students)
  data\\digits\\index.csv          one row per digit image
  reports\\m3_review.csv           cells whose piece count does not match the label - check or override
  data\\preview\\m3_digits.jpg     a sample grid per class for a visual check
Overrides for cells the splitter gets wrong: annotations\\digit_overrides.csv  (scan_id,crop,labels)
  e.g.  b008,q7_c,x4   -> pieces left-to-right are a scribble then a 4
"""
from __future__ import annotations

import csv
import random
import re
import shutil

import cv2
import numpy as np

from common import ANNOTATION_DIR, LABELS_CSV, PREVIEW_DIR, REPORT_DIR, ROOT
from sm_align import full_strokes, has_loop, ink_height, join_fragments, pieces, split_touching, to_mnist

PROCESSED = ROOT / "data" / "processed"
OUT = ROOT / "data" / "digits"
OVERRIDES = ANNOTATION_DIR / "digit_overrides.csv"
TEST_STUDENTS = 6          # whole students (both A and B sheets) held out for the final test
SEED = 2026
CLASSES = [str(d) for d in range(10)] + ["x"]


def fit(ps: list[np.ndarray], target: str, single: bool) -> tuple[list[np.ndarray], str]:
    """Make the ink pieces line up with the label's digits.

    - a roll box or a scribble ('x') is one mark: all pieces joined;
    - small fragments (< half the tallest piece) are joined to their nearest neighbour;
    - one extra full-size piece in front = a leading zero ('09' labeled 9); so is one piece wider
      than tall whose left part is a closed loop (a '0' touching the digit);
    - one piece for a 2-digit label = touching digits, cut at the thinnest column.
    """
    if not ps:
        return ps, target
    if single or target == "x":
        return [np.bitwise_or.reduce(ps)], target
    want = len(target)
    ps = join_fragments(ps, want)
    if len(ps) == want + 1:
        target = "0" + target
    elif len(ps) == want == 1:
        xs = np.nonzero(ps[0])[1]
        if np.ptp(xs) + 1 > 1.15 * ink_height(ps[0]):  # wider than tall: maybe a '0' touching the digit
            halves = split_touching(ps[0])
            if has_loop(halves[0]):  # a wide 2, 5 or 7 has no loop on its left side
                ps, target = halves, "0" + target
    if len(ps) == 1 and want == 2:
        ps = split_touching(ps[0])
    return ps, target


def noted_cells(note: str) -> set[str]:
    found: dict[str, set[str]] = {}
    for q, part in re.findall(r"\bQ([1-8])([a-d])?\b", note, flags=re.IGNORECASE):
        found.setdefault(q, set())
        if part:
            found[q].add(part.lower())
    return {f"q{q}_{p}" for q, parts in found.items() for p in (parts or "abcd")}


def main() -> None:
    with LABELS_CSV.open(encoding="utf-8-sig", newline="") as f:
        labels = {r["scan_id"]: r for r in csv.DictReader(f) if r["label_status"].strip().lower() == "verified"}
    overrides = {}
    if OVERRIDES.exists():
        with OVERRIDES.open(encoding="utf-8-sig", newline="") as f:
            overrides = {(r["scan_id"], r["crop"]): r["labels"].strip() for r in csv.DictReader(f)}

    rolls = sorted({r["roll_no"] for r in labels.values() if r["roll_no"]})
    test_rolls = set(random.Random(SEED).sample(rolls, min(TEST_STUDENTS, len(rolls))))

    for split in ("train", "test"):  # regenerated each run; stale images would pollute classes (public.npz is kept)
        shutil.rmtree(OUT / split, ignore_errors=True)
    rows, review = [], []
    for sid, lab in sorted(labels.items()):
        d = PROCESSED / sid / "mask"
        if not d.exists():
            continue
        split = "test" if lab["roll_no"] in test_rolls else "train"
        roll_empty = "roll boxes left empty" in lab["notes"].lower()
        crops = [] if roll_empty else [(f"roll_{k + 1}", lab["roll_no"][k] if len(lab["roll_no"]) == 7 else "")
                                       for k in range(7)]
        noted = noted_cells(lab["notes"])
        crops += [(f"q{q}_{c}", "x" if f"q{q}_{c}" in noted else lab[f"q{q}_{c}"].strip())
                  for q in range(1, 9) for c in "abcd"]
        for crop, target in crops:
            strict = cv2.imread(str(d / f"{crop}.png"), cv2.IMREAD_GRAYSCALE)
            colour = cv2.imread(str(d.parent / f"{crop}.png"))
            if strict is None or colour is None:
                continue
            mask = full_strokes(colour, strict)
            ps = pieces(mask)
            forced = overrides.get((sid, crop))
            target = forced or target
            if target in ("", "?"):
                if ps and target == "":
                    review.append((sid, crop, target, len(ps), "ink found in a cell labeled blank"))
                continue
            if not forced:
                ps, target = fit(ps, target, single=crop.startswith("roll_"))
            elif len(ps) == 1 and len(forced) == 2:
                ps = split_touching(ps[0])
            want = list(target)
            if len(ps) != len(want):
                review.append((sid, crop, target, len(ps), "piece count does not match label"))
                continue
            for k, (m, ch) in enumerate(zip(ps, want)):
                if ch not in CLASSES:
                    review.append((sid, crop, target, len(ps), f"unknown class {ch!r}"))
                    continue
                folder = OUT / split / ch
                folder.mkdir(parents=True, exist_ok=True)
                name = f"{sid}_{crop}_{k}.png"
                cv2.imwrite(str(folder / name), to_mnist(m))
                rows.append({"path": (folder / name).relative_to(ROOT).as_posix(), "label": ch, "split": split,
                             "scan_id": sid, "roll_no": lab["roll_no"], "crop": crop, "position": k,
                             "source": "roll" if crop.startswith("roll") else "mark"})

    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "index.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    with (REPORT_DIR / "m3_review.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["scan_id", "crop", "label", "pieces_found", "problem"])
        w.writerows(review)

    # sample grid: one row per class, up to 24 examples
    grid = np.zeros((len(CLASSES) * 30, 24 * 30), np.uint8)
    for r, cls in enumerate(CLASSES):
        for c, row in enumerate([x for x in rows if x["label"] == cls][:24]):
            grid[r * 30 + 1:r * 30 + 29, c * 30 + 1:c * 30 + 29] = cv2.imread(str(ROOT / row["path"]), 0)
    cv2.imwrite(str(PREVIEW_DIR / "m3_digits.jpg"), cv2.resize(grid, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST))

    total_cells = sum(1 for _ in rows)
    print(f"Held-out test students (rolls): {', '.join(sorted(test_rolls))}")
    print(f"Digits written: {total_cells} | review list: {len(review)} cells -> reports\\m3_review.csv")
    print("class  train  test")
    for cls in CLASSES:
        tr = sum(r["label"] == cls and r["split"] == "train" for r in rows)
        te = sum(r["label"] == cls and r["split"] == "test" for r in rows)
        print(f"  {cls:>3}  {tr:5d}  {te:4d}")
    for r in review:
        print("  REVIEW", *r)


if __name__ == "__main__":
    main()
