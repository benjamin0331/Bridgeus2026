# Godot 配對綁定階段三：建房 callback 與網頁跳轉 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 木樁配對成功後，兩位玩家的瀏覽器自動跳轉到 `/topic/<id>?mode=match` 的配對聊天室；建房失敗（後端不可達、同一人雙分頁）時明確拒絕並讓玩家可重試，不再無聲刪除角色。

**Architecture:** `_do_seat` 的建房呼叫改為帶 callback——`match_found` 與 `_finish_match` 目前跟 HTTP **平行**跑，成功失敗都會刪角色，要搬進 callback 裡「成功才通知、失敗退座」。`match_found` 帶上後端回的 `topic_id` / `room_id`，Web 端用 `JavaScriptBridge` 對父視窗 `postMessage`，`GodotLobby.jsx` 驗證來源後 `navigate`。後端 `GodotMatchRoomView` 的回應補一個 `topic_id` 欄位（它本來就驗證過這個值，回傳出來省得 Godot 端維護第二份 slug→id 對照）。

**Tech Stack:** Django + DRF + pytest（後端小改）、GDScript（Godot 4.7）、React。

**依據 spec:** `docs/superpowers/specs/2026-07-28-godot-identity-and-match-binding-design.md` §7.1（時序）、§7.1.1（同一人雙分頁）、§9.1（message 監聽）、§14 階段 3

**前置：階段一、二已完成**（`c58a728..74c9b3b`）。`game.gd` 已有 `_peer_users` 身份表與 `_do_seat` 的入口資格檢查；`GodotLobby.jsx` 已有拉式發券。

---

## 執行前必讀

- 後端指令在 `backend/` 用 `uv run` 執行，**必須帶絕對路徑 cd**（否則 `Failed to spawn: pytest` 靜默失敗）。
- **後端測試共用同一個 Postgres test DB，一次只跑一個 pytest。**
- **`pytest api` 會收集到 0 個測試（exit 5）**——測試檔叫 `tests_*.py`。一律指定檔名。
- Godot 端無自動化測試；**不要嘗試啟動 Godot**。驗收靠計劃尾端的手動清單。
- 目前分支 `feat/Light`，直接 commit，不要 amend/rebase 既有 commit。
- GDScript 慣例見 `godot/CLAUDE.md`；註解繁中帶 rationale。

## 檔案結構

| 檔案 | 動作 |
|---|---|
| `backend/api/views.py` | `GodotMatchRoomView` 兩個回應（200 與 201）補 `topic_id` 欄位 |
| `backend/api/tests_godot_match_rooms.py` | 補斷言 |
| `godot/World/game.gd` | `_do_seat` 建房改 callback、同 user 擋法、`_seat_deny_and_unseat` 共用 helper、`match_found(topic_id, room_id)` + postMessage |
| `frontend/src/pages/GodotLobby.jsx` | `message` 事件監聽 → navigate |

---

## Task 1: 後端回應補 `topic_id`

**Files:**
- Modify: `backend/api/views.py`（`GodotMatchRoomView.post` 的兩個 `Response`）
- Test: `backend/api/tests_godot_match_rooms.py`

**為什麼**：Godot 端要在 postMessage 裡帶數字 `topic_id`（前端路由是 `/topic/<數字>`），但 `_do_seat` 手上只有 slug（`"nuclear_energy"`）。後端本來就用 `topic_id` 驗證過請求，回傳出來就不用在 Godot 端維護第二份 slug→id 對照（`Backend.gd` 的 `_TOPIC_ID_MAP` 只該負責送出，不該再被讀回來用）。

- [ ] **Step 1: 寫失敗的測試**

讀 `backend/api/tests_godot_match_rooms.py`，找到 201 快樂路徑測試（約 31 行）與冪等 200 測試（約 230 行），各補一行斷言：

```python
    assert response.data["topic_id"] == 102          # 201 路徑（topic 102 的那個測試）
```

```python
    assert second.data["topic_id"] == 102            # 200 冪等路徑
```

