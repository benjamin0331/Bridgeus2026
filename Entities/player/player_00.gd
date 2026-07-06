extends CharacterBody2D

const SPEED = 150.0
const PROXIMITY_RADIUS = 40.0
const BUBBLE_SCALE = 0.5   # counters the 2x camera zoom so the bubble font stays crisp
const BUBBLE_Y = -32       # ponytail: bubble height above origin; tune to sit just over the head (sprite frame is 144px tall, head ~ -10)
const BANNER_GAP = 2       # ponytail: 頭銜被頂到議題泡泡上方時，兩框之間的間距（local 單位）
@onready var camera = $Camera2D

# Issue this player has submitted. Replicated to every peer via apply_issue().
# stance/emotion reserved for later backend analysis; unused for now.
var issue_title := ""
var issue_body := ""

# 頭銜 (banner)：像議題泡泡一樣廣播到每個 peer。文字來自後端 API（見 game.gd）。
var banner_text := ""
var banner_color := Color(0.15, 0.15, 0.15, 0.85)   # 預設同泡泡底色

var _bubble_box: PanelContainer
var _bubble: Label
var _banner_box: PanelContainer
var _banner: Label
var _banner_sb: StyleBoxFlat
var _nearby: Array = []   # other player nodes currently in range (authority only)

func _enter_tree():
	# Name carries the network id (set by the spawner). Owner = authority.
	var node_id = name.to_int()
	if node_id > 0:
		set_multiplayer_authority(node_id)

func _ready():
	add_to_group("players")
	# Players pass through each other (own layer 2) but still collide with the
	# environment (layer 1). Without this, walking into another player triggers
	# move_and_slide overlap-recovery and shoves the other player along.
	collision_layer = 2
	collision_mask = 1
	_make_bubble()
	_make_banner()
	if not is_multiplayer_authority():
		if camera:
			camera.enabled = false
		return
	# Only the locally controlled player needs proximity detection + UI.
	_make_proximity_area()

func _physics_process(_delta):
	if not is_multiplayer_authority():
		return

	# Don't drive movement with WASD while the user is typing in a UI field.
	var focus = get_viewport().gui_get_focus_owner()
	if focus is LineEdit or focus is TextEdit:
		velocity = Vector2.ZERO
		move_and_slide()
		return

	var direction = Input.get_vector("left", "right", "up", "down")
	if direction:
		velocity = direction * SPEED
	else:
		velocity = velocity.move_toward(Vector2.ZERO, SPEED)
	move_and_slide()

# --- visuals --------------------------------------------------------------
func _make_bubble():
	# Lives in world space under the player, so the 2x camera zoom doubles it —
	# keep the font small. Gray rounded box + text, like the UI panels.
	_bubble_box = PanelContainer.new()
	var sb = StyleBoxFlat.new()
	sb.bg_color = Color(0.15, 0.15, 0.15, 0.85)
	sb.set_corner_radius_all(3)
	sb.set_content_margin_all(4)
	_bubble_box.add_theme_stylebox_override("panel", sb)
	_bubble = Label.new()
	_bubble.add_theme_font_size_override("font_size", 18)
	_bubble.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_bubble_box.add_child(_bubble)
	# Rasterize the font at 18px then scale the whole box down by 0.5; the 2x
	# camera zoom brings it back to 18px on screen → 1:1, no blur.
	_bubble_box.scale = Vector2(BUBBLE_SCALE, BUBBLE_SCALE)
	# 永遠畫在地圖最上層，蓋過樹等場景物件；用絕對 z 不受父節點影響。
	_bubble_box.z_index = RenderingServer.CANVAS_ITEM_Z_MAX
	_bubble_box.z_as_relative = false
	_bubble_box.visible = false
	add_child(_bubble_box)

func _make_banner():
	# 頭銜框：做法同頭頂泡泡（世界空間、抗 2x 縮放），但保留 StyleBox 參考好即時改色。
	_banner_box = PanelContainer.new()
	_banner_sb = StyleBoxFlat.new()
	_banner_sb.bg_color = banner_color
	_banner_sb.set_corner_radius_all(3)
	_banner_sb.set_content_margin_all(4)
	_banner_box.add_theme_stylebox_override("panel", _banner_sb)
	_banner = Label.new()
	_banner.add_theme_font_size_override("font_size", 18)
	_banner.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_banner_box.add_child(_banner)
	_banner_box.scale = Vector2(BUBBLE_SCALE, BUBBLE_SCALE)
	_banner_box.z_index = RenderingServer.CANVAS_ITEM_Z_MAX
	_banner_box.z_as_relative = false
	_banner_box.visible = false
	add_child(_banner_box)

