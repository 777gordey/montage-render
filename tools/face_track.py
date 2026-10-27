"""Per-segment face anchor from the raw footage (OpenCV Haar, offline, no model download).
face_for_segments(raw, segments, fallback) -> list of {x,y,h,found} normalised to the source frame; y is the face-box centre.
Median of up to 5 sample frames per segment; segments with no detection reuse the nearest detected one, then the fallback.
"""
from __future__ import annotations

import os
import shutil
import statistics
import tempfile

import cv2


def _cascade():
    """OpenCV cannot open files under a non-ASCII path (Cyrillic user folder): load a copy from the ASCII 8.3 temp path."""
    src = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
    dst = os.path.join(tempfile.gettempdir(), "haarcascade_frontalface_default.xml")
    if not os.path.exists(dst):
        shutil.copyfile(src, dst)
    return cv2.CascadeClassifier(dst)


def face_for_segments(raw: str, segments, fallback: dict) -> list[dict]:
    cap = cv2.VideoCapture(raw)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    W, H = cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    cas = _cascade()
    out: list[dict | None] = []
    for a, b in segments:
        hits = []
        for k in range(5):
            t = a + (b - a) * (k + 0.5) / 5
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
            ok, fr = cap.read()
            if not ok:
                continue
            g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
            f = cas.detectMultiScale(g, 1.1, 6, minSize=(int(W * 0.12), int(W * 0.12)))
            if len(f):
                x, y, w, h = max(f, key=lambda r: r[2] * r[3])
                hits.append(((x + w / 2) / W, (y + h / 2) / H, h / H))
        if hits:
            out.append({"x": round(statistics.median(h[0] for h in hits), 4), "y": round(statistics.median(h[1] for h in hits), 4),
                        "h": round(statistics.median(h[2] for h in hits), 4), "found": len(hits)})
        else:
            out.append(None)
    cap.release()
    idx = [i for i, o in enumerate(out) if o]
    res = []
    for i, o in enumerate(out):
        if o:
            res.append(o)
        elif idx:
            j = min(idx, key=lambda k: abs(k - i))
            res.append({**out[j], "found": 0})
        else:
            res.append({"x": fallback["x"], "y": fallback["y"], "h": fallback["h"], "found": 0})
    return res


if __name__ == "__main__":
    import json, sys
    src = json.load(open(sys.argv[1], encoding="utf-8"))
    for s, f in zip(src["segments"], face_for_segments(sys.argv[2], src["segments"], src["face"])):
        print(s, f)
