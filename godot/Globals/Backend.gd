extends Node
# 與 Django 後端唯一的出入口。所有 network-facing 呼叫都走這裡，
# 換後端只要改這一個檔。底層用 Godot 原生 HTTPRequest，無第三方套件。

# 桌面開發預設值；Web 版於 _ready() 依同源拓樸改寫（見 godot-web-deployment-spec.md §3）。
var BASE_URL := "http://localhost:8005/api"

var access_token := ""   # 來自 acquire_token_from_host()：主功能頁交接的真登入 JWT

# 只在常駐 headless server 從環境變數讀到才會有值；web client 一律空字串，
# 不會/不該呼叫需要它的方法（金鑰絕不可流向瀏覽器，見部署規格 §4「服務金鑰建房契約」）。
var service_token := ""

func _ready() -> void:
	if OS.has_feature("web"):
		var override = JavaScriptBridge.eval("window.bridgeus_api_base || ''", true)
		# HTTPRequest 是 Godot 原生網路層，不是瀏覽器的 fetch——不會自動幫相對
		# 路徑補上目前頁面的 origin，餵它「/api」這種缺 scheme/host 的路徑會在
		# _parse_url 直接失敗（err=31，實測踩過）。宿主頁交接的 bridgeus_api_base
		# 是相對路徑（同源拓樸，前端不必自己組 origin），所以補 origin 的責任
		# 落在這裡。
		var origin = JavaScriptBridge.eval("window.location.origin", true)
		if typeof(override) == TYPE_STRING and override != "":
			BASE_URL = str(origin) + override if override.begins_with("/") else override
		else:
			BASE_URL = str(origin) + "/api"   # 同源拓樸預設
	else:
		service_token = OS.get_environment("GODOT_SERVICE_TOKEN")
		# 桌面開發沒設這個變數時維持上面宣告的 localhost 預設，行為不變。
		# 常駐 server 進了 Docker 之後 localhost 指的是 container 自己，不是
		# 跑 Django 的 host——這裡沒改到、只改了 service_token 的那個版本，
		# 就是「WS 握手成功、票券兌換卻默默連不上」這個症狀的成因（兌換是
		# server 對 server 的呼叫，不經過 nginx，所以 Web 版的同源機制救不到它）。
		var base_url_override = OS.get_environment("GODOT_BACKEND_BASE_URL")
		if base_url_override != "":
			BASE_URL = base_url_override

# --- 認證：正式交接（唯一途徑）----------------------------------------------
# 由主功能頁面把這個場景嵌進 <iframe> 前，設定 window.bridgeus_token
# 交給 Godot（見 docs/godot-backend-integration.md §1）。
# 只有 Web 匯出才讀得到 window；桌面開發直接回 false。
# 回 true 代表已經拿到主功能的真登入 token，access_token 就緒可直接打其他 API。
func acquire_token_from_host() -> bool:
	if not OS.has_feature("web"):
		return false
	var token = JavaScriptBridge.eval("window.bridgeus_token || ''", true)
	if typeof(token) != TYPE_STRING or token == "":
		return false
	access_token = token
	return true

# 輪詢間隔與次數：券 TTL 60 秒，多等不花成本；15 秒上限是為了「Cloudflare Tunnel
# 一跳 + Django round trip + 校園網路」留夠餘裕——失敗的代價是硬失敗（見 Important 1）。
const _TICKET_POLL_INTERVAL := 0.1
const _TICKET_POLL_TRIES := 150   # 0.1s * 150 = 15s

# request_entry_ticket() 的重入守衛：window.bridgeus_ticket 是共用全域，沒有請求
# 身份的概念，兩個 coroutine 同時輪詢會讀到同一張券，而後端兌換是 CAS，
# 第二個 peer 會被踢且查不出原因。目前只有 _on_join_pressed 的 hide_buttons()
# 巧合地擋住重複呼叫，這裡加明確守衛而不依賴呼叫端的 UI 狀態。
var _ticket_in_flight := false

# 最近一次 request_entry_ticket() 失敗的原因代碼，供 UI 顯示可行動的訊息用。
# 回傳值仍然只有「拿到／沒拿到」，因為呼叫端的控制流只有兩條；但研究情境下
# 受試者看不到 console，訊息必須說得出「該做什麼」。
# 值：""=成功、"not_web"、"no_host"、"denied"（宿主頁回報 ERROR，多半是未登入
# 或後端錯）、"timeout"、"busy"（重入守衛擋下）。
var last_ticket_error := ""

