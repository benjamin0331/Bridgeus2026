# Godot 身份綁定與木樁配對接軌 Design

> 日期：2026-07-28
> 狀態：設計定案，待實作計畫
> 範圍：`godot/`（Globals/Backend.gd、World/game.gd、Entities/player/player_00.gd、player_00.tscn）、
> `backend/api/`（views.py、urls.py、models.py、permissions.py、management/commands）、
> `backend/apps/matching/services/matcher.py`、`frontend/src/pages/{GodotLobby,TopicChat}.jsx`、
> `frontend/src/components/SurveyModal.jsx`
> 相關：`docs/superpowers/specs/2026-07-26-backend-identity-code-review.md`（本案取代其中「guest 隔離」的建議，改為直接移除）、
> `docs/superpowers/specs/2026-07-27-supervisor-display-settings-and-mixed-entry-design.md`（混合入口分流規則，§4 D3 有例外）、
> `docs/superpowers/plans/2026-07-19-godot-deployment-readiness.md`（部署拓樸）

---

## 1. 背景與目標

Godot 大廳（`/chat` 內嵌的 2D 世界）目前可以讓兩位玩家坐上同一議題的木樁完成「遊戲內配對」，
後端也已經有 `POST /api/godot/match-rooms/` 會建出 `DialogueMatch`。但這條路徑跟主功能的實驗
流程是斷開的，2026-07-28 的 code review 找出三個彼此相關的問題：

1. **身份不可信**：`player_00.gd` 的 `backend_user_id` 由玩家自己的瀏覽器透過
   MultiplayerSynchronizer 宣告（`player_00.tscn` `properties/4`），Godot server 原封不動送進
   `/api/godot/match-rooms/`，後端完全信任（`views.py:3464`）。改過的 Web client 可以自稱任何
   user_id，讓後端替兩個不相干的真實使用者開一間 ACTIVE 房間。服務金鑰擋的是「誰能呼叫端點」，
   沒擋「這個 peer 是不是那個 user」。
2. **不知名帳號**：`Backend.gd::guest_login()` 會打 `/api/guest/` 開一個 `guest_xxxxxxxx` 帳號，
   `Backend.user_id` 維持 0。實驗資料裡因此可能出現無主帳號，而 `user_ids=[0, 0]` 也讓配對必定失敗。
3. **配對走不完**：`game.gd::_do_seat` 呼叫 `Backend.request_topic_match()` **不帶 callback**，
   後端回的 `room_id` / `redirect_url` 直接丟掉；`match_found()` 只跳一個 toast，1.5 秒後
   `_finish_match()` 把兩位的角色 `queue_free()`。不論後端成功或失敗，玩家都停在一個沒有身體、
   沒有按鈕的空世界。而且建房時 `user_a_score` / `user_b_score` 是寫死的 `Decimal("4.00")`
   佔位值（`views.py:3504`），沒有真實的前測立場分數（s_pre）。

目標：

1. Godot 世界裡的每個角色都對應一個**已在主功能登入的真實帳號**，且該對應由 server 端驗證，
   不接受 client 自報。移除訪客登入。
2. 木樁配對成功後，兩位玩家的瀏覽器**自動跳轉**到配對聊天室，並且是**強制綁定**這兩人的房間
   （不重新排隊、不會配到別人）。
3. 跳轉後兩人各自填**前測問卷**，答案回寫成真實的 s_pre，讓 Godot 房的研究資料跟一般配對房同構。
4. 問卷階段有明確的時限與退出處理，不會產生卡死的半殘房間。

## 2. 範圍

**納入**

- 一次性 ticket 身份交接機制（取代 `window.bridgeus_token` 直傳 JWT 與 guest fallback）。
- Godot server 端的 `peer_id → user_id` 對照表，以及依此重寫的 spawn 授權。
- `request_spawn` 的來源驗證與防重複。
- 木樁配對 → 建房 → 前端跳轉的完整鏈路（含失敗回頭）。
- Godot 房專屬的「限時前測問卷」狀態機（5 分鐘、倒數、對方退出偵測、退回一般模式）。
- 前測問卷答案回寫 `DialogueMatch.user_a_score` / `user_b_score` 與 `UserStanceProfile`；
  兩個分數欄位改 nullable（migration），廢除 4.00 佔位值。
- 逾期 Godot 房的清理 management command。
- 移除 `GuestLoginView` 與 `/api/guest/`。

**不納入**

- code review 其餘項目：語音頻寬（每幀原始 PCM ≈ 2.8 Mbps）、`receive_voice`/`receive_chat`/
  `receive_invite` 的發話者驗證、`game_ui.gd` 硬編碼版面、
  `player_00.gd` 拆檔、`_TOPIC_ID_MAP` 與 `_TOPIC_TRUNKS` 兩份議題真值來源。這些另案處理。
- Godot 端自動化測試框架（GUT）導入。

> `HTTPRequest` timeout 原本列在這裡，但階段三實作時發現它不再是純整潔問題：加了
> `_matching_topics` 在途旗標之後，卡住的連線會讓那個議題**永久無法配對**（回呼永遠不來、
> 旗標永遠不解除）。已於 `61653d8` 在 `Backend.gd` 兩處 `HTTPRequest` 建立點補上 10 秒 timeout。
- 配對房本身的對話功能（已存在，不動）。
- `godot/CLAUDE.md` 的文件漂移修正（port 8080/8085、export_presets、語音子系統缺漏）——
  實作完成後順手更新，不列為設計項目。

## 3. 現況

