"""Synthetic inputs for the runner self-test (no real footage): a 40 s test-pattern clip with a tone and a made-up edit plan.
Usage: python selftest_inputs.py <out_dir>   -> <out_dir>/plan.json, <out_dir>/raw.mp4
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

WORDS = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar papa quebec romeo sierra "
         "tango uniform victor whiskey xray yankee zulu one two three four five six seven eight nine ten eleven twelve "
         "thirteen fourteen fifteen sixteen seventeen eighteen").split()


def build(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    segments = [[1.0, 8.0], [10.0, 17.0], [19.0, 25.0]]  # 20 s of raw time
    words, t = [], 0.1
    for w in WORDS[:40]:
        words.append({"w": w, "s": round(t, 2), "e": round(t + 0.36, 2)})
        t += 0.4
    plan = {"dur": 20.0, "raw_dur": 40.0, "segments": segments, "words": words, "hook": "synthetic selftest hook line",
            "moments": [], "face": {"x": 0.5, "y": 0.45, "h": 0.3, "found": 0}, "codeword": "TEST"}
    (out / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-f", "lavfi", "-i", "testsrc2=size=720x1280:rate=30:duration=40",
                        "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=40",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-c:a", "aac", "-shortest", str(out / "raw.mp4")],
                       capture_output=True, text=True)
    if r.returncode:
        raise SystemExit("selftest raw failed")


if __name__ == "__main__":
    build(Path(sys.argv[1]))
