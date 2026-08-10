# CONTEXT.md — BridgeUs 開發狀態

> 最後更新：2026-08-02
> 用途：每次對話開始先讀此檔。**「待提交變更」區塊** = 尚未 commit 的工作，下次 commit 直接依此即可；commit 完就把該項移除。

---

## 🟡 待提交變更（Uncommitted）

> 每完成一項未 commit 的工作就記在這；commit 後刪掉該行。

（目前無）


_（未追蹤的資料/設定檔 `.claude/`、`chroma_data/`、`*.csv`、`0530…txt` 不納入 commit。）_

---

## 專案目標

BridgeUs（橋得攏）— AI 驅動的去極化對話平台。
核心功能：異質觀點配對、AI Agent 對話（RAG + LLM）、CCND 概念認知網路圖視覺化。
對話模式：H-H（真人對真人）、H-AI（真人對 AI Agent）。

---

## 技術棧

| 層級 | 技術 |
|------|------|
| 後端 | Django 6 + DRF + Django Channels（ASGI/WebSocket） |
| 前端 | React + Vite |
| 資料庫 | SQLite（dev）/ PostgreSQL（prod）、Django Cache（LocMem dev / Redis prod） |
| NLP | Sentence-Transformers（本地 embedding）、LangChain + ChromaDB（RAG） |
| LLM | Claude Sonnet（Anthropic SDK，主要）/ Gemini / OpenAI（透過 llm_provider 抽象） |
| Package | uv（Python 3.13.5） |
| ASGI | uvicorn |

**環境變數（.env）：** `LLM_PROVIDER`(claude/gemini/openai)、`ANTHROPIC_API_KEY`/`GOOGLE_API_KEY`、`DB_ENGINE`(sqlite/postgres)、`USE_REDIS_CACHE`、`USE_REDIS_CHANNEL`(H-H 多 worker 必開)、`REDIS_URL`、`CLAUDE_CHAT_MODEL`(預設 `claude-sonnet-4-6`)。

**ChromaDB 知識庫：** Collection `nuclear_energy_all`（1449 chunks：新聞 184 + 法律 230 + PTT 40）。

**NLP 模型：**
- Embedding：`paraphrase-multilingual-MiniLM-L12-v2`（384 維，中英；VectorField 已對齊 384）
- 情緒：`lxyuan/distilbert-base-multilingual-cased-sentiments-student`，取 negative-class 機率為強度分數（0–1），閾值 `EMOTION_THRESHOLD=0.7`（待真實數據調）。已知：topic-sensitive，含「事故」等詞的事實陳述分數偏高。
- 攻擊性過濾：關鍵字黑名單（35 詞 frozenset）。Stage 2 分類器 `thu-coai/roberta-base-cold` 暫不引入（VPS 記憶體）。

---

## 模組現況（高層）

| 模組 | 狀態 | 重點 |
|------|------|------|
| M1 認證 | ✅ | JWT（simplejwt）、登入/刷新、前端 LoginPage；Supervisor 帳號管理（「研究者」Group 具 Django Admin 帳號管理權限；加入 Group 自動連動 is_staff；帳號列表顯示 last_login／is_active；刪除刻意不開放，改用停用 is_active）；前端設定頁（齒輪 → `/settings`）研究者帳號管理面板（清單／新增／停用啟用／升降研究者／重設密碼；`/api/accounts/*` 由 IsResearcher 把關；護欄：不能動 superuser、不能停用/取消自己） |
| M2 議題/立場 | ✅ | `api/dialogue_topics.py` 的 `TOPIC_CONFIGS`/`SURVEY_CONFIGS`；李克特 + 反向題 → stance score → support/neutral/oppose |
| M3 配對 + AI Agent | ✅（持續調整） | `apps/matching/services/matcher.py`（立場向量配對）、`ai_agent.py`（RAG+Claude、三階段策略、streaming）；近期加了 focus signal 偵測、reasoning mode 升級 |
| M4 對話室 | ✅ | `api/consumers.py`：H-AI streaming + H-H 配對房 WebSocket；離題/情緒/僵局介入 |
| M5 NLP/CCND | 🟡 進行中 | 語意樹 `apps/matching/services/semantic_tree.py`（topic-aware anchors）、立場偏移 drift；D3 前端指標列。CCND 視覺化持續中 |
| M6 摘要/知識庫 | 🟡 | ✅ 對話後問卷（`PostDialogueResponse`）+ debriefing 同意/撤回 + Part F 平台體驗回饋（`PlatformFeedback`）+ 問卷版立場偏移（`s_pre`/`delta_s`/`stance_centrism` 存檔＋前端結果卡片）；⬜ 語意向量版偏移報告、知識庫沉澱 |

