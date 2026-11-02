"""Milestone 2: align every scan to the template, crop roll boxes + mark cells, isolate red ink.

Usage:  .venv\\Scripts\\python.exe scripts\\04_align_and_crop.py [--only a001 b002 ...]

Writes, per scan (data\\processed\\<scan_id>\\):
  aligned.jpg              the photo flattened onto the 300-dpi template canvas
  roll_1..7.png, q1_a..q8_d.png, letter.png, header_roll.png      colour crops
  mask\\*.png               red-ink masks of the same crops (white = red ink)
  sheet.json               alignment quality, detected section letter, per-crop ink stats
And:
  reports\\m2_alignment.csv   one row per scan  (status ok / review / fail)
  reports\\m2_cells.csv       one row per crop  (ink amount, filled yes/no, label if known)
  data\\preview\\m2\\<scan_id>.jpg   crops laid out like the sheet, for a visual check
"""
from __future__ import annotations

import argparse
import csv
import json
import time

import cv2
import numpy as np

from common import (LABELS_CSV, PREVIEW_DIR, REPORT_DIR, ROOT, assign_scan_ids, list_raw_images)
from sm_align import FILLED_MIN_INK, align_sheet, ink_stats, load_template, red_mask

PROCESSED = ROOT / "data" / "processed"


def crop(img, box):
    x0, y0, x1, y1 = box
    return img[y0:y1, x0:x1]


def load_labels() -> dict:
    if not LABELS_CSV.exists():
        return {}
    with LABELS_CSV.open(encoding="utf-8-sig", newline="") as f:
        return {r["scan_id"]: r for r in csv.DictReader(f) if r.get("label_status", "").strip().lower() == "verified"}