func _make_proximity_area():
	var area = Area2D.new()
	area.collision_layer = 0
	area.collision_mask = 2   # detect player bodies (layer 2); group-filtered in the callback
	var shape = CollisionShape2D.new()
	var circle = CircleShape2D.new()
	circle.radius = PROXIMITY_RADIUS
	shape.shape = circle
	area.add_child(shape)
	add_child(area)
	area.body_entered.connect(_on_body_entered)
	area.body_exited.connect(_on_body_exited)

func _ui():
	return get_tree().get_first_node_in_group("issue_ui")

func _find_player(id: int):
	return get_parent().get_node_or_null(str(id))

# --- proximity (authority only) -------------------------------------------
func _on_body_entered(body):
	if body == self or not body.is_in_group("players"):
		return
	if not _nearby.has(body):
		_nearby.append(body)
	_refresh_menu()

func _on_body_exited(body):
	_nearby.erase(body)
	_refresh_menu()

func _refresh_menu():
	var ui = _ui()
	if not ui:
		return
	var target = _nearby[0] if _nearby.size() > 0 else null
	ui.show_interaction(target)

# --- issue submit (authority broadcasts to everyone) ----------------------
func submit_issue(title: String, body: String) -> void:
	apply_issue.rpc(title, body)
	# 後端 POST 由 UI 的 _submit_form() 發出（callback 在那邊跳成功/失敗提示）。

@rpc("authority", "call_local", "reliable")
func apply_issue(title: String, body: String):
	issue_title = title
	issue_body = body
	_bubble.text = title
	_bubble_box.visible = title != ""
	_bubble_box.reset_size()
	_bubble_box.position = Vector2(-_bubble_box.size.x * BUBBLE_SCALE / 2.0, BUBBLE_Y)
	# 議題有無會影響頭銜要不要被頂上去 → 重算頭銜位置。
	_reposition_banner()
	# A nearby peer's menu was rendered before this issue arrived — refresh it so
	# "（對方尚未提交議題）" updates to the real title in real time.
	var ui = _ui()
	if ui:
		ui.refresh_menu()

# --- banner 頭銜 (authority broadcasts to everyone) -----------------------
# 本地選了頭銜或改了顏色 → 廣播給所有 peer。
func set_banner(text: String, color: Color) -> void:
	apply_banner.rpc(text, color)

@rpc("authority", "call_local", "reliable")
func apply_banner(text: String, color: Color):
	banner_text = text
	banner_color = color
	_banner.text = text
	_banner_sb.bg_color = color
	_banner_box.visible = text != ""
	_reposition_banner()

# 頭銜位置：沒議題時佔議題泡泡原本的位置(BUBBLE_Y)；有議題時被頂到泡泡正上方。
func _reposition_banner():
	_banner_box.reset_size()
	var x = -_banner_box.size.x * BUBBLE_SCALE / 2.0
	var y = BUBBLE_Y
	if _bubble_box.visible:
		y -= _banner_box.size.y * BUBBLE_SCALE + BANNER_GAP
	_banner_box.position = Vector2(x, y)

# --- chat invite handshake ------------------------------------------------
# Local: ask `target` to chat. Runs receive_invite on the target's own player.
func invite_to_chat(target):
	var target_id = target.name.to_int()
	target.receive_invite.rpc_id(target_id, multiplayer.get_unique_id())

@rpc("any_peer", "reliable")
func receive_invite(from_id: int):
	var ui = _ui()
	if ui:
		ui.show_invite(from_id)

# Local (target side): answer an invite from `from_id`.
func respond_invite(from_id: int, accepted: bool):
	var inviter = _find_player(from_id)
	if inviter:
		inviter.invite_result.rpc_id(from_id, multiplayer.get_unique_id(), accepted)
	if accepted:
		var ui = _ui()
		if ui:
			ui.open_chat(from_id)

@rpc("any_peer", "reliable")
func invite_result(from_id: int, accepted: bool):
	var ui = _ui()
	if not ui:
		return
	if accepted:
		ui.open_chat(from_id)
	else:
		ui.notify("對方拒絕了聊天邀請")

# --- chat messages --------------------------------------------------------
func send_chat(to_id: int, text: String):
	var other = _find_player(to_id)
	if other:
		other.receive_chat.rpc_id(to_id, multiplayer.get_unique_id(), text)

@rpc("any_peer", "reliable")
func receive_chat(from_id: int, text: String):
	var ui = _ui()
	if ui:
		ui.append_chat(from_id, text)

# Local: tell the other side we closed the chat.
func leave_chat(to_id: int):
	var other = _find_player(to_id)
	if other:
		other.peer_left_chat.rpc_id(to_id, multiplayer.get_unique_id())

@rpc("any_peer", "reliable")
func peer_left_chat(from_id: int):
	var ui = _ui()
	if ui:
		ui.peer_left_chat(from_id)
