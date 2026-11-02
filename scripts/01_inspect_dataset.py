from __future__ import annotations

import csv
import hashlib
import math

from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

from common import (ANNOTATION_DIR, LABEL_FIELDS, LABELS_CSV, PREVIEW_DIR, REPORT_DIR, ROOT,
                    assign_scan_ids, list_raw_images)

INVENTORY_FIELDS = ["scan_id", "relative_path", "width", "height", "aspect_ratio", "file_size_kb", "sha256", "check"]


def make_thumb(img: Image.Image, scan_id: str, label: str) -> Image.Image:
    tile_w, tile_h = 256, 360
    tile = Image.new("RGB", (tile_w, tile_h), "white")
    photo = ImageOps.contain(img, (236, 316), method=Image.Resampling.LANCZOS)
    tile.paste(photo, ((tile_w - photo.width) // 2, 8))
    pen = ImageDraw.Draw(tile)
    pen.text((10, 330), scan_id, fill="black")
    pen.text((10, 346), label[:33], fill="black")
    return tile


def main() -> None:
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ANNOTATION_DIR.mkdir(parents=True, exist_ok=True)
    files = list_raw_images()
    if not files:
        print("No supported images found. Copy JPG/PNG/TIFF/WEBP scans into data\\raw\\ and rerun.")
        return
    ids = assign_scan_ids(files)

    rows, thumbs, seen_hashes = [], [], {}
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        scan_id = ids[path]
        contents_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        flags = []
        if contents_hash in seen_hashes:
            flags.append("exact_duplicate_of:" + seen_hashes[contents_hash])
        else:
            seen_hashes[contents_hash] = scan_id
        width, height = 0, 0
        try:
            with Image.open(path) as opened:
                opened.verify()  # This does not fully decode the image.
            with Image.open(path) as opened:
                img = ImageOps.exif_transpose(opened).convert("RGB")
                img.load()  # Ensure image pixels are readable.
            width, height = img.size
            if width > height:
                flags.append("landscape_orientation_check")
            if min(width, height) < 1000:
                flags.append("small_pixel_dimensions_review")
            thumbs.append(make_thumb(img, scan_id, path.name))
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            flags.append("cannot_read:" + type(exc).__name__)
        rows.append({
            "scan_id": scan_id,
            "relative_path": rel,
            "width": width,
            "height": height,
            "aspect_ratio": f"{width / height:.3f}" if height else "",
            "file_size_kb": f"{path.stat().st_size / 1024:.1f}",
            "sha256": contents_hash,
            "check": ";".join(flags) if flags else "OK_manual_visual_check_still_needed",
        })

    report = REPORT_DIR / "image_inventory.csv"
    with report.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=INVENTORY_FIELDS)
        w.writeheader()
        w.writerows(rows)

    previous = {}
    if LABELS_CSV.exists():
        with LABELS_CSV.open("r", encoding="utf-8-sig", newline="") as f:
            previous = {row["scan_id"]: row for row in csv.DictReader(f)}
    with LABELS_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LABEL_FIELDS, extrasaction="ignore")
        w.writeheader()
        for item in rows:
            record = {key: "" for key in LABEL_FIELDS}
            record.update({"scan_id": item["scan_id"], "relative_path": item["relative_path"],
                           "label_status": "unlabeled"})
            if item["scan_id"] in previous:
                record.update({k: v for k, v in previous[item["scan_id"]].items() if k in record})
            w.writerow(record)

    if thumbs:
        cols, rows_count = 4, math.ceil(len(thumbs) / 4)
        sheet = Image.new("RGB", (256 * cols, 360 * rows_count), "#e5e7eb")
        for i, img in enumerate(thumbs):
            sheet.paste(img, ((i % cols) * 256, (i // cols) * 360))
        sheet.save(PREVIEW_DIR / "contact_sheet.jpg", quality=85)

    print(f"Images found: {len(files)}")
    print(f"Unreadable files: {sum(r['width'] == 0 for r in rows)}")
    print(f"Items flagged for checking: {sum(not r['check'].startswith('OK_') for r in rows)}")
    print(f"Inventory: {report.relative_to(ROOT)}")
    print(f"Annotation template: {LABELS_CSV.relative_to(ROOT)}")
    print(f"Visual contact sheet: {(PREVIEW_DIR / 'contact_sheet.jpg').relative_to(ROOT)}")
    print("Note: inspect image quality manually; image dimensions alone do not establish readability.")


if __name__ == "__main__":
    main()