| 項目 | 現況 | 位置 |
|---|---|---|
| Godot 身份來源 | `window.bridgeus_token`（主功能 JWT 直接進 iframe）或 guest fallback | `Backend.gd:30`, `:46` |
| user_id 傳遞 | client 自己設 `backend_user_id`，MultiplayerSynchronizer 同步 | `player_00.gd:87`, `player_00.tscn` `properties/4` |
| 建房呼叫 | 階段三已補上非同步 callback：`_try_start_match` 建房前標記 `_matching_topics` 在途、擋新插隊；`_on_match_room_created` 收 HTTP 回應，成功才轉發 `match_found`，失敗或座位驗證不過就 `_seat_deny_and_unseat` 讓玩家可重坐 | `game.gd:_try_start_match`, `:_on_match_room_created` |
| 建房端點 | 服務金鑰驗證、冪等、回 `room_id` + `redirect_url` + `topic_id`（階段三新增，Godot 端組前端路由要用數字 id） | `views.py:3428`, `:3519-3521`, `:3543-3548` |
| 建房分數 | 寫死 `Decimal("4.00")`，`matching_algorithm_version="godot_manual"` | `views.py:3504` |
| 分數欄位限制 | `user_a_score` / `user_b_score` **NOT NULL**，CHECK 1..7 | `models.py:136`, `:162` |
| 分數唯一讀取端 | `_post_dialogue_stance_snapshot`（後測問卷取 s_pre）。立場偏移分析用 `UserStanceProfile.q9_embedding`，不碰這兩欄 | `views.py:3103`, `hh_analysis.py:42` |
| 配對狀態查詢 | `get_matching_state()` 直接從 `DialogueMatch` 找，`queue_entry` 可為 None | `matcher.py:626` |
| 房間 presence | 存在 `match.stats` 的 JSON，預設 `connected=False, disconnected_at=None` | `matcher.py:148` |
| 缺席關房 | 180 秒；但 `disconnected_at` 為 None 時 `_absence_deadline_for_participant` 回 None | `matcher.py:31`, `:188` |
| 前端跳轉 | 階段三已實作。`match_found(topic_id, room_id)` 在 Web 端經 `JavaScriptBridge` 對 `window.parent` `postMessage`，`GodotLobby.jsx` 驗證 `origin` 與 `source` 後 `navigate('/topic/<topic_id>?mode=match&from=godot')` | `game.gd:match_found`, `GodotLobby.jsx` |
| 前端問卷 | `setShowSurvey(status === 'idle')`——Godot 建完房後 status 已是 `matched`，問卷不會出現 | `TopicChat.jsx:858` |
| 問卷期間輪詢 | `showSurvey` 時 `roomId` 傳 null，等於不 poll | `TopicChat.jsx:705` |
| 排程機制 | 無 celery / cron；既有做法是 management command 交給 ops 排 | `api/management/commands/close_stale_dialogue_sessions.py` |

**關鍵觀察 A**：現有的 180 秒缺席關房機制在問卷階段**完全不會作用**。它只在「WS 連上過又斷掉」
之後才起算（`disconnected_at` 有值才有 deadline）。所以既不會誤殺正在填問卷的房間，也**偵測不到
問卷階段關掉網頁的人**——後者要自己做。

**關鍵觀察 B**：`get_matching_state()` 對 `queue_entry is None` 是容忍的，所以 Godot 房在沒有
`MatchQueueEntry` 的情況下也查得到。反過來說，**「有沒有 MATCHED 的 MatchQueueEntry」剛好可以
當作「這個人填過前測問卷沒有」的憑證**，不需要為此新增欄位。

## 4. 設計決策

### D1：身份用一次性 ticket，不把 JWT 送進 Godot

**決定**：前端向後端換一張 60 秒、一次性的 ticket，塞進 iframe；Godot client 把 ticket 交給
Godot server；server 用服務金鑰向後端兌換，拿到 `user_id`。

**理由**：主功能的 access token 是長效憑證，一旦進到遊戲 server 的記憶體與 log，日後很難收回，
而 Godot server 其實只需要知道「這個 peer 是誰」這一件事。ticket 短效、一次性、只能由持有服務
金鑰的一方兌換，洩漏的後果有界。

**否決的替代方案**：
- *client 直接把 JWT `rpc_id(1)` 給 Godot server 驗證*——實作最省（不用新 model），但等於把主功能
  的長效憑證交給一個不該碰它的服務，且 Godot server 的 log 很容易不小心印出來。
- *維持 client 自報 user_id，只在後端加 rate limit*——沒有解決冒充問題，只是降低利用速度。

### D2：`backend_user_id` 從 player 節點移除，改由 server 端持有

**決定**：刪掉 `player_00.gd` 的 `backend_user_id` 欄位與 `player_00.tscn` 的 `properties/4`
同步項；Godot server 用 server-only 的 `_peer_users: Dictionary` 保存 `peer_id → user_id`。

**理由**：MultiplayerSynchronizer 的語意就是「authority 說了算」，而這裡的 authority 是玩家自己
的瀏覽器。任何「權威資料」放在客戶端擁有的同步屬性上都不可能安全。user_id 只有 server 需要用
（建房），其他 peer 根本不需要知道，本來就不該同步。

### D3：Godot 房不套用「中立 → AI」的分流規則

**決定**：Godot 木樁配對成立的房，一律以 `route=match` 記錄 `DialogueEntryAssignment`，
即使該使用者的問卷分數落在中立區間。stance_score 照實記錄。

**理由**：問卷是在配對成立**之後**才填的（D4）。若這時判定某人「該去 AI」，已經配好的兩人就會
卡死——一個進得去、一個進不去。分流規則的目的是控制實驗分組，而 Godot 大廳本身是遊戲內的自主
相遇，性質不同，資料上用 `matching_algorithm_version="godot_manual"` 已經可以區分。

**例外**：一旦這間房因逾時或對方退出而作廢（D6），使用者退回一般模式時**恢復套用**分流規則。

