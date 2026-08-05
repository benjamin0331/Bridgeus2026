extends CharacterBody2D

const SPEED = 150.0
const PROXIMITY_RADIUS = 40.0
const BUBBLE_SCALE = 0.5   # counters the 2x camera zoom so the bubble font stays crisp
const BUBBLE_Y = -32       # ponytail: bubble height above origin; tune to sit just over the head (sprite frame is 144px tall, head ~ -10)
const BANNER_GAP = 2       # ponytail: 頭銜被頂到議題泡泡上方時，兩框之間的間距（local 單位）
const HEAD_MAX = 10        # 頭上表情最多顯示幾個，超過用「…」代替（完整數量看 Read 面板）
const SPRITE_PX = 48.0     # 每個角色不論原圖尺寸，縮放到這個高度（≈青蛙 NPC 大小）
@onready var camera = $Camera2D

# Issue this player has submitted. Broadcast via apply_issue() and re-sent to
# late joiners via game.gd's request_issue_sync.
# stance/emotion reserved for later backend analysis; unused for now.
var issue_title := ""
var issue_body := ""

# 後端的 issue id（POST /api/issues/ 回的）。讀者要用它把表情回復存回後端。
# 作者提交成功後由 game.gd 呼叫 set_issue_backend_id 廣播；apply_issue 會歸零，
# 因為新議題的後端 id 要等作者的 backend POST 回來才知道。0 = 未知/未存後端。
var backend_issue_id: int = 0

# 後端 user id（M3 配對建房需要，見 game.gd _do_seat 與 Backend.gd request_topic_match）。
# 由 authority 在 _ready() 從 Backend.user_id 帶入；透過 MultiplayerSynchronizer 同步
# （spawn=true，晚進的人也拿得到），這樣 server 端才查得到「這個 peer 對應哪個後端使用者」。
# guest_login 測試帳號沒有對應 id，會是 0——只在本機測試情境出現，正式流程不會。
var backend_user_id: int = 0

# 頭上的表情回復（權威＝議題作者持有）。廣播與補送方式與議題相同：
# apply_reactions() 廣播給連線中的人，request_issue_sync 補送給晚進者。
# 公開（無底線）因為 game.gd 要讀它做補送，跟 issue_title/banner_text 一致。
# 內容是「依回復順序的 idx 陣列」，可含重複（頭上逐個顯示、Read 面板統計數量都靠它）。
var reactions: Array = []
# authority-only：peer_id → 選的 idx，是表情的真正來源（reactions = 它的 values()）。
# 一人一表情、可改選（覆蓋 value）。不同步、不補送。
var _reactor_ids := {}

# 頭銜 (banner)：像議題泡泡一樣廣播到每個 peer。文字來自後端 API（見 game.gd）。
var banner_text := ""
var banner_color := Color(0.15, 0.15, 0.15, 0.85)   # 預設同泡泡底色

var _bubble_box: PanelContainer
var _bubble: Label
var _banner_box: PanelContainer
var _banner: Label
var _banner_sb: StyleBoxFlat
var _reactions_box: PanelContainer
var _reactions_hbox: HBoxContainer
var _nearby: Array = []   # other player nodes currently in range (authority only)
var _seated := false          # 坐上木樁等待配對時鎖住移動
var _original_pos := Vector2.ZERO

