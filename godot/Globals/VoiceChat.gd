extends Node
# 語音通話的本地音訊 DSP（autoload）。麥克風 → VoiceMic bus →
#   PitchShift / Reverb / Distortion（UI 滑桿即時調）→ Capture(傳送) → SpectrumAnalyzer(UI 聲波)。
# Capture/Analyzer 固定在鏈尾：傳送與視覺化拿到的都是變聲後的音訊。與網路解耦。

const GEN_BUFFER := 0.1   # ponytail: 播放緩衝秒數；破音/延遲時實機微調

var _bus_idx := -1
var _pitch: AudioEffectPitchShift = null
var _capture: AudioEffectCapture = null
var _analyzer: AudioEffectSpectrumAnalyzerInstance = null
var _mic: AudioStreamPlayer = null
var _gen_player: AudioStreamPlayer = null
var _playback: AudioStreamGeneratorPlayback = null

func start() -> void:
	if _bus_idx != -1:
		return
	# 動態建 VoiceMic bus
	_bus_idx = AudioServer.bus_count
	AudioServer.add_bus(_bus_idx)
	AudioServer.set_bus_name(_bus_idx, "VoiceMic")
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
	# 可調音高（預設 1.0＝原音），鏈尾固定 Capture → SpectrumAnalyzer。
	_pitch = AudioEffectPitchShift.new()
	_pitch.pitch_scale = 1.0
	AudioServer.add_bus_effect(_bus_idx, _pitch)
	_capture = AudioEffectCapture.new()
	AudioServer.add_bus_effect(_bus_idx, _capture)
	AudioServer.add_bus_effect(_bus_idx, AudioEffectSpectrumAnalyzer.new())
	var cnt := AudioServer.get_bus_effect_count(_bus_idx)
	_analyzer = AudioServer.get_bus_effect_instance(_bus_idx, cnt - 1) as AudioEffectSpectrumAnalyzerInstance

# --- 滑桿即時調整 ---------------------------------------------------------
func set_pitch(v: float) -> void:      # 0.7 低沉 ~ 1.5 尖細（收窄範圍以保持可聽懂）
	if _pitch:
		_pitch.pitch_scale = v

# UI 用：某頻段的能量強度（複合波幅）。
func get_spectrum(from_hz: float, to_hz: float) -> float:
	if _analyzer:
		return _analyzer.get_magnitude_for_frequency_range(from_hz, to_hz).length()
	return 0.0

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
	_analyzer = null
	_pitch = null
	if _bus_idx != -1:
		AudioServer.remove_bus(_bus_idx)
		_bus_idx = -1
