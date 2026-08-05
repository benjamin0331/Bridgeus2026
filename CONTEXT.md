# CONTEXT.md — BridgeUs 開發狀態

> 最後更新：2026-07-11
> 用途：每次對話開始先讀此檔。**「待提交變更」區塊** = 尚未 commit 的工作，下次 commit 直接依此即可；commit 完就把該項移除。

---

## 🟡 待提交變更（Uncommitted）

> 每完成一項未 commit 的工作就記在這；commit 後刪掉該行。

- **Godot 等級系統（七色青蛙）**——規格見 `docs/godot_level_colors.md`。
  - 後端：`views.py` 加 `LEVEL_THRESHOLDS` / `dialogue_level()`，`TitleMeView.get` 回傳 `level` / `dialogue_count` / `level_thresholds`（無 migration，純衍生值）。`tests.py` 加 `DialogueLevelTests`（5 項，全過）。
  - Godot：`Backend.gd` 快取 `level`；`player_00.gd` 的 `appearance` 改成等級並播 `lvN_*`（原 `char_N_*` 保留但不播）；`game_ui.gd` 右上角等級色表；`player_00.tscn` 加 14 個 `lv0`–`lv6` 動畫。
  - 新增 `scripts/recolor_frog.py` 生成 `godot/Assets/ToxicFrog/Level/` 的 14 張 sprite，以及 `--rainbow` 產的 Lv777 稀有款彩虹蛙（每次進場 1/10 機率，不持久化；刷到時色表不標箭頭）。
  - 新增 `scripts/check_player_tscn.py`：tscn 等級動畫接線的靜態檢查（無 Godot CLI 時的替代驗證）。
  - 稀有款恭喜彈窗（`game_ui.gd`）：版面照 Invite 面板（預設主題 Panel、絕對 offset 置中），
    兩顆按鈕「太閃了，我不要」（→ `decline_rare()` 換回等級色，只影響該場）／「確定」。
    彩虹彩度由 0.95 降到 0.62（原本螢光刺眼）。
    等級彈窗**已移除**，改成常駐顯示在成就頁最上面：
    新增 `frontend/src/pages/LevelSummaryCard.jsx`（等級／場次／離下一級 + hover 小問號說明），
    掛在 `AchievementPage` 頂端，CSS 併入 `AchievementPage.css`。
    卡片背景是遊戲內實拍截圖 `public/frogs/map_bg.png`，青蛙上緣露出卡片外 1/3 做層次；尺寸全部由 `--frog-w`（clamp）推導，響應式等比縮放。
    ⚠️ 目前吃寫死的示意資料（卡上有「後端尚未串接」標籤）—— `/api/titles/me/` 實測抓不到，見 `docs/0804.md` §2.1。
  - 新增 `scripts/export_web_assets.py`（青蛙圖）→ `frontend/public/frogs/`。背景改用人工截圖；曾寫的 `render_map_region.py`（解析 tscn 重畫地圖）因水域畫壞已移除。
    首頁虛擬大廳卡片也改用同一張地圖背景，右下角加了一隻水平翻轉的待機動畫青蛙（CSS sprite，非 GIF）。
  - 新增 `frontend/src/components/ScatterDecor.jsx`＋CSS：首頁兩張入口卡的散落裝飾（大廳＝6 隻等級青蛙，知識庫＝5 個遊戲內表情），固定座標非亂數。
    `ActionCard` 多一個 `children` prop 供裝飾層掛載。`export_web_assets.py` 一併匯出 `emoji0`–`emoji4.png`（座標同 `Globals/Emoji.gd`）。
  - **新增 `docs/0804.md`**：交接給後端，列出等級功能上線後需要確認／補的事（成就系統的資料缺口為主；彈窗已讀狀態那項已因改成常駐顯示而取消）。
  - 稀有款彈窗改用 anchor 定位到「中間下方」（原本照 Invite 寫死 1280 座標，
    但 stretch 是 canvas_items+expand、實際視野更寬，所以會偏左；`.tscn` 裡的舊面板同樣有此問題，未修）。
  - 修掉主功能交接的競態：`game.gd::_ready()` 在 Web 上先 `await _await_host_handoff()`
    （輪詢 `window.bridgeus_token`，2 秒逾時）再讀那些變數，並重解析連線位址。
    這是「偶爾等級表沒有場次／等級永遠 0」的根因——Godot 開機比 `GodotLobby.jsx::handleLoad` 設值更早時搶輸。
  - 視窗開著時鎖移動：`game_ui.blocks_movement()` 從只擋 Read 擴充成擋 Read/Invite/Chat/Voice/稀有款彈窗
    ＋ NPC 對話框（`dialogue` group 轉問 `dialogue_box.is_open()`）＋ 議題表單（`suppress_menu`）。
    Menu 刻意不擋（那個面板靠走出範圍才關，鎖了會卡死）。
  - 背景音樂：新增 `Globals/Bgm.gd` autoload（循環播放 `Assets/Audio/retro-bgm-chan-home-at-night-516298.mp3`，-14dB），
    語音通話期間靜音、掛斷還原；靜音鉤子放在 `VoiceChat.start()/stop()`（語音唯一的開關點）。
    `project.godot` 註冊 autoload。⚠️ mp3 還沒被 Godot 匯入過，第一次開編輯器會產生 `.import`，記得一起 commit。
  - ⚠️ 未完成：`godot/Assets/GreenBlue/`（`Assets/ToxicFrog/GreenBlue/` 的整份重複、無人引用）該刪但還沒刪。

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
