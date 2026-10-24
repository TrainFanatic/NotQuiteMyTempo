# Prompts for Claude Code (Fletcher module)

Paste the first prompt into Claude Code, started at the repo root, after the `fletcher/`
folder is committed. The later ones are for follow-up sessions; start a fresh session per
task to save usage.

---

## 1. First session: plug Fletcher into the real repo

```
I'm on a 4-person Cal Hacks team building a Whiplash-style mixed-reality drum trainer.
I've taken over the Fletcher module ("Aaron's section" in the plan): Fletcher's mind and
voice. The code is in fletcher/ and already works end to end against mocks.
Read CONTRACT.md, AGENTS.md (if present) and fletcher/CLAUDE.md first.

My teammates' code has drifted from the plan. Before changing anything, find out what
really happens in this repo and report it to me as a short table:
1. The hub: is there one (e.g. hub/server.py)? Port, any handshake, does it echo a
   client's own messages back? If there is no hub, say so: I own it now, and we can
   promote fletcher/dev/mini_hub.py to hub/server.py.
2. The referee (Chris, probably referee/): every message type and field he actually
   publishes, the sign of his timing errors (is positive late?), and whether bar numbers
   are included. Use the code AND the newest recordings/*.jsonl if any exist.
3. What session.command he obeys (stop / restart / from_bar) and what he does on restart.
4. What Adam's Unity hub client expects from fletcher.* (unity/WhiplashVR).
Mark every difference from CONTRACT.md.

Then make the smallest change that makes Fletcher work with what they really send:
- Map their real messages onto the contract in fletcher/adapt.py ONLY (aliases, error
  sign, missing fields). Add a test in fletcher/tests using 3-5 real lines copied from a
  recording or from their code's output.
- Do not change any message format. If Fletcher's OUTPUT must change for Adam or Chris,
  stop and tell me why first.
- Run: python -m pytest fletcher/tests -q, then python -m fletcher.dev.e2e and
  python -m fletcher.dev.e2e --notes-only. Both must print PASS.
- If a recording exists, replay it (python -m fletcher.dev.mini_hub,
  python -m fletcher --dry-run, python -m fletcher.dev.replay <file>) and tell me where
  Fletcher would cut off and what he'd say.
Keep every module running if the hub disconnects. Don't touch other people's folders.
```

---

## 2. Go live with Claude + ElevenLabs

```
fletcher/.env now has ANTHROPIC_API_KEY, ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID.
Run python -m fletcher.dev.e2e --live and report the "first audio N ms after cutoff"
numbers and every generated line. Budget: 1.5 s from cutoff to first audio.
If the Eleven v4 Turbo socket fails, show me the exact error from fletcher.voice and check
the current ElevenLabs Text to Dialogue WebSocket docs before changing voice.py. Keep the
fallback chain (v4 -> Flash -> barks). Then generate the barks with
python -m fletcher.generate_barks and list any clip that failed.
```

## 3. Tune him on real playing

```
Here is what happened when Chris played: <paste what you saw: too eager / too slow /
wrong bar / lines too long>. The newest session is recordings/<file>.jsonl.
Replay it against python -m fletcher --dry-run and tune only fletcher/config.py defaults
(cutoff_anger, alert_gain, grace_bars, phrase_bars, speculate_margin, stare_s) so that
<goal, e.g. he cuts off about 2 bars after the drift starts, never in the first 2 bars>.
Show me the before/after cutoff bars from the replay.
```

## 4. Make the lines better

```
Look at the generated lines in the last log. Improve SYSTEM_TEMPLATE and LEVELS in
fletcher/tirade.py so lines are more specific to the fact sheet and stay under the word
limits. Keep every content rule. Never quote or paraphrase the film. Test 10 generations
per level with a script against the real API and show them to me.
```

## 5. Unity voice with Adam

```
Adam's Unity project is unity/WhiplashVR. Help wire fletcher/unity/FletcherVoice.cs:
find where his hub client dispatches messages and call
fletcherVoice.HandleMessage(type, json) on the main thread for fletcher.* messages.
Don't edit the scene file (Adam owns it); give him exact Inspector steps instead.
Copy fletcher/barks/*.wav to Assets/Resources/Barks/.
```
