# CONTEXT.md — BridgeUs 開發狀態

> 最後更新：2026-08-02
> 用途：每次對話開始先讀此檔。**「待提交變更」區塊** = 尚未 commit 的工作，下次 commit 直接依此即可；commit 完就把該項移除。

---

## 🟡 待提交變更（Uncommitted）

> 每完成一項未 commit 的工作就記在這；commit 後刪掉該行。

- **登入可用帳號或 Email（新功能，M1）**：`BridgeUsTokenObtainPairSerializer.validate` 在交給 simplejwt 帳密驗證前，把 email 換成對應帳號的 username（`email__iexact`，email 是 unique）。守衛：某人 username 剛好等於別人 email 時，按 username 登入仍進自己的帳號（先查 `filter(username=login).exists()`）。前端 `LoginPage` label 改「帳號或 Email」；`api/auth.js` 的 `login()` 登入後多打一次 `/api/me/` 取回真正 username（否則首頁問候語／`changePassword` 會用到打字時輸入的 email）。測試 `api/tests_login_with_email.py`（8；其 setUp 會 `cache.clear()`，不然 login throttle 計數會溢到後面跑的測試檔）。文件：API Spec、backend/README、frontend/README。
- **信箱驗證（新功能，M1）**：註冊「一定要先驗信箱」＋登入後補／換信箱也要驗。
  - 後端：`accounts.EmailVerificationCode`（migration `accounts/0007`，key 是 email＋可空的 user FK，另有 `verified_at`；同樣只存 SHA-256、單次、10 分鐘、錯 5 次作廢）。
    - 註冊前（未登入）：`POST /api/email-verification/{request,confirm}/`（request 對已註冊的 email 回 400；confirm 成功寫 `verified_at`）。`RegistrationSerializer.validate_email` 多一關：`EmailVerificationCode.is_pre_registration_verified()`（該 email 在 `EMAIL_VERIFICATION_GRACE_MINUTES`＝30 內驗過），沒過回 400「請先完成信箱驗證。」；成功建帳號時把該 email 的驗證紀錄**刪掉**（不能拿去註冊第二個帳號），並把 `User.email_verified_at` 設起來。
    - 登入後（補／換信箱）：`POST /api/me/email/verify/{request,confirm}/`（request 無 body、寄到 `request.user.email`；已驗證或沒信箱回 400）。`/api/me/` GET 多回 `email_verified` 布林。
  - 限流：新 scope `THROTTLE_EMAIL_VERIFICATION_REQUEST`／`_CONFIRM`（預設 `60/hour`，量級對齊 register；未登入依 IP、登入依 user）。settings 新增 `EMAIL_VERIFICATION_CODE_TTL_MINUTES`／`EMAIL_VERIFICATION_GRACE_MINUTES`。
  - 前端：`RegisterPage.jsx` email 欄位下方就地做驗證（寄送驗證碼 → 6 位數欄 → 驗證 → ✓ 已驗證，email 欄鎖定；改 email 就重來），「建立帳號」在驗證前 disabled；`SettingsPage` 的 `ProfileCard.jsx` 改／存 email 後顯示「這個信箱尚未驗證」＋寄碼／驗碼；`api/auth.js` 加 `requestEmailVerification`／`confirmEmailVerification`／`requestMyEmailVerification`／`confirmMyEmailVerification`。
  - 共用 `accounts/emails.py` 的 `send_email_verification_code`（主旨「信箱驗證碼」）。
  - 測試：`accounts/tests_email_verification.py`（18）＋`accounts/tests_registration.py` 補了 `_mark_email_verified` fixture（不然全紅）。
  - ⚠️ 拉到這版要跑 `uv run python manage.py migrate accounts`（dev DB 已跑過）。
