# Godot 身份層階段二：ticket 接線與 spawn 授權 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Godot 端與前端接上階段一的一次性入場券——client 連線前向宿主頁拉一張券、交給 Godot server 兌換成 user_id；server 端持有 `peer_id → user_id` 身份表並以它授權 spawn 與建房；移除可被冒充的 `backend_user_id` 同步屬性與訪客登入 fallback。

**Architecture:** 身份的真值只存在 Godot server 的 `_peer_users` dict（server-only，不同步）。client 唯一能做的是遞出一張不透明的券；券由 server 用服務金鑰向 Django 兌換。發券是「拉」式：Godot 每次連線前呼叫宿主頁掛的 `bridgeus_request_ticket()`，因為券是一次性的，iframe 載入時發一張會撐不過重連與 WASM 冷啟動（見 spec §5.1 警告）。`bridgeus_token`（使用者 JWT）保留給 client 自己打議題／頭銜 API 用——iframe 與主功能同源、同一信任域；只有 `bridgeus_user_id` 消失（見 spec §9.1）。

**Tech Stack:** Godot 4.7 GDScript（無自動化測試，驗收靠雙開手測）、React（GodotLobby.jsx）。

**依據 spec:** `docs/superpowers/specs/2026-07-28-godot-identity-and-match-binding-design.md`（§5.1 拉式發券警告、§6 身份表與 spawn、§9.1、§11 邊界表）

**前置：階段一已完成**（commits `c58a728..0b3a128`）：後端已有 `POST /api/godot/tickets/`（發券，需 JWT）與 `POST /api/godot/tickets/redeem/`（兌換，需 `X-Godot-Service-Token`，回 `{"user_id": N}`，失敗回 400 `{"detail": "入場券無效。"}`）；`/api/guest/` 已移除（Godot 現在打它會 404，本階段把呼叫端拆掉）。

---

## 執行前必讀

- **本階段沒有自動化測試。** Godot 是編輯器驅動的專案，沒有 CLI 測試框架；React 端這次只改一個檔案。每個 task 的驗證是（1）程式碼自我檢查清單、（2）計劃最後的手動驗收清單——後者由人類執行，subagent 不要嘗試啟動 Godot。
- **不要動 `backend/` 任何檔案。** 後端在階段一已完成並通過審查。
- **不要跑 pytest。** 與本階段無關，且測試 DB 是共用的。
- 目前分支 `feat/Light`，直接在上面 commit，不要切分支、不要 amend 既有 commit。
- GDScript 慣例：註解用繁體中文、帶 rationale；私有成員前置底線；`ponytail:` 標記原型捷徑。改動前先讀 `godot/CLAUDE.md` 的「Architecture」與「Conventions that bit us」兩節——尤其 RPC 一律放 player/game 節點、UI 零 RPC 的鐵律。
- **`godot/CLAUDE.md` 與程式碼有已知漂移**（port 寫 8080 實為 8085、沒提語音系統）；以程式碼為準，Task 6 會一併修文件。

## 設計決策（本計劃內生效）

**桌面開發模式的定義：`Backend.service_token == ""` 的 server 就是本機開發 host。** 正式部署只有 headless dedicated server 會從環境變數拿到 `GODOT_SERVICE_TOKEN`；桌面編輯器裡按 Host 的開發者沒有金鑰。規則：
- **有金鑰的 server（＝正式）**：spawn 一律要求票券兌換成功，否則 `disconnect_peer`。
- **沒金鑰的 server（＝本機開發 host）**：允許無身份 spawn，純本地遊玩（議題泡泡、聊天、語音都是 P2P，照常動）；但 `_peer_users` 是空的，所以坐木樁配對會被拒——建房需要真實 user_id，這正是想要的行為。
- 這不會重開冒充漏洞：正式環境的 client 只連得到 dedicated server，而它有金鑰、走嚴格路徑。

這條規則取代了「桌面也一律拒絕」——後者會讓團隊完全無法在編輯器裡雙開測試多人功能。DB 裡不會再有無主帳號（guest 已刪，本機模式根本不碰後端），滿足「不要再有不知名帳號」的原始要求。

## 檔案結構

