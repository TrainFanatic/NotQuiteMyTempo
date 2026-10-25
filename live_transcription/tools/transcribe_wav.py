"""Replay prepared WAV (or live input) through the streaming core; canonical notes only on stdout."""

import argparse
import signal
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import soundfile as sf
from common import (
    Native,
    QUEST_MUTE,
    QuestMuteFilter,
    RATE,
    events,
    note_line,
    onset_line,
    ROOT,
    peak_rss_bytes,
)
from live_input import open_input


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("audio", nargs="?", help="prepared 48 kHz mono WAV (or use --input)")
    p.add_argument(
        "--input",
        help="live audio instead of a WAV: mic[:device] (laptop microphone, sounddevice) or "
        "hub:ws://host:8765 (the Quest's mic.pcm frames); runs until Ctrl-C",
    )
    p.add_argument(
        "--clock",
        choices=("song", "monotonic"),
        default="song",
        help="song: time_s = --song-origin + sample/48000 (default); monotonic: time_s on the "
        "laptop clock, time.monotonic() = fletcher.contract.now() (live input, or a WAV with "
        "--realtime: sample 0 = replay start)",
    )
    p.add_argument(
        "--reorder-ms",
        type=float,
        default=60.0,
        help="hub input: wait this long for a missing mic.pcm frame before treating it as a gap",
    )
    p.add_argument(
        "--mic-latency-ms",
        type=float,
        default=None,
        help="mic input: subtract this from PortAudio's block timestamps (default: the "
        "stream's reported input latency; see LaptopMic in live_input.py)",
    )
    p.add_argument(
        "--idle-exit-s",
        type=float,
        default=None,
        help="hub input: exit after the stream has been silent this long (tests)",
    )
    p.add_argument("--model")
    p.add_argument(
        "--onsets-only",
        action="store_true",
        help="no model: emit every onset as midi 0 'onset' (drum unknown); "
        "a score gate downstream assigns the drum",
    )
    p.add_argument(
        "--mute-window-ms",
        type=float,
        default=None,
        help="drop an onset this soon after the last kept one if the Quest muted the mic "
        "in between (0 disables). Default: onsets-only 240; with --model, whatever filter "
        "the model was trained with (its .json from train_model.py; none for older models)",
    )
    p.add_argument(
        "--mute-db",
        type=float,
        default=None,
        help="level (dBFS, 2 ms frames) below which the mic counts as muted (default -75, "
        "or the model's)",
    )
    p.add_argument(
        "--mute-drop-db",
        type=float,
        default=None,
        help="a dip this far under the last onset's peak also counts as muted "
        "(the higher of the two thresholds wins; negative = --mute-db only; default 35, "
        "or the model's)",
    )
    p.add_argument("--output", type=Path, default=None)
    p.add_argument("--diagnostics", type=Path)
    p.add_argument("--chunk", type=int, default=480)
    p.add_argument(
        "--song-origin",
        type=float,
        default=0.0,
        help="song seconds at capture sample zero; negative for count-in",
    )
    p.add_argument("--realtime", action="store_true")
    a = p.parse_args()
    if not 1 <= a.chunk <= 4800:
        p.error("--chunk must be 1..4800")
    trained = QUEST_MUTE if a.onsets_only else trained_mute_filter(a.model)
    if a.mute_window_ms is None:
        a.mute_window_ms = trained["window_s"] * 1000 if trained else 0.0
    if a.mute_db is None:
        a.mute_db = (trained or QUEST_MUTE)["mute_db"]
    if a.mute_drop_db is None:
        drop = (trained or QUEST_MUTE)["drop_db"]
        a.mute_drop_db = -1.0 if drop is None else drop
    if a.mute_window_ms < 0:
        p.error("--mute-window-ms must be >= 0")
    if bool(a.model) == a.onsets_only:
        p.error("Give exactly one of --model or --onsets-only")
    if bool(a.audio) == bool(a.input):
        p.error("Give exactly one of a WAV path or --input")
    if a.clock == "monotonic" and a.audio and not a.realtime:
        p.error("--clock monotonic with a WAV needs --realtime")
    output = a.output or ROOT.parent / "rushingOrDragging" / "out" / (
        "played_"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        + ".drums.txt"
    )
    paths = [output.resolve()] + ([Path(a.audio).resolve()] if a.audio else [])
    if a.model:
        paths.append(Path(a.model).resolve())
    if a.diagnostics:
        paths.append(a.diagnostics.resolve())
    if len(set(paths)) != len(paths):
        p.error("Input, model, output and diagnostics paths must be distinct")
    output.parent.mkdir(parents=True, exist_ok=True)
    if a.diagnostics:
        a.diagnostics.parent.mkdir(parents=True, exist_ok=True)
    block_ms = []
    latencies = []
    wall_latencies = []
    count = 0
    mute = (
        QuestMuteFilter(
            a.mute_window_ms / 1000,
            a.mute_db,
            a.mute_drop_db if a.mute_drop_db >= 0 else None,
        )
        if a.mute_window_ms > 0
        else None
    )
    live = (
        open_input(
            a.input,
            a.chunk,
            a.reorder_ms / 1000,
            a.idle_exit_s,
            None if a.mic_latency_ms is None else a.mic_latency_ms / 1000,
        )
        if a.input
        else None
    )
    # SIGTERM (a launcher stopping us) ends a live run like Ctrl-C: summary, clean close
    signal.signal(signal.SIGTERM, _interrupt)
    with (
        sf.SoundFile(a.audio) if a.audio else _NoFile()
    ) as source, Native(a.model, a.song_origin) as core:
        if a.audio and (source.samplerate != RATE or source.channels != 1 or not len(source)):
            p.error("Use a nonempty prepared 48 kHz mono WAV")
        with output.open("x", encoding="utf-8", newline="\n") as saved:
            diagnostic = (
                a.diagnostics.open("x", encoding="utf-8") if a.diagnostics else None
            )
            try:
                header = (
                    "# live transcription: onsets only, drum unknown (midi 0)\n"
                    if a.onsets_only
                    else "# live transcription: six broad families\n"
                ) + (
                    "# clock: monotonic (laptop time.monotonic() seconds)\n"
                    if a.clock == "monotonic"
                    else ""
                ) + "# time_s  midi  name\n"
                saved.write(header)
                saved.flush()
                sys.stdout.write(header)
                sys.stdout.flush()
                started = last_flush = time.monotonic()
                position = 0

                def when(c):
                    """Output time of candidate c on the requested clock."""
                    if a.clock == "song":
                        return c.time_s
                    if live:
                        return live.time_of(c.sample)
                    return started + c.sample / RATE  # realtime WAV replay

                def emit(candidates, delivery_sample):
                    nonlocal count
                    for c in candidates:
                        # Include block delivery delay; available_sample is core readiness only.
                        latency = (delivery_sample - c.sample) / RATE * 1000
                        latencies.append(latency)
                        if live:  # hit (laptop clock) -> this line, incl. input latency
                            wall_latency = (time.monotonic() - live.time_of(c.sample)) * 1000
                        elif a.realtime:
                            wall_latency = (time.monotonic() - started - c.sample / RATE) * 1000
                        else:
                            wall_latency = None
                        if wall_latency is not None:
                            wall_latencies.append(wall_latency)
                        if diagnostic:
                            diagnostic.write(
                                json.dumps(
                                    {
                                        "sample": c.sample,
                                        "time_s": c.time_s,
                                        "algorithmic_latency_ms": latency,
                                        "replay_wall_latency_ms": wall_latency,
                                        "accepted": bool(c.family_mask),
                                        "probabilities": list(c.probability)[
                                            : c.class_count
                                        ],
                                    }
                                )
                                + "\n"
                            )
                    # Same filter for both modes; train_model.py trains/evaluates with it too.
                    candidates = [c for c in candidates if mute is None or mute.keep(c)]
                    if a.onsets_only:
                        lines = "".join(
                            onset_line(when(c)) for c in candidates if c.time_s >= 0
                        )
                    elif a.clock == "song":
                        lines = "".join(note_line(t, m) for t, m in events(candidates))
                    else:
                        lines = "".join(
                            note_line(when(c), c.midi[k])
                            for c in candidates
                            if c.time_s >= 0
                            for k in range(c.class_count)
                            if c.family_mask & (1 << k)
                        )
                    count += lines.count("\n")
                    if lines:
                        saved.write(lines)
                        sys.stdout.write(lines)
                        sys.stdout.flush()

                if live:
                    try:
                        position = run_live(live, core, mute, emit, block_ms, saved)
                    except KeyboardInterrupt:
                        pass
                    finally:
                        live.close()
                for block in [] if live else source.blocks(blocksize=a.chunk, dtype="float32"):
                    if a.realtime:
                        time.sleep(
                            max(
                                0,
                                started
                                + (position + len(block)) / RATE
                                - time.monotonic(),
                            )
                        )
                    before = time.perf_counter()
                    if mute:
                        mute.feed(block, position)
                    candidates, _ = core.process(block, position)
                    block_ms.append((time.perf_counter() - before) * 1000)
                    position += len(block)
                    emit(candidates, position)
                    if time.monotonic() - last_flush >= 0.25:
                        saved.flush()
                        last_flush = time.monotonic()
                emit(core.finish(), position)
                saved.flush()
                summary = {
                    "output": str(output),
                    "notes": count,
                    "stats": core.stats(),
                    "quest_mute_dropped": mute.dropped if mute else None,
                    "processing_ms_per_block_p95": float(np.percentile(block_ms, 95))
                    if block_ms
                    else None,
                    "algorithmic_latency_ms_p95": float(np.percentile(latencies, 95))
                    if latencies
                    else None,
                    "replay_wall_latency_ms_p95": float(
                        np.percentile(wall_latencies, 95)
                    )
                    if wall_latencies
                    else None,
                    "desktop_process_peak_rss_bytes": peak_rss_bytes(),
                    "elapsed_s": time.monotonic() - started,
                    "device": "desktop replay, not Quest benchmark",
                }
                if live:
                    summary["device"] = "live input"
                    summary["live"] = live.stats()
                    summary["hit_to_line_ms_p50"] = (
                        float(np.percentile(wall_latencies, 50)) if wall_latencies else None
                    )
                print(json.dumps(summary), file=sys.stderr)
            finally:
                if diagnostic:
                    diagnostic.close()


