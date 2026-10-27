import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import guard  # noqa: E402


class GuardUnit(unittest.TestCase):
    def test_media_by_extension_and_magic(self):
        self.assertTrue(guard.is_media("a.mp4", b""))
        self.assertTrue(guard.is_media("noext", b"\x00\x00\x00\x18ftypisom"))
        self.assertTrue(guard.is_media("x.bin", b"RIFF\x00\x00\x00\x00WAVEfmt "))
        self.assertIsNone(guard.is_media("f.ttf", b"\x00\x01\x00\x00"))
        self.assertIsNone(guard.is_media("a.py", b"print(1)"))

    def test_secrets_and_pii_detected(self):
        fake = {
            "github-token": "x = 'ghp_" + "a" * 36 + "'",
            "telegram-bot-token": "t = 123456789:" + "A" * 35,
            "signed-url-param": "https://h.example/p?sig=" + "ab12" * 8,
            "tunnel-host": "https://abc-def." + "trycloudflare" + ".com/x",
            "email": "mail me at person" + "@" + "mailbox.org",
            "public-ipv4": "host 203.0.113" + ".7 here",
            "home-path": "C:" + chr(92) + "Users" + chr(92) + "someone" + chr(92) + "x",
            "phone": "call +1 415 555 0100 now",  # guard:allow
        }
        for rule, line in fake.items():
            self.assertIn(rule, [r for _, r in guard.scan_text(line)], rule)

    def test_clean_text_and_allow_marker(self):
        self.assertEqual(guard.scan_text("version 4.0.538 and 24.095/24.1 s, 127.0.0.1, 10.0.0.2"), [])
        self.assertEqual(guard.scan_text("t = 123456789:" + "A" * 35 + "  # guard:allow"), [])

    def test_findings_do_not_echo_matched_text(self):
        out = guard.check_file("x.py", ("k = 'ghp_" + "Z" * 36 + "'").encode())
        self.assertTrue(out and "ZZZZ" not in " ".join(out))


class GuardStagedMedia(unittest.TestCase):
    def test_staged_media_fails_pre_commit(self):
        with tempfile.TemporaryDirectory() as d:
            run = lambda *a: subprocess.run(a, cwd=d, capture_output=True, text=True)
            run("git", "init", "-q")
            Path(d, "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\0" * 64)
            run("git", "add", "clip.mp4")
            r = subprocess.run([sys.executable, str(ROOT / "scripts" / "guard.py"), "--staged"], cwd=d, capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            self.assertIn("MEDIA", r.stdout)

    def test_staged_clean_file_passes(self):
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init", "-q"], cwd=d)
            Path(d, "a.py").write_text("print('hi')\n")
            subprocess.run(["git", "add", "a.py"], cwd=d)
            r = subprocess.run([sys.executable, str(ROOT / "scripts" / "guard.py"), "--staged"], cwd=d, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout)


if __name__ == "__main__":
    unittest.main()
