# P5 設計 — AI Session 狀態單一化(turns 為權威)

對象:`api/services/dialogue_session.py`(P4 已抽出)、`api/views.py` 的 4 個 AI session view、
`api/consumers.py` 的 `DialogueStreamConsumer`。

本文件只是設計,不含實作。實作者(5b)請嚴格照此文件;與 P1 鎖設計
(`p1_lock_design.md`)衝突時以 P1 為準並回報,不要自行仲裁。
**本設計需人工過目放行後才進 5b。**

5b 請以 P4 分支 `refactor/m3-p4-service-extraction`(或其合併目標)為基底——
本設計引用的 `api/services/dialogue_session.py` 只存在於 P4 之後;
在 P4 之前的分支上,同一批函式還在 `api/views.py` 內,邏輯相同。

---

## 1. 現況問題(三份 source of truth)

同一份對話狀態存在三處:

1. cache dict(`dialogue_session:{session_id}`,TTL 12h)
2. `DialogueSessionRecord.session_state`(JSON,含 history 全文)
3. `AIConversation` turns(append-only,reply 與 WS 兩條路徑本來就每輪寫)

reply(`views.py` `DialogueSessionReplyView`)、WS stream(`consumers.py`)、
analyze(`DialogueSessionSemanticTreeAnalyzeView`)三條路徑都是
「讀 → 記憶體改 → `_persist_dialogue_session_record` 整包 `update_or_create` + `cache.set` 整包覆蓋」,
互相 last-write-wins(CODE_REVIEW P2)。analyze 後 views.py 還把整個 session_record
(含它沒動過的 history)重新 persist + cache,是互蓋的最大破口。

附帶發現(現況 bug,本設計順帶修掉):reply/WS 都先
`session_record["session"] = session.to_dict()` 再算 stance drift——`to_dict()` 不含
`stance_drift`,舊值被抹掉,`previous_value` 永遠是 None,`direction` 永遠 `"stable"`。
metadata 與 `DialogueSession` 序列化分離(§3)後此 bug 結構性消失。

## 2. 權威資料定義(核心決策)

| 資料 | 權威來源 | 說明 |
|------|---------|------|
| 對話 history | `AIConversation` turns | 唯一來源;任何地方不再持久化 history 全文 |
| dialogue_phase | turns(可推導) | `DialoguePhase.from_turn_count` + per-turn 欄位;session_state 存 denormalized 副本,不一致時以 turns 為準 |
| stance 基線(score / label / agent_stance / initial_argument…) | `session_state` | 建立時寫入,之後不變 |
| user_reasoning_mode / focus_signal_count | `session_state` | 唯二會變動的 metadata,寫入走 row lock(§6) |
| stance_drift | 每輪從 turns 全量重算 | session_state 只存「上一次量測值」(方向判定需要 previous_value) |
| semantic tree | `semantic_tree_state` 欄位 | analyze 路徑獨占,依 P1 鎖方案讀寫;本設計不碰其內部 |
| cache | 非權威 | 純 read-through 投影,可隨時 kill(§5) |

## 3. `session_state` 保留 / 移除欄位清單

**保留(11 個 key):**
`topic`, `topic_description`, `agent_stance`, `agent_stance_summary`,
`user_stance_label`, `user_stance_score`, `user_initial_argument`,
`user_reasoning_mode`, `focus_signal_count`, `dialogue_phase`, `stance_drift`

**移除(1 個 key):**
`history` — 不再寫入 DB;舊資料讀取相容見 §8。

實作形狀:新增 `session_metadata(session: DialogueSession, *, stance_drift) -> dict`
之類的 helper = `to_dict()` 去掉 `history` 再併入 `stance_drift`。
**`DialogueSession` dataclass 本身不改**——in-memory 物件仍持有 history
(prompt 組裝需要 `format_history` / `turn_count`),只在持久化邊界剝離。

組裝順序約束:drift 重算(`calculate_ai_session_stance_drift`)必須在
read-through 讀到的 metadata 上進行——它的 `previous_value` 來自前次寫回的
`stance_drift`——算完再組 metadata 寫回。不可先用 `to_dict()` 覆蓋
`session_record["session"]` 再計算,那正是 §1 direction-永遠-stable bug 的成因。

## 4. History 重建時機與 `_rebuild_session_state_from_turns` 去留