# --- 入場券：client 端拉券（Web 專用）---------------------------------------
# 每次要連線前呼叫。向宿主頁（GodotLobby.jsx）要一張一次性入場券，輪詢等結果。
# 「拉」而不是 iframe 載入時「推」：券 60 秒到期且一次性，撐不過 WASM 冷啟動，
# 重連時更會拿著已兌換的券被踢（見 spec §5.1）。
# 回空字串代表要不到；用 last_ticket_error 分辨原因。
#
# 呼叫端必須 await。開頭無條件讓出一幀，是為了讓桌面與 Web 的行為一致：
# 否則桌面在 OS.has_feature("web") 為 false 時會在碰到任何 await 之前就 return
# （回真的 String），Web 才會在第一個 await 處 suspend（回 completion Signal）。
# 兩種回傳型別在 GDScript 呼叫端看起來都能後面加 `.xxx` 而不報錯，漏寫 await
# 的錯誤只會在桌面雙開測試——也就是本階段的驗收方式——完全看不出來，等到
# 正式 Web 環境才爆。
#
# 取捨：審查建議改用 JavaScriptBridge.create_callback() 消掉輪詢/三態協定/
# 'ERROR' 哨兵與重入問題，這是 Godot 4 更慣用的做法。不採用是因為 Task 1 的
# window.bridgeus_ticket 三態契約已經 commit（改動範圍會外溢到 GodotLobby.jsx），
# 且 JS callback 物件需要用成員變數持有以避免被 GC——複雜度是轉移，不是消失。
func request_entry_ticket() -> String:
	await get_tree().process_frame
	if _ticket_in_flight:
		last_ticket_error = "busy"
		return ""
	_ticket_in_flight = true
	var ticket: String = await _request_entry_ticket_inner()
	_ticket_in_flight = false
	return ticket

func _request_entry_ticket_inner() -> String:
	if not OS.has_feature("web"):
		last_ticket_error = "not_web"
		return ""
	# 直開 export（沒有宿主頁）快速失敗，不空等。
	var has_fn = JavaScriptBridge.eval(
		"typeof window.bridgeus_request_ticket == 'function'", true)
	# 實測發現 JavaScriptBridge.eval() 在 Web 匯出把 JS 布林值轉回來時，
	# 有時是 GDScript 的 int（0/1）而不是 bool；int != bool 這個組合在
	# GDScript 是不合法運算，會直接丟 SCRIPT ERROR 中止整個函式（曾經在這裡
	# 寫成 `has_fn != true` 求跟其他 eval 檢查風格一致，結果每次呼叫都炸掉，
	# 且錯誤不會往外傳播，症狀只會是「入場券要不到」，很難聯想到這裡）。
	# `not` 走真值判斷（truthy coercion），int 跟 bool 都吃得下，才是安全的寫法。
	if not has_fn:
		last_ticket_error = "no_host"
		return ""
	JavaScriptBridge.eval("window.bridgeus_request_ticket()", true)
	for _i in _TICKET_POLL_TRIES:
		await get_tree().create_timer(_TICKET_POLL_INTERVAL).timeout
		# 耦合提醒：宿主頁 bridgeus_request_ticket() 第一件事就是把
		# window.bridgeus_ticket 同步清成 ''，所以這裡第一次輪詢不會讀到
		# 上一輪的殘值。安全性來自 GodotLobby.jsx 那邊的語句順序，不是這裡
		# 保證的——未來重構那支函式時要留意別打破這個順序。
		var t = JavaScriptBridge.eval("window.bridgeus_ticket || ''", true)
		if typeof(t) == TYPE_STRING and t != "":
			if t == "ERROR":
				last_ticket_error = "denied"
				return ""
			last_ticket_error = ""
			return t
	last_ticket_error = "timeout"
	return ""

# --- 議題 -----------------------------------------------------------------
# 送出議題。需先登入（header 帶 Authorization: Bearer <access_token>）。
# 成功（201）data 會帶 issue id，呼叫端要拿它做表情回復（見 game.gd _submit_issue）。
func submit_issue(title: String, body_text: String, callback := Callable()) -> void:
	var payload := {"title": title, "body": body_text}
	_post("/issues/", payload, true, func(code, data):
		if callback.is_valid():
			callback.call(code, data)
	)

