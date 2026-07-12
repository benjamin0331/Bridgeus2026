# BridgeUs Backend Refactor — 結案報告(P7)

- 日期:2026-07-12(第一輪整合 P1~P5;同日第二輪補整合 P6,即本版)
- 執行:Claude Fable 5 @ Claude Code(P7 最終整合 Review)
- 整合分支:`refactor/p7-integration`(自 tag `refactor-baseline` 依序 merge P1 → P2 → P3 → P5(內含 P4)→ P6)
- Review 範圍:`git diff refactor-baseline...refactor/p7-integration`(42 檔,+3792 / -1035)

---

## 0. 重要:實際 repo 狀態與計畫的落差

P7 第一輪啟動時的前提「P1~P6 已併回 feat/Light」**與事實不符**:

1. `feat/Light` 仍停在 baseline(tag `refactor-baseline`),各 Part 都留在自己的子分支上**未併回**。
2. 各 Part 分支都直接從 baseline 切出(而非計畫要求的「從已含前面 Part 的 feat/Light 切出」),
   所以 P3 與 P4/P5 對 `views.py` 的改動互相衝突,P1 與 P5 對 analyze view 的改動也互相衝突。
3. P7 第一輪時 **P6(清理包)尚未執行**;第一輪後 P6 已在 `refactor/core-p6-cleanup`
   補做完成(4 commits + HANDOFF_P6),但同樣是**從 baseline 切出**,沒看到 P1~P5 的
   service 結構——P6 對 `views.py`/`consumers.py` 收斂的重複碼,實際位置已被 P4/P5 搬進
   `api/services/*` 與 `message_pipeline.py`。

P7 的處置:建立 `refactor/p7-integration`,依依賴順序 merge 全部 Part 並解衝突
(解法記錄在各 merge commit message 與本文第 2 節);P6 併入時把它的語意變更
重新落在 P4 之後的 service 位置(見 §2 第 5 點)。
**把 `refactor/p7-integration` 併回 `feat/Light` 屬計畫中人工把關的步驟,留給人類執行**
(計畫 §0.5 第 4 步:review 通過才併回)。

---

## 1. 測試與冒煙結果(P6 併入後全部重跑)

| 指令 | 結果 |
|---|---|
| `DB_ENGINE=sqlite uv run python manage.py test api --keepdb --noinput` | 78 tests OK(含 P6 新增的 topic-config 錯誤測試) |
| `DB_ENGINE=sqlite uv run pytest api/tests_websocket.py` | 13 passed(第一輪修 1 個 P3×P5 接縫,見 §2) |
| `DB_ENGINE=sqlite uv run pytest apps/matching -q` | 58 passed(含 P1 的 8 個鎖測試) |
| `DB_ENGINE=sqlite uv run pytest apps/matching/tests_semantic_tree_{prompt,timeline,node_birth,node_creation_metadata}.py` | 28 passed(P6 改動的測試檔) |
| `DB_ENGINE=sqlite uv run pytest chat/tests_embedding.py chat/tests_warm_nlp_models.py` | 13 passed |
| `uv run pytest`(baseline 預設指令) | 58 passed |
| `manage.py check` | 無 issue |
| ruff(F/E9)掃合併後全部改動檔 | 無 finding(僅 1 個 baseline 既有 unused-variable,見 §2 第 6 點) |
| 冒煙(暫時性 e2e 測試,跑完即刪):建 session → reply(mock LLM)→ semantic-tree analyze(mock 分類器)→ history 列表 → history detail | 全通過;history `message_count=2`、analyze `analysisStatus=ready`、detail 2 則訊息 |

---

## 2. P7 整合時發現並當場修掉的問題

1. **P1×P5 接縫(雙寫)**:P5 在 analyze view 留了 `update_semantic_tree_state()` stand-in
   (HANDOFF_P5 已標 NEEDS_DECISION「P1 落地後收斂」)。P1 落地後 `analyze_pending_ai_conversations`
   已在自己的鎖交易內寫回 + 刪 cache,view 端再寫一次會用記憶體舊值蓋掉服務層驗證過的寫回。
   → 解衝突時採 P1 語意(view 不寫回),並**刪除** `update_semantic_tree_state`(已無任何呼叫者)。
2. **P3×P5 接縫(views.py 結構)**:P3 的 history N+1 修復寫在 views.py 內,P5(P4)把 history helper
   搬進 `api/services/history.py` 但搬的是修復前版本。→ 把 P3 的 annotation 版
   `_history_ai_summary` / `_history_match_summary` 與兩個 summary queryset builder 移植進
   `api/services/history.py`,views.py 只保留 import。
3. **P3×P5 接縫(測試)**:P3 新增的 `test_dialogue_websocket_ignores_non_object_payload_without_crashing`
   patch 的是 `api.views.get_dialogue_agent`(P4 之後 consumer 改從 `api.services.dialogue_session` import),
   且只 seed cache 沒建 DB row(P5 寫入路徑需要真實 row)。→ 修 patch 目標 + 補 DB row。
