"""Every bark has a body cue (fletcher/barks/cues.json) that Terence can actually play."""

import json
import unittest
from pathlib import Path

from fletcher.tirade import GESTURES

FLETCHER = Path(__file__).resolve().parents[1]
BARKS = FLETCHER / "barks"
ADAM_MOVES = FLETCHER.parent / "quest_app" / "assets" / "terence" / "terence_v004_moves.json"


class BarkCuesTest(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((BARKS / "manifest.json").read_text())
        self.cues = json.loads((BARKS / "cues.json").read_text())

    def test_every_clip_has_a_cue_in_range(self):
        for cid, m in self.manifest.items():
            with self.subTest(clip=cid):
                cues = self.cues.get(cid, {}).get("cues", [])
                self.assertGreaterEqual(len(cues), 1)
                for c in cues:
                    self.assertGreaterEqual(c["at_s"], -0.3)  # negative: the clip is held back (bark_lead_max_s)
                    self.assertLessEqual(c["at_s"], m["duration_s"])
                    self.assertIn(c.get("priority", 1), (0, 1, 2))

    def test_no_cues_for_unknown_clips(self):
        self.assertEqual(set(self.cues) - set(self.manifest), set())

    @unittest.skipUnless(ADAM_MOVES.exists(), "quest_app's Terence moves json not present")
    def test_gestures_resolve(self):
        moves = json.loads(ADAM_MOVES.read_text())["moves"]
        for cid, entry in self.cues.items():
            for c in entry["cues"]:
                with self.subTest(clip=cid, gesture=c["gesture"]):
                    self.assertTrue(c["gesture"] in moves or c["gesture"] in GESTURES)

    @unittest.skipUnless(ADAM_MOVES.exists(), "quest_app's Terence moves json not present")
    def test_key_frames_match_the_moves(self):
        """Each cue's key_frame is a frame of its move's key event, so its emphasis lands on it."""
        moves = json.loads(ADAM_MOVES.read_text())["moves"]
        for cid, entry in self.cues.items():
            for c in entry["cues"]:
                with self.subTest(clip=cid, gesture=c["gesture"]):
                    m = moves[c["gesture"]]
                    frames = [f for k, v in m.items() if k not in ("frames", "seconds", "loop", "hold_start", "release",
                                                                    "windup", "beat_frames", "spins", "distance_m")
                              for f in (v if isinstance(v, list) else [v]) if isinstance(f, int)]
                    self.assertIn(c["key_frame"], frames)
                    self.assertAlmostEqual(c["at_s"], c["emphasis_s"] - c["key_frame"] / 24, places=3)
        for cid in ("stop_1", "stop_2"):
            self.assertEqual(self.cues[cid]["cues"][0]["key_frame"], moves["t1_fist_cutoff"]["cutoff"])

    @unittest.skipUnless(ADAM_MOVES.exists(), "quest_app's Terence moves json not present")
    def test_countoff_gestures_resolve(self):
        moves = json.loads(ADAM_MOVES.read_text())["moves"]
        co = json.loads((FLETCHER / "countoff" / "cues.json").read_text())["count_in"]
        for c in co["cues"] + co["alternatives"]:
            self.assertIn(c["gesture"], moves)
        finger = co["cues"][0]
        self.assertEqual(finger["key_frames"], moves["t0_countoff_finger"]["finger_up"])
        self.assertEqual(finger["beats_before_t0"], 4 + finger["key_frames"][0] / 12)


if __name__ == "__main__":
    unittest.main()
