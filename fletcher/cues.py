"""Terence's choreography around voice lines, as fletcher.state body cues
(optional `gesture`, `utterance_id`, one anchor `at_index`/`at_s`/`after_s`/`t0`, `priority`).

- Barks: cues per clip from barks/cues.json, else a per-category default. While the band
  plays they stay priority 1 and never walk or close in. `at_s` may be negative: the move
  must start before the word, so the headset holds the clip back (at most bark_lead_max_s).
- Tirade gestures resolve to Terence move ids here. Without close-ups (the default) every
  one is a move he plays where he stands; close-ups would walk him ~4 s each way.
- After a tirade: a beat of stare, the restart instruction (only if it fits), then the
  count-off. It runs on ONE clock: the cue carries `t0` (laptop monotonic, the downbeat of the
  restart bar) and `bpm`; the headset plays the finger move (from t0 - 5 beats) and the
  count-off words (onsets on beats -4..-1) from it, and main.py sends session.command restart
  at t0 - count_in_beats beats (the bridge starts the bar count_in_beats after the command).
Move lengths and key frames are Adam's: read from quest_app/assets/terence/terence_v004_moves.json
(24 fps, authored at 120 bpm) when the repo has it, else the v004 table below.
- Holds (v004): the t1_* accents (fist cutoff, fist slam, flick, wave off, clap stop) freeze
  their pose from hold_start to release. The headset lets the next move take over at the
  release frame, so `busy_s` (what spacing uses) is the release, not the move's end. The
  cutoff fist lasts 72 frames (3 s at 120 bpm) but releases at 50 (2.08 s): the tirade starts
  during the hold (speak_at doesn't wait for the fist: the glare under his first words is the
  look we want), and the line's first gesture cue plays at the release."""

from __future__ import annotations

import json
from pathlib import Path

from .barks import BARK_DIR

CUES_FILE = BARK_DIR / "cues.json"
MOVES_JSON = Path(__file__).resolve().parents[1] / "quest_app" / "assets" / "terence" / "terence_v004_moves.json"
FPS, AUTHORED_BPM, DEFAULT_BPM = 24.0, 120.0, 120.0
# Fallback (terence_v004) when MOVES_JSON is absent: frames, and hold release where there is one.
_V004 = {"t1_fist_cutoff": (72, 50), "t2_point_player": (36, None), "t2_score_point": (36, None),
         "t0_cue_section": (36, None), "t1_flick": (28, 20), "t1_wave_off": (38, 28),
         "t0_countoff_finger": (60, None), "ap_nod": (24, None), "t1_fist_slam": (38, 26),
         "t2_impatient_watch": (48, None), "t4_shout": (48, None), "t0_check_watch": (48, None),
         "t1_clap_stop": (32, 23), "t0_clap_off": (60, None), "t2_forced_calm": (48, None), "t4_charge": (30, None)}


def _load_moves() -> dict[str, dict]:
    try:
        moves = json.loads(MOVES_JSON.read_text())["moves"]
        if isinstance(moves, dict) and moves:
            return moves
    except (OSError, ValueError, KeyError, TypeError):
        pass
    out: dict[str, dict] = {k: {"frames": f} for k, (f, _) in _V004.items()}
    for k, (_, rel) in _V004.items():
        if rel is not None:
            out[k].update(hold_start=0, release=rel)
    out["t0_countoff_finger"]["finger_up"] = [12, 24, 36, 48]
    return out


