# NotQuiteMyTempo

NotQuiteMyTempo is made of several parts, each in its own top-level subfolder. Different
contributors own different subfolders, and together they make up the whole project. Each
subfolder documents itself; start with its own docs before changing it.

## Subfolders

### `rushingOrDragging/`: timing comparison against sheet music

Scope:
- **Import:** downloads a song's drum part from Songsterr and converts it into a simple
  timestamped note list, the `.drums.txt` format.
- **Grade:** compares a drummer's performance against that reference, either after the fact
  (an early/late report) or live (a running **RUSHING!** / **DRAGGING!** / **PERFECT!** display).
- **Check:** renders note files to WAV or MIDI so you can listen to them, and generates
  simulated performances for testing.

Out of scope: it doesn't capture or transcribe audio. It expects the played performance to
arrive as a `.drums.txt` file or stream, so another part of the project has to produce one.

The `.drums.txt` format is specified in
[`rushingOrDragging/file_format.md`](rushingOrDragging/file_format.md); other parts should
follow it to work with `rushingOrDragging`. For the file layout, commands and workflows, see
[`rushingOrDragging/STRUCTURE.md`](rushingOrDragging/STRUCTURE.md).

### `live_transcription/`: microphone audio to drum events

Portable C++ drum-onset and family-classification core, with desktop recording preparation,
annotation, training, WAV replay and evaluation tools. Produces the timestamped feed consumed
by `rushingOrDragging`. Quest/Godot capture integration is a later milestone; a trained kit
model requires labeled recordings. See the complete [setup and workflow guide](live_transcription/README.md).

### `hub/`: the team hub and the referee bridge

The WebSocket hub every part talks through, and the bridge that turns `live_transcription`'s hits
into per-hit judgments (using `rushingOrDragging`'s live matcher) for Fletcher. See
[`hub/README.md`](hub/README.md).

### `fletcher/`: the conductor's mind and voice

Mood machine, Claude-written tirades, ElevenLabs voice, barks and gesture cues. See
[`fletcher/CLAUDE.md`](fletcher/CLAUDE.md).

## Adding a subfolder

Put new functionality in its own top-level subfolder with its own documentation, and add a
section for it above. The root `.gitignore` applies to every subfolder: it ignores `data/`,
`out/`, `samples/` and Python caches at any depth.

### `quest_app/`: Meta Quest mixed-reality rehearsal room (Godot 4)

The player sits at their real kit, seen through passthrough, with a virtual big band and
bandleader Terence around them. It covers the band and Terence animation (tempo-scaled, beat
locked to a song), Terence's entrance through the door, real recorded positional sound effects
for Terence and his props, the passthrough window over the real kit, and the Quest build. It is
driven through `scripts/scene_director.gd` messages. See `quest_app/README.md`.

### `quest_assets/`: Blender build scripts and exported assets for `quest_app/`

Headless Blender scripts that build the room, the band, Terence, instruments and props as
PS2-style low-poly glTF, plus the sound effects (real CC0/CC-BY recordings, credited in
`sfx/CREDITS.md`) and their animation cue tables.
See `quest_assets/ANIMATIONS.md` and `quest_assets/DECISIONS.md`.

### `datacollection/`: recording plan and free labels for the Quest mic

How to record training/evaluation takes on the Quest together with a laptop reference mic and
guide track (Fletcher's count-off + click), then align them and write `labels.csv` for
`live_transcription/` without hand-labeling. Also scores the Quest mic against the reference to
decide which mic the demo uses. See [`datacollection/README.md`](datacollection/README.md).