> ⚠️ 這一條影響實驗分組的可解釋性，實作前建議跟指導老師確認一次。

### D4：問卷排在跳轉之後，不排在進大廳之前

**決定**：玩家不需要填問卷就能進 Godot 大廳逛；配對成立、跳轉到 `/topic/<id>?mode=match` 之後
才跳出前測問卷。

**理由**：使用者選擇。大廳有教學 NPC、議題張貼、表情回復等與實驗無關的社交功能，把問卷擋在門口
會讓純粹想逛的人也被迫作答。

**代價**（要在實作裡處理，不是可以忽略的）：房間建立時還沒有真實分數。由 D8 解決——
`user_a_score` / `user_b_score` 改為 nullable，建房時留 NULL，問卷送出後回填。

### D5：用 `MatchQueueEntry` 的存在當作「已填問卷」的憑證

**決定**：不新增欄位。使用者送出 Godot 房的前測問卷時，建立一筆
`MatchQueueEntry(status=MATCHED, match=<該房>, profile=<新建的 UserStanceProfile>)`，
與一般配對房同構。「這位參與者有沒有對應的 MATCHED queue entry」＝「他填過問卷沒有」。

**理由**：一般配對房本來就有這筆記錄，下游（M5 分析、M6 摘要 pipeline、`get_matching_state`
的 `profile` 欄位）都預期它存在。順手建立它同時解決「填卷憑證」與「下游資料同構」兩件事，
比加一個 `survey_completed_a/b` 布林欄位更省，也更不容易跟既有邏輯打架。

### D6：問卷階段的三種收場

**決定**：Godot 房在雙方都完成問卷前處於「綁定待確認」狀態，有三種收場：

| 收場 | 觸發 | 結果 |
|---|---|---|
| 成功 | 5 分鐘內雙方都送出問卷 | 房間維持 ACTIVE，兩人進聊天室，分數皆為真值 |
| 逾時 | `now > survey_deadline` 且任一方未完成 | 房 CANCELLED，雙方退回一般模式 |
| 對方退出 | 對方 `last_seen` 距今 > 45 秒 | 房 CANCELLED，留下的一方看到「對方已退出」後退回一般模式 |

`survey_deadline = match.created_at + 300s`（兩人是被 server 同時通知跳轉的，用建立時間即可）。

**「退回一般模式」的定義**：
- 已填完問卷者：他的 `UserStanceProfile` 已經存好，直接套用既有的 `DialogueEntryView` 分流——
  極端立場 → `enqueue_for_matching()` 進一般配對佇列；中立 → `DialogueEntryAssignment(route=ai)`，
  前端導去 AI 對話。
- 未填完問卷者：沒有 stance 資料，前端把問卷 Modal 就地換成一般入口問卷（改送
  `/api/dialogue/entry/`），等於回到正常入口。

**理由**：不設時限的話，一方掛著不填就會讓另一方無限期等待；而純粹用既有的 180 秒缺席機制
偵測不到問卷階段的關頁面（觀察 A）。45 秒的門檻是 5 秒輪詢間隔的 9 倍，足以容忍暫時的網路抖動
與換頁，又比 5 分鐘的上限即時得多。

### D8：分數欄位改 nullable，佔位值 4.00 廢除

**決定**：`DialogueMatch.user_a_score` / `user_b_score` 改為 `null=True`，CHECK constraint 放寬為
「NULL 或 1..7」。Godot 房建立時**不寫分數**（留 NULL），問卷送出後由 `godot-survey` 端點回填。
一般配對房的寫入路徑不變（建房當下就有真值，永遠不會是 NULL）。

**理由**：NULL 的語意就是「還沒有 s_pre」，這正是 Godot 房建立當下的真實狀態。寫 4.00 等於在
資料庫裡放一個跟真值無法區分的假值（真實分數也可能剛好是 4.00），然後靠文件約定提醒所有人
別信它——靠紀律維持的正確性遲早會被忘掉。改 nullable 之後「有沒有前測資料」由型別系統管，
分析時一行 `user_a_score IS NOT NULL` 就能過濾。

影響評估（已逐一查證）：
- 全後端**唯一的讀取端**是 `_post_dialogue_stance_snapshot`（`views.py:3103`，後測問卷取 s_pre）。
  在本設計的流程裡它實際碰不到 NULL——能進聊天室、之後填後測的人必然已通過問卷閘門——但仍須
  防禦性處理：值為 None 時後測記錄的 s_pre 留空，不丟例外。
- 立場偏移分析（`hh_analysis.py`）用 `UserStanceProfile.q9_embedding`，不讀這兩欄。
- 需要一支 migration；既有資料不用搬（一般配對房沒有 NULL，既有 Godot 房的 4.00 保持原樣，
  用 `matching_algorithm_version="godot_manual"` + 建立日期即可辨識為舊制資料）。

**否決的替代方案**：*維持 NOT NULL + 4.00 佔位值，靠 §D9 的模組化函式與文件約定辨識*——
零 migration，但假值仍在資料庫裡，防線在紀律而不是在型別。在讀取端只有一處的前提下，
migration 的代價小到不值得為了省它而留下這個謊。

### D9：前測完成狀態收斂到單一函式

**決定**：新增 `apps/matching/services/binding.py`，提供全專案唯一合法的「誰填了前測」查詢入口：

```python
def match_pretest_state(match) -> dict:
    """Godot 綁定房的前測完成狀態。唯一合法的判斷入口——
    不要在任何地方直接比對 user_a_score/user_b_score 來判斷「填過沒有」。"""
    completed = set(
        MatchQueueEntry.objects.filter(
            match=match, status=MatchQueueEntry.Status.MATCHED,
        ).values_list("user_id", flat=True)
    )
    return {
        "user_a_done": match.user_a_id in completed,
        "user_b_done": match.user_b_id in completed,
        "both_done": {match.user_a_id, match.user_b_id} <= completed,
    }
```

