"""Milestone 5: read answer-script covers end to end - photo -> section, roll number, 32 marks.

Usage:
    .venv\\Scripts\\python.exe scripts\\09_read_sheets.py                    every photo in data\\raw\\
    .venv\\Scripts\\python.exe scripts\\09_read_sheets.py D:\\scans\\batch1    a folder (or list of photos)
Options: --threshold 0.8   digits read with lower confidence are flagged for the teacher

Every cell gets a value, a confidence and - if anything is doubtful - a flag. Nothing doubtful is guessed:
  - a digit below the confidence threshold                 -> flag
  - a crossed-out / scribbled mark (class x)               -> read as EMPTY (a clean number beside it counts)
  - a mark above 35, or a question totalling above 35      -> flag
  - writing in the other section's rows                    -> flag
  - roll boxes empty / unclear, or the same roll + section twice in a batch -> sheet flagged
Writes:
  reports\\m5_sheets.csv   one row per photo: status ok / review / fail, section, roll, marks, reasons
  reports\\m5_cells.csv    one row per cell: value, confidence, flag reason
  data\\preview\\m5\\<scan_id>.jpg   the crops with what was read (orange = flagged)
  data\\processed\\<scan_id>\\read\\   flagged-cell, roll-row and cover-roll crops for the Excel review sheet
  reports\\m5_evaluation.txt        if annotations\\sheet_labels.csv has these sheets: silent errors vs flags
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

from common import (LABELS_CSV, PREVIEW_DIR, REPORT_DIR, ROOT, SECTION_QUESTIONS, SUPPORTED, assign_scan_ids,
                    list_raw_images)
from sm_align import (FILLED_MIN_INK, LOWRES_DARK, LOWRES_REF_CONTRAST, SUSPECT_MIN_INK, align_sheet, dark_ink_score,
                      full_strokes, ink_stats,
                      join_fragments, load_template, lowres_mask, lowres_strokes, page_contrast, pieces, red_mask,
                      split_touching, to_mnist)

MODEL = ROOT / "models" / "digit_cnn.onnx"
CLASSES = json.loads((ROOT / "models" / "digit_model.json").read_text(encoding="utf-8"))["classes"] \
    if (ROOT / "models" / "digit_model.json").exists() else [str(d) for d in range(10)] + ["x"]
MAX_MARK = 35
# Page height (pixels) in the photo. Real phone photos: 3700-4300 px. Below FULL_PAGE_PX the small-photo ink test
# is used and digits need LOWRES_THRESHOLD confidence; below MIN_PAGE_PX the cover is refused. Measured on the 48
# demo covers shrunk to that size (also dull / blurred / JPEG-70 copies at ~1490 px): unflagged wrong values
# 0 at 1476-1500, 1800, 2100 px; 1 at 1300 px. About 1 in 3 values is flagged below 3000 px (1 in 30 above).
# A whole cover in a 1080p frame (phone upright, cover filling the picture) is ~1500 px tall.
FULL_PAGE_PX = 3000
MIN_PAGE_PX = 1475
LOWRES_THRESHOLD = 0.95
MARK_CELLS = [f"q{q}_{c}" for q in range(1, 9) for c in "abcd"]


def classify(net, m: np.ndarray) -> np.ndarray:
    """Probabilities for one ink piece, prepared exactly like the training digits."""
    x = to_mnist(m).astype(np.float32)
    x /= max(float(x.max()), 1.0)
    net.setInput(x[None, None])
    z = net.forward()[0]
    e = np.exp(z - z.max())
    return e / e.sum()


UNREAD_INK = "ink found but nothing readable"
NOT_RED = "dark writing but no red ink found"


def read_crop(net, colour: np.ndarray, single: bool, dark: float = LOWRES_DARK, lowres: bool = False) -> dict:
    """One roll box (single digit) or mark cell -> {'value', 'conf', 'note'}; value '' = empty.
    dark: the photo's darkness cut-off (see read_sheet); lowres: use the small-photo ink test."""
    strict = lowres_mask(colour, dark) if lowres else red_mask(colour)
    if ink_stats(strict)[1] < FILLED_MIN_INK:
        if dark_ink_score(colour, dark) >= SUSPECT_MIN_INK:  # never call a cell blank while something dark is in it
            return {"value": "", "conf": 0.0, "note": NOT_RED}
        return {"value": "", "conf": 1.0, "note": ""}
    ps = join_fragments(pieces(lowres_strokes(colour, strict, dark) if lowres else full_strokes(colour, strict)))
    if not ps:
        return {"value": "", "conf": 0.0, "note": UNREAD_INK}
    if single:
        ps = [np.bitwise_or.reduce(ps)]
    digits, confs = [], []
    for m in ps:
        p = classify(net, m)
        xs = np.nonzero(m)[1]
        if not single and np.ptp(xs) + 1 > 0.8 * (np.ptp(np.nonzero(m)[0]) + 1):  # wide: maybe two touching digits
            halves = [classify(net, h) for h in split_touching(m)]
            if min(h.max() for h in halves) > p.max():
                for h in halves:
                    digits.append(CLASSES[int(h.argmax())])
                    confs.append(float(h.max()))
                continue
        digits.append(CLASSES[int(p.argmax())])
        confs.append(float(p.max()))
    kept = [d for d in digits if d != "x"]
    note = "crossed out" if "x" in digits and not kept else ("correction beside crossed-out mark" if "x" in digits else "")
    value = str(int("".join(kept))) if kept else ""
    if single and len(kept) != 1:
        value = ""
    return {"value": value, "conf": min(confs), "note": note}