---

## H-H / 對話架構（重要：已重構，勿被舊碼誤導）

**現行實作**（live）：
- WebSocket：`api/consumers.py`（H-AI 逐字串流 + H-H 配對房 relay/介入）、`api/routing.py`
- 分析服務：`apps/matching/services/`
  - `hh_analysis.py` — 立場偏移、離題、僵局、關鍵詞（配對房 & AI session）
  - `hh_ai.py`、`semantic.py`、`semantic_tree.py`（CCND 語意樹，topic-aware）、`matcher.py`、`ai_agent.py`

**已淘汰**（舊 `chat` app）：
- `chat/consumers.py`、`chat/routing.py` **已刪除**；`chat` 仍掛在 INSTALLED_APPS（DB 表還在）但無 WebSocket 入口。
- `chat/services` 仍被外部使用（live）：`embedding`、`emotion`、`filter`（由 `api/consumers.py`、`api/views.py`、`apps/matching/*` import）。
- `chat/services` 已成 dead code（僅 chat 內部/自身測試引用）：`drift`、`session`、`stalemate`、`topic`、`ai_assist`。功能已被 `apps/matching/*` 重寫取代。
- ⚠️ 清理 `chat` 死碼是待辦，尚未動（涉及 migrations/DB 表/INSTALLED_APPS，需另開一次）。

---

## 立場偏移度（drift）計算現況

概念：`drift_value = cosine_distance(該用戶當前區間發言的平均 embedding, 初始立場向量)`；越大＝離初始越遠（假設為趨近對方）。

- 初始立場向量 = 問卷開放式 Q9 回答的 embedding。
- 現行函式（`apps/matching/services/hh_analysis.py`）：
  - `calculate_match_stance_drift`（H-H 配對房，用 `MatchMessage` + `UserStanceProfile.q9_embedding`）
  - `calculate_ai_session_stance_drift`（H-AI，用 `survey_context["q9_embedding"]` + `AIConversation`）
- 取樣區間：**該用戶在這場對話的全部實質發言**（累積平均，非增量窗口）。
  H-H 的增量窗口（只算上一筆 drift 之後的新訊息）已在 2026-08-02 併入 feat/Light 時移除，
  與 H-AI 一致。`MatchStanceDrift` 上一筆仍用來判方向 `DIRECTION_THRESHOLD=0.02`
  （approaching/diverging/stable，前端不顯示）。
- **觸發時機（2026-07-05 起 H-H 與 H-AI 一致）**：每則「發言者本人」的新發言就重算其自己的 drift。
  - H-AI：`_update_session_stance_drift`（每輪 agent 回應後）。
  - H-H：`api/consumers.py::_run_stance_drift`（`_run_message_analysis` 內，embedding 存檔後），算完以 WS `match_stance_drift` **只推給發言者**。舊的 200 字/300 秒節流（`_maybe_run_periodic_analysis`）已移除。
  - 僵局偵測（stalemate）**未跟著改**：獨立時間節流 `_STALEMATE_MIN_INTERVAL_SECONDS=300`（`_match_last_stalemate`，single-process）。
- 舊版 `chat/services/drift.py::calculate_drift` 為 dead code（見上）。
- 前端 UI 標籤已更名為「論述移動」（數值/欄位不變）。
- 另有**問卷版**去極化指標：`PostDialogueResponse`（`s_pre`/`delta_s_value`/`stance_centrism_value` 三欄），與語意向量版獨立。
  - `s_pre` 來源是**該場對話自己的前測快照**（`_post_dialogue_stance_snapshot`）：
    H-AI 取 `DialogueSessionRecord.session_state["user_stance_score"]`，
    H-H 取 `DialogueMatch.user_a_score`/`user_b_score`。找不到對應對話 → 400。
    之後重填問卷產生的新 `UserStanceProfile` 不會回頭改寫這場對話的 `s_pre`。
    經 `fill_stance_metrics()` 算出並存檔；無前測值時三欄為 NULL。
  - `delta_s = s_post − s_pre`（正=偏支持、負=偏反對）；`stance_centrism = |s_post−4|−|s_pre−4|`（< 0 去極化）。output serializer 以 `s_pre`/`s_post`/`delta_s`/`stance_centrism` 回傳。
  - 前端：問卷送出後由 `SettlementReceipt.jsx` 呈現「結算單」（前後立場分數 + 兩項指標白話解讀 + 投入度評級，可匯出 PNG），再進 debriefing。舊的 `ResultCard` 已移除。後台 `PostDialogueResponseAdmin` 可檢視。

