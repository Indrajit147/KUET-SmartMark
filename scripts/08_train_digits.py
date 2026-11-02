"""Milestone 4: train the digit reader (0-9 + 'x' = scribble/dash, read as empty) on your laptop CPU.

Usage:
    .venv\\Scripts\\python.exe scripts\\08_train_digits.py            full run (about 10-20 min on a laptop)
    .venv\\Scripts\\python.exe scripts\\08_train_digits.py --quick    2-minute check that everything works

Needs: data\\digits\\public.npz (07) and data\\digits\\index.csv + images (06). PyTorch in the .venv.
Steps:
  A. pre-train on the public digits + generated scribbles      -> 'public only' score on your test sheets
  B. fine-tune on your own training digits (+ some public ones) with augmentation
  C. score on the held-out students: per digit, per cell ("12" fully right), per roll number
  D. export models\\digit_cnn.onnx and check OpenCV runs it with the same answers
Writes: models\\digit_cnn.onnx, models\\digit_cnn.pt, models\\digit_model.json,
        reports\\m4_report.txt, data\\preview\\m4_mistakes.jpg
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import time
from collections import defaultdict

import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from common import PREVIEW_DIR, REPORT_DIR, ROOT
from sm_align import to_mnist

DIGITS = ROOT / "data" / "digits"
MODELS = ROOT / "models"
CLASSES = [str(d) for d in range(10)] + ["x"]
X = 10  # index of the scribble class
GATE = {"digit": 0.99, "cell": 0.98, "roll": 0.97}


# ---------------------------------------------------------------- data
def norm(img: np.ndarray) -> np.ndarray:
    """uint8 28x28 -> float32 0..1 with the brightest stroke at 1 (our photos are greyer than EMNIST)."""
    x = img.astype(np.float32)
    return x / max(float(x.max()), 1.0)


def load_ours():
    with (DIGITS / "index.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    imgs = np.stack([norm(cv2.imread(str(ROOT / r["path"]), cv2.IMREAD_GRAYSCALE)) for r in rows])
    labels = np.array([CLASSES.index(r["label"]) for r in rows])
    return rows, imgs, labels


def load_public():
    d = np.load(DIGITS / "public.npz", allow_pickle=False)
    x = d["images"].astype(np.float32) / 255.0
    x /= np.maximum(x.reshape(len(x), -1).max(1), 1e-3)[:, None, None]
    return x, d["labels"].astype(np.int64), d["source"]


def make_scribbles(n: int, public_x: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Synthetic class 'x': heavy zig-zag scribbles (often over a digit) and short dashes.
    Heavy on purpose: a crossed 7 or a 4 with a bar must NOT look like a scribble."""
    out = np.empty((n, 28, 28), np.float32)
    for i in range(n):
        c = np.zeros((112, 112), np.uint8)
        kind = rng.random()
        if kind < 0.25:  # dash
            y = rng.integers(40, 72)
            x0, x1 = rng.integers(10, 40), rng.integers(70, 102)
            cv2.line(c, (int(x0), int(y)), (int(x1), int(y + rng.integers(-8, 9))), 255, int(rng.integers(5, 10)))
        else:
            if kind < 0.75:  # crossed-out digit underneath
                d = cv2.resize((public_x[rng.integers(len(public_x))] * 255).astype(np.uint8), (112, 112))
                c = np.maximum(c, d)
            # dense back-and-forth strike-through, like real cross-outs (b011 Q8, b026 Q1)
            k = int(rng.integers(11, 20))
            cx, cy = rng.integers(48, 64, size=2)
            w, h = int(rng.integers(28, 45)), int(rng.integers(28, 40))  # must hide most of the digit
            xs = cx + np.where(np.arange(k) % 2, w, -w) + rng.integers(-8, 9, k)
            ys = cy - h + np.arange(k) * 2 * h / (k - 1) + rng.integers(-6, 7, k)
            pts = np.stack([xs, ys], 1).astype(np.int32)
            cv2.polylines(c, [pts], False, 255, int(rng.integers(5, 10)))
        out[i] = norm(to_mnist(c))
    return out