- **忘記密碼：Email 收 6 位數驗證碼（新功能，M1）**：登入頁 →「忘記密碼？」→ 輸入 email → 收驗證碼 → 驗碼 + 設新密碼 → 回登入。
  - 後端：`accounts.PasswordResetCode`（migration `accounts/0006`，只存 SHA-256、單次使用、10 分鐘、錯 5 次作廢該碼）；`POST /api/password-reset/request/`（查無此信箱回 404「沒有註冊」——**刻意不做防帳號列舉**，註冊頁本來就會回報 email 已被使用；寄信失敗回 502 並作廢該碼）＋`POST /api/password-reset/confirm/`（驗碼 → `set_password` → `revoke_user_tokens` → 補 `email_verified_at`，**不發 token**；confirm 這支仍回通用錯誤不分辨）；serializer `PasswordResetRequestSerializer`／`PasswordResetConfirmSerializer`；寄信 `accounts/emails.py`（純文字常數）。
  - 限流（未認證、依 IP）：新 scope `THROTTLE_PASSWORD_RESET_REQUEST`（`5/hour`）／`THROTTLE_PASSWORD_RESET_CONFIRM`（`10/hour`）。真正的暴力破解防線是碼自己的 `attempt_count`。
  - Email：settings 新增 `EMAIL_*`（dev 預設 console backend，不寄真信）＋`PASSWORD_RESET_CODE_TTL_MINUTES`。正式機在 `backend/.env` 設 `EMAIL_BACKEND=...smtp...`＋`EMAIL_HOST_USER=bridgeus2026@gmail.com`＋`EMAIL_HOST_PASSWORD=<Gmail 應用程式密碼>`。⚠️ 多數 VPS 封鎖對外 587/465，部署要確認。
  - 舊帳號（研究者代開、無 email）走不到這條流程是刻意的，仍由研究者後台重設。
  - 前端：**做在登入卡片內的檢視切換**（不換頁）——`LoginPage.jsx` 加 `view` 狀態（login → request → confirm → done），「忘記密碼？」按鈕就地展開；`api/auth.js` 的 `requestPasswordReset()`／`confirmPasswordReset()`。（先前的獨立 `ForgotPasswordPage.jsx` 與 `/forgot-password` route 已移除。）
  - 測試：`accounts/tests_password_reset.py`（20 passed）。文件：`docs/BridgeUs_API_Spec.md`、`backend/README.md`、`backend/.env.example`、`frontend/README.md`。
  - ⚠️ 拉到這版要跑 `uv run python manage.py migrate accounts`（dev DB 已跑過）。

- **GPT 備案（新功能）**：演示當天 Anthropic 出事時，改一個 env 就整套切到 OpenAI。
  - `core/llm_provider.py` **真正實作** `LLM_PROVIDER`（`claude` 預設／`openai` 備案，認不得的值一律回 claude）；新增 `active_provider()`／`active_model_name()`／`chat_max_tokens()`。Embedding **不跟著切**（換掉會讓立場向量／CCND／drift 的新舊資料不可比較）。
  - 涵蓋範圍＝所有 LLM 呼叫：H-AI 對話（`ai_agent.astream_respond` 加 `_astream_openai` 分支，走 LangChain astream；Claude 那條原封不動保留原生 client 與 `cache_control` 提示快取）、REST fallback `respond()`、AI 開場（本來就走 `get_llm`）、H-H 介入提示（`hh_ai._call_claude` → `_call_llm`，舊名保留為別名）、M6 摘要（`assemble.generate_ai_summary`）。
  - **刻意不做自動 failover**：一場對話中途換模型，實驗操作就變了一半。切換是人為決定 + 重啟。
  - `AIConversation` 加 `llm_provider`／`llm_model`（migration `api/0038`），WS 與 REST 兩條路都會寫；空字串＝加欄位之前的舊資料（一律 Claude）。⚠️ 拉到這版要跑 `uv run python manage.py migrate api`（dev DB 已跑過）
  - 新指令 `uv run python manage.py llm_smoke_test [--turns N] [--topic-id 102]`：實際打 API 跑幾輪，量**輸出契約一次過的比率**。GPT 對系統 prompt 第十節的遵守度是這件事最大的未知數，違約＝每輪多打一次 API。**演示前務必用 `LLM_PROVIDER=openai` 跑一次。**
  - 依賴：新增 `langchain-openai`；`uv add` 連帶把 `langchain-core` 1.3.0 → 1.6.1。
  - 文件：`.env.example`（`LLM_PROVIDER`／`OPENAI_CHAT_MODEL`／`OPENAI_CHAT_MAX_TOKENS`）
  - 附帶修正：`OPENAI_API_KEY` 其實**早就設好了**（本檔先前記載「兩份 .env 都沒有」是錯的），所以議題 104 的 CCND 走 GPT 版應該是通的。
  - ⚠️ `OPENAI_CHAT_MODEL` 預設值 `gpt-4o` 只是佔位，請依帳號實際可用的模型調整。

