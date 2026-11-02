# KUET SmartMark labeling rules

Label only copies of original scans you are authorized to use. Keep images and CSV private.

## Fields

- `scan_id`: The image file name without extension (`a001`); do not change.
- `relative_path`: Path relative to project root; do not change.
- `section`: `A` for questions 1–4 or `B` for questions 5–8 — copy the big letter printed top-right of the sheet, not the file name. If unidentified, leave blank.
- `roll_no`: Manually transcribe the seven boxed digits. Keep as TEXT so leading zeros are preserved.
- `label_status`: `unlabeled`, `in_progress`, or `verified`. **Do not mark verified until every applicable cell has been checked visually.**
- `image_quality`: `good`, `review`, or `unusable`. Quality is checked by a person, not inferred from file size.
- `q1_a` ... `q8_d`: Raw handwritten marks. For a verified sheet, use:
  - Empty value = truly blank (do not enter 0 for blanks).
  - `0` = handwritten zero, which counts as attempted.
  - `1` ... `35` = transcribed numeric marks.
  - `?` = unreadable; flag for review, never guess.
  - Written with a leading zero (`05`, `01`)? Enter the number (`5`, `1`); the import script also normalises this.
  - Scratched out / scribbled over with no clean replacement = leave blank and write e.g. `Q8 crossed out` in `notes`.
    If a new number is written beside the crossed-out one, label the new number and note it.
  - For section A, only label Q1–Q4; leave Q5–Q8 blank. Vice versa for section B.
- `notes`: Any uncertain reading or unusual form.

**Never alter raw marks to implement the highest-three rule.** Raw marks are labels; scoring will be computed separately.

## First batch

Label 10–12 sheets, including both sections if represented. Have a second person verify at least a few independently if possible. Include sheets with explicit zeros, blank cells, crowded writing, crosses/overwrites and a variety of two-digit values. Do not intentionally share student-identifying screenshots publicly.

## Important

Rows created by the script have `label_status=unlabeled`; blank mark cells in those rows mean *not labeled yet*, not that all questions were unanswered.

Type labels in `KUET_SmartMark_label_template.xlsx` and run `scripts\02_import_xlsx_labels.py` to rebuild `sheet_labels.csv`; don't edit the CSV by hand. Keep `roll_no` as a text cell in Excel so leading zeros are never lost.

A script is identified by **roll_no + section** (each student has one A and one B cover with the same roll).
