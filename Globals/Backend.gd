extends Node
# 與 Django 後端唯一的出入口。所有 network-facing 呼叫都走這裡，
# 換後端只要改這一個檔。底層用 Godot 原生 HTTPRequest，無第三方套件。

const BASE_URL := "http://localhost:8005/api"

var access_token := ""   # guest_login 成功後存這裡，submit_issue 會帶上

# --- 認證 -----------------------------------------------------------------
# 訪客登入。成功（code 201）後把 access_token 存起來。
# callback 形如 func(code: int, data: Dictionary)。
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
