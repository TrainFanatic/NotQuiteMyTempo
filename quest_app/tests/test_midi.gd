extends TestCase
## C2: Standard MIDI file parsing and phrase extraction against generated fixtures.

const FIX := "res://tests/fixtures/"
var expected: Dictionary


func _init() -> void:
	expected = JSON.parse_string(FileAccess.get_file_as_string(FIX + "expected.json"))


func test_type0_running_status_and_tempo() -> void:
	var smf := SMFParser.parse_file(FIX + "type0_running_status.mid")
	var e: Dictionary = expected["type0"]
	check_eq(smf["format"], 0, "format")
	check_eq(smf["tracks"].size(), 1, "track count")
	var notes: Array = smf["tracks"][0]["notes"]
	check_eq(notes.size(), e["notes"], "note count (running status + velocity-0 note-offs)")
	for i in min(notes.size(), e["note_times"].size()):
		check_near(notes[i]["start"], e["note_times"][i][0], 1e-6, "note %d start" % i)
		check_near(notes[i]["end"], e["note_times"][i][1], 1e-6, "note %d end" % i)
	check_eq(smf["tempo_map"].size(), 2, "tempo changes")
	check_eq(smf["tempo_map"][1]["us_per_qn"], 400000, "second tempo is 150 BPM")
	check_near(SMFParser.bpm_at(3000, smf["tempo_map"]), 150.0, 1e-6, "bpm after the change")


func test_type1_tracks_and_names() -> void:
	var smf := SMFParser.parse_file(FIX + "type1_tracks.mid")
	var e: Dictionary = expected["type1"]
	check_eq(smf["format"], 1, "format")
	var names: Array = []
	for t in smf["tracks"]:
		names.append(t["name"])
	check_eq(names, e["names"], "track names")
	check_eq(smf["tracks"][1]["notes"].size(), e["notes"]["Trumpets"], "trumpet notes (0x8n note-offs, sysex + CC skipped)")
	check_eq(smf["tracks"][2]["notes"].size(), e["notes"]["Trombones"], "trombone notes")
	check_near(SMFParser.bpm_at(0, smf["tempo_map"]), e["bpm"], 1e-6, "tempo from the conductor track")


func test_phrases_match_expected() -> void:
	var prm: Dictionary = expected["params"]
	for fixture in [["type0_running_status.mid", "type0"], ["type1_tracks.mid", "type1"]]:
		var smf := SMFParser.parse_file(FIX + fixture[0])
		var got := PhraseExtractor.extract(smf, prm["rest_beats"], prm["raise_lead"], prm["lower_delay"])
		var want = expected[fixture[1]]["phrases"]
		if fixture[1] == "type0":
			want = {"track_0": want}
		for track in want:
			if not check(got.has(track), "%s has phrases for %s" % [fixture[0], track]):
				continue
			check_eq(got[track].size(), want[track].size(), "%s %s phrase count" % [fixture[0], track])
			for i in min(got[track].size(), want[track].size()):
				for k in ["start", "end", "raise_at", "lower_at"]:
					check_near(got[track][i][k], want[track][i][k], 1e-6, "%s %s phrase %d %s" % [fixture[0], track, i, k])


func test_state_at_walks_the_cycle() -> void:
	var ph := [{"start": 3.6, "end": 4.4, "raise_at": 3.0, "lower_at": 4.8}]
	check_eq(PhraseExtractor.state_at(ph, 1.0), &"resting", "before")
	check_eq(PhraseExtractor.state_at(ph, 3.2), &"raising", "raising")
	check_eq(PhraseExtractor.state_at(ph, 4.0), &"playing", "playing")
	check_eq(PhraseExtractor.state_at(ph, 4.6), &"playing", "still up just after the phrase")
	check_eq(PhraseExtractor.state_at(ph, 5.0), &"resting", "after")


func test_bad_file_fails_loudly() -> void:
	check(SMFParser.parse_file(FIX + "bad_header.mid").is_empty(), "non-MIDI file gives an empty result")
	var bytes := FileAccess.get_file_as_bytes(FIX + "type1_tracks.mid")
	check(SMFParser.parse(bytes.slice(0, bytes.size() - 20)).is_empty(), "truncated file gives an empty result")
