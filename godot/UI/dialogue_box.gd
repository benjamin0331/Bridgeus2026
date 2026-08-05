extends CanvasLayer
# 多句對話框：NPC 呼叫 start_dialogue() 帶入整段對話，玩家點擊/空白鍵逐句切換。
# 本地限定 UI（教學性質），不走多人連線。

@onready var _panel: PanelContainer = $PanelContainer
@onready var _label: RichTextLabel = $PanelContainer/MarginContainer/RichTextLabel

const TYPE_SPEED := 0.04   # 打字機：每個字露出的間隔秒數

var dialogue_lines: Array[String] = []
var current_line_index: int = 0
var _typing_tween: Tween = null

func _ready() -> void:
	add_to_group("dialogue")   # 讓 NPC 用 group 找到我
	_panel.hide()

# 對話中要鎖住玩家移動（player_00.gd 每幀問 game_ui.blocks_movement()，那邊會轉問這裡）。
func is_open() -> bool:
	return _panel != null and _panel.visible

# 由 NPC 傳入多句對話，從第一句開始顯示。
func start_dialogue(lines: Array[String]) -> void:
	if lines.is_empty():
		return
	dialogue_lines = lines
	current_line_index = 0
	_panel.show()
	_show_line(dialogue_lines[0])

# 用 _input（在 GUI 之前攔）而非 _unhandled_input：對話框面板 mouse_filter=STOP 會先
# 把點擊吃掉，_unhandled_input 就收不到，導致點在框上無法切換。
func _input(event: InputEvent) -> void:
	if not _panel.visible:
		return
	if not _is_advance(event):
		return
	get_viewport().set_input_as_handled()
	# 還在打字 → 先把整句補完，不前進。
	if _typing_tween and _typing_tween.is_running():
		_finish_typing()
		return
	# 已顯示完整 → 前進；沒有下一句就收起對話框。
	current_line_index += 1
	if current_line_index < dialogue_lines.size():
		_show_line(dialogue_lines[current_line_index])
	else:
		_panel.hide()

func _is_advance(event: InputEvent) -> bool:
	if event is InputEventMouseButton:
		return event.pressed and event.button_index == MOUSE_BUTTON_LEFT
	if event is InputEventKey:
		return event.pressed and not event.echo and event.keycode == KEY_SPACE
	return false

# 打字機：設好整句文字，再用 visible_ratio 從 0→1 逐字露出。
# 用 visible_ratio 而非 visible_characters：後者要靠 get_total_character_count()，
# 但剛設 text 的同一幀 RichTextLabel 還沒排版好、會回 0，導致整句都不顯示。
func _show_line(line: String) -> void:
	if _typing_tween and _typing_tween.is_running():
		_typing_tween.kill()
	_label.text = line
	_label.visible_ratio = 0.0
	_typing_tween = create_tween()
	_typing_tween.tween_property(_label, "visible_ratio", 1.0, maxi(line.length(), 1) * TYPE_SPEED)

func _finish_typing() -> void:
	if _typing_tween:
		_typing_tween.kill()
	_label.visible_ratio = 1.0