裁決函式（§7.3）、status payload 的 `survey_required` / `partner_state`（§8.1）、
`godot-survey` 的重複送出判斷（§8.2）全部只呼叫這一支。

**理由**：這個查詢至少有三個呼叫點，散寫三次一定會漂移。D8 之後雖然 `IS NOT NULL` 也能判斷，
但 queue entry 才是 D5 定義的憑證本體（分數只是它的副產品），以憑證為準。

### D7：裁決由輪詢驅動，另配一支清理指令兜底

**決定**：D6 的三種收場都在 `get_matching_state()` 內判定（任一方的輪詢都會觸發對雙方的裁決）；
另外寫 `manage.py close_expired_godot_matches` 處理「兩人都關掉網頁、沒有人輪詢」的殘留。

**理由**：專案沒有 celery / cron，既有的清理做法就是 management command 交給 ops 排
（`close_stale_dialogue_sessions.py` 是先例）。輪詢驅動能覆蓋絕大多數情況且零基礎設施成本，
指令只負責收尾。

---

## 5. 身份層：一次性 ticket

### 5.1 流程

```
瀏覽器 (React /chat)                後端 (Django)              Godot client (iframe)      Godot server (headless)
      │                                  │                            │                          │
      │─POST /api/godot/tickets/ ───────▶│  (使用者 JWT)               │                          │
      │◀── {ticket, expires_in: 60} ─────│                            │                          │
      │─ frameWindow.bridgeus_ticket = ticket ────────────────────────▶│                          │
      │                                  │                            │─ create_client(ws) ─────▶│
      │                                  │                            │─ submit_ticket.rpc_id(1)▶│
      │                                  │◀── POST /api/godot/tickets/redeem/ ───────────────────│
      │                                  │      (X-Godot-Service-Token)                          │
      │                                  │─── {user_id} ────────────────────────────────────────▶│
      │                                  │                            │◀── 生成角色 ─────────────│
```

⚠️ **券要「拉」不要「推」。** 上圖把發券畫在 iframe 載入時，實作階段一之後發現這樣不行：
券是一次性的，Godot client 只要重連一次（WS 斷線、headless server 重啟）就會拿著已兌換過的
券被踢掉，而且永遠救不回來。60 秒的 TTL 也撐不過冷快取下「下載數 MB WASM → 引擎啟動 →
建立 WS」的總時間。階段二實作時，`GodotLobby.jsx` 不要在 `onLoad` 就把券塞進 window，
改成 Godot 端每次要連線前透過 `JavaScriptBridge` 主動向宿主頁要一張新的。

驗證失敗（ticket 不存在／已用過／逾期／後端不可達）：server 呼叫
`multiplayer.multiplayer_peer.disconnect_peer(peer_id)`，不生角色。

### 5.2 資料模型

新增 `api/models.py`：

```python
class GodotEntryTicket(models.Model):
    """一次性的 Godot 大廳入場券。主功能發，Godot server 用服務金鑰兌換。

    存在的理由：Godot server 只需要知道「這個 peer 是哪個 user」，不需要、也不該
    持有主功能的長效 access token。
    """

    token = models.CharField(max_length=64, unique=True, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="godot_tickets")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    redeemed_at = models.DateTimeField(null=True, blank=True)
```

`token` 用 `secrets.token_urlsafe(32)`。TTL 60 秒。兌換時在 `transaction.atomic()` 內
`select_for_update()` 後檢查 `redeemed_at is None and expires_at > now`，通過才寫入
`redeemed_at`——一次性語意必須靠行鎖保證，兩個 peer 同時送同一張 ticket 只能有一個成功。

### 5.3 端點契約

**`POST /api/godot/tickets/`**（`permissions.IsAuthenticated`）

要求：無 body。
回應 `201`：

```json
{"ticket": "xY3…", "expires_in": 60}
```

**`POST /api/godot/tickets/redeem/`**（`authentication_classes = []`，`permission_classes = [IsGodotServiceToken]`）

要求：

```json
{"ticket": "xY3…"}
```

回應 `200`：

```json
{"user_id": 42}
```

**只回 user_id，不回顯示名稱。** 初版設計曾規劃一併回 `nickname`，實作審查時發現兩個問題：
（a）後端只有 `GuestLoginView` 會寫 `first_name`，而它正要被移除，所以 `first_name or username`
實際上永遠會落到 `username`——那是研究者建帳號時填的字串，很可能是真名或學號；
（b）系統其他地方刻意反向操作（配對房的 `ANONYMOUS_MATCH_USER_NAME = "匿名對話者"`、
`MatchMessageSerializer.get_sender_name` 硬回 `"匿名使用者"`），這裡卻把真實識別字交給遊戲 server。
而且 Godot 端目前沒有任何消費者。日後大廳若真要在頭上顯示名字，再加一個明確是「顯示名稱」
的欄位（例如沿用頭銜 `/api/titles/me/` 那套），不要沿用登入帳號。

回應 `400`（ticket 不存在／已用過／逾期，訊息一律相同，不區分原因）：

```json
{"detail": "入場券無效。"}
```

> `GodotMatchRoomView` 已經示範過 `authentication_classes = []` 是必要的：預設的
> `JWTAuthentication` 遇到過期或損壞的 `Authorization` header 會直接丟 401，輪不到服務金鑰驗證。
> 新端點沿用同一寫法。

### 5.3.1 階段一實作後的已知落差（階段二要處理）

- **停用中的帳號仍可兌換**：`redeem_ticket` 沒有檢查 `user.is_active`。既有的
  `/api/godot/match-rooms/` 也一樣，所以不是退步；但研究過程若需要把某位受試者硬性請出場，
  兩個地方都要補檢查。
