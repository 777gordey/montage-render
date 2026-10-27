"""CI entry for one render job. Runs on the Actions runner.

Env: BUNDLE (openssl-encrypted JSON with the three signed URLs), MONTAGE_BUNDLE_KEY (Actions secret), optional CODEWORD.
Steps: decrypt -> mask URLs -> fetch plan + raw -> fetch sound packs -> run_clip.py -> pack result.tar -> signed PUT.
Logs carry only the generic job id, step names, timings and error classes. Never a URL, path of owner files or plan text.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def log(jid: str, *a) -> None:
    print(f"[task-{jid}]", *a, flush=True)


def decrypt(bundle_b64: str) -> dict:
    p = subprocess.run(
        ["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-md", "sha256", "-a", "-A", "-pass", "env:MONTAGE_BUNDLE_KEY"],
        input=bundle_b64.encode(), capture_output=True)
    if p.returncode != 0:
        raise RuntimeError("bundle_decrypt_failed")
    b = json.loads(p.stdout)
    for k in ("raw", "plan", "put"):
        if not str(b.get(k, "")).startswith("https://"):
            raise RuntimeError("bundle_invalid")
    return b


def fetch(url: str, dest: Path, tries: int = 3) -> int:
    last = "unknown"
    for n in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "montage-render"}), timeout=120) as r, open(dest, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            return dest.stat().st_size
        except urllib.error.HTTPError as e:
            last = f"http_{e.code}"
        except Exception as e:  # noqa: BLE001  (message may hold the URL: keep only the class)
            last = type(e).__name__
        time.sleep(3 * (n + 1))
    raise RuntimeError(f"fetch_failed:{last}")


def put(url: str, src: Path, tries: int = 3) -> None:
    last = "unknown"
    for n in range(tries):
        try:
            with open(src, "rb") as f:
                req = urllib.request.Request(url, data=f, method="PUT", headers={"Content-Length": str(src.stat().st_size), "Content-Type": "application/x-tar"})
                with urllib.request.urlopen(req, timeout=300) as r:
                    if r.status // 100 == 2:
                        return
                    last = f"http_{r.status}"
        except urllib.error.HTTPError as e:
            last = f"http_{e.code}"
        except Exception as e:  # noqa: BLE001
            last = type(e).__name__
        time.sleep(3 * (n + 1))
    raise RuntimeError(f"upload_failed:{last}")


def main() -> int:
    jid = sys.argv[1] if len(sys.argv) > 1 else ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,16}", jid):
        print("[task-?] invalid job id")
        return 2
    t0 = time.time()
    try:
        b = decrypt(os.environ["BUNDLE"])
    except Exception as e:  # noqa: BLE001
        log(jid, "FAILED", type(e).__name__, str(e) if isinstance(e, RuntimeError) else "")
        return 2
    for k in ("raw", "plan", "put"):
        print(f"::add-mask::{b[k]}", flush=True)
    work = ROOT / "work" / "job"
    work.mkdir(parents=True, exist_ok=True)
    plan, raw = work / "plan.json", work / "raw.mp4"
    try:
        n_plan = fetch(b["plan"], plan)
        n_raw = fetch(b["raw"], raw)
        log(jid, f"fetched plan {n_plan} B, raw {n_raw / 2**20:.1f} MB in {time.time() - t0:.0f} s")
        t1 = time.time()
        r = subprocess.run([PY, str(ROOT / "tools" / "fetch_sound.py"), str(ROOT / "work" / "sound_raw")], cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            log(jid, "FAILED sound_fetch", "rc", r.returncode, (r.stderr or "").strip().splitlines()[-1][:120] if os.environ.get("MONTAGE_DEBUG") == "1" and r.stderr.strip() else "")
            return 3
        log(jid, f"sound packs ready in {time.time() - t1:.0f} s")
        cmd = [PY, str(ROOT / "tools" / "run_clip.py"), jid, str(plan), str(raw), "--no-llm", "--min-free-mb", "1500", "--max-attempts", "3"]
        rc = subprocess.run(cmd, cwd=ROOT).returncode
        if rc != 0:
            log(jid, "FAILED render rc", rc)
            return 4
        out = ROOT / "out" / jid
        tar = work / "result.tar"
        with tarfile.open(tar, "w") as t:
            t.add(out / f"{jid}_code_layer_v2.mp4", arcname="result.mp4")
            t.add(out / f"{jid}_result.json", arcname="result.json")
        t2 = time.time()
        put(b["put"], tar)
        log(jid, f"uploaded {tar.stat().st_size / 2**20:.1f} MB in {time.time() - t2:.0f} s; job total {time.time() - t0:.0f} s")
        return 0
    except RuntimeError as e:
        log(jid, "FAILED", str(e))
        return 5


if __name__ == "__main__":
    sys.exit(main())
