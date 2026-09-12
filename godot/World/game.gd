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
var _matching_topics := {}   # topic:String -> true，建房 HTTP 在途中；防插隊與重入
var _peer_users := {}   # peer_id:int -> 後端 user_id:int（僅 server 使用，不同步——
                        # 身份的真值只能放 server；任何 client 可寫的同步屬性都可冒充）
var _peer_names := {}   # peer_id:int -> 匿名代號:String。server 是唯一發號者，
                        # 再用 apply_names 廣播整份給所有人；每個 peer 手上這份
                        # 只是顯示用的副本。代號綁 peer 而不綁 user_id，所以本機
                        # 開發（無服務金鑰、沒有身份）也照樣有名字可顯示。
var _ticket := ""       # client 端：join 前向宿主頁拉到的入場券，連上後遞給 server
var _redeem_pending := {}   # peer_id -> session 序號；兌換 HTTP 在途中。
                            # 節點要等 HTTP 回來才生，光靠 get_node_or_null 擋不住
                            # 同幀連發（後端允許一人同時持有多張有效券）。
var _redeem_seq := 0        # 單調遞增；用來分辨「同一個 peer id 的不同連線階段」——
                            # 斷線後新 peer 可能拿到同一個 id，沒有這個序號的話
                            # 上一位的兌換結果會被寫成新來者的身份。
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
	multiplayer.connection_failed.connect(_on_connection_failed)
	# 「連上之後才斷」是另一個訊號，而且比 connection_failed 更常走到：券兌換
	# 失敗時 server 就是呼叫 disconnect_peer 把人踢掉（見 submit_ticket），
	# server 重啟、Cloudflare Tunnel 掉線也都是這條。沒接的話玩家會停在一個
	# 沒有按鈕、沒有訊息的空世界，只剩重整一途——正是下面那段要避免的事。
	multiplayer.server_disconnected.connect(_on_server_disconnected)

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
	# 搶輸 → 拿不到 token → 從不呼叫 /titles/me/ → 沒有門檻可顯示，等級也永遠是 0
	# （白青蛙）。連線位址 bridgeus_ws_url 吃同一組變數，搶輸會連到本機預設位址，
	# 症狀更嚴重，所以等完之後要重解析一次。
	# handleLoad 是一支同步函式，四個變數同一個 tick 設完，所以只輪詢 token 就夠。
	# 唯一等不到的是 Backend.gd 的 _ready()（autoload 先於場景初始化，來不及等）——
	# 它讀的 bridgeus_api_base 搶輸時會退回 origin + "/api"，跟宿主頁給的值在同源
	# 拓樸下相同，所以無害。
	if OS.has_feature("web"):
		await _await_host_handoff()
		_resolve_connection_settings()

	# 身份交接：主功能登入的 JWT（window.bridgeus_token）只給 client 自己打
	# 議題/頭銜 API 用。對遊戲 server 的身份識別走一次性入場券（見 _on_join_pressed）。
	# 拿不到 token（桌面開發、直開 export）就沒有後端持久化功能，純本地遊玩——
	# 訪客登入已移除，不會再產生無主帳號。
	if Backend.acquire_token_from_host():
		_fetch_banner_options()   # 真登入才有頭銜；本地模式維持假頭銜

