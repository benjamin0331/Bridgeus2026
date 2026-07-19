extends Node
# 與 Django 後端唯一的出入口。所有 network-facing 呼叫都走這裡，
# 換後端只要改這一個檔。底層用 Godot 原生 HTTPRequest，無第三方套件。

const BASE_URL := "http://localhost:8005/api"

var access_token := ""   # 二擇一來源：acquire_token_from_host()（正式）或 guest_login()（本機測試 fallback）
var user_id := 0         # 主功能後端 user id，隨 host token 一併交接；guest_login 沒有對應 id，維持 0

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
func submit_issue(title: String, body_text: String, callback := Callable()) -> void:
	var payload := {"title": title, "body": body_text}
	_post("/issues/", payload, true, func(code, data):
		if callback.is_valid():
			callback.call(code, data)
	)

# --- 內部：POST 一次性請求 ------------------------------------------------
# with_auth=true 時帶上 Bearer token。done 形如 func(code: int, data: Dictionary)。
func _post(path: String, payload: Dictionary, with_auth: bool, done: Callable) -> void:
	var http := HTTPRequest.new()
	add_child(http)
	var headers := ["Content-Type: application/json"]
	if with_auth:
		headers.append("Authorization: Bearer " + access_token)
	http.request_completed.connect(func(_result, code, _headers, body):
		var body_str: String = body.get_string_from_utf8()
		var data := {}
		# 用 JSON.new().parse 而非 parse_string：非 JSON 回應（如 404 HTML 頁）
		# 只回錯誤碼、不會在 console 噴紅字，這樣真正的 HTTP code 才看得清楚。
		var json := JSON.new()
		if json.parse(body_str) == OK and json.data is Dictionary:
			data = json.data
		http.queue_free()       # 一次性，用完即丟
		done.call(code, data)
	)
	var err := http.request(BASE_URL + path, headers, HTTPClient.METHOD_POST, JSON.stringify(payload))
	if err != OK:
		push_error("Backend 請求失敗 %s err=%d" % [path, err])
		http.queue_free()
		done.call(0, {})   # code 0 = 連請求都送不出去（網路層失敗）

# --- 議題配對（後端接手前為 no-op）---------------------------------------
# 兩位玩家在遊戲內選同一議題並坐上木樁後呼叫。後端之後在此建立 match room，
# 回傳 { room_id, ws_url } 供跳轉網頁聊天室。規格見 docs/topic-match-backend.md。
# callback 形如 func(code: int, data: Dictionary)。
func request_topic_match(topic: String, peer_ids: Array, callback := Callable()) -> void:
	print("[Backend stub] request_topic_match topic=%s peers=%s" % [topic, str(peer_ids)])
	# TODO(後端)：POST /api/matching/rooms/ {topic_id, users} → 回傳 room_id / ws_url
	if callback.is_valid():
		callback.call(0, {})   # code 0 = stub，尚未接後端


# --- 保留：分析 hook（後端尚未支援，先放 null） --------------------------
func analyze_stance(_title: String, _body: String):
	return null

func analyze_emotion(_text: String):
	return null
