# CONTRACT.md: hub messages as the code sends and reads them (2026-10-24)

This file documents what the code does today. It is not a wish list. To change a format, change
this file first and get the team to agree. Adding an OPTIONAL field is fine, but post it in team chat.
Builders/parsers: `fletcher/contract.py`. Name tables: `MOODS`, `ACTIONS`, `LOOK_AT` there.

## Transport and envelope

`hub/server.py` (same code as `fletcher/dev/mini_hub.py`) is a WebSocket on port 8765. It sends
every frame to every OTHER client: there is no echo and no handshake. It logs each frame to
`recordings/<time>.jsonl` (turn that off with `--no-record`). Each frame is one JSON object:

| field | type | meaning |
|---|---|---|
| `type` | str, required | message type (below). Frames without a string `type` are dropped (`contract.parse`) |
| `src` | str | sender: `aaron` (Fletcher), `chris` (bridge), `quest` (headset). `HubClient` drops frames with its own `src` |
| `seq` | int | per-sender counter, starting at 1 |
| `t` | float | send time on the sender's clock. Python senders use the laptop clock (below) |
| `data` | object | payload. A missing or non-object `data` becomes `{}` |

Receivers ignore types and fields they don't know. `_rx` (receive time, laptop clock) is added
locally by `HubClient` and is never sent.