| 檔案 | 動作 |
|---|---|
| `frontend/src/pages/GodotLobby.jsx` | 移除 `bridgeus_user_id`；掛 `bridgeus_request_ticket()` 拉式發券 |
| `godot/Globals/Backend.gd` | 刪 `guest_login()`；新增 `request_entry_ticket()`（client 拉券）與 `redeem_ticket()`（server 兌換） |
| `godot/World/game.gd` | `_peer_users` 身份表；`submit_ticket` RPC 取代 `request_spawn`；join 流程拉券；`_do_seat` 改讀身份表 |
| `godot/Entities/player/player_00.gd` | 刪 `backend_user_id` 欄位與賦值 |
| `godot/Entities/player/player_00.tscn` | 刪 synchronizer 的 `properties/4`（backend_user_id 同步項） |
| `godot/CLAUDE.md` | 更新架構描述（spawn 流程、身份層、port、guest 移除） |

---

## Task 1: GodotLobby.jsx — 拉式發券

**Files:**
- Modify: `frontend/src/pages/GodotLobby.jsx`

- [ ] **Step 1: 改寫 handleLoad**

把 `handleLoad` 整段換成：

```jsx
  const handleLoad = () => {
    const frameWindow = iframeRef.current?.contentWindow;
    if (!frameWindow) return;

    // JWT 保留給 Godot client 自己打議題/頭銜 API 用——iframe 與主功能同源、
    // 同一個瀏覽器信任域，跟 React 把 token 放 localStorage 是同一件事。
    // 不再交 user_id：對遊戲 server 的身份識別改走一次性入場券（見下），
    // client 不需要、也不該知道要自報什麼 id（自報的 id 就是可冒充的 id）。
    frameWindow.bridgeus_token = localStorage.getItem('access') || '';
    frameWindow.bridgeus_api_base = `${api.defaults.baseURL || ''}/api`;

    // 拉式發券：Godot 每次要連線前呼叫這個函式，完成後把券寫進 bridgeus_ticket
    // （Godot 端輪詢）。不能在 iframe 載入時就發——券是一次性、60 秒到期，
    // 撐不過 WASM 冷啟動，重連時更會拿著已兌換的券被踢（見 spec §5.1）。
    // 失敗寫 'ERROR' 而非留空：讓 Godot 分得出「還在等」跟「要不到」。
    frameWindow.bridgeus_request_ticket = () => {
      frameWindow.bridgeus_ticket = '';
      api.post('/api/godot/tickets/')
        .then((res) => { frameWindow.bridgeus_ticket = res.data.ticket; })
        .catch(() => { frameWindow.bridgeus_ticket = 'ERROR'; });
    };

    // 多人連線位址：同源拓樸下 /godot-ws 由 Cloudflare Tunnel 轉到 headless
    // Godot server（見部署 runbook）。用當前 origin 自動組，不寫死網域；
    // https 頁面自動用 wss。本機直接開 build（不經此頁）時不會被設，Godot 端
    // _resolve_connection_settings() 會退回 ws://127.0.0.1:8085。
    const wsProto = location.protocol === 'https:' ? 'wss' : 'ws';
    frameWindow.bridgeus_ws_url = `${wsProto}://${location.host}/godot-ws`;
  };
```

同時檢查檔頭 import：`getAccessTokenPayload` 若因此不再被使用就從 import 拿掉
（用 `grep -n "getAccessTokenPayload" frontend/src/pages/GodotLobby.jsx` 確認）。

- [ ] **Step 2: 自我檢查**

- `bridgeus_user_id` 在整個 `frontend/src/` 沒有其他寫入點：
  `grep -rn "bridgeus_user_id" frontend/src/` 應該零筆。
- `frontend/` 若有 lint script（看 `package.json` 的 `scripts`），跑
  `cd /Users/light/code/frontend && npm run lint 2>&1 | tail -5`；沒有就略過。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/GodotLobby.jsx
git commit -m "feat(m1): pull-based godot entry ticket in lobby host page"
```

---

## Task 2: Backend.gd — 拉券與兌換，刪 guest

**Files:**
- Modify: `godot/Globals/Backend.gd`

- [ ] **Step 1: 刪掉 `guest_login()`**

整段移除（含它上方的兩行區塊註解「--- 認證：訪客登入…」）。後端 `/api/guest/` 在階段一已是 404，這個函式已死。

- [ ] **Step 2: `acquire_token_from_host()` 只拿 token，不再拿 user_id**

