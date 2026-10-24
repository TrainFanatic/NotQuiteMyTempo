# fletcher/ — Fletcher's mind and voice

Consumes `session.state` and `judgment.*` from the hub; publishes `fletcher.state`,
`fletcher.speech`, `fletcher.audio` and `session.command`. Formats: `CONTRACT.md` at the
repo root is the source of truth.

## How it works
- `brain.py` is a deterministic mood machine (one `anger` number, 0–1). It alone decides
  when to bark and when to cut off. Never move the cutoff decision into the LLM: it must
  fire on the beat and be testable.
- Three tiers: reflexes are Adam's (he reads `judgment.note` directly); **barks** are
  pre-generated clips sent as `clip_id` (no network at demo time); **tirades** are written
  by Claude (`claude-haiku-5-5`, thinking disabled, effort low) only after a cutoff.
- Speculation: when anger nears the threshold, `main.py` starts drafting the line before
  the cutoff (re-drafted each bar with fresh facts). The fist + `stare_s` glare hides the rest.
- `tirade.py` streams Claude's text and splits it into sentences; `voice.py` sends each
  sentence to ElevenLabs with a flush. Engines in order: Eleven v4 Turbo (Text to Dialogue
  WebSocket, performs audio tags) → Flash v2.5 (Text to Speech WebSocket, tags stripped)
  → barks only. Without an API key, canned lines replace Claude.
- `adapt.py` is the ONLY place that maps teammates' real message shapes onto the contract.
  When the referee's output differs from CONTRACT.md, fix it there, with a test.

## Files
| file | role |
|---|---|
| `__main__.py` | `python -m fletcher` CLI |
| `main.py` | orchestrator: hub → brain → barks/tirade/voice → hub; the cutoff sequence |
| `brain.py` | mood machine + `Facts` (the LLM's fact sheet). Pure, no I/O |
| `adapt.py` | normalizes incoming messages (aliases, sign, missing bar numbers) |
| `tirade.py` | system prompt, Claude streaming, `Draft`, canned fallback lines |
| `voice.py` | ElevenLabs engines + dry-run tone |
| `barks.py`, `generate_barks.py` | bark catalog; generates `barks/*.wav` + `manifest.json` |
| `config.py` | every tunable; env vars override (`fletcher/.env`) |
| `hub_client.py` | reconnects every 1 s forever |
| `dev/` | `mini_hub`, `mock_referee`, `watch`, `replay`, `e2e` |
| `unity/FletcherVoice.cs` | Unity side of the voice; Adam wires it into the scene |

## Commands (run from the repo root)
- Tests: `python -m pytest fletcher/tests -q`
- Whole loop, no keys: `python -m fletcher.dev.e2e` (and `--notes-only`) → must print PASS
- Live loop with keys: `python -m fletcher.dev.e2e --live`
- Real run: `python -m fletcher` (`--dry-run`, `--play-local`, `--barks-only`)
- Replay a real session: `python -m fletcher.dev.replay recordings/<file>.jsonl`

## Rules
- Never change a message format. If one seems wrong, stop and explain why; CONTRACT.md
  changes first, agreed by all four. Adding an optional field is fine: post it in team chat.
- Ignore unknown types and fields; never crash on them. Keep running if the hub drops.
- Test against real messages from `recordings/*.jsonl`, never imagined ones.
- Latency budget: cutoff → first generated word ≤ 1.5 s; bark ≤ 200 ms. Log and measure
  (`first audio N ms after cutoff`). If a stage misses budget, cut the feature, not the budget.
- Content: harsh about the playing only. No slurs, nothing about identity/body/family, no
  self-harm or violence. Profanity off by default. Original lines only: never quote the film.
- The voice is an original designed voice. Never clone a real actor's voice.
- Never print, log or commit API keys; `.env` is git-ignored.
- Keep `brain.py` free of I/O and time calls (it receives `t`), so tests stay deterministic.
- Smallest change that works end to end; run the tests and `dev.e2e` before committing.