MOVES = _load_moves()
FRAMES = {k: int(m["frames"]) for k, m in MOVES.items() if "frames" in m}
# Held one-shots: the frame the hold ends ("release" only counts with a "hold_start": in the
# throws it means letting go of the prop).
RELEASE = {k: int(m["release"]) for k, m in MOVES.items() if "hold_start" in m and "release" in m}
FIRST_FINGER = int((MOVES.get("t0_countoff_finger", {}).get("finger_up") or [12])[0])  # one finger per beat
# The finger move starts this many beats before t0: finger 1 ("five") is up on beat -4.
COUNTOFF_LEAD_BEATS = 4.0 + FIRST_FINGER / FPS * AUTHORED_BPM / 60.0
STARE, FIST, COUNTOFF = "t2_judging_stare", "t1_fist_cutoff", "t0_countoff_finger"
CLOSE_UP = {"t3_loom", "t3_whisper", "t3_crouch", "t3_circle_kit", "t3_clap_in_face", "t3_fake_warmth",
            "t5_rhythm_slaps", "t5_cowbell",
            "t4_charge", "t4_storm_out", "t0_pace", "walk", "in_face", "storm_over", "kick_stand", "throw_chair"}
# Terence walks to the drummer for these (his terence.gd CLOSE_MOVES + t3_circle_kit).
WALKS = {"t3_loom", "t3_whisper", "t3_crouch", "t3_circle_kit", "t3_clap_in_face", "t3_fake_warmth",
         "t5_rhythm_slaps", "t5_cowbell"}
# ...and what he does instead, where he stands (same feel, a visible key event).
STATIONARY = {"t3_loom": "t2_point_player", "t3_whisper": "t2_forced_calm", "t3_crouch": "t0_lean_listen",
              "t3_circle_kit": "t2_hunt_sections", "t3_clap_in_face": "t0_clap_off",
              "t3_fake_warmth": "t2_forced_calm", "t5_rhythm_slaps": "t0_clap_off", "t5_cowbell": "t0_clap_off"}
# tirade.GESTURES name -> Terence move (same table as fletcher_link.gd, before STATIONARY).
GESTURE_MOVES = {"slam_stand": "t1_fist_slam", "throw_music": "t4_throw_music", "kick_stand": "t4_sweep_stand",
                 "throw_chair": "t5_chair_throw", "storm_over": "t4_charge", "in_face": "t3_loom",
                 "point": "t2_point_player", "beat_time": "t5_rhythm_slaps", "grab_cymbal": "t5_cymbal_throw",
                 "turn_away": "t1_wave_off", "spin_back": "t2_point_player", "throw_hands_up": "t4_shout",
                 "slow_clap": "t3_clap_in_face"}
RESTART_MOVE_AT_S = 0.4  # the restart instruction starts this long into the post-line stare
WALK_IN = "t3_loom"  # close-ups on: sent with the Stop bark so he is at the kit for the line
MOVE_ACTION = {"t0_conduct": "conduct", STARE: "glare", "t3_loom": "lean_in", "t2_point_player": "point",
               FIST: "fist_cutoff", "t4_charge": "walk_toward", "ap_nod": "nod", "t1_clap_stop": "slow_clap",
               COUNTOFF: "conduct", "t2_score_point": "point", "t0_cue_section": "conduct"}
BARK_DEFAULTS = {  # category -> [(gesture, at_s, priority)]
    "rushing": [("t1_wave_off", 0.0, 1)], "dragging": [("t1_flick", 0.0, 1)],
    "no": [("t1_wave_off", 0.0, 1)], "watch": [("t0_cue_section", 0.0, 1)],
    "stop": [(FIST, 0.0, 2)], "again": [("t2_point_player", 0.0, 1)], "top": [("t0_cue_section", 0.0, 1)],
    "praise": [("ap_nod", 0.0, 1)], "count": [(COUNTOFF, 0.0, 2)], "bar": [("t2_score_point", 0.0, 1)],
}


def gesture_move(name: str, closeups: bool = False) -> str:
    """A tirade gesture name (or a move id) -> the Terence move to send."""
    m = GESTURE_MOVES.get(name, name)
    return m if closeups else STATIONARY.get(m, m)


def bark_lead(at_s: list[float], cap: float) -> float:
    """How long the headset holds a bark back so its earliest move can start first."""
    return round(min(cap, max([0.0] + [-a for a in at_s])), 3)