---

## Input gate（輸入閘門 / token 消耗控制）

LLM 呼叫**之前**的純規則過濾。命中時回靜態字串，零 API 成本。H-H 與 H-AI 共用。

- 模組：`apps/matching/services/input_gate.py`（純規則，無 I/O、無模型推論）、
  `rate_limit.py`（Django cache → prod Redis）、`input_gate_store.py`（計數落庫）。
- 判定順序（不可調換）：**0 單字粗口** → 1 短回應白名單 → 2 純數字/符號 →
  3 字元重複度 <0.3 → 4 語意字元佔比 <0.4 → 5 `len<4` 且前一輪 AI 沒提問。
  **白名單豁免 2/3/4，但規則 5 仍適用**（「好」在 AI 提問後放行，無脈絡時攔截）。
  所有門檻是工程性防禦值，不是實驗參數。
- **規則 0（單字粗口）不看 `prev_ai_is_question`**：AI 剛提問會讓「好」變成合法輪次，
  但不會讓「幹」變成回答。字彙在 `chat/services/_blacklist.py::STANDALONE_PROFANITY`
  （幹/操/靠/屌），比對在 `filter.py::find_standalone_profanity()`：剝除標點空白後，
  整串只由這些字組成才命中 →「幹」「幹幹幹」「幹！！！」「幹 幹 幹」全擋，
  「幹嘛」「樹幹」「幹部」「操作」「幹，核電根本是騙局」不受影響。
  這些字**不可**放進 `BLACKLIST`（子字串比對會誤殺上述複合詞）。
  重複的「幹×n」靠遞進節流累加，第 6 則進冷卻。回覆走專屬的
  `FALLBACK_PROFANITY_ONLY`（承接情緒導回議題），不是「可以再多說一點嗎」。
- `prev_ai_is_question` 來自 `AIConversation.ai_turn_is_question`，回應落庫時由策略層寫入：
  讀 `<judgment>` 的型別代號（C=視角翻轉型→True、E=承接深化型→False），
  A/B/D 退回句尾問號判斷（TODO：prompt 第十節短碼補欄位）。
- 攔截的訊息**不進** session_state.history / AIConversation / RAG / embedding /
  CCND / 對話輪數，只更新計數欄位。四個入口都擋：H-AI WS、H-AI REST reply、
  H-H WS（含 modify_suggestion 改寫框）、H-H REST messages。
- 遞進節流：1–2 對話氣泡、3–5 系統提示列、≥6 進 60 秒冷卻（WS `input_cooldown`）。
  冷卻結束不歸零，需一則有效發言重置。
- Rate limit（獨立於內容判斷）：最小間隔 1.5s、每分鐘 20 則，per-user，兩種對話室同時生效。
- 實驗欄位：`DialogueSessionRecord.{invalid_input_count, invalid_input_total,
  input_attempt_total, invalid_ratio, substantive_turn_count}`；H-H 為 `MatchInputGateStat`
  （per match×user，同名欄位）。`invalid_ratio` / `substantive_turn_count` 在後測問卷送出時計算。
  **系統不自動排除樣本**，只產出欄位。
- NLP 管線：離題偵測、論述移動度、僵局偵測三處一律排除短回應，
  共用 `input_gate.is_substantive_message()`。離題偵測已搬到
  `apps/matching/services/topic_relevance.py`（per-topic policy：anchor/threshold/
  window_size/min_messages 讀 `TOPIC_CONFIGS[...]["off_topic_detection"]`），
  短回應過濾同樣在那裡做。
- 前端：相同 fallback 就地累加 `×N` 不新增氣泡；`input_blocked` / `input_cooldown` /
  `rate_limited` 三種事件；冷卻時停用輸入框並倒數。

```bash
uv run pytest apps/matching/tests/test_input_gate.py -q   # 規則單元測試（84 項）
uv run pytest api/tests_input_gate_ws.py -q               # Consumer/REST 整合（17 項）
uv run pytest chat/tests_filter.py -q                     # 黑名單 + 單字粗口（38 項）
```

---

## 訊息讚/倒讚（MessageReaction）

