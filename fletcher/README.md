# Fletcher: mind and voice

Reads the referee's stream (`session.state`, `judgment.note/bar/alert`), decides when
Fletcher glares, barks and cuts the band off, has Claude write one cutting line about the
actual mistake, and streams it through your ElevenLabs voice to the headset.

```
judgment.* ──► brain.py (anger 0–1) ──► bark: clip_id ─────────────────────────► fletcher.speech
                     │                                                         
                     └─ cutoff ─► session.command stop ─► glare ─► Claude Haiku ─► ElevenLabs v4 Turbo
                                                                   (drafted early)   └► fletcher.speech + fletcher.audio
                                  session.command restart from_bar ◄── after his last word
```

## Quick start (from the repo root)

```bash
pip install -r fletcher/requirements.txt
python -m pytest fletcher/tests -q          # 12 tests
python -m fletcher.dev.e2e                  # whole loop with mocks, no keys -> PASS
python -m fletcher.dev.e2e --notes-only     # referee sends only early/late per note -> PASS
```

Then add keys: `cp fletcher/.env.example fletcher/.env` and fill in
`ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` (the voice you designed).

```bash
python -m fletcher.dev.e2e --live           # real Claude + ElevenLabs, prints latency
python -m fletcher.generate_barks           # ~50 clips -> fletcher/barks/*.wav, give to Adam
python -m fletcher                          # the real thing, against the team hub
```

Hear him on the laptop without the Quest (4 terminals):

```bash
python -m fletcher.dev.mini_hub             # only if the team hub isn't running
python -m fletcher --play-local             # add --dry-run to skip the APIs
python -m fletcher.dev.watch                # what Adam would receive, with latencies
python -m fletcher.dev.mock_referee         # fake drummer: rushes, then drags, then nails it
```

## What he decides, and when

| Tier | When | How fast | Who |
|---|---|---|---|
| Reflex (glare, head flick) | every bad note | next frame | Adam, straight from `judgment.note` |
| Mood (`fletcher.state`) | anger crosses 0.2 / 0.45 / 0.7 | instant | brain |
| Bark ("Rushing.") | alert severity ≥ 0.45, 2.5 s cooldown | ~0 ms, local clip | brain → `clip_id` |
| Cutoff | anger ≥ threshold, or an alert ≥ 0.7 held for a full bar, never in the first 2 bars | instant | brain |
| Tirade | after the fist + 0.5 s glare | ~0.5–1.5 s | Claude Haiku 5.5 → ElevenLabs |

- **Patience shrinks**: the threshold drops 0.08 per cutoff (0.8 → 0.72 → 0.64 → floor 0.55),
  and the line escalates: cutoff 1 is one cold sentence, cutoff 3 starts quiet and explodes.
- **Restart bar** is the start of the phrase where the trouble began (bars 1, 5, 9, 13).
  The line always names it; if Claude forgets, the order is appended.
- **Speculation**: once anger is within 0.2 of the threshold (or severity ≥ 0.55) he starts
  drafting, re-drafting each bar with fresh numbers. At the cutoff the line is usually ready,
  so first audio ≈ the glare (measured ~520 ms in the mock run).
- **Approval**: after a restart, two clean bars past the trouble spot → `nod`, sometimes "Good."
- **Works on a bare note stream**: if the referee only sends early/late per note, the brain
  detects drift itself (mean of the last 8 hits ≥ 18 ms).

## Failure modes, already handled

| If… | then… |
|---|---|
| no Anthropic key, or Claude takes > 2 s | canned original line with the real numbers |
| Eleven v4 socket fails | Flash v2.5 (tags stripped) |
| no voice at all (`--barks-only`, no key, no internet) | "Stop!" + "From bar nine." clips |
| hub down | reconnect every second, never crash |
| referee renames fields / flips the sign | fix in `adapt.py` (+ `ERROR_SIGN=-1`) |

## Messages out

As in CONTRACT.md. One optional addition (post it in team chat): the final `fletcher.audio`
chunk carries `"text"` with the full line, because `fletcher.speech` is sent at the first
audio chunk and may hold only the first sentence. The final chunk has an empty `pcm_b64`.

## Tuning

Everything is in `config.py`, overridable in `.env` (upper-case names). The ones that matter
most at the kit: `CUTOFF_ANGER`, `ALERT_GAIN`, `GRACE_BARS`, `PHRASE_BARS`, `STARE_S`,
`TTS_STABILITY` (lower = more volatile voice), `PROFANITY` (off for judging).
