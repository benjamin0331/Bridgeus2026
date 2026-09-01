# Godot Deployment Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the `godot/` prototype's code to the state described in `godot-web-deployment-spec.md` §3–§4 and `godot-backend-integration.md` §3.3 — configurable endpoints, a dedicated-server code path, a real (if currently 404ing) match-room call keyed on backend `user_id` — so that the *remaining* blockers to going live are purely operational (export the Web build, stand up the headless server, wire the frontend iframe, ship the backend's P1 endpoints), not code.

**Architecture:** Two files carry all of this: `godot/Globals/Backend.gd` (the sole HTTP seam — gets a configurable `BASE_URL` and a service-token-authenticated `request_topic_match`) and `godot/World/game.gd` (the sole owner of the `WebSocketMultiplayerPeer` — gets a dedicated-server branch, configurable connection address, a keepalive heartbeat, and a signal-based handshake). `godot/Entities/player/player_00.gd` + its `.tscn` gain one synced field (`backend_user_id`) because the match-room call needs backend user ids, not Godot peer ids, and nothing currently carries that mapping across the network.

**Tech Stack:** Godot 4.7 / GDScript. **No automated test harness exists for this project** (`godot/CLAUDE.md`: "No build/lint/test CLI — this is an editor-driven Godot project") and no Godot binary is available in this environment (`which godot` → not found). Every task's "verification" step is therefore static (re-read the diff, `grep` for the exact symbols that must exist) rather than a passing test run. Task 9 lays out the manual, editor-based smoke test someone with Godot 4.7 installed must run before this is truly "ready" — that step cannot be completed by this plan's executor.

**Scope boundary (read before starting):** This plan only touches `godot/`. Explicitly **out of scope**, tracked elsewhere, and not to be started here:
- Frontend `/chat` iframe + token handoff (`frontend/src/App.jsx` etc.) — separate app, separate team concern.
- Actually creating `godot/export_presets.cfg` / running an export — requires the Godot editor and export templates, neither available here.
- Backend `POST /api/godot/match-rooms/`, `GODOT_SERVICE_TOKEN` env, titles/reactions endpoints — backend team's P1 work (`godot-backend-gap-analysis.md`).
- systemd unit files, Cloudflare Tunnel ingress config — infra, not application code.

---

## Task 1: `backend_user_id` on the player — sync backend identity across peers

**Why first:** Task 6 (match-room call) needs each seated player's backend `user_id`. Nothing currently carries it past `Backend.user_id` on the local authority peer — it has to be added to the replicated player state before anything can read it off a *remote* player.

**Files:**
- Modify: `godot/Entities/player/player_00.gd:15-16` (var declarations), `:68-74` (`_ready()`)
- Modify: `godot/Entities/player/player_00.tscn` (SceneReplicationConfig sub-resource)

- [ ] **Step 1: Add the field**

In `godot/Entities/player/player_00.gd`, immediately after:
```gdscript
var issue_title := ""
var issue_body := ""
```
insert:
```gdscript

# 後端 user id（M3 配對建房需要，見 game.gd _do_seat 與 Backend.gd request_topic_match）。
# 由 authority 在 _ready() 從 Backend.user_id 帶入；透過 MultiplayerSynchronizer 同步
# （spawn=true，晚進的人也拿得到），這樣 server 端才查得到「這個 peer 對應哪個後端使用者」。
# guest_login 測試帳號沒有對應 id，會是 0——只在本機測試情境出現，正式流程不會。
var backend_user_id: int = 0
```

- [ ] **Step 2: Set it at spawn (authority-only, same reasoning as `position`)**

In the same file's `_ready()`, immediately after:
```gdscript
	var spawn = get_parent().get_node_or_null("SpawnPoint")
	if spawn:
		position = spawn.position
```
insert:
```gdscript
	# 後端 user id：同理必須由 authority 自己設（見上方欄位註解），才能透過
	# synchronizer 正確同步給其他 peer；guest 測試帳號沒有對應 id，維持 0。
	backend_user_id = Backend.user_id
```

- [ ] **Step 3: Add it to the MultiplayerSynchronizer's replication list**

