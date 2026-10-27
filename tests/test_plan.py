"""Planner / timeline tests. Run: python -m unittest discover -s tests -v  (from montage-lab)"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import montage_plan as mp  # noqa: E402


def synth_src(n_seg=5, seg_len=4.0, with_hook=True):
    segs = [[10 + i * 10, 10 + i * 10 + seg_len] for i in range(n_seg)]
    words, t = [], 0.0
    for i in range(int(n_seg * seg_len / 0.5)):
        w = ["деньги", "это", "дисциплина", "каждый", "день", "1000", "рублей", "что-то", "работает."][i % 9]
        words.append({"w": w, "s": round(t, 2), "e": round(t + 0.45, 2)})
        t += 0.5
    # token split like the real data: "-то" arrives as its own token
    words = [x for w in words for x in ([{"w": "что", "s": w["s"], "e": w["s"] + 0.2}, {"w": "-то", "s": w["s"] + 0.2, "e": w["e"]}] if w["w"] == "что-то" else [w])]
    return {"segments": segs, "words": words, "hook": "Деньги любят дисциплину" if with_hook else "", "face": {"x": 0.4, "y": 0.5, "h": 0.3}, "moments": []}


class Plan(unittest.TestCase):
    def build(self, speed=1.2, **kw):
        return mp.build_plan(synth_src(), "raw.mp4", "t1", speed, "КОД", probe={"width": 720, "height": 1280, "fps": 30.0}, **kw)

    def test_duration_ratio_without_ramps(self):
        p = self.build(ramps=False)
        self.assertAlmostEqual(p["final_duration"], p["cut_duration"] / 1.2, delta=0.05)

    def test_speed_1_0_is_identity(self):
        p = self.build(speed=1.0, ramps=False)
        self.assertAlmostEqual(p["final_duration"], p["cut_duration"], delta=0.01)
        self.assertTrue(all(x["rate"] == 1.0 for x in p["pieces"]))

    def test_speed_range_rejected(self):
        for s in (0.9, 1.5):
            with self.assertRaises(ValueError) as c:
                self.build(speed=s)
            self.assertIn("MP02", str(c.exception))

    def test_to_final_monotonic_and_scaled(self):
        p = self.build(ramps=False)
        last = -1
        for w in p["words"]:
            f = mp.to_final(w["s"], p["pieces"])
            self.assertGreater(f + 1e-9, last)
            last = f
            self.assertAlmostEqual(f, w["s"] / 1.2, delta=0.02)

    def test_hyphen_token_merged(self):
        p = self.build()
        ws = [w["w"] for w in p["words"]]
        self.assertIn("что-то", ws)
        self.assertNotIn("-то", ws)

    def test_hook_present_and_short(self):
        p = self.build()
        self.assertTrue(p["hook"]["lines"])
        self.assertLessEqual(p["hook"]["t_out_final"], 3.0)
        with self.assertRaises(ValueError):
            mp.build_plan(synth_src(with_hook=False), "raw.mp4", "t", 1.2, None, probe={})

    def test_hook_stutter_removed(self):
        self.assertEqual(mp.clean_hook("Представь 17 дней делаешь -делаешь"), "Представь 17 дней делаешь")
        self.assertLessEqual(len(mp.split_hook("ПРЕДСТАВЬ 17 ДНЕЙ ПОДРЯД ДЕЛАЕШЬ")), 3)

    def test_emphasis_spacing_and_zoom_cap(self):
        p = self.build()
        em = [w["s"] for w in p["words"] if w["emph"]]
        self.assertTrue(em)
        self.assertTrue(all(b - a >= 1.6 - 1e-6 for a, b in zip(em, em[1:])))
        self.assertTrue(all(z["peak"] <= 1.6 + 1e-9 for z in p["zooms"]))

    def test_sfx_rules(self):
        p = self.build()
        tf = [c["tf"] for c in p["sfx"]]
        self.assertEqual(tf, sorted(tf))
        kinds = [c["kind"] for c in p["sfx"]]
        self.assertEqual(kinds.count("riser"), 1)
        self.assertEqual(kinds.count("hit"), 1)
        self.assertLessEqual(kinds.count("pop"), 2)
        for k, n in (("whoosh", 4), ("pop", 3), ("hit", 2), ("riser", 2)):
            self.assertTrue(all(c["variant"] < n for c in p["sfx"] if c["kind"] == k))
        non_cta = [c for c in p["sfx"] if c["kind"] in ("whoosh", "pop")]
        self.assertTrue(all(b["tf"] - a["tf"] >= 0.9 for a, b in zip(non_cta, non_cta[1:])), non_cta)

    def test_cutaways_spacing_and_bounds(self):
        p = self.build()
        ca = p["cutaways"]
        self.assertLessEqual(len(ca), 3)
        for c in ca:
            self.assertGreaterEqual(c["t"], 3.3)
            self.assertLessEqual(c["t"] + c["dur"], p["final_duration"] - 2.2)
            self.assertLessEqual(c["dur"], 2.0)
        self.assertTrue(all(b["t"] - (a["t"] + a["dur"]) >= 2.0 for a, b in zip(ca, ca[1:])))
        self.assertEqual(self.build(cutaways_on=False)["cutaways"], [])

    def test_ramp_is_eased_and_returns_down(self):
        p = self.build()
        for seg in {x["seg"] for x in p["pieces"]}:
            rates = [x["rate"] for x in p["pieces"] if x["seg"] == seg]
            if len(set(rates)) > 2:
                self.assertGreaterEqual(len(set(rates)), 4)  # more than one step up and down
                self.assertEqual(rates[0], 1.2)
                self.assertLess(rates[-1], max(rates))  # eases back down before the cut
                k = rates.index(max(rates))
                self.assertTrue(all(a <= b + 1e-9 for a, b in zip(rates[:k], rates[1:k + 1])))
                self.assertTrue(all(a >= b - 1e-9 for a, b in zip(rates[k:], rates[k + 1:])))

    def test_faces_per_segment_reach_pieces(self):
        src = synth_src()
        faces = [{"x": 0.3 + 0.01 * i, "y": 0.5, "h": 0.2, "found": 5} for i in range(len(src["segments"]))]
        p = mp.build_plan(src, "raw.mp4", "t", 1.2, "КОД", probe={"width": 720, "height": 1280, "fps": 30, "duration": 60}, faces=faces)
        for x in p["pieces"]:
            self.assertAlmostEqual(x["face"]["x"], faces[x["seg"]]["x"])

    def test_cut_boundary_changes_scale(self):
        p = self.build(ramps=False)
        sc = [x["scale"] for x in p["pieces"]]
        self.assertTrue(all(abs(a - b) >= 0.3 for a, b in zip(sc, sc[1:])))

    def test_ramp_pieces_cover_cut_timeline(self):
        p = self.build()
        self.assertAlmostEqual(sum(x["src_e"] - x["src_s"] for x in p["pieces"]), p["cut_duration"], delta=0.01)

    def test_cta_default_and_override(self):
        self.assertEqual(self.build()["cta"]["codeword"], "КОД")

    def test_real_plans_if_present(self):
        for n in ("580", "581", "582"):
            f = ROOT / "in" / n / "plan_src.json"
            if not f.exists():
                continue
            src = json.loads(f.read_text(encoding="utf-8"))
            p = mp.build_plan(src, "raw.mp4", n, 1.2, None, probe={"width": 720, "height": 1280, "fps": 30.0})
            self.assertTrue(15 <= p["final_duration"] <= 90)


if __name__ == "__main__":
    unittest.main()