- **`expires_in` 會回 59 而不是 60**：view 在 `issue_ticket` 蓋完 `expires_at` 之後才重新取
  `timezone.now()` 再 `int()` 截斷。無害，除非階段二拿這個值去算重新發券的排程。
- **券不做回收**：一次入場一列，永久累積。~60 人的研究規模下已接受（見 §12）；
  若日後對外開放要補一支清理指令，沿用 `close_stale_dialogue_sessions.py` 的形式。

### 5.4 移除 guest

- 刪除 `views.py::GuestLoginView` 與 `urls.py:124` 的 `guest/` 路由。
- 刪除 `Backend.gd::guest_login()` 與 `game.gd:56-63` 的 fallback 分支。
- 既有的 `guest_xxxxxxxx` 帳號不自動刪除（可能已經產生實驗資料），由研究者在 Django admin
  自行決定；本案只保證不會再產生新的。

## 6. Godot 端：身份表與 spawn 授權

`game.gd` 新增 server-only 狀態：

```gdscript
var _peer_users := {}   # peer_id:int -> backend user_id:int（僅 server 使用，不同步）
```

流程改為：

1. client `_on_connected_to_server()` → `submit_ticket.rpc_id(1, _ticket)`（`any_peer`, `reliable`）。
2. server 收到 → `Backend.redeem_ticket(ticket, callback)`；成功則 `_peer_users[sender] = user_id`
   並直接 `_spawn_player(sender)`；失敗則 `disconnect_peer(sender)`。
3. `request_spawn` 保留給 host 自己（`call_local` 路徑），並補上授權與防重複：

```gdscript
@rpc("any_peer", "call_local", "reliable")
func request_spawn():
	if not multiplayer.is_server():
		return
	var id = multiplayer.get_remote_sender_id()
	if id == 0:
		id = 1                       # call_local，host 自己
	if not _peer_users.has(id):
		return                       # 身份未驗證，不給身體
	if get_node_or_null(str(id)) != null:
		return                       # 已有身體，防重複
	_spawn_player(id)
```

4. `_on_peer_disconnected()` 補 `_peer_users.erase(id)`。
5. `_do_seat()` 的 user_id 來源從 `pl.backend_user_id` 改為 `_peer_users.get(pid, 0)`；
   任一方拿不到就不建房，直接 `seat_denied` 兩人並釋放座位。

**同時修掉的 code review 項目**：原 `request_spawn(id)` 用 client 傳來的參數，可以冒名或洗版；
撞名會讓 Godot 自動改節點名，而「節點路徑在每個 peer 一致」是整份多人架構的地基
（見 `godot/CLAUDE.md`），一旦破掉，`rpc_id` 定位、`_find_player()`、`_do_seat` 的
`get_node_or_null(str(pid))` 會一起錯。原本也沒宣告 `"reliable"`（`@rpc` 的 transfer mode
預設是 unreliable），一併補上。

## 7. 配對綁定狀態機

### 7.1 時序

```
玩家A 坐木樁 ──┐
玩家B 坐木樁 ──┴─▶ server _do_seat: 兩樁滿
                     │
                     ├─▶ POST /api/godot/match-rooms/ (帶 callback)
                     │      ├─ 200/201 ─▶ match_found.rpc_id(A/B, topic_id, room_id)
                     │      └─ 其他 ────▶ seat_denied + _do_unseat，玩家可重坐
                     │
                     ▼
              Godot client: JavaScriptBridge → window.parent.postMessage
                     │
                     ▼
              GodotLobby.jsx: navigate('/topic/<topic_id>?mode=match&from=godot')
                     │
                     ▼
              TopicChat: poll /api/matching/status/ → survey_required=true
                     │
                     ├─ 兩人都送出問卷（5 分鐘內）──▶ 進聊天室
                     ├─ 逾時 ───────────────────────▶ 房 CANCELLED，退回一般模式
                     └─ 對方 last_seen > 45s ───────▶ 房 CANCELLED，提示後退回一般模式
```

### 7.1.1 一併補上「同一人佔兩個木樁」的擋法（階段二審查發現）

同一位使用者開兩個分頁會拿到兩張券、兌換成兩個不同的 peer 但**同一個 `user_id`**。
`_do_seat` 目前只擋 `user_ids.has(0)`，擋不掉 `user_ids[0] == user_ids[1]`——後端會回 400
（`views.py` 的 `user_ids[0] == user_ids[1]` 檢查），但 Godot 端在 C1 之前沒有接 callback，
照樣會跑 `_finish_match` 把兩個身體都刪掉，使用者白白失去角色又沒有任何訊息。

C1 接上 callback 之後這種情況會被錯誤路徑自然接住，但更早擋在 `_do_seat`（沿用既有的
`seat_denied` + `_do_unseat` 拒絕路徑，訊息如「不能和自己配對」）比較精確，實作 C1 時一併加。

**階段三已實作**：擋法落在 `_try_start_match` 而不是 `_do_seat`——同一人的兩個分頁仍然可以
各自坐上議題的兩根木樁（`_do_seat` 只看座位是否已被同一個 peer 佔用，兩個不同 peer 對它是
合法的兩人），要等兩樁都坐滿、湊出 `occupants` 之後才查得到 `user_ids[0] == user_ids[1]`。
命中時對兩個 peer 各自 `_seat_deny_and_unseat(pid, "不能與自己配對，請關閉多餘的分頁")`，
不會呼叫 `Backend.request_topic_match`。

### 7.2 建房時寫入綁定資訊

`GodotMatchRoomView` 建立 `DialogueMatch` 時，在 `stats` 寫入：