# 讀議題清單，data 是陣列 [{id,title,body,author_id,created_at},...]。需登入。
# 保留給日後「議題總覽」之類功能，目前沒有 UI 消費它（seam 完整用）。
func get_issues(callback := Callable()) -> void:
	_http_get("/issues/", true, func(code, data):
		if callback.is_valid():
			callback.call(code, data)
	)

# --- 頭銜 Titles（見 godot-backend-integration.md §3.1）--------------------
# 讀自己擁有的頭銜清單＋目前選哪個。需登入。
# data 形如 {"owned":[{"id":1,"name":"探索者"},...], "selected_id":1, "color":"#2680d9"}。
func get_my_titles(callback := Callable()) -> void:
	_http_get("/titles/me/", true, func(code, data):
		if callback.is_valid():
			callback.call(code, data)
	)

# 設定目前顯示的頭銜。title_id <= 0 代表「不顯示頭銜」（送 null 給後端）；
# color 為 "#RRGGBB"（空字串代表不改色）。需登入。
func set_my_title(title_id: int, color: String, callback := Callable()) -> void:
	var payload := {"title_id": title_id if title_id > 0 else null}
	if color != "":
		payload["color"] = color
	_post("/titles/me/", payload, true, func(code, data):
		if callback.is_valid():
			callback.call(code, data)
	)

# --- 議題表情回復 Reactions（見 §3.2）------------------------------------
# 對某則議題送/改表情（emoji_index 0-4）。一人一議題一個、重送覆蓋（後端 upsert）。
# 頭上顯示走 Godot P2P；這支只負責把研究資料落地。需登入。
func add_issue_reaction(issue_id: int, emoji_index: int, callback := Callable()) -> void:
	_post("/issues/%d/reactions/" % issue_id, {"emoji_index": emoji_index}, true, func(code, data):
		if callback.is_valid():
			callback.call(code, data)
	)

# 讀某則議題的表情統計。data 形如 {"counts":{"0":2,"3":5}, "mine":3}。需登入。
# 目前 UI 用 P2P 即時顯示，這支保留給重連還原/離線分析用，尚未接 UI（seam 完整用）。
func get_issue_reactions(issue_id: int, callback := Callable()) -> void:
	_http_get("/issues/%d/reactions/" % issue_id, true, func(code, data):
		if callback.is_valid():
			callback.call(code, data)
	)

# --- 內部：一次性 HTTP 請求 -----------------------------------------------
# with_auth=true 時帶 Bearer token。payload=null 代表無 body（GET 用）。
# done 形如 func(code: int, data)——data 可能是 Dictionary 或 Array（/issues/ 回陣列）。
func _request(method: int, path: String, payload, with_auth: bool, done: Callable) -> void:
	var http := HTTPRequest.new()
	http.timeout = 10.0   # 沒有 timeout 的話，卡住的連線永遠不會回呼——呼叫端
	                      # 若有在途狀態（例如 game.gd 的 _matching_topics）就會
	                      # 永久卡死。逾時會以 result=TIMEOUT、code=0 走正常回呼路徑。
	add_child(http)
	var headers := []
	if payload != null:
		headers.append("Content-Type: application/json")
	if with_auth:
		headers.append("Authorization: Bearer " + access_token)
	http.request_completed.connect(func(_result, code, _headers, body):
		var body_str: String = body.get_string_from_utf8()
		# 用 JSON.new().parse 而非 parse_string：非 JSON 回應（如 404 HTML 頁）
		# 只回錯誤碼、不會在 console 噴紅字，這樣真正的 HTTP code 才看得清楚。
		var data = {}
		var json := JSON.new()
		if json.parse(body_str) == OK:
			data = json.data   # Dictionary 或 Array 都保留，讓呼叫端自己判型別
		http.queue_free()       # 一次性，用完即丟
		done.call(code, data)
	)
	var out_body := JSON.stringify(payload) if payload != null else ""
	var err := http.request(BASE_URL + path, headers, method, out_body)
	if err != OK:
		push_error("Backend 請求失敗 %s err=%d" % [path, err])
		http.queue_free()
		done.call(0, {})   # code 0 = 連請求都送不出去（網路層失敗）

func _post(path: String, payload: Dictionary, with_auth: bool, done: Callable) -> void:
	_request(HTTPClient.METHOD_POST, path, payload, with_auth, done)

func _http_get(path: String, with_auth: bool, done: Callable) -> void:
	_request(HTTPClient.METHOD_GET, path, null, with_auth, done)