（依實際測試用的 topic_id 調整——103 的測試就斷言 103。兩條路徑都要蓋到。）

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd /Users/light/code/backend && uv run pytest api/tests_godot_match_rooms.py -q`
Expected: 新斷言 KeyError/AssertionError 失敗，其餘照舊通過。

- [ ] **Step 3: 實作**

`backend/api/views.py` 的 `GodotMatchRoomView.post`，兩個回傳點各加一個鍵：

```python
        if existing:
            return Response(
                {
                    "room_id": existing.room_id,
                    "topic_id": topic_id,
                    "redirect_url": f"/topic/{topic_id}?mode=match",
                },
                status=status.HTTP_200_OK,
            )
```

```python
        return Response(
            {
                "room_id": match.room_id,
                "topic_id": topic_id,
                # 前端沒有獨立的 /dialogue/room/<id> 路由——配對聊天室其實是
                # TopicChat.jsx 掛在 /topic/<topic_id>?mode=match，內部再用
                # GET /api/matching/status/?topic_id= 找到這筆 DialogueMatch。
                "redirect_url": f"/topic/{topic_id}?mode=match",
            },
            status=status.HTTP_201_CREATED,
        )
```

（既有註解保留在 201 那邊即可。）

- [ ] **Step 4: 跑測試確認通過**

Run: `cd /Users/light/code/backend && uv run pytest api/tests_godot_match_rooms.py -q`
Expected: 全數 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/api/views.py backend/api/tests_godot_match_rooms.py
git commit -m "feat(m3): return topic_id in godot match-room responses"
```

---

## Task 2: game.gd — 建房 callback、同 user 擋法、match_found 帶參數

**Files:**
- Modify: `godot/World/game.gd`

- [ ] **Step 1: 新增共用的拒絕 helper**

加在 `_do_unseat` 附近：

```gdscript
# server-only：拒絕並退座一位玩家（訊息 + 座位釋放）。配對的各種失敗路徑共用，
# 免得「rpc_id 對自己不會本地執行」的分支寫三遍。
func _seat_deny_and_unseat(pid: int, msg: String) -> void:
	if pid == multiplayer.get_unique_id():
		seat_denied(msg)
	else:
		seat_denied.rpc_id(pid, msg)
	_do_unseat(pid)
```

並把 `_do_seat` 裡既有的 `user_ids.has(0)` 區塊的迴圈內容換成 `_seat_deny_and_unseat(pid, "配對需要正式登入身份，請從主功能頁面進入")`（原本三行 if/else + `_do_unseat`），註解不動。

- [ ] **Step 2: `_do_seat` 的建房段整段改寫**

把目前的（`user_ids.has(0)` 區塊**之後**）：

```gdscript
		Backend.request_topic_match(topic, user_ids)
		for pid in occupants:
			# rpc_id 對自己(host)不會本地執行 → 目標是自己時直接呼叫。
			if pid == multiplayer.get_unique_id():
				match_found()
			else:
				match_found.rpc_id(pid)
		# 配對成功、交給後端導去網頁聊天室後，把這兩位的人物清掉、還原木樁。
		_finish_match(topic, occupants)
```

換成：

```gdscript
		# 同一位使用者開兩個分頁會兌換成兩個 peer、同一個 user_id。後端會 400
		# （user_ids 相同的檢查），但在這裡先擋，訊息才說得清楚（見 spec §7.1.1）。
		if user_ids[0] == user_ids[1]:
			for pid in occupants:
				_seat_deny_and_unseat(pid, "不能與自己配對，請關閉多餘的分頁")
			return
		# 建房是非同步 HTTP：成功才通知配對成立、清人還原木樁；失敗把兩位放回
		# 可重試的狀態。原本「發完就不管」在後端失敗時照樣刪角色，玩家停在沒有
		# 身體也沒有按鈕的空世界（code review 問題 3）。
		# occupants 要複製一份給閉包：HTTP 回來之前 _occupancy 可能已被斷線改動。
		var occupants_for_cb := occupants.duplicate()
		Backend.request_topic_match(topic, user_ids, func(code, data):
			if code != 200 and code != 201:
				for pid in occupants_for_cb:
					_seat_deny_and_unseat(pid, "配對建立失敗，請稍後再試")
				return
			var room_id: String = str(data.get("room_id", ""))
			var topic_id: int = int(data.get("topic_id", 0))
			for pid in occupants_for_cb:
				# rpc_id 對自己(host)不會本地執行 → 目標是自己時直接呼叫。
				if pid == multiplayer.get_unique_id():
					match_found(topic_id, room_id)
				else:
					match_found.rpc_id(pid, topic_id, room_id)
			# 配對成功、交給後端導去網頁聊天室後，把這兩位的人物清掉、還原木樁。
			_finish_match(topic, occupants_for_cb)
		)
```