# --- 等主功能交接 window.bridgeus_* ----------------------------------------
# 輪詢到 window.bridgeus_token 有值就放行，逾時也放行（讓「直接開 build」或桌面測試
# 照舊退回無後端的純本地遊玩，不要卡死在這裡）。UI 已經在上面接好了，等的期間畫面仍可操作。
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
	push_warning("等不到 window.bridgeus_token（%.1fs），改為無後端的本地遊玩" % HANDOFF_TIMEOUT_SEC)

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
	# 正式部署漏設金鑰要顯性失敗。否則玩家連得上、走得動，只有配對時被告知
	# 「請從主功能頁面進入」——那句話指向使用者不指向 ops，設定錯誤會被誤判成
	# 使用者問題。dedicated server 沒有金鑰就等於不能建房，沒有存在意義。
	if Backend.service_token == "":
		push_error("[dedicated server] 未設定 GODOT_SERVICE_TOKEN，無法建立配對房間，拒絕啟動")
		get_tree().quit(1)
		return
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
	# Web 版必須先拿到入場券才連線；拿不到就別連——沒有券的連線只會被
	# dedicated server 踢掉，先擋在這裡才能給出有用的錯誤訊息。
	if OS.has_feature("web"):
		join_btn.disabled = true
		# 型別標註不是裝飾：request_entry_ticket() 是 coroutine，漏寫 await 會綁到
		# Signal，宣告成 String 才會當場報錯而不是默默往下走。
		var ticket: String = await Backend.request_entry_ticket()
		join_btn.disabled = false
		if ticket == "":
			# 失敗原因對受試者要可行動——他看不到 console，「重新登入」跟
			# 「叫研究員來」是不同的處置（見 Backend.last_ticket_error）。
			match Backend.last_ticket_error:
				"no_host":
					_notify("請從主功能頁面進入大廳，不要直接開啟遊戲檔案")
				"denied":
					_notify("登入狀態已失效，請回主功能頁面重新登入")
				"timeout":
					_notify("連線逾時，請稍後再試；持續失敗請告知研究人員")
				"busy":
					_notify("正在取得入場券，請稍候")
				_:
					_notify("無法取得入場券，請從主功能頁面進入大廳並確認已登入")
			return
		_ticket = ticket
	var error = peer.create_client(_ws_url)
	if error != OK:
		print("無法連接 WebSocket，錯誤碼：", error)
		_ticket = ""   # 用完即丟，跟流程其他地方一致——連不上就別留著半用的券
		_notify("無法建立連線，請稍後再試")
		return

	multiplayer.multiplayer_peer = peer
	print("正在嘗試連線到：", _ws_url)
	hide_buttons()

	# 等真正握手完成（connected_to_server 訊號）再遞券申請生身體，取代固定 0.2s
	# 猜測值——真實網路延遲（尤其 Cloudflare Tunnel 代理）下 200ms 不一定夠。
	multiplayer.connected_to_server.connect(_on_connected_to_server, CONNECT_ONE_SHOT)

func _on_connected_to_server() -> void:
	# 遞券給 server 換身份＋身體。桌面開發連本機 host 時 _ticket 是空字串，
	# server 端（無服務金鑰的本機模式）會放行、無身份 spawn（見 submit_ticket）。
	submit_ticket.rpc_id(1, _ticket)
	_ticket = ""   # 一次性，用掉就丟
	# 等身體生出來後，向所有人索取已提交的議題，補上「我加入前就貼出」的那些。
	# （這段 0.2s 不是握手等待，是給 MultiplayerSpawner 初始複製一點時間。
	#   正式環境 spawn 前多了一趟 server→Django 的兌換 HTTP（本機 <50ms），
	#   仍在餘裕內；若實測晚到，議題同步本來就有 request_issue_sync 補救。）
	await get_tree().create_timer(0.2).timeout
	request_issue_sync.rpc()

# 3. 遞券申請生成（只有 server 會處理）。取代舊的 request_spawn(id)——那個版本
#    信任 client 自報的 id，可以冒名或洗版；現在 id 一律取 get_remote_sender_id()，
#    身份一律由券兌換而來，client 沒有任何可自報的欄位。
@rpc("any_peer", "reliable")
func submit_ticket(ticket: String) -> void:
	if not multiplayer.is_server():
		return
	var id = multiplayer.get_remote_sender_id()
	if get_node_or_null(str(id)) != null or _redeem_pending.has(id):
		return   # 已有身體或兌換在途中，防重複——同一幀連送兩張有效券不能兩次都通過
	# 本機開發 host（沒有服務金鑰）：無身份 spawn，純本地遊玩。
	# 建房需要真實 user_id，_peer_users 沒有這個 peer → 坐木樁會被拒，正確。
	if Backend.service_token == "":
		_spawn_player(id)
		return
	# 正式（dedicated server）：券兌換成功才有身體，失敗就踢。
	# session 序號防兩種競態：(1) 同一 peer 連送多張券導致重複 spawn——已被
	# 上面的 _redeem_pending 擋住；(2) peer 斷線後同一個 id 被新來者重用，
	# 舊那張券的兌換結果晚回來時不能寫成新來者的身份（見欄位宣告處註解）。
	_redeem_seq += 1
	var my_seq = _redeem_seq
	_redeem_pending[id] = my_seq
	Backend.redeem_ticket(ticket, func(user_id):
		# 這期間 peer 可能已斷線（項目被 _on_peer_disconnected 清掉），
		# 或斷線後有新 peer 拿到同一個 id（序號已被換掉）——兩種都不能寫入。
		if _redeem_pending.get(id) != my_seq:
			return
		_redeem_pending.erase(id)
		if user_id <= 0:
			if multiplayer.multiplayer_peer and id in multiplayer.get_peers():
				multiplayer.multiplayer_peer.disconnect_peer(id)
			return
		# 兌換是非同步 HTTP，回來時 peer 可能已自己斷線——別替幽靈生身體。
		if not (id in multiplayer.get_peers()):
			return
		_peer_users[id] = user_id
		_spawn_player(id)
	)

