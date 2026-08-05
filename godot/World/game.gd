extends Node2D

var peer = WebSocketMultiplayerPeer.new()
const DEFAULT_PORT := 8085   # 8080 已被 React(Docker) 佔用，多人連線改用 8085
const DEFAULT_ADDRESS := "127.0.0.1"
var _ws_url := ""   # 由 _resolve_connection_settings() 在 _ready() 填入，Join/dedicated server 都讀這個

# 議題 → 該議題的兩個木樁節點路徑（相對 /root/Game）。
const _TOPIC_TRUNKS := {
	"nuclear_energy": ["Entities/L_1", "Entities/L_2"],
	"women_soldier": ["Entities/R_1", "Entities/R_2"],
}
var _occupancy := {}   # trunk_path:String -> peer_id:int（僅 server 使用）
var _title_ids: Array = []   # 頭銜下拉選單 index → 後端 title_id（0 = 假頭銜，不回寫後端）

@onready var host_btn = $CanvasLayer/UI_Root/HostButton
@onready var join_btn = $CanvasLayer/UI_Root/JoinButton
@onready var issue_btn = $CanvasLayer_issue/Control/IssueButton
@onready var option_btn = $CanvasLayer_issue/Control/OptionButton
@onready var color_btn = $CanvasLayer_issue/Control/BannerColorButton
@onready var _form = $CanvasLayer_issue/IssueForm
@onready var _title_edit = $CanvasLayer_issue/IssueForm/TitleEdit
@onready var _body_edit = $CanvasLayer_issue/IssueForm/BodyEdit
@onready var _waiting_panel = $WaitingLayer/Panel

func _ready():
	_resolve_connection_settings()
	multiplayer.peer_disconnected.connect(_on_peer_disconnected)

	# 常駐 headless server（見 godot-web-deployment-spec.md §4）：只跑連線與配對邏輯，
	# 不代表任何玩家、不生自己的身體、不叫身份交接（沒有 window 可讀、也沒有真人要登入）。
	if _is_dedicated_server():
		_start_dedicated_server()
		return

	host_btn.pressed.connect(_on_host_pressed)
	join_btn.pressed.connect(_on_join_pressed)
	# 提交表單的送出/取消（IssueButton 本身在場景裡已連到 _on_issue_button_pressed）
	$CanvasLayer_issue/IssueForm/SubmitButton.pressed.connect(_submit_issue)
	$CanvasLayer_issue/IssueForm/CancelButton.pressed.connect(_cancel_submit)
	$CanvasLayer_issue/IssueForm/ClearButton.pressed.connect(_clear_form)
	$CanvasLayer_issue/IssueForm/CloseButton.pressed.connect(_close_form)
	_setup_banner_ui()
	$WaitingLayer/Panel/VBox/CancelButton.pressed.connect(_cancel_wait)

	# Web 版不能當 host（瀏覽器分頁無法 create_server）；隱藏 Host 鍵，只留 Join。
	if OS.has_feature("web"):
		host_btn.hide()

	# ⚠️ 先等主功能把 window.bridgeus_* 設好再讀。那些變數是在 iframe 的 load 事件裡設的
	# （frontend GodotLobby.jsx::handleLoad），而 wasm/pck 被瀏覽器快取時 Godot 開機可能
	# 更快，於是這裡有機會跑在設值之前。這就是「偶爾等級表沒有場次、偶爾又有」的原因：
	# 搶輸 → 拿不到 token → 退回訪客登入 → 從不呼叫 /titles/me/ → 沒有門檻可顯示，
	# 等級也永遠是 0（白青蛙）。連線位址 bridgeus_ws_url 吃同一組變數，搶輸會連到本機
	# 預設位址，症狀更嚴重，所以等完之後要重解析一次。
	if OS.has_feature("web"):
		await _await_host_handoff()
		_resolve_connection_settings()

	# 身份交接：優先用主功能登入的真 JWT（window.bridgeus_token，見 Backend.gd）。
	# 桌面開發、或還沒從主功能進來時，acquire_token_from_host() 回 false，
	# 退回訪客登入方便本機測試——純測試用，不是正式使用者，正式環境不會走到這條。
	if Backend.acquire_token_from_host():
		print("已取得主功能登入 token，user_id=%d" % Backend.user_id)
		_fetch_banner_options()   # 真登入才有頭銜；訪客/桌面維持假頭銜
	else:
		# ponytail: 暱稱先寫死「訪客」；之後有登入輸入框再換成玩家輸入。
		Backend.guest_login("訪客", func(code, data):
			if code == 201:
				print("訪客登入成功（測試用）")
			else:
				push_warning("訪客登入失敗 code=%d data=%s" % [code, data])
		)

