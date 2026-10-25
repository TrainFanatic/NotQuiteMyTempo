extends Control
## Records one continuous take from AudioStreamMicrophone via AudioEffectCapture.
## Stores stereo float32 PCM in a WAV so the desktop preparation tool can downmix it.

const RECORD_AUDIO := "android.permission.RECORD_AUDIO"
const TAKE_ROOT := "user://takes"
const CHANNELS := 2
const BYTES_PER_FRAME := CHANNELS * 4
const MAX_DATA_BYTES := 0xFFFFFF00  # RIFF/WAV stores the data size in 32 bits.

var mic: AudioStreamPlayer
var capture: AudioEffectCapture
var wav: FileAccess
var sample_rate: int = 0
var frames_written: int = 0
var near_clip_samples: int = 0
var peak: float = 0.0
var discarded_at_start: int = 0
var started_unix_s: float = 0.0
var take_dir: String = ""
var take_id: String = ""
var recording: bool = false
var write_failed: bool = false
var current_export_source: String = ""

var session_input: LineEdit
var player_input: LineEdit
var kit_input: LineEdit
var room_input: LineEdit
var take_picker: OptionButton
var start_button: Button
var stop_button: Button
var export_wav_button: Button
var export_json_button: Button
var status_label: Label
var export_dialog: FileDialog


func _ready() -> void:
	_build_ui()
	var bus_index := AudioServer.get_bus_index(&"QuestMic")
	if bus_index < 0:
		_set_status("Missing QuestMic bus. Check default_bus_layout.tres.")
		return
	capture = AudioServer.get_bus_effect(bus_index, 0) as AudioEffectCapture
	if capture == null:
		_set_status("Missing AudioEffectCapture on the QuestMic bus.")
		return
	mic = AudioStreamPlayer.new()
	mic.stream = AudioStreamMicrophone.new()
	mic.bus = &"QuestMic"
	add_child(mic)
	_refresh_saved_takes()
	if OS.get_name() == "Android":
		OS.request_permission(RECORD_AUDIO)
	_set_status("Grant microphone access, then tap Start. This records mic audio only.")


func _process(_delta: float) -> void:
	if capture == null:
		return
	if OS.get_name() == "Android" and not OS.get_granted_permissions().has(RECORD_AUDIO):
		if recording:
			_stop_recording("Microphone permission was removed")
		return
	if not mic.playing:
		mic.play()
	if recording:
		_drain_capture()
		if write_failed:
			_stop_recording("WAV write failed")


func _notification(what: int) -> void:
	if what == NOTIFICATION_APPLICATION_PAUSED and recording:
		_stop_recording("App paused")
	elif what == NOTIFICATION_WM_CLOSE_REQUEST and recording:
		_stop_recording("App closed")


func _build_ui() -> void:
	var outer := MarginContainer.new()
	outer.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	outer.add_theme_constant_override("margin_left", 36)
	outer.add_theme_constant_override("margin_right", 36)
	outer.add_theme_constant_override("margin_top", 22)
	outer.add_theme_constant_override("margin_bottom", 22)
	add_child(outer)

	var column := VBoxContainer.new()
	column.add_theme_constant_override("separation", 14)
	outer.add_child(column)

	var title := Label.new()
	title.text = "Quest Drum Training Recorder"
	title.add_theme_font_size_override("font_size", 30)
	column.add_child(title)
	var info := Label.new()
	info.text = "Mic-only WAV • no backing track • stop before exporting • keep the original"
	column.add_child(info)

	session_input = _add_field(column, "Take name (e.g. quest_train_01)", "quest_smoke_01")
	player_input = _add_field(column, "Player ID", "player_1")
	kit_input = _add_field(column, "Kit ID", "kit_1")
	room_input = _add_field(column, "Room / mic setting notes", "")

	var controls := HBoxContainer.new()
	controls.add_theme_constant_override("separation", 16)
	column.add_child(controls)
	start_button = _add_button(controls, "Start new take", _start_recording)
	stop_button = _add_button(controls, "Stop and save", func(): _stop_recording("Stopped by player"))
	stop_button.disabled = true
	_add_button(controls, "Request mic permission", func(): OS.request_permission(RECORD_AUDIO))

	var saved := Label.new()
	saved.text = "Saved takes (select one to export):"
	column.add_child(saved)
	take_picker = OptionButton.new()
	take_picker.custom_minimum_size = Vector2(0, 55)
	column.add_child(take_picker)

	var exports := HBoxContainer.new()
	exports.add_theme_constant_override("separation", 16)
	column.add_child(exports)
	export_wav_button = _add_button(exports, "Export WAV", func(): _choose_export("source.wav"))
	export_json_button = _add_button(exports, "Export metadata", func(): _choose_export("source.capture.json"))
	_update_export_buttons()

	status_label = Label.new()
	status_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	status_label.custom_minimum_size = Vector2(0, 100)
	column.add_child(status_label)

	export_dialog = FileDialog.new()
	export_dialog.access = FileDialog.ACCESS_FILESYSTEM
	export_dialog.file_mode = FileDialog.FILE_MODE_SAVE_FILE
	export_dialog.use_native_dialog = true  # Android 10+ uses the system file picker (SAF).
	export_dialog.file_selected.connect(_export_selected)
	add_child(export_dialog)