def move_s(move: str, bpm: float | None, frames: int | None = None) -> float:
    """Seconds into `move` (its whole length by default) at the song tempo."""
    f = FRAMES.get(move, 36) if frames is None else frames
    return f / FPS * AUTHORED_BPM / (bpm or DEFAULT_BPM)


def busy_s(move: str, bpm: float | None) -> float:
    """How long `move` keeps the next one waiting: to its hold's release if it has one."""
    return move_s(move, bpm, RELEASE.get(move))


class BarkCues:
    def __init__(self, path: Path | None = None):
        self.table: dict[str, dict] = {}
        p = path or CUES_FILE
        if p.exists():
            try:
                self.table = json.loads(p.read_text())
            except ValueError:
                self.table = {}

    def get(self, clip_id: str, category: str, playing: bool, bpm: float | None = None) -> list[tuple[str, float, int]]:
        """[(gesture, at_s, priority)]. Moves scale with tempo, the clip doesn't: the move
        starts so its key frame lands on the word it emphasises (file at_s is for 120 bpm)."""
        raw = (self.table.get(clip_id) or {}).get("cues") or []
        cues = [(str(c["gesture"]), _at_s(c, bpm), int(c.get("priority", 1)))
                for c in raw if isinstance(c, dict) and c.get("gesture")]
        if playing:
            cues = [(g, a, min(p, 1)) for g, a, p in cues if g not in CLOSE_UP]
        if not cues:
            cues = [(g, a, min(p, 1) if playing else p) for g, a, p in BARK_DEFAULTS.get(category, [])]
        return _spaced(sorted(cues, key=lambda c: c[1]), bpm)


    def fist_at_s(self, clip_id: str, bpm: float | None) -> float:
        """at_s of the cutoff fist on a Stop clip: its cutoff frame on the word (may be negative)."""
        for c in (self.table.get(clip_id) or {}).get("cues") or []:
            if isinstance(c, dict) and c.get("gesture") == FIST:
                return _at_s(c, bpm)
        return 0.0


def _spaced(cues: list[tuple[str, float, int]], bpm: float | None) -> list[tuple[str, float, int]]:
    """Drop a cue that would start while the previous one still plays at this tempo (it would
    wait for it and land late); the earlier cue wins."""
    out: list[tuple[str, float, int]] = []
    for g, a, p in cues:
        if out and a < out[-1][1] + busy_s(out[-1][0], bpm) - 0.05:
            continue
        out.append((g, a, p))
    return out


def _at_s(c: dict, bpm: float | None) -> float:
    """Key frame on the emphasis at the song tempo. May be negative: the move has to start
    before the clip (the headset delays the clip by up to bark_lead_max_s)."""
    if c.get("key_frame") is None or c.get("emphasis_s") is None:
        return float(c.get("at_s", 0.0))
    return round(float(c["emphasis_s"]) - move_s(str(c["gesture"]), bpm, int(c["key_frame"])), 3)


def post_line(bpm: float | None, stare_s: float, restart_move: str | None) -> list[tuple[str, str, float, int]]:
    """Cues after a line, before the count-off finger starts `stare_s` after it ends:
    [(gesture, anchor, value, priority)]: a stare, and the restart instruction if it fits."""
    out = [(STARE, "after_s", 0.0, 1)]
    if restart_move and RESTART_MOVE_AT_S + busy_s(restart_move, bpm) <= stare_s + 1e-9:
        out.append((restart_move, "after_s", RESTART_MOVE_AT_S, 1))
    return out


def countoff_t0(line_end: float, bpm: float | None, stare_s: float) -> float:
    """The restart bar's downbeat: the finger move starts stare_s after the line, five beats
    before t0 (finger 1 = "five" on beat -4)."""
    return line_end + stare_s + COUNTOFF_LEAD_BEATS * 60.0 / (bpm or DEFAULT_BPM)