# 外觀＝等級：appearance 就是玩家等級（0–6），決定青蛙顏色。authority 在 _ready() 從
# Backend.level 帶入，透過 synchronizer 同步（spawn=true → 晚進的人也看到正確顏色）。
# moving 由 authority 依移動狀態設；每個 peer 依這兩者播 lv{appearance}_{idle|run}。
#
# 等級來源是後端 GET /api/titles/me/ 的 level（累積完成對話場次換算，門檻見
# backend/api/views.py::LEVEL_THRESHOLDS）；顏色對照見 scripts/recolor_frog.py。
# 舊的 char_N_* 動畫還留在 SpriteFrames 裡但已不播——保留是為了隨時能退回隨機外觀。
var appearance := 0
var moving := false
var _scaled_for := -1   # 已依哪個 appearance 套過縮放（避免每幀重算，也讓 scale 與動畫切換脫鉤）

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
	_make_reactions()
	if not is_multiplayer_authority():
		if camera:
			camera.enabled = false
		return
	# 出生點：由 authority 自己定位，MultiplayerSynchronizer 再複製給其他 peer。
	# （若在 game.gd 的 _spawn_player 設，join 的玩家會被自己 authority 的同步值
	#   ——場景預設座標——蓋掉，所以一定要在這裡、由 authority 自己設。）
	var spawn = get_parent().get_node_or_null("SpawnPoint")
	if spawn:
		position = spawn.position
	# 後端 user id：同理必須由 authority 自己設（見上方欄位註解），才能透過
	# synchronizer 正確同步給其他 peer；guest 測試帳號沒有對應 id，維持 0。
	backend_user_id = Backend.user_id
	# 外觀＝等級，但先擲 1/10 的稀有款彩虹蛙（見 roll_appearance）。沒刷到就套等級色；
	# 等級還沒抓回來時是 0（Lv0 白），game.gd 在 /titles/me/ 回來後會補設一次
	# （見 _apply_level_to_local_player）。
	roll_appearance()
	# Only the locally controlled player needs proximity detection + UI.
	_make_proximity_area()

func _physics_process(_delta):
	if not is_multiplayer_authority():
		return

	# 坐上木樁等待配對中：鎖住移動，直到配對成功或取消。
	if _seated:
		velocity = Vector2.ZERO
		moving = false
		move_and_slide()
		return

	# Don't drive movement with WASD while typing in a field, or while the
	# read-issue panel is open (must close it before moving again).
	var focus = get_viewport().gui_get_focus_owner()
	var ui = _ui()
	if focus is LineEdit or focus is TextEdit or (ui and ui.blocks_movement()):
		velocity = Vector2.ZERO
		moving = false
		move_and_slide()
		return

	var direction = Input.get_vector("left", "right", "up", "down")
	if direction:
		velocity = direction * SPEED
	else:
		velocity = velocity.move_toward(Vector2.ZERO, SPEED)
	# 有水平移動才翻轉；純上下移動保持原朝向。flip_h 已由 MultiplayerSynchronizer 同步。
	if velocity.x != 0:
		$AnimatedSprite2D.flip_h = velocity.x < 0
	moving = velocity.length() > 1.0   # 同步給其他 peer 決定播 idle / run
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

func _make_reactions():
	# 表情列：同泡泡的世界空間＋抗 2x 縮放做法。HBox 讓表情並排。
	_reactions_box = PanelContainer.new()
	# 頭上表情不要黑底：空 StyleBox 去掉背景與內距。
	_reactions_box.add_theme_stylebox_override("panel", StyleBoxEmpty.new())
	_reactions_hbox = HBoxContainer.new()
	# 負間距讓表情重疊約一半（表情寬 16px → -8），按多了畫面也不會太長。
	_reactions_hbox.add_theme_constant_override("separation", -8)
	_reactions_box.add_child(_reactions_hbox)
	_reactions_box.scale = Vector2(BUBBLE_SCALE, BUBBLE_SCALE)
	_reactions_box.z_index = RenderingServer.CANVAS_ITEM_Z_MAX
	_reactions_box.z_as_relative = false
	_reactions_box.visible = false
	add_child(_reactions_box)

# --- render helpers (called by the apply_* RPCs on every peer) ------------
func _render_bubble():
	if _bubble_box == null:
		return
	_bubble.text = issue_title
	_bubble_box.visible = issue_title != ""
	_bubble_box.reset_size()
	_bubble_box.position = Vector2(-_bubble_box.size.x * BUBBLE_SCALE / 2.0, BUBBLE_Y)
	_reposition_banner()
	_reposition_reactions()

func _render_reactions():
	if _reactions_box == null:
		return
	for c in _reactions_hbox.get_children():
		c.queue_free()
	# 頭上逐個重疊顯示；超過 10 個就只顯示前 10 個再接「…」（詳細數量看 Read 面板）。
	var shown = mini(reactions.size(), HEAD_MAX)
	for i in shown:
		var tr = TextureRect.new()
		tr.texture = Emoji.tex(reactions[i])
		_reactions_hbox.add_child(tr)
	if reactions.size() > HEAD_MAX:
		var more = Label.new()
		more.text = "…"
		more.add_theme_font_size_override("font_size", 18)
		more.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
		_reactions_hbox.add_child(more)
	_reactions_box.visible = reactions.size() > 0
	_reactions_box.reset_size()
	_reposition_reactions()