參與者可對「對方發言」按讚/倒讚，寫入 DB 供研究分析。H-H 與 H-AI 皆支援。

- 模型：`api/models.py::MessageReaction`（`user` + `target_type`(ai/match) + `target_id` + `value`(±1) + 去正規化 `topic_id`/`conversation_id`）。`(user, target_type, target_id)` 唯一 → 重按同一個=切換/取消，按另一個=改值。
- target：`ai` → `AIConversation.id`（AI 回覆那筆 turn）；`match` → `MatchMessage.id`（對方發言）。
- 端點：`GET/POST /api/message-reactions/`（`MessageReactionView`）。POST body `{target_type, target_id, value}`，`value=0` 刪除；只允許對「對方」發言反應（AI turn 需屬於本人 session 且有 ai_response；match 訊息 sender 不可為自己且需為房間成員）。GET `?target_type=&conversation_id=` 回傳本人反應清單供前端初始高亮。
- **AI turn id 串接**：`_live_history_with_turn_ids()` 讓 latest/detail/reply 回傳的 `history` 每則帶 `turn_id`；WS `agent_stream_end` 也帶 `turn_id`（`consumers.py`）。前端 `TopicChat.jsx` 的 `mapHistoryToMessages`/`mapMatchMessagesToDisplay` 產生 `reactTarget`，`MessageReactions` 元件渲染 👍/👎（樂觀更新、失敗回滾）。
- 後台 `MessageReactionAdmin` 可檢視。migration `0014_messagereaction`。

## 測試

```bash
cd backend
uv run pytest api/tests.py -v                       # api app（含 matching、post-questionnaire、Part F）
uv run pytest api/tests.py::PlatformFeedbackApiTests -v   # Part F（6 項）
uv run pytest api/tests_message_reactions.py -v           # 讚/倒讚（11 項）
uv run pytest api/tests_input_gate_ws.py -v               # Input gate 整合（14 項）
# ⚠️ 專案根執行 `uv run pytest` 只會收到 apps/matching/tests/（pytest 預設
#    python_files 是 test_*.py，api/ 底下的 tests_*.py 必須指名檔案才會跑）。
#    完整套件：
uv run pytest apps api/tests.py api/tests_websocket.py api/tests_live_contract.py \
  api/tests_message_reactions.py api/tests_post_questionnaire.py \
  api/tests_ccnd_timeline_gate.py api/tests_input_gate_ws.py chat/tests_filter.py -q   # 525 passed
# chat/ 底下的 tests_* 多對應已淘汰服務，屬 legacy
```

---

## 啟動方式（開發）

```bash
# 後端
cd backend
uvicorn BridgeUs_Django.asgi:application --host 0.0.0.0 --port 8000 --reload
# 前端
cd frontend
npm run dev      # Vite，port 5173
```

---

## 待辦

**近期**
- [ ] ⚠️ `api/tests.py` 有 50 項失敗（合併前就存在於 feat/Light）：混合入口 gate
      `_entry_gate_response` 上線後，測試沒建 `DialogueEntryAssignment`，
      `/api/matching/join/` 與 `/api/dialogue/sessions/` 一律 403。
      需補 fixture（或在測試把 entry mode 設成 split）。
- [ ] 問卷初始立場 embedding（Q9）確實填入配對/ session 流程（M2/M3 整合）— 影響 drift 是否有基準
- [ ] CCND 前端視覺化 / WebSocket 推送收尾
- [ ] M6：立場偏移量化報告、觀點知識庫沉澱
- [ ] 清理 `chat` app dead code（drift/session/stalemate/topic/ai_assist + 對應 tests）
- [ ] 刪 `godot/Assets/GreenBlue/`（`Assets/ToxicFrog/GreenBlue/` 的整份重複、無人引用；併 feat/Ceeeuu 時帶進來的遺留）
- [ ] `LevelSummaryCard`（成就頁等級卡）在 `/api/titles/me/` 取不到時會顯示「示意資料 · 後端尚未串接」；確認正式環境是否還會走到這個 fallback，見 `docs/0804.md` §2.1
- [ ] `docs/BridgeUs_API_Spec.md` 更新（新增 `platform-feedback`、`post-questionnaire` 等）

**基礎設施**
- [ ] Docker Compose（PostgreSQL + Redis）
- [ ] PostgreSQL 切換（`CREATE EXTENSION vector`）
- [ ] 多議題知識庫（目前僅核能；兵役語料已收集未建 collection）
- [ ] CI/CD（GitHub Actions）
