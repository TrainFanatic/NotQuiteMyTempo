"""python -m fletcher [--dry-run] [--play-local] [--hub ws://localhost:8765]"""

from __future__ import annotations

import argparse
import asyncio

from .config import Config
from .main import Fletcher, setup_logging


def main() -> None:
    p = argparse.ArgumentParser(prog="fletcher", description="Fletcher's mind and voice")
    p.add_argument("--hub", dest="hub_url", help="hub URL (default ws://localhost:8765)")
    p.add_argument("--dry-run", action="store_true", default=None,
                   help="no API calls: canned lines and a hum instead of the voice")
    p.add_argument("--play-local", action="store_true", default=None, help="also play audio on this laptop")
    p.add_argument("--barks-only", action="store_true", help="never call the voice API at cutoff")
    p.add_argument("--profanity", action="store_true", default=None)
    p.add_argument("--log-level", default=None)
    a = p.parse_args()
    cfg = Config.from_env(hub_url=a.hub_url, dry_run=a.dry_run, play_local=a.play_local,
                          profanity=a.profanity, log_level=a.log_level,
                          tts_engine="none" if a.barks_only else None)
    setup_logging(cfg.log_level)
    try:
        asyncio.run(Fletcher(cfg).run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