- **保留並轉正**:它從「對齊備援」變成 history 的唯一產生器。建議改名
  `build_session_state_from_turns` 以反映新語意(不再是 re-build 修補)。
- **重建時機只有兩個**:
  1. read-through miss——`_restore_dialogue_session_record_for_user` 的 DB fallback;
  2. history 檢視/timeline payload 組裝(`_dialogue_session_cache_payload_from_record` 的兩個 history view 呼叫點)。
  寫入路徑(reply/WS)**不**重建——它手上的 in-memory session 已含完整 history
  (讀取時 rebuilt + 本輪 append)。
- **重建規則維持現行**:`order_by("created_at", "id")`;`user_prompt` 有值 → user entry;
  `ai_response` 有值 → agent entry;`ai_response` 為空的 turn(LLM 失敗輪)仍收錄
  user entry——使用者確實說過,且與 turns 權威一致。
- dialogue_phase:取最後一個非空 `turn.dialogue_phase`,fallback 到 session_state 副本,
  再 fallback `"engagement"`(現行邏輯不變)。

## 5. 快取規則(read-through + invalidate-on-write)

Key 與 TTL 不變(`dialogue_session:{session_id}`,12h)。

- **`cache.set` 只允許出現在一個地方**:`_restore_dialogue_session_record_for_user`
  的 DB fallback(read-through populate)。cache 內容 = 組裝好的 session_record
  (含 rebuilt history),與現在相同形狀,前端/呼叫端無感。
- **所有寫入者一律 `cache.delete`,禁止 write-through**(與 P1 §4 同一決策、同一理由:
  write-through 會與並發 reply 再賽跑一次)。
- **失效點清單**(每一點 = DB 有任何 turn 或 record 寫入之後):

| 時機 | 備註 |
|------|------|
| reply 成功(turn 補上 ai_response + metadata 寫回後) | 取代現在的 `cache.set` |
| reply 失敗(user turn 已 create、LLM 掛掉回 503) | **現況漏掉**——DB 已多一個 turn,cache 卻停在舊 history,必須補 delete |
| WS stream 成功 / 失敗 | 同上兩列 |
| analyze 交易 2 commit 後 | P1 已定,本設計承接 |
| history analyze(`HistoryConversationSemanticTreeAnalyzeView` ai 分支) | 現在的 `_cache_dialogue_session_record` 改成 delete |
| 未來 session close / status 變更 | 目前無此 view,規則先立 |

- **create 路徑不 `cache.set`**:只寫 DB,讓第一次讀 populate。少一條「寫路徑可以 set」
  的規則例外,代價只是首次讀多一次 DB query。
- **已知且接受的 read-repopulate race**:讀者從 DB 讀出舊狀態、寫者寫 DB 並 delete、
  讀者才 `cache.set` ⇒ cache 短暫停留在舊資料。接受理由:權威在 turns,cache 永遠
  不會回寫 DB,不可能造成永久損壞;髒窗只影響 history 顯示少一輪,且下一次任何
  寫入的 delete 就清掉,TTL 12h 兜底。不為此引入版本化 key 或 double-delete。

## 6. 持久化寫入規則(欄位所有權)

`_persist_dialogue_session_record`(整包 `update_or_create`)拆成兩個,原函式刪除:

1. `create_dialogue_session_record(...)` — 僅 create 路徑,完整建立(行為同現在)。
2. `update_session_metadata(*, session_id, user_id, metadata: dict)` — reply/WS 用:
   `transaction.atomic()` 內 `select_for_update` 鎖 record row → 鎖內重讀並依下述
   規則合併 → 只更新 `session_state`(= §3 的 metadata dict,無 history)
   與 `last_activity_at`,以 `update_fields` 寫回。**絕不碰**
   `semantic_tree_state` / `survey_context` / topic 欄位 / `status`。
   WS 呼叫端經 `sync_to_async`(thread_sensitive 預設值)包裝,交易完整落在同一執行緒。

欄位所有權(寫入者 × 欄位互斥):

| 欄位 | 唯一寫入者 |
|------|-----------|
| `session_state`, `last_activity_at` | reply / WS(經 `update_session_metadata`) |
| `semantic_tree_state` | analyze(P1 交易 2,自己的 `select_for_update`) |
| 其餘欄位 | create 路徑一次寫定 |

因此 reply × analyze 並發寫入的欄位互斥,P2 的「三路徑互蓋、history 遺失」結構性消失
(history 根本不再被寫)。

