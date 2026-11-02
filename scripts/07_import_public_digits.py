"""Milestone 3: convert public handwritten-digit datasets into the same 28x28 format as our digits.

Download the files on your laptop (see README step 9) into data\\external\\ and run:
    .venv\\Scripts\\python.exe scripts\\07_import_public_digits.py
Reads every '*-images-idx3-ubyte(.gz)' file in data\\external\\ (any subfolder) with its matching
'*-labels-idx1-ubyte(.gz)' file - this covers MNIST and EMNIST. EMNIST images are stored transposed
and are flipped back. Each digit is re-centred exactly like ours (fit in 20x20, centre by mass).
Writes data\\digits\\public.npz  (images uint8 N x 28 x 28, labels uint8, source str) - load with
np.load(..., allow_pickle=False) - and data\\preview\\m3_public.jpg for a visual check.
Self-test:  scripts\\07_import_public_digits.py --selftest
"""
from __future__ import annotations

import gzip
import sys
from pathlib import Path

import cv2
import numpy as np

from common import PREVIEW_DIR, ROOT
from sm_align import to_mnist

EXTERNAL = ROOT / "data" / "external"
OUT = ROOT / "data" / "digits" / "public.npz"


def read_idx(path: Path) -> np.ndarray:
    """Parse an IDX file (unsigned bytes only). Validates the header instead of trusting the download."""
    raw = (gzip.open if path.suffix == ".gz" else open)(path, "rb").read()
    if len(raw) < 8 or raw[0] != 0 or raw[1] != 0 or raw[2] != 0x08:
        raise ValueError(f"{path.name}: not an unsigned-byte IDX file")
    ndim = raw[3]
    dims = [int.from_bytes(raw[4 + 4 * i:8 + 4 * i], "big") for i in range(ndim)]
    body = np.frombuffer(raw, np.uint8, offset=4 + 4 * ndim)
    if body.size != int(np.prod(dims)):
        raise ValueError(f"{path.name}: header says {dims}, file holds {body.size} bytes (truncated download?)")
    return body.reshape(dims)


def pairs(folder: Path):
    for img in sorted(folder.rglob("*-images-idx3-ubyte*")):
        lab = img.with_name(img.name.replace("-images-idx3-ubyte", "-labels-idx1-ubyte"))
        if lab.exists():
            yield img, lab
        else:
            print(f"  ! no labels file for {img.name} - skipped")


def load_pair(img: Path, lab: Path) -> tuple[np.ndarray, np.ndarray]:
    x, y = read_idx(img), read_idx(lab)
    if x.ndim != 3 or x.shape[1:] != (28, 28) or y.ndim != 1 or len(x) != len(y):
        raise ValueError(f"{img.name}: unexpected shapes {x.shape} / {y.shape}")
    if y.max() > 9:
        raise ValueError(f"{lab.name}: labels above 9 - only digit sets are supported (use EMNIST 'digits')")
    if "emnist" in img.name.lower():
        x = x.transpose(0, 2, 1)  # EMNIST stores images transposed
    return x, y


def recentre(x: np.ndarray) -> np.ndarray:
    return np.stack([to_mnist((im > 30).astype(np.uint8) * im, blur=False) if im.max() > 30 else im for im in x])


def _selftest() -> None:
    import tempfile
    rng = np.random.default_rng(0)
    imgs = np.zeros((5, 28, 28), np.uint8)
    for i in range(5):
        imgs[i, 4:20, 8 + i:12 + i] = 255  # an off-centre bar
    labs = rng.integers(0, 10, 5).astype(np.uint8)
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        with gzip.open(d / "t-images-idx3-ubyte.gz", "wb") as f:
            f.write(bytes([0, 0, 8, 3]) + b"".join(v.to_bytes(4, "big") for v in (5, 28, 28)) + imgs.tobytes())
        with gzip.open(d / "t-labels-idx1-ubyte.gz", "wb") as f:
            f.write(bytes([0, 0, 8, 1]) + (5).to_bytes(4, "big") + labs.tobytes())
        (img, lab), = pairs(d)
        x, y = load_pair(img, lab)
        assert x.shape == (5, 28, 28) and (y == labs).all()
        r = recentre(x)
        cx = [cv2.moments(im)["m10"] / cv2.moments(im)["m00"] for im in r]
        assert all(abs(c - 14) < 1 for c in cx), cx  # re-centred by mass
        (d / "bad-images-idx3-ubyte").write_bytes(bytes([0, 0, 8, 3]) + (9).to_bytes(4, "big") * 3)
        try:
            read_idx(d / "bad-images-idx3-ubyte")
            raise AssertionError("truncated file was accepted")
        except ValueError:
            pass
    print("selftest ok")


def main() -> None:
    if "--selftest" in sys.argv:
        _selftest()
        return
    found = list(pairs(EXTERNAL)) if EXTERNAL.exists() else []
    if not found:
        raise SystemExit(f"No '*-images-idx3-ubyte' files under {EXTERNAL} - download them first (README step 9)")
    xs, ys, src = [], [], []
    for img, lab in found:
        x, y = load_pair(img, lab)
        name = img.parent.name + "/" + img.name.split("-images")[0]
        print(f"  {name}: {len(x)} digits")
        xs.append(recentre(x))
        ys.append(y)
        src += [name] * len(x)
    x, y = np.concatenate(xs), np.concatenate(ys)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(OUT, images=x, labels=y, source=np.array(src))
    grid = np.vstack([np.hstack(list(x[y == d][:30])) for d in range(10)])
    cv2.imwrite(str(PREVIEW_DIR / "m3_public.jpg"), cv2.resize(grid, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST))
    print(f"Public digits: {len(x)} -> {OUT.relative_to(ROOT)} | per class: {np.bincount(y, minlength=10).tolist()}")
    print("Check data\\preview\\m3_public.jpg: each row should show one digit, upright.")


if __name__ == "__main__":
    main()
