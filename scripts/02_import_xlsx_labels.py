"""Copy labels from annotations/KUET_SmartMark_label_template.xlsx into annotations/sheet_labels.csv.

The Excel sheet is the place you type labels; sheet_labels.csv is what the validator
(and later milestones) read. Rows are matched to images by file name (a001.jpg),
taken from relative_path, or from scan_id if relative_path is empty.

Usage:  .venv\\Scripts\\python.exe scripts\\02_import_xlsx_labels.py [path\\to\\labels.xlsx]
"""
from __future__ import annotations

import csv
import shutil
import sys
from pathlib import Path

from common import (LABEL_FIELDS, LABELS_CSV, LABELS_XLSX, MARKS, ROOT, assign_scan_ids, list_raw_images,
                    normalize_mark, normalize_roll, read_xlsx_first_sheet, windows_basename)


def main() -> None:
    xlsx = Path(sys.argv[1]) if len(sys.argv) > 1 else LABELS_XLSX
    if not xlsx.exists():
        raise SystemExit(f"Label workbook not found: {xlsx}")
    files = list_raw_images()
    if not files:
        raise SystemExit("No scans in data\\raw\\ - nothing to match labels to.")
    ids = assign_scan_ids(files)
    by_name = {p.name.lower(): p for p in files}
    by_stem = {p.stem.lower(): p for p in files}

    try:
        table = read_xlsx_first_sheet(xlsx)
    except PermissionError:
        raise SystemExit("Cannot open the workbook - close it in Excel (or save it) and run again.")
    header = [h.strip().lower() for h in table[0]]
    missing_cols = [c for c in LABEL_FIELDS if c not in header]
    if missing_cols:
        raise SystemExit(f"Workbook header is missing columns: {', '.join(missing_cols)}")
    col = {name: header.index(name) for name in LABEL_FIELDS}

    imported: dict[Path, dict] = {}
    warnings = []
    for excel_row, values in enumerate(table[1:], start=2):
        get = lambda name: (values[col[name]] if col[name] < len(values) else "").strip()
        if not any(get(name) for name in LABEL_FIELDS):
            continue  # blank row
        ref = get("relative_path")
        path = by_name.get(windows_basename(ref).lower()) if ref else None
        if path is None and get("scan_id"):
            path = by_stem.get(Path(get("scan_id")).stem.lower())
        if path is None:
            warnings.append(f"Excel row {excel_row}: no image matches relative_path={ref!r} scan_id={get('scan_id')!r}")
            continue
        if path in imported:
            warnings.append(f"Excel row {excel_row}: {path.name} already labeled in Excel row "
                            f"{imported[path]['_row']} - keeping the first one")
            continue
        record = {
            "scan_id": ids[path],
            "relative_path": path.relative_to(ROOT).as_posix(),
            "section": get("section").upper(),
            "roll_no": normalize_roll(get("roll_no")),
            "label_status": get("label_status").lower() or "unlabeled",
            "image_quality": get("image_quality").lower(),
            "notes": get("notes"),
            "_row": excel_row,
        }
        for m in MARKS:
            record[m] = normalize_mark(get(m))
        if "demo row only" in record["notes"].lower():
            warnings.append(f"Excel row {excel_row} ({path.name}): notes still say DEMO ROW ONLY")
        imported[path] = record

    if LABELS_CSV.exists():
        backup = LABELS_CSV.with_name("sheet_labels.backup.csv")
        shutil.copy2(LABELS_CSV, backup)
    with LABELS_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LABEL_FIELDS, extrasaction="ignore")
        w.writeheader()
        for path in files:
            if path in imported:
                w.writerow(imported[path])
            else:
                blank = {k: "" for k in LABEL_FIELDS}
                blank.update({"scan_id": ids[path], "relative_path": path.relative_to(ROOT).as_posix(),
                              "label_status": "unlabeled"})
                w.writerow(blank)

    unlabeled = [p.name for p in files if p not in imported]
    print(f"Workbook: {xlsx.name}")
    print(f"Images in data\\raw: {len(files)} | Labeled rows imported: {len(imported)} | Images with no Excel row: {len(unlabeled)}")
    if unlabeled:
        print("  No label yet: " + ", ".join(unlabeled[:20]) + (" ..." if len(unlabeled) > 20 else ""))
    for msg in warnings:
        print(" ! " + msg)
    print(f"Written: {LABELS_CSV.relative_to(ROOT)}  (Excel is the source - edit Excel, then re-run this)")
    print("Next: run scripts\\03_validate_labels.py")


if __name__ == "__main__":
    main()
