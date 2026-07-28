extends CharacterBody2D
# 教學青蛙：本地玩家靠近 → 青蛙頭上冒出「請與我交談呱」按鈕 → 點了就開始多句教學對話。
# 全程本地（每個 peer 各自判斷自己的玩家），不走多人連線。

const TUTORIAL_LINES: Array[String] = [
	"呱！歡迎來到 Godot 的世界，你可以在此透過與他人互動來瞭解更多不在開放討論區的議題呱",
	"左上角的提交議題按鈕可輸入你想發表的議題與看法，提交後其他人就能跟你互動、看到你的想法咯呱！",
	"當然想進一步跟對方探討議題內容的話也可跟他發起聊天，同時其他人也可能向你發起聊天，如果不想進一步交流的話按下拒絕聊天邀請就行了呱",
	"除了打字聊天也有提供語音功能呱，為了保護隱私可以拖動界面拉桿調整聲調哦呱",
	"提交議題下方的按鈕可選擇顯示頭銜呱，也能自訂顏色，如果還沒有頭銜，快回主世界探索來解鎖成就獲得頭銜吧呱！",
	"在地圖左上角有篝火區域，按下目前開放中的主題按鈕就可以坐在樹幹上等待，有其他人也來配對的話你們就能一起傳送回聊天室聊天咯呱",
	"探索與互動的同時別忘了禮貌，一起促進友好的交流呱，跟別人吵架的話功德會-1-1呱",
	"呱？你問我為什麼我是一隻青蛙？",
	"因為我們的專題伺服器頭像是一隻青蛙呱！",
]

@onready var _area: Area2D = $Area2D
@onready var _prompt: Control = $TalkPrompt          # 世界空間容器，scale 放這層（見 npc_frog.tscn）
@onready var _talk_btn: Button = $TalkPrompt/Button  # 實際按鈕，不自帶 scale → 點擊命中正常

func _ready() -> void:
	_area.collision_mask = 2   # 玩家在 layer 2（同 player_00 的靠近偵測）
	_area.body_entered.connect(_on_body_entered)
	_area.body_exited.connect(_on_body_exited)
	_talk_btn.pressed.connect(_on_talk_pressed)
	_prompt.hide()

func _on_body_entered(body: Node) -> void:
	# 只對「本地玩家」跳提示（青蛙在每個 peer 各自判斷自己的玩家）。
	if body.is_in_group("players") and body.is_multiplayer_authority():
		_prompt.show()

func _on_body_exited(body: Node) -> void:
	if body.is_in_group("players") and body.is_multiplayer_authority():
		_prompt.hide()

func _on_talk_pressed() -> void:
	var dm = get_tree().get_first_node_in_group("dialogue")
	if dm:
		dm.start_dialogue(_lines())

# 子類（如 npc_frog2）覆寫這個換自己的台詞，其餘行為沿用。
func _lines() -> Array[String]:
	return TUTORIAL_LINES