# --- 議題配對 → 建聊天室 ---------------------------------------------------
# 兩位玩家在遊戲內選同一議題並坐上木樁後，由 server 端（唯一 authority）呼叫一次。
# 後端端點 POST /api/godot/match-rooms/ 已上線（backend/api/views.py
# GodotMatchRoomView）。回 201 = 新建房間，200 = 同一對人同議題已有進行中的房間、
# 沿用既有的（冪等，重試不會開出第二間）；兩者的 data 都有 room_id / redirect_url。
# 契約見 godot-backend-integration.md §3.3：要傳的是後端 user_id，不是 Godot peer_id
# （user_id 來自 game.gd 的 _peer_users 身份表，由入場券兌換而來）。
# 身份驗證用共用服務金鑰（X-Godot-Service-Token）；只有 headless server 有 service_token，
# web client 一律不會/不該呼叫到這裡（金鑰不流向瀏覽器，見部署規格 §4）。
# callback 形如 func(code: int, data: Dictionary)。
const _TOPIC_ID_MAP := {
	"nuclear_energy": 102,
	"women_soldier": 103,
}

func request_topic_match(topic: String, user_ids: Array, callback := Callable()) -> void:
	if service_token == "":
		push_warning("request_topic_match 需要 GODOT_SERVICE_TOKEN（僅 headless server 該有），略過")
		if callback.is_valid():
			callback.call(0, {})
		return
	var payload := {
		"topic": topic,
		"topic_id": _TOPIC_ID_MAP.get(topic, 0),
		"user_ids": user_ids,
	}
	_post_with_service_token("/godot/match-rooms/", payload, func(code, data):
		if callback.is_valid():
			callback.call(code, data)
	)

# --- 入場券：server 端兌換（僅 headless dedicated server 呼叫）---------------
# 把 client 遞來的券換成後端 user_id。成功 callback(user_id > 0)；
# 任何失敗（券無效/已用過/逾期/網路錯）一律 callback(0)——呼叫端的處置只有
# 「踢掉這個 peer」一種，不需要區分原因（後端 log 有記，見階段一 spec §5.3）。
# callback 這裡是必填（不像檔案裡其他公開函式用 `:= Callable()` 加 is_valid()
# 守衛）：故意不一致——兌換沒有 callback 就沒有意義，呼叫端一定要處理結果。
func redeem_ticket(ticket: String, callback: Callable) -> void:
	if ticket == "":
		callback.call(0)
		return
	if service_token == "":
		# 正式流程走不到這裡：dev host 在 submit_ticket 就該早退。真的走到
		# 代表部署設定漏了 GODOT_SERVICE_TOKEN，跟 request_topic_match 一樣出聲。
		push_warning("redeem_ticket 需要 GODOT_SERVICE_TOKEN（僅 headless server 該有），略過")
		callback.call(0)
		return
	_post_with_service_token("/godot/tickets/redeem/", {"ticket": ticket}, func(code, data):
		callback.call(int(data.get("user_id", 0)) if code == 200 else 0)
	)

# --- 內部：帶服務金鑰的 POST（配對建房專用，不帶 user JWT）-------------------
func _post_with_service_token(path: String, payload: Dictionary, done: Callable) -> void:
	var http := HTTPRequest.new()
	http.timeout = 10.0   # 沒有 timeout 的話，卡住的連線永遠不會回呼——呼叫端
	                      # 若有在途狀態（例如 game.gd 的 _matching_topics）就會
	                      # 永久卡死。逾時會以 result=TIMEOUT、code=0 走正常回呼路徑。
	add_child(http)
	var headers := [
		"Content-Type: application/json",
		"X-Godot-Service-Token: " + service_token,
	]
	http.request_completed.connect(func(_result, code, _headers, body):
		var body_str: String = body.get_string_from_utf8()
		var data := {}
		var json := JSON.new()
		if json.parse(body_str) == OK and json.data is Dictionary:
			data = json.data
		http.queue_free()
		done.call(code, data)
	)
	var err := http.request(BASE_URL + path, headers, HTTPClient.METHOD_POST, JSON.stringify(payload))
	if err != OK:
		push_error("Backend 請求失敗 %s err=%d" % [path, err])
		http.queue_free()
		done.call(0, {})


# --- 保留：分析 hook（後端尚未支援，先放 null） --------------------------
func analyze_stance(_title: String, _body: String):
	return null

func analyze_emotion(_text: String):
	return null