**The clock.** There is one clock: laptop `time.monotonic()` seconds (`contract.now()`). Every `t`, `t0`
and `_rx` is on it. The bridge and Fletcher run on the same laptop, so they share it. The headset
estimates `clock_offset = min(local - t)` over every frame it receives and maps laptop times with
`FletcherHub.to_local_time(t)` (`fletcher/godot/fletcher_hub.gd`). The headset (src `quest`, FletcherHub's default) sends
`session.command start/quit`, `session.request`, `library.request` and, opt-in, `mic.pcm`.

Senders (`src`) since the e2e app: `session` (hub/session.py, the session controller), `library`
(library/server.py), `console` (the laptop console page, and hub/app_e2e.py), `quest` (the
headset game), `file_mic` and `nqmt` (the launcher's file mic and settings follower; they only listen).

## session.state: session controller + bridge -> Fletcher, headset, bridge

With the session controller running (`python -m nqmt`), it is the ONE publisher of `count_in`,
`finished` and `idle`; the bridge (`--session`) publishes only `playing` and `stopped`. Without
it (the old one-sheet bridge, hub/e2e.py, hub/full_e2e.py) the bridge sends `count_in` on restart
as below.

| field | req | values / meaning |
|---|---|---|
| `state` (controller) | yes | `idle` (startup, after `quit`), `count_in` (a song start, or Fletcher's restart), `finished` (song time past the last bar + `ring_s` 1.5 s while not stopped). The controller tracks `playing`/`stopped` internally from the clock, the bridge and Fletcher's stop |
| `reason` | controller `count_in` | `start` (a new song: Fletcher counts the drummer in and resets his mood) or `restart` (answers Fletcher's restart; he already counted off) |
| `song_id`, `title`, `bars`, `beats_per_bar` | controller always while a song is loaded | the song (`songs/<song_id>/song.json`) |
| `from_bar` | controller `count_in` | the bar that starts at `t0` (also in `bar`) |
| `t0` | controller `count_in` | laptop clock, downbeat of `from_bar`. Start: now + 5 beats + 1.5 s + `--lead-s` (1 s) so the headset can fetch and schedule the song. Restart: Fletcher's `t0` (else arrival + 4 beats) |
| `song_time_at_t0` | controller `count_in` | s, tempo-map start of `from_bar` (song time 0 = bar 1 downbeat). The headset plays backing-track time `song_time_at_t0` at `t0`; the bridge expects bar `from_bar` there. Song time at laptop t = t - t0 + song_time_at_t0 |
| `error` | opt | controller: a refused `start` (unknown song); `state` stays what it was |
| `state` | yes | bridge sends `playing` (first hit, then every new bar), `stopped` (on `stop` and at end of input), `count_in` (on `restart`). Fletcher's brain also accepts `idle` (reset) and `calibrating` |
| `song_id` | bridge always | sheet file stem |
| `bpm` | bridge always | float. Brain: tempo. Headset: `set_tempo` if >= 40 |
| `bar`, `beat` | bridge always | current bar (1-based), `beat` is always 1 |
| `t0` | opt | **bridge, on `count_in` only:** the downbeat of bar `bar` (laptop clock). This is the restart command's `t0` when one was sent, else the command's arrival time + `count_in_beats` beats. `fletcher/dev/mock_referee.py` sends `t0` on every state, but on `playing`/`stopped` it means the virtual downbeat of bar 1 |
| `restart_count` | opt | mock referee only |

Odd meters (7/4 Whiplash) and tempo changes: the count-off counts BEATS, not bars. Fletcher's
finger starts 5 beats and the words "five six seven eight" land on beats -4..-1 before t0, at the
`bpm` of `from_bar` (quarter-note bpm, the tempo map's). In 7/4 that is the last 4 beats of a
virtual count-in bar, and t0 is still the downbeat. Bars and song times always come from the
tempo map (`song_time_at_t0`), never from bpm x beats.

`session.request` `{}` (any client, e.g. the headset on connect): the controller repeats its last
`session.state`.

## session.result: controller -> headset, console, Fletcher

Once per song: at `finished` (0.6 s later, after the bridge flushed the unplayed notes as `miss`)
or on `quit` mid-song (`completed: false`). Aggregated from the bridge's `judgment.note`.

| field | meaning |
|---|---|
| `song_id`, `title` | the song |
| `completed` | true = played to the end, false = quit |
| `bars_played` | sum over runs (start, each restart) of bars from `from_bar` to the last bar heard (to the end when finished) |
| `cutoffs` | Fletcher's `stop`s |
| `notes` | sheet notes judged: `perfect + good + off + miss` |
| `perfect`, `good`, `off`, `miss`, `extra` | counts by grade |
| `accuracy` | `(perfect + good) / notes`, 3 dp, or null |
| `mean_error_ms`, `mean_abs_error_ms` | over hits (perfect/good/off), + = late, 1 dp, or null |
| `rushing_bars`, `dragging_bars` | sorted bar numbers (lists, not counts) whose mean hit error was <= -20 / >= +20 ms (>= 2 hits) |

Fletcher barks once on a completed result: "Good." (no cutoff, accuracy >= 0.85) or "Again."
(accuracy < 0.6).

Consumers: `brain.on_session_state` (`state`, `bpm`, `bar`), `adapt.Adapter` (`t0`, `bpm`) and
`FletcherLink._on_session` (headset). On `count_in`, the link starts the finger at
`to_local_time(t0) - 5` beats, or immediately if `t0` is absent. It skips the finger if
Fletcher already cued one. Caveat: when a note has neither `bar` nor `song_time`,
`Adapter._bar_at` falls back to treating `t0` as the bar-1 origin. Bridge notes always carry
`bar`, so this fallback never runs on bridge data.

## judgment.*: referee -> Fletcher

`judgment.note` comes from `hub/bridge.py`, one per hit or missed note:

| field | req | meaning |
|---|---|---|
| `error_ms` | yes (may be null) | + = late (Chris's sign). null for `miss`/`extra` |
| `grade` | yes | `perfect` <= 15 ms, `good` <= 35, `off` <= 80, else `miss` (`adapt._grade`). `miss` = skipped sheet note, `extra` = hit with no sheet note |
| `bar`, `beat` | yes | the written note's bar, and its beat as a float (1.0 = downbeat) |
| `drum` | yes | Kevin's/GM drum name |
| `song_time` | yes | s from bar 1 of the sheet |

`judgment.bar` and `judgment.alert` are read by the brain but are **sent only by the mock
referee** today. The bridge doesn't send them, so the brain derives both from notes.

| type | fields (all optional to the brain) |
|---|---|
| `judgment.bar` | `bar`, `accuracy` 0-1, `mean_error_ms`, `tempo_ratio`, `trend` `steady`/`rushing`/`dragging`, `misses`, `extras` |
| `judgment.alert` | `kind` (`rushing`, `dragging`, `missed_notes`, `lost`, ...), `severity` 0-1, `since_bar`, `detail` |

Accepted aliases are listed in `fletcher/adapt.py` (`TYPE_ALIASES`, `FIELD_ALIASES`). Types
`note`/`hit`/`timing`/`judgement.note`, `bar`, `alert`, `state` map to the types above.
`error`/`offset_ms`/`delta_ms`/`timing_ms` are read as `error_ms`. The seconds fields
`err`/`error_s`/`median` are multiplied by 1000. Label strings (`early`/`late`/`perfect`...)
become -25/+25/0 ms. `instrument`/`piece` map to `drum`, `measure` to `bar`, `rating` to `grade`,
`status` to `state`, `tempo` to `bpm`. Bar fields: `ratio`, `mean_error`/`avg_error_ms`. Alert
fields: `type`, `level`, `start_bar`, `message`. A missing `bar` is computed from
`song_time` (or from `t` and `t0`) and `bpm`. A missing `grade` comes from `error_ms`.

## session.command: Fletcher / headset / console -> controller, bridge, headset

| `action` | fields | sent | effect |
|---|---|---|---|
| `start` | `song_id`, `from_bar` (opt, 1) | src `quest` or `console` (PLAY) | controller loads `songs/<song_id>/`, picks t0, publishes `count_in` (reason `start`); from any state (a running song is dropped) |
| `quit` | | src `quest` or `console` | controller publishes `session.result` (if the song has none yet), then `idle`. Fletcher drops a cutoff in progress |
| `stop` | `from_bar` | at the cutoff, with the fist | bridge mutes judging and answers `session.state stopped`. Headset lowers the band |
| `restart` | `from_bar`, **`t0` (opt)** | at `t0 - count_in_beats` beats (`main.py`) | bridge re-aligns the sheet: bar `from_bar` starts at **`t0`** (laptop clock = Fletcher's count-off downbeat), whenever the command arrives. Without `t0` it starts `count_in_beats` beats after arrival (old path). It answers `session.state count_in` with the `t0` it used. Hits before `t0 - beat/2` are not judged |

With the controller (bridge `--session`), the bridge ignores `restart` and re-aligns on the
controller's `count_in` instead (same t0, plus the seek); the controller ignores `stop`/`restart`
when no song is running.

Hub time maps to the transcriber's stream time through the median of (arrival - line time -
`--latency-ms`). With `t0` the restart bar's hub time is exact. The stream mapping is only as good
as that offset.

## fletcher.state: Fletcher -> headset (mood, body and cues)

Every message has `mood` (`MOODS`), `anger` (0-1, 2 dp), `action` (`ACTIONS`) and `look_at`
(`LOOK_AT`). A message without `gesture` is a plain state: the headset plays a move only when
`action` changes. The optional cue fields are below. They apply only when `gesture` is present,
except `priority`:

| field | meaning |
|---|---|
| `gesture` | `tirade.GESTURES` name or a Terence move id (`t1_fist_slam`). Headset resolves `GESTURE_MOVES[g]`, else the move id, else `ACTION_MOVES[action]` |
| `utterance_id` | the `fletcher.speech` it belongs to (`u-0001`...) |
| `at_index` | start when `fletcher.audio` chunk N of that utterance starts playing (lines) |
| `at_s` | s after the utterance's first sound (barks and lines). Negative = the move leads: the headset holds the clip back by `-min(at_s)`, capped at `bark_lead_max_s` (0.3) |
| `after_s` | s after the utterance finished playing (0 = at the end) |
| `t0` | count-off only (`gesture: t0_countoff_finger`): laptop-clock downbeat of the restart bar. Comes with `bpm` and `clips` (count-off clip ids, one per beat before t0, `fletcher/countoff/`). The finger starts 5 beats before t0. Word onsets (manifest `onset_s`) land on beats -4..-1 |
| `priority` | 0 ambient, 1 normal (default), 2 must play (the fist, the count-off) |

A cue carries exactly one anchor (`at_index`/`at_s`/`after_s`/`t0`). Cues may arrive before their
speech. Producers are `fletcher/main.py` (`send_state`, `send_cue`, `send_gestures`) and `fletcher/cues.py`.
Headset behaviour (`fletcher/godot/README.md`): one move at a time. A due cue preempts idle
moves, looping moves and lower-priority moves. Otherwise it waits for `move_finished`. It is
dropped if it ends up more than `max_late_s` (1.5 s) late, unless it has priority 2. After a line,
the headset falls back to the action's loop.

## fletcher.speech / fletcher.audio: Fletcher -> headset

| type | fields |
|---|---|
| `fletcher.speech` | `utterance_id`, `text`, `sample_rate` (24000), plus either `clip_id` (a bark: play `res://fletcher_barks/<clip_id>.wav`) or `stream: true` (a line: audio follows) |
| `fletcher.audio` | `utterance_id`, `index` (0, 1, ...), `pcm_b64`, `final: false`. Ends with `{index, pcm_b64: "", final: true, text}` |

Audio format: **16-bit little-endian mono PCM, 24 kHz, base64**. Chunks always hold a whole
number of samples. The headset prebuffers 0.15 s (`FletcherVoice`).

## library.*: library server -> headset, console (agent A's library/server.py)

`library.songs` `{"songs": [song.json, ...], "base_url": "http://<lan ip>:8766/songs/"}` on connect,
on every library change and in reply to `library.request` `{}`. Files: `<base_url><song_id>/<file>`.

## quest.settings: headset -> launcher

src `quest`, on connect and whenever the drummer changes Settings: `{"mic_source": "laptop"|"quest", ...}`
(other fields optional, ignored by the laptop). `python -m nqmt --mic laptop|quest` starts with its
`--mic` and then follows `mic_source` live: it stops Kevin's transcriber and starts it on the other
input (`--input mic` / `--input hub:<url>`). `--mic file:...` and `--mic-fixed` ignore it.

## mic.pcm: headset -> laptop transcriber (opt-in mic source)

src `quest` (`quest_app/scripts/game/mic_streamer.gd`), ~50 ms per frame:

| field | meaning |
|---|---|
| `seq` | frame counter |
| `rate`, `channels` | the mic's sample rate, always 1 channel |
| `first_sample` | index of the frame's first sample since the stream started |
| `local_t` | headset clock of `first_sample` (input latency subtracted) |
| `clock_offset` | opt: the headset's `FletcherHub.clock_offset` estimate (local - laptop) when it has one |
| `pcm_b64` | 16-bit LE mono PCM, base64 |

The hub does not record `mic.pcm` (~130 KB/s) unless started with `--record-mic-pcm`.

## calibrate.*: round-trip latency calibration (nqmt/calibrate.py <-> headset)

`python -m nqmt.calibrate [--mic quest|laptop]` (src `calibrate`) measures speaker out + mic in
through the real chain and the headset (`quest_app/scripts/game/calibration_player.gd`, game mode)
plays the claps. No session state changes; run it with nobody playing.

| type | from | data |
|---|---|---|
| `calibrate.start` | calibrate | `id` (str), `times` (laptop clock, one short clap each, default 8, 1 s apart), opt `gain_db` (clap level on the Band bus, 0), `interval_s` (info) |
| `calibrate.ack` | quest | `id`, `n` (claps scheduled), `lead_s` (first clap this far ahead on arrival), `output_latency_s` (what the clap train is started early by: driver + Settings latency), `clock_offset`; `error` if refused |
| `calibrate.result` | calibrate | `id`, `mic`, `ok`, `latency_ms` (median of onset - scheduled), `spread_ms` (max - min), `mad_ms`, `stdev_ms`, `n`, `matched`, `delays_ms` (per clap, null = not heard), `noise_onsets_per_s` (before the claps), `mute_filter`; `error` if not ok |

The headset plays clap k so it is heard at `to_local_time(times[k])`, scheduled exactly like the
backing (one generated clap train through BackingPlayer.play_at, same `extra_latency_s`). So the
measured delay is how late a drummer playing exactly with the band appears in the transcriber's
lines. An `ok` result is saved to `calibration.json` (repo root, `{mic: {latency_ms, ...}}`) and
the session bridge (`--calibration`, `--mic-source`, `--follow-mic`; `python -m nqmt` passes them)
uses it as its `input_latency` for that mic, live on `calibrate.result` and at startup. Changing
the headset's Settings latency afterwards changes the round trip: calibrate again.

## Teammates' line formats (not on the hub)

Kevin's transcriber and Chris's sheets both use `time_s  midi  name` lines, as specified in
`rushingOrDragging/file_format.md`. `hub/bridge.py` reads those lines from stdin or a FIFO. Without
a trained model the transcriber runs `--onsets-only` and every line is `time_s  0  onset` (drum
unknown, file_format.md §10). The bridge's score gate then gives each onset the notes the sheet
expects within +-100 ms of sheet time + tracked drift (one hit per note, so chords pair fully),
drops re-triggers within 60 ms of the last onset, or within 150 ms of a matched one when no note is
near, and sends the rest as `grade: "extra"` with `drum: "unknown"`. Lines with a real MIDI drum
skip the gate. With `--clock monotonic` (the live inputs, `transcribe_wav.py --input mic|hub:<url>`)
the header carries `# clock: monotonic` and `time_s` is the hit on the laptop clock; the session
bridge (`--session --clock laptop`) takes song time = time_s - t0 + song_time_at_t0 -
`--input-latency-ms` (0 by default: the live inputs already subtract their latency). Chris's
`live_compare.py --plain` output is parsed by `adapt.parse_live_compare_line` (seconds, + = late).

## How to run the whole thing (from the repo root, `python` = `.venv/bin/python`)

The app (song library, menu, sessions): one command starts hub, library/console, session
controller, session bridge, mic -> transcriber and Fletcher, and prints the LAN IP for the Quest:

```sh
python -m nqmt [--mic laptop|quest|file:<wav>[,<wav>...]] [--live] [--no-fletcher-voice] [--usb]
python -m nqmt.calibrate --mic quest       # round trip -> calibration.json -> the bridge (calibrate.*)
python -m hub.app_e2e --godot <Godot 4.5>  # the whole app with the headless headset; prints PASS
```

The one-sheet path, by hand:

```sh
python -m hub.server                       # 1. hub on :8765 (--port, --no-record)
python -m fletcher                         # 2. Fletcher (--dry-run: no API calls; --play-local; --barks-only)
cd live_transcription && uv run python tools/transcribe_wav.py <take.wav> --onsets-only \
    --song-origin -<count-in s> --realtime \
  | ../.venv/bin/python -m hub.bridge ../rushingOrDragging/out/<song>.drums.txt --bpm <bpm>   # 3. referee
Godot --path quest_app -- --fletcher ws://<laptop IP>:8765          # 4. desktop preview of the headset app
python -m fletcher.dev.watch               # optional: print what the headset would get
```

- Sheets: `python rushingOrDragging/cli.py <songsterr url|id> -o rushingOrDragging/out/<song>.drums.txt`.
  With a `<song>.tempo.json` next to the sheet (`songsterr/tempo_map.write_tempo_map`), `--bpm` is not needed.
- `live_transcription/models/` is empty in the repo, so the demo runs `--onsets-only` (no model;
  the bridge's score gate names the drums). With a trained model use `--model models/<model>.bin`
  instead; the bridge needs no change.
- Quest: Android export from `quest_app/` (its README, "XR / Quest"). Set the Rehearsal node's
  `fletcher_enabled` and `fletcher_hub_url` to the laptop's LAN IP.
- No hardware: `python -m hub.e2e` (bridge + hub + Fletcher, prints PASS) and
  `python -m fletcher.dev.e2e` (mock referee).
