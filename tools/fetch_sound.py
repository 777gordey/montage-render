"""Fetch free-licence SFX/music from Mixkit (public pages, no login), normalise, write assets/real/ and the licence ledger.
Run once: python fetch_sound.py. Raw downloads go to a scratch dir, never to git (assets/ is media: see .gitignore).
"""
from __future__ import annotations

import datetime
import subprocess
import sys
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "real"
RAW = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "work" / "sound_raw"
UA = {"User-Agent": "Mozilla/5.0"}
# (kind, variant, mixkit id, page title) ; riser candidates are auto-screened by rising energy
SFX = [("whoosh", 0, 1489, "Woosh"), ("whoosh", 1, 1491, "Whoosh"), ("whoosh", 2, 1485, "Whoosh"), ("whoosh", 3, 1490, "Woosh"),
       ("hit", 0, 2198, "Impact"), ("hit", 1, 2182, "Hit"), ("pop", 0, 2358, "Pop"), ("pop", 1, 2364, "Pop"), ("pop", 2, 2354, "Pop")]
RISER_CAND = [1492, 787, 788, 784, 2918, 2623, 2350, 166, 1714, 1486]
MUSIC = [(0, 282, "Chillout", "https://mixkit.co/free-stock-music/chillout/"), (1, 876, "Serious", "https://mixkit.co/free-stock-music/mood/serious/"),
         (2, 738, "Contemporary R&B", "https://mixkit.co/free-stock-music/contemporary-r-and-b/")]


def get(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.write_bytes(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read())
    return dest


def ff(*a):
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *a], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr[-400:])


def mean_db(p: Path) -> float:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(p), "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True)
    return float(r.stderr.split("mean_volume:")[1].split("dB")[0])


def pcm(p: Path) -> np.ndarray:
    b = subprocess.run(["ffmpeg", "-v", "error", "-i", str(p), "-ac", "1", "-ar", "16000", "-f", "s16le", "-"], capture_output=True).stdout
    return np.frombuffer(b, dtype=np.int16).astype(np.float32) / 32768


def rising(p: Path) -> float:
    x = pcm(p)
    n = len(x) // 3
    if n < 100:
        return -99
    r = lambda s: 20 * np.log10(np.sqrt(np.mean(s ** 2)) + 1e-9)
    return r(x[2 * n:]) - r(x[:n])


def sfx(src: Path, dst: Path, maxd: float) -> None:
    ff("-i", str(src), "-af", f"silenceremove=start_periods=1:start_threshold=-45dB,atrim=0:{maxd},afade=t=out:st={maxd - 0.12}:d=0.12,loudnorm=I=-18:TP=-2:LRA=7,aresample=48000",
       "-ac", "1", "-ar", "48000", str(dst))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    today = datetime.date.today().isoformat()
    for kind, v, i, title in SFX:
        raw = get(f"https://assets.mixkit.co/active_storage/sfx/{i}/{i}-preview.mp3", RAW / f"sfx_{i}.mp3")
        sfx(raw, OUT / f"{kind}_{v}.wav", 1.4 if kind != "hit" else 1.2)
        rows.append((f"{kind}_{v}.wav", f"Mixkit SFX #{i} ({title})", f"https://mixkit.co/free-sound-effects/ (item id {i}; preview file https://assets.mixkit.co/active_storage/sfx/{i}/{i}-preview.mp3)", "Mixkit Sound Effects Free License", today))
    scored = []
    for i in RISER_CAND:
        raw = get(f"https://assets.mixkit.co/active_storage/sfx/{i}/{i}-preview.mp3", RAW / f"sfx_{i}.mp3")
        scored.append((rising(raw), i, raw))
    scored.sort(reverse=True)
    print("riser screen (rise dB, id):", [(round(s, 1), i) for s, i, _ in scored[:5]])
    for v, (s, i, raw) in enumerate(scored[:2]):
        sfx(raw, OUT / f"riser_{v}.wav", 2.0)
        rows.append((f"riser_{v}.wav", f"Mixkit SFX #{i}, picked by rising-energy screen (+{s:.1f} dB last vs first third)", f"https://assets.mixkit.co/active_storage/sfx/{i}/{i}-preview.mp3", "Mixkit Sound Effects Free License", today))
    for v, i, title, page in MUSIC:
        raw = get(f"https://assets.mixkit.co/music/{i}/{i}.mp3", RAW / f"music_{i}.mp3")
        tmp = OUT / f"bed_{v}.tmp.wav"
        ff("-i", str(raw), "-t", "75", "-ac", "1", "-ar", "48000", str(tmp))
        g = -20.0 - mean_db(tmp)  # same convention as the old bed.wav: -20 dBFS mean
        ff("-i", str(tmp), "-af", f"volume={g:.2f}dB,alimiter=limit=0.9", str(OUT / f"bed_{v}.wav"))
        tmp.unlink()
        rows.append((f"bed_{v}.wav", f"Mixkit music #{i} ({title}), first 75 s", page + f" (track id {i}, https://assets.mixkit.co/music/{i}/{i}.mp3)", "Mixkit Music Free License (no attribution; do not redistribute the files as a pack)", today))
    led = ["# Audio licence ledger (assets/real/)", "", "All files: free-licence downloads from Mixkit public pages (no login). Not redistributable standalone: keep out of any public repo (assets/ is git-ignored).", "",
           "| File | Source item | URL | Licence | Fetched |", "|---|---|---|---|---|"]
    led += [f"| {a} | {b} | {c} | {d} | {e} |" for a, b, c, d, e in rows]
    (OUT / "LICENSES.md").write_text("\n".join(led) + "\n", encoding="utf-8")
    print("ok", len(rows), "files")


if __name__ == "__main__":
    main()
