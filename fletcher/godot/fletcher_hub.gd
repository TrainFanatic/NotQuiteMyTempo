class_name FletcherHub
extends Node
## WebSocket client for the team hub (hub/server.py). Emits every frame as
## message_received(type, data, envelope); reconnects every second forever, so
## the game keeps running if the laptop or the hub drops.
##
## Envelope: {type, src, seq, t, data}. `t` is the laptop's monotonic clock;
## to_local_time() maps it onto this device's clock (Time.get_ticks_usec).

signal message_received(type: String, data: Dictionary, envelope: Dictionary)
signal connected
signal disconnected

@export var url := "ws://192.168.1.10:8765"  ## the laptop's LAN IP; the hub's default port is 8765
@export var src := "quest"
@export var reconnect_s := 1.0

var _ws := WebSocketPeer.new()
var _seq := 0
var _open := false
var _retry_at := 0.0
## local seconds - laptop seconds, the smallest seen: one-way network delay
## inflates every sample, so the minimum is the best estimate.
var clock_offset := INF


func _ready() -> void:
	_ws.inbound_buffer_size = 1 << 22  # audio chunks are base64 PCM
	_connect()


func _connect() -> void:
	_ws = WebSocketPeer.new()
	_ws.inbound_buffer_size = 1 << 22
	if _ws.connect_to_url(url) != OK:
		_retry_at = _now() + reconnect_s


func _process(_delta: float) -> void:
	if _retry_at > 0.0:
		if _now() >= _retry_at:
			_retry_at = 0.0
			_connect()
		return
	_ws.poll()
	match _ws.get_ready_state():
		WebSocketPeer.STATE_OPEN:
			if not _open:
				_open = true
				print("[FletcherHub] connected to ", url)
				connected.emit()
			while _ws.get_available_packet_count() > 0:
				_handle(_ws.get_packet().get_string_from_utf8())
		WebSocketPeer.STATE_CLOSED:
			if _open:
				_open = false
				push_warning("[FletcherHub] lost %s; retrying every %.0f s" % [url, reconnect_s])
				disconnected.emit()
			_retry_at = _now() + reconnect_s


func _handle(raw: String) -> void:
	var msg = JSON.parse_string(raw)
	if typeof(msg) != TYPE_DICTIONARY or typeof(msg.get("type")) != TYPE_STRING:
		return  # not an envelope: ignore, never crash
	var data = msg.get("data")
	if typeof(data) != TYPE_DICTIONARY:
		data = {}
	if typeof(msg.get("t")) in [TYPE_FLOAT, TYPE_INT]:
		clock_offset = minf(clock_offset, _now() - float(msg["t"]))
	message_received.emit(msg["type"], data, msg)


func is_open() -> bool:
	return _open


## Send a contract message. Dropped (returns false) while disconnected.
func send(type: String, data: Dictionary) -> bool:
	if not _open:
		return false
	_seq += 1
	var env := {"type": type, "src": src, "seq": _seq, "t": _now(), "data": data}
	return _ws.send_text(JSON.stringify(env)) == OK


## A laptop timestamp (envelope t, session t0) in this device's seconds.
func to_local_time(laptop_t: float) -> float:
	return laptop_t + (clock_offset if clock_offset != INF else 0.0)


static func _now() -> float:
	return Time.get_ticks_usec() / 1e6
