# CONTEXT.md — BridgeUs 開發狀態

> 最後更新：2026-07-11
> 用途：每次對話開始先讀此檔。**「待提交變更」區塊** = 尚未 commit 的工作，下次 commit 直接依此即可；commit 完就把該項移除。

---

## 🟡 待提交變更（Uncommitted）

> 每完成一項未 commit 的工作就記在這；commit 後刪掉該行。

- 新增 `apps/summary/pipeline/`（`quality_filter.py`、`dedup.py`、`write.py`）+ `apps/summary/tests_pipeline.py`：把 `feat/polarbear` 分支 `觀點知識庫_對接溝通說明.md` 提到的觀點知識庫腳本（品質篩選、去重、寫入）改寫成 Django ORM 併入 `apps/summary`，改用現有 `chat.services.embedding.get_embedding`/`cosine_similarity`，不再各自開 `psycopg` 連線、不再重複一份 embedding 包裝。尚未在有 `uv`/PostgreSQL 的環境跑過 `manage.py check` 與 `pytest apps/summary/tests_pipeline.py`，commit 前請先跑過。
  - 未併入：`ccnd_semantic_dist`／`ccnd_stance_shift` 兩個逐則欄位已在下面追加中各自接上真實資料來源。`dimension` 分類體系來源已在下面兩筆追加中解決。
- 2026-07-15 追加規則（同一批未 commit 變更內）：`write.py` 新增 `_validate_topic_id()`，`topic_id` 只接受 `api.dialogue_topics.TOPIC_CONFIGS` 已定義的議題（目前 102、103），其餘一律 `ValueError`。另外 `write_viewpoint()` 的 `topic_id` 改成**繼承自 `summary_id` 對應的 `DialogueSummary.topic_id`**（不再由呼叫端另外傳），確保同一場對話產出的觀點跟原始聊天室永遠同一個議題——`topic_id` 的源頭規則是「跟聊天室（H-H 用 `DialogueMatch.topic_id`／H-AI 用 `AIConversation.topic_id`）一致」，`write_dialogue_summary()` 的呼叫端要負責從那邊帶正確的值進來。查過 `bridgeus_test` 資料庫：真實聊天室資料（`api_dialoguematch`、`api_aiconversation`）目前全部乾淨，topic_id 只有 102/103，沒有 NULL 或雜值。
- 2026-07-15 追加（`dimension` 分類體系來源，同一批未 commit 變更內）：比照 `_validate_topic_id()` 同一套防呆邏輯，`write.py` 新增 `_validate_dimension(topic_id, dimension)`，用 `apps/matching/services/semantic_tree.py::get_topic_anchors(topic_id)` 當來源（M5 CCND 既有的 topic-aware anchor 分類，不是 M2 問卷題目的 `dimension` 標籤）。`ViewpointNode.dimension` 現在規定只能存該議題底下的 anchor id（例如 `anchor_safety`），不能是裸字串（舊測試資料的 `"safety"`），也不能是別的議題的 anchor（例如把 103 議題的 `anchor_equality` 用在 102 議題）。`tests_pipeline.py` 對應更新：fixture 改用 `anchor_safety`，新增 `DimensionValidationTests` 驗證未知/跨議題 anchor 會被拒絕。
- 2026-07-15 追加（攻擊性詞典改共用，同一批未 commit 變更內）：`quality_filter.py` 的 `_count_attack_hits()` 原本自帶一份只有 7 詞的 `ATTACK_WORDS`（比 M4 對話室即時攔截寬鬆很多），改成直接 import `chat.services._blacklist.BLACKLIST`（31 詞，`chat/services/filter.py` 也在用同一份），M6 品質篩選跟 M4 正式對話室的攻擊性判定標準統一。
- 2026-07-15 追加（`ccnd_semantic_dist`／`ccnd_stance_shift` 接上真實來源，同一批未 commit 變更內；**這筆取代了同一天稍早「`ccnd_stance_shift` 接上 hh_analysis drift」的決定，欄位對應關係後來對調了**）：
  - `ccnd_semantic_dist`（論述移動 / drift）：`apps/matching/services/hh_analysis.py` 新增唯讀函式 `get_message_drift_value(match_id, user_id, as_of)`（原名 `get_message_stance_shift`，因為這次改對應到 `ccnd_semantic_dist` 而不是 `ccnd_stance_shift`，改成語意中立的名字，避免函式名字跟它餵的欄位對不上）——查詢 `api/consumers.py` 每則「發言者本人」新發言後即時寫入的 `MatchStanceDrift` 記錄，回傳 `as_of` 當下最新一筆已存在的 `drift_value`，不重算、不新增記錄。測試檔同步從 `tests_hh_analysis_stance_shift.py` 改名為 `apps/matching/tests_hh_analysis_drift_value.py`（4 案例：無記錄回 0、取最新、忽略 as_of 之後的記錄、依 user 隔離）。
  - `ccnd_stance_shift`（立場偏移量，改用「CCND 點亮節點數」衡量，誰的亮點多選誰）：`apps/matching/services/semantic_tree.py` 新增唯讀函式 `get_lit_node_count(match, owner_key, source_message_id, root_name)`——用既有的 `resolve_cutoff_for_message` + `reconstruct_tree_as_of` 還原該訊息當下的樹快照，再用 `ccnd_snapshot_analysis.flatten_tree` 攤平、以 `(owner_key, node_id)` 去重計數。呼叫端要自己套公式 `100 / 36 * get_lit_node_count(...)`（36 是假設的滿分點亮節點數，寫在 `quality_filter.py` docstring 裡，不是 `get_lit_node_count` 本身回傳的）。新增 `apps/matching/tests_semantic_tree_lit_node_count.py`（5 案例：累積計數、跨訊息累加、無分析回 0、未知訊息 id 回 0、同一節點重複命中不重複計數）。
  - `quality_filter.py` 模組 docstring 同步更新這兩個欄位的來源說明。
  - **注意**：兩個函式都還沒被任何「組資料」層呼叫——專案裡沒有任何程式碼會把 `DialogueMatch`/`MatchMessage` 組成 `run_pipeline()` 要的 `messages: list[dict]` 格式，這兩個函式目前是「資料來源已備妥，但沒人接線」的狀態。
