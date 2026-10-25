extends RefCounted
## A song's drum part as a chart: the library's drums.drums.txt (`time_s midi name` lines, song
## time 0 = bar 1's downbeat) cut into bars by drums.tempo.json (`bars`: start_s, bar_s,
## numerator, denominator; the same time origin). Each hit lands in one of six lanes, top to
## bottom as on a drum chart: crash, ride, hi-hat, snare, toms, kick. Pure data and layout
## (tests use it directly); scripts/game/chart_stand.gd draws it on the music stand.
## Loaded with preload() by its users.

const LANES := ["crash", "ride", "hihat", "snare", "toms", "kick"]
const LABELS := ["CR", "RD", "HH", "SN", "TM", "BD"]
const SHEET := "drums.drums.txt"
const TEMPO := "drums.tempo.json"
## General MIDI drum notes -> lane (the sheet's `name` column is only a fallback).
const GM := {
	49: 0, 57: 0, 52: 0, 55: 0,                  # crash, crash 2, china, splash
	51: 1, 53: 1, 59: 1, 93: 1,                  # ride, bell, ride 2, ride edge
	42: 2, 44: 2, 46: 2, 92: 2,                  # hi-hat closed, pedal, open, half
	37: 3, 38: 3, 40: 3, 91: 3,                  # side stick, snare, electric, rimshot
	41: 4, 43: 4, 45: 4, 47: 4, 48: 4, 50: 4,    # toms
	35: 5, 36: 5,                                # kicks
}
const EPS := 1e-4   # s: a hit this close before a bar line belongs to the new bar

var bars: Array = []    # [{bar, start_s, bar_s, beats}], bar numbers from 1 in order
var hits: Array = []    # per bar (same index): [[fraction of the bar 0..1, lane], ...]
var end_s := 0.0


## Lane of a hit, or -1 for a sound that has none (maracas, claps...).
static func lane_of(midi: int, name: String) -> int:
	if GM.has(midi):
		return GM[midi]
	for i in LANES.size():
		if name.begins_with(LANES[i]) or (i == 0 and (name.begins_with("china") or name.begins_with("splash"))) \
				or (i == 4 and name.begins_with("tom")):
			return i
	return -1


## The chart from a song directory (SongCache's), or null when its files are missing or bad.
static func load_dir(dir: String) -> Variant:
	var sheet := dir.path_join(SHEET)
	var tempo := dir.path_join(TEMPO)
	if not FileAccess.file_exists(sheet) or not FileAccess.file_exists(tempo):
		return null
	var t = JSON.parse_string(FileAccess.get_file_as_string(tempo))
	return parse(FileAccess.get_file_as_string(sheet), t if t is Dictionary else {})


## The chart from the two files' text, or null when the tempo map has no bars.
static func parse(sheet: String, tempo: Dictionary) -> Variant:
	var c = new()
	for b in tempo.get("bars", []):
		if not b is Dictionary or not typeof(b.get("start_s")) in [TYPE_INT, TYPE_FLOAT] \
				or not typeof(b.get("bar_s")) in [TYPE_INT, TYPE_FLOAT] or float(b["bar_s"]) <= 0.0:
			continue
		c.bars.append({"bar": c.bars.size() + 1, "start_s": float(b["start_s"]), "bar_s": float(b["bar_s"]),
			"beats": maxi(1, int(b.get("numerator", 4)))})
		c.hits.append([])
	if c.bars.is_empty():
		return null
	var last: Dictionary = c.bars[-1]
	c.end_s = float(tempo.get("end_s", last["start_s"] + last["bar_s"]))
	for line in sheet.split("\n", false):
		line = line.strip_edges()
		if line.begins_with("#") or line == "":
			continue
		var f := line.split_floats(" ", false) if not "\t" in line else line.split_floats("\t", false)
		var parts := line.split(" ", false) if not "\t" in line else line.split("\t", false)
		if f.size() < 2:
			continue
		var lane := lane_of(int(f[1]), parts[2] if parts.size() > 2 else "")
		var i: int = c.bar_index(f[0])
		if lane < 0 or i < 0:
			continue
		var b: Dictionary = c.bars[i]
		c.hits[i].append([clampf((f[0] - b["start_s"]) / b["bar_s"], 0.0, 0.999), lane])
	return c


## Index into `bars` of the bar playing at song time t, -1 before bar 1 or after the last.
func bar_index(t: float) -> int:
	t += EPS
	if bars.is_empty() or t < bars[0]["start_s"]:
		return -1
	var lo := 0
	var hi := bars.size() - 1
	while lo < hi:   # last bar starting at or before t
		var mid := (lo + hi + 1) / 2
		if bars[mid]["start_s"] <= t:
			lo = mid
		else:
			hi = mid - 1
	var b: Dictionary = bars[lo]
	if lo == bars.size() - 1 and t >= b["start_s"] + b["bar_s"] + EPS:
		return -1   # past the last bar line
	return lo


## The bar number at song time t, clamped to the song (1 before it starts, the last after).
func bar_at(t: float) -> int:
	var i := bar_index(t)
	if i >= 0:
		return i + 1
	return 1 if bars.is_empty() or t < bars[0]["start_s"] else bars.size()


## How far through its bar song time t is, 0..1.
func fraction(t: float) -> float:
	var b: Dictionary = bars[bar_at(t) - 1]
	return clampf((t - b["start_s"]) / b["bar_s"], 0.0, 1.0)


func bar_count() -> int:
	return bars.size()


func beats(bar: int) -> int:
	return bars[bar - 1]["beats"] if bar >= 1 and bar <= bars.size() else 4


func hits_in(bar: int) -> Array:
	return hits[bar - 1] if bar >= 1 and bar <= bars.size() else []


## The bars on a page of `count` slots while bar `current` plays. Each slot keeps its bar until
## the playhead has passed it, then turns to the bar `count` later, so the eye always has the
## rest of the page to read ahead and nothing jumps under it.
static func slots(current: int, count := 4) -> Array:
	var s := posmod(current - 1, count)
	var out := []
	for i in count:
		out.append(current - s + i + (count if i < s else 0))
	return out