```python
stats={
    "binding": {
        "source": "godot",
        "survey_deadline": (timezone.now() + timedelta(seconds=300)).isoformat(),
    }
}
```

`stats` 已經被 presence 狀態使用（`matcher.py:148` 的 `_stats_dict`），兩者是不同的 key，
互不干擾。冪等路徑（沿用既有房間）不重設 deadline。

同時（D8）：建房時 `user_a_score` / `user_b_score` **不再寫 4.00**，留 NULL，
待 `godot-survey` 回填。

### 7.3 裁決函式

`matcher.py` 新增，由 `get_matching_state()` 在 `binding.source == "godot"` 且
房仍 ACTIVE 時呼叫：

```python
def resolve_godot_survey_gate(*, match, now=None) -> DialogueMatch:
    """Godot 綁定房在雙方完成前測問卷前的裁決：逾時或對方退出就作廢。

    只在輪詢路徑上跑（雙方在問卷階段都以 5 秒間隔打 /api/matching/status/，
    get_matching_state 內部的 mark_match_participant_connected 就是心跳）。
    兩人都關掉網頁的殘留由 close_expired_godot_matches 指令收尾。
    """
```

判定順序（先判退出再判逾時，因為退出的訊息對使用者比較具體）：

1. 對任一參與者，若 `last_seen` 存在且距 `now` > `GODOT_SURVEY_PRESENCE_TIMEOUT_SECONDS`（45）
   → `cancel_reason = "godot_partner_left"`。
2. 若 `now > survey_deadline` 且 `match_pretest_state(match)["both_done"]` 為偽（D9）
   → `cancel_reason = "godot_survey_timeout"`。
3. 命中任一條：`match.status = CANCELLED`，`stats["binding"]["cancel_reason"]` 記原因與時間戳，
   然後對**已填問卷**的參與者執行退回一般模式（§7.4）。

兩個秒數都做成可用環境變數覆寫的 module 常數，與 `match_room_idle_timeout_seconds()` 同模式。

### 7.4 退回一般模式

對已建立 `UserStanceProfile` 的參與者：

- 取回他送問卷時算出的 `stance_score` / `stance_category`。
- `can_enter_human_matching(stance_category)` 為真 → `enqueue_for_matching(...)` 進一般佇列，
  並 `update_or_create` 一筆 `DialogueEntryAssignment(route=MATCH)`。
- 為偽 → `update_or_create` 一筆 `DialogueEntryAssignment(route=AI)`；前端據此導去 AI 對話。
- 這位參與者原本掛在作廢房上的 `MatchQueueEntry` 一併處理：舊那筆標 `CANCELLED`，
  新的排隊由 `enqueue_for_matching` 自己建，避免 `uniq_active_queue_user_topic` 衝突。

未建立 profile 的參與者不需要後端動作——他下一次輪詢會看到房已 CANCELLED，前端把問卷改送
`/api/dialogue/entry/` 即可。

## 8. API 契約

### 8.1 修改：`GET /api/matching/status/` 的回應

`_build_matching_state_payload()` 新增四個欄位（非 Godot 房時 `binding_source` 為 null，
其餘欄位省略或給預設，前端只在 `binding_source === "godot"` 時使用）：

| 欄位 | 型別 | 說明 |
|---|---|---|
| `binding_source` | `"godot"` \| null | 這間房是不是 Godot 木樁綁定的 |
| `survey_required` | bool | 這位使用者還沒送出前測問卷（由 `match_pretest_state()` 判定，D9） |
| `survey_deadline` | ISO 8601 \| null | 前端倒數用 |
| `partner_state` | `"pending"` \| `"ready"` \| `"left"` | 對方的問卷/在線狀態 |

房被裁決作廢後，回應的 `status` 會變成裁決後的新狀態（`matching` / `idle`），並帶
`binding_cancel_reason`（`"godot_partner_left"` / `"godot_survey_timeout"`）讓前端顯示對應提示。

### 8.2 新增：`POST /api/matching/godot-survey/`

`permissions.IsAuthenticated`。**不經過** `_entry_gate_response`——這條路徑的分組是由 Godot
配對決定的，不是由分流決定的（D3）。

要求：

```json
{
  "topic_id": 102,
  "survey_answers": {"Q1": 6, "…": 3},
  "survey_open_answers": {"Q9": "我認為…"}
}
```

處理：

1. 找 `status=ACTIVE`、`stats.binding.source == "godot"`、同 topic、且 `request.user` 是
   `user_a` 或 `user_b` 的 `DialogueMatch`。找不到 → `404`。
2. 先跑一次 §7.3 的裁決；若房已作廢 → `409` 並回帶 `cancel_reason`，前端據此走退回流程。
3. `_compute_user_stance_score()` 算分 → `resolve_stance_category()` → upsert `UserStanceProfile`
   （含 survey answers、open answers、embedding，與 `enqueue_for_matching` 用同一套建構邏輯）。
4. 建 `MatchQueueEntry(status=MATCHED, match=match, profile=profile, stance_score=…)`（D5）。
5. 回填 `match.user_a_score` 或 `user_b_score`（該欄從 NULL 變真值，D8）；若兩邊都已非 NULL，
   順便計算 `likert_distance`。
6. `update_or_create` 一筆 `DialogueEntryAssignment(route=MATCH, …)`（D3）。
7. 回 `_build_matching_state_payload(...)`。

重複送出（`match_pretest_state()` 顯示已完成，D9）→ 視為更新，覆寫同一筆 profile 與分數，
不建第二筆 queue entry。

### 8.3 新增：ticket 兩支端點

見 §5.3。

### 8.4 移除：`POST /api/guest/`

見 §5.4。

## 9. 前端行為

### 9.1 `GodotLobby.jsx`

