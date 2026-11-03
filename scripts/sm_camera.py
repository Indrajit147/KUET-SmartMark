"""Live camera helpers for the teacher app (no window code here, so they can be tested on their own).

Camera(source)   source = webcam number (0, 1, ...) or a phone running the free 'IP Webcam' app,
                 e.g. 'http://192.168.0.105:8080' (preview from /video, full-resolution photo from /photoaf.jpg).
find_cover()     outline of the cover in a (small) preview frame, or None.
AutoShutter      says "take the picture now" once a cover has been held still, then waits for the next cover.
"""
from __future__ import annotations

import ipaddress
import os
import ssl
import threading
import time
import urllib.parse
import urllib.request

import cv2
import numpy as np

from sm_align import find_homography

DETECT_MIN_INLIERS = 25   # enough to say "a cover is in view"; reading re-checks everything at full resolution


class Camera:
    def __init__(self, source):
        self.source, self.phone = source, isinstance(source, str)
        if self.phone:
            cap = cv2.VideoCapture(source.rstrip("/") + "/video")
        else:
            cap = cv2.VideoCapture(source, cv2.CAP_DSHOW) if os.name == "nt" else cv2.VideoCapture(source)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 3840)   # ask for the most the webcam can give
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 2160)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open camera {source!r}" + (" - check the phone's address and that the phone "
                                                                  "and laptop are on the same Wi-Fi" if self.phone else ""))
        self.cap, self._frame, self._lock, self.running, self.error = cap, None, threading.Lock(), True, ""
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        misses = 0
        while self.running:
            ok, f = self.cap.read()
            if ok:
                with self._lock:
                    self._frame = f
                misses = 0
            else:
                misses += 1
                time.sleep(0.05)
                if misses > 100:  # ~5 s without a picture
                    self.error, self.running = "The camera stopped sending pictures.", False
        self.cap.release()

    def frame(self) -> np.ndarray | None:
        with self._lock:
            return None if self._frame is None else self._frame.copy()

    def still(self) -> np.ndarray:
        """The picture to read: the phone's full-resolution photo, or the newest webcam frame."""
        if not self.phone:
            f = self.frame()
            if f is None:
                raise RuntimeError("No picture from the webcam yet.")
            return f
        problems = []
        for path in ("/photoaf.jpg", "/photo.jpg"):  # photoaf = focus first, then take the photo
            url = self.source.rstrip("/") + path
            try:
                data = urllib.request.urlopen(url, timeout=20, context=_local_tls(url)).read()
            except OSError as exc:
                problems.append(f"{path}: {getattr(exc, 'reason', exc)}")
                continue
            img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                return img
            problems.append(f"{path}: the phone did not send a picture")
        raise RuntimeError("Could not get a photo from the phone (" + "; ".join(problems) + "). "
                           "Is the IP Webcam app still running?")

    def close(self):
        self.running = False


class FolderWatch:
    """Camera stand-in for photos taken with the phone's own camera and synced to a laptop folder
    (OneDrive camera upload, Syncthing, ...). Only photos that appear after watching starts are read."""
    phone = False

    def __init__(self, folder: str):
        from pathlib import Path
        self.folder = Path(folder)
        if not self.folder.is_dir():
            raise RuntimeError(f"Folder not found: {folder}")
        self.seen = {p.name for p in self.folder.iterdir()}
        self.sizes: dict[str, int] = {}
        self.running, self.error, self.note, self._frame = True, "", "", None

    def new_photo(self):
        """The next new photo that has finished copying (same size on two checks), or None."""
        try:
            files = sorted((p for p in self.folder.iterdir() if p.name not in self.seen and p.is_file()),
                           key=lambda p: p.stat().st_mtime)
        except OSError as exc:
            self.error, self.running = f"Cannot read the folder: {exc}", False
            return None
        for p in files:
            ext = p.suffix.lower()
            if ext in (".heic", ".heif"):
                self.seen.add(p.name)
                self.note = f"{p.name} is HEIC - set the phone camera to save JPEG photos."
            elif ext not in (".jpg", ".jpeg", ".png") or p.name.startswith((".", "~")):
                self.seen.add(p.name)  # sync tools' temp files and other files
            else:
                size = p.stat().st_size
                if size and self.sizes.get(p.name) == size:
                    self.seen.add(p.name)
                    self.note = ""
                    return p
                self.sizes[p.name] = size
        return None

    def show(self, img: np.ndarray):
        k = min(1.0, 1000 / max(img.shape[:2]))
        self._frame = cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)

    def frame(self) -> np.ndarray | None:
        return None if self._frame is None else self._frame.copy()

    def close(self):
        self.running = False


def read_image(path) -> np.ndarray:
    """cv2.imread that also works for non-English Windows paths."""
    img = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError(f"{os.path.basename(str(path))} is not a readable picture")
    return img


def _local_tls(url: str):
    """Phone camera apps serve https with their own (self-signed) certificate. Accept it only for addresses on
    the local network; anything else keeps normal certificate checks."""
    if not url.startswith("https://"):
        return None
    host = urllib.parse.urlparse(url).hostname or ""
    try:
        local = ipaddress.ip_address(host).is_private
    except ValueError:
        local = host.endswith(".local")
    if not local:
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def find_cover(frame: np.ndarray, t) -> np.ndarray | None:
    """Four corners of the cover in the frame (pixels), or None when no cover is in view."""
    H, inliers, _ = find_homography(frame, t)
    if H is None or inliers < DETECT_MIN_INLIERS:
        return None
    W, Hc = t.regions["canvas"]
    pts = cv2.perspectiveTransform(np.float32([[[0, 0]], [[W, 0]], [[W, Hc]], [[0, Hc]]]), np.linalg.inv(H)).reshape(4, 2)
    area = cv2.contourArea(pts)
    if not cv2.isContourConvex(pts.astype(np.int32)) or area < 0.05 * frame.shape[0] * frame.shape[1]:
        return None  # a twisted or tiny 'page' is a false match
    return pts


class AutoShutter:
    """Capture when the same cover outline is seen `steady` checks in a row (held still); then wait until
    the cover leaves the view before capturing again, so one cover is never taken twice."""
    def __init__(self, steady: int = 2, tolerance: float = 0.03):
        self.steady, self.tolerance = steady, tolerance
        self.prev, self.count, self.armed = None, 0, True

    def update(self, outline: np.ndarray | None, frame_diag: float) -> bool:
        if outline is None:
            self.prev, self.count, self.armed = None, 0, True
            return False
        still = self.prev is not None and float(np.abs(outline - self.prev).max()) < self.tolerance * frame_diag
        self.count = self.count + 1 if still else 1
        self.prev = outline
        if self.armed and self.count >= self.steady:
            self.armed = False
            return True
        return False