把函式尾端讀 `window.bridgeus_user_id` 的三行（`var uid = ...` 到 `user_id = int(uid)`）刪掉，並把 `user_id` 欄位宣告（`var user_id := 0` 與其註解）一併移除。函式其餘不動。接著全域確認：`grep -rn "Backend.user_id\|user_id" godot/Globals/Backend.gd godot/World/game.gd godot/Entities/player/player_00.gd` ——`player_00.gd` 的使用點會在 Task 5 移除，Backend.gd 內不應再有殘留。

- [ ] **Step 3: 新增 client 端拉券**

加在 `acquire_token_from_host()` 之後：

```gdscript
# --- 入場券：client 端拉券（Web 專用）---------------------------------------
# 每次要連線前呼叫。向宿主頁（GodotLobby.jsx）要一張一次性入場券，輪詢等結果。
# 「拉」而不是 iframe 載入時「推」：券 60 秒到期且一次性，撐不過 WASM 冷啟動，
# 重連時更會拿著已兌換的券被踢（見 spec §5.1）。
# 回空字串代表要不到：未嵌在主功能頁（直開 export）、未登入、或後端故障。
func request_entry_ticket() -> String:
	if not OS.has_feature("web"):
		return ""
	# 直開 export（沒有宿主頁）快速失敗，不空等 5 秒。
	var has_fn = JavaScriptBridge.eval(
		"typeof window.bridgeus_request_ticket == 'function'", true)
	if not has_fn:
		return ""
	JavaScriptBridge.eval("window.bridgeus_request_ticket()", true)
	for i in 50:   # 最多等 5 秒（fetch 正常 <1 秒；'ERROR' 是宿主頁報的失敗）
		await get_tree().create_timer(0.1).timeout
		var t = JavaScriptBridge.eval("window.bridgeus_ticket || ''", true)
		if typeof(t) == TYPE_STRING and t != "":
			return "" if t == "ERROR" else t
	return ""
```

- [ ] **Step 4: 新增 server 端兌換**

加在 `request_topic_match()` 附近（同為服務金鑰路徑）：

```gdscript
# --- 入場券：server 端兌換（僅 headless dedicated server 呼叫）---------------
# 把 client 遞來的券換成後端 user_id。成功 callback(user_id > 0)；
# 任何失敗（券無效/已用過/逾期/網路錯）一律 callback(0)——呼叫端的處置只有
# 「踢掉這個 peer」一種，不需要區分原因（後端 log 有記，見階段一 spec §5.3）。
func redeem_ticket(ticket: String, callback: Callable) -> void:
	if service_token == "":
		callback.call(0)
		return
	_post_with_service_token("/godot/tickets/redeem/", {"ticket": ticket}, func(code, data):
		callback.call(int(data.get("user_id", 0)) if code == 200 else 0)
	)
```

- [ ] **Step 5: 自我檢查**

- `grep -n "guest" godot/Globals/Backend.gd` 零筆。
- `grep -rn "guest_login" godot/` 零筆（game.gd 的呼叫端 Task 3 才拆，此時仍會有一筆在 game.gd——確認只剩那一筆）。

- [ ] **Step 6: Commit**

```bash
git add godot/Globals/Backend.gd
git commit -m "feat(m1): ticket pull and redeem seams in godot backend singleton"
```

---

## Task 3: game.gd — 身份表、submit_ticket 取代 request_spawn、join 流程

**Files:**
- Modify: `godot/World/game.gd`

- [ ] **Step 1: 加身份表與券暫存**

在 `var _occupancy := {}` 附近加：

```gdscript
var _peer_users := {}   # peer_id:int -> 後端 user_id:int（僅 server 使用，不同步——
                        # 身份的真值只能放 server；任何 client 可寫的同步屬性都可冒充）
var _ticket := ""       # client 端：join 前向宿主頁拉到的入場券，連上後遞給 server
```

- [ ] **Step 2: 拆掉 guest fallback**

`_ready()` 裡的身份交接段改成：

```gdscript
	# 身份交接：主功能登入的 JWT（window.bridgeus_token）只給 client 自己打
	# 議題/頭銜 API 用。對遊戲 server 的身份識別走一次性入場券（見 _on_join_pressed）。
	# 拿不到 token（桌面開發、直開 export）就沒有後端持久化功能，純本地遊玩——
	# 訪客登入已移除，不會再產生無主帳號。
	if Backend.acquire_token_from_host():
		_fetch_banner_options()   # 真登入才有頭銜；本地模式維持假頭銜
```

