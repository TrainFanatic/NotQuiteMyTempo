"""Write and voice a handful of tirades with the real Claude + ElevenLabs keys,
saved to fletcher/lines/ (wav + index.jsonl) for listening, no hub needed.

    python -m fletcher.dev.sample_lines              # one line per level, rushing + dragging
    python -m fletcher.dev.sample_lines --levels 3 --kinds dragging --n 2
    python -m fletcher.dev.sample_lines --text-only --n 3   # Claude only, no voice credits
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from ..brain import Facts
from ..config import Config
from ..contract import now
from ..main import Fletcher, setup_logging
from ..text import strip_tags
from ..tirade import Draft

log = logging.getLogger("sample_lines")

SCENES = {
    "rushing": dict(mean_error_ms=-38.0, tempo_ratio=1.06, detail="snare on 2 and 4 early by ~40 ms"),
    "dragging": dict(mean_error_ms=34.0, tempo_ratio=0.95, detail="kick behind the click every bar"),
    "missed_notes": dict(misses=4, accuracy=0.6, detail="4 missed hi-hat notes in bar 10"),
}


async def sample(levels: list[int], kinds: list[str], n: int, text_only: bool = False) -> None:
    cfg = Config.from_env()
    f = Fletcher(cfg)
    if not f.writer.live or not (f.voices or text_only):
        raise SystemExit("needs ANTHROPIC_API_KEY, ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID in fletcher/.env")
    for kind in kinds:
        for level in levels:
            for _ in range(n):
                facts = Facts(kind=kind, severity=0.6 + 0.12 * level, since_bar=9, current_bar=11,
                              from_bar=9, bpm=100, cutoff_number=level, **SCENES[kind])
                draft = Draft(f.writer, facts, level).start()
                t = now()
                if text_only:
                    await draft.done.wait()
                    print(f"L{level} {kind:<9} {len(strip_tags(draft.text).split()):>3}w  {draft.text}")
                elif not await f.speak(draft, t, t, facts, level):
                    log.warning("no voice for %s level %d", kind, level)
                await draft.done.wait()
                f.writer.recent.append(draft.text)
    for v in f.voices:
        await v.close()
    log.info("done; listen in %s (index.jsonl has the text)", cfg.lines_dir)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--levels", default="1,2,3")
    p.add_argument("--kinds", default="rushing,dragging")
    p.add_argument("--n", type=int, default=1, help="lines per kind and level")
    p.add_argument("--text-only", action="store_true", help="print Claude's lines, don't voice them")
    a = p.parse_args()
    setup_logging("INFO")
    asyncio.run(sample([int(x) for x in a.levels.split(",")], a.kinds.split(","), a.n, a.text_only))


if __name__ == "__main__":
    main()