# 4. 唯一的生成角色方法（由 Server 執行，Spawner 會自動空投給所有人）
func _spawn_player(id):
	# 匿名代號在這裡發：這是「某個 peer 正式成為玩家」的唯一入口，發號跟生身體
	# 綁在一起就不會有「有身體卻沒名字」的中間狀態。只有 server 能發——client
	# 自選的話既擋不住冒名，也沒人能協調撞名（見 Globals/AnonNames.gd）。
	if multiplayer.is_server() and not _peer_names.has(id):
		_peer_names[id] = AnonNames.pick(_peer_names.values())
		apply_names.rpc(_peer_names)

	var player_scene = preload("res://Entities/player/player_00.tscn")
	var player = player_scene.instantiate()

	# 1. 唯一的任務：把名字改成網路 ID
	player.name = str(id)

	# 2. 出生點由玩家自己（authority）在 _ready 依場景的「SpawnPoint」定位，
	#    這樣 host 與 join 都正確（見 player_00.gd 註解）。
	add_child(player)

# 4b. 匿名代號名冊廣播。整份送而不是只送異動的那一筆：名冊最多幾十筆，整份
#     覆蓋天生冪等，遲到的玩家也不必另外補一條同步路徑（不像議題那樣需要
#     request_issue_sync——他被 spawn 的那一刻就會收到含自己在內的完整名冊）。
@rpc("authority", "call_local", "reliable")
func apply_names(table: Dictionary) -> void:
	_peer_names = table.duplicate()
	var ui = _ui()
	if ui:
		ui.set_peer_names(_peer_names)

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

# 連不上時把入口還給玩家。沒有這段的話按鈕已經被 hide_buttons() 藏起來，
# 玩家只剩重整一途——而重整要再付一次 WASM 冷啟動。
func _on_connection_failed() -> void:
	_return_to_lobby("無法連線到伺服器，請稍後再試")

# 已經握手成功、之後才斷：被 server 踢（券無效／已用過／逾期，見 submit_ticket）、
# server 重啟、tunnel 掉線。善後跟「連不上」完全一樣，訊息不一樣——這裡玩家是
# 「進去過又被請出來」，要說得出下一步該做什麼。
func _on_server_disconnected() -> void:
	_return_to_lobby("與伺服器的連線已中斷，請重新加入；持續失敗請回主功能頁面重新登入")

