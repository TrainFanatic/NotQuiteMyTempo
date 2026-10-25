# hub/ — the team hub and the referee bridge

Owner: Aaron. Everything talks through one WebSocket hub; message formats are the
contract: `CONTRACT.md` at the repo root (builders in `fletcher/contract.py`).

| file | role |
|---|---|
| `server.py` | the hub: broadcasts every frame to every other client (no echo, no handshake), records `recordings/<time>.jsonl`. Same code as `fletcher/dev/mini_hub.py` |
| `bridge.py` | Kevin's hits (`time_s  midi  name` on stdin or a FIFO) -> Chris's `LiveMatcher` (imported, unmodified) -> `session.state` + one `judgment.note` per hit |
| `session.py` | the session controller: song session state machine (start/quit from the headset or console, count_in t0 + `song_time_at_t0`, Fletcher's stop/restart, `finished` by the clock, `session.result`). `Session` is pure; tests in `tests/test_session.py` |
| `bridge.py --session` | `SessionBridge`: loads `songs/<song_id>/` on each controller count_in (runtime song switching); `--clock laptop` = input lines on the laptop clock (live mic), song time = t - t0 + song_time_at_t0 - `--input-latency-ms` |
| `app_e2e.py` | the whole app: `python -m nqmt --mic file:<sloppy>,<clean>` + the headless headset game (`-- --game`) + a scripted console; start -> count-off -> cutoff -> restart (seek) -> finished -> result; must print PASS |
| `tests/fixtures/songs/` | a fixture song library (Chris's 4-bar Back in Black x4); `tests/fixtures/make_songs.py` regenerates it |
| `e2e.py` | FIFO -> bridge -> hub -> Fletcher in one process; must print PASS |
| `full_e2e.py` | the whole system as separate processes, headset app included (below); must print PASS |
| `tests/` | `python -m pytest hub/tests -q` (real fixture + Chris's drift simulator; `test_score_gate.py`: onsets-only path, incl. kit audio through Kevin's transcriber) |

## Run it

The app: `python -m nqmt` (repo root `nqmt/`) starts everything below plus the library and
session controller; `python -m hub.app_e2e --godot <Godot 4.5>` tests it whole (~75 s).
The one-sheet path by hand:

    python -m hub.server
    python -m fletcher
    cd live_transcription && uv run python tools/transcribe_wav.py <take.wav> --onsets-only \
            --song-origin -<count-in s> --realtime \
        | ../.venv/bin/python -m hub.bridge ../rushingOrDragging/out/<song>.drums.txt --bpm <bpm>

No model ships, so the live path is `--onsets-only`: every line is `time_s  0  onset` (drum unknown,
`rushingOrDragging/file_format.md` §10) and the bridge's `ScoreGate` names it from the sheet. With a
model, swap `--onsets-only` for `--model <model>.bin`; known-drum lines skip the gate.

Score gate (unknown-drum lines only), causal, per sheet time-group (notes at the same time):
an onset takes the nearest unconsumed group within `--gate-window-ms` (100) of sheet time + drift
(an EMA of matched errors, weight `--gate-drift-alpha` 0.3; the plain sheet time is also in reach so
a fast swing back can't lock it out), and every note of that group is judged against it:
error = onset - the group's sheet time. Chris's LiveMatcher is not asked to re-search gated onsets
(its own drift median got pulled ~145 ms off by ride/splash triplets on `songs/bond_demo`, so clean
hits graded as misses); it still holds the bookkeeping, and known-drum lines go through it
unchanged. Groups the gate passes without an onset are the misses. Onsets
within `--gate-tail-ms` (60; 0.6 x the gap in faster runs) of the previous one, or matching nothing
within `--gate-ring-ms` (200) of a matched one (kick/crash re-triggers 50-90 ms into the decay; the
Quest mic's un-mute re-attack at 150-200 ms), are dropped; the rest are extras. Also dropped: a
re-trigger in the ring that is `--gate-jump-ms` (50) earlier than the stroke before it, and the
detected second stroke of a two-time group (32nd pairs, flams). An onset that takes its group more
than 30 ms early is held for one onset (or until the bridge's 50 ms tick says none can still come):
a nearer next onset takes the group instead. A cymbal/hat-only group 100 ms or less after a played
group gets no miss (buried in that stroke's decay; the onset detector can't hear it). After a
restart the beat before the bar is in the gate as silent decoys, so a pickup can't take the downbeat.
`tests/test_gate_judging.py` covers each case and bond_demo rendered clean and drifting.

With a `<song>.tempo.json` next to the sheet (`rushingOrDragging/songsterr/tempo_map.py`) the bridge
takes bars, tempo changes and meter from it and `--bpm` is not needed.

## Whole-system test (`full_e2e.py`)

    python -m hub.full_e2e --godot <Godot 4.5 binary>     # or GODOT=...; ~50 s, no API calls

Runs what the demo runs, each as its own process on free ports: `hub.server` (recording to a temp
dir), `python -m fletcher --dry-run`, Adam's app headless (`Godot --headless --path quest_app --
--fletcher ws://127.0.0.1:PORT --no-intro --fletcher-log --fletcher-quit-on-close`), and Kevin's
`transcribe_wav.py --onsets-only --realtime` (his uv env) piped into `hub.bridge`. The audio is
the sample kit playing a heavily drifting take (Chris's simulator, `--seed 5 --drift-sd 0.07`) of
`datacollection/tests/fixtures/back_in_black_4bars.drums.txt` looped to 16 bars.

It checks the hub recording against the app's `[fletcher-log]` lines (laptop clock): playing ->
judgments -> barks sound and their cued moves play -> stop, Stop bark, `t1_fist_cutoff` within
0.15 s of its cue -> tirade with gesture moves -> count-off finger on t0 - 5 beats -> restart and
the bridge's `count_in` carry the same t0 -> the four count-off words land on their beats
(0.1 s) -> no SCRIPT ERROR / unexpected ERROR in the app -> every process exits 0. It prints the
timeline and PASS/FAIL (exit code); logs and the recording stay in the printed temp dir on
FAIL (or with `--keep`). The take keeps playing after a restart, so later cutoffs are artifacts
of the canned audio; only cues due before shutdown are checked.

## What the bridge sends

- `session.state` `playing` at the first hit and at every new bar (`bpm`, `bar`); `stopped` on
  `session.command stop` and at end of input; `count_in` on `restart`.
- `judgment.note` per hit: `error_ms` (+ = late, Chris's sign), `bar`/`beat` of the written note,
  `drum`, `grade` (perfect/good/off by Fletcher's bands), `song_time`. Sheet notes the drummer skipped
  arrive as `grade: "miss"`, hits with no sheet note as `grade: "extra"` (both `error_ms: null`).

Per-hit errors are what drive Fletcher's mood. `live_compare.py --plain` prints only verdict
*changes*, so a drummer who keeps dragging produces no output at all; that feed can't anger him.

## Known limits

- Restart alignment: Fletcher's `restart` carries `t0` (its count-off downbeat, laptop clock), so the
  restart bar starts at t0 however late the command arrives; `count_in` echoes that t0. Without `t0`
  it assumes the count-in starts on arrival. Hub time -> transcriber time still comes from hit arrival
  times minus `--latency-ms`.
- Use the transcriber's line timestamps (Chris's `--clock line` equivalent); arrival times include
  ~170 ms of transcriber latency.