4. **死 import**:views.py 的 `MatchMessageSerializer`(CODE_REVIEW 主題 7 項目)與未再使用的
   `django.db.models.Q` import 一併移除。
5. **P6×(P1/P2/P4/P5)接縫(重複碼收斂落點)**:P6 從 baseline 切出,它改的
   `views.py`/`consumers.py` helper 已被 P4/P5 搬走。併入時採「P1~P5 結構為準、
   P6 語意重新套用」:
   - `api/services/dialogue_session.py` 改用 `core.env` 的 `SESSION_TTL_SECONDS` 與
     `dialogue_session_cache_key`(刪本地副本);
   - `api/services/history.py` 的 `_semantic_tree_root_name_for_topic_id` 改走
     `get_topic_title`(查不到 raise,不再 fallback `"核電"`);
   - `api/services/room_state.py` 與 `message_pipeline.py` 的匿名顯示名改用
     `core.env` 常數(「匿名對話者」/「匿名使用者」單一定義);
   - `semantic_tree.py` 保留 P1 鎖結構,anchors/descriptions 改 import 自
     `api.dialogue_topics`(缺 config 即 raise),並刪掉 P1 加的本地
     `_dialogue_session_cache_key` 副本改用 `core.env`(第一輪標記的主題 4 殘留);
   - consumers/views 丟棄 P6 的 baseline 形狀 cache 直取(P5 read-through 已取代),
     並移除因此變死的 `core.env` import 與 `Issue` import。
6. **死 import(P6 精神順手修)**:`api/serializers.py` 的 `DiscomfortReport`
   baseline 起即未使用,移除。ruff 另報 `ccnd_snapshot_analysis.py:284` 一個
   baseline 既有的 unused variable(`segment`),在演算法迴圈中、不影響行為,留給維護者。

以上均已 commit 在 `refactor/p7-integration`。

---

## 3. 對照 CODE_REVIEW.md 逐項核銷

### 二、正確性/效能問題

| 項目 | 狀態 | 說明 |
|---|---|---|
| P1 鎖內呼叫 OpenAI(room analyze) | **已修**(Part 1) | claim(150s TTL)+ 兩段交易,LLM 全在鎖外;衝突丟棄回 `conflict_retry`。P7 逐行 review 通過:claim 過期重搶會收斂為單邊寫入 + 另一邊 conflict 丟棄,無重複 append |
| P1 History N+1 | **已修**(Part 3,P7 移植進 service) | 常數查詢(P3 測試以 assertNumQueries 驗證);annotation 缺失時有逐筆 fallback |
| P1 REST 發訊息繞過安全管線 | **已修**(Part 2) | REST/WS 共用 `finalize_match_message`;REST 被攔回 400 + 同一份 `BLOCKED_MESSAGE`;決策:REST 不做建議重寫互動(無 socket) |
| P2 配對 enqueue 並發 deadlock | **未修 → NEEDS_DECISION** | ⚠️ 計畫從未把此項分配給任何 Part。`enqueue_for_matching` 仍是「先鎖自己 entry、再 select_for_update 掃候選人」,兩人互相 enqueue 仍可能 deadlock + 未捕捉的 500。實驗上線(30+30 人同時配對)前建議修:統一按 entry id 排序加鎖,或捕 `OperationalError` retry 一次 |
| P2 AI session 分析無鎖 + last-write-wins | **已修**(Part 1 + Part 5) | analyze 走 P1 鎖交易;reply/WS 走 P5 `update_session_metadata`(鎖內重讀合併,欄位所有權分離);history 唯一權威 = AIConversation turns |
| P2 Guest / LLM endpoint 無限流 | **已修**(Part 3) | 5 個 throttle class,env 可調(預設 guest 10/hour、reply 30/min、analyze 6/min) |
| P3 WS payload 形狀假設 | **已修**(Part 3) | 兩個 consumer 都有 `isinstance(data, dict)` + content str 檢查,測試覆蓋 `"[1]"` |
| P3 Serializer `__all__` | **已修**(Part 3) | 白名單欄位、embedding 不輸出、detail 改 RetrieveAPIView(PUT/DELETE 回 405);前端 grep 無使用 |
| P3 DialogueAgent 同步初始化阻塞 event loop | **未修(延後)** | 計畫未分配。`consumers.py` `_stream_response` 仍同步呼叫 `get_dialogue_agent`(首次載模型秒級阻塞 event loop)。緩解:`warm_nlp_models` 預熱已存在;正式修法 = 包 `sync_to_async` |
| P3 `thread_sensitive=False` 包 ORM,連線洩漏 | **未修(延後)** | 計畫未分配。`hh_analysis.py:239-244`、`hh_ai.py:119-121` 原樣;其中含 ORM 的函式應改 `database_sync_to_async` |
| 小項:WS query string 帶 JWT | 未修(知情接受) | CODE_REVIEW 原文即標註「WS 常見做法,但要知情」 |
| 小項:history 排序 fallback naive datetime | 未修(延後) | `views.py` 排序 key 的 `timezone.datetime.min` 地雷仍在;目前 last_activity_at 均非空踩不到 |
| 小項:`get_matching_state` GET 有寫入副作用 | 未修(延後) | 原樣 |
| 小項:`astream_respond` 歷史塞 system prompt | 未修(延後) | 原樣 |