**reply × reply 並發(同 user 前端重試/連點)**:turns append-only 不會遺失;
但 metadata 是鎖外舊讀算出來的,單純用 row lock 序列化寫入**擋不住 lost update**——
後取得鎖的 writer 照樣用自己的舊基準蓋掉前一個 writer 的結果。因此
`update_session_metadata` 必須「**鎖內重讀 → 合併 → 寫回**」,不可拿呼叫端
鎖外組好的 dict 直接整欄覆蓋。合併規則(只有 4 個 key 會變動,其餘 key
建立後不變,取呼叫端值即可):

| key | 合併規則 | 理由 |
|-----|---------|------|
| `dialogue_phase` | 鎖內以 DB user-turn 數重算 `DialoguePhase.from_turn_count` | phase 是 turn 數的單調函數,重算必然收斂正確 |
| `focus_signal_count` | `max(DB 現值, 呼叫端值)` | 單調遞增計數,max 不遺失 |
| `user_reasoning_mode` | 單向升級:任一方為非 `"unknown"` 就取非 unknown 值 | 現行唯一轉移是 unknown → collaborative,不可逆 |
| `stance_drift` | 取呼叫端值 | 每次都以全量 turns 重算,後寫覆蓋無害(頂多 `measured_at` 較新) |

兩把 `select_for_update`(本設計的 metadata 寫回、P1 的 analyze)鎖同一 row,
但都是毫秒級純 DB 操作,互相短暫排隊即可,無 deadlock 面(單一 row、無巢狀鎖)。

**呼叫端清理**:`views.py` analyze 後的
`_cache_dialogue_session_record` + `_persist_dialogue_session_record`(兩處:
`DialogueSessionSemanticTreeAnalyzeView`、`HistoryConversationSemanticTreeAnalyzeView` ai 分支)
整包覆蓋刪除,改為 `cache.delete`;semantic tree 寫回由 P1 交易 2 全權負責。

## 7. 四條路徑影響面

| 路徑 | 讀取 | 寫入 | 行為變化 |
|------|------|------|---------|
| **reply**(REST) | read-through 恢復(cache 或 turns 重建)→ `DialogueSession.from_dict` | ① user turn create(不變)② ai_response 補寫(不變)③ `update_session_metadata`(取代整包 persist)④ `cache.delete`(取代 `cache.set`);LLM 失敗路徑補 `cache.delete` | 回應 payload 的 `history` 改用 in-memory session 組(內容等價);`stance_drift.direction` 開始有非 stable 值(§1 bug 修復) |
| **WS stream** | 移除自己的裸 `cache.get`,改用與 REST 同一個 read-through helper(順帶補上 cache 命中時的 `user_id` 檢查——現行裸 get 沒驗) | 同 reply ①–④;`focus_signal_count`/`user_reasoning_mode` 變動隨 metadata 寫回 | 重連恢復從「cache 沒了就可能斷」變成「一定能從 turns 重建」。另建議:`_create_ai_conversation` 失敗時回錯誤中止本輪,取代現況吞例外繼續——單一權威下,沒落 DB 的 turn 等於不存在,繼續對話只會產生使用者看得到、系統記不住的訊息 |
| **analyze** | 依 P1:收 `session_id`/`user_id`,鎖內重讀 record;pending 來源本來就是 turns,不變 | 只寫 `semantic_tree_state`(P1 交易 2)+ `cache.delete`;**不再** persist/cache 整包 session_record | 不再有機會覆蓋 reply/WS 剛寫的狀態;view 端兩行整包覆蓋刪除 |
| **restore**(latest / detail / history views) | read-through;history 一律由 turns 重建 | 無(read-only;`_restore` 的 populate 除外) | payload 形狀不變;kill cache 後可完整恢復成為不變量而非僥倖 |

## 8. 舊資料相容(migration 策略)

