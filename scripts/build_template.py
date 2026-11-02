"""Build the alignment template + region map (Milestone 1) from one reference photo.

No blank PDF of the cover was available, so the template is made from a clean photo
on a dark background (a012 by default): the page outline is found, the page is
flattened onto an A4 canvas at 300 dpi, red ink is painted out, and the roll-box
and marks-grid lines are measured. A Section B photo supplies the 'B' letter image.

Usage:  .venv\\Scripts\\python.exe scripts\\build_template.py [--ref a012] [--ref-b b012]
Output: templates\\kuet_cover_v1\\ template.png, kp_mask.png, regions.json,
        letter_A.png, letter_B.png, regions_preview.jpg
"""
from __future__ import annotations

import argparse
import json

import cv2
import numpy as np

from common import RAW_DIR
from sm_align import TEMPLATE_DIR, find_homography, line_masks, load_template, red_mask

W, H = 2480, 3508  # A4 at 300 dpi
HEADER_FIELDS = (350, 560, W, 1140)      # x0, y0, x1, y1 of handwritten header lines
HEADER_ROLL = [1780, 740, 2330, 900]      # "Roll No. ______" written in the header


def page_quad(img):
    s = 1000 / max(img.shape[:2])
    sm = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(sm, cv2.COLOR_BGR2HSV)
    m = ((hsv[..., 2] > 150) & (hsv[..., 1] < 60)).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    hull = cv2.convexHull(max(cs, key=cv2.contourArea))
    q = cv2.approxPolyDP(hull, 0.02 * cv2.arcLength(hull, True), True).reshape(-1, 2) / s
    if len(q) != 4:
        raise SystemExit("Reference page outline is not a clean quadrilateral - pick a photo on a dark, plain background")
    q = q.astype(np.float32)
    ssum, diff = q.sum(1), np.diff(q, axis=1).ravel()
    return np.array([q[ssum.argmin()], q[diff.argmin()], q[ssum.argmax()], q[diff.argmax()]], np.float32)


def peaks(profile, min_val, min_gap):
    idx = []
    for i in np.argsort(-profile.astype(float)):
        if profile[i] < min_val:
            break
        if all(abs(i - j) >= min_gap for j in idx):
            idx.append(int(i))
    return sorted(idx)


