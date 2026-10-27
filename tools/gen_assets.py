"""Deterministic procedural SFX + music bed (numpy only). No third-party audio, so no licences to track.
Writes assets/*.wav (48 kHz mono, 16-bit). Replace with licensed packs later (see BACKLOG.md).
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SR = 48000
OUT = Path(__file__).resolve().parent.parent / "assets"


def write(name: str, x: np.ndarray) -> None:
    x = np.clip(x, -1, 1)
    OUT.mkdir(exist_ok=True)
    with wave.open(str(OUT / name), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((x * 32767).astype("<i2").tobytes())


def whoosh(seed: int, dur=0.55) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(SR * dur)
    x = rng.standard_normal(n)
    t = np.linspace(0, 1, n)
    # swept one-pole low-pass: cutoff rises then falls (air movement)
    cut = 300 + 5200 * np.sin(np.pi * t) ** 2 * (0.8 + 0.1 * seed)
    a = 1 - np.exp(-2 * np.pi * cut / SR)
    y = np.empty(n)
    acc = 0.0
    for i in range(n):
        acc += a[i] * (x[i] - acc)
        y[i] = acc
    env = np.sin(np.pi * t) ** 1.6
    y = y * env
    return 0.9 * y / (np.abs(y).max() + 1e-9)


def hit(seed: int, dur=0.7) -> np.ndarray:
    rng = np.random.default_rng(100 + seed)
    n = int(SR * dur)
    t = np.arange(n) / SR
    f = 48 + 90 * np.exp(-t * 18)
    ph = 2 * np.pi * np.cumsum(f) / SR
    body = np.sin(ph) * np.exp(-t * 5.5)
    click = rng.standard_normal(n) * np.exp(-t * 90) * 0.5
    y = body + click
    return 0.95 * y / (np.abs(y).max() + 1e-9)


def pop(seed: int, dur=0.16) -> np.ndarray:
    n = int(SR * dur)
    t = np.arange(n) / SR
    f = (900 + 120 * seed) * np.exp(-t * 7)
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 28)
    return 0.8 * y


def music(dur=60.0, bpm=92, seed=7) -> np.ndarray:
    """Quiet minor pad + soft plucked arpeggio. Intentionally plain: it sits under speech."""
    n = int(SR * dur)
    t = np.arange(n) / SR
    beat = 60 / bpm
    chords = [(57, 60, 64), (53, 57, 60), (48, 52, 55), (55, 59, 62)]  # Am F C G
    bar = beat * 4
    out = np.zeros(n)
    mid = lambda m: 440 * 2 ** ((m - 69) / 12)
    for bi in range(int(dur / bar) + 1):
        c = chords[bi % 4]
        s0, s1 = int(bi * bar * SR), min(n, int((bi + 1) * bar * SR))
        if s0 >= n:
            break
        tt = t[s0:s1] - t[s0]
        env = np.minimum(1, tt / 0.35) * np.minimum(1, (bar - tt) / 0.35)
        pad = sum(np.sin(2 * np.pi * mid(m - 12) * tt) + 0.5 * np.sin(2 * np.pi * mid(m) * 1.003 * tt) for m in c)
        out[s0:s1] += 0.12 * pad * env
        for k in range(8):  # eighth-note arpeggio
            st = k * beat / 2
            m = c[k % 3] + 12
            i0 = int(st * SR)
            seg = tt[i0:] - st
            if seg.size == 0:
                continue
            pl = np.sin(2 * np.pi * mid(m) * seg) * np.exp(-seg * 9)
            L = min(pl.size, s1 - s0 - i0)
            out[s0 + i0:s0 + i0 + L] += 0.09 * pl[:L]
        # soft kick on the beat
        for k in range(4):
            st = k * beat
            i0 = int(st * SR)
            seg = tt[i0:] - st
            if seg.size == 0:
                continue
            kk = np.sin(2 * np.pi * np.cumsum(50 + 60 * np.exp(-seg * 25)) / SR) * np.exp(-seg * 8)
            L = min(kk.size, s1 - s0 - i0)
            out[s0 + i0:s0 + i0 + L] += 0.22 * kk[:L]
    # gentle low-pass (one pole) so it never competes with consonants
    a = 1 - np.exp(-2 * np.pi * 3200 / SR)
    y = np.empty(n)
    acc = 0.0
    for i in range(n):
        acc += a * (out[i] - acc)
        y[i] = acc
    rms = np.sqrt(np.mean(y ** 2)) + 1e-9
    return y * (10 ** (-20 / 20) / rms)  # normalise to -20 dBFS RMS; level vs voice is set in audio_mix.py


def main() -> None:
    for i in range(4):
        write(f"whoosh_{i}.wav", whoosh(i))
    for i in range(2):
        write(f"hit_{i}.wav", hit(i))
        write(f"pop_{i}.wav", pop(i))
    write("bed.wav", music())
    print("assets written:", sorted(p.name for p in OUT.glob("*.wav")))


if __name__ == "__main__":
    main()
