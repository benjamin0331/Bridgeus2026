extends Node
# 與 Django 後端唯一的出入口。所有 network-facing 呼叫都走這裡，
# 換後端只要改這一個檔。底層用 Godot 原生 HTTPRequest，無第三方套件。

# 桌面開發預設值；Web 版於 _ready() 依同源拓樸改寫（見 godot-web-deployment-spec.md §3）。
var BASE_URL := "http://localhost:8005/api"

var access_token := ""   # 二擇一來源：acquire_token_from_host()（正式）或 guest_login()（本機測試 fallback）
var user_id := 0         # 主功能後端 user id，隨 host token 一併交接；guest_login 沒有對應 id，維持 0

# 只在常駐 headless server 從環境變數讀到才會有值；web client 一律空字串，
# 不會/不該呼叫需要它的方法（金鑰絕不可流向瀏覽器，見部署規格 §4「服務金鑰建房契約」）。
var service_token := ""

func _ready() -> void:
	if OS.has_feature("web"):
		var override = JavaScriptBridge.eval("window.bridgeus_api_base || ''", true)
		if typeof(override) == TYPE_STRING and override != "":
			BASE_URL = override
		else:
			BASE_URL = "/api"   # 同源拓樸預設：相對路徑，瀏覽器自動補目前 origin
	else:
		service_token = OS.get_environment("GODOT_SERVICE_TOKEN")

# --- 認證：正式交接（優先）-------------------------------------------------
# 由主功能頁面把這個場景嵌進 <iframe> 前，設定 window.bridgeus_token /
# window.bridgeus_user_id 交給 Godot（見 docs/godot-backend-integration.md §1）。
# 只有 Web 匯出才讀得到 window；桌面開發直接回 false，呼叫端可退回 guest_login 測試。
# 回 true 代表已經拿到主功能的真登入 token，access_token/user_id 就緒可直接打其他 API。
func acquire_token_from_host() -> bool:
	if not OS.has_feature("web"):
		return false
	var token = JavaScriptBridge.eval("window.bridgeus_token || ''", true)
	if typeof(token) != TYPE_STRING or token == "":
		return false
	access_token = token
	var uid = JavaScriptBridge.eval("window.bridgeus_user_id || 0", true)
	if typeof(uid) == TYPE_FLOAT or typeof(uid) == TYPE_INT:
		user_id = int(uid)
	return true

# --- 認證：訪客登入（本機測試 fallback，非正式使用者）----------------------
# ponytail: 純粹方便沒有主功能可交接 token 時（桌面開發、多開測試）也能跑通議題流程。
# 正式環境一律先呼叫 acquire_token_from_host()，拿到 host token 就不會走這條。
# 成功（code 201）後把 access_token 存起來。callback 形如 func(code: int, data: Dictionary)。
func guest_login(nickname: String, callback := Callable()) -> void:
	var payload := {"nickname": nickname}
	_post("/guest/", payload, false, func(code, data):
		if code == 201:
			access_token = data.get("access", "")
		if callback.is_valid():
			callback.call(code, data)
	)

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
# （見 player_00.gd 的 backend_user_id 與 game.gd 的 _do_seat）。
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

# --- 內部：帶服務金鑰的 POST（配對建房專用，不帶 user JWT）-------------------
func _post_with_service_token(path: String, payload: Dictionary, done: Callable) -> void:
	var http := HTTPRequest.new()
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