- **小 bug 修正四項（WS 斷線／改名／問卷捲動／離題匿名）**：
  - **H-AI 送出後看不到回覆、重新整理才看得到**：回覆其實已生成也落庫，只是那一幀推進了一條半死的連線。三層修法：
    ① 後端生成期間每 10 秒推 `agent_heartbeat`（`_GENERATION_HEARTBEAT_SECONDS`），消除 `agent_thinking`→`agent_stream` 之間數十秒的靜默，中間層才不會把連線收掉；
    ② `DialogueStreamConsumer.disconnect` **不再 cancel** 生成中的 worker（改用 `_STOP_WORKER` 哨兵 + `_detached_turn_workers` 續命），讓那一輪跑完並寫進 DB／history；覆寫 `send()` 在斷線後靜音，避免推不出去的訊息把已完成的一輪記成錯誤；
    ③ 前端 `TopicChat.jsx` 加看門狗：連續 30 秒沒心跳（或收到 close）就改用 `GET /api/dialogue/sessions/<id>/` 把那一則取回貼上，並解除卡住的送出狀態，不再顯示「請重新整理」。`staleAiTurnRef` 擋掉之後補送的 socket 事件避免重複泡泡。
    附帶：`get_dialogue_agent` 加 `lru_cache(maxsize=8)`（原本每則訊息都重建 Chroma retriever，阻塞 event loop；代價是知識庫重建要重啟服務），並用 `sync_to_async(..., thread_sensitive=False)` 移出 event loop；H-H 配對房 WS 掉線改用 `matchWsEpoch` 自動重連（訊息本來就有 3 秒輪詢兜底，但 AI 介入提示只走 WS）。
    測試：`api/tests_websocket.py::test_disconnect_mid_generation_still_persists_the_reply`（1 passed）
  - **全站改名 TakeAbridge**：登入／註冊封面、`index.html` title、聊天室 AI 顯示名、Godot `config/name`（側邊欄本來就已經是這個名字）
  - **後測問卷換頁回到最上面**：`.pq-container` 與 `.pq-body` 兩層都要歸零（`.pq-page` 是 overflow:hidden，`window.scrollTo` 無效）
  - **H-H 離題／僵局提示不再出現「USER 3」**：`hh_ai.py` 的 transcript 從 `User {sender_id}` 改成配對房匿名代號（`_recent_transcript` / `_room_anonymous_ids`），prompt 另加「不要提及任何參與者的名稱或代號」。測試 `apps/matching/tests_hh_ai_transcript.py`（5 項，連同既有 tests_anonymity 共 11 passed）
  - **離題提醒卡片不再 sticky**：拿掉 `.match-assist-card.pinned`，與情緒改寫卡片一致隨對話流排版
  - ⚠️ 這台機器跑 WS 測試會大量假失敗（`receive_json_from(timeout=3)` 太短、rate limit 1.5 秒窗口），`api/tests_input_gate_ws.py` 在 **baseline 就 17 failed / 6 passed**；已用 stash 控制組比對過，本次改動沒有造成迴歸

- **新手導覽只跳一次 + 設定頁可重看（新功能）**：合併 `feat/Ceeeuu` 帶進來的 `OnboardingTour` 原本每次進站都跳，改成第一次登入才自動跳。
  - 後端：`accounts.User` 加 `onboarding_completed_at`（migration `accounts/0005`）；`MeView` GET 多回 `onboarding_completed`（布林），PATCH 白名單加 `onboarding_completed`（`MeProfileUpdateSerializer`）。⚠️ `True` **只在欄位還是 NULL 時**才蓋時間戳——設定頁重看關掉時也會送 `True`，會覆寫的話「第一次看完的時間」就沒了；送 `False` 則清空，等於讓導覽下次登入再自動跳。
  - 前端：`OnboardingTour` 改成受控（拿掉永遠 `return true` 的 `shouldOpenOnMount()`，開關改由 `App.jsx` 條件渲染，所以每次開都是全新掛載）；`App.jsx` 的 `/api/me/` effect 順便讀旗標，**只有 `=== false` 才自動開**（請求失敗或舊版後端回 undefined 時寧可不跳）；`autoTourShownRef` 擋掉同一次登入內重複自動開，登出時連同 `isTourOpen` 一起重設（共用機器）；新增 `components/OnboardingCard.jsx`＋`.css`，`SettingsPage` 一般使用者與研究者「我的帳號」分頁各放一張。
  - 測試：`api/tests_me_profile.py` 的 `MeOnboardingFlagTests`（5 個；整檔 18 passed）
  - 文件：`docs/BridgeUs_API_Spec.md`
  - ⚠️ 拉到這版後要跑 `uv run python manage.py migrate accounts`（dev DB 已跑過）
- **一般使用者設定介面（新功能）**：設定頁 → 個人資料（顯示名稱、Email）。
  - 後端：`MeView` 加 PATCH（`serializers.MeProfileUpdateSerializer`，白名單只有 `display_name` / `email`；email 唯一性 iexact 且排除自己、不可清空；改 email 會清掉 `email_verified_at`）；GET 多回 `email`（NULL 正規化成 `""`，避免前端 input 變 uncontrolled）
  - 前端：`components/ProfileCard.jsx`＋`.css`；`SettingsPage.jsx` 非研究者疊兩張卡片（個人資料＋修改密碼），研究者分頁「帳號安全」改名「我的帳號」並同時放這兩張
  - 測試：`api/tests_me_profile.py`（13 passed）
  - 文件：`docs/BridgeUs_API_Spec.md`
  - ⚠️ `display_name` 目前**哪裡都還沒顯示**（只有 /api/me/ 回給本人），知識庫署名是未來用途；ProfileCard 的提示文字照這個現況寫，不要先寫成「會出現在知識庫」
