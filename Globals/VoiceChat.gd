extends Node
# 語音通話的本地音訊 DSP（autoload，每個 peer 單例）。與網路解耦：玩家節點負責把
# get_captured_frames() 的封包用 RPC 送出、收到再丟回 play_frames()。
#
# VoiceMic bus 效果鏈（切換預設時動態重建）：
#   [預設變聲效果…] → AudioEffectCapture(傳送) → AudioEffectSpectrumAnalyzer(UI 視覺)
# Capture/Analyzer 永遠固定在鏈尾：確保「加工完成 → 發射傳輸 → UI 視覺化」都拿到變聲後的音訊。

enum VoiceFilter { ANONYMOUS, CYBER_PUNK, RADIO_COMMS, GHOST }

const GEN_BUFFER := 0.1          # ponytail: 播放緩衝秒數；破音/延遲時實機微調
const ANTI_TRACK_INTERVAL := 3.0 # 防聲紋：ANONYMOUS 每隔幾秒微幅隨機飄移音高

var current_filter := VoiceFilter.ANONYMOUS
var _bus_idx := -1
var _capture: AudioEffectCapture = null
var _analyzer: AudioEffectSpectrumAnalyzerInstance = null
var _mic: AudioStreamPlayer = null
var _gen_player: AudioStreamPlayer = null
var _playback: AudioStreamGeneratorPlayback = null
var _anti_timer: Timer = null

func _ready() -> void:
	_anti_timer = Timer.new()
	_anti_timer.wait_time = ANTI_TRACK_INTERVAL
	_anti_timer.timeout.connect(_on_anti_tracking_timeout)
	add_child(_anti_timer)

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
	# 一開始就套用匿名防聲紋預設（無原音選項）。
	apply_voice_filter(VoiceFilter.ANONYMOUS)

# 切換變聲預設：清空並重建 VoiceMic 的效果鏈。
func apply_voice_filter(type: int) -> void:
	current_filter = type
	if _anti_timer:
		_anti_timer.stop()
	if _bus_idx == -1:
		return
	AudioServer.clear_bus_effects(_bus_idx)
	match type:
		VoiceFilter.ANONYMOUS:
			var ps := AudioEffectPitchShift.new()
			ps.pitch_scale = 0.65
			ps.fft_size = AudioEffectPitchShift.FFT_SIZE_2048
			AudioServer.add_bus_effect(_bus_idx, ps)
			if _anti_timer:
				_anti_timer.start()   # 啟動防聲紋定時隨機微調
		VoiceFilter.CYBER_PUNK:
			var ch := AudioEffectChorus.new()
			ch.voice_count = 4
			ch.dry = 0.0
			ch.set("voice/1/delay_ms", 15.0)
			ch.set("voice/2/delay_ms", 18.0)
			ch.set("voice/3/delay_ms", 22.0)
			ch.set("voice/4/delay_ms", 25.0)
			AudioServer.add_bus_effect(_bus_idx, ch)
			var ds := AudioEffectDistortion.new()
			ds.mode = AudioEffectDistortion.MODE_LOFI
			ds.drive = 0.15
			AudioServer.add_bus_effect(_bus_idx, ds)
		VoiceFilter.RADIO_COMMS:
			var bp := AudioEffectBandPassFilter.new()
			bp.cutoff_hz = 1500
			bp.resonance = 0.5
			AudioServer.add_bus_effect(_bus_idx, bp)
			var lm := AudioEffectLimiter.new()
			lm.ceiling_db = -3.0
			AudioServer.add_bus_effect(_bus_idx, lm)
		VoiceFilter.GHOST:
			var rv := AudioEffectReverb.new()
			rv.room_size = 0.85
			rv.damping = 0.2
			rv.wet = 0.6
			rv.dry = 0.5
			AudioServer.add_bus_effect(_bus_idx, rv)
			var ps2 := AudioEffectPitchShift.new()
			ps2.pitch_scale = 0.8
			AudioServer.add_bus_effect(_bus_idx, ps2)
	# 固定鏈尾：Capture（傳送）→ SpectrumAnalyzer（UI 數據）
	_capture = AudioEffectCapture.new()
	AudioServer.add_bus_effect(_bus_idx, _capture)
	AudioServer.add_bus_effect(_bus_idx, AudioEffectSpectrumAnalyzer.new())
	# 動態重新取得執行時的分析儀 instance 才能讀頻譜。
	var cnt := AudioServer.get_bus_effect_count(_bus_idx)
	_analyzer = AudioServer.get_bus_effect_instance(_bus_idx, cnt - 1) as AudioEffectSpectrumAnalyzerInstance

# UI 用：某頻段的能量強度（複合波幅）。
func get_spectrum(from_hz: float, to_hz: float) -> float:
	if _analyzer:
		return _analyzer.get_magnitude_for_frequency_range(from_hz, to_hz).length()
	return 0.0

# 防聲紋：只在 ANONYMOUS 時，把第一個效果（PitchShift）的音高隨機飄移，混淆 AI 聲紋比對。
func _on_anti_tracking_timeout() -> void:
	if current_filter == VoiceFilter.ANONYMOUS and _bus_idx != -1:
		var ps = AudioServer.get_bus_effect(_bus_idx, 0)
		if ps is AudioEffectPitchShift:
			ps.pitch_scale = randf_range(0.6, 0.8)

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
	if _anti_timer:
		_anti_timer.stop()
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
	if _bus_idx != -1:
		AudioServer.remove_bus(_bus_idx)
		_bus_idx = -1