# 表情列擺在議題泡泡/頭銜那疊的最上方。
func _reposition_reactions():
	if _reactions_box == null or not _reactions_box.visible:
		return
	_reactions_box.reset_size()
	var x = -_reactions_box.size.x * BUBBLE_SCALE / 2.0
	# 疊在最上方：泡泡頂在 BUBBLE_Y，頭銜（若有）再往上一層，表情放頭銜之上。
	var top = float(BUBBLE_Y)
	if _banner_box.visible:
		top -= _banner_box.size.y * BUBBLE_SCALE + BANNER_GAP
	top -= _reactions_box.size.y * BUBBLE_SCALE + BANNER_GAP
	_reactions_box.position = Vector2(x, top)

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

# --- 木樁座位（本地方法，由 game.gd 的 apply_seat/apply_unseat 呼叫）---------
# 位置由 MultiplayerSynchronizer 從 authority 複製，所以只在 authority peer 傳送。
func sit_at(pos: Vector2) -> void:
	# 坐著時畫在樹幹之上。用絕對 z（z_as_relative=false）跳出 root 的 y-sort z 分層，
	# 否則靠下（y 較大）的樹幹在 y-sort 裡仍會蓋過玩家。所有 peer 都套用。
	z_as_relative = false
	z_index = 20
	if not is_multiplayer_authority():
		return
	_original_pos = global_position
	global_position = pos
	_seated = true

func stand_up() -> void:
	z_index = 0
	z_as_relative = true   # 還原成一般（跟世界一起 y-sort）
	if not is_multiplayer_authority():
		return
	global_position = _original_pos
	_seated = false

# --- issue submit (authority broadcasts to everyone) ----------------------
func submit_issue(title: String, body: String) -> void:
	apply_issue.rpc(title, body)
	# 後端 POST 由 UI 的 _submit_form() 發出（callback 在那邊跳成功/失敗提示）。

# 作者提交議題、後端回傳 id 後由 game.gd 呼叫：把 id 廣播給所有 peer（含自己）。
# 讀者要用它把表情回復存回後端（見 game_ui.gd _on_react）。id 走獨立 RPC 是因為
# 後端 id 比 P2P 的 apply_issue 晚到（要等 backend POST 回來）。
func set_issue_backend_id(id: int) -> void:
	apply_issue_backend_id.rpc(id)

@rpc("authority", "call_local", "reliable")
func apply_issue_backend_id(id: int) -> void:
	backend_issue_id = id

@rpc("authority", "call_local", "reliable")
func apply_issue(title: String, body: String):
	issue_title = title
	issue_body = body
	backend_issue_id = 0   # 新議題，後端 id 未知，等作者廣播 apply_issue_backend_id
	reactions.clear()      # 新議題清掉舊表情——涵蓋重新提交與刪除（刪除＝提交空議題）
	_reactor_ids.clear()   # 一併清掉「誰回過」的記錄，新議題可重新回復
	_render_bubble()
	_render_reactions()
	# A nearby peer's menu was rendered before this issue arrived — refresh it so
	# "（對方尚未提交議題）" updates to the real title in real time.
	var ui = _ui()
	if ui:
		ui.refresh_menu()
		# 新議題把表情歸零 → 讀者端解除對這人的「已回過」鎖，可重新回復。
		ui.on_issue_reset(self)

# --- issue reactions (same broadcast + late-join pattern as issues) --------
# Local (reader side): react to `target`'s issue. Only the target's authority may
# mutate its reaction list, so we RPC the author, who then broadcasts the result.
func react_to_issue(target, idx: int) -> void:
	target.add_reaction.rpc_id(target.name.to_int(), idx)