- [ ] **Step 3: `match_found` 改簽名並加 postMessage**

```gdscript
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
```

- [ ] **Step 4: 自我檢查**

- `grep -n "match_found" godot/World/game.gd`：只剩 callback 內的兩個呼叫點與函式定義，且呼叫端都帶兩個參數。
- 讀一遍新的 callback：確認失敗路徑、同 user 路徑、成功路徑各自有終點；`occupants_for_cb` 是複製不是參照原陣列的別名（`duplicate()`）。
- 確認 `user_ids.has(0)` 區塊仍在 `user_ids[0] == user_ids[1]` 檢查**之前**（沒身份要先於同人檢查——兩邊都是 0 時訊息該是「需要登入」不是「不能與自己配對」）。

- [ ] **Step 5: Commit**

```bash
git add godot/World/game.gd
git commit -m "feat(m3): gate match completion on room creation and broadcast redirect"
```

---

## Task 3: GodotLobby.jsx — message 監聽與跳轉

**Files:**
- Modify: `frontend/src/pages/GodotLobby.jsx`

- [ ] **Step 1: 加監聽**

檔頭 import 補 `useEffect`（來自 react）與 `useNavigate`（來自 react-router-dom），元件內加：

```jsx
  const navigate = useNavigate();

  // Godot 端配對成功時會 postMessage 通知跳轉（見 game.gd match_found）。
  // 兩個來源檢查都必要：只驗 origin 擋不掉同源的其他 frame，只驗 source
  // 擋不掉惡意站台開的視窗。topic_id 再驗一次型別，路由參數不能信任外部輸入。
  useEffect(() => {
    const handleMessage = (event) => {
      if (event.origin !== window.location.origin) return;
      if (event.source !== iframeRef.current?.contentWindow) return;
      const data = event.data;
      if (data?.type !== 'bridgeus_match') return;
      const topicId = Number(data.topic_id);
      if (!Number.isInteger(topicId) || topicId <= 0) return;
      navigate(`/topic/${topicId}?mode=match&from=godot`);
    };
    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [navigate]);
```

- [ ] **Step 2: 自我檢查**

- `frontend/` 有 lint 就跑：`cd /Users/light/code/frontend && npm run lint 2>&1 | tail -5`。
- 確認 `useEffect` 的清理函式有移除監聽（離開 `/chat` 後不能殘留）。
- `room_id` 刻意**不**進網址：TopicChat 是用 `GET /api/matching/status/?topic_id=` 自己找房的，網址帶 room_id 只會製造一個可以亂填的參數。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/GodotLobby.jsx
git commit -m "feat(m3): navigate to match room on godot match message"
```

---

## Task 4: spec 與 godot/CLAUDE.md 同步

**Files:**
- Modify: `docs/superpowers/specs/2026-07-28-godot-identity-and-match-binding-design.md`
- Modify: `godot/CLAUDE.md`

- [ ] **Step 1: spec 更新**

- §3 現況表的「前端跳轉｜**無**」與「建房呼叫｜無 callback」兩列改為已完成敘述。
- §7.1.1 補一句「已於階段三在 `_do_seat` 實作（`user_ids[0] == user_ids[1]` → 雙方退座）」。
- §8（如有描述 match-rooms 回應的地方）補 `topic_id` 欄位。

- [ ] **Step 2: godot/CLAUDE.md 更新**

「Issue 配對」相關段落（描述木樁配對的那段）補上：建房成功才 `match_found(topic_id, room_id)`、Web 端 postMessage 給宿主頁跳轉、失敗與同 user 雙分頁都走 `_seat_deny_and_unseat` 退座可重試。**照程式碼寫，不要照本計畫的摘要寫。**

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-07-28-godot-identity-and-match-binding-design.md godot/CLAUDE.md
git commit -m "docs: sync match redirect flow into spec and godot CLAUDE.md"
```

