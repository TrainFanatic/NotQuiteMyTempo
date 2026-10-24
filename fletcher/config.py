"""All tunables in one place. Every value can be overridden with an env var
(put them in fletcher/.env). Names match the field names in UPPER_CASE."""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(MODULE_DIR / ".env")
    load_dotenv()  # also a .env in the current directory


@dataclass
class Config:
    # --- hub -------------------------------------------------------------
    hub_url: str = "ws://localhost:8765"
    src: str = "aaron"  # our sender name in the envelope

    # --- Claude (tirades) -------------------------------------------------
    anthropic_api_key: str = ""
    llm_model: str = "claude-haiku-5-5"
    llm_effort: str = "low"  # low = fastest; thinking is disabled too
    llm_max_tokens: int = 160
    llm_first_sentence_timeout_s: float = 2.0  # then fall back to a canned line

    # --- ElevenLabs (voice) -----------------------------------------------
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    tts_engine: str = "v4"  # v4 | flash | none
    tts_model: str = "eleven_v4_turbo"  # streams over Text to Dialogue WS
    tts_fallback_model: str = "eleven_flash_v2_5"  # Text to Speech WS, no tags
    bark_model: str = "eleven_v4"  # offline bark generation, best quality
    tts_stability: float = 0.35  # lower = wider emotional range (v4 reads only this)
    sample_rate: int = 24000  # pcm_24000 = 16-bit mono little-endian

    # --- content ----------------------------------------------------------
    profanity: bool = False  # off by default for judging

    # --- mood machine -----------------------------------------------------
    cutoff_anger: float = 0.8  # first cutoff threshold
    patience_step: float = 0.08  # threshold drops this much per cutoff
    cutoff_floor: float = 0.55
    alert_gain: float = 0.3  # anger added per bar = gain * severity
    miss_gain: float = 0.02
    bar_decay: float = 0.08  # anger removed per clean bar
    idle_decay_per_s: float = 0.01
    sustain_severity: float = 0.7  # alert this high for a full bar -> cutoff
    bark_severity: float = 0.45
    bark_cooldown_s: float = 2.5
    speculate_margin: float = 0.2  # start drafting when anger >= threshold - margin
    speculate_severity: float = 0.55
    grace_bars: int = 2  # no cutoff in the first bars after a (re)start
    phrase_bars: int = 4  # restart on a phrase boundary (1, 5, 9...); 0 = exact bar
    approve_bars: int = 2  # clean bars after a restart before he nods
    praise_chance: float = 0.3
    beats_per_bar: int = 4

    # --- note-derived fallback referee --------------------------------------
    derive_threshold_ms: float = 18.0  # mean error of last 8 hits that counts as drift
    error_sign: int = 1  # set -1 if the referee's error_ms is positive = EARLY

    # --- timing of the cutoff sequence ---------------------------------------
    stare_s: float = 0.5  # silent glare after the fist, before he speaks
    restart_gap_s: float = 0.8  # pause after his last word before the restart
    quest_latency_s: float = 0.15  # headset audio buffering we wait out

    # --- modes --------------------------------------------------------------
    dry_run: bool = False  # no API calls: canned lines + tone audio
    play_local: bool = False  # also play his voice on the laptop speakers
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, **overrides) -> "Config":
        _load_dotenv()
        cfg = cls()
        for f in fields(cls):
            raw = os.environ.get(f.name.upper())
            if raw is None or raw == "":
                continue
            if f.type in ("bool", bool):
                val = raw.strip().lower() in ("1", "true", "yes", "on")
            elif f.type in ("int", int):
                val = int(raw)
            elif f.type in ("float", float):
                val = float(raw)
            else:
                val = raw
            setattr(cfg, f.name, val)
        for k, v in overrides.items():
            if v is not None:
                setattr(cfg, k, v)
        return cfg

    def describe(self) -> str:
        hidden = {"anthropic_api_key", "elevenlabs_api_key"}
        parts = []
        for f in fields(self):
            v = getattr(self, f.name)
            if f.name in hidden:
                v = "set" if v else "MISSING"
            parts.append(f"{f.name}={v}")
        return " ".join(parts)
