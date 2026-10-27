"""One clip end to end: plan -> (memory gate) -> Remotion video -> audio -> mux -> ffprobe + frames.
One render at a time, Remotion concurrency 1. Waits (never aborts) when free RAM is low.
Usage: python run_clip.py <clip_id> <intake_plan.json> <raw.mp4> [--codeword X] [--min-free-mb 900] [--no-llm] [--no-cutaways] [--stall-s 600] [--plan-only]
No-progress watchdog: if the render prints nothing for --stall-s seconds (default 600) its process tree is killed by exact PID and the attempt is retried.
Everything the run needs is a local file, so the same script runs on a CI runner after fetching inputs.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import audio_mix  # noqa: E402
import emphasis_llm  # noqa: E402
import face_track  # noqa: E402
import montage_plan  # noqa: E402


def log(tag: str, *a):
    print(f"[task-{tag}]", *a, flush=True)


def wait_for_ram(tag: str, min_mb: int) -> int:
    while True:
        av = psutil.virtual_memory().available // 2**20
        if av >= min_mb:
            return av
        log(tag, f"low RAM {av} MB < {min_mb} MB, waiting 5 min")
        time.sleep(300)


class PeakMeter(threading.Thread):
    """Samples RSS of the whole process tree of `proc` plus the lowest system-available RAM."""

    def __init__(self, proc: subprocess.Popen):
        super().__init__(daemon=True)
        self.proc, self.peak_rss, self.min_avail = proc, 0, 10**9
        self.stop = False

    def run(self):
        try:
            root = psutil.Process(self.proc.pid)
        except psutil.Error:
            return
        while not self.stop:
            try:
                procs = [root] + root.children(recursive=True)
                rss = sum(p.memory_info().rss for p in procs if p.is_running())
                self.peak_rss = max(self.peak_rss, rss)
            except psutil.Error:
                pass
            self.min_avail = min(self.min_avail, psutil.virtual_memory().available)
            time.sleep(1.0)


def kill_tree(proc: subprocess.Popen) -> list[int]:
    """Kill `proc` and its descendants by exact PID (never by image name: other sessions run node/ffmpeg too)."""
    killed = []
    try:
        root = psutil.Process(proc.pid)
        victims = root.children(recursive=True) + [root]
    except psutil.Error:
        return killed
    for p in victims:
        try:
            p.kill()
            killed.append(p.pid)
        except psutil.Error:
            pass
    return killed


def sh(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


def main() -> None:
    a = sys.argv[1:]
    tag, src_plan, raw = a[0], a[1], a[2]
    opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    min_mb = int(opt("--min-free-mb", 900))
    stall_s = int(opt("--stall-s", 600))
    max_attempts = int(opt("--max-attempts", 0))  # 0 = retry forever (local default); CI passes a cap
    speed = float(opt("--speed", 1.2))
    out_dir = ROOT / "out" / tag
    out_dir.mkdir(parents=True, exist_ok=True)
    t_all = time.time()

    src = json.loads(Path(src_plan).read_text(encoding="utf-8"))
    segs = [(float(x), float(y)) for x, y in src["segments"]]
    faces_cache = ROOT / "plans" / f"{tag}.faces.json"
    if faces_cache.exists():
        faces = json.loads(faces_cache.read_text(encoding="utf-8"))
    else:
        faces = face_track.face_for_segments(str(Path(raw).resolve()), segs, src["face"])
        faces = [{k: (float(v) if k != "found" else int(v)) for k, v in f.items()} for f in faces]
        faces_cache.parent.mkdir(exist_ok=True)
        faces_cache.write_text(json.dumps(faces), encoding="utf-8")
    merged = montage_plan.merge_tokens(src["words"])
    emph_idx = src.get("emph") or (None if "--no-llm" in a else emphasis_llm.pick(tag, [w["w"] for w in merged], src.get("hook", "")))
    log(tag, f"emphasis: {'model ' + str(len(emph_idx)) + ' words' if emph_idx else 'heuristic'}; faces found {[f['found'] for f in faces]}")
    plan = montage_plan.build_plan(src, str(Path(raw).resolve()), tag, speed, opt("--codeword", src.get("codeword")),
                                   ramps="--no-ramps" not in a, probe=montage_plan.probe_video(raw),
                                   faces=faces, emph_idx=emph_idx, cutaways_on="--no-cutaways" not in a)
    plan_path = ROOT / "plans" / f"{tag}.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    log(tag, f"plan: cut {plan['cut_duration']}s -> final {plan['final_duration']}s")

    if "--plan-only" in a:
        return
    video = out_dir / f"{tag}_video.mp4"
    attempts = 0
    while True:
        attempts += 1
        avail = wait_for_ram(tag, min_mb)
        log(tag, f"RAM ok: {avail} MB available, starting render (attempt {attempts})")
        t0 = time.time()
        proc = subprocess.Popen(["node", str(HERE / "render.mjs"), str(plan_path), str(Path(raw).resolve()), str(video)],
                                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        meter = PeakMeter(proc)
        meter.start()
        tail: list[str] = []
        state = {"last": time.time()}

        def pump():
            for line in proc.stdout:
                state["last"] = time.time()
                tail.append(line.rstrip())
                if "progress" in line or "done" in line:
                    print(line.rstrip(), flush=True)

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        stalled = False
        while proc.poll() is None:
            time.sleep(2)
            if time.time() - state["last"] > stall_s:
                stalled = True
                log(tag, f"WATCHDOG: no render output for {stall_s} s, killing PIDs {kill_tree(proc)}")
                break
        rc = proc.wait()
        reader.join(timeout=5)
        meter.stop = True
        if stalled:
            rc = rc or -9
        render_s = time.time() - t0
        if rc == 0:
            break
        (ROOT / "logs").mkdir(exist_ok=True)
        (ROOT / "logs" / f"{tag}_render_err_{attempts}.log").write_text(chr(10).join(tail), encoding="utf-8")
        log(tag, f"render FAILED rc {rc} (full output in logs/{tag}_render_err_{attempts}.log)")
        if os.environ.get("MONTAGE_DEBUG") == "1":
            print(chr(10).join(tail[-12:]), flush=True)
        if max_attempts and attempts >= max_attempts:
            log(tag, f"giving up after {attempts} attempts")
            sys.exit(2)
        log(tag, "retry in 60 s")
        time.sleep(60)  # never abort; transient failures under memory pressure are retried

    wav = out_dir / f"{tag}_audio.wav"
    t1 = time.time()
    voice = str(wav) + ".voice.wav"
    audio_mix.build_voice(plan, plan["source"]["file"], voice)
    ainfo = audio_mix.build_mix(plan, voice, str(wav))
    Path(voice).unlink(missing_ok=True)
    final = out_dir / f"{tag}_code_layer_v2.mp4"
    r = sh(["ffmpeg", "-y", "-hide_banner", "-i", str(video), "-i", str(wav), "-map", "0:v", "-map", "1:a",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(final)])
    if r.returncode != 0:
        log(tag, "mux FAILED", r.stderr[-400:])
        sys.exit(1)
    pr = json.loads(sh(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height,r_frame_rate,duration,pix_fmt",
                        "-show_entries", "format=duration,size", "-of", "json", str(final)]).stdout)
    fdur = float(pr["format"]["duration"])
    res = {
        "id": tag, "final": str(final), "expected_dur": plan["final_duration"], "dur": round(fdur, 3),
        "dur_ok": abs(fdur - plan["final_duration"]) <= 0.1,
        "render_s": round(render_s, 1), "audio_s": round(time.time() - t1, 1), "wall_s": round(time.time() - t_all, 1),
        "peak_tree_rss_mb": round(meter.peak_rss / 2**20), "min_system_avail_mb": round(meter.min_avail / 2**20),
        "size_mb": round(int(pr["format"]["size"]) / 2**20, 1), "audio": ainfo, "ffprobe": pr,
    }
    (out_dir / f"{tag}_result.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    log(tag, f"done: {res['dur']}s (expected {res['expected_dur']}), render {res['render_s']}s, peak tree RSS {res['peak_tree_rss_mb']} MB")


if __name__ == "__main__":
    main()