- **一般使用者自助修改密碼（新功能）**：設定頁 → 帳號安全。
  - 後端：`POST /api/me/password/`（`views.MePasswordChangeView` + `serializers.ChangePasswordSerializer`，驗 old_password／confirm／新舊不得相同）、限流 scope `change_password`（`THROTTLE_CHANGE_PASSWORD`，預設 5/min，依 user 計數）
  - **裝了 `rest_framework_simplejwt.token_blacklist`**：改密碼與研究者 reset-password 都會作廢該帳號所有 outstanding refresh token（`api/token_revocation.py`），改完回一組新 token 讓當前裝置續用。⚠️ 拉到這版後要跑 `uv run python manage.py migrate token_blacklist`（dev DB 已跑過）。access token 不會即時失效，仍活到過期。
  - 前端：`components/ChangePasswordCard.jsx`＋`.css`、`api/auth.js` 的 `changePassword()`（會把新 token 寫回 localStorage）、`SettingsPage.jsx`（非研究者顯示這張卡片取代「尚無設定項」；研究者多一個「帳號安全」分頁）
  - 測試：`api/tests_change_password.py`（11 passed）
  - 文件：`docs/BridgeUs_API_Spec.md`、`backend/README.md`、`backend/.env.example`
  - 附帶：`.claude/launch.json` 加了 backend（8005）設定，供瀏覽器預覽用
- **新增議題 104「手扶梯靠邊站討論」**：`api/dialogue_topics.py` 加 `TOPIC_CONFIGS[104]` + `SURVEY_CONFIGS[104]`（8 李克特 + Q9/Q10，反向題 2/4/6/8，高分＝支持一側站立一側通行）、6 個 CCND 錨點；測試 `api/tests_topic_escalator.py`（8 passed）。
  - **CCND 走 GPT 版**：104 不在 `semantic_tree._LOCAL_CLASSIFIER_MODULES`，`analyze_text_for_tree()` 自動落到 `analyze_with_openai()`。⚠️ 需要 `OPENAI_API_KEY`（目前 `.env` 兩份都沒有），否則這個議題的節點分析會回 `missing_openai_api_key`。
  - RAG collection 用 `escalator_standing_all` 佔位，目前是空的（AI 對話可用，只是沒有可引用來源）；之後灌資料進同名 collection 即生效。
- **AI 開場（新功能，H-AI + H-H）**：依前測開放式問卷（Q9／Q10）產生開場白與可討論方向。
  - 後端：`apps/matching/services/opening.py`（LLM + 議題錨點 bigram fallback）、`MatchOpeningBrief` model（migration `0034`）、`POST /api/dialogue/sessions/<id>/opening/`、`POST|GET /api/matching/rooms/<room_id>/opening/`、房間 payload 加 `opening` 欄位、WS `match_opening` 廣播、env flag `AI_OPENING_ENABLED`（預設 true）
  - 前端：`TopicChat.jsx`（H-AI 進房自動補開場成第一則 agent 訊息；H-H 顯示共用開場卡片）+ `TopicChat.css`
  - 測試：`api/tests_ai_opening.py`（17 passed）
  - 文件：`docs/BridgeUs_API_Spec.md`、`backend/.env.example`、`README.md`、`backend/README.md`、`frontend/README.md`
  - 附帶：input gate 規則 5 的 `prev_ai_is_question` 抽成 `views._previous_ai_turn_is_question`（WS／REST 共用），有開場時第一則短回應（如「第二個」）不再被當低訊息量擋掉
- 後測 D1（對立觀點陳述）最低字數 50 → 30：`api/serializers.py`、`api/models.py` help_text（migration `0033`）、前端 `PostQuestionnairePage.jsx`、`docs/post_questionnaire_v1.1.md`


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

**環境變數（.env）：** `LLM_PROVIDER`(claude/openai，預設 claude；2026-09-04 才真正實作，之前這行是錯的——當時程式碼寫死 Claude)、`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`、`DB_ENGINE`(sqlite/postgres)、`USE_REDIS_CACHE`、`USE_REDIS_CHANNEL`(H-H 多 worker 必開)、`REDIS_URL`、`CLAUDE_CHAT_MODEL`(預設 `claude-sonnet-4-6`)。

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