def read_sheet(net, t, img: np.ndarray, threshold: float) -> dict:
    a = align_sheet(img, t)
    if not a["ok"]:
        return {"status": "fail", "reasons": [a["reason"]], "section": "", "roll": "", "cells": {}}
    if a["page_px"] < MIN_PAGE_PX:
        return {"status": "fail", "section": "", "roll": "", "cells": {},
                "reasons": [f"photo resolution too low (page {a['page_px']:.0f} px tall, needs {MIN_PAGE_PX}+): "
                            "move the camera closer so the cover fills the picture, phone held upright"]}
    warp, named = a["warp"], a["named"]
    dark = LOWRES_DARK * min(1.0, page_contrast(warp, named) / LOWRES_REF_CONTRAST)  # dull photo -> lower
    lowres = a["page_px"] < FULL_PAGE_PX
    if lowres:  # small photo: own ink test, and only very sure digits pass unflagged
        threshold = max(threshold, LOWRES_THRESHOLD)
    def crop(name):
        x0, y0, x1, y1 = named[name]
        return warp[y0:y1, x0:x1]
    reasons = list(a["reasons"])
    section = a["letter"] or ""
    cells = {}
    for name in MARK_CELLS:
        r = read_crop(net, crop(name), single=False, dark=dark, lowres=lowres)
        flags = []
        if r["conf"] < threshold:
            flags.append(f"low confidence {r['conf']:.2f}")
        if r["note"] in (UNREAD_INK, NOT_RED):
            flags.append(r["note"])
        if r["value"] and int(r["value"]) > MAX_MARK:
            flags.append(f"mark above {MAX_MARK}")
        q = int(name[1])
        if section and r["value"] and q not in SECTION_QUESTIONS[section]:
            flags.append(f"written in Q{q}, outside Section {section}")
        cells[name] = {**r, "flag": "; ".join(flags)}
    for q in range(1, 9):
        vals = [int(cells[f"q{q}_{c}"]["value"]) for c in "abcd" if cells[f"q{q}_{c}"]["value"]]
        if sum(vals) > MAX_MARK:
            for c in "abcd":
                if cells[f"q{q}_{c}"]["value"]:
                    cells[f"q{q}_{c}"]["flag"] = "; ".join(filter(None, [cells[f"q{q}_{c}"]["flag"],
                                                                         f"Q{q} total {sum(vals)} > {MAX_MARK}"]))
    roll_reads = [read_crop(net, crop(f"roll_{k + 1}"), single=True, dark=dark, lowres=lowres) for k in range(7)]
    roll = "".join(r["value"] for r in roll_reads)
    roll_conf = min(r["conf"] for r in roll_reads)
    if not roll:
        reasons.append("roll boxes empty - read the roll number on the cover / ask the teacher")
    elif len(roll) != 7 or any(not r["value"] for r in roll_reads):
        reasons.append(f"roll number incomplete ({roll!r})")
    elif roll_conf < threshold:
        reasons.append(f"roll number unsure (confidence {roll_conf:.2f})")
    for k, r in enumerate(roll_reads):
        cells[f"roll_{k + 1}"] = {**r, "flag": ""}
    flagged = [n for n in MARK_CELLS if cells[n]["flag"]]
    status = "review" if reasons or flagged else "ok"
    return {"status": status, "reasons": reasons, "section": section, "roll": roll, "roll_conf": roll_conf,
            "cells": cells, "flagged": flagged, "warp": warp, "named": named}


