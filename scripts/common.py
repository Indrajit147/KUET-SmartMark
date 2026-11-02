"""Shared paths, field names and helpers for the KUET SmartMark scripts (stdlib only)."""
from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
PREVIEW_DIR = ROOT / "data" / "preview"
REPORT_DIR = ROOT / "reports"
ANNOTATION_DIR = ROOT / "annotations"
LABELS_CSV = ANNOTATION_DIR / "sheet_labels.csv"
LABELS_XLSX = ANNOTATION_DIR / "KUET_SmartMark_label_template.xlsx"
SUPPORTED = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}

MARKS = [f"q{i}_{part}" for i in range(1, 9) for part in "abcd"]
LABEL_FIELDS = ["scan_id", "relative_path", "section", "roll_no", "label_status", "image_quality", *MARKS, "notes"]
SECTION_QUESTIONS = {"A": range(1, 5), "B": range(5, 9)}


def list_raw_images() -> list[Path]:
    """All scans under data/raw, skipping hidden/temp files such as Office '~$' locks."""
    return sorted(
        p for p in RAW_DIR.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED and not p.name.startswith(("~$", "."))
    )


def assign_scan_ids(files: list[Path]) -> dict[Path, str]:
    """scan_id = filename stem (e.g. 'a001'), which matches the Excel sheet.

    If two files in different subfolders share a stem, both get the subfolder
    path prefixed (e.g. 'section_A/a001') so IDs remain unique and stable.
    """
    counts: dict[str, int] = {}
    for p in files:
        counts[p.stem.lower()] = counts.get(p.stem.lower(), 0) + 1
    ids = {}
    for p in files:
        if counts[p.stem.lower()] == 1:
            ids[p] = p.stem
        else:
            ids[p] = p.relative_to(RAW_DIR).with_suffix("").as_posix()
    return ids


def section_hint_from_name(scan_id: str) -> str | None:
    """Naming convention used in this dataset: a### = Section A, b### = Section B."""
    m = re.match(r"^([ab])\d", Path(scan_id).name, flags=re.IGNORECASE)
    return m.group(1).upper() if m else None


def normalize_mark(value: str) -> str:
    """'05' -> '5', '12.0' -> '12', ' ? ' -> '?'. Anything else is returned stripped so the validator flags it."""
    v = str(value).strip()
    if re.fullmatch(r"\d+(?:\.0+)?", v):
        return str(int(float(v)))
    return v


def normalize_roll(value: str) -> str:
    v = str(value).strip()
    if re.fullmatch(r"\d+\.0+", v):  # Excel stored it as a number
        v = v.split(".")[0]
    return v


_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _col_index(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group():
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_xlsx_first_sheet(path: Path) -> list[list[str]]:
    """Read the first worksheet of an .xlsx as text rows, without third-party libraries."""
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", _NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        first = wb.find("m:sheets/m:sheet", _NS)
        rid = first.get(f"{{{_NS['r']}}}id")
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        target = next(r.get("Target") for r in rels.iter(f"{_REL_NS}Relationship") if r.get("Id") == rid)
        target = target.lstrip("/")
        sheet_path = target if target.startswith("xl/") else "xl/" + target
        sheet = ET.fromstring(z.read(sheet_path))

    rows = []
    for row in sheet.find("m:sheetData", _NS):
        cells: dict[int, str] = {}
        for c in row:
            kind = c.get("t")
            v = c.find("m:v", _NS)
            if kind == "inlineStr":
                text = "".join(t.text or "" for t in c.iter(f"{{{_NS['m']}}}t"))
            elif v is None:
                continue
            elif kind == "s":
                text = shared[int(v.text)]
            else:
                text = v.text or ""
            cells[_col_index(c.get("r"))] = text
        if cells:
            width = max(cells) + 1
            rows.append([cells.get(i, "") for i in range(width)])
        else:
            rows.append([])
    return rows


def windows_basename(path_text: str) -> str:
    """Basename of a path written with either / or \\ separators (e.g. an E:\\ path from Excel)."""
    return PureWindowsPath(path_text.strip()).name