In `godot/Entities/player/player_00.tscn`, the `SceneReplicationConfig_dls3q` sub-resource currently ends with:
```
properties/3/path = NodePath(".:moving")
properties/3/spawn = true
properties/3/replication_mode = 1
```
Append immediately after it (before the blank line / next `[sub_resource ...]` block):
```
properties/4/path = NodePath(".:backend_user_id")
properties/4/spawn = true
properties/4/replication_mode = 1
```

- [ ] **Step 4: Verify statically**

```bash
grep -n "backend_user_id" godot/Entities/player/player_00.gd godot/Entities/player/player_00.tscn
```
Expected: 3 hits in the `.gd` file (declaration, comment reference is part of the same block, assignment) and 1 hit in the `.tscn` (the `properties/4/path` line). Confirm the `.tscn` still parses as valid Godot resource text — no unmatched braces, the new lines sit inside the same `SceneReplicationConfig` block as `properties/0-3`.

- [ ] **Step 5: Commit**

```bash
git add godot/Entities/player/player_00.gd godot/Entities/player/player_00.tscn
git commit -m "feat(godot): sync backend_user_id across peers for match-room handoff"
```

---

## Task 2: `game.gd` — dedicated-server detection + configurable connection address

**Why:** A browser tab can never `create_server` (deployment spec §4). Production needs one headless, always-on Godot instance acting as the sole server, and every other instance (including Web clients) as a client connecting to a configurable, non-`127.0.0.1` address.

**Files:**
- Modify: `godot/World/game.gd:1-48` (top consts + `_ready()`)

- [ ] **Step 1: Replace the hardcoded connection consts**

Replace:
```gdscript
var peer = WebSocketMultiplayerPeer.new()
const PORT = 8080
const ADDRESS = "127.0.0.1"
```
with:
```gdscript
var peer = WebSocketMultiplayerPeer.new()
const DEFAULT_PORT := 8080
const DEFAULT_ADDRESS := "127.0.0.1"
var _ws_url := ""   # 由 _resolve_connection_settings() 在 _ready() 填入，Join/dedicated server 都讀這個
```

- [ ] **Step 2: Restructure `_ready()` — resolve address, branch to dedicated-server, hide Host on Web**

Replace the full `_ready()` body:
```gdscript
func _ready():
	host_btn.pressed.connect(_on_host_pressed)
	join_btn.pressed.connect(_on_join_pressed)
	# 提交表單的送出/取消（IssueButton 本身在場景裡已連到 _on_issue_button_pressed）
	$CanvasLayer_issue/IssueForm/SubmitButton.pressed.connect(_submit_issue)
	$CanvasLayer_issue/IssueForm/CancelButton.pressed.connect(_cancel_submit)
	$CanvasLayer_issue/IssueForm/ClearButton.pressed.connect(_clear_form)
	$CanvasLayer_issue/IssueForm/CloseButton.pressed.connect(_close_form)
	_setup_banner_ui()
	$WaitingLayer/Panel/VBox/CancelButton.pressed.connect(_cancel_wait)
	multiplayer.peer_disconnected.connect(_on_peer_disconnected)

	# 身份交接：優先用主功能登入的真 JWT（window.bridgeus_token，見 Backend.gd）。
	# 桌面開發、或還沒從主功能進來時，acquire_token_from_host() 回 false，
	# 退回訪客登入方便本機測試——純測試用，不是正式使用者，正式環境不會走到這條。
	if Backend.acquire_token_from_host():
		print("已取得主功能登入 token，user_id=%d" % Backend.user_id)
	else:
		# ponytail: 暱稱先寫死「訪客」；之後有登入輸入框再換成玩家輸入。
		Backend.guest_login("訪客", func(code, data):
			if code == 201:
				print("訪客登入成功（測試用）")
			else:
				push_warning("訪客登入失敗 code=%d data=%s" % [code, data])
		)
```
with:
```gdscript
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

	# 身份交接：優先用主功能登入的真 JWT（window.bridgeus_token，見 Backend.gd）。
	# 桌面開發、或還沒從主功能進來時，acquire_token_from_host() 回 false，
	# 退回訪客登入方便本機測試——純測試用，不是正式使用者，正式環境不會走到這條。
	if Backend.acquire_token_from_host():
		print("已取得主功能登入 token，user_id=%d" % Backend.user_id)
	else:
		# ponytail: 暱稱先寫死「訪客」；之後有登入輸入框再換成玩家輸入。
		Backend.guest_login("訪客", func(code, data):
			if code == 201:
				print("訪客登入成功（測試用）")
			else:
				push_warning("訪客登入失敗 code=%d data=%s" % [code, data])
		)

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
```