- 2026-07-15 追加（組資料串接層，同一批未 commit 變更內；補上一筆備註提到的那條空缺）：新增 `apps/summary/pipeline/assemble.py::build_messages_for_match(match)`，把一場 `DialogueMatch` 底下所有 `MatchMessage` 依時間順序組成 `run_pipeline()` 要的格式，逐則呼叫 `get_message_drift_value`（填 `ccnd_semantic_dist`）與 `get_lit_node_count` 換算 `100/MAX_LIT_NODES`（填 `ccnd_stance_shift`，`MAX_LIT_NODES=36`）。**時間語意需注意**：`get_message_drift_value(as_of=message.created_at)` 抓的是「這則訊息當下已存在的最新 drift」，而 `MatchStanceDrift` 實際上是訊息存檔後才重算寫入（略晚於 `created_at`），所以拿到的通常是「上一則自己發言」的結果，不是這則訊息自己觸發的重算結果——這是特意記下的設計判斷，不是 bug，如果要改成「這則訊息觸發的重算結果」需要改成找 `measured_at >= created_at` 的第一筆。新增 `apps/summary/tests_assemble.py`（5 案例，含餵進 `run_pipeline()` 驗證格式相容不會噴錯）。
- 2026-07-15 追加（配對房結束自動觸發 M6 pipeline，同一批未 commit 變更內；補上上一筆備註「還沒接 write_dialogue_summary/write_viewpoint、沒人觸發」的缺口）：
  - `apps/matching/services/semantic_tree.py` 新增 `get_message_dimension(match, owner_key, source_message_id)`：攤平該參與者的樹，找出這則訊息命中的第一個 anchor id，當作 `ViewpointNode.dimension` 的來源；訊息沒有任何 CCND 命中就回傳 `None`。
  - `apps/summary/pipeline/assemble.py` 新增 `run_pipeline_for_match(match_id)`：`build_messages_for_match` → `run_pipeline` → 為整場對話寫一筆 `write_dialogue_summary`（只填 `dialogue_id=str(match.id)`、`topic_id=match.topic_id`，`side_a_stance`/`side_b_stance`/`summary_text`/`quality_score`/`stance_shift_magnitude` 都還沒有餵值來源，先留空，這幾個欄位本來就 nullable，不影響寫入但也還沒解決）→ 對每個 ranked pair 用 `get_message_dimension` 找 anchor，找不到就跳過不寫、找得到就呼叫 `write_viewpoint`。
  - `apps/matching/services/matcher.py::_close_locked_match()`（H-H 配對房狀態轉為 CLOSED 的唯一收斂點，`close_match()`/`close_match_if_idle()`/`close_match_if_participant_absent()` 三條關房路徑都會走到這裡）狀態存檔後用 `transaction.on_commit()` 註冊 `_trigger_m6_pipeline_for_closed_match(match_id)`，確保鎖已釋放、交易已提交才觸發；整個包在 `try/except Exception` 裡只記 log，pipeline 掛掉絕對不會讓配對房關不掉。
  - 新增 `apps/matching/tests_m6_trigger_on_close.py`（2 案例：關房確實寫出 `DialogueSummary`+`ViewpointNode`；用 `mock.patch` 讓 pipeline 噴例外時關房仍然成功、且沒有殘留寫入）。測試用 `self.captureOnCommitCallbacks(execute=True)` 讓 on_commit 在 `TestCase` 交易式測試裡也能真的跑到。
  - **已知取捨**：pipeline 現在是同步、在 HTTP 請求（leave 端點）或閒置檢查的呼叫堆疊裡直接跑完（`transaction.on_commit` 只是延到交易提交後，不是丟到背景），會讓那次請求變慢（embedding 模型呼叫、多次 DB 寫入）；如果之後需要非同步化（Celery/背景任務），`run_pipeline_for_match(match_id)` 這個入口本身已經是純粹吃 `match_id` 的函式，接 Celery task 不需要再改介面。
  - **仍未解決**：`side_a_stance`/`side_b_stance`/`summary_text`/`quality_score`/`stance_shift_magnitude` 這幾個 `DialogueSummary` 欄位還沒有真正的資料來源／公式，目前寫入時是空的。
