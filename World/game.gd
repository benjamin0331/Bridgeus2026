extends Node2D

var peer = WebSocketMultiplayerPeer.new()
const PORT = 8080
const ADDRESS = "127.0.0.1"

@onready var host_btn = $CanvasLayer/UI_Root/HostButton
@onready var join_btn = $CanvasLayer/UI_Root/JoinButton
@onready var issue_btn = $CanvasLayer_issue/Control/IssueButton
@onready var option_btn = $CanvasLayer_issue/Control/OptionButton
@onready var color_btn = $CanvasLayer_issue/Control/BannerColorButton
@onready var _form = $CanvasLayer_issue/IssueForm
@onready var _title_edit = $CanvasLayer_issue/IssueForm/TitleEdit
@onready var _body_edit = $CanvasLayer_issue/IssueForm/BodyEdit

func _ready():
	host_btn.pressed.connect(_on_host_pressed)
	join_btn.pressed.connect(_on_join_pressed)
	# 提交表單的送出/取消（IssueButton 本身在場景裡已連到 _on_issue_button_pressed）
	$CanvasLayer_issue/IssueForm/SubmitButton.pressed.connect(_submit_issue)
	$CanvasLayer_issue/IssueForm/CancelButton.pressed.connect(_cancel_submit)
	$CanvasLayer_issue/IssueForm/ClearButton.pressed.connect(_clear_form)
	$CanvasLayer_issue/IssueForm/CloseButton.pressed.connect(_close_form)
	_setup_banner_ui()

	# 訪客登入：拿到 access_token 後 submit_issue 才不會被後端擋（401）。
	# ponytail: 暱稱先寫死「訪客」；之後有登入輸入框再換成玩家輸入。
	Backend.guest_login("訪客", func(code, data):
		if code == 201:
			print("訪客登入成功")
		else:
			push_warning("訪客登入失敗 code=%d data=%s" % [code, data])
	)

func _process(_delta):
	# 這些按鈕只有在自己已經有身體（本地玩家）時才有意義。
	var p = _local_player()
	var have_body = p != null
	issue_btn.visible = have_body
	option_btn.visible = have_body
	color_btn.visible = have_body
	# 已提交過議題就把提交鍵調暗，提示「已提交、可再點進去編輯」。
	if p:
		issue_btn.modulate.a = 0.55 if p.issue_title != "" else 1.0

# 1. Host 點擊方法
func _on_host_pressed() -> void:
	var error = peer.create_server(PORT)
	if error != OK:
		print("無法啟動 WebSocket 伺服器，錯誤碼：", error)
		return

	multiplayer.multiplayer_peer = peer
	print("內建 WebSocket 伺服器已啟動！")
	_spawn_player(1) # （Host）給自己生身體
	hide_buttons()

# 2. Join 點擊方法
func _on_join_pressed() -> void:
	var error = peer.create_client("ws://" + ADDRESS + ":" + str(PORT))
	if error != OK:
		print("無法連接 WebSocket，錯誤碼：", error)
		return

	multiplayer.multiplayer_peer = peer
	print("正在嘗試連線到本機...")
	hide_buttons()

	# 一連上線，立刻向 Server 廣播：發放我的網路 ID 並生身體
	# 用一個延遲，確保網路 peer 已經完全握手成功
	await get_tree().create_timer(0.2).timeout
	var my_id = multiplayer.get_unique_id()
	request_spawn.rpc_id(1, my_id)
	# 等身體都生出來後，向所有人索取已提交的議題，補上「我加入前就貼出」的那些。
	await get_tree().create_timer(0.2).timeout
	request_issue_sync.rpc()

# 3. 遠端申請角色生成的傳送門（只有 Server 1 號會執行它）
@rpc("any_peer", "call_local")
func request_spawn(id):
	if multiplayer.is_server():
		print("Server 收到申請！準備幫玩家生成網路 ID: ", id)
		_spawn_player(id)

# 4. 唯一的生成角色方法（由 Server 執行，Spawner 會自動空投給所有人）
func _spawn_player(id):
	var player_scene = preload("res://Entities/player/player_00.tscn")
	var player = player_scene.instantiate()

	# 1. 唯一的任務：把名字改成網路 ID
	player.name = str(id)

	# 2. 直接放進世界，剩下的權限交給小人自己處理
	add_child(player)