def preview(sid, res, path):
    tile = 120
    canvas = np.full((tile * 9 + 60, tile * 7, 3), 255, np.uint8)
    cv2.putText(canvas, f"{sid}  section {res['section'] or '?'}  roll {res['roll'] or '?'}  {res['status']}",
                (6, 36), 0, 0.9, (0, 0, 0), 2)
    warp, named = res["warp"], res["named"]
    def put(name, r, c):
        x0, y0, x1, y1 = named[name]
        t = cv2.resize(warp[y0:y1, x0:x1], (tile - 6, tile - 6), interpolation=cv2.INTER_AREA)
        cell = res["cells"][name]
        colour = (0, 140, 255) if cell["flag"] else ((0, 160, 0) if cell["value"] else (200, 200, 200))
        y, x = 60 + r * tile + 3, c * tile + 3
        canvas[y:y + tile - 6, x:x + tile - 6] = t
        cv2.rectangle(canvas, (x - 2, y - 2), (x + tile - 5, y + tile - 5), colour, 3)
        label = cell["value"] if cell["value"] else ("x" if cell["note"] == "crossed out" else "")
        if label:
            cv2.putText(canvas, label, (x + 4, y + 28), 0, 1.0, (255, 0, 0), 2)
    for k in range(7):
        put(f"roll_{k + 1}", 0, k)
    for q in range(8):
        for ci, c in enumerate("abcd"):
            put(f"q{q + 1}_{c}", q + 1, ci)
        cv2.putText(canvas, f"Q{q + 1}", (4 * tile + 20, 60 + (q + 1) * tile + 70), 0, 1.2, (90, 90, 90), 2)
    cv2.imwrite(str(path), canvas, [cv2.IMWRITE_JPEG_QUALITY, 80])


def save_crops(sid: str, res: dict) -> None:
    """Crops the Excel review sheet shows: every flagged cell, the roll-box row and the written roll on the cover."""
    out = ROOT / "data" / "processed" / sid / "read"
    shutil.rmtree(out, ignore_errors=True)  # no stale crops from an earlier run
    out.mkdir(parents=True, exist_ok=True)
    warp, named = res["warp"], res["named"]
    boxes = {c: named[c] for c in res["flagged"]}
    boxes["roll_row"] = [named["roll_1"][0], named["roll_1"][1], named["roll_7"][2], named["roll_7"][3]]
    boxes["header_roll"] = named["header_roll"]
    boxes["letter"] = named["letter"]
    for name, (x0, y0, x1, y1) in boxes.items():
        cv2.imwrite(str(out / f"{name}.png"), warp[y0:y1, x0:x1])


def evaluate(results: dict, threshold: float) -> list[str]:
    """Compare with verified labels: how many wrong values would reach Excel unflagged."""
    if not LABELS_CSV.exists():
        return []
    with LABELS_CSV.open(encoding="utf-8-sig", newline="") as f:
        labels = {r["scan_id"]: r for r in csv.DictReader(f) if r["label_status"].strip().lower() == "verified"}
    test_ids = set()
    idx = ROOT / "data" / "digits" / "index.csv"
    if idx.exists():
        with idx.open(encoding="utf-8-sig", newline="") as f:
            test_ids = {r["scan_id"] for r in csv.DictReader(f) if r["split"] == "test"}
    from importlib import import_module
    noted_cells = import_module("05_evaluate_m2").noted_cells
    lines = []
    for name, keep in (("held-out students (never trained on)", lambda s: s in test_ids),
                       ("all labeled sheets (includes training sheets - optimistic)", lambda s: True)):
        ids = [s for s in results if s in labels and keep(s) and results[s]["status"] != "fail"]
        if not ids:
            continue
        lines.append(f"== {name}: {len(ids)} sheets")
        for th in sorted({0.5, 0.7, 0.8, 0.9, 0.95, threshold}):
            n = acc = acc_wrong = flagged = 0
            for s in ids:
                lab, cells = labels[s], results[s]["cells"]
                noted = noted_cells(lab["notes"])
                for c in MARK_CELLS:
                    truth = "" if c in noted else lab[c].strip()
                    truth = str(int(truth)) if truth.isdigit() else truth
                    got = cells[c]
                    if not truth and not got["value"]:
                        continue  # blank and read as blank - not counted, would inflate the score
                    n += 1
                    rule_flag = any(f and not f.startswith("low confidence") for f in got["flag"].split("; "))
                    if got["conf"] < th or rule_flag:
                        flagged += 1
                    else:
                        acc += 1
                        acc_wrong += got["value"] != truth
            lines.append(f"  threshold {th:.2f}: written cells {n} | flagged {flagged} ({flagged / max(n, 1):.1%}) | "
                         f"accepted {acc}, of which WRONG {acc_wrong} ({acc_wrong / max(acc, 1):.2%})")
        rolls = [(results[s]["roll"], labels[s]["roll_no"].strip(), bool(results[s]["reasons"])) for s in ids
                 if "roll boxes left empty" not in labels[s]["notes"].lower()]
        lines.append(f"  roll numbers: {sum(r == t for r, t, _ in rolls)}/{len(rolls)} exactly right; "
                     f"wrong and NOT flagged: {sum(r != t and not f for r, t, f in rolls)}")
        lines.append(f"  section letter right: {sum(results[s]['section'] == labels[s]['section'] for s in ids)}/{len(ids)}")
    return lines