（原本的 `else: Backend.guest_login(...)` 整段刪除。）

- [ ] **Step 3: join 流程先拉券**

`_on_join_pressed()` 改成：

```gdscript
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
		return

	multiplayer.multiplayer_peer = peer
	print("正在嘗試連線到：", _ws_url)
	hide_buttons()

	# 等真正握手完成（connected_to_server 訊號）再遞券申請生身體，取代固定 0.2s
	# 猜測值——真實網路延遲（尤其 Cloudflare Tunnel 代理）下 200ms 不一定夠。
	multiplayer.connected_to_server.connect(_on_connected_to_server, CONNECT_ONE_SHOT)
```

`_on_connected_to_server()` 改成：

```gdscript
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
```

- [ ] **Step 4: `submit_ticket` 取代 `request_spawn`**

刪掉整個 `request_spawn` RPC（原 `game.gd:157-161` 附近，含註解），換成：

```gdscript
# 3. 遞券申請生成（只有 server 會處理）。取代舊的 request_spawn(id)——那個版本
#    信任 client 自報的 id，可以冒名或洗版；現在 id 一律取 get_remote_sender_id()，
#    身份一律由券兌換而來，client 沒有任何可自報的欄位。
@rpc("any_peer", "call_local", "reliable")
func submit_ticket(ticket: String) -> void:
	if not multiplayer.is_server():
		return
	var id = multiplayer.get_remote_sender_id()
	if id == 0:
		id = multiplayer.get_unique_id()   # call_local：host 自己
	if get_node_or_null(str(id)) != null:
		return   # 已有身體，防重複（重送 RPC 不會生第二個）
	# 本機開發 host（沒有服務金鑰）：無身份 spawn，純本地遊玩。
	# 建房需要真實 user_id，_peer_users 沒有這個 peer → 坐木樁會被拒，正確。
	if Backend.service_token == "":
		_spawn_player(id)
		return
	# 正式（dedicated server）：券兌換成功才有身體，失敗就踢。
	Backend.redeem_ticket(ticket, func(user_id):
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
```

注意 `_on_host_pressed()` 裡 host 給自己生身體的 `_spawn_player(1)` 不動——host 是桌面開發模式，走的本來就是無身份路徑。

- [ ] **Step 5: 斷線清身份**

`_on_peer_disconnected()` 開頭（`_do_unseat(id)` 之前）加：

```gdscript
		_peer_users.erase(id)   # 身份表跟著 peer 走；殘留會讓下一個拿到同 id 的人冒名
```

- [ ] **Step 6: `_do_seat` 改讀身份表**

把收集 `user_ids` 的段落（原本 `for pid in occupants: var pl = get_node_or_null(str(pid)) ... user_ids.append(pl.backend_user_id)`）換成：

```gdscript
			# 後端只在 server 端呼叫一次（兩位都打會建兩間房）。user_id 一律取自
			# server 端身份表 _peer_users——不再讀 player 節點上的同步屬性，那個
			# 欄位已移除（client 可寫的同步屬性＝可冒充的身份，見 spec §D2）。
			var user_ids := []
			for pid in occupants:
				user_ids.append(_peer_users.get(pid, 0))
			# 任一方沒有已驗證身份（本機開發模式、或不該發生的漏網）就不建房，
			# 明確拒絕並釋放座位——比送出 [0, 0] 讓後端 400 之後無聲無息好。
			if user_ids.has(0):
				for pid in occupants:
					if pid == multiplayer.get_unique_id():
						seat_denied("配對需要正式登入身份，請從主功能頁面進入")
					else:
						seat_denied.rpc_id(pid, "配對需要正式登入身份，請從主功能頁面進入")
					_do_unseat(pid)
				return
```

（緊接原本的 `Backend.request_topic_match(topic, user_ids)` 與 `match_found` 迴圈，不動。）

- [ ] **Step 7: 自我檢查**