- **`bridgeus_token` 保留、`bridgeus_user_id` 移除**。初版設計寫「兩個都拿掉」，實作階段一後
  修正：Godot client 自己打的 `/issues/`、`/titles/me/`、`/issues/<id>/reactions/` 都需要使用者
  JWT，而 iframe 與主功能同源、同一個瀏覽器信任域——JWT 留在 client 跟留在 React 的
  localStorage 是同一件事，D1 防的是 JWT 流進「Godot server」那個不同機器的程序，不是這裡。
  `bridgeus_user_id` 才是真正該消失的：識別交給 ticket 兌換後，client 不再需要（也不該）知道
  要自報什麼 user_id。
- 發券改「拉」式（見 §5.1 的警告）：`handleLoad` 掛一個 `frameWindow.bridgeus_request_ticket`
  函式，Godot 每次連線前呼叫它，完成後把券寫進 `frameWindow.bridgeus_ticket`。不在 iframe
  載入時就發（一次性的券撐不過重連與 WASM 冷啟動）。`bridgeus_api_base` / `bridgeus_ws_url` 維持。
- 新增 `message` 事件監聽，收到跳轉訊息時 `navigate()`。**必須同時檢查
  `event.origin === window.location.origin` 與 `event.source === iframeRef.current.contentWindow`**——
  只檢查 origin 擋不掉同源的其他 frame。

```js
const handleMessage = (event) => {
  if (event.origin !== window.location.origin) return;
  if (event.source !== iframeRef.current?.contentWindow) return;
  const data = event.data;
  if (data?.type !== 'bridgeus_match') return;
  navigate(`/topic/${Number(data.topic_id)}?mode=match&from=godot`);
};
```

### 9.2 `TopicChat.jsx`

- **輪詢**：`showSurvey` 為真時目前把 poll 的 `roomId` 設成 null（`:705`）。改成
  `binding_source === "godot"` 的情況下問卷期間**照樣以 5 秒間隔 poll**——它同時是倒數的時間
  來源、對方存在的心跳、與自己的心跳。
- **問卷觸發**：`setShowSurvey(status === 'idle')`（`:858`）改為
  `setShowSurvey(status === 'idle' || payload.survey_required)`。
- **送出目標**：`handleSurveySubmit` 在 `survey_required && binding_source === "godot"` 時改打
  `/api/matching/godot-survey/`，其餘情況維持現行的 `/api/matching/join/` 或 `/api/dialogue/entry/`。
  **打錯端點是這一段最危險的地方**：對已綁定的房呼叫 `matching/join` 會重新排隊，把綁好的房弄壞。
- **等待畫面**：自己已送出、`partner_state === "pending"` → 顯示「等待對方完成問卷（剩 mm:ss）」，
  不進聊天室。`"ready"` → 進聊天室。
- **對方退出**：`partner_state === "left"` 或回應帶 `binding_cancel_reason` → 關閉問卷/等待畫面，
  顯示「對方已退出配對，已為你轉回一般模式」，然後依後端回的新 `status` 接手（`matching` 顯示
  排隊中；AI 分流則導向 AI 對話）。
- **逾時**：前端**不自己判定**倒數歸零，等後端輪詢回 CANCELLED。單一真值來源，避免兩邊時鐘
  不一致造成一方以為還有時間、一方已經作廢。

### 9.3 `SurveyModal.jsx`

新增 optional 的 `deadline` prop（ISO 字串）。有值時右上角顯示 `剩餘 mm:ss`，剩 60 秒轉紅。
沒傳就完全不顯示——一般入口的問卷不該有倒數。

## 10. 研究資料完整性

| 情況 | `user_X_score` | `MatchQueueEntry` | `DialogueEntryAssignment` | 可辨識性 |
|---|---|---|---|---|
| Godot 房，雙方填完 | 兩邊皆真值 | 兩筆 MATCHED | 兩筆 route=match | `matching_algorithm_version="godot_manual"` |
| Godot 房，逾時作廢 | 一邊真值一邊 NULL | 只有填的人有 | 填的人有（作廢後重算） | `stats.binding.cancel_reason` |
| Godot 房，對方退出 | 同上 | 同上 | 同上 | 同上 |
| 一般配對房 | 兩邊皆真值 | 兩筆 | 兩筆 | `matching_algorithm_version` 為演算法版本 |

**M6 摘要 pipeline 的處理**：CANCELLED 的房不會觸發 M6（pipeline 掛在
`_close_locked_match` → CLOSED）。作廢房不會產生觀點知識庫資料，符合預期。

**分析時的過濾規則**（供資料分析同學參考）：D8 之後，「有完整前測資料的 Godot 房」就是
`matching_algorithm_version='godot_manual' AND user_a_score IS NOT NULL AND user_b_score IS NOT NULL`。
程式碼內的判斷一律走 `match_pretest_state()`（D9），不要自己寫查詢。
**例外**：本案上線前既有的 `godot_manual` 房分數是舊制的 4.00 佔位值，無法與真值區分，
分析時應以建立日期（本案部署日之前）整批排除。

## 11. 邊界與失敗模式

