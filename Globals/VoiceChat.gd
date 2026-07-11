extends Node
# 語音通話的本地音訊 DSP（autoload，每個 peer 單例）。與網路解耦：玩家節點負責把
# get_captured_frames() 的封包用 RPC 送出、收到再丟回 play_frames()。
# 麥克風 → VoiceMic bus →（依滑桿）AudioEffectPitchShift → AudioEffectCapture（抓變調後 PCM）。

const GEN_BUFFER := 0.1   # ponytail: 播放緩衝秒數；破音/延遲時實機微調

var _pitch_value := 1.0
var _bus_idx := -1
var _pitch: AudioEffectPitchShift = null
var _capture: AudioEffectCapture = null
var _mic: AudioStreamPlayer = null
var _gen_player: AudioStreamPlayer = null
var _playback: AudioStreamGeneratorPlayback = null

func start() -> void:
	if _bus_idx != -1:
		return   # 已在通話中
	# 動態建 VoiceMic bus：pitch(0) → capture(1)
	_bus_idx = AudioServer.bus_count
	AudioServer.add_bus(_bus_idx)
	AudioServer.set_bus_name(_bus_idx, "VoiceMic")
	_pitch = AudioEffectPitchShift.new()
	AudioServer.add_bus_effect(_bus_idx, _pitch, 0)
	_capture = AudioEffectCapture.new()
	AudioServer.add_bus_effect(_bus_idx, _capture, 1)
	_pitch.pitch_scale = _pitch_value
	# 麥克風導進 bus；靜音避免聽到自己（capture 在效果鏈中仍抓得到變調後音框）。
	_mic = AudioStreamPlayer.new()
	_mic.stream = AudioStreamMicrophone.new()
	_mic.bus = "VoiceMic"
	add_child(_mic)
	_mic.play()
	AudioServer.set_bus_mute(_bus_idx, true)
	# 播放對方聲音：AudioStreamGenerator → Master
	_gen_player = AudioStreamPlayer.new()
	var gen := AudioStreamGenerator.new()
	gen.mix_rate = AudioServer.get_mix_rate()
	gen.buffer_length = GEN_BUFFER
	_gen_player.stream = gen
	_gen_player.bus = "Master"
	add_child(_gen_player)
	_gen_player.play()
	_playback = _gen_player.get_stream_playback()

func set_voice_pitch(pitch_value: float) -> void:
	_pitch_value = pitch_value
	if _pitch:
		_pitch.pitch_scale = pitch_value

func get_captured_frames() -> PackedVector2Array:
	if _capture == null:
		return PackedVector2Array()
	var n := _capture.get_frames_available()
	if n == 0:
		return PackedVector2Array()
	return _capture.get_buffer(n)

func play_frames(frames: PackedVector2Array) -> void:
	if _playback == null:
		return
	var avail := _playback.get_frames_available()
	if avail <= 0:
		return
	if frames.size() <= avail:
		_playback.push_buffer(frames)
	else:
		_playback.push_buffer(frames.slice(0, avail))

func stop() -> void:
	if _mic:
		_mic.stop()
		_mic.queue_free()
		_mic = null
	if _gen_player:
		_gen_player.stop()
		_gen_player.queue_free()
		_gen_player = null
	_playback = null
	_capture = null
	_pitch = null
	if _bus_idx != -1:
		AudioServer.remove_bus(_bus_idx)
		_bus_idx = -1