- `grep -n "request_spawn" godot/` 零筆（scene 檔沒有連這個 RPC，訊號都是程式碼裡連的）。
- `grep -n "guest_login" godot/` 零筆。
- `grep -n "backend_user_id" godot/World/game.gd` 零筆。
- 讀一遍 `submit_ticket`：確認每條路徑（本機/兌換成功/兌換失敗/peer 已斷）都有終點。

- [ ] **Step 8: Commit**

```bash
git add godot/World/game.gd
git commit -m "feat(m1): server-side identity table and ticket-gated spawn"
```

---

## Task 4: player_00 — 移除可冒充的同步屬性

**Files:**
- Modify: `godot/Entities/player/player_00.gd`
- Modify: `godot/Entities/player/player_00.tscn`

- [ ] **Step 1: 刪 `.gd` 的欄位**

- 刪掉 `var backend_user_id: int = 0` 與它上方整段註解（原 23-27 行附近）。
- `_ready()` 裡的 `backend_user_id = Backend.user_id` 賦值**已在 Task 3 的修正輪先行刪除**
  （Task 2 移除 `Backend.user_id` 後那行會在每次 spawn 丟 `Invalid get index`，
  分支不能停在壞掉的狀態）。確認它確實不在了再繼續。
- 順手清掉第 26 行附近那句提到 `guest_login 測試帳號` 的過時註解——該函式已不存在。

- [ ] **Step 2: 刪 `.tscn` 的同步項**

`player_00.tscn` 的 `SceneReplicationConfig` 裡刪掉這三行：

```
properties/4/path = NodePath(".:backend_user_id")
properties/4/spawn = true
properties/4/replication_mode = 1
```

**不要**重編其餘 properties 的編號——Godot 的 SceneReplicationConfig 用連續索引，刪掉中間一項要把後面的往前補。確認 `properties/4` 是最後一項（目前是：0=position、1=flip_h、2=appearance、3=moving、4=backend_user_id），是最後一項就直接刪、不用重編。用 `grep -n "properties/" godot/Entities/player/player_00.tscn` 驗證刪完後只剩 0-3 且連續。

- [ ] **Step 3: 自我檢查**

`grep -rn "backend_user_id" godot/` 零筆。

- [ ] **Step 4: Commit**

```bash
git add godot/Entities/player/player_00.gd godot/Entities/player/player_00.tscn
git commit -m "feat(m1): drop client-declared backend_user_id from player sync"
```

---

## Task 5: godot/CLAUDE.md — 文件同步

**Files:**
- Modify: `godot/CLAUDE.md`
- Modify: `godot/Globals/Backend.gd`（只改一行註解——見 Step 2）

- [ ] **Step 1: 更新內容**

按實際程式碼修正（讀檔對照，不要照抄這裡的摘要）：

1. **Spawn 流程**（「Architecture」節）：`request_spawn` 已由 `submit_ticket` 取代；描述券的來源（宿主頁拉式發券）、server 端 `_peer_users` 身份表、本機開發模式（無服務金鑰＝無身份 spawn）與正式模式（兌換失敗即踢）的分野。
2. **Backend seam** 節：`guest_login` 已移除；`acquire_token_from_host()` 只拿 token；新增 `request_entry_ticket()` / `redeem_ticket()`；`user_id` 欄位已不存在。
3. **port 修正**：兩處「port 8080」改 8085（程式碼註解早就說明 8080 被 React 佔用）。
4. **「目前沒有 export_presets.cfg」** 一句刪掉（檔案已存在，Web preset）。
5. **語音通話**：補一段簡述（`Globals/VoiceChat.gd` autoload、pitch shift、頻譜柱、`invite_to_voice` handshake 鏈）——它是第二大子系統卻完全沒寫。
6. **本機開發模式的坐樁行為**：明寫「桌面 host（無服務金鑰）兩人坐同議題木樁時，兩位都會被
   退座並看到『配對需要正式登入身份』」。這是設計行為不是 bug，但看起來很像 bug，不寫下來
   一定會被回報。

- [ ] **Step 2: 清掉最後一處懸空參照**

`godot/Globals/Backend.gd:206` 的註解仍寫「（見 player_00.gd 的 backend_user_id 與
game.gd 的 _do_seat）」，但那個欄位已在 Task 4 移除。改成指向現在的真值來源：
「（user_id 來自 game.gd 的 _peer_users 身份表，由入場券兌換而來）」。
**只改註解，不要動 `request_topic_match` 的程式碼。**

