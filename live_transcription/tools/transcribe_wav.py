"""Replay prepared WAV through the streaming core; canonical notes only on stdout."""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import soundfile as sf
from common import Native, RATE, events, note_line, ROOT, peak_rss_bytes


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("audio")
    p.add_argument("--model", required=True)
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
    output = a.output or ROOT.parent / "rushingOrDragging" / "out" / (
        "played_"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        + ".drums.txt"
    )
    paths = [Path(a.audio).resolve(), Path(a.model).resolve(), output.resolve()]
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
    with sf.SoundFile(a.audio) as source, Native(a.model, a.song_origin) as core:
        if source.samplerate != RATE or source.channels != 1 or not len(source):
            p.error("Use a nonempty prepared 48 kHz mono WAV")
        with output.open("x", encoding="utf-8", newline="\n") as saved:
            diagnostic = (
                a.diagnostics.open("x", encoding="utf-8") if a.diagnostics else None
            )
            try:
                header = (
                    "# live transcription: six broad families\n# time_s  midi  name\n"
                )
                saved.write(header)
                saved.flush()
                sys.stdout.write(header)
                sys.stdout.flush()
                started = last_flush = time.monotonic()
                position = 0

                def emit(candidates, delivery_sample):
                    nonlocal count
                    for c in candidates:
                        # Include block delivery delay; available_sample is core readiness only.
                        latency = (delivery_sample - c.sample) / RATE * 1000
                        latencies.append(latency)
                        wall_latency = (
                            ((time.monotonic() - started - c.sample / RATE) * 1000)
                            if a.realtime
                            else None
                        )
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
                    lines = "".join(note_line(t, m) for t, m in events(candidates))
                    count += lines.count("\n")
                    if lines:
                        saved.write(lines)
                        sys.stdout.write(lines)
                        sys.stdout.flush()

                for block in source.blocks(blocksize=a.chunk, dtype="float32"):
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
                    "processing_ms_per_block_p95": float(np.percentile(block_ms, 95)),
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
                print(json.dumps(summary), file=sys.stderr)
            finally:
                if diagnostic:
                    diagnostic.close()


if __name__ == "__main__":
    main()
