"""Alignment, cell geometry and red-ink helpers for KUET SmartMark (Milestone 2).

Pipeline for one photo:
  1. SIFT features of the photo are matched to the printed parts of the template
     (handwriting areas are masked out), and a homography is fitted (MAGSAC).
  2. The photo is warped onto the 300-dpi template canvas (2480 x 3508).
  3. Each grid / roll-box line is searched for near its template position and
     snapped to where it really is, so cell boxes follow the printed lines.
  4. Cells are cropped; red ink is isolated by colour; blank = almost no red ink.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from common import ROOT

TEMPLATE_DIR = ROOT / "templates" / "kuet_cover_v1"
MATCH_SCALE = 0.4          # template is matched at 40% size (992 x 1403)
PHOTO_MATCH_SIDE = 1600    # photo long side used for matching
LINE_SEARCH = 40           # px (300 dpi) searched either side of each template line
CELL_INSET = 6             # px trimmed inside the snapped lines
MIN_INLIERS = 60
MAX_LINE_OFFSET_OK = 25     # px: printed lines further than this from the template = review
MIN_PIECE = 60              # red px: smaller ink pieces are specks
FILLED_MIN_INK = 120        # ink score (see ink_stats) at or above this = something is written


def line_masks(gray: np.ndarray, length: int = 120) -> tuple[np.ndarray, np.ndarray]:
    """Binary masks of long horizontal and vertical dark strokes (printed lines)."""
    b = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 41, 15)
    h = cv2.morphologyEx(b, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (length, 1)))
    v = cv2.morphologyEx(b, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, length)))
    return h, v


def red_mask(bgr: np.ndarray, dark: int = 6, red: int = 5) -> np.ndarray:
    """255 where the pixel is red/pink pen ink.

    Thin pen strokes in a phone photo are only faintly red (saturation ~20-60), so the
    test is relative to the paper around them: the pixel must be darker than the local
    paper AND redder than it, with a red hue. Black print, grid lines, shadows and
    black/blue handwriting fail the 'redder' test and are ignored.
    """
    img = bgr.astype(np.int16)
    b, g, r = img[..., 0], img[..., 1], img[..., 2]
    redness = r - (g + b) // 2
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    k = 31 if min(gray.shape[:2]) > 31 else (min(gray.shape[:2]) // 2) * 2 - 1
    paper = cv2.medianBlur(gray, max(k, 3)).astype(np.int16)
    paper_red = cv2.medianBlur(np.clip(redness + 128, 0, 255).astype(np.uint8), max(k, 3)).astype(np.int16) - 128
    hue = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)[..., 0]
    m = ((gray.astype(np.int16) < paper - dark) & (redness > paper_red + red) & ((hue <= 20) | (hue >= 160)))
    m = m.astype(np.uint8) * 255
    return cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))


def full_strokes(bgr: np.ndarray, strict: np.ndarray) -> np.ndarray:
    """Whole pen strokes for reading digits: faint red pixels kept only where they connect to
    confident ink (hysteresis). red_mask alone keeps just the darker parts of faint strokes."""
    loose = cv2.bitwise_or(red_mask(bgr, dark=2, red=2), strict)
    _, lab = cv2.connectedComponents(loose)
    keep = np.unique(lab[strict > 0])
    return np.isin(lab, keep[keep > 0]).astype(np.uint8) * 255


def to_mnist(m: np.ndarray, blur: bool = True) -> np.ndarray:
    """Crop to the ink, fit in 20x20 keeping aspect, centre by mass in 28x28, light blur like MNIST
    (skip the blur for images that are already anti-aliased, e.g. public datasets)."""
    ys, xs = np.nonzero(m)
    c = m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    s = 20 / max(c.shape)
    c = cv2.resize(c, (max(1, round(c.shape[1] * s)), max(1, round(c.shape[0] * s))), interpolation=cv2.INTER_AREA)
    out = np.zeros((28, 28), np.uint8)
    y0, x0 = (28 - c.shape[0]) // 2, (28 - c.shape[1]) // 2
    out[y0:y0 + c.shape[0], x0:x0 + c.shape[1]] = c
    M = cv2.moments(out)
    if M["m00"]:
        dx, dy = 14 - M["m10"] / M["m00"], 14 - M["m01"] / M["m00"]
        out = cv2.warpAffine(out, np.float32([[1, 0, dx], [0, 1, dy]]), (28, 28))
    return cv2.GaussianBlur(out, (3, 3), 0) if blur else out


@dataclass
class Template:
    gray: np.ndarray
    regions: dict
    keypoints: list
    descriptors: np.ndarray
    letters: dict = field(default_factory=dict)


_SIFT = None


def _sift():
    global _SIFT
    if _SIFT is None:
        _SIFT = cv2.SIFT_create(nfeatures=6000)
    return _SIFT


def load_template(folder: Path = TEMPLATE_DIR) -> Template:
    gray = cv2.imread(str(folder / "template.png"), cv2.IMREAD_GRAYSCALE)
    mask = cv2.imread(str(folder / "kp_mask.png"), cv2.IMREAD_GRAYSCALE)
    if gray is None or mask is None:
        raise SystemExit(f"Template files missing in {folder} - run scripts\\build_template.py first")
    regions = json.loads((folder / "regions.json").read_text(encoding="utf-8"))
    small = cv2.resize(gray, None, fx=MATCH_SCALE, fy=MATCH_SCALE, interpolation=cv2.INTER_AREA)
    small_mask = cv2.resize(mask, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_NEAREST)
    kp, desc = _sift().detectAndCompute(small, small_mask)
    letters = {}
    for name in ("A", "B"):
        p = folder / f"letter_{name}.png"
        if p.exists():
            letters[name] = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
    return Template(gray, regions, kp, desc, letters)


def find_homography(photo_bgr: np.ndarray, t: Template) -> tuple[np.ndarray | None, int, int]:
    """Homography photo(full res) -> template canvas, inlier count, good-match count."""
    ps = PHOTO_MATCH_SIDE / max(photo_bgr.shape[:2])
    small = cv2.resize(cv2.cvtColor(photo_bgr, cv2.COLOR_BGR2GRAY), None, fx=ps, fy=ps, interpolation=cv2.INTER_AREA)
    kp, desc = _sift().detectAndCompute(small, None)
    if desc is None or len(kp) < 10:
        return None, 0, 0
    flann = cv2.FlannBasedMatcher({"algorithm": 1, "trees": 5}, {"checks": 64})
    pairs = flann.knnMatch(desc, t.descriptors, k=2)
    good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < 0.75 * p[1].distance]
    if len(good) < 10:
        return None, 0, len(good)
    src = np.float32([kp[m.queryIdx].pt for m in good])
    dst = np.float32([t.keypoints[m.trainIdx].pt for m in good])
    hs, inliers = cv2.findHomography(src, dst, cv2.USAC_MAGSAC, 4.0)
    if hs is None:
        return None, 0, len(good)
    full = np.diag([1 / MATCH_SCALE, 1 / MATCH_SCALE, 1]) @ hs @ np.diag([ps, ps, 1])
    return full, int(inliers.sum()), len(good)


def _snap(mask: np.ndarray, positions: list[int], axis: str, span: tuple[int, int], min_frac: float):
    """Find each line near its template position. axis 'y' = horizontal lines."""
    found, offsets = [], []
    for p in positions:
        lo, hi = p - LINE_SEARCH, p + LINE_SEARCH + 1
        if axis == "y":
            prof = (mask[lo:hi, span[0]:span[1]] > 0).sum(1)
        else:
            prof = (mask[span[0]:span[1], lo:hi] > 0).sum(0)
        need = min_frac * (span[1] - span[0])
        if prof.size and prof.max() >= need:
            off = int(np.argmax(prof)) - LINE_SEARCH
            found.append(p + off)
            offsets.append(off)
        else:
            found.append(None)
    med = int(np.median(offsets)) if offsets else 0
    snapped = [f if f is not None else p + med for f, p in zip(found, positions)]
    return snapped, sum(f is not None for f in found), offsets


def refine_lines(warp_gray: np.ndarray, t: Template) -> tuple[dict, dict]:
    L = t.regions["lines"]
    h, v = line_masks(warp_gray)
    gx0, gx1 = L["grid_x"][0], L["grid_x"][-1]
    gy0, gy1 = L["grid_y"][0], L["grid_y"][-1]
    rx0, rx1 = L["roll_x"][0], L["roll_x"][-1]
    ry0, ry1 = L["roll_y"][0], L["roll_y"][-1]
    out, stats_found, all_offsets = {}, 0, []
    for key, mask, axis, span, frac in (
        ("grid_y", h, "y", (gx0 + 20, gx1 - 20), 0.2),
        ("grid_x", v, "x", (gy0 + 20, gy1 - 20), 0.2),
        ("roll_y", h, "y", (rx0 + 20, rx1 - 20), 0.3),
        ("roll_x", v, "x", (ry0 + 10, ry1 - 10), 0.4),
    ):
        snapped, n, offs = _snap(mask, L[key], axis, span, frac)
        out[key] = snapped
        stats_found += n
        all_offsets += offs
    total = sum(len(L[k]) for k in ("grid_y", "grid_x", "roll_y", "roll_x"))
    stats = {"lines_found": stats_found, "lines_total": total,
             "max_line_offset_px": int(max((abs(o) for o in all_offsets), default=99))}
    return out, stats


def boxes_from_lines(lines: dict) -> dict:
    gx, gy, rx, ry = lines["grid_x"], lines["grid_y"], lines["roll_x"], lines["roll_y"]
    i = CELL_INSET
    cells = {f"q{q + 1}_{c}": [gx[ci + 1] + i, gy[q + 1] + i, gx[ci + 2] - i, gy[q + 2] - i]
             for q in range(8) for ci, c in enumerate("abcd")}
    roll = [[rx[k] + i, ry[0] + i, rx[k + 1] - i, ry[1] - i] for k in range(7)]
    return {"cells": cells, "roll_boxes": roll}


def classify_letter(warp_gray: np.ndarray, t: Template) -> tuple[str | None, dict]:
    """Which printed section letter (A/B) is on the sheet, by template matching."""
    x0, y0, x1, y1 = t.regions["letter_box"]
    pad = 40
    region = warp_gray[max(0, y0 - pad):y1 + pad, max(0, x0 - pad):x1 + pad]
    scores = {}
    for name, ref in t.letters.items():
        if region.shape[0] >= ref.shape[0] and region.shape[1] >= ref.shape[1]:
            scores[name] = float(cv2.matchTemplate(region, ref, cv2.TM_CCOEFF_NORMED).max())
    if not scores:
        return None, scores
    best = max(scores, key=scores.get)
    others = [s for k, s in scores.items() if k != best]
    margin = scores[best] - (max(others) if others else 0)
    return (best if scores[best] > 0.5 and margin > 0.1 else None), scores


def ink_stats(mask: np.ndarray) -> tuple[int, int]:
    """(red pixels, ink score) for a cell mask.

    Ink score = red pixels in the biggest piece of writing, after joining broken
    faint strokes (7x7 closing). Pieces that only poke in from a cell edge (the tail
    of a digit written in the neighbouring cell) are ignored: they touch the border
    and reach less than 30% into the cell.
    Tuned on 1,860 labeled crops: written >= 186, blank <= 69 -> threshold 120.
    """
    h, w = mask.shape[:2]
    joined = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(joined)
    best = 0
    for i in range(1, n):
        x, y, bw, bh, _ = st[i]
        from_top_bottom = (y <= 1 or y + bh >= h - 1) and bh < 0.3 * h
        from_side = (x <= 1 or x + bw >= w - 1) and bw < 0.3 * w
        if from_top_bottom or from_side:
            continue
        best = max(best, int((mask[lab == i] > 0).sum()))
    return int((mask > 0).sum()), best


def align_sheet(img: np.ndarray, t: Template) -> dict:
    """Photo -> page flattened onto the template, every crop box, section letter and quality reasons.
    Returns {"ok": False, "reason": ...} when the cover is not recognised."""
    Hm, inliers, good = find_homography(img, t)
    if Hm is None or inliers < MIN_INLIERS:
        return {"ok": False, "inliers": inliers, "reason": "cover not recognised - retake photo"}
    W, H = t.regions["canvas"]
    warp = cv2.warpPerspective(img, Hm, (W, H), flags=cv2.INTER_LINEAR, borderValue=(255, 255, 255))
    wgray = cv2.cvtColor(warp, cv2.COLOR_BGR2GRAY)
    lines, lstats = refine_lines(wgray, t)
    boxes = boxes_from_lines(lines)
    letter, letter_scores = classify_letter(wgray, t)
    reasons = []
    if lstats["lines_found"] < 0.75 * lstats["lines_total"]:
        reasons.append(f"only {lstats['lines_found']}/{lstats['lines_total']} printed lines found")
    if lstats["max_line_offset_px"] > MAX_LINE_OFFSET_OK:
        reasons.append(f"lines up to {lstats['max_line_offset_px']} px off template")
    if letter is None:
        reasons.append("section letter unclear")
    named = {f"roll_{k + 1}": b for k, b in enumerate(boxes["roll_boxes"])}
    named.update(boxes["cells"])
    named["letter"] = t.regions["letter_box"]
    named["header_roll"] = t.regions["header_roll_box"]
    return {"ok": True, "warp": warp, "inliers": inliers, "matches": good, "lines": lines, "lstats": lstats,
            "letter": letter, "letter_scores": letter_scores, "reasons": reasons, "named": named}


# ---- splitting a cell into single digits (used by 06 for training data and 09 for reading) ----
def pieces(mask: np.ndarray) -> list[np.ndarray]:
    """Ink pieces left-to-right, each as a full-size mask. Edge intrusions dropped.
    Only a 3x3 join: full_strokes already repairs faint strokes, and a bigger join fuses close digits ("10")."""
    h, w = mask.shape
    joined = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(joined)
    out = []
    for i in range(1, n):
        x, y, bw, bh, _ = st[i]
        if ((y <= 1 or y + bh >= h - 1) and bh < 0.3 * h) or ((x <= 1 or x + bw >= w - 1) and bw < 0.3 * w):
            continue  # tail of a digit from the neighbouring cell
        m = np.where(lab == i, mask, 0).astype(np.uint8)
        if (m > 0).sum() >= MIN_PIECE:
            out.append(m)
    out.sort(key=lambda m: np.nonzero(m)[1].mean())
    # merge pieces that sit mostly on top of each other (e.g. the two strokes of a 4, a detached 5 bar)
    merged: list[np.ndarray] = []
    for m in out:
        if merged:
            ax = np.nonzero(merged[-1])[1]
            bx = np.nonzero(m)[1]
            overlap = min(ax.max(), bx.max()) - max(ax.min(), bx.min())
            if overlap > 0.5 * min(np.ptp(ax) + 1, np.ptp(bx) + 1):
                merged[-1] = cv2.bitwise_or(merged[-1], m)
                continue
        merged.append(m)
    return merged


def split_touching(m: np.ndarray) -> list[np.ndarray]:
    """Cut one piece holding two touching digits at the thinnest column near its middle."""
    xs = np.nonzero(m)[1]
    x0, x1 = xs.min(), xs.max()
    lo, hi = x0 + (x1 - x0) // 4, x1 - (x1 - x0) // 4
    cut = lo + int(np.argmin((m[:, lo:hi + 1] > 0).sum(0)))
    a, b = m.copy(), m.copy()
    a[:, cut:] = 0
    b[:, :cut] = 0
    return [a, b]


def ink_height(m: np.ndarray) -> int:
    ys = np.nonzero(m)[0]
    return int(ys.max() - ys.min() + 1)


def has_loop(m: np.ndarray) -> bool:
    """True if the ink encloses a hole of real size (a '0'), found as an inner contour."""
    closed = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    cs, hier = cv2.findContours(closed, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hier is None:
        return False
    ys, xs = np.nonzero(m)
    box = (np.ptp(ys) + 1) * (np.ptp(xs) + 1)
    return any(h[3] >= 0 and cv2.contourArea(c) > 0.08 * box for c, h in zip(cs, hier[0]))


def join_fragments(ps: list[np.ndarray], keep: int = 1) -> list[np.ndarray]:
    """Join pieces shorter than half the tallest piece onto their nearest neighbour (broken faint strokes),
    never going below `keep` pieces."""
    ps = list(ps)
    while len(ps) > max(keep, 1):
        tallest = max(ink_height(m) for m in ps)
        i = min(range(len(ps)), key=lambda k: ink_height(ps[k]))
        if ink_height(ps[i]) >= 0.5 * tallest:
            break
        cx = [np.nonzero(m)[1].mean() for m in ps]
        j = min((k for k in range(len(ps)) if k != i), key=lambda k: abs(cx[k] - cx[i]))
        ps[j] = cv2.bitwise_or(ps[j], ps[i])
        del ps[i]
    return ps