def collect(inputs: list) -> tuple[list[Path], dict]:
    """Photos from files/folders (scan id = file name), or data\\raw when none are given."""
    if not inputs:
        files = list_raw_images()
        return files, assign_scan_ids(files)
    files = []
    for i in map(Path, inputs):
        files += sorted(p for p in i.rglob("*") if p.suffix.lower() in SUPPORTED and not p.name.startswith("~$")) \
            if i.is_dir() else [i]
    return files, {p: p.stem for p in files}


def load_model():
    if not MODEL.exists():
        raise SystemExit("models\\digit_cnn.onnx missing - run scripts\\08_train_digits.py first")
    return cv2.dnn.readNetFromONNX(str(MODEL)), load_template()


def read_one(net, t, path: Path, sid: str, threshold: float = 0.8) -> dict:
    """Read one photo (and save its preview + crops for the review screen)."""
    img = cv2.imread(str(path))
    res = read_sheet(net, t, img, threshold) if img is not None else \
        {"status": "fail", "reasons": ["cannot open image"], "section": "", "roll": "", "cells": {}}
    res["file"] = str(path)
    if res["status"] != "fail":
        preview(sid, res, PREVIEW_DIR / "m5" / f"{sid}.jpg")
        save_crops(sid, res)
    return res


def write_reports(results: dict, threshold: float = 0.8) -> list[str]:
    """Batch checks + reports for everything read so far. Safe to call again after each new photo."""
    dup_note = "sheet in this batch"
    dup = Counter((r["roll"], r["section"]) for r in results.values() if r["roll"] and r["section"])
    for r in results.values():
        r["reasons"] = [x for x in r["reasons"] if not x.endswith(dup_note)]  # recomputed every call
        if r["roll"] and dup[(r["roll"], r["section"])] > 1:
            r["reasons"].append(f"roll {r['roll']} has more than one Section {r['section']} {dup_note}")
        if r["status"] != "fail":
            r["status"] = "review" if r["reasons"] or r.get("flagged") else "ok"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    with (REPORT_DIR / "m5_sheets.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["scan_id", "file", "status", "section", "roll_no", "flagged_cells", "reasons", *MARK_CELLS])
        for sid, r in results.items():
            w.writerow([sid, r["file"], r["status"], r["section"], r["roll"], " ".join(r.get("flagged", [])),
                        "; ".join(r["reasons"]), *[r["cells"].get(c, {}).get("value", "") for c in MARK_CELLS]])
    with (REPORT_DIR / "m5_cells.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["scan_id", "cell", "value", "confidence", "flag", "note"])
        for sid, r in results.items():
            for c, v in r["cells"].items():
                w.writerow([sid, c, v["value"], f"{v['conf']:.3f}", v["flag"], v["note"]])
    st = Counter(r["status"] for r in results.values())
    summary = [f"Sheets: {len(results)} | ok {st['ok']} | review {st['review']} | fail {st['fail']} "
               f"(threshold {threshold})"]
    summary += evaluate(results, threshold)
    (REPORT_DIR / "m5_evaluation.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
    return summary


def run(files: list[Path], ids: dict, threshold: float = 0.8, progress=None) -> list[str]:
    """Read every photo, write the reports/crops/previews, return the summary lines.
    progress(i, n, line) is called after each photo (the app uses it for its progress bar)."""
    net, t = load_model()
    (PREVIEW_DIR / "m5").mkdir(parents=True, exist_ok=True)
    results = {}
    for n, path in enumerate(files, start=1):
        t0 = time.time()
        sid = ids[path]
        res = results[sid] = read_one(net, t, path, sid, threshold)
        line = (f"{sid}: {res['status']:6s} section {res['section'] or '?'} roll {res['roll'] or '?':7s} "
                f"flagged cells {len(res.get('flagged', []))}  {'; '.join(res['reasons'])}  ({time.time() - t0:.1f}s)")
        (progress or (lambda i, k, txt: print(txt, flush=True)))(n, len(files), line)
    return write_reports(results, threshold)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="*", help="photos or folders (default: data\\raw)")
    ap.add_argument("--threshold", type=float, default=0.8)  # held-out: 0 silent errors from 0.7 up; 0.8 keeps flags <= 10%
    args = ap.parse_args()
    files, ids = collect(args.inputs)
    summary = run(files, ids, args.threshold)
    print("\n" + "\n".join(summary))
    print("Results: reports\\m5_sheets.csv, reports\\m5_cells.csv | previews: data\\preview\\m5\\")


if __name__ == "__main__":
    main()