func _add_field(parent: VBoxContainer, label_text: String, initial: String) -> LineEdit:
	var row := HBoxContainer.new()
	parent.add_child(row)
	var label := Label.new()
	label.text = label_text
	label.custom_minimum_size = Vector2(325, 50)
	row.add_child(label)
	var field := LineEdit.new()
	field.text = initial
	field.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(field)
	return field


func _add_button(parent: HBoxContainer, text: String, handler: Callable) -> Button:
	var button := Button.new()
	button.text = text
	button.custom_minimum_size = Vector2(215, 65)
	button.pressed.connect(handler)
	parent.add_child(button)
	return button


func _start_recording() -> void:
	if recording or capture == null:
		return
	if OS.get_name() == "Android" and not OS.get_granted_permissions().has(RECORD_AUDIO):
		OS.request_permission(RECORD_AUDIO)
		_set_status("Grant microphone permission, then tap Start again.")
		return
	if not mic.playing:
		mic.play()

	var requested := session_input.text.strip_edges()
	var safe := ""
	for ch in requested:
		if "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-".contains(ch):
			safe += ch
	if safe.is_empty():
		safe = "take"
	take_id = safe + "_" + str(int(Time.get_unix_time_from_system())) + "_" + str(Time.get_ticks_usec())
	take_dir = TAKE_ROOT.path_join(take_id)
	var dir_error := DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path(take_dir))
	if dir_error != OK:
		_set_status("Cannot create take directory: error %d" % dir_error)
		return

	sample_rate = AudioServer.get_mix_rate()
	if sample_rate <= 0:
		_set_status("Audio mix rate is invalid.")
		return
	wav = FileAccess.open(take_dir.path_join("source.wav"), FileAccess.WRITE)
	if wav == null:
		_set_status("Cannot open WAV: error %d" % FileAccess.get_open_error())
		return
	_write_wav_header()
	capture.clear_buffer()
	discarded_at_start = capture.get_discarded_frames()
	frames_written = 0
	near_clip_samples = 0
	peak = 0.0
	write_failed = false
	started_unix_s = Time.get_unix_time_from_system()
	recording = true
	start_button.disabled = true
	stop_button.disabled = false
	_update_export_buttons()
	_set_status("Recording %s at %d Hz, stereo float32. Keep headset on." % [take_id, sample_rate])


func _write_wav_header() -> void:
	wav.store_buffer("RIFF".to_ascii_buffer())
	wav.store_32(0)
	wav.store_buffer("WAVEfmt ".to_ascii_buffer())
	wav.store_32(16)
	wav.store_16(3)  # IEEE float PCM
	wav.store_16(CHANNELS)
	wav.store_32(sample_rate)
	wav.store_32(sample_rate * BYTES_PER_FRAME)
	wav.store_16(BYTES_PER_FRAME)
	wav.store_16(32)
	wav.store_buffer("data".to_ascii_buffer())
	wav.store_32(0)


func _drain_capture() -> void:
	while capture.get_frames_available() > 0 and not write_failed:
		var count: int = min(capture.get_frames_available(), 2048)
		var block: PackedVector2Array = capture.get_buffer(count)
		if block.is_empty():
			break
		var bytes := block.to_byte_array()
		if bytes.size() != block.size() * BYTES_PER_FRAME:
			write_failed = true
			break
		if (frames_written + block.size()) * BYTES_PER_FRAME > MAX_DATA_BYTES:
			write_failed = true
			break
		if not wav.store_buffer(bytes):
			write_failed = true
			break
		for frame in block:
			peak = max(peak, max(abs(frame.x), abs(frame.y)))
			if abs(frame.x) >= 0.999:
				near_clip_samples += 1
			if abs(frame.y) >= 0.999:
				near_clip_samples += 1
		frames_written += block.size()


