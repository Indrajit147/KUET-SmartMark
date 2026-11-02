# KUET SmartMark — Milestone 1 Starter

This project is an **offline dataset inspection and manual-label preparation kit**. It does not perform OCR, generate grades, or connect to Excel/Sheets yet.

## Requirements

- Windows laptop, VS Code, a **standard** Python 3.10–3.14 (not the free-threaded "3.14t" build: OpenCV has no ready-made package for it). `py -0p` lists the installed versions.
- Permission to use the scans; keep real exam data private
- At least one JPG, JPEG, PNG, BMP, TIFF or WEBP scan

## 1 — Set up (Command Prompt)

Open **Command Prompt** (or VS Code's terminal set to *Command Prompt*) and run:

```bat
E:
cd "E:\3-2\KUET_SmartMark_Milestone1_Starter\KUET_SmartMark_Starter"
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements.txt
```

`--only-binary=:all:` stops pip from trying to compile a package from source (that needs Visual Studio).
The virtual environment lives inside this folder on E:, so nothing is installed to C:.
(To keep pip's download cache off C: too, run `set PIP_CACHE_DIR=E:\pip-cache` first.)

## 2 — Place scans

Copy your scans to `data\raw\`. Do NOT overwrite or modify originals. Subfolders are allowed (`data\raw\section_A\`, ...).

Naming convention: `a###.jpg` for Section A covers and `b###.jpg` for Section B covers. The validator uses the first letter to catch a wrong `section` value.

If the source consists of PDF pages, export them to high-resolution JPG/PNG first; do not take low-resolution screenshots.

## 3 — Inspect

```bat
.venv\Scripts\python.exe scripts\01_inspect_dataset.py
```

Generated files:

- `reports\image_inventory.csv`: image dimensions, file hashes, probable exact duplicates, and warnings to review.
- `data\preview\contact_sheet.jpg`: quick visual overview of the scanned sheets.
- `annotations\sheet_labels.csv`: label rows matched to each scan. `scan_id` is the file name without extension (`a001`).

## 4 — Label in Excel, then import

Read `annotations\LABELING_GUIDE.md`, then type labels in `annotations\KUET_SmartMark_label_template.xlsx`
(one row per scan; `relative_path` or `scan_id` must name the image, e.g. `a001`). Save, then:

```bat
.venv\Scripts\python.exe scripts\02_import_xlsx_labels.py
```

This rebuilds `annotations\sheet_labels.csv` from the workbook (a copy of the previous CSV is kept as
`sheet_labels.backup.csv`). Excel is the source of truth — always edit Excel and re-import; don't hand-edit the CSV.
Close the workbook in Excel if the script says it can't open it.

## 5 — Validate the labels

```bat
.venv\Scripts\python.exe scripts\03_validate_labels.py
```

- `ERROR` = the label is wrong or inconsistent (wrong section for the file name, mark outside the section,
  bad roll number, mark > 35, the same roll + section appearing twice, missing image file).
- `REVIEW` = probably fine but worth a look (`?` marks, a question totalling exactly 35, fewer than 3 questions attempted).
- The full list is written to `reports\label_issues.csv`.

The validator checks annotation consistency only; it does **not** judge image recognition accuracy.

## Identity of a script

A student has one Section A cover and one Section B cover with the same roll number.
So a script is identified by **roll_no + section**, never roll_no alone. Every later step (merging, the final
marks spreadsheet) must key on both.

## 6 — Template and region map (Milestone 1, already built)

`templates\kuet_cover_v1\` holds the alignment template made from photo a012 (there was no blank PDF),
the region map `regions.json` (7 roll boxes, the 8x4 marks grid, the A/B letter, the header roll field)
and `regions_preview.jpg` showing those regions. Rebuild only if the cover design changes:

```bat
.venv\Scripts\python.exe scripts\build_template.py --ref a012 --ref-b b012
```

Common photo problems and how they are handled: `docs\IMAGE_ISSUES.md`.

## 7 — Align and crop every scan (Milestone 2)

```bat
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe scripts\04_align_and_crop.py
.venv\Scripts\python.exe scripts\05_evaluate_m2.py
```

- `data\processed\<scan_id>\`: aligned page, 7 roll-box crops, 32 mark-cell crops, red-ink masks (`mask\`), `sheet.json`.
- `data\preview\m2\<scan_id>.jpg`: crops laid out like the sheet, red ink tinted, written cells boxed green — check these by eye.
- `reports\m2_alignment.csv`: per scan `ok` / `review` / `fail` with the reason (e.g. empty roll boxes).
- `reports\m2_evaluation.txt`: the M2 exit-gate check against your labels.
- About 1–2 seconds per photo on a laptop CPU (48 photos in under 2 minutes).

## 8 — Digit dataset from your sheets (Milestone 3)

```bat
.venv\Scripts\python.exe scripts\06_extract_digits.py
```

- Splits every written cell and roll box into single digits, labels them from `sheet_labels.csv`
  and saves them MNIST-style (28x28) in `data\digits\train\<class>\` and `data\digits\test\<class>\`.
  `data\digits\index.csv` lists every image. Class `x` = scribble / dash / crossed-out = read as EMPTY.
- Leading zeros ("09" labeled 9) and faint, broken strokes are handled automatically.
- Cells it cannot match go to `reports\m3_review.csv`. Fix a cell by adding a line to
  `annotations\digit_overrides.csv` (`scan_id,crop,labels,why`, e.g. `b008,q7_c,x4,...`).
- Test set = 6 whole students held out (fixed random choice), never used for training.
- Visual check: `data\preview\m3_digits.jpg`.

## 9 — Public digit datasets (Milestone 3)

Download EMNIST "digits" (280,000 handwritten digits, NIST) straight to E: with Windows' built-in
`curl` and `tar` (about 560 MB download, ~20 MB kept):

```bat
cd /d "E:\3-2\KUET_SmartMark_Milestone1_Starter\KUET_SmartMark_Starter"
mkdir data\external\emnist
curl -L -o data\external\emnist\gzip.zip https://biometrics.nist.gov/cs_links/EMNIST/gzip.zip
tar -xf data\external\emnist\gzip.zip -C data\external\emnist gzip/emnist-digits-train-images-idx3-ubyte.gz gzip/emnist-digits-train-labels-idx1-ubyte.gz gzip/emnist-digits-test-images-idx3-ubyte.gz gzip/emnist-digits-test-labels-idx1-ubyte.gz
del data\external\emnist\gzip.zip
.venv\Scripts\python.exe scripts\07_import_public_digits.py
```

Result: `data\digits\public.npz` + `data\preview\m3_public.jpg` (each row one digit, upright).
If the NIST download fails, MNIST works too: put its four `*-idx?-ubyte.gz` files in `data\external\mnist\`.

## 10 — Train the digit reader (Milestone 4)

Runs on your laptop CPU; your images never leave the machine. One-time install (into `.venv` on E:):

```bat
set PIP_CACHE_DIR=E:\pip-cache
.venv\Scripts\python.exe -m pip install --only-binary=:all: torch onnx onnxscript
```

Then a 2-minute setup check, followed by the real run (about 10-20 minutes):

```bat
.venv\Scripts\python.exe scripts\08_train_digits.py --quick
.venv\Scripts\python.exe scripts\08_train_digits.py
```

- Pre-trains on the 280,000 public digits + generated scribbles, then fine-tunes on your digits.
- Classes: 0-9 and `x` (scribble / dash / crossed-out = read as EMPTY).
- `reports\m4_report.txt`: accuracy on the held-out students per digit, per cell, per roll number,
  before and after fine-tuning, plus the M4 gate (digit >= 99%, cell >= 98%, roll >= 97%).
- `data\preview\m4_mistakes.jpg`: every wrong answer, labeled `true>predicted`.
- `models\digit_cnn.onnx`: the model, checked to give the same answers in OpenCV (no PyTorch needed to use it).

## 11 — Read covers end to end (Milestone 5)

```bat
.venv\Scripts\python.exe scripts\09_read_sheets.py
.venv\Scripts\python.exe scripts\09_read_sheets.py D:\scans\new_batch
```

Photo -> section letter, roll number and all 32 marks, each with a confidence. About 1-2 s per photo.
Doubtful cells are **flagged, never guessed**: confidence below `--threshold` (default 0.8), a mark above 35,
a question totalling above 35, writing in the other section, crossed-out ink that still looks like a digit.
Crossed-out / scribbled marks are read as empty. Sheets with empty or unclear roll boxes, or a repeated
roll + section in the batch, are marked `review`.

- `reports\m5_sheets.csv`: one row per photo (status, section, roll, marks, reasons).
- `reports\m5_cells.csv`: one row per cell (value, confidence, flag).
- `data\preview\m5\<scan_id>.jpg`: crops with the value read; orange = flagged.
- `reports\m5_evaluation.txt`: for labeled sheets, wrong values that were NOT flagged vs. flag rate, per threshold.

## 12 — The marks workbook (Milestone 6)

```bat
.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements.txt
.venv\Scripts\python.exe scripts\09_read_sheets.py
.venv\Scripts\python.exe scripts\10_fill_excel.py
```

Creates a **new** `output\SmartMark_marks_<date>_<time>.xlsx` every run (an edited workbook is never overwritten):

- **Marks**: one row per student, Section A and B covers merged by roll number. Q1a-Q8d, question totals,
  each section = **best 3 of its 4 questions**, Final = A + B. Totals are formulas, so fixing a cell
  updates everything. **Yellow** = check this cell (hover for the comment: what was read and why);
  **Status** says `OK` or `CHECK n`. Missing covers and unreadable roll numbers get their own note / row.
- **Review**: every yellow cell with the photo of what was written, so checking is quick.
- **Covers**: every photo with its status.

## 13 — Teacher app (Milestone 7)

```bat
.venv\Scripts\python.exe scripts\smartmark_app.py
```

1. **Photos folder** -> Browse... to the folder of cover photos, then **Scan** (progress bar, ~1 s per photo).
2. Every doubtful item is shown one at a time: the photo of what was written, what was read, and why it
   was flagged (a mark, an unsure roll number, an unclear section letter, or a page to glance at).
   Type the right value and press **Enter** (an empty box = blank cell); Up/Down = previous/next.
   Answers are saved as you go, so the app can be closed and reopened (**Review last scan**).
3. **Export Excel** applies the answers and writes a new workbook in `output\` (**Open workbook** opens it).
   Items not checked stay yellow in the workbook.

Every correction is logged in `reports\teacher_corrections.csv` (with the crop) to retrain the model later.
Needs Tkinter, which the standard python.org installer includes ("tcl/tk and IDLE" option).

## Exit criteria for Milestone 1

- Inventory covers every image, and all suspected corrupt/duplicate/sideways files are investigated.
- At least 10–12 varied sheets are carefully transcribed with section and roll.
- There is a documented template image and a rough map of roll and marks regions.
- A clear list of the most common image issues is available.
- No handwritten recognition model has been claimed or trained yet.

## Next milestone

M7 part 2 — package the app as a Windows program (SmartMark.exe) that runs without Python, plus a one-page teacher guide.