改完確認 `grep -rn "backend_user_id" godot/` 為零筆。

- [ ] **Step 3: Commit**

```bash
git add godot/CLAUDE.md godot/Globals/Backend.gd
git commit -m "docs: sync godot CLAUDE.md with ticket identity flow"
```

---

## 手動驗收清單（人類執行；subagent 到此為止）

前置：Django 跑在 8005、React dev server 跑起來、Godot 匯出 Web build 或用編輯器。

| # | 情境 | 預期 |
|---|---|---|
| 1 | 桌面編輯器雙開，一 Host 一 Join | 都能生身體、互見、議題泡泡正常（本機無身份模式） |
| 2 | 桌面模式兩人坐同議題木樁 | 被拒：「配對需要正式登入身份…」，座位釋放 |
| 3 | 瀏覽器登入主功能 → `/chat` → Join | 拉券成功、連上、生身體；server log 有兌換成功 |
| 4 | 未登入直開 `/godot/takeAbridge_godot.html` → Join | 快速失敗：「無法取得入場券…」，沒有連線 |
| 5 | 兩個瀏覽器不同帳號 → 坐同議題木樁 | 後端建房（201）、雙方收到 match_found |
| 6 | 連線後手動斷網再重整重進 | 重新拉到新券、重新連上（不會拿舊券被踢） |
| 7 | headless server 未設 `GODOT_SERVICE_TOKEN` 就有人從 Web 連 | client 被踢（`redeem_ticket` 直接回 0）——部署設定錯誤要顯性失敗 |
| 8 | **同一個分頁內重連兩次**：Join → 殺掉 headless server → 重開 → 再 Join（不重整 iframe） | 拿到**新**券並連上。這是拉式發券存在的理由，必測 |
| 9 | **快速連按 Join 兩次**（讓 `create_client` 失敗使按鈕復原，再連點） | 不會有 peer 因重複兌換同一張券被踢；重入守衛應回 `busy` |
| 10 | 主功能已登出／localStorage 沒有 access 就開 `/chat` | 5 秒內看到「登入狀態已失效，請回主功能頁面重新登入」，不是空等或籠統訊息 |
| 11 | 關掉 Django 再 Join | 看到逾時／失敗訊息且可區分於未登入 |
| 12 | 直接開 `takeAbridge_godot.html`（不經 iframe）按 Join | **立即**失敗（`has_fn` 快速失敗），不是等滿輪詢上限 |
| 13 | devtools 限速 Slow 3G 後 Join | 仍能拿到券；若失敗代表輪詢上限太短，調高而不是硬上 |
| 14 | 改過的 client 連上但**從不送 `submit_ticket`**，直接送 `request_seat` | 被靜默忽略（沒有身體）；不會佔住木樁、更不會把已就座的無辜玩家一起退座 |
| 15 | server 沒開時按 Join → 啟動 server → **再按一次 Join** | 第二次要能連上。驗 `_on_connection_failed` 換了新 peer、解除了 one-shot 訊號；若出現 `ERR_ALREADY_IN_USE` 或 "signal already connected" 就是沒修乾淨 |
| 16 | 同一人開兩個分頁、各自坐同議題的兩根木樁 | 後端會 400（同一 user_id）。階段二尚未接建房 callback，所以目前兩人角色仍會被刪——**這是已知待辦，階段三修**（見 spec §7.1.1），不是本階段的迴歸 |

> ⚠️ 情境 1、2（編輯器雙開）**不會驗到 `await` 相關的行為**——`request_entry_ticket()`
> 在桌面走的是提前 return 的路徑。Web 端的情境 3、8-13 才是真正驗證券機制的地方，
> 不要因為編輯器裡都正常就跳過。

驗收 3、5 需要 headless dedicated server 本機跑法：
```bash
godot --headless --path godot --server
```
（需設 `GODOT_SERVICE_TOKEN` 環境變數，值同 Django `.env` 的 `GODOT_SERVICE_TOKEN`。）

## 已知的殘餘（不在本階段）

- 配對成功後的網頁跳轉（`match_found` 帶 room_id → postMessage → React navigate）是 spec §C2／階段三。
- `receive_chat` / `receive_voice` / `receive_invite` 的發話者驗證、語音頻寬——code review 待辦，另案。
- 券/房間清理指令——spec §14 階段 5。