- 2026-07-15 追加（M6 觀點知識庫人工審核介面，同一批未 commit 變更內；對應 `quality_filter.py` docstring 說的「Step 4 人工終審」，選的是「完整前端頁面 + API」而非 Django Admin）：
  - `apps/summary/models.py::ViewpointNode` 新增 `review_status`（`ReviewStatus` choices：pending/approved/rejected，預設 pending，有 index）、`reviewed_by`（FK User，null）、`reviewed_at`、`review_notes`。新增 migration `apps/summary/migrations/0002_viewpointnode_review_fields.py`——**這份 migration 是手寫的**（這台機器沒有能跑 `makemigrations` 的完整環境），`AddIndex` 的自動索引名稱 `summary_vie_review__f0f5e4_idx`是用既有索引命名規則猜的，還沒有實際跑過 `makemigrations --check` 驗證是否跟 Django 真正會產生的名稱一致，**套用前務必在有完整環境的機器上跑一次 `makemigrations --check` 確認**。
  - API 沿用整個專案「HTTP 一律在 `api` app」的既有慣例（`apps/matching`、`apps/summary` 都只有 service/model，沒有自己的 `views.py`/`urls.py`），沒有另外建 `apps/summary/views.py` 等檔案：
    - `api/serializers.py` 新增 `ViewpointNodeReviewSerializer`（唯讀列表/詳情，`dialogue_id` 從 `summary` 帶出來）、`ViewpointNodeReviewDecisionSerializer`（`action`: approve/reject + `notes`）。
    - `api/views.py` 新增 `ViewpointReviewListView`（`GET /api/summary/viewpoints/?status=&topic_id=`，預設 `status=pending`，依 `-composite_score` 排序）、`ViewpointReviewDecisionView`（`POST /api/summary/viewpoints/<id>/review/`）。兩個都 `permission_classes = [permissions.IsAdminUser]`，理由跟既有的 `CCNDSnapshotAnalysisView` 一樣寫在 docstring 裡：這裡列的是還沒定案的候選觀點，不是給參與者看的。
    - `api/urls.py` 掛上這兩條路徑。
  - 前端新增 `frontend/src/pages/ViewpointReviewPage.jsx` + `.css`：左側清單（依 pending/approved/rejected/all 分頁）、右側詳情（使用者發言、對方回應、評分細節可展開、審核備註輸入、通過/退回按鈕）。研究者直接開 `/viewpoint-review` 網址進去；一般參與者帳號打開會看到後端回的 403（跟 CCND 研究指標端點一樣的道理）。`App.jsx` 加了這條 route。
  - 新增 `api/tests_viewpoint_review.py`（權限、清單篩選/排序、審核決定三個面向，共 11 個案例）。
  - 這台機器缺 `uv`/PostgreSQL 環境，`manage.py makemigrations --check`（含後面追加的 `0013_create_researcher_group.py`）、`manage.py test`、`npm run lint`/`npm run build` 都还没跑過，commit 前請務必在有完整環境的機器上跑一次。
