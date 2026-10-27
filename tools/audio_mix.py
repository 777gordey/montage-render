"""Audio for the montage, built from the same plan as the video.

voice: atrim per piece -> atempo(rate) (pitch preserved) -> concat -> highpass
mix:   + SFX at final-timeline cues + music bed ducked under speech (sidechain)
last:  two-pass loudnorm I=-14 TP=-1.5 LRA=9 on the sped-up mix, then fades.
Usage: python audio_mix.py <plan.json> <out.wav>
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from montage_plan import to_final  # noqa: E402

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "real"  # Mixkit packs, see assets/real/LICENSES.md
# target mean level of each SFX kind relative to the voice mean (dB): real files differ by 10 dB, so each is levelled by its own mean
SFX_REL = {"hit": -6.0, "whoosh": -9.0, "pop": -10.0, "riser": -11.0}
_dur_cache: dict = {}


def wav_dur(path) -> float:
    if path not in _dur_cache:
        _dur_cache[path] = float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], out="stdout").strip())
    return _dur_cache[path]


def run(args: list[str], out: str = "stderr") -> str:
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {p.stderr[-600:]}")
    return p.stdout if out == "stdout" else p.stderr


def mean_volume(path: str) -> float:
    err = run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af", "volumedetect", "-f", "null", "-"])
    m = re.search(r"mean_volume: (-?[\d.]+) dB", err)
    return float(m.group(1)) if m else -24.0


def build_voice(plan: dict, raw: str, out: str) -> None:
    parts, labels = [], []
    for i, p in enumerate(plan["pieces"]):
        parts.append(f"[0:a]atrim={p['src_s']:.4f}:{p['src_e']:.4f},asetpts=PTS-STARTPTS,atempo={p['rate']:.4f}[a{i}]")
        labels.append(f"[a{i}]")
    parts.append("".join(labels) + f"concat=n={len(labels)}:v=0:a=1,highpass=f=80,aresample=48000[v]")
    run(["ffmpeg", "-y", "-hide_banner", "-i", raw, "-filter_complex", ";".join(parts), "-map", "[v]", "-ac", "1", out])


def build_mix(plan: dict, voice: str, out: str, music_path: str | None = None) -> dict:
    d_fin = plan["final_duration"]
    ins = ["-i", voice]
    fc = []
    n = 1
    sfx_labels = []
    vdb = mean_volume(voice)
    for c in plan["sfx"]:
        f = ASSETS / f"{c['kind']}_{c['variant']}.wav"
        ins += ["-i", str(f)]
        t0 = c["tf"] - (wav_dur(f) if c.get("ends") else 0.0)  # `ends`: the sound finishes at tf (riser into the CTA)
        ms = max(0, int(round(t0 * 1000)))
        g = vdb + SFX_REL[c["kind"]] - mean_volume(str(f))
        fc.append(f"[{n}:a]volume={g:.2f}dB,adelay={ms}:all=1[s{n}]")
        sfx_labels.append(f"[s{n}]")
        n += 1
    music_path = music_path or str(ASSETS / f"bed_{plan['music'].get('bed', 0)}.wav")
    ins += ["-stream_loop", "-1", "-i", music_path]
    mi = n
    mdb = vdb + plan["music"]["gain_db"] - (-20.0)  # bed.wav is normalised to -20 dBFS RMS
    fc.append("[0:a]asplit=2[vm][vs]")
    fc.append(f"[{mi}:a]atrim=0:{d_fin:.3f},asetpts=PTS-STARTPTS,volume={mdb:.2f}dB[mus]")
    fc.append("[mus][vs]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=350:makeup=1[duck]")
    fc.append("".join(sfx_labels) + f"amix=inputs={len(sfx_labels)}:normalize=0,apad[sfxbus]")
    fc.append(f"[vm][duck][sfxbus]amix=inputs=3:normalize=0:duration=first,atrim=0:{d_fin:.3f},asetpts=PTS-STARTPTS[mix]")
    run(["ffmpeg", "-y", "-hide_banner", *ins, "-filter_complex", ";".join(fc), "-map", "[mix]", "-ac", "1", "-ar", "48000", out + ".pre.wav"])
    # two-pass loudnorm, measured AFTER speed + mix
    err = run(["ffmpeg", "-hide_banner", "-nostats", "-i", out + ".pre.wav", "-af", "loudnorm=I=-14:TP=-1.5:LRA=9:print_format=json", "-f", "null", "-"])
    m = json.loads(re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", err, re.S).group(0))
    ln = (f"loudnorm=I=-14:TP=-1.5:LRA=9:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    fade = f"afade=t=in:d=0.04,afade=t=out:st={d_fin - 0.35:.3f}:d=0.35"
    run(["ffmpeg", "-y", "-hide_banner", "-i", out + ".pre.wav", "-af", f"{ln},{fade},aresample=48000", "-ar", "48000", out])
    Path(out + ".pre.wav").unlink(missing_ok=True)
    return {"voice_mean_db": vdb, "music_gain_db": mdb, "n_sfx": len(sfx_labels)}


def main() -> None:
    plan = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out = sys.argv[2]
    voice = out + ".voice.wav"
    build_voice(plan, plan["source"]["file"], voice)
    info = build_mix(plan, voice, out)
    Path(voice).unlink(missing_ok=True)
    print("audio ok", info)


if __name__ == "__main__":
    main()