def preview(scan_id, crops, filled, letter, status):
    """Crops arranged like the sheet: roll row on top, 8x4 grid below, red-ink coverage tinted."""
    tile = 120
    canvas = np.full((tile * 10 + 60, tile * 7, 3), 255, np.uint8)
    cv2.putText(canvas, f"{scan_id}  section={letter or '?'}  {status}", (6, 36), 0, 1.0, (0, 0, 0), 2)
    def put(img, mask, r, c, is_filled):
        t = cv2.resize(img, (tile - 6, tile - 6), interpolation=cv2.INTER_AREA)
        m = cv2.resize(mask, (tile - 6, tile - 6), interpolation=cv2.INTER_NEAREST)
        t[m > 0] = (0, 0, 255)
        y, x = 60 + r * tile + 3, c * tile + 3
        canvas[y:y + tile - 6, x:x + tile - 6] = t
        cv2.rectangle(canvas, (x - 2, y - 2), (x + tile - 5, y + tile - 5), (0, 170, 0) if is_filled else (200, 200, 200), 3)
    for k in range(7):
        n = f"roll_{k + 1}"
        put(crops[n][0], crops[n][1], 0, k, filled[n])
    for q in range(8):
        for ci, c in enumerate("abcd"):
            n = f"q{q + 1}_{c}"
            put(crops[n][0], crops[n][1], q + 1, ci, filled[n])
        cv2.putText(canvas, f"Q{q + 1}", (4 * tile + 20, 60 + (q + 1) * tile + 70), 0, 1.2, (90, 90, 90), 2)
    return canvas


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="scan ids to process (default: all)")
    args = ap.parse_args()

    t = load_template()
    files = list_raw_images()
    ids = assign_scan_ids(files)
    if args.only:
        files = [p for p in files if ids[p] in set(args.only)]
    labels = load_labels()
    (PREVIEW_DIR / "m2").mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    align_rows, cell_rows = [], []
    for path in files:
        sid, t0 = ids[path], time.time()
        out = PROCESSED / sid
        (out / "mask").mkdir(parents=True, exist_ok=True)
        img = cv2.imread(str(path))
        if img is None:
            align_rows.append({"scan_id": sid, "status": "fail", "reason": "cannot read image"})
            continue
        a = align_sheet(img, t)
        if not a["ok"]:
            align_rows.append({"scan_id": sid, "inliers": a["inliers"], "status": "fail", "reason": a["reason"]})
            print(f"{sid}: FAIL (inliers {a['inliers']})")
            continue
        warp, inliers, good, lines, lstats = a["warp"], a["inliers"], a["matches"], a["lines"], a["lstats"]
        letter, letter_scores, reasons, named = a["letter"], a["letter_scores"], a["reasons"], a["named"]
        crops, filled, ink = {}, {}, {}
        for name, box in named.items():
            c = crop(warp, box)
            m = red_mask(c)
            crops[name] = (c, m)
            px, score = ink_stats(m)
            ink[name] = {"red_px": px, "ink_score": score}
            filled[name] = score >= FILLED_MIN_INK
            cv2.imwrite(str(out / f"{name}.png"), c)
            cv2.imwrite(str(out / "mask" / f"{name}.png"), m)
        cv2.imwrite(str(out / "aligned.jpg"), warp, [cv2.IMWRITE_JPEG_QUALITY, 90])

        roll_filled = sum(filled[f"roll_{k + 1}"] for k in range(7))
        if roll_filled < 7:
            reasons.append(f"roll boxes: only {roll_filled}/7 written - read the header roll / ask teacher")
        status = "ok" if not reasons else "review"
        info = {"scan_id": sid, "source": path.relative_to(ROOT).as_posix(), "status": status, "reasons": reasons,
                "inliers": inliers, "matches": good, **lstats, "section_letter": letter,
                "letter_scores": letter_scores, "lines": lines, "ink": ink,
                "filled": {k: bool(v) for k, v in filled.items()}}
        (out / "sheet.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
        cv2.imwrite(str(PREVIEW_DIR / "m2" / f"{sid}.jpg"), preview(sid, crops, filled, letter, status),
                    [cv2.IMWRITE_JPEG_QUALITY, 80])

        lab = labels.get(sid)
        align_rows.append({"scan_id": sid, "status": status, "reason": "; ".join(reasons), "inliers": inliers,
                           "lines_found": f"{lstats['lines_found']}/{lstats['lines_total']}",
                           "max_line_offset_px": lstats["max_line_offset_px"], "section_letter": letter or "",
                           "label_section": lab["section"] if lab else "", "roll_boxes_written": roll_filled,
                           "seconds": f"{time.time() - t0:.1f}"})
        for name in list(named):
            if name in ("letter", "header_roll"):
                continue
            label = ""
            if lab and name.startswith("q"):
                label = lab.get(name, "").strip()
            elif lab and name.startswith("roll_"):
                roll = lab.get("roll_no", "").strip()
                k = int(name.split("_")[1]) - 1
                label = roll[k] if len(roll) == 7 else ""
            cell_rows.append({"scan_id": sid, "crop": name, "red_px": ink[name]["red_px"],
                              "ink_score": ink[name]["ink_score"], "filled": int(filled[name]),
                              "label": label, "has_label": int(lab is not None)})
        print(f"{sid}: {status:6s} inliers={inliers:4d} lines={lstats['lines_found']}/{lstats['lines_total']} "
              f"off<={lstats['max_line_offset_px']}px letter={letter} {('; '.join(reasons))}", flush=True)

    if args.only:  # a partial run must not overwrite the full-dataset reports
        print("(--only: reports not rewritten)")
    else:
        with (REPORT_DIR / "m2_alignment.csv").open("w", encoding="utf-8-sig", newline="") as f:
            cols = ["scan_id", "status", "reason", "inliers", "lines_found", "max_line_offset_px", "section_letter",
                    "label_section", "roll_boxes_written", "seconds"]
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(align_rows)
        with (REPORT_DIR / "m2_cells.csv").open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(cell_rows[0]) if cell_rows else ["scan_id"])
            w.writeheader()
            w.writerows(cell_rows)

    n = len(align_rows)
    print(f"\nScans: {n} | ok: {sum(r['status'] == 'ok' for r in align_rows)} | "
          f"review: {sum(r['status'] == 'review' for r in align_rows)} | fail: {sum(r['status'] == 'fail' for r in align_rows)}")
    print("Reports: reports\\m2_alignment.csv, reports\\m2_cells.csv | previews: data\\preview\\m2\\")
    print("Next: run scripts\\05_evaluate_m2.py to compare against your labels")


if __name__ == "__main__":
    main()
