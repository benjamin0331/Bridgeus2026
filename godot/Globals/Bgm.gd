extends Node
# 背景音樂：整個遊戲一首、無限循環。語音通話期間靜音，掛斷後恢復。
#
# 為什麼是 autoload 而不是放在 Game.tscn 裡：音樂要跨場景活著，而且 VoiceChat 需要
# 一個穩定的對象可以叫（見下方 mute 的說明）。跟 Backend / VoiceChat 同一個模式。

const TRACK := "res://Assets/Audio/retro-bgm-chan-home-at-night-516298.mp3"
const VOLUME_DB := -14.0    # 背景音樂要墊在腳步/語音底下，實機覺得吵就往下調
const SILENT_DB := -80.0    # Godot 的實質靜音

var _player: AudioStreamPlayer = null
var _muted := false

func _ready() -> void:
	# headless（常駐 dedicated server）不需要也不該播音樂，它根本沒有玩家。
	if DisplayServer.get_name() == "headless":
		return

	# 用 load() 而不是 preload()：preload 是解析期就要拿到資源，檔案還沒被 Godot
	# 匯入過（沒有 .import）時整支腳本會直接 parse error。load() 只會回 null，
	# 遊戲照跑。
	var stream = load(TRACK)
	if stream == null:
		push_warning("BGM 載入失敗：%s（在編輯器開一次專案讓 Godot 匯入 mp3）" % TRACK)
		return
	# mp3 匯入預設不循環，這裡直接在程式裡開，免得依賴 .import 的設定被誰改掉。
	if "loop" in stream:
		stream.loop = true

	_player = AudioStreamPlayer.new()
	_player.name = "BgmPlayer"
	_player.stream = stream
	_player.volume_db = VOLUME_DB
	_player.bus = "Master"
	add_child(_player)
	# Web 匯出時瀏覽器會擋自動播放，AudioContext 要等第一次使用者互動才解鎖。
	# 這裡照常 play()，玩家按下 Host/Join 的那一刻就會有聲音，不必特別處理。
	_player.play()

# 語音通話期間靜音。由 VoiceChat.start()/stop() 呼叫 —— 那是語音唯一的開關點，
# 掛在那裡就不可能有某條路徑忘記還原音量。
func set_muted(muted: bool) -> void:
	if _player == null or _muted == muted:
		return
	_muted = muted
	_player.volume_db = SILENT_DB if muted else VOLUME_DB
