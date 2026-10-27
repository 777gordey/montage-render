"""Pick payoff words for caption emphasis / punch-ins / keyword cards with ONE `claude -p` call per clip.
Optional: goes through a local helper project (kf.llm.call_claude) found via MONTAGE_KF_DIR; without it the caller falls back to the stem heuristic.
Result is cached in plans/<tag>.emph.json: re-renders never spend a call. Any failure (cap, auth, bad JSON) -> None, caller falls back to the stem heuristic.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KF = Path(os.environ.get("MONTAGE_KF_DIR") or ROOT.parent / "knowledge-feed")

RUNNER = r"""
import sys, json
sys.path.insert(0, ".")
from kf import db, llm
con = db.connect("data/kf.sqlite")
prompt = sys.stdin.buffer.read().decode("utf-8")
try:
    out = {"ok": True, "text": llm.call_claude(con, prompt, note="montage emphasis")}
except Exception as e:
    out = {"ok": False, "err": type(e).__name__ + ": " + str(e)[:200]}
sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))
"""


def _prompt(words: list[str], hook: str) -> str:
    numbered = " ".join(f"{i}:{w}" for i, w in enumerate(words))
    return (
        "Ты монтажёр коротких вертикальных видео. Ниже транскрипт Reels, слова пронумерованы.\n"
        f"Хук: {hook}\n\n{numbered}\n\n"
        "Выбери 4-6 слов-смысловых кульминаций (то, что зритель должен запомнить: ключевое существительное, "
        "число, контраст, обещание). Нельзя служебные слова (что, это, как, то, и, в, на). Не два слова подряд. "
        "Минимум 2 слова между выбранными. Ответь ТОЛЬКО JSON: {\"emph\": [номера слов]}."
    )


def pick(tag: str, words: list[str], hook: str, cache: Path | None = None) -> list[int] | None:
    cache = cache or ROOT / "plans" / f"{tag}.emph.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))["emph"]
    if not KF.is_dir():
        return None
    p = subprocess.run([sys.executable, "-c", RUNNER], input=_prompt(words, hook).encode("utf-8"), capture_output=True, cwd=str(KF), timeout=700)
    try:
        r = json.loads(p.stdout.decode("utf-8"))
        if not r["ok"]:
            print(f"[emphasis_llm] {r['err']} -> heuristic fallback", flush=True)
            return None
        t = r["text"]
        j = json.loads(t[t.index("{"):t.rindex("}") + 1])
        idx = sorted({int(i) for i in j["emph"] if 0 <= int(i) < len(words)})
        if not 2 <= len(idx) <= 9:
            return None
    except Exception as e:  # noqa: BLE001
        print(f"[emphasis_llm] unusable reply ({type(e).__name__}) -> heuristic fallback", flush=True)
        return None
    cache.parent.mkdir(exist_ok=True)
    cache.write_text(json.dumps({"emph": idx, "words": [words[i] for i in idx]}, ensure_ascii=False), encoding="utf-8")
    return idx