# --- 等主功能交接 window.bridgeus_* ----------------------------------------
# 輪詢到 window.bridgeus_token 有值就放行，逾時就放行（讓「直接開 build」或桌面測試
# 照舊退回訪客登入，不要卡死在這裡）。UI 已經在上面接好了，等的期間畫面仍可操作。
const HANDOFF_TIMEOUT_SEC := 2.0
const HANDOFF_POLL_SEC := 0.05

func _await_host_handoff() -> void:
	var waited := 0.0
	while waited < HANDOFF_TIMEOUT_SEC:
		var t = JavaScriptBridge.eval("window.bridgeus_token || ''", true)
		if typeof(t) == TYPE_STRING and t != "":
			return
		await get_tree().create_timer(HANDOFF_POLL_SEC).timeout
		waited += HANDOFF_POLL_SEC
	push_warning("等不到 window.bridgeus_token（%.1fs），改用訪客登入" % HANDOFF_TIMEOUT_SEC)

# --- 連線位址解析 -----------------------------------------------------------
# 桌面開發固定連本機；Web 版優先讀主功能交接的 window.bridgeus_ws_url
# （見 godot-web-deployment-spec.md §4），沒有就退回本機位址方便單機測試。
func _resolve_connection_settings() -> void:
	if OS.has_feature("web"):
		var url = JavaScriptBridge.eval("window.bridgeus_ws_url || ''", true)
		if typeof(url) == TYPE_STRING and url != "":
			_ws_url = url
			return
	_ws_url = "ws://" + DEFAULT_ADDRESS + ":" + str(DEFAULT_PORT)

# --- 常駐 headless server 偵測與啟動 ----------------------------------------
# --server 由部署啟動指令帶（見部署規格 §4 systemd ExecStart：
# `godot --headless --path ... --server`）；DisplayServer "headless" 是備援偵測，
# 兩者都不依賴尚未建立的 Web/Server export preset（目前沒有 export_presets.cfg）。
func _is_dedicated_server() -> bool:
	return "--server" in OS.get_cmdline_args() or DisplayServer.get_name() == "headless"

func _start_dedicated_server() -> void:
	var error = peer.create_server(DEFAULT_PORT)
	if error != OK:
		push_error("[dedicated server] 無法啟動 WebSocket 伺服器，錯誤碼：%d" % error)
		get_tree().quit(1)
		return
	multiplayer.multiplayer_peer = peer
	print("[dedicated server] 已啟動，port=%d（peer id 1，唯一 authority）" % DEFAULT_PORT)
	host_btn.hide()
	join_btn.hide()
	# 其餘 UI（議題鍵、頭銜選單…）已由 _process 的 have_body 邏輯自然隱藏——
	# dedicated server 從不 _spawn_player 自己，_local_player() 永遠回 null。

const HEARTBEAT_INTERVAL := 25.0   # Cloudflare WS 閒置逾時約 100s，留充分餘裕
var _heartbeat_elapsed := 0.0

func _process(delta):
	# 這些按鈕只有在自己已經有身體（本地玩家）時才有意義。
	var p = _local_player()
	var have_body = p != null
	issue_btn.visible = have_body
	option_btn.visible = have_body
	color_btn.visible = have_body
	# 已提交過議題就把提交鍵調暗，提示「已提交、可再點進去編輯」。
	if p:
		issue_btn.modulate.a = 0.55 if p.issue_title != "" else 1.0

	# 心跳：連線中（含 dedicated server）就定期送一個空 RPC，避免 Cloudflare
	# 判定閒置斷線（deployment spec §4 point 3）。
	if multiplayer.multiplayer_peer and multiplayer.multiplayer_peer.get_connection_status() == MultiplayerPeer.CONNECTION_CONNECTED:
		_heartbeat_elapsed += delta
		if _heartbeat_elapsed >= HEARTBEAT_INTERVAL:
			_heartbeat_elapsed = 0.0
			if multiplayer.is_server():
				_heartbeat.rpc()
			else:
				_heartbeat.rpc_id(1)