def band_lines(mask, horizontal, lo, hi, a, b, min_val, gap=40):
    sub = mask[lo:hi, a:b] if horizontal else mask[a:b, lo:hi]
    return [p + lo for p in peaks((sub > 0).sum(1 if horizontal else 0), min_val, gap)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="a012", help="Section A photo (file stem) to build the template from")
    ap.add_argument("--ref-b", default="b012", help="Section B photo used for the 'B' letter image")
    args = ap.parse_args()
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)

    img = cv2.imread(str(next(RAW_DIR.rglob(args.ref + ".*"))))
    M = cv2.getPerspectiveTransform(page_quad(img), np.float32([[0, 0], [W, 0], [W, H], [0, H]]))
    warp = cv2.warpPerspective(img, M, (W, H), flags=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(warp, cv2.COLOR_BGR2GRAY)
    red = cv2.dilate(red_mask(warp), np.ones((9, 9), np.uint8))
    bg = cv2.medianBlur(gray, 51)
    gray[red > 0] = bg[red > 0]

    h, v = line_masks(gray)
    roll_y = band_lines(h, True, 1250, 1520, 1500, 2350, 500)
    roll_x = band_lines(v, False, 1480, 2350, roll_y[0] + 5, roll_y[-1] - 5, 0.6 * (roll_y[-1] - roll_y[0]))
    grid_y = band_lines(h, True, 1520, 3100, 1500, 2350, 250)
    grid_x = band_lines(v, False, 1480, 2350, grid_y[0] + 5, grid_y[-1] - 5, 0.3 * (grid_y[-1] - grid_y[0]))
    if (len(roll_y), len(roll_x), len(grid_y), len(grid_x)) != (2, 8, 11, 6):
        raise SystemExit(f"Unexpected line count roll {len(roll_y)}x{len(roll_x)}, grid {len(grid_y)}x{len(grid_x)}")

    sub = (gray[150:500, 1950:2450] < 110).astype(np.uint8)
    _, _, st, _ = cv2.connectedComponentsWithStats(sub)
    i = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
    x, y, w, hh = (int(s) for s in st[i, :4])
    letter = [1950 + x - 30, 150 + y - 30, 1950 + x + w + 30, 150 + y + hh + 30]

    mask = np.full((H, W), 255, np.uint8)
    x0, y0, x1, y1 = HEADER_FIELDS
    mask[y0:y1, x0:x1] = 0
    mask[roll_y[0] - 10:roll_y[1] + 10, roll_x[0] - 10:roll_x[-1] + 10] = 0
    mask[grid_y[1]:grid_y[-1], grid_x[1]:grid_x[-1]] = 0
    mask[letter[1]:letter[3], letter[0]:letter[2]] = 0

    regions = {
        "form": "KUET answer-script cover (Form No. 1)",
        "canvas": [W, H], "dpi": 300, "reference_photo": args.ref,
        "lines": {"roll_y": roll_y, "roll_x": roll_x, "grid_y": grid_y, "grid_x": grid_x},
        "letter_box": letter,
        "header_roll_box": HEADER_ROLL,
        "grid_rows": ["header", "q1", "q2", "q3", "q4", "q5", "q6", "q7", "q8", "total"],
        "grid_cols": ["ques_no", "a", "b", "c", "d"],
    }
    cv2.imwrite(str(TEMPLATE_DIR / "template.png"), gray)
    cv2.imwrite(str(TEMPLATE_DIR / "kp_mask.png"), mask)
    (TEMPLATE_DIR / "regions.json").write_text(json.dumps(regions, indent=1), encoding="utf-8")
    cv2.imwrite(str(TEMPLATE_DIR / "letter_A.png"), gray[letter[1]:letter[3], letter[0]:letter[2]])

    # 'B' letter: align the Section B reference with the new template and cut the same box
    t = load_template()
    imgb = cv2.imread(str(next(RAW_DIR.rglob(args.ref_b + ".*"))))
    Hb, inl, _ = find_homography(imgb, t)
    wb = cv2.cvtColor(cv2.warpPerspective(imgb, Hb, (W, H)), cv2.COLOR_BGR2GRAY)
    cv2.imwrite(str(TEMPLATE_DIR / "letter_B.png"), wb[letter[1]:letter[3], letter[0]:letter[2]])

    vis = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    for k in range(7):
        cv2.rectangle(vis, (roll_x[k], roll_y[0]), (roll_x[k + 1], roll_y[1]), (255, 0, 0), 4)
    for q in range(8):
        for c in range(4):
            cv2.rectangle(vis, (grid_x[c + 1], grid_y[q + 1]), (grid_x[c + 2], grid_y[q + 2]), (0, 160, 0), 4)
            cv2.putText(vis, f"q{q + 1}_{'abcd'[c]}", (grid_x[c + 1] + 8, grid_y[q + 1] + 40), 0, 1.1, (0, 120, 0), 3)
    cv2.rectangle(vis, tuple(letter[:2]), tuple(letter[2:]), (0, 0, 255), 5)
    cv2.rectangle(vis, tuple(HEADER_ROLL[:2]), tuple(HEADER_ROLL[2:]), (255, 0, 255), 5)
    vis[mask == 0] = (vis[mask == 0] * 0.7 + np.array([0, 0, 80])).astype(np.uint8)
    cv2.imwrite(str(TEMPLATE_DIR / "regions_preview.jpg"), cv2.resize(vis, (1000, 1414)), [cv2.IMWRITE_JPEG_QUALITY, 80])
    print(f"Template written to {TEMPLATE_DIR}")
    print(f"Roll boxes: 7 | grid: {len(grid_y) - 1} rows x {len(grid_x) - 1} cols | B-letter alignment inliers: {inl}")


if __name__ == "__main__":
    main()
