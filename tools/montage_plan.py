"""Build and validate a montage plan (v1) from an intake plan.

Montage plan = the single input of the code layer. All times are on the CUT timeline
(raw segments concatenated, before speed). The renderer and the audio builder convert
to the final timeline in exactly one place each (to_final), using `pieces`.

Usage: python montage_plan.py <plan_src.json> <raw.mp4> <out_plan.json> [--id N] [--speed 1.2]
       [--codeword TEXT] [--no-ramps]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PLAN_VERSION = 1
MIN_SPEED, MAX_SPEED = 1.0, 1.3
PUNCH = 1.35  # face-anchored punch-in framing (source is 720p: >1.4 gets soft)
MAX_CUTAWAYS, CUTAWAY_DUR = 3, 1.15

# stems of words that carry the point of money/discipline/business/self-growth talk
EMPH_STEMS = (
    "деньг", "рубл", "миллион", "тысяч", "доход", "прибыл", "бизнес", "клиент", "продаж", "успех",
    "дисциплин", "привыч", "мотивац", "никогда", "всегда", "ошибк", "правд", "секрет", "главн",
    "глуп", "умн", "лень", "страх", "время", "результат", "цель", "бедн", "богат", "работ",
    "автомат", "агент", "ии", "нейросет", "полезн", "бесполезн", "запомн", "забуд",
)
NEG = ("не", "нет", "ни", "без")


def err(code: str, msg: str):
    raise ValueError(f"{code}: {msg}")


def merge_tokens(words: list[dict]) -> list[dict]:
    """'что' + '-то' -> 'что-то'; keeps first start / last end."""
    out: list[dict] = []
    for w in words:
        t = w["w"]
        if out and (t.startswith("-") or t in {"—", "-"}) and len(t) > 0:
            out[-1] = {"w": out[-1]["w"] + t, "s": out[-1]["s"], "e": w["e"]}
        else:
            out.append({"w": t, "s": float(w["s"]), "e": float(w["e"])})
    return out


def _clean(w: str) -> str:
    return re.sub(r"[^\w-]", "", w.lower())


def emphasis_scores(words: list[dict], kws: list[str]) -> list[float]:
    kws = {k.lower() for k in kws}
    sc = []
    for i, w in enumerate(words):
        c = _clean(w["w"])
        s = 0.0
        if re.search(r"\d", c):
            s += 3
        if any(c.startswith(st) for st in EMPH_STEMS):
            s += 2.5
        if c in kws:
            s += 3
        if len(c) >= 9:
            s += 0.8
        if c in NEG and i + 1 < len(words):
            s += 0.3
        if re.search(r"[.!?]$", w["w"]):
            s += 0.6  # sentence-final word often carries the payoff
        sc.append(s)
    return sc


def pick_emphasis(words: list[dict], kws: list[str], min_gap: float = 1.6, max_share: float = 0.14) -> set[int]:
    sc = emphasis_scores(words, kws)
    order = sorted(range(len(words)), key=lambda i: -sc[i])
    chosen: list[int] = []
    cap = max(2, int(len(words) * max_share))
    for i in order:
        if sc[i] < 2.0 or len(chosen) >= cap:
            continue
        if all(abs(words[i]["s"] - words[j]["s"]) >= min_gap for j in chosen):
            chosen.append(i)
    return set(chosen)


def clean_hook(text: str) -> str:
    """Drop transcript stutter: leading hyphens and immediate word repeats ('делаешь -делаешь')."""
    out: list[str] = []
    for tok in text.split():
        t = tok.lstrip("-—")
        if not t:
            continue
        if out and re.sub(r"\W", "", out[-1].lower()) == re.sub(r"\W", "", t.lower()):
            continue
        out.append(t)
    return " ".join(out)


def split_hook(text: str, width: int = 17) -> list[str]:
    lines, cur = [], ""
    for tok in text.split():
        if cur and len(cur) + 1 + len(tok) > width:
            lines.append(cur)
            cur = tok
        else:
            cur = f"{cur} {tok}".strip()
    if cur:
        lines.append(cur)
    return lines[:4]


RAMP_STEPS = 6


def build_pieces(segments, speed, scales, ramp_windows, faces=None):
    """Pieces on the raw timeline. A ramp window is (src_t0, src_t1, peak): that part of a segment is
    played with a smooth speed bump 1 -> peak -> 1 (sin^2 easing, RAMP_STEPS sub-pieces), so the
    change of speed is eased instead of a single step. Rates are absolute (speed * multiplier)."""
    import math
    pieces = []
    for si, (s, e) in enumerate(segments):
        face = (faces[si] if faces else None)
        wins = sorted(ramp_windows.get(si, []))
        cur = s
        def add(a, b, mult):
            if b - a < 1e-3:
                return
            d = {"seg": si, "src_s": round(a, 4), "src_e": round(b, 4), "rate": round(speed * mult, 4), "scale": scales[si]}
            if face:
                d["face"] = {"x": face["x"], "y": face["y"], "h": face["h"]}
            pieces.append(d)
        for (r0, r1, peak) in wins:
            add(cur, r0, 1.0)
            n = RAMP_STEPS
            for k in range(n):
                u = (k + 0.5) / n
                add(r0 + (r1 - r0) * k / n, r0 + (r1 - r0) * (k + 1) / n, 1.0 + (peak - 1.0) * math.sin(math.pi * u) ** 2)
            cur = r1
        add(cur, e, 1.0)
    return pieces


def to_final(t_cut: float, pieces: list[dict]) -> float:
    """cut-timeline seconds -> final-timeline seconds (the one conversion helper)."""
    acc_cut, acc_fin = 0.0, 0.0
    for p in pieces:
        d = p["src_e"] - p["src_s"]
        if t_cut <= acc_cut + d + 1e-9:
            return acc_fin + (t_cut - acc_cut) / p["rate"]
        acc_cut += d
        acc_fin += d / p["rate"]
    return acc_fin


def build_plan(src: dict, raw_path: str, clip_id: str, speed: float, codeword: str | None,
               ramps: bool = True, probe: dict | None = None, faces: list | None = None,
               emph_idx: list | None = None, cutaways_on: bool = True) -> dict:
    if not (MIN_SPEED <= speed <= MAX_SPEED):
        err("MP02", f"speed {speed} out of range {MIN_SPEED}..{MAX_SPEED}")
    segs = [(float(a), float(b)) for a, b in src["segments"]]
    if not segs:
        err("MP03", "no segments")
    for a, b in segs:
        if b - a < 0.2:
            err("MP04", f"segment too short {a}-{b}")
    d_cut = sum(b - a for a, b in segs)
    words = merge_tokens(src["words"])
    if not words:
        err("MP05", "no words")
    if words[-1]["e"] > d_cut + 0.5:
        err("MP06", f"words end {words[-1]['e']} beyond cut duration {d_cut}")
    kws = [m["kw"] for m in src.get("moments", []) if m.get("kw")] + ([src["hook_scene"]["kw"]] if src.get("hook_scene", {}).get("kw") else [])
    emph = set(emph_idx) if emph_idx else pick_emphasis(words, kws)
    emph_source = "model" if emph_idx else "heuristic"
    for i, w in enumerate(words):
        w["emph"] = i in emph

    # cut timeline start of every segment
    cut_starts, acc = [], 0.0
    for a, b in segs:
        cut_starts.append(acc)
        acc += b - a

    # alternating framing: wide / punch, so every cut also changes scale (hides the jump cut)
    scales = [1.0 if i % 2 == 0 else PUNCH for i in range(len(segs))]

    # eased speed ramps: a 0.9 s source window at the tail of up to 3 plain segments, peak x1.45 (sin^2 bump)
    ramp_windows: dict[int, list] = {}
    if ramps:
        n = 0
        for si, (a, b) in enumerate(segs):
            if si == 0 or si == len(segs) - 1 or b - a < 2.2 or n >= 3 or si % 3 != 1:
                continue
            c0 = cut_starts[si] + (b - a) - 0.9
            c1 = cut_starts[si] + (b - a)
            if any(w["emph"] and c0 - 0.1 <= w["s"] <= c1 for w in words):
                continue
            ramp_windows[si] = [(b - 0.9, b, 1.45)]
            n += 1
    pieces = build_pieces(segs, speed, scales, ramp_windows, faces)
    d_final = to_final(d_cut, pieces)

    # zooms (cut timeline): opening punch + one per emphasis word
    zooms = [{"t": 0.0, "dur": 0.5, "peak": 1.12, "why": "hook-open"}]
    for i in sorted(emph):
        w = words[i]
        base = scales[min(range(len(segs)), key=lambda k: abs(cut_starts[k] - w["s"]) if cut_starts[k] <= w["s"] + 1e-6 else 1e9)]
        zooms.append({"t": round(max(0.0, w["s"] - 0.04), 3), "dur": 0.6, "peak": round(min(1.6, max(base + 0.12, 1.12)), 3), "why": words[i]["w"]})

    # cutaway slots (final timeline): keyword cards on model-picked payoff words, 1.15 s, max 3, >= 3.5 s apart, not in the hook or the CTA
    cutaways: list[dict] = []
    if cutaways_on:
        last_end = -9.0
        for i in sorted(emph, key=lambda k: words[k]["s"]):
            tf = to_final(words[i]["s"], pieces)
            txt = re.sub(r"[^\w-]", "", words[i]["w"]).upper()
            if len(txt) < 3 or tf < 3.4 or tf > d_final - 2.2 - CUTAWAY_DUR - 1.0 or tf - last_end < 3.5 or len(cutaways) >= MAX_CUTAWAYS:
                continue
            cutaways.append({"t": round(tf - 0.05, 3), "dur": CUTAWAY_DUR, "text": txt, "style": len(cutaways) % 3})
            last_end = tf - 0.05 + CUTAWAY_DUR
    in_cut = lambda tf, pad=0.0: any(c["t"] - pad <= tf <= c["t"] + c["dur"] + pad for c in cutaways)

    # sparse SFX (final timeline `tf`): spec = at most 1 per 2 cuts, whoosh on cutaway entries, quiet pop on 2 emphasis words, hit + riser into the CTA
    cues: list[dict] = [{"tf": c["t"] - 0.04, "kind": "whoosh"} for c in cutaways]
    for si in range(1, len(segs)):
        tf = to_final(cut_starts[si], pieces)
        if si % 3 == 2 and 1.5 < tf < d_final - 3.0 and not in_cut(tf, 1.0) and all(abs(tf - c["tf"]) > 1.2 for c in cues):
            cues.append({"tf": round(tf - 0.03, 3), "kind": "whoosh"})
    npop = 0
    for i in sorted(emph, key=lambda k: words[k]["s"]):
        tf = to_final(words[i]["s"], pieces)
        if npop < 2 and 2.0 < tf < d_final - 3.5 and not in_cut(tf, 0.8) and all(abs(tf - c["tf"]) > 1.0 for c in cues):
            cues.append({"tf": round(tf, 3), "kind": "pop"})
            npop += 1
    cta_t = d_final - 2.2
    cues.append({"tf": round(cta_t, 3), "kind": "hit"})
    cues.append({"tf": round(cta_t, 3), "kind": "riser", "ends": True})  # riser lands on the CTA card start
    cues.sort(key=lambda c: c["tf"])
    variants = {"hit": 2, "whoosh": 4, "pop": 3, "riser": 2}
    counters: dict[str, int] = {}
    for c in cues:
        n = counters.get(c["kind"], 0)
        c["variant"] = n % variants[c["kind"]]
        counters[c["kind"]] = n + 1

    hook_text = clean_hook((src.get("hook") or "").strip())
    if not hook_text:
        err("MP07", "hook text missing")
    hook_lines = split_hook(hook_text.upper())
    hook_key = max(hook_text.split(), key=lambda w: len(_clean(w))).upper()
    face = src.get("face") or {"x": 0.5, "y": 0.45, "h": 0.3}

    plan = {
        "version": PLAN_VERSION,
        "id": str(clip_id),
        "fps": 30,
        "width": 1080,
        "height": 1920,
        "source": {"file": raw_path, **(probe or {})},
        "speed": speed,
        "cut_duration": round(d_cut, 4),
        "final_duration": round(d_final, 4),
        "segments": [[round(a, 4), round(b, 4)] for a, b in segs],
        "pieces": pieces,
        "words": words,
        "zooms": zooms,
        "sfx": cues,
        "cutaways": cutaways,
        "emph_source": emph_source,
        "face": {"x": face["x"], "y": face["y"], "h": face["h"]},
        "hook": {"text": hook_text, "lines": hook_lines, "key": hook_key, "font_px": 104 if max(len(l) for l in hook_lines) <= 9 else (88 if max(len(l) for l in hook_lines) <= 12 else 78), "t_out_final": min(2.9, max(2.0, d_final * 0.12))},
        "cta": {"codeword": (codeword or "АГЕНТ").upper(), "line": "Напиши в комментариях", "dur_final": 2.2},
        "music": {"bed": int(clip_id) % 3 if str(clip_id).isdigit() else 0, "gain_db": -24.0, "duck_db": -9.0},
        "grade": {"brightness": 1.07, "contrast": 1.06, "saturate": 1.08},
        "caption": {"font_px": 54, "max_words": 4, "max_chars": 24, "emph_color": "#FFC83D", "chin_off": 0.05, "min_y": 0.5, "max_y": 0.84},
    }
    validate_plan(plan)
    return plan


def validate_plan(p: dict) -> None:
    if p.get("version") != PLAN_VERSION:
        err("MP01", "bad version")
    if not (MIN_SPEED <= p["speed"] <= MAX_SPEED):
        err("MP02", "speed out of range")
    d_cut = sum(x["src_e"] - x["src_s"] for x in p["pieces"])
    if abs(d_cut - p["cut_duration"]) > 0.05:
        err("MP08", f"pieces cover {d_cut}, cut_duration {p['cut_duration']}")
    d_fin = to_final(p["cut_duration"], p["pieces"])
    if abs(d_fin - p["final_duration"]) > 0.05:
        err("MP09", "final_duration mismatch")
    if not (15 <= p["final_duration"] <= 90):
        err("MP10", f"final_duration {p['final_duration']} outside 15..90")
    prev = -1.0
    for w in p["words"]:
        if w["s"] < prev - 1e-6 or w["e"] < w["s"]:
            err("MP11", f"word order broken at {w['w']}")
        prev = w["s"]
    if p["hook"]["t_out_final"] > 3.0:
        err("MP12", "hook longer than 3 s")


def probe_video(path: str) -> dict:
    import subprocess
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,r_frame_rate,duration", "-of", "json", path],
                         capture_output=True, text=True, check=True).stdout
    s = json.loads(out)["streams"][0]
    n, d = s["r_frame_rate"].split("/")
    return {"width": s["width"], "height": s["height"], "fps": round(int(n) / int(d), 3), "duration": float(s["duration"])}


def main(argv: list[str]) -> None:
    a = argv[1:]
    opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    src = json.loads(Path(a[0]).read_text(encoding="utf-8"))
    plan = build_plan(src, a[1], opt("--id", "0"), float(opt("--speed", 1.2)), opt("--codeword"),
                      ramps="--no-ramps" not in a, probe=probe_video(a[1]))
    Path(a[2]).write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"plan ok: cut {plan['cut_duration']}s -> final {plan['final_duration']}s, "
          f"{len(plan['pieces'])} pieces, {sum(w['emph'] for w in plan['words'])} emph, {len(plan['sfx'])} sfx")


if __name__ == "__main__":
    main(sys.argv)