# 1. Host 點擊方法
func _on_host_pressed() -> void:
	var error = peer.create_server(DEFAULT_PORT)
	if error != OK:
		print("無法啟動 WebSocket 伺服器，錯誤碼：", error)
		return

	multiplayer.multiplayer_peer = peer
	print("內建 WebSocket 伺服器已啟動！")
	_spawn_player(1) # （Host）給自己生身體
	hide_buttons()

# 2. Join 點擊方法
func _on_join_pressed() -> void:
	var error = peer.create_client(_ws_url)
	if error != OK:
		print("無法連接 WebSocket，錯誤碼：", error)
		return

	multiplayer.multiplayer_peer = peer
	print("正在嘗試連線到：", _ws_url)
	hide_buttons()

	# 等真正握手完成（connected_to_server 訊號）再申請生身體，取代固定 0.2s
	# 猜測值——真實網路延遲（尤其 Cloudflare Tunnel 代理）下 200ms 不一定夠。
	multiplayer.connected_to_server.connect(_on_connected_to_server, CONNECT_ONE_SHOT)

func _on_connected_to_server() -> void:
	var my_id = multiplayer.get_unique_id()
	request_spawn.rpc_id(1, my_id)
	# 等身體生出來後，向所有人索取已提交的議題，補上「我加入前就貼出」的那些。
	# （這段 0.2s 不是握手等待，是給 MultiplayerSpawner 初始複製一點時間，維持原樣。）
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

	# 2. 出生點由玩家自己（authority）在 _ready 依場景的「SpawnPoint」定位，
	#    這樣 host 與 join 都正確（見 player_00.gd 註解）。
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
			# apply_issue 會清空 backend_issue_id 與表情，所以補送議題後才補送
			# id 與表情，順序不能反。
			if p.backend_issue_id > 0:
				p.apply_issue_backend_id.rpc_id(requester, p.backend_issue_id)
			if not p.reactions.is_empty():
				p.apply_reactions.rpc_id(requester, p.reactions)
		if p.banner_text != "":
			p.apply_banner.rpc_id(requester, p.banner_text, p.banner_color)

func hide_buttons():
	host_btn.hide()
	join_btn.hide()