# 兩條斷線路徑共用的善後。分開兩支訊號但共用這裡，是因為要還原的東西完全相同，
# 分頭寫兩份遲早會有一邊漏掉其中一項。
func _return_to_lobby(msg: String) -> void:
	# 換一顆全新的 peer：舊的 socket 未必已回到 DISCONNECTED，沿用會讓下一次
	# create_client 回 ERR_ALREADY_IN_USE。
	multiplayer.multiplayer_peer = null
	peer = WebSocketMultiplayerPeer.new()
	# one-shot 只在訊號真的發出時才解除；連線失敗時它還掛著，不斷開的話
	# 第二次按 Join 會重複連接。
	if multiplayer.connected_to_server.is_connected(_on_connected_to_server):
		multiplayer.connected_to_server.disconnect(_on_connected_to_server)
	_ticket = ""
	# 斷線後所有身體都是殘影：MultiplayerSpawner 的「移除」是 server 端 queue_free
	# 才複製過來的，而斷線本身就是收不到那則訊息的原因。不清的話重新 Join 會看到
	# 上一輪的幽靈玩家跟自己的新身體並存，而舊身體的 authority 已經不是自己了，
	# _local_player() 也找不到它——只能站在原地永遠不動。
	for p in get_tree().get_nodes_in_group("players"):
		p.queue_free()
	# 坐在木樁上等配對時被踢的話，等待視窗會留在畫面上蓋住 Join 鍵。
	_show_waiting(false)
	host_btn.visible = not OS.has_feature("web")
	# _on_join_pressed 拉券期間會把 Join 停用，斷在那之後就得自己還原。
	join_btn.disabled = false
	join_btn.show()
	_notify(msg)

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
		# 程式設 selected 不會觸發 item_selected（只有玩家點才會），所以這一行只是
		# 把「上次選的」顯示在下拉選單上；頭上要另外貼，見 _apply_banner_to_local_player。
		option_btn.selected = selected_idx
		var c = data.get("color")
		if typeof(c) == TYPE_STRING and c != "":
			color_btn.color = Color.html(c)
		# 這支同時帶回等級（Backend.get_my_titles 已快取進 Backend.level）。等級決定
		# 青蛙顏色，而這個 HTTP 回應跟玩家按 Host/Join 的時機無關，所以兩邊都要顧：
		# 先生成的話這裡補設，後生成的話 player_00.gd::_ready 自己讀 Backend.level。
		_apply_level_to_local_player()
		_apply_banner_to_local_player()
	)

# 把後端記著的頭銜貼回自己頭上。沒有這支的話，後端明明記著你上次選的頭銜、下拉
# 選單也顯示對了，但青蛙頭上是空的——每次進大廳都得重新點一次同一個頭銜才會出現。
# 兩條路都要有，理由同 _apply_level_to_local_player：HTTP 先回來就走這裡，
# 身體先生出來就走 player_00.gd::_ready。
func _apply_banner_to_local_player() -> void:
	if Backend.banner_text == "":
		return   # 沒選頭銜（selected_id 是 null）就什麼都不貼，不要蓋掉空狀態
	var p = _local_player()
	if p:
		p.set_banner(Backend.banner_text, Backend.banner_color)

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
	# 先擋沒有資格的請求者，而不是等湊成一對才用 user_ids.has(0) 把兩個人一起退座——
	# 那條路徑會連無辜的另一位一起趕走，等於讓不送券的 client 無限癱瘓配對。
	if get_node_or_null(str(peer_id)) == null:
		return   # 沒有身體（沒走過 submit_ticket）就沒有坐的資格，靜默忽略
	if Backend.service_token != "" and not _peer_users.has(peer_id):
		# 正式模式下沒有已驗證身份 → 明確拒絕請求者本人，不動別人的座位。
		_seat_notify(peer_id, "配對需要正式登入身份，請從主功能頁面進入")
		return
	if _occupancy.values().has(peer_id):
		return   # 不可同時佔兩個座位
	var trunks: Array = _TOPIC_TRUNKS.get(topic, [])
	if _matching_topics.has(topic):
		# 這個議題正在建房（HTTP 在途）。此時讓人插隊坐上空出來的樁，會導致
		# 回呼的座位驗證失敗、連帶把已經配對成功的另一位也退掉。擋在門口乾淨得多。
		_seat_notify(peer_id, "這個議題正在配對中，請稍候再試")
		return
	var free_trunk := ""
	for t in trunks:
		if not _occupancy.has(t):
			free_trunk = t
			break
	if free_trunk == "":
		# rpc_id 對自己(host)不會本地執行 → 目標是自己時直接呼叫。
		_seat_notify(peer_id, "位置已滿")
		return
	_occupancy[free_trunk] = peer_id
	apply_seat.rpc(peer_id, free_trunk)
	# 兩木樁都被不同玩家佔用 → 配對成功。
	var occupants := []
	for t in trunks:
		if _occupancy.has(t):
			occupants.append(_occupancy[t])
	if occupants.size() == 2 and occupants[0] != occupants[1]:
		_try_start_match(topic, occupants)