@rpc("any_peer", "reliable")
func add_reaction(idx: int) -> void:
	if not is_multiplayer_authority():
		return
	if issue_title == "":
		return   # 沒議題就沒得回復（UI 已擋，防呆）。
	# 一人一表情、可改選：直接覆蓋這個 peer 的選擇。Dictionary 改 value 不動 key 順序，
	# 所以改選時該 peer 在頭上表情列的位置不變、只換圖示。reactions 由此導出後廣播。
	var sender = multiplayer.get_remote_sender_id()
	_reactor_ids[sender] = idx
	reactions = _reactor_ids.values()
	apply_reactions.rpc(reactions)

@rpc("authority", "call_local", "reliable")
func apply_reactions(arr: Array):
	reactions = arr
	_render_reactions()
	# Read 面板若正開著這個人，更新右側的表情數量統計。
	var ui = _ui()
	if ui:
		ui.on_reactions_changed(self)

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

# --- voice call handshake (mirrors chat) ----------------------------------
var _in_voice := false
var _voice_peer := -1

# Local: ask `target` for a voice call.
func invite_to_voice(target):
	var target_id = target.name.to_int()
	target.receive_voice_invite.rpc_id(target_id, multiplayer.get_unique_id())

@rpc("any_peer", "reliable")
func receive_voice_invite(from_id: int):
	var ui = _ui()
	if ui:
		ui.show_invite(from_id, true)

# Local (target side): answer a voice invite.
func respond_voice_invite(from_id: int, accepted: bool):
	var inviter = _find_player(from_id)
	if inviter:
		inviter.voice_invite_result.rpc_id(from_id, multiplayer.get_unique_id(), accepted)
	if accepted:
		_start_voice(from_id)
		var ui = _ui()
		if ui:
			ui.open_voice(from_id)

@rpc("any_peer", "reliable")
func voice_invite_result(from_id: int, accepted: bool):
	var ui = _ui()
	if accepted:
		_start_voice(from_id)
		if ui:
			ui.open_voice(from_id)
	elif ui:
		ui.notify("對方拒絕了語音邀請")

# Local: leave the voice call.
func leave_voice(to_id: int):
	var other = _find_player(to_id)
	if other:
		other.peer_left_voice.rpc_id(to_id, multiplayer.get_unique_id())
	_stop_voice()

@rpc("any_peer", "reliable")
func peer_left_voice(from_id: int):
	_stop_voice()
	var ui = _ui()
	if ui:
		ui.voice_peer_left(from_id)

func _start_voice(peer: int):
	_in_voice = true
	_voice_peer = peer
	VoiceChat.start()

func _stop_voice():
	_in_voice = false
	_voice_peer = -1
	VoiceChat.stop()

# --- voice audio transport ------------------------------------------------
# 本地玩家每幀把擷取到的變調後 PCM 送給通話對象。
func _process(_delta):
	_update_anim()
	if _in_voice and is_multiplayer_authority():
		var f = VoiceChat.get_captured_frames()
		if f.size() > 0:
			receive_voice.rpc_id(_voice_peer, f)

# SpriteFrames 裡有幾個 lvN_idle＝支援幾個等級。加一個等級只要加兩個動畫，不用改程式。
# 靜態的：每個 peer 的 SpriteFrames 都一樣，算一次就夠。
static var _level_count := -1

func level_count() -> int:
	if _level_count < 0:
		# 從 0 依序探測到缺號為止，而不是數所有 lv*_idle。SpriteFrames 裡有 lv777_idle
		# （彩虹蛙彩蛋，掛著沒接系統），用 pattern match 會把它算成第 8 級，clampi 的上限
		# 就變成 7，而 lv7_idle 並不存在。依序探測天然忽略這種非連號的額外動畫。
		# 型別要明寫：$AnimatedSprite2D 是無型別的 Node，取出來的 sprite_frames 是
		# Variant，用 := 推不出型別會 parse error。
		var frames: SpriteFrames = $AnimatedSprite2D.sprite_frames
		_level_count = 0
		while frames.has_animation("lv%d_idle" % _level_count):
			_level_count += 1
	return _level_count