---

## 手動驗收清單（人類執行）

前置：Django（8005）、React dev server、headless Godot server（帶 `GODOT_SERVICE_TOKEN`）都在跑，兩個瀏覽器各登入不同帳號進 `/chat`。

| # | 情境 | 預期 |
|---|---|---|
| 1 | 兩人坐同議題木樁 | 後端 201；**兩邊瀏覽器都**跳到 `/topic/102?mode=match&from=godot`；TopicChat 能載入配對房 |
| 2 | 跳轉後按上一頁回 `/chat` 再重進 | 重新拉券、重新生角色（前一階段行為不退化） |
| 3 | 關掉 Django 後兩人坐木樁 | 兩位都看到「配對建立失敗，請稍後再試」、退座，Django 恢復後可重坐成功 |
| 4 | 同一帳號開兩個分頁、各坐一根木樁 | 兩個分頁都看到「不能與自己配對，請關閉多餘的分頁」、退座；後端 **沒有**建出房 |
| 5 | 在 devtools console 對頁面 `window.postMessage({type:'bridgeus_match',topic_id:102}, location.origin)` | **不跳轉**（source 不是 iframe） |
| 6 | 配對成功的瞬間其中一人斷網 | 另一人正常跳轉進房；斷網者錯過通知——房間靠既有的 idle timeout 收掉，這是已知可接受的邊界 |
| 7 | 重複配對：同兩人跳轉後回大廳再坐一次 | 後端回 200（冪等沿用同房），兩人跳進**同一間**房 |

### ⚠️ 在途競態（必測，且需要人為放慢才重現得了）

以下情境全部發生在「兩人坐滿 → 建房 HTTP 回來」之間的數百毫秒內。**要重現必須先人為拉長這段時間**——在 `Backend.gd` 的 `_post_with_service_token` 送出前插一個 `await get_tree().create_timer(3.0).timeout`，或用 devtools 限速。測完記得移除。

| # | 情境 | 預期 |
|---|---|---|
| 8 | 兩人坐滿後，A 立刻按等待面板的**取消** | 兩人都**不**跳轉、身體都還在；B 收到「對方已取消配對，請重新選擇」並退座。（修正前：兩人都被導進房、身體都被刪） |
| 9 | 兩人坐滿後，立刻關掉 A 的分頁 | 同上，B 被釋放且可重坐。順便看 headless server log 有沒有 `rpc_id` unknown peer 的錯誤 |
| 10 | 三人在場：A、B 坐滿 → A 取消 → C 立刻坐上空出來的樁 | C 應該被擋下（「這個議題正在配對中，請稍候再試」）。最終不應出現「人坐著但樁顯示空的」，也不應開出兩間房 |
| 11 | 後端回 2xx 但 body 缺 `room_id`（可暫時改 view 回 `{"topic_id": 102}` 測） | **不跳轉**，且兩人身體都還在、可重坐——不能因為 HTTP 是 200 就往下刪人 |

## 已知的殘餘（不在本階段）

- 跳轉後的限時前測問卷（spec §14 階段 4）——目前進房後 `user_a_score`/`user_b_score` 仍是 4.00 佔位值。
- 問卷收尾狀態機與 `close_expired_godot_matches`（階段 5）。
- 階段 4 動工前先與指導老師確認 spec §15 第 1 點（Godot 配對不套用中立→AI 分流）。
