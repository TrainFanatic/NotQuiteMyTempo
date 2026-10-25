# hub/ — the team hub and the referee bridge

Owner: Aaron. Everything talks through one WebSocket hub; message formats are the
contract (see `fletcher/contract.py` until a root `CONTRACT.md` exists).

| file | role |
|---|---|
| `server.py` | the hub: broadcasts every frame to every other client (no echo, no handshake), records `recordings/<time>.jsonl`. Same code as `fletcher/dev/mini_hub.py` |
| `bridge.py` | Kevin's hits (`time_s  midi  name` on stdin or a FIFO) -> Chris's `LiveMatcher` (imported, unmodified) -> `session.state` + one `judgment.note` per hit |
| `e2e.py` | FIFO -> bridge -> hub -> Fletcher in one process; must print PASS |
| `tests/` | `python -m pytest hub/tests -q` (real fixture + Chris's drift simulator) |

## Run it

    python -m hub.server
    python -m fletcher
    python live_transcription/tools/transcribe_wav.py <take.wav> --model <model> --song-origin -<count-in s> --realtime \
        | python -m hub.bridge rushingOrDragging/out/<song>.drums.txt --bpm <bpm>

With a `<song>.tempo.json` next to the sheet (`rushingOrDragging/songsterr/tempo_map.py`) the bridge
takes bars, tempo changes and meter from it and `--bpm` is not needed.

## What the bridge sends

- `session.state` `playing` at the first hit and at every new bar (`bpm`, `bar`); `stopped` on
  `session.command stop` and at end of input; `count_in` on `restart`.
- `judgment.note` per hit: `error_ms` (+ = late, Chris's sign), `bar`/`beat` of the written note,
  `drum`, `grade` (perfect/good/off by Fletcher's bands), `song_time`. Sheet notes the drummer skipped
  arrive as `grade: "miss"`, hits with no sheet note as `grade: "extra"` (both `error_ms: null`).

Per-hit errors are what drive Fletcher's mood. `live_compare.py --plain` prints only verdict
*changes*, so a drummer who keeps dragging produces no output at all; that feed can't anger him.

## Known limits

- Restart alignment is provisional: it maps hub time to the transcriber's clock from hit arrival
  times minus `--latency-ms`, and assumes the count-in starts when `restart` arrives. It becomes exact
  once the count-off publishes the shared song start (`session.state count_in` with `t0`).
- Use the transcriber's line timestamps (Chris's `--clock line` equivalent); arrival times include
  ~170 ms of transcriber latency.
