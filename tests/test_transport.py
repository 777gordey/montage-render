import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import ci_job  # noqa: E402
import dispatch_job  # noqa: E402


def code(url, method="GET", data=None):
    try:
        req = urllib.request.Request(url, method=method, data=data, headers={"Content-Length": str(len(data))} if data is not None else {})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


class Transport(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        (self.d / "raw.bin").write_bytes(b"R" * 3000)
        (self.d / "plan.json").write_text("{}")
        self.sg = dispatch_job.Signer("j1", 60)
        self.dest = self.d / "result.tar"
        self.srv, self.done = dispatch_job.make_server(self.sg, {"raw": self.d / "raw.bin", "plan": self.d / "plan.json"}, self.dest)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.d, ignore_errors=True)

    def test_valid_get_and_ci_fetch(self):
        self.assertEqual(code(self.base + self.sg.path("raw", "raw.mp4"))[0], 200)
        n = ci_job.fetch(self.base + self.sg.path("plan", "plan.json"), self.d / "got.json")
        self.assertEqual(n, 2)

    def test_tampered_wrong_kind_and_expired_are_404(self):
        p = self.sg.path("raw", "raw.mp4")
        self.assertEqual(code(self.base + p[:-8] + "x" + p[-7:])[0], 404)
        self.assertEqual(code(self.base + p.replace("/raw/", "/plan/"))[0], 404)
        self.assertEqual(code(self.base + "/raw/1/" + "0" * 40 + "/raw.mp4")[0], 404)
        old = dispatch_job.Signer("j1", -5)
        old.key = self.sg.key
        self.assertEqual(code(self.base + old.path("raw", "raw.mp4"))[0], 404)

    def test_get_limited_to_three_then_revoked(self):
        p = self.base + self.sg.path("raw", "raw.mp4")
        self.assertEqual([code(p)[0] for _ in range(4)], [200, 200, 200, 404])

    def test_revoke_kills_all_urls(self):
        p = self.base + self.sg.path("plan", "plan.json")
        self.sg.revoked = True
        self.assertEqual(code(p)[0], 404)

    def test_put_once_atomic_and_second_refused(self):
        p = self.base + self.sg.path("put", "result.tar")
        ci_job.put(p, self.d / "plan.json")
        self.assertTrue(self.done.is_set())
        self.assertEqual(self.dest.read_text(), "{}")
        self.assertFalse(self.dest.with_suffix(".part").exists())
        self.assertEqual(code(p, "PUT", b"x")[0], 404)

    def test_put_cannot_be_used_as_get_and_get_urls_cannot_put(self):
        self.assertEqual(code(self.base + self.sg.path("put", "result.tar"))[0], 404)
        self.assertEqual(code(self.base + self.sg.path("raw", "raw.mp4"), "PUT", b"x")[0], 404)

    @unittest.skipUnless(shutil.which("openssl"), "openssl missing")
    def test_bundle_roundtrip_and_wrong_key(self):
        b = {"raw": "https://h.example/a", "plan": "https://h.example/b", "put": "https://h.example/c"}
        enc = dispatch_job.encrypt(b, "k" * 20)
        self.assertNotIn("h.example", enc)
        os.environ["MONTAGE_BUNDLE_KEY"] = "k" * 20
        self.assertEqual(ci_job.decrypt(enc), b)
        os.environ["MONTAGE_BUNDLE_KEY"] = "z" * 20
        with self.assertRaises(RuntimeError):
            ci_job.decrypt(enc)

    def test_job_id_validation(self):
        sys.argv = ["ci_job.py", "../x"]
        self.assertEqual(ci_job.main(), 2)


if __name__ == "__main__":
    unittest.main()