- 2026-07-15 追加（研究者權限改用「研究者」Django Group，不用 is_staff；同一批未 commit 變更內。**這筆取代了同一天稍早「登入 JWT 帶 is_staff claim」的做法**——一開始借用 Django 內建的 `is_staff` 判斷要不要顯示/放行觀點知識庫審核功能，後來覺得 `is_staff`（能不能登入 `/admin/`）語意上不等於「有沒有研究者身分」，改成獨立的 Group）：
  - 新增 `api/permissions.py`：`RESEARCHER_GROUP_NAME = "研究者"` + `IsResearcher`（DRF permission class，檢查 `user.groups.filter(name=RESEARCHER_GROUP_NAME).exists()`）。
  - 新增 `api/migrations/0013_create_researcher_group.py`（手寫，同樣沒環境驗證過）：建立「研究者」Group，並把既有 `is_staff=True` 的帳號自動加進去，避免切換過去時原本能審核的人（目前是 `light` 這個帳號）突然失去權限。
  - `api/views.py` 的 `ViewpointReviewListView`/`ViewpointReviewDecisionView` 的 `permission_classes` 從 `IsAdminUser` 改成 `IsResearcher`。**`CCNDSnapshotAnalysisView` 沒有動**，那個是這次調整之前就存在的 view，仍然用 `IsAdminUser`，這次只調整我這批新增的兩個 view——如果要全專案統一改用 `IsResearcher`，那是之後另一次的決定。
  - `api/serializers.py::BridgeUsTokenObtainPairSerializer` 的 JWT claim 從 `token["is_staff"]` 改成 `token["is_researcher"] = user.groups.filter(name=RESEARCHER_GROUP_NAME).exists()`。
  - 前端 `LoginPage.jsx` 解出 `tokenPayload.is_researcher` 存進 `user.isResearcher`（原本叫 `isStaff`）；`Sidebar.jsx` 的 prop 改名 `isResearcher`，為真才顯示「觀點知識庫審核」連結（📋 icon，導去 `/viewpoint-review`）；`App.jsx` 把 `user?.isResearcher` 傳給 `Sidebar`。同樣**只是前端顯示依據，不是安全邊界**，真正的存取控制在後端 `IsResearcher`。
  - 測試：`api/tests_token_is_staff_claim.py` 刪掉，改成 `api/tests_token_is_researcher_claim.py`（3 案例，含「單純 `is_staff=True` 但沒加入 Group 不會拿到 `is_researcher: true`」這個關鍵案例，確認兩者真的獨立）。`api/tests_viewpoint_review.py` 的研究者帳號建立方式改成 `_make_researcher()`（建立/取得「研究者」Group 並加入），新增一個「`is_staff=True` 但沒在 Group 裡不能審核」的權限案例。

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
| M1 認證 | ✅ | JWT（simplejwt）、登入/刷新、前端 LoginPage |
| M2 議題/立場 | ✅ | `api/dialogue_topics.py` 的 `TOPIC_CONFIGS`/`SURVEY_CONFIGS`；李克特 + 反向題 → stance score → support/neutral/oppose |
| M3 配對 + AI Agent | ✅（持續調整） | `apps/matching/services/matcher.py`（立場向量配對）、`ai_agent.py`（RAG+Claude、三階段策略、streaming）；近期加了 focus signal 偵測、reasoning mode 升級 |
| M4 對話室 | ✅ | `api/consumers.py`：H-AI streaming + H-H 配對房 WebSocket；離題/情緒/僵局介入 |
| M5 NLP/CCND | 🟡 進行中 | 語意樹 `apps/matching/services/semantic_tree.py`（topic-aware anchors）、立場偏移 drift；D3 前端指標列。CCND 視覺化持續中 |
| M6 摘要/知識庫 | 🟡 | ✅ 對話後問卷（`PostDialogueResponse`）+ debriefing 同意/撤回 + Part F 平台體驗回饋（`PlatformFeedback`）；⬜ 立場偏移報告、知識庫沉澱 |

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
- 增量區間：只算上一筆 drift 之後的新訊息；方向 `DIRECTION_THRESHOLD=0.02`（approaching/diverging/stable，前端不顯示）。
- **觸發時機（2026-07-05 起 H-H 與 H-AI 一致）**：每則「發言者本人」的新發言就重算其自己的 drift。
  - H-AI：`_update_session_stance_drift`（每輪 agent 回應後）。
  - H-H：`api/consumers.py::_run_stance_drift`（`_run_message_analysis` 內，embedding 存檔後），算完以 WS `match_stance_drift` **只推給發言者**。舊的 200 字/300 秒節流（`_maybe_run_periodic_analysis`）已移除。
  - 僵局偵測（stalemate）**未跟著改**：獨立時間節流 `_STALEMATE_MIN_INTERVAL_SECONDS=300`（`_match_last_stalemate`，single-process）。