- [ ] **Step 3: Point `_on_host_pressed` / `_on_join_pressed` at the renamed const/var**

Replace:
```gdscript
func _on_host_pressed() -> void:
	var error = peer.create_server(PORT)
```
with:
```gdscript
func _on_host_pressed() -> void:
	var error = peer.create_server(DEFAULT_PORT)
```

Replace:
```gdscript
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
```
with (this also completes Task 4's handshake fix — see rationale there):
```gdscript
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
```

- [ ] **Step 4: Verify statically**

```bash
grep -n "PORT\b\|ADDRESS\b" godot/World/game.gd
```
Expected: only `DEFAULT_PORT` / `DEFAULT_ADDRESS` remain — no bare `PORT`/`ADDRESS` references left (old consts fully replaced).

```bash
grep -n "_is_dedicated_server\|_start_dedicated_server\|_resolve_connection_settings\|_on_connected_to_server" godot/World/game.gd
```
Expected: each name appears at both its definition and its call site (`_is_dedicated_server` and `_resolve_connection_settings` called from `_ready`; `_start_dedicated_server` called from `_ready`'s branch; `_on_connected_to_server` connected in `_on_join_pressed` and defined once).

- [ ] **Step 5: Commit**

```bash
git add godot/World/game.gd
git commit -m "feat(godot): add dedicated-server branch and configurable connection address"
```

---

## Task 3: `game.gd` — heartbeat to survive Cloudflare's WS idle timeout

**Why:** Deployment spec §4 point 3 — Cloudflare closes idle WebSocket connections around ~100s. A player standing still with no chat/voice activity would get silently disconnected.

**Files:**
- Modify: `godot/World/game.gd` (`_process`, plus one new const/var and one new `@rpc`)

- [ ] **Step 1: Add the interval const and accumulator, and give `_process` its delta**

Replace:
```gdscript
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
```
with:
```gdscript
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
```

- [ ] **Step 2: Add the empty heartbeat RPC**

Add near the other `@rpc` methods (e.g. right after `hide_buttons()`):
```gdscript
# 空心跳：內容不重要，重點是「有資料在傳」讓代理層（Cloudflare）不判定閒置。
@rpc("any_peer", "unreliable")
func _heartbeat() -> void:
	pass
```

- [ ] **Step 3: Verify statically**

```bash
grep -n "HEARTBEAT_INTERVAL\|_heartbeat\b\|func _process" godot/World/game.gd
```
Expected: `HEARTBEAT_INTERVAL` const, `_heartbeat_elapsed` var, the `_process(delta)` signature (not `_delta`), a call site inside `_process`, and the `func _heartbeat()` definition with its `@rpc` annotation directly above.

- [ ] **Step 4: Commit**

```bash
git add godot/World/game.gd
git commit -m "feat(godot): add WS heartbeat to survive Cloudflare idle timeout"
```

---

## Task 4: `game.gd` — pass backend `user_id`s (not peer_ids) to the match-room call

**Why:** `godot-backend-integration.md` §3.3 and the gap-analysis both flag this: the backend's future `/api/godot/match-rooms/` endpoint identifies people by their Django `user_id`, which Godot peer_ids have no relationship to. Task 1 added `backend_user_id` to the player; this task is the one call site that needs to read it.

**Files:**
- Modify: `godot/World/game.gd:273-306` (`_do_seat`)

- [ ] **Step 1: Change the call site**

Replace:
```gdscript
	if occupants.size() == 2 and occupants[0] != occupants[1]:
		# 後端只在 server 端呼叫一次（兩位都打會建兩間房）。
		Backend.request_topic_match(topic, occupants)
```
with:
```gdscript
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
```

(Everything below this — the `for pid in occupants: ... match_found()/.rpc_id(pid)` loop and `_finish_match(topic, occupants)` — stays exactly as-is: those still address players by Godot peer_id, which is correct for RPCs and node cleanup. Only the `Backend.request_topic_match` argument changes.)

- [ ] **Step 2: Verify statically**

```bash
grep -n "request_topic_match\|user_ids" godot/World/game.gd
```
Expected: one call site, passing a locally-built `user_ids` array (not `occupants` directly).

- [ ] **Step 3: Commit**

```bash
git add godot/World/game.gd
git commit -m "fix(godot): pass backend user_ids instead of peer_ids to match-room request"
```

---

## Task 5: `Backend.gd` — configurable `BASE_URL` (same-origin Web default)

**Why:** Deployment spec §3 — the whole stack deploys behind one Cloudflare-tunneled domain, path-routed (same-origin topology, already decided). A hardcoded `http://localhost:8005/api` only works for desktop dev.

**Files:**
- Modify: `godot/Globals/Backend.gd:1-9`

- [ ] **Step 1: Make `BASE_URL` a `var`, add a `_ready()` override, add the (unrelated but co-located) service-token field**

Replace:
```gdscript
extends Node
# 與 Django 後端唯一的出入口。所有 network-facing 呼叫都走這裡，
# 換後端只要改這一個檔。底層用 Godot 原生 HTTPRequest，無第三方套件。

const BASE_URL := "http://localhost:8005/api"

var access_token := ""   # 二擇一來源：acquire_token_from_host()（正式）或 guest_login()（本機測試 fallback）
var user_id := 0         # 主功能後端 user id，隨 host token 一併交接；guest_login 沒有對應 id，維持 0
```
with:
```gdscript
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
```

- [ ] **Step 2: Verify statically**

```bash
grep -n "^var BASE_URL\|^const BASE_URL\|service_token\|func _ready" godot/Globals/Backend.gd
```
Expected: `var BASE_URL` (not `const`), no leftover `const BASE_URL`, `service_token` declared, and a `_ready()` that sets both.

- [ ] **Step 3: Commit**

```bash
git add godot/Globals/Backend.gd
git commit -m "feat(godot): make Backend.gd BASE_URL configurable for same-origin deployment"
```

---

## Task 6: `Backend.gd` — real `request_topic_match` (service-token POST)

**Why:** Currently a `print`-only stub. Deployment spec §4's "service token contract" (decision C, already ratified 2026-07-19 per the gap-analysis) says the headless server calls the backend's match-room endpoint with a shared `X-Godot-Service-Token` header — never a user JWT, never from a Web client. This task makes that the actual implementation, even though the backend endpoint doesn't exist yet (P1, tracked separately) — so the call 404s until the backend ships it, which is expected, not a regression.

**Files:**
- Modify: `godot/Globals/Backend.gd` (the `request_topic_match` stub and its surrounding comment)

- [ ] **Step 1: Replace the stub**

Replace:
```gdscript
# --- 議題配對（後端接手前為 no-op）---------------------------------------
# 兩位玩家在遊戲內選同一議題並坐上木樁後呼叫。後端之後在此建立 match room，
# 回傳 { room_id, ws_url } 供跳轉網頁聊天室。規格見 docs/topic-match-backend.md。
# callback 形如 func(code: int, data: Dictionary)。
func request_topic_match(topic: String, peer_ids: Array, callback := Callable()) -> void:
	print("[Backend stub] request_topic_match topic=%s peers=%s" % [topic, str(peer_ids)])
	# TODO(後端)：POST /api/matching/rooms/ {topic_id, users} → 回傳 room_id / ws_url
	if callback.is_valid():
		callback.call(0, {})   # code 0 = stub，尚未接後端
```
with:
```gdscript
# --- 議題配對 → 建聊天室 ---------------------------------------------------
# 兩位玩家在遊戲內選同一議題並坐上木樁後，由 server 端（唯一 authority）呼叫一次。
# 後端端點 POST /api/godot/match-rooms/ 尚未上線（P1，見 godot-backend-gap-analysis.md
# §5/§8）——這支呼叫在後端補上前會 404，這是預期狀態，不是這裡的 bug。
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
```

- [ ] **Step 2: Verify statically**

```bash
grep -n "request_topic_match\|_TOPIC_ID_MAP\|_post_with_service_token\|X-Godot-Service-Token" godot/Globals/Backend.gd
```
Expected: `request_topic_match(topic: String, user_ids: Array, ...)` (parameter renamed from `peer_ids`), the topic map, and `_post_with_service_token` used by it, with the header string present verbatim.

Also confirm the one caller (Task 4) matches the new signature:
```bash
grep -n "Backend.request_topic_match" godot/World/game.gd
```
Expected: called with a `user_ids` array (built in Task 4), matching this new signature positionally (signature itself is unchanged in shape — still `(topic, Array, callback)` — only the parameter's *meaning* changed, so no caller-side breakage).

- [ ] **Step 3: Commit**

```bash
git add godot/Globals/Backend.gd
git commit -m "feat(godot): implement request_topic_match as a real service-token POST"
```

---

## Task 7: Document the new architecture in `godot/CLAUDE.md`

**Why:** `godot/CLAUDE.md` is this project's load-bearing decisions log ("Conventions that bit us — don't relitigate"). Everything added in Tasks 1–6 is exactly that kind of decision (why a dedicated-server branch exists, why `BASE_URL` is a var, why the service token never touches a Web client) — future readers (including future Claude sessions) need it recorded here, not just in the plan file.

**Files:**
- Modify: `godot/CLAUDE.md` (append to `## Backend seam`; add a new `## Deployment (dedicated server)` section before `## Style`)

- [ ] **Step 1: Append to the existing `## Backend seam` section**

Find the paragraph ending `...docs/README.md §四.` (or whatever it currently reads after the prior doc-cleanup pass — locate it via `grep -n "^## Backend seam" -A 5 godot/CLAUDE.md` and confirm the paragraph boundary before editing) and append a new paragraph directly after it:

```markdown
`BASE_URL` is now a `var`, not `const`: desktop dev keeps `http://localhost:8005/api`; Web builds default to same-origin `/api` (the whole stack — frontend, API, Godot static files, Godot WS — sits behind one Cloudflare-tunneled domain, path-routed) unless the host page sets `window.bridgeus_api_base` first. `service_token` is a second, unrelated secret: read from the `GODOT_SERVICE_TOKEN` env var, **only ever populated on the headless dedicated server** (never in a Web export — `OS.has_feature("web")` is false there, and it's never set from `window.*`). `request_topic_match` is no longer a stub — it POSTs to `/godot/match-rooms/` with an `X-Godot-Service-Token` header and the two players' **backend `user_id`s**, not Godot peer_ids (see `player_00.gd`'s `backend_user_id` and `game.gd`'s `_do_seat`). The backend endpoint doesn't exist yet, so this 404s until it ships — expected, not a bug here.
```

- [ ] **Step 2: Add a new section before `## Style`**

Insert immediately before the `## Style` heading:

```markdown
## Deployment (dedicated server)

A browser tab can't `create_server` — the Web export can only ever `create_client`. `game.gd::_is_dedicated_server()` detects the headless deployment target two ways (`--server` on the command line, matching the systemd `ExecStart` ops runs; or `DisplayServer.get_name() == "headless"` as a fallback that doesn't depend on any not-yet-created export preset) and short-circuits `_ready()` into `_start_dedicated_server()`: it opens the WebSocket server and returns *before* the UI wiring, banner setup, or `Backend` identity handshake run — a dedicated server represents no player and must never call `guest_login`/`acquire_token_from_host`. On Web, `OS.has_feature("web")` hides the Host button (nothing left that could call `create_server` anyway) and leaves Join as the sole entry point. The connection address is resolved once in `_resolve_connection_settings()`: Web reads `window.bridgeus_ws_url` (falls back to the local desktop address if unset), desktop always uses the local default — replacing the old hardcoded `PORT`/`ADDRESS` constants (renamed `DEFAULT_PORT`/`DEFAULT_ADDRESS`, now only the fallback, not the only option).

A ~25s any-peer heartbeat RPC (`_heartbeat`, unreliable, empty body) fires from `_process` whenever the multiplayer peer is connected, to keep Cloudflare's WebSocket idle timeout (~100s) from closing quiet connections. The interval is a fixed constant, not configurable — it only needs to comfortably undercut the platform's timeout, not track it precisely.

`_on_join_pressed`'s first `await get_tree().create_timer(0.2).timeout` (guessing when the WebSocket handshake finished before firing `request_spawn`) is now a one-shot `connected_to_server` signal wait instead — real network latency (especially through a Cloudflare Tunnel hop) isn't guaranteed to fit in 200ms. The **second** 0.2s wait (before `request_issue_sync`, giving the `MultiplayerSpawner`'s initial replication a moment to land) is unchanged — it isn't a handshake wait and wasn't in scope for this fix.
```

- [ ] **Step 3: Verify statically**

```bash
grep -n "^## " godot/CLAUDE.md
```
Expected: a `## Deployment (dedicated server)` heading now exists between `## Backend seam` and `## Style`, in that order.

- [ ] **Step 4: Commit**

```bash
git add godot/CLAUDE.md
git commit -m "docs(godot): record dedicated-server, heartbeat, and service-token decisions"
```

---

## Task 8: Full-diff self-check against the three source specs

**Why:** Tasks 1–7 touched five files across six commits; before calling this "done," re-read the actual diff against what the three spec documents in `/Users/light/project/0723報告/godot修改/` asked for, catching anything paraphrased wrong or missed.

**Files:** none (read-only verification task)

- [ ] **Step 1: Dump the full diff for review**

```bash
git -C /Users/light/code diff main -- godot/
```

- [ ] **Step 2: Check against `godot-web-deployment-spec.md`**

Confirm every "工作項" under its §2 (Godot-side items only — 前端/後端/infra items are out of scope, see plan header), §3 point 1, and §4 points 1–4 has a corresponding change in the diff. Specifically confirm:
- §3: `BASE_URL` no longer hardcoded (Task 5).
- §4.1: dedicated-server / web-client branching exists (Task 2).
- §4.2: connection address configurable via `window.bridgeus_ws_url` (Task 2).
- §4.3: heartbeat exists (Task 3).
- §4.4: handshake improvement — first `await 0.2s` replaced by a signal (Task 2/4's combined edit).
- §4 "服務金鑰建房契約": `X-Godot-Service-Token` header, `user_ids` body field, real POST (Task 6).

- [ ] **Step 3: Check against `godot-backend-integration.md` §3.3**

Confirm `backend_user_id` exists on the player, is synced (`spawn=true` in the replication config), and is what gets passed to `Backend.request_topic_match` — not the Godot peer_id (Tasks 1 and 4).

- [ ] **Step 4: Check against `godot-backend-gap-analysis.md` §8**

Every row in that table's "🔴 完全沒有" / "🟡 stub" column for Godot-side methods should now read differently for `request_topic_match` (real implementation) — the *other* five methods (`get_issues`, `get_my_titles`, `set_my_title`, `add_issue_reaction`, `get_issue_reactions`) are correctly **still absent**, because their backend endpoints don't exist yet either (P1, not this plan's scope). Confirm nothing in the diff accidentally added stub versions of those five — that would be scope creep the plan didn't ask for.

- [ ] **Step 5: Note remaining blockers (for the human, not a code change)**

After this plan, `godot/` is code-ready per the scope boundary stated at the top. State explicitly (in the final report to the user) that these remain and are **not** addressed by this plan:
1. Actual Web export (`export_presets.cfg` + running the export — needs the Godot editor).
2. Frontend `/chat` iframe + token/ws-url handoff.
3. Backend `POST /api/godot/match-rooms/`, `GODOT_SERVICE_TOKEN` env var, titles/reactions endpoints.
4. Standing up the headless server (systemd) + Cloudflare Tunnel ingress for `/godot-ws`.

No commit for this task — it's a review checkpoint, not a code change.

---

## Self-review notes (completed during planning, not a task to execute)

- **Spec coverage:** every Godot-side work item in deployment-spec §3/§4 and integration §3.3 maps to a task above; frontend/backend/infra items are explicitly excluded per the scope boundary, not silently dropped.
- **No placeholders:** every step above shows exact before/after GDScript or `.tscn` text, not descriptions of changes.
- **Type/name consistency checked:** `_ws_url` (Task 2) is the single var `_on_join_pressed` (Task 2) reads; `backend_user_id` (Task 1) is exactly the field name `_do_seat` (Task 4) reads via `pl.backend_user_id`; `request_topic_match`'s second parameter is renamed `peer_ids` → `user_ids` in Task 6 and Task 4's call site already passes a `user_ids`-named array — consistent; `service_token` (Task 5) is exactly the field `request_topic_match` (Task 6) checks before proceeding; `DEFAULT_PORT`/`DEFAULT_ADDRESS` (Task 2) replace every remaining `PORT`/`ADDRESS` reference in the same task, including in `_on_host_pressed`.