func _stop_recording(reason: String) -> void:
	if not recording:
		return
	if not write_failed:
		_drain_capture()
	recording = false
	var data_bytes := frames_written * BYTES_PER_FRAME
	wav.seek(4)
	wav.store_32(36 + data_bytes)
	wav.seek(40)
	wav.store_32(data_bytes)
	wav.flush()
	wav.close()
	wav = null
	var drops := capture.get_discarded_frames() - discarded_at_start
	var metadata := {
		"session_id": take_id,
		"player_id": player_input.text.strip_edges(),
		"kit_id": kit_input.text.strip_edges(),
		"room_and_settings": room_input.text.strip_edges(),
		"app": "NotQuiteMyTempo quest_recorder",
		"godot_version": Engine.get_version_info().get("string", "unknown"),
		"source": "AudioStreamMicrophone -> QuestMic bus -> AudioEffectCapture",
		"mic_only": true,
		"started_unix_s": started_unix_s,
		"stopped_unix_s": Time.get_unix_time_from_system(),
		"stop_reason": reason,
		"sample_rate": sample_rate,
		"channels": CHANNELS,
		"wav_format": "IEEE float32 PCM",
		"frames": frames_written,
		"duration_s": float(frames_written) / sample_rate,
		"peak": peak,
		"near_clip_fraction": float(near_clip_samples) / max(1, frames_written * CHANNELS),
		"discarded_frames": drops,
		"write_failed": write_failed,
		"valid_for_training": frames_written > 0 and drops == 0 and not write_failed and reason == "Stopped by player",
	}
	var info := FileAccess.open(take_dir.path_join("source.capture.json"), FileAccess.WRITE)
	if info != null:
		info.store_string(JSON.stringify(metadata, "  ") + "\n")
		info.close()
	start_button.disabled = false
	stop_button.disabled = true
	_refresh_saved_takes()
	if not metadata["valid_for_training"]:
		_set_status("Take saved but invalid (%s; %d frames, %d dropped). Retake before training." % [reason, frames_written, drops])
	else:
		_set_status("Saved %s (%.1f s, peak %.3f). Export both files." % [take_id, float(frames_written) / sample_rate, peak])


func _refresh_saved_takes() -> void:
	DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path(TAKE_ROOT))
	take_picker.clear()
	var dirs := DirAccess.get_directories_at(TAKE_ROOT)
	dirs.sort()
	for dir_name in dirs:
		var dir_path := TAKE_ROOT.path_join(dir_name)
		if FileAccess.file_exists(dir_path.path_join("source.wav")):
			take_picker.add_item(dir_name)
			take_picker.set_item_metadata(take_picker.item_count - 1, dir_path)
	if take_picker.item_count > 0:
		take_picker.select(take_picker.item_count - 1)
	_update_export_buttons()


func _update_export_buttons() -> void:
	if export_wav_button == null:
		return
	var available := not recording and take_picker.item_count > 0
	export_wav_button.disabled = not available
	export_json_button.disabled = not available


func _choose_export(filename: String) -> void:
	if recording or take_picker.item_count == 0:
		return
	var folder: String = take_picker.get_selected_metadata()
	current_export_source = folder.path_join(filename)
	if not FileAccess.file_exists(current_export_source):
		_set_status("Missing %s; stop the take first." % filename)
		return
	export_dialog.clear_filters()
	if filename.ends_with(".wav"):
		export_dialog.add_filter("*.wav", "WAV audio")
	else:
		export_dialog.add_filter("*.json", "Capture metadata")
	export_dialog.current_file = take_picker.get_item_text(take_picker.selected) + (".wav" if filename.ends_with(".wav") else ".capture.json")
	export_dialog.popup_centered(Vector2i(850, 600))


func _export_selected(destination: String) -> void:
	var source := FileAccess.open(current_export_source, FileAccess.READ)
	if source == null:
		_set_status("Cannot open source file for export.")
		return
	var target := FileAccess.open(destination, FileAccess.WRITE)
	if target == null:
		_set_status("Cannot open export destination. Try another folder.")
		return
	while not source.eof_reached():
		var bytes := source.get_buffer(1024 * 1024)
		if bytes.is_empty():
			break
		if not target.store_buffer(bytes):
			_set_status("Export failed while copying data. Try again.")
			source.close()
			target.close()
			return
	target.flush()
	target.close()
	source.close()
	_set_status("Exported %s. Export the matching second file too." % destination)


func _set_status(text: String) -> void:
	if status_label != null:
		status_label.text = text
	print(text)