# 5. 遲到同步：新玩家生成後向所有人索取議題。收到的人若自己的 authority 玩家已提交
#    議題，就把它單獨補送給索取者（apply_issue 是 authority-gated，剛好合法）。
@rpc("any_peer", "reliable")
func request_issue_sync():
	var requester = multiplayer.get_remote_sender_id()
	for p in get_tree().get_nodes_in_group("players"):
		if not p.is_multiplayer_authority():
			continue
		if p.issue_title != "":
			p.apply_issue.rpc_id(requester, p.issue_title, p.issue_body)
		if p.banner_text != "":
			p.apply_banner.rpc_id(requester, p.banner_text, p.banner_color)

func hide_buttons():
	host_btn.hide()
	join_btn.hide()

# --- 提交議題（原本在 issue_ui.gd，依需求搬進 game.gd）--------------------
# IssueButton 按下：打開表單，帶出目前已提交的內容讓使用者決定要不要改（不清空）。
func _on_issue_button_pressed() -> void:
	var p = _local_player()
	_title_edit.text = p.issue_title if p else ""
	_body_edit.text = p.issue_body if p else ""
	_form.visible = true
	_set_menu_suppressed(true)
	_title_edit.grab_focus()

func _close_form() -> void:
	_form.visible = false
	_set_menu_suppressed(false)

# 取消提交：廣播空議題 → 收回自己頭頂的議題泡泡（頭銜會自動掉回原位）。
func _cancel_submit() -> void:
	var p = _local_player()
	if p:
		p.submit_issue("", "")
	_notify("已取消提交議題")
	_close_form()

# 清空表單欄位（只清輸入框，不影響已提交/頭頂泡泡；要重貼得再按送出）。
func _clear_form() -> void:
	_title_edit.text = ""
	_body_edit.text = ""
	_title_edit.grab_focus()

func _submit_issue() -> void:
	var title = _title_edit.text.strip_edges()
	if title == "":
		_notify("請先輸入議題標題")
		return
	var body = _body_edit.text.strip_edges()
	# 1) 廣播給其他玩家（頭頂泡泡）— 純 Godot 連線，不靠後端，永遠執行。
	var p = _local_player()
	if p:
		p.submit_issue(title, body)
	# 2) 有登入才存進後端資料庫；沒登入（後端沒開）就只跑本地流程。
	if Backend.access_token == "":
		_notify("議題已送出（未連後端，未存檔）")
	else:
		Backend.submit_issue(title, body, func(code, _data):
			if code == 201:
				_notify("議題已送出！")
			else:
				_notify("送出失敗 (code %d)" % code)
		)
	_close_form()

# --- 小工具：找本地玩家、找 UI（issue_ui group）------------------------
func _local_player():
	for p in get_tree().get_nodes_in_group("players"):
		if p.is_multiplayer_authority():
			return p
	return null

func _ui():
	return get_tree().get_first_node_in_group("issue_ui")

func _notify(msg: String) -> void:
	var ui = _ui()
	if ui:
		ui.notify(msg)

func _set_menu_suppressed(v: bool) -> void:
	var ui = _ui()
	if ui:
		ui.suppress_menu = v
		ui.refresh_menu()


# --- 頭銜 banner UI -------------------------------------------------------
# ponytail: 選項先寫死 test_1~3；正式版改成向後端要清單（見下方 _fetch_banner_options）。
func _setup_banner_ui() -> void:
	option_btn.clear()
	option_btn.add_item("test_1")
	option_btn.add_item("test_2")
	option_btn.add_item("test_3")
	option_btn.selected = -1   # 不預選，任何一次點選都會觸發 item_selected
	option_btn.item_selected.connect(_on_banner_selected)
	color_btn.color = Color(0.15, 0.15, 0.15, 0.85)   # 預設同泡泡底色
	color_btn.color_changed.connect(_on_banner_color_changed)

# 選了頭銜 → 用目前調色盤顏色貼到自己頭上（廣播給所有人）。
func _on_banner_selected(index: int) -> void:
	var p = _local_player()
	if p:
		p.set_banner(option_btn.get_item_text(index), color_btn.color)

# 改色 → 若已選了頭銜，用新顏色重貼。
func _on_banner_color_changed(color: Color) -> void:
	var p = _local_player()
	if p and p.banner_text != "":
		p.set_banner(p.banner_text, color)

# 之後接後端：把上面 add_item 的三行換成 Backend 回傳的清單。
# func _fetch_banner_options() -> void:
#     Backend.get_titles(func(code, data):
#         if code == 200:
#             option_btn.clear()
#             for t in data:            # data 形如 [{"id":1,"name":"探索者"}, ...]
#                 option_btn.add_item(t["name"])
#             option_btn.selected = -1)