### 三、Refactor 主題

| 主題 | 狀態 | 說明 |
|---|---|---|
| 1. 模組邊界(views helpers → services;consumers 不再 import views) | **已修(第 1、2 步)**(Part 4) | `api/services/{dialogue_session,stance_scoring,history,room_state}.py`;`rg "from api.views import" consumers.py` 無結果。**model 搬遷(第 3 步)延後**(計畫本來就排最後) |
| 2. AI session 狀態單一化 | **已修**(Part 5) | turns 為權威、session_state 只留 metadata、cache 純 read-through、寫入者一律 invalidate;kill-cache 恢復 / reply×analyze / reply×reply 均有測試 |
| 3. topic 102 預設值拔出共用碼 | **已修**(Part 6,P7 落點調整) | `FIXED_ANCHORS`/`ANCHOR_DESCRIPTIONS` 已刪;anchors/title/descriptions 一律 `api.dialogue_topics.get_topic_*`,缺 config raise `ValueError`(有測試);root name fallback `"核電"` 已從 `api/services/history.py` 移除 |
| 4. 重複碼收斂 | **已修**(Part 6 + P7 收斂) | `_env_bool` 單一定義於 `core/env.py`(settings/matching_algorithm/hh_ai 共用);session cache key 單一定義(`core.env.dialogue_session_cache_key`,dialogue_session 與 semantic_tree 都改 import);匿名顯示名兩個常數集中 `core/env.py`。向量清理(`_clean_vector`≈`_clean_embedding`)未收斂——計畫未分配,兩者語意略異 |
| 5. LLM / embedding 技術棧收斂 | **未修(延後)** | 計畫未分配任何 Part |
| 6. `match.stats` JSON 無上限成長 | **未修(延後)** | P1 的 `pendingClaims` 也放進同一 JSON,但有 TTL + 寫回即清,增量有界 |
| 7. 死碼清理 | **已修**(Part 6 + P7) | `main.py` 已刪;`Issue` model + view + URL 已刪(migration 0012);`from_turn_count` 註解已改為描述現實(production 判定);`MatchMessageSerializer`/`DiscomfortReport`/`User` 死 import 已移除 |
| 8. 其他(文件對齊、sqlite+pgvector README、tests 拆分、分頁) | **部分修**(Part 6) | CLAUDE.md 版本對齊(Django 6.0.4 / Python 3.12+);README 已寫入 SQLite+pgvector 限制。**未修**:`api/tests.py` 拆分(現 2200+ 行)、全站分頁——計畫未分配 |

### 前面 Part 留下的 NEEDS_DECISION 處置

| 來源 | 項目 | P7 處置 |
|---|---|---|
| HANDOFF_P5 | `update_semantic_tree_state` stand-in 去留 | **已解決**:P1 落地,stand-in 已刪除 |
| HANDOFF_P5 | 量測生產 DB 中「session_state.history 非空且無對應 turns」的舊資料量 | **仍開放**:需生產 DB 權限,上線前人工跑一次 |
| HANDOFF_P2 | `chat/tests*.py` 因 `chat.models` 不再 export 舊 model 而 collection 失敗 | **仍開放**:刪除這批舊測試或還原 model,需 owner 決定 |
| HANDOFF_P2 | pytest 預設不收集 `tests.py`/`tests_websocket.py`(python_files 設定) | **仍開放**:加 `python_files` 設定會連帶收集壞掉的 `chat/tests*.py`,需先解上一項才能改 |

---

## 4. 遺留事項優先序(給下一棒)

1. **(高)配對 enqueue deadlock**——唯一未修的多人上線即可能觸發的正確性問題。
2. **(高)人工把 `refactor/p7-integration` review 後併回 `feat/Light`**,再走 feat/Light → dev PR。
3. **(中)chat/tests*.py 處置 + pytest python_files 設定**(兩項綁定)。
4. **(中)行為變更知會**:P6 把「查不到 topic config」從默默長出核能樹改成 raise
   `ValueError`(HTTP 500)。現有資料 topic_id 都在 config 內,但若未來刪 config 條目
   或有 legacy record topic_id 為空,history/analyze 端點會 500——上線前可考慮在
   view 層把 `ValueError` 轉 4xx。
5. **(低)DialogueAgent async 包裝、thread_sensitive ORM、LLM 棧收斂、model 搬遷、
   api/tests.py 拆分、全站分頁。**

## 5. 驗收判定

- 整合後 review 發現的 blocker:**0**(第一輪 4 個接縫問題 + 第二輪 P6 落點調整
  均已當場修復並有測試)。
- P1~P6 全部執行完畢並整合;全測試綠(見 §1),冒煙通過。
- 未修項目均為計畫本來就沒分配的項目——已如實列於 §3 與 §4,不影響驗收。
