extends SceneTree

func _initialize() -> void:
	var recorder = load("res://recorder.gd").new()
	recorder.sample_rate = 48000
	var path := "res://../build/quest_recorder_wav_smoke.wav"
	recorder.wav = FileAccess.open(path, FileAccess.WRITE)
	assert(recorder.wav != null)
	recorder._write_wav_header()
	var samples := PackedVector2Array()
	samples.resize(4800)
	for i in range(samples.size()):
		var value := 0.25 * sin(TAU * 440.0 * float(i) / 48000.0)
		samples[i] = Vector2(value, value)
	var bytes := samples.to_byte_array()
	assert(bytes.size() == 4800 * recorder.BYTES_PER_FRAME)
	assert(recorder.wav.store_buffer(bytes))
	recorder.wav.seek(4)
	recorder.wav.store_32(36 + bytes.size())
	recorder.wav.seek(40)
	recorder.wav.store_32(bytes.size())
	recorder.wav.close()
	print("WAV smoke file: ", ProjectSettings.globalize_path(path))
	recorder.free()
	quit()