| 情境 | 行為 |
|---|---|
| 未登入直接開 `/godot/takeAbridge_godot.html` | 沒有 ticket → Join 鍵停用，顯示「請先從主功能登入後進入大廳」 |
| ticket 逾期（放著 60 秒以上才連） | server 兌換失敗 → 斷線；前端 iframe 重載會自動換一張新的 |
| 同一張 ticket 被送兩次 | 行鎖保證只有第一次成功，第二個 peer 被斷線 |
| 後端在建房當下不可達 | `request_topic_match` callback 收到 `code=0` → `seat_denied("配對建立失敗，請稍後再試")` + 釋放兩人座位，可重坐 |
| `service_token` 未設定（設定錯誤） | 同上，玩家看到失敗訊息而不是被靜默刪角色 |
| 建房成功但玩家沒跳轉（擋 postMessage、手動關 iframe） | 房仍存在；5 分鐘後由逾時裁決或清理指令作廢 |
| 兩人都關掉網頁 | 無人輪詢 → `close_expired_godot_matches` 收尾 |
| 一人在問卷階段重新整理頁面 | 輪詢中斷 < 45 秒不觸發退出判定；重載後從 `survey_deadline` 繼續倒數 |
| 玩家從配對頁按上一頁回大廳 | iframe 重載 → 換新 ticket → 重新驗證 → 重新生角色。原房若已作廢就是一般狀態 |
| 對方在自己送出問卷的同一瞬間退出 | `godot-survey` 端點步驟 2 先跑裁決，回 `409` + `cancel_reason`，不會寫入孤兒 profile |

## 12. 已知妥協

- **本案上線前既有的 `godot_manual` 房留著 4.00 佔位值**，不做資料搬移（無法區分真假，
  搬了反而製造假資料）。分析時以建立日期整批排除（§10）。
- **45 秒的退出判定會誤判長時間斷網**。使用者網路中斷 45 秒以上會被當成退出。考慮到問卷階段
  只有 5 分鐘，這個誤判的損失有限（退回一般模式，不會遺失問卷資料）。
- **Godot 端沒有自動化測試**。§13 的 Godot 部分全部是手動雙開驗證。

## 13. 測試策略

**後端**（`backend/api/tests/`，`cd /Users/light/code/backend && uv run pytest <目標>`，
**必須序列執行**——共用同一個 Postgres test database）：

- ticket：發券需登入；兌換需服務金鑰；一次性（第二次回 400）；逾期回 400；
  併發兌換只有一個成功（`select_for_update` 行為）。
- 分數 nullable（D8）：Godot 建房後兩欄為 NULL；一般配對房建房後兩欄非 NULL（迴歸）；
  `_post_dialogue_stance_snapshot` 遇到 NULL 回 None 而非丟例外。
- `match_pretest_state`（D9）：零筆／一筆／兩筆 MATCHED queue entry 三種狀態的回傳值。
- `godot-survey`：非參與者回 404；房已作廢回 409 + cancel_reason；成功後該欄從 NULL 變真值、
  `MatchQueueEntry` 建出來了、`DialogueEntryAssignment.route == "match"`
  （即使 stance 是中立——D3 的迴歸測試）；雙方都填完後 `likert_distance` 有值；
  重複送出不會建第二筆 queue entry。
- 裁決：逾時作廢；對方 last_seen 過期作廢；已填問卷者退回一般模式後，極端 → 進佇列、
  中立 → route=ai（兩條分支各一個測試）。
- 清理指令：`--dry-run` 不寫入；逾期房被關；未逾期房不動。
- 移除 guest：`POST /api/guest/` 回 404。

> **注意 `force_authenticate` 的盲點**（階段一與混合入口階段二都踩過）：它直接塞
> `request.user`，完全不走 authentication 流程，所以驗證不到「這個 view 用哪個
> authentication class」。ticket 的兩支端點各需要一個用真 `AccessToken`
> （`rest_framework_simplejwt.tokens.AccessToken.for_user`）或真服務金鑰 header 的測試，
> 並確認它在退化時真的會失敗。

**前端**：`GodotLobby` 的 message handler 需要單元測試（錯誤 origin / 錯誤 source 不觸發 navigate）。

**Godot / 端到端手動驗證**（雙開兩個瀏覽器分頁，各用不同帳號）：

1. 未登入直開 export → 進不去。
2. 兩人登入 → 各自角色的 user_id 在 server log 正確。
3. 兩人坐同議題木樁 → 雙方跳轉到 `/topic/<id>?mode=match`。
4. 兩人各填問卷 → DB 中分數為真值 → 能互傳訊息。
5. 一人填完、一人關頁面 → 45 秒內另一方看到「對方已退出」並轉一般模式。
6. 兩人都不填 → 5 分鐘後房 CANCELLED，`cancel_reason` 正確。
7. 關掉 Django → 坐木樁 → 看到失敗訊息且能重坐（不是被靜默刪角色）。

## 14. 實作階段

| 階段 | 內容 | 可獨立驗收 |
|---|---|---|
| 1 | ticket 端點 + model + 移除 guest（後端） | 是（API 測試） |
| 2 | Godot 身份表 + spawn 授權 + 移除 `backend_user_id` 同步 | 是（手動 1–2） |
| 3 | 建房 callback + `match_found` 帶參數 + postMessage + `GodotLobby` 跳轉 | 是（手動 3、7） |
| 4 | 分數 nullable migration（D8）+ `binding.py` 的 `match_pretest_state`（D9）+ `godot-survey` 端點 + status payload 新欄位 + 前端問卷觸發與送出 | 是（手動 4） |
| 5 | 裁決函式 + 退回一般模式 + 倒數 UI + 清理指令 | 是（手動 5、6） |

階段 1、2 合併成一次 PR（身份層是一個完整的改動，拆開會留下半套狀態）；3 一次；4、5 一次。

## 15. 待決問題

1. **D3 需要指導老師確認**：Godot 木樁配對不套用「中立 → AI」分流，會讓中立立場的受試者
   出現在真人配對資料中（可用 `matching_algorithm_version` 區分，但實驗設計上要有說法）。
2. **既有 guest 帳號的處置**：本案只保證不再產生新的。已存在的要不要清、清之前要不要保留
   其實驗資料，需要研究者決定。
3. **`close_expired_godot_matches` 的排程頻率**：建議每分鐘，需與 ops（部署 runbook）確認
   systemd timer 或 cron 的掛法。