- **無 schema migration**:`session_state` 是 JSONField,欄位縮減只是 key 消失。
- 舊 record 的 `session_state` 內含 `history`:
  - **讀**:turns 非空 → 忽略舊 `history` key(turns 為準,即現行 rebuild 的覆蓋行為);
    turns 為空**且**舊 history 非空 → fallback 使用舊 history(log warning 一次)。
    注意這正是現行 `_rebuild_session_state_from_turns` 的既有行為
    (`if history:` 才覆蓋,turns 空時舊 key 原樣保留)——5b 只需補 log,不需新邏輯。
    後者保護「歷史上只寫了 session_state、沒有對應 turns」的極舊 session——
    無法從程式碼證明這種資料不存在,計畫原建議的「一律忽略」有靜默丟資料風險,
    fallback 一行成本換掉這個風險。
  - **寫**:`update_session_metadata` 寫入的 dict 不含 `history` key,
    整欄覆蓋 `session_state` ⇒ 該 session 首次有新活動時舊 history key 被清除。
  - **已知有界損失(明講,不掩蓋)**:若某 record 屬於「turns 全空、僅
    session_state 有 history」的極舊資料,且使用者恢復它並發出新訊息,
    則清除後 turns 只剩新的一輪,舊對話永久遺失(fallback 從此不再觸發)。
    接受理由:(1) 產生這種 record 需要該 session 當年**每一輪** turn 寫入都失敗
    ——REST reply 的 turn create 沒有 try/except,失敗直接 500、連 history
    都不會累積;只有 WS 路徑吞例外——機率趨近於零;(2) 這類 record 對分析管線
    (stance drift、semantic tree)本來就不可見,它們一直只讀 turns。
    同理,「單輪 turn 寫入失敗但 history 有該輪」的個別 exchange 在單一權威化後
    會從 history 消失——同樣是分析管線從未看過的資料,接受。
  - **5b 合併前先量測,把未知風險變成已量測風險**:跑一次 one-off 統計
    「`session_state.history` 非空且無任何對應 `AIConversation`」的 record 數
    (資料量小,shell 迴圈逐筆 `exists()` 即可)。結果為 0(預期)→ 照本設計走;
    \> 0 → 對這些 record 先做一次性 turns 回填再上線,回填規則屆時另議。
  - **不做全面 data migration 回填 turns**:否決理由——把 history 的
    user/agent entry 配對回 turn 的規則含糊(失敗輪、半輪、順序異常);
    fallback 讀取 + 上述量測已覆蓋需求。殘留在舊 record 裡、已被 turns
    重複覆蓋的 history key,由 P6 清理包擇機掃掉,不在 P5 範圍。

## 9. 與 P1 鎖設計的相容性

- P1 §4 要求 AI 版 analyze 改收 `session_id`/`user_id`、record 鎖內重讀——與本設計
  read-through、不信任傳入 dict 的方向完全一致;5b 若先於 P1 實作合併,view 呼叫端
  照 P1 目標簽名寫。
- P1 交易 2「只更新 semantic-tree 欄位、commit 後 cache delete、不 write-through」——
  本設計 §5/§6 原樣承接;P1 §4 預告的「P5 之後這裡可能整段簡化」具體化為:
  view 端 analyze 後的整包 persist/cache 兩行刪除。
- P1 的 claim / `analyzedSourceIds` 衝突偵測不受影響:pending 來源本來就是 turns
  (append-only),本設計沒有改變 analyze 讀 turns 的方式。

## 10. 驗收對應與 5b 邊界

對應 Part 5 驗收標準:

- 「kill cache 後 session 可完整從 DB 恢復」← §4 重建 + §5 read-through(不變量,測試:清 cache → detail/reply 均正常且 history 完整)。
- 「reply 與 analyze 並發不遺失 history」← §6 欄位所有權互斥(測試:analyze 進行中模擬 reply 寫入,兩者結果都在)。
- 「`session_state` 不再存 history 全文」← §3 + §8 寫路徑自然清除(測試:reply 後重讀 DB record,`session_state` 無 `history` key)。
- 另補:重連恢復(WS helper 統一)、reply 後 history 正確(payload 與 turns 重建一致)、舊資料 fallback(turns 空 + 舊 history 非空可讀)、併發 reply×reply 的 metadata 合併(兩次交錯寫回後 `focus_signal_count` 不遺失、`user_reasoning_mode` 不回退,§6 合併規則)。

**5b 要做**:改 `api/services/dialogue_session.py`(§3–§6 的 helper 拆分)、
`api/consumers.py`(共用 read-through、invalidate)、`api/views.py` 4 個呼叫點、
上列測試、HANDOFF_P5。

**5b 不做**:`semantic_tree.py` 內部(P1 範圍)、`DialogueSession` dataclass 與其
`to_dict`/`from_dict`(in-memory 形狀不變)、cache backend 更換、
任何 data migration(§8 已否決)、對外 API payload 形狀變更。
