"""Local side of one render job: the one-command render.

  python tools/dispatch_job.py <id> <plan.json> <raw.mp4> [--repo OWNER/montage-render] [--codeword X] [--emph emph.json]
                               [--expect-dur SEC] [--out DIR] [--ttl 1500] [--tunnel-bin ssh|PATH_TO_CLOUDFLARED] [--debug]

Serves the plan and the raw clip from a throw-away HTTP server on 127.0.0.1 through an HTTPS quick tunnel, behind
HMAC-signed URLs that expire (default 25 min) and are revoked as soon as the job ends. The three URLs are encrypted into
one workflow input with the Actions secret MONTAGE_BUNDLE_KEY (env var or .env.local, never printed), because dispatch
inputs of a public repo are readable by anyone. The result comes back by a signed PUT, is verified with ffprobe
and written to <out>/<id>/. Output carries only the generic job id.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.server
import json
import os
import re
import secrets
import shutil
import socketserver
import subprocess
import sys
import tarfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def log(jid, *a):
    print(f"[task-{jid}]", *a, flush=True)


class Signer:
    def __init__(self, jid: str, ttl: int):
        self.jid, self.exp, self.key = jid, int(time.time()) + ttl, secrets.token_bytes(32)
        self.uses: dict[str, int] = {}
        self.revoked = False

    def sig(self, kind: str) -> str:
        return hmac.new(self.key, f"{self.jid}|{kind}|{self.exp}".encode(), hashlib.sha256).hexdigest()[:40]

    def path(self, kind: str, name: str) -> str:
        return f"/{kind}/{self.exp}/{self.sig(kind)}/{name}"

    def check(self, path: str, want_kind: str) -> bool:
        m = re.fullmatch(r"/(raw|plan|put)/(\d+)/([0-9a-f]{40})/[\w.\-]+", path)
        if not m or m[1] != want_kind or self.revoked or int(m[2]) != self.exp or int(m[2]) < time.time():
            return False
        return hmac.compare_digest(m[3], self.sig(want_kind))


def make_server(signer: Signer, files: dict[str, Path], put_dest: Path, max_get: int = 3):
    done = threading.Event()
    stats = {"put": 0}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):  # no access log: paths carry signatures
            pass

        def _no(self):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            kind = self.path.split("/")[1] if self.path.count("/") >= 4 else ""
            if kind not in files or not signer.check(self.path, kind) or signer.uses.get(kind, 0) >= max_get:
                return self._no()
            signer.uses[kind] = signer.uses.get(kind, 0) + 1
            f = files[kind]
            self.send_response(200)
            self.send_header("Content-Length", str(f.stat().st_size))
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            with open(f, "rb") as fh:
                shutil.copyfileobj(fh, self.wfile)

        def do_PUT(self):
            if not signer.check(self.path, "put") or stats["put"] >= 1 or signer.uses.get("put", 0) >= max_get:
                return self._no()
            signer.uses["put"] = signer.uses.get("put", 0) + 1
            n = int(self.headers.get("Content-Length", "0"))
            part = put_dest.with_suffix(".part")
            got = 0
            with open(part, "wb") as f:
                while got < n:
                    chunk = self.rfile.read(min(1 << 20, n - got))
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
            if got != n or n == 0:
                part.unlink(missing_ok=True)
                return self._no()
            os.replace(part, put_dest)
            stats["put"] = 1
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            done.set()

    class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True

    srv = S(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, done


def start_tunnel(binary: str, port: int):
    """binary 'ssh' = localhost.run over the system ssh client (no download, no account); otherwise a cloudflared path."""
    if binary == "ssh":
        null = "NUL" if os.name == "nt" else "/dev/null"
        cmd = ["ssh", "-T", "-o", f"UserKnownHostsFile={null}", "-o", "StrictHostKeyChecking=no", "-o", "ServerAliveInterval=30",
               "-o", "ExitOnForwardFailure=yes", "-R", f"80:127.0.0.1:{port}", "nokey@localhost.run"]  # guard:allow
        rx = r"https://[a-z0-9]+\.lhr\.life"
    else:
        cmd = [binary, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"]
        rx = r"https://[a-z0-9-]+\.trycloudflare\.com"
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    found: list[str] = []

    def pump():
        for line in p.stdout:
            m = re.search(rx, line)
            if m and not found:
                found.append(m[0])

    threading.Thread(target=pump, daemon=True).start()
    for _ in range(90):
        if found:
            return p, found[0]
        if p.poll() is not None:
            break
        time.sleep(1)
    p.kill()
    raise SystemExit("tunnel_failed")


def encrypt(obj: dict, key: str) -> str:
    env = {**os.environ, "MONTAGE_BUNDLE_KEY": key}
    p = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-md", "sha256", "-a", "-A", "-pass", "env:MONTAGE_BUNDLE_KEY"],
                       input=json.dumps(obj).encode(), capture_output=True, env=env)
    if p.returncode != 0:
        raise SystemExit("encrypt_failed")
    return p.stdout.decode()


def load_key() -> str:
    k = os.environ.get("MONTAGE_BUNDLE_KEY")
    f = ROOT / ".env.local"
    if not k and f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.startswith("MONTAGE_BUNDLE_KEY="):
                k = line.split("=", 1)[1].strip()
    if not k:
        raise SystemExit("MONTAGE_BUNDLE_KEY missing (env or .env.local)")
    return k


def gh(*a):
    return subprocess.run(["gh", *a], capture_output=True, text=True, encoding="utf-8", errors="replace")


def iso(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def probe_dur(p: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    return float(r.stdout.strip())


def main() -> int:
    a = sys.argv[1:]
    jid, plan_p, raw_p = a[0], Path(a[1]), Path(a[2])
    opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    repo = opt("--repo", "777gordey/montage-render")
    ttl = int(opt("--ttl", 1500))
    out = Path(opt("--out", ROOT / "out")) / jid / "actions"
    out.mkdir(parents=True, exist_ok=True)
    key = load_key()

    plan = json.loads(plan_p.read_text(encoding="utf-8"))
    if opt("--emph"):
        plan["emph"] = json.loads(Path(opt("--emph")).read_text(encoding="utf-8"))["emph"]
    if opt("--codeword"):
        plan["codeword"] = opt("--codeword")
    served_plan = out / "plan.served.json"
    served_plan.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    signer = Signer(jid, ttl)
    result_tar = out / "result.tar"
    srv, done = make_server(signer, {"raw": raw_p, "plan": served_plan}, result_tar)
    tun = None
    t_start = time.time()
    try:
        tun, base = start_tunnel(opt("--tunnel-bin", "ssh"), srv.server_address[1])
        bundle = encrypt({"raw": base + signer.path("raw", "raw.mp4"), "plan": base + signer.path("plan", "plan.json"),
                          "put": base + signer.path("put", "result.tar")}, key)
        t_disp = time.time()
        cmd = ["workflow", "run", "render.yml", "-R", repo, "-f", f"task_id={jid}", "-f", f"bundle={bundle}"]
        if "--debug" in a:
            cmd += ["-f", "debug=true"]
        r = gh(*cmd)
        if r.returncode != 0:
            log(jid, "dispatch FAILED", r.stderr.strip()[:200].replace(bundle, "<bundle>"))
            return 1
        run_id = None
        for _ in range(30):
            time.sleep(4)
            lst = json.loads(gh("run", "list", "-R", repo, "--workflow", "render.yml", "--limit", "5", "--json", "databaseId,createdAt,event").stdout or "[]")
            lst = [x for x in lst if x["event"] == "workflow_dispatch" and iso(x["createdAt"]) >= t_disp - 10]
            if lst:
                run_id = lst[0]["databaseId"]
                break
        if not run_id:
            log(jid, "run not found")
            return 1
        log(jid, f"dispatched, run {run_id}")
        status = {}
        while True:
            time.sleep(10)
            v = gh("run", "view", str(run_id), "-R", repo, "--json", "status,conclusion,createdAt,updatedAt,jobs")
            if v.returncode == 0 and v.stdout:
                status = json.loads(v.stdout)
                if status["status"] == "completed":
                    break
            if time.time() - t_disp > ttl + 600:
                log(jid, "timeout waiting for the run")
                return 1
        log(jid, f"run finished: {status['conclusion']}")
        t_end = time.time()
    finally:
        signer.revoked = True
        srv.shutdown()
        if tun:
            tun.kill()

    tim = {"job": jid, "run_id": run_id, "conclusion": status["conclusion"], "wall_total_s": round(t_end - t_disp, 1)}
    for j in status.get("jobs", []):
        if j.get("startedAt") and j.get("completedAt"):
            tim["queue_s"] = round(iso(j["startedAt"]) - iso(status["createdAt"]), 1)
            tim["job_s"] = round(iso(j["completedAt"]) - iso(j["startedAt"]), 1)
            tim["steps"] = {s["name"]: round(iso(s["completedAt"]) - iso(s["startedAt"]), 1) for s in j["steps"] if s.get("startedAt") and s.get("completedAt")}
    ok = status["conclusion"] == "success" and result_tar.exists()
    if ok:
        with tarfile.open(result_tar) as t:
            names = t.getnames()
            if sorted(names) != ["result.json", "result.mp4"]:
                raise SystemExit("unexpected tar content")
            t.extractall(out, filter="data")
        res = json.loads((out / "result.json").read_text(encoding="utf-8"))
        dur = probe_dur(out / "result.mp4")
        exp = float(opt("--expect-dur", res["expected_dur"]))
        tim.update({"ffprobe_dur": round(dur, 3), "expected_dur": exp, "dur_ok": abs(dur - exp) <= 0.1,
                    "remote_render_s": res["render_s"], "remote_audio_s": res["audio_s"], "remote_wall_s": res["wall_s"],
                    "remote_peak_rss_mb": res["peak_tree_rss_mb"], "size_mb": res["size_mb"]})
    (out / "timings.json").write_text(json.dumps(tim, indent=1), encoding="utf-8")
    log(jid, json.dumps({k: v for k, v in tim.items() if k != "steps"}))
    return 0 if ok and tim.get("dur_ok") else 1


if __name__ == "__main__":
    sys.exit(main())