def trained_mute_filter(model):
    """QuestMuteFilter settings the model was trained and evaluated with (train_model.py's
    sidecar <model>.json), or None: no sidecar or an older model trained on every candidate."""
    sidecar = Path(model).with_suffix(".json") if model else None
    if not sidecar or not sidecar.exists():
        return None
    options = json.loads(sidecar.read_text()).get("training_options", {})
    return options.get("quest_mute_filter")


def _interrupt(*_):
    raise KeyboardInterrupt


class _NoFile:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


def run_live(live, core, mute, emit, block_ms, saved):
    """Feed live blocks to the core; returns the next sample index. A forward jump in the
    first-sample index is a gap: the core restarts its acoustic state (DT_GAP) and the mute
    filter its partial frame. Prints the input's stats to stderr every 10 s."""
    position = None
    report = time.monotonic() + 10
    for block, first in live.blocks():
        if mute:
            if position is not None and first != position:
                mute.pending = np.zeros(0, dtype=np.float32)  # no 2 ms frame across a gap
            mute.feed(block, first)
        before = time.perf_counter()
        candidates, _ = core.process(block, first)
        block_ms.append((time.perf_counter() - before) * 1000)
        position = first + len(block)
        emit(candidates, position)
        if time.monotonic() >= report:
            saved.flush()
            print(json.dumps(live.stats()), file=sys.stderr, flush=True)
            report = time.monotonic() + 10
    return position or 0


if __name__ == "__main__":
    main()
