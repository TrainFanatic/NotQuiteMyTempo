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

## Adding a subfolder

Put new functionality in its own top-level subfolder with its own documentation, and add a
section for it above. The root `.gitignore` applies to every subfolder: it ignores `data/`,
`out/`, `samples/` and Python caches at any depth.
