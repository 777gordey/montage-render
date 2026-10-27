"""Repo guard: refuses media files, secrets and personal data.

  python scripts/guard.py --staged   # pre-commit: only files in the git index
  python scripts/guard.py --all      # CI: every tracked file

Exit 1 on any finding. Findings print file, line and rule name only, never the matched text.
A line can opt out with a trailing `guard:allow` comment (use for test fixtures that must contain a fake secret).
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

MEDIA_EXT = {
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpg", ".mpeg", ".flv", ".wmv", ".3gp",
    ".wav", ".mp3", ".aac", ".m4a", ".flac", ".ogg", ".opus", ".aiff", ".wma",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".bmp", ".tif", ".tiff", ".psd",
}
BINARY_OK_EXT = {".ttf", ".otf", ".woff", ".woff2"}  # fonts are code assets, not media
MAX_BYTES = 2 * 1024 * 1024  # nothing bigger than this except the lockfile

MAGIC = [  # (offset, bytes, name)
    (4, b"ftyp", "mp4/mov"), (0, b"\x1a\x45\xdf\xa3", "matroska/webm"), (0, b"ID3", "mp3"), (0, b"OggS", "ogg"),
    (0, b"fLaC", "flac"), (0, b"\x89PNG", "png"), (0, b"\xff\xd8\xff", "jpeg"), (0, b"GIF8", "gif"),
]

SECRET_RULES = {
    "github-token": r"\bgh[pousr]_[A-Za-z0-9]{30,}\b|github_pat_[A-Za-z0-9_]{30,}",
    "aws-key": r"\bAKIA[0-9A-Z]{16}\b",
    "private-key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "telegram-bot-token": r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b",
    "llm-api-key": r"\bsk-[A-Za-z0-9_-]{20,}\b",
    "slack-token": r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b",
    "age-secret": r"AGE-SECRET-KEY-[A-Z0-9]{20,}",
    "signed-url-param": r"[?&](?:sig|signature|token|X-Amz-Signature|X-Goog-Signature)=[A-Za-z0-9%_.\-]{16,}",
    "tunnel-host": r"https?://[a-z0-9-]+\.(?:trycloudflare\.com|ngrok(?:-free)?\.(?:app|io|dev)|loca\.lt|lhr\.life)",
    "assigned-secret": r"(?i)\b(?:secret|passw(?:or)?d|api[_-]?key|auth[_-]?token|access[_-]?token)\b['\"]?\s*[:=]\s*['\"][^'\"\s]{12,}['\"]",
}
PII_RULES = {
    "email": r"\b[A-Za-z0-9._%+-]+@(?!example\.|users\.noreply\.github\.com)[A-Za-z0-9-]+\.[A-Za-z]{2,}\b",
    "phone": r"(?<![\w.])\+\d[\d ()\-]{9,}\d\b",
    "public-ipv4": r"(?<![\w.=<>~^-])(?!(?:10|127|0)\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\d.]*\d)",
    "home-path": r"(?i)(?:[A-Z]:[\\/]+Users[\\/]+(?!runner\b|user\b|name\b|you\b)[^\\/\s'\"]+|/home/(?!runner\b|user\b|name\b)[a-z0-9._-]+/|/Users/(?!Shared\b)[A-Za-z0-9._-]+/)",
    "ssh-key-file": r"\b(?:id_rsa|id_ed25519|vps_key)\b",  # guard:allow
}
RULES = [(n, re.compile(p)) for n, p in {**SECRET_RULES, **PII_RULES}.items()]
SKIP_TEXT_SCAN = {"package-lock.json"}  # integrity hashes only


def is_media(path: str, head: bytes) -> str | None:
    ext = Path(path).suffix.lower()
    if ext in MEDIA_EXT:
        return f"media extension {ext}"
    if ext in BINARY_OK_EXT:
        return None
    for off, sig, name in MAGIC:
        if head[off:off + len(sig)] == sig:
            return f"media content ({name})"
    if head[:4] == b"RIFF" and head[8:12] in (b"WAVE", b"AVI ", b"WEBP"):
        return "media content (riff)"
    return None


def scan_text(text: str) -> list[tuple[int, str]]:
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        if "guard:allow" in line:
            continue
        for name, rx in RULES:
            if rx.search(line):
                hits.append((i, name))
    return hits


def check_file(path: str, data: bytes) -> list[str]:
    out = []
    m = is_media(path, data[:16])
    if m:
        out.append(f"{path}: MEDIA {m}")
        return out
    name = Path(path).name
    if len(data) > MAX_BYTES and name not in SKIP_TEXT_SCAN:
        out.append(f"{path}: SIZE {len(data)} bytes over {MAX_BYTES}")
    if Path(path).suffix.lower() in BINARY_OK_EXT or name in SKIP_TEXT_SCAN:
        return out
    if b"\x00" in data[:4096]:
        out.append(f"{path}: BINARY non-font file")
        return out
    for ln, rule in scan_text(data.decode("utf-8", errors="replace")):
        out.append(f"{path}:{ln}: {rule}")
    return out


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], capture_output=True, check=True).stdout


def main(argv: list[str]) -> int:
    staged = "--staged" in argv
    if staged:
        names = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").decode("utf-8", "replace").split("\0")
    else:
        names = git("ls-files", "-z").decode("utf-8", "replace").split("\0")
    findings: list[str] = []
    for n in filter(None, names):
        data = git("show", f":{n}") if staged else Path(n).read_bytes()
        findings += check_file(n, data)
    for f in findings:
        print("GUARD:", f)
    if findings:
        print(f"GUARD: {len(findings)} finding(s); commit refused. Nothing matched is printed on purpose.")
        return 1
    print("GUARD: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