# 空心跳：內容不重要，重點是「有資料在傳」讓代理層（Cloudflare）不判定閒置。
@rpc("any_peer", "unreliable")
func _heartbeat() -> void:
	pass

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
		Backend.submit_issue(title, body, func(code, data):
			if code == 201:
				_notify("議題已送出！")
				# 後端回的 issue id 存到自己身上並廣播——讀者要用它把表情回復
				# 存回後端（見 game_ui.gd _on_react、player_00.gd backend_issue_id）。
				var lp = _local_player()
				var iid = int(data.get("id", 0)) if typeof(data) == TYPE_DICTIONARY else 0
				if lp and iid > 0:
					lp.set_issue_backend_id(iid)
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
func _setup_banner_ui() -> void:
	option_btn.selected = -1   # 不預選，任何一次點選都會觸發 item_selected
	option_btn.item_selected.connect(_on_banner_selected)
	color_btn.color = Color(0.15, 0.15, 0.15, 0.85)   # 預設同泡泡底色
	color_btn.color_changed.connect(_on_banner_color_changed)
	_load_placeholder_banners()   # 先塞假頭銜；有真登入才會被 _fetch_banner_options 換掉

# 桌面開發／訪客沒有真頭銜，塞假的純測 UI（title_id=0，選了不回寫後端）。
func _load_placeholder_banners() -> void:
	_title_ids.clear()
	option_btn.clear()
	for t in ["test_1", "test_2", "test_3"]:
		option_btn.add_item(t)
		_title_ids.append(0)
	option_btn.selected = -1

# 正式登入後向後端要真頭銜清單，替換掉假的，並在下拉選單顯示上次選的那個。
# 拿不到（非 200 或格式不對）就維持假頭銜，不擋玩家。
func _fetch_banner_options() -> void:
	Backend.get_my_titles(func(code, data):
		if code != 200 or typeof(data) != TYPE_DICTIONARY:
			return
		var owned = data.get("owned", [])
		_title_ids.clear()
		option_btn.clear()
		var selected_idx := -1
		var selected_id = data.get("selected_id")
		for i in owned.size():
			var t = owned[i]
			option_btn.add_item(str(t.get("name", "")))
			_title_ids.append(int(t.get("id", 0)))
			if selected_id != null and int(t.get("id", 0)) == int(selected_id):
				selected_idx = i
		# 程式設 selected 不會觸發 item_selected（只有玩家點才會），所以頭上不會
		# 自動貼——玩家要親自點一次才套到頭上。這裡只是把「上次選的」顯示出來。
		option_btn.selected = selected_idx
		var c = data.get("color")
		if typeof(c) == TYPE_STRING and c != "":
			color_btn.color = Color.html(c)
		# 這支同時帶回等級（Backend.get_my_titles 已快取進 Backend.level）。等級決定
		# 青蛙顏色，而這個 HTTP 回應跟玩家按 Host/Join 的時機無關，所以兩邊都要顧：
		# 先生成的話這裡補設，後生成的話 player_00.gd::_ready 自己讀 Backend.level。
		_apply_level_to_local_player()
	)

# 把 Backend.level 套到自己的青蛙上（appearance 是同步欄位，改了就會廣播出去）。
# apply_level 內部會順便刷右上角色表（含「你在這一級」的箭頭與場次門檻），而且刷到
# 稀有款彩虹蛙時會自己擋掉等級色的覆寫，所以這裡不需要判斷稀有與否。
func _apply_level_to_local_player() -> void:
	var p = _local_player()
	if p:
		p.apply_level(Backend.level)
		return
	# 玩家還沒生成（HTTP 比 Host/Join 先回來）：色表先把場次門檻填上，箭頭等
	# player_00.gd::roll_appearance 擲完稀有款後自己刷。
	for ui in get_tree().get_nodes_in_group("issue_ui"):
		ui.refresh_level_legend(true)

# 選了頭銜 → 用目前調色盤顏色貼到自己頭上（P2P 廣播）＋有真 id 才回寫後端。
func _on_banner_selected(index: int) -> void:
	var p = _local_player()
	if p:
		p.set_banner(option_btn.get_item_text(index), color_btn.color)
	var tid: int = _title_ids[index] if index < _title_ids.size() else 0
	if tid > 0:
		Backend.set_my_title(tid, "#" + color_btn.color.to_html(false))

# 改色 → 若已選了頭銜，用新顏色重貼（P2P）＋回寫後端（若目前選的是真頭銜）。
func _on_banner_color_changed(color: Color) -> void:
	var p = _local_player()
	if p and p.banner_text != "":
		p.set_banner(p.banner_text, color)
	var idx: int = option_btn.selected
	if idx >= 0 and idx < _title_ids.size() and _title_ids[idx] > 0:
		Backend.set_my_title(_title_ids[idx], "#" + color.to_html(false))


# --- 議題配對 ------------------------------------------------------------
# 兩個按鈕已在 Game.tscn 連到這兩個方法。
func _on_women_soldier_pressed() -> void:
	_try_seat("women_soldier")

func _on_nuclear_enegry_pressed() -> void:
	_try_seat("nuclear_energy")

# 還沒有身體（本地玩家）就忽略。host(id 1) 直接處理、client 送給 server，
# 避免對自己 rpc_id 時 get_remote_sender_id() 回 0（沿用 request_spawn 的做法）。
func _try_seat(topic: String) -> void:
	if _local_player() == null:
		return
	if multiplayer.is_server():
		_do_seat(multiplayer.get_unique_id(), topic)
	else:
		request_seat.rpc_id(1, topic)

func _cancel_wait() -> void:
	if multiplayer.is_server():
		_do_unseat(multiplayer.get_unique_id())
	else:
		request_unseat.rpc_id(1)

@rpc("any_peer", "reliable")
func request_seat(topic: String) -> void:
	if multiplayer.is_server():
		_do_seat(multiplayer.get_remote_sender_id(), topic)

@rpc("any_peer", "reliable")
func request_unseat() -> void:
	if multiplayer.is_server():
		_do_unseat(multiplayer.get_remote_sender_id())

# server-only：指派到第一個空木樁；已在座位者忽略；都滿則拒絕。
func _do_seat(peer_id: int, topic: String) -> void:
	if _occupancy.values().has(peer_id):
		return   # 不可同時佔兩個座位
	var trunks: Array = _TOPIC_TRUNKS.get(topic, [])
	var free_trunk := ""
	for t in trunks:
		if not _occupancy.has(t):
			free_trunk = t
			break
	if free_trunk == "":
		# rpc_id 對自己(host)不會本地執行 → 目標是自己時直接呼叫。
		if peer_id == multiplayer.get_unique_id():
			seat_denied("位置已滿")
		else:
			seat_denied.rpc_id(peer_id, "位置已滿")
		return
	_occupancy[free_trunk] = peer_id
	apply_seat.rpc(peer_id, free_trunk)
	# 兩木樁都被不同玩家佔用 → 配對成功。
	var occupants := []
	for t in trunks:
		if _occupancy.has(t):
			occupants.append(_occupancy[t])
	if occupants.size() == 2 and occupants[0] != occupants[1]:
		# 後端只在 server 端呼叫一次（兩位都打會建兩間房）。傳的是後端 user_id
		# （player.backend_user_id），不是 Godot peer_id——後端 match-rooms 端點
		# 認 user_id，見 integration §3.3 與 Backend.gd request_topic_match。
		var user_ids := []
		for pid in occupants:
			var pl = get_node_or_null(str(pid))
			if pl:
				user_ids.append(pl.backend_user_id)
		Backend.request_topic_match(topic, user_ids)
		for pid in occupants:
			# rpc_id 對自己(host)不會本地執行 → 目標是自己時直接呼叫。
			if pid == multiplayer.get_unique_id():
				match_found()
			else:
				match_found.rpc_id(pid)
		# 配對成功、交給後端導去網頁聊天室後，把這兩位的人物清掉、還原木樁。
		_finish_match(topic, occupants)

# server-only：釋放該 peer 的座位。
func _do_unseat(peer_id: int) -> void:
	var trunk_path := ""
	for t in _occupancy:
		if _occupancy[t] == peer_id:
			trunk_path = t
			break
	if trunk_path == "":
		return
	_occupancy.erase(trunk_path)
	apply_unseat.rpc(peer_id, trunk_path)

func _on_peer_disconnected(id: int) -> void:
	if multiplayer.is_server():
		_do_unseat(id)   # 等待中玩家斷線 → 釋放位子，別卡死配對
		# 斷線（含直接關分頁——WS 連線只是被動掉線，沒有任何「離開」訊號）不會
		# 自動清掉這個人的角色：MultiplayerSpawner 只有 server 端 queue_free()
		# 時才會複製「移除」給所有人，不做的話會變成永遠站在原地的幽靈玩家。
		var p = get_node_or_null(str(id))
		if p:
			p.queue_free()   # server free → MultiplayerSpawner 複製移除給所有 peer

# server-only：配對成功後稍等一下（讓「配對成功」提示看得到），再清掉兩位人物、還原木樁。
# ponytail: 原型固定 2 人；正式版若要支援重連/回主世界，這裡再改成別的善後。
func _finish_match(topic: String, peer_ids: Array) -> void:
	await get_tree().create_timer(1.5).timeout
	for t in _TOPIC_TRUNKS[topic]:
		_occupancy.erase(t)
		clear_trunk.rpc(t)
	for pid in peer_ids:
		var p = get_node_or_null(str(pid))
		if p:
			p.queue_free()   # server free → MultiplayerSpawner 複製移除給所有 peer

@rpc("authority", "call_local", "reliable")
func clear_trunk(trunk_path: String) -> void:
	var t = get_node_or_null(trunk_path)
	if t:
		t.set_occupied(false)

# 全 peer 執行：亮木樁、傳送玩家、鎖住；若是本地玩家則開等待視窗。
@rpc("authority", "call_local", "reliable")
func apply_seat(peer_id: int, trunk_path: String) -> void:
	var trunk = get_node_or_null(trunk_path)
	var player = get_node_or_null(str(peer_id))
	if trunk == null or player == null:
		return
	trunk.set_occupied(true)
	player.sit_at(trunk.seat_point.global_position)
	if peer_id == multiplayer.get_unique_id():
		_show_waiting(true)

@rpc("authority", "call_local", "reliable")
func apply_unseat(peer_id: int, trunk_path: String) -> void:
	var trunk = get_node_or_null(trunk_path)
	var player = get_node_or_null(str(peer_id))
	if trunk:
		trunk.set_occupied(false)
	if player:
		player.stand_up()
	if peer_id == multiplayer.get_unique_id():
		_show_waiting(false)

# 只有配對到的兩位收到：關等待視窗、提示（真正跳轉聊天室由後端接手）。
@rpc("authority", "reliable")
func match_found() -> void:
	_show_waiting(false)
	_notify("配對成功，準備進入聊天室…")

@rpc("authority", "reliable")
func seat_denied(msg: String) -> void:
	_notify(msg)

func _show_waiting(v: bool) -> void:
	_waiting_panel.visible = v