- 舊版 `chat/services/drift.py::calculate_drift` 為 dead code（見上）。
- 前端 UI 標籤已更名為「論述移動」（數值/欄位不變）。
- 另有**問卷版**去極化指標：`PostDialogueResponse.stance_centrism()`（`|S_post-4|-|S_pre-4|`），與語意向量版獨立。

---

## 測試

```bash
cd backend
uv run pytest api/tests.py -v                       # api app（含 matching、post-questionnaire、Part F）
uv run pytest api/tests.py::PlatformFeedbackApiTests -v   # Part F（6 項）
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
- [ ] 問卷初始立場 embedding（Q9）確實填入配對/ session 流程（M2/M3 整合）— 影響 drift 是否有基準
- [ ] CCND 前端視覺化 / WebSocket 推送收尾
- [ ] M6：立場偏移量化報告、觀點知識庫沉澱
- [ ] 清理 `chat` app dead code（drift/session/stalemate/topic/ai_assist + 對應 tests）
- [ ] `docs/BridgeUs_API_Spec.md` 更新（新增 `platform-feedback`、`post-questionnaire` 等）

**基礎設施**
- [ ] Docker Compose（PostgreSQL + Redis）
- [ ] PostgreSQL 切換（`CREATE EXTENSION vector`）
- [ ] 多議題知識庫（目前僅核能；兵役語料已收集未建 collection）
- [ ] CI/CD（GitHub Actions）