# server-only：只送拒絕訊息，不動座位（用於還沒坐上就被擋下的請求）。
# 順手擋掉已離線的 peer——rpc_id 給不存在的 peer 會在 server log 噴錯。
func _seat_notify(pid: int, msg: String) -> void:
	if pid == multiplayer.get_unique_id():
		seat_denied(msg)
	elif pid in multiplayer.get_peers():
		seat_denied.rpc_id(pid, msg)

# server-only：拒絕並退座一位玩家。配對的各種失敗路徑共用，
# 免得「rpc_id 對自己不會本地執行」的分支寫三遍。
func _seat_deny_and_unseat(pid: int, msg: String) -> void:
	_seat_notify(pid, msg)
	_do_unseat(pid)

# server-only：兩根木樁都坐滿 → 驗證 → 標記在途 → 非同步建房。
func _try_start_match(topic: String, occupants: Array) -> void:
	# 後端只在 server 端呼叫一次（兩位都打會建兩間房）。user_id 一律取自
	# server 端身份表 _peer_users——不讀 player 節點上的同步屬性（client 可寫
	# 的同步屬性＝可冒充的身份，見 spec §D2）。
	var user_ids := []
	for pid in occupants:
		user_ids.append(_peer_users.get(pid, 0))
	# 縱深防禦第二道：主要防線已在 _do_seat 開頭擋掉沒身份的請求者，正常情況
	# 不該走到這裡；留著是防本機開發模式（兩邊都沒身份仍會湊成一對）或任何漏網。
	if user_ids.has(0):
		# 正式環境走到這裡代表部署設定有問題（GODOT_SERVICE_TOKEN 未設或兌換失敗），
		# 但玩家看到的訊息是「請重新登入」——會讓所有人白白重登。要留給 ops 一個信號。
		push_warning("配對中止：peer 缺少已驗證身份 user_ids=%s" % [user_ids])
		for pid in occupants:
			_seat_deny_and_unseat(pid, "配對需要正式登入身份，請從主功能頁面進入")
		return
	# 同一位使用者開兩個分頁會兌換成兩個 peer、同一個 user_id。後端會 400
	# （user_ids 相同的檢查），但在這裡先擋，訊息才說得清楚（見 spec §7.1.1）。
	if user_ids[0] == user_ids[1]:
		for pid in occupants:
			_seat_deny_and_unseat(pid, "不能與自己配對，請關閉多餘的分頁")
		return
	_matching_topics[topic] = true
	Backend.request_topic_match(topic, user_ids, _on_match_room_created.bind(topic, occupants))