# ---------------------------------------------------------------- model + augmentation
class DigitCNN(nn.Module):
    def __init__(self, n_classes: int = len(CLASSES)):
        super().__init__()
        def block(i, o):
            return nn.Sequential(nn.Conv2d(i, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(),
                                 nn.Conv2d(o, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(), nn.MaxPool2d(2))
        self.features = nn.Sequential(block(1, 32), block(32, 64))
        self.head = nn.Sequential(nn.Flatten(), nn.Linear(64 * 7 * 7, 128), nn.ReLU(), nn.Dropout(0.3),
                                  nn.Linear(128, n_classes))

    def forward(self, x):
        return self.head(self.features(x))


def augment(x: torch.Tensor) -> torch.Tensor:
    """Random small rotation/scale/shift, stroke thickness and blur - on the whole batch at once."""
    n = x.shape[0]
    ang = (torch.rand(n) - 0.5) * 2 * np.deg2rad(12)
    sc = 1 + (torch.rand(n) - 0.5) * 0.25
    sh = (torch.rand(n) - 0.5) * 0.25  # slant
    tx, ty = (torch.rand(2, n) - 0.5) * 0.2
    cos, sin = torch.cos(ang) / sc, torch.sin(ang) / sc
    theta = torch.stack([torch.stack([cos, -sin + sh, tx], 1), torch.stack([sin, cos, ty], 1)], 1)
    x = F.grid_sample(x, F.affine_grid(theta, x.shape, align_corners=False), align_corners=False)
    r = torch.rand(n)
    thick, thin = r < 0.25, (r >= 0.25) & (r < 0.45)
    if thick.any():
        x[thick] = F.max_pool2d(x[thick], 3, 1, 1)
    if thin.any():
        x[thin] = -F.max_pool2d(F.pad(-x[thin], (0, 1, 0, 1)), 2, 1)  # erode = min filter
    blur = torch.rand(n) < 0.3
    if blur.any():
        k = torch.tensor([[1., 2., 1.], [2., 4., 2.], [1., 2., 1.]]).div(16).view(1, 1, 3, 3)
        x[blur] = F.conv2d(x[blur], k, padding=1)
    return x.clamp(0, 1)


def run_epoch(model, opt, x: np.ndarray, y: np.ndarray, rng, batch=256, aug=True):
    model.train()
    order = rng.permutation(len(x))
    total, correct, loss_sum = 0, 0, 0.0
    for i in range(0, len(order), batch):
        idx = order[i:i + batch]
        xb = torch.from_numpy(x[idx]).unsqueeze(1)
        yb = torch.from_numpy(y[idx])
        if aug:
            xb = augment(xb)
        out = model(xb)
        loss = F.cross_entropy(out, yb, label_smoothing=0.05)
        opt.zero_grad()
        loss.backward()
        opt.step()
        loss_sum += loss.item() * len(idx)
        correct += int((out.argmax(1) == yb).sum())
        total += len(idx)
    return loss_sum / total, correct / total


@torch.no_grad()
def predict(model, x: np.ndarray) -> np.ndarray:
    model.eval()
    return np.concatenate([F.softmax(model(torch.from_numpy(x[i:i + 1024]).unsqueeze(1)), 1).numpy()
                           for i in range(0, len(x), 1024)])


# ---------------------------------------------------------------- scoring
def score(rows, probs, labels) -> dict:
    pred = probs.argmax(1)
    conf = probs.max(1)
    res = {"n": len(labels), "digit": float((pred == labels).mean())}
    cells, rolls = defaultdict(list), defaultdict(list)
    for r, p, t in zip(rows, pred, labels):
        key = (r["scan_id"], r["crop"])
        (rolls[r["scan_id"]] if r["source"] == "roll" else cells[key]).append((int(r["position"]), p, t))
    def all_right(items):
        return all(p == t for _, p, t in sorted(items))
    res["cell"] = float(np.mean([all_right(v) for v in cells.values()])) if cells else 0.0
    res["roll"] = float(np.mean([all_right(v) for v in rolls.values()])) if rolls else 0.0
    res["n_cells"], res["n_rolls"] = len(cells), len(rolls)
    res["per_class"] = {CLASSES[c]: (int(((pred == c) & (labels == c)).sum()), int((labels == c).sum()))
                        for c in range(len(CLASSES)) if (labels == c).any()}
    res["confusion"] = np.bincount(labels * len(CLASSES) + pred, minlength=len(CLASSES) ** 2)\
        .reshape(len(CLASSES), len(CLASSES)).tolist()
    for th in (0.9, 0.99):
        keep = conf >= th
        res[f"conf>={th}"] = (float(keep.mean()), float((pred[keep] == labels[keep]).mean()) if keep.any() else 0.0)
    return res


def fmt(name: str, s: dict) -> list[str]:
    lines = [f"{name}: digits {s['digit']:.2%} (n={s['n']}) | cells {s['cell']:.2%} (n={s['n_cells']}) | "
             f"rolls {s['roll']:.2%} (n={s['n_rolls']})"]
    for th in (0.9, 0.99):
        cov, acc = s[f"conf>={th}"]
        lines.append(f"  confidence >= {th}: covers {cov:.1%} of digits, {acc:.2%} of those right")
    return lines


def mistakes_image(rows, imgs, probs, labels, path):
    pred = probs.argmax(1)
    bad = [i for i in np.argsort(probs.max(1)) if pred[i] != labels[i]][:60]
    tiles = []
    for i in bad:
        t = cv2.cvtColor(cv2.resize((imgs[i] * 255).astype(np.uint8), (84, 84), interpolation=cv2.INTER_NEAREST),
                         cv2.COLOR_GRAY2BGR)
        cv2.putText(t, f"{CLASSES[labels[i]]}>{CLASSES[pred[i]]}", (2, 14), 0, 0.45, (0, 255, 255), 1)
        cv2.putText(t, rows[i]["scan_id"], (2, 80), 0, 0.35, (180, 180, 180), 1)
        tiles.append(t)
    if not tiles:
        tiles = [np.zeros((84, 84, 3), np.uint8)]
    while len(tiles) % 10:
        tiles.append(np.zeros_like(tiles[0]))
    cv2.imwrite(str(path), np.vstack([np.hstack(tiles[i:i + 10]) for i in range(0, len(tiles), 10)]))
    return len(bad)


# ---------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="small subset, 1 epoch each - checks the setup only")
    args = ap.parse_args()
    torch.manual_seed(0)
    rng = np.random.default_rng(0)
    torch.set_num_threads(max(1, os.cpu_count() or 1))
    t0 = time.time()

    rows, ours_x, ours_y = load_ours()
    split = np.array([r["split"] for r in rows])
    tr, te = split == "train", split == "test"
    pub_x, pub_y, pub_src = load_public()
    pub_train = np.array(["test" not in s for s in pub_src])
    if args.quick:
        keep = rng.choice(len(pub_x), min(len(pub_x), 6000), replace=False)
        pub_x, pub_y, pub_train = pub_x[keep], pub_y[keep], pub_train[keep]
    n_scr = 600 if args.quick else 8000
    scr = make_scribbles(n_scr, pub_x[pub_train], rng)
    print(f"Data: public {len(pub_x)} | ours train {tr.sum()} test {te.sum()} | generated scribbles {n_scr} "
          f"| threads {torch.get_num_threads()}", flush=True)

    model = DigitCNN()
    report = []

    # A. pre-train on public digits + generated scribbles
    xa = np.concatenate([pub_x[pub_train], scr[: n_scr * 3 // 4]])
    ya = np.concatenate([pub_y[pub_train], np.full(n_scr * 3 // 4, X)])
    opt = torch.optim.Adam(model.parameters(), 1e-3)
    for ep in range(1 if args.quick else 2):
        loss, acc = run_epoch(model, opt, xa, ya, rng)
        print(f"A{ep + 1}: loss {loss:.3f} train acc {acc:.2%}  ({time.time() - t0:.0f}s)", flush=True)
    pub_test = ~pub_train
    if pub_test.any():
        p = predict(model, pub_x[pub_test])
        report.append(f"Public test digits (sanity): {(p.argmax(1) == pub_y[pub_test]).mean():.2%}")
    base = score([r for r, k in zip(rows, te) if k], predict(model, ours_x[te]), ours_y[te])
    report += fmt("A. public data only, on your held-out sheets", base)

    # B. fine-tune: your digits repeated, real scribbles over-weighted, plus public digits so nothing is forgotten
    ours_tr_x, ours_tr_y = ours_x[tr], ours_y[tr]
    reps = 2 if args.quick else 30
    real_x = ours_tr_y == X
    for g in opt.param_groups:
        g["lr"] = 3e-4
    for ep in range(1 if args.quick else 6):
        pick = rng.choice(int(pub_train.sum()), min(int(pub_train.sum()), 3000 if args.quick else 30000), replace=False)
        xb = np.concatenate([np.repeat(ours_tr_x, reps, 0), np.repeat(ours_tr_x[real_x], reps * 5, 0),
                             pub_x[pub_train][pick], scr[n_scr * 3 // 4:]])
        yb = np.concatenate([np.repeat(ours_tr_y, reps), np.full(int(real_x.sum()) * reps * 5, X),
                             pub_y[pub_train][pick], np.full(n_scr - n_scr * 3 // 4, X)])
        if ep == 3:
            for g in opt.param_groups:
                g["lr"] = 1e-4
        loss, acc = run_epoch(model, opt, xb, yb, rng)
        print(f"B{ep + 1}: loss {loss:.3f} train acc {acc:.2%}  ({time.time() - t0:.0f}s)", flush=True)

    test_rows = [r for r, k in zip(rows, te) if k]
    probs = predict(model, ours_x[te])
    final = score(test_rows, probs, ours_y[te])
    report += fmt("B. after fine-tuning, on your held-out sheets", final)
    report.append("Per class (right / total): " + ", ".join(f"{k}: {a}/{b}" for k, (a, b) in final["per_class"].items()))
    report.append("Confusion (rows = true, columns = predicted " + " ".join(CLASSES) + "):")
    report += [f"  {CLASSES[i]}: " + " ".join(f"{v:3d}" for v in row) for i, row in enumerate(final["confusion"])]
    n_bad = mistakes_image(test_rows, ours_x[te], probs, ours_y[te], PREVIEW_DIR / "m4_mistakes.jpg")
    report.append(f"Mistakes shown in data\\preview\\m4_mistakes.jpg: {n_bad}")
    report.append("")
    for k, th in GATE.items():
        report.append(f"[{'PASS' if final[k] >= th else 'FAIL'}] {k} accuracy >= {th:.0%}: {final[k]:.2%}")
    report.append("Note: your held-out sheets are written by the same 1-2 people as the training sheets; the real test"
                  " is new writers (after M3 collection).")

    # D. export + check that OpenCV gives the same answers
    MODELS.mkdir(exist_ok=True)
    model.eval()
    torch.save(model.state_dict(), MODELS / "digit_cnn.pt")
    onnx_path = MODELS / "digit_cnn.onnx"
    dummy = torch.from_numpy(ours_x[:8]).unsqueeze(1)
    try:
        kw = dict(input_names=["image"], output_names=["logits"], dynamic_axes={"image": {0: "n"}, "logits": {0: "n"}})
        try:  # classic exporter: no extra packages; newer PyTorch may have removed it
            torch.onnx.export(model, dummy, str(onnx_path), opset_version=17, dynamo=False, **kw)
        except Exception:  # newer exporter needs: pip install onnx onnxscript
            torch.onnx.export(model, (dummy,), str(onnx_path), **kw)
        net = cv2.dnn.readNetFromONNX(str(onnx_path))
        net.setInput(dummy.numpy())
        diff = float(np.abs(net.forward() - model(dummy).detach().numpy()).max())
        assert diff < 1e-3, f"OpenCV and PyTorch disagree by {diff}"
        report.append(f"ONNX export: {onnx_path.relative_to(ROOT)} (OpenCV check: max difference {diff:.1e})")
    except Exception as exc:  # the .pt weights are saved either way
        report.append(f"ONNX export FAILED ({type(exc).__name__}: {exc}) - weights kept in models\\digit_cnn.pt")
    (MODELS / "digit_model.json").write_text(json.dumps({
        "classes": CLASSES, "input": "1x1x28x28 float32, white digit on black, brightest stroke = 1.0 "
        "(sm_align.to_mnist on the full_strokes mask, then divide by its max)", "x_means": "scribble/dash = empty",
        "test_scores": {k: final[k] for k in ("digit", "cell", "roll")}, "quick_run": args.quick}, indent=1))

    report.insert(0, f"M4 training {'(QUICK CHECK - not a real result)' if args.quick else ''} "
                     f"- {time.time() - t0:.0f}s on {torch.get_num_threads()} CPU threads")
    REPORT_DIR.mkdir(exist_ok=True)
    (REPORT_DIR / "m4_report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n" + "\n".join(report))


if __name__ == "__main__":
    main()