# --- 稀有款彩虹蛙（Lv777 彩蛋）--------------------------------------------
# 777 不是「第 777 級」，是刻意選來對上 SpriteFrames 裡 lv777_idle / lv777_run 的哨兵值：
# _update_anim 用 "lv%d_%s" 組動畫名，所以 appearance = 777 就自然播到彩虹蛙，不需要
# 任何對映程式碼，也照樣走 MultiplayerSynchronizer 同步 → 別人也看得到你刷到稀有款。
# level_count() 是依序探測到缺號為止，所以 777 不會被算成一個等級（見上方註解）。
const RAINBOW_APPEARANCE := 777
const RAINBOW_DENOM := 10       # 1/10 機率

# 每次進場擲一次，不持久化：這一場刷到就是彩虹蛙，下次進來重新擲，沒刷到就正常顯示等級色。
func roll_appearance() -> void:
	if not is_multiplayer_authority():
		return
	# 混入 peer id：多開實例常同時啟動，時間種子會撞在一起、前幾個都擲出同一個結果
	# （沿用原本隨機外觀的做法）。
	if (randi() + name.to_int()) % RAINBOW_DENOM == 0:
		appearance = RAINBOW_APPEARANCE
		print("[外觀] 玩家 %s 刷到稀有款彩虹蛙" % name)
		_refresh_level_legend()
		for ui in get_tree().get_nodes_in_group("issue_ui"):
			ui.show_rare_popup(self)
	else:
		apply_level(Backend.level)

func is_rare() -> bool:
	return appearance == RAINBOW_APPEARANCE

# 玩家在彈窗按「太閃了，我不要」：放棄這次的稀有款，換回自己的等級色。
# 只影響這一場 —— 下次進場照樣有 1/10 機率再刷到，不做持久化的「拒絕」紀錄。
# 不能重用 apply_level()：那支在 is_rare() 時會刻意不套色（防止 /titles/me/ 覆寫）。
func decline_rare() -> void:
	if not is_multiplayer_authority() or not is_rare():
		return
	appearance = clampi(Backend.level, 0, max(0, level_count() - 1))
	_refresh_level_legend()

# 只有 authority 該呼叫（appearance 是同步欄位）。夾在合法範圍內：後端等級數若比
# 素材多（新增門檻但還沒畫圖），夾住比播不存在的動畫噴錯好。
# 刷到稀有款的話不套等級色 —— 這支會在 /titles/me/ 回來後被 game.gd 再呼叫一次，
# 沒有這個判斷就會把彩虹蛙覆寫掉。色表仍然要刷（要填場次門檻、要關掉箭頭）。
func apply_level(level: int) -> void:
	if not is_multiplayer_authority():
		return
	if is_rare():
		_refresh_level_legend()   # 稀有款：只刷色表（填場次門檻、關箭頭），不套等級色
		return
	appearance = clampi(level, 0, max(0, level_count() - 1))
	_refresh_level_legend()

# 右上角色表：刷到稀有款就不標箭頭（玩家的青蛙不屬於任何一級）。
# 由 player 呼叫 UI 是既有慣例（同 ui.refresh_menu()），UI 本身不含 RPC。
func _refresh_level_legend() -> void:
	for ui in get_tree().get_nodes_in_group("issue_ui"):
		ui.refresh_level_legend(not is_rare())

# 每個 peer 都跑：依同步來的 appearance（＝等級）+ moving 播對應動畫（idle/run）。
func _update_anim():
	var s = $AnimatedSprite2D
	var want = "lv%d_%s" % [appearance, "run" if moving else "idle"]
	if s.animation != want:
		s.play(want)
	# 縮放依 appearance 設一次即可（與 idle/run 切換脫鉤，否則移動時才套用會「忽大忽小」）。
	if _scaled_for != appearance:
		_scaled_for = appearance
		# 依格子高度正規化到 SPRITE_PX 高（統一 48px 素材 → scale 1 → 跟青蛙一樣大）。
		var tex = s.sprite_frames.get_frame_texture("lv%d_idle" % appearance, 0)
		if tex and tex.get_height() > 0:
			var k = SPRITE_PX / float(tex.get_height())
			s.scale = Vector2(k, k)

@rpc("any_peer", "unreliable")
func receive_voice(frames: PackedVector2Array):
	VoiceChat.play_frames(frames)