# server-only：建房 HTTP 回來。注意 Callable.bind() 是把參數接在**後面**，
# 所以 request_topic_match 呼叫 callback.call(code, data) 之後，簽名是
# (code, data, topic, occupants)。
func _on_match_room_created(code: int, data: Dictionary, topic: String, occupants: Array) -> void:
	# 注意：旗標不在這裡統一放掉。失敗路徑各自放，成功路徑要一路押到
	# _finish_match 的 1.5 秒善後做完為止——那段期間座位還佔著，提早放掉
	# 等於留一個窄版的同一個競態（有人斷線 → 第三人坐上 → 又觸發一次配對）。
	if code != 200 and code != 201:
		_matching_topics.erase(topic)
		push_error("配對建房失敗 code=%d" % code)
		for pid in occupants:
			_seat_deny_and_unseat(pid, "配對建立失敗，請稍後再試")
		return
	var room_id: String = str(data.get("room_id", ""))
	var topic_id: int = int(data.get("topic_id", 0))
	if room_id == "" or topic_id <= 0:
		# 2xx 但沒有房間資訊（舊版部署、代理攔截、契約改動）。不能往下走：
		# _finish_match 會刪掉兩位的身體，玩家又回到沒有身體的空世界。
		_matching_topics.erase(topic)
		push_error("配對建房回應缺少 room_id/topic_id，視為失敗：%s" % [data])
		for pid in occupants:
			_seat_deny_and_unseat(pid, "配對建立失敗，請稍後再試")
		return
	# 座位重新驗證：HTTP 在途期間有人按取消或斷線的話，peer id 早就不代表座位了。
	# 只有「現在誰坐在這個議題的樁上」才是真的。
	var seated := []
	for t in _TOPIC_TRUNKS[topic]:
		if _occupancy.has(t):
			seated.append(_occupancy[t])
	for pid in occupants:
		if not seated.has(pid):
			# 這一對已經不成立。還坐著的那位退座重來，不要把他單方面送進房間。
			_matching_topics.erase(topic)
			for other in occupants:
				if seated.has(other):
					_seat_deny_and_unseat(other, "對方已取消配對，請重新選擇")
			return
	for pid in occupants:
		# rpc_id 對自己(host)不會本地執行 → 目標是自己時直接呼叫。
		if pid == multiplayer.get_unique_id():
			match_found(topic_id, room_id)
		elif pid in multiplayer.get_peers():
			match_found.rpc_id(pid, topic_id, room_id)
	# 配對成功、交給後端導去網頁聊天室後，把這兩位的人物清掉、還原木樁——旗標要
	# 撐到那邊做完才放（見上方註解），所以這裡不 erase。
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
		_peer_users.erase(id)   # 身份表跟著 peer 走；殘留會讓下一個拿到同 id 的人冒名
		if _peer_names.erase(id):
			# 代號跟著人走，放回池子讓後面的人可以撿。代價是離開者的名字之後可能
			# 由別人接手——聊天記錄裡的舊訊息會變成「同名不同人」。在一場大廳內
			# 可接受（換到的人本來就看不到你之前的對話），換取的是代號不會用完。
			apply_names.rpc(_peer_names)
		_redeem_pending.erase(id)   # 未決兌換也要跟著清；序號機制另外擋住晚到的回呼寫錯身份
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
	# 只清這兩位實際佔著的樁，不是整個議題的——這 1.5 秒內若有第三位玩家坐上
	# 空出來的樁，無條件清空會把他的座位一起清掉（人還坐著、樁卻顯示是空的）。
	for t in _TOPIC_TRUNKS[topic]:
		if _occupancy.has(t) and peer_ids.has(_occupancy[t]):
			_occupancy.erase(t)
			clear_trunk.rpc(t)
	for pid in peer_ids:
		var p = get_node_or_null(str(pid))
		if p:
			p.queue_free()   # server free → MultiplayerSpawner 複製移除給所有 peer
	# 善後做完才解除在途旗標——從建房 HTTP 送出到這裡，這個議題的座位一直
	# 處於「已配對、待清理」的狀態，不該讓新的人插進來。
	_matching_topics.erase(topic)

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

# 只有配對到的兩位收到：關等待視窗、通知宿主頁跳轉到配對聊天室。
# topic_id/room_id 來自後端建房回應（server 轉發，client 不能自己編）。
@rpc("authority", "reliable")
func match_found(topic_id: int, room_id: String) -> void:
	_show_waiting(false)
	_notify("配對成功，正在前往聊天室…")
	if OS.has_feature("web") and topic_id > 0:
		# 用 JSON.stringify 組 payload：room_id 是後端 uuid4().hex（僅 [0-9a-f]），
		# 但不靠這個假設——經過序列化就不存在字串拼接的跳脫問題。
		# targetOrigin 用當前 origin：iframe 與宿主頁同源（部署拓樸如此），
		# 不用 '*'，訊息不會漏給其他來源的視窗。
		var payload := JSON.stringify({
			"type": "bridgeus_match",
			"topic_id": topic_id,
			"room_id": room_id,
		})
		JavaScriptBridge.eval(
			"window.parent.postMessage(%s, window.location.origin)" % payload, true)

@rpc("authority", "reliable")
func seat_denied(msg: String) -> void:
	_notify(msg)

func _show_waiting(v: bool) -> void:
	_waiting_panel.visible = v
