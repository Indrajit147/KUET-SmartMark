from __future__ import annotations

import csv
import re
from collections import defaultdict

from common import (LABELS_CSV, REPORT_DIR, ROOT, SECTION_QUESTIONS, section_hint_from_name)

MAX_PER_QUESTION = 35


def main() -> None:
    if not LABELS_CSV.exists():
        raise SystemExit("Run scripts\\01_inspect_dataset.py (and 02_import_xlsx_labels.py if you label in Excel) first")
    with LABELS_CSV.open(encoding="utf-8-sig", newline="") as f:
        records = list(csv.DictReader(f))
    verified = 0
    issues: list[tuple[str, str, str]] = []  # (scan_id, level, message)
    student_key: dict[tuple[str, str], list[str]] = defaultdict(list)  # (roll, section) -> scan_ids
    rolls_by_section: dict[str, set[str]] = defaultdict(set)

    def flag(sid: str, message: str, level: str = "ERROR") -> None:
        issues.append((sid, level, message))

    for record in records:
        sid = record["scan_id"]
        if not (ROOT / record["relative_path"]).exists():
            flag(sid, f"Image file not found: {record['relative_path']}")
        status = record["label_status"].strip().lower()
        if status not in {"unlabeled", "in_progress", "verified"}:
            flag(sid, "Unexpected label_status")
            continue
        if status != "verified":
            continue
        verified += 1
        section = record["section"].strip().upper()
        if section not in SECTION_QUESTIONS:
            flag(sid, "Verified sheet must have section A or B")
            continue
        hint = section_hint_from_name(sid)
        if hint and hint != section:
            flag(sid, f"section is {section} but file name suggests {hint} - check the letter printed on the sheet")
        roll = record["roll_no"].strip()
        if not re.fullmatch(r"\d{7}", roll):
            flag(sid, "Roll number must have seven digits; check form specification")
        else:
            student_key[(roll, section)].append(sid)
            rolls_by_section[section].add(roll)
        if record["image_quality"].strip().lower() not in {"good", "review", "unusable"}:
            flag(sid, "Set image_quality to good/review/unusable")
        attempted = 0
        for n in range(1, 9):
            values = [record[f"q{n}_{letter}"].strip() for letter in "abcd"]
            if n not in SECTION_QUESTIONS[section]:
                if any(values):
                    flag(sid, f"Q{n} is outside Section {section}")
                continue
            if any(v != "" for v in values):
                attempted += 1
            if any(v == "?" for v in values):
                flag(sid, f"Q{n} has unreadable mark; manual review needed", "REVIEW")
            numerics = []
            for v in values:
                if not v or v == "?":
                    continue
                if not re.fullmatch(r"(?:0|[1-9]\d*)", v) or int(v) > MAX_PER_QUESTION:
                    flag(sid, f"Q{n} contains invalid label {v!r}")
                else:
                    numerics.append(int(v))
            if len(numerics) == sum(bool(v) for v in values):
                total = sum(numerics)
                if total > MAX_PER_QUESTION:
                    flag(sid, f"Q{n} total {total} exceeds {MAX_PER_QUESTION}: verify transcription")
                elif total == MAX_PER_QUESTION and any(values):
                    flag(sid, f"Q{n} total exactly {MAX_PER_QUESTION}: manual check required", "REVIEW")
        if attempted < 3:
            flag(sid, f"Only {attempted} attempted questions in Section {section}", "REVIEW")

    # One student has at most one A script and one B script: (roll_no, section) must be unique.
    for (roll, section), sids in student_key.items():
        if len(sids) > 1:
            for sid in sids:
                flag(sid, f"Roll {roll} has more than one Section {section} sheet: {', '.join(sids)}")
    both = rolls_by_section["A"] & rolls_by_section["B"]
    only_a = rolls_by_section["A"] - rolls_by_section["B"]
    only_b = rolls_by_section["B"] - rolls_by_section["A"]

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report = REPORT_DIR / "label_issues.csv"
    with report.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["scan_id", "level", "message"])
        w.writerows(issues)

    errors = sum(1 for _, level, _ in issues if level == "ERROR")
    print(f"Total sheets: {len(records)} | Verified labels: {verified}")
    print(f"Students (roll numbers) with both A and B: {len(both)} | only A: {len(only_a)} | only B: {len(only_b)}")
    if only_a or only_b:
        print(f"  Only A: {', '.join(sorted(only_a)) or '-'} | Only B: {', '.join(sorted(only_b)) or '-'}")
    print(f"Validation flags: {len(issues)} ({errors} errors, {len(issues) - errors} to review)")
    for sid, level, message in issues[:30]:
        print(f" - [{level}] {sid}: {message}")
    if len(issues) > 30:
        print(f"... and {len(issues) - 30} more - see {report.relative_to(ROOT)}")
    print(f"Full list: {report.relative_to(ROOT)}")
    if verified == 0:
        print("Next: manually label and mark 10-12 sheets as verified, then rerun.")


if __name__ == "__main__":
    main()
