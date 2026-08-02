# Supervisor 前端顯示設定 + 混合型對話入口 Design

> 日期：2026-07-27
> 狀態：設計定案，待實作計畫
> 相關：`docs/superpowers/specs/2026-07-21-supervisor-account-management-design.md`（研究者身分定義）、
> `docs/superpowers/specs/2026-07-26-backend-identity-code-review.md`（身份層已知問題，本次不處理）

---

## 1. 背景與目標

目前每個議題在首頁會渲染**兩張卡**（AI／配對），使用者自己選要跟 AI 對談還是等真人配對
（`frontend/src/components/IssueCard.jsx:16`）。這對研究者測試很方便，但對受試者是錯的：

- 分流本身是實驗變項。讓受試者自選模式，等於讓他們自己決定要進哪一組。
- `mode` 只是 query string（`/topic/102?mode=match`），**後端完全沒有把關**——受試者手改網址就能換組。

目標：

1. 一般使用者只看到**一個混合型入口**：填完立場問卷後，由後端依立場自動分流——中立 → AI 對話、
   極端（support／oppose）→ 真人配對。
2. 研究者維持現在分開的兩個入口，方便測試。
3. Supervisor 可在設定頁調整三組顯示設定：議題開關、入口模式、分流門檻。

## 2. 範圍

**納入**

- `議題開關`：每個議題 × 每種角色，可決定該議題是否出現在清單。
- `入口模式`：每種角色一個值（`mixed` / `split`）。
- `分流門檻`：每個議題的 `support_threshold` / `oppose_threshold` 可覆寫（不分角色）。
- `配對逾時分鐘數`：全站一個值，決定等多久後詢問使用者要不要改跟 AI 對話。
- 混合入口的後端分流端點與強制把關。
- 分流指派紀錄（`DialogueEntryAssignment`）。
- 修正顯示路徑即時重算 stance category 的問題（見 §9）。

**不納入**

- 把 `TOPIC_CONFIGS` / `SURVEY_CONFIGS` 整個搬進 DB（見 §11）。
- 通用 feature flag 機制。
- 針對個別帳號（而非角色）指定入口模式。
- 身份層 code review 的 P0/P1 問題（另案）。

## 3. 現況

| 項目 | 現況 | 位置 |
|---|---|---|
| 議題定義 | 寫死在 Python dict，`get_dialogue_topics()` 回傳全部，無開關 | `api/dialogue_topics.py` |
| 問卷／門檻 | 寫死在 `SURVEY_CONFIGS[topic_id]["stance_rules"]` | `api/dialogue_topics.py` |
| 門檻唯一讀取點 | `_get_survey_scoring_config(topic_id)`，**無快取** | `api/views.py:291` |
| stance 計算 | 後端算。前端只送原始問卷答案 | `api/views.py:340`, `:373` |
| 前端入口 | 每議題兩張卡 → `/topic/<id>?mode=ai\|match` | `IssueCard.jsx:16` |
| 中立擋配對 | **已存在**：`_can_enter_human_matching()` → `ai_recommended` | `apps/matching/services/matcher.py:517` |
| 前端提示 | 「你的立場目前較適合先和 AI 代理人討論」+ 手動按鈕 | `TopicChat.jsx:1788` |
| mode 把關 | **無**。`MatchingJoinView` / `DialogueSessionCreateView` 只有 `IsAuthenticated` | `api/views.py:1825`, `:942` |

關鍵觀察：**「中立→AI、極端→配對」的判斷邏輯後端已經有了**。缺的不是判斷，是把它從
「使用者先選錯再被退回」變成「單一入口自動分流」，並補上把關。

## 4. 設計決策

| 決策 | 選擇 | 理由 |
|---|---|---|
| 設定存放 | **覆寫層（overlay）**：`TOPIC_CONFIGS` 仍是內容真實來源，DB 只存被改過的值 | 不必搬遷兩個大 dict；門檻預設值仍留在程式碼裡可讀；只有實際調過的進 DB |
| 分流決策 | **後端**。問卷送出 → 後端算 stance → 回傳 route | 前端決策擋不住改網址；實驗分組必須可信 |
| 把關依據 | **明確的指派紀錄**，不用推導 | fallback 後極端使用者也要能進 AI，純推導無法表達；且指派本身是該留的實驗資料 |
| 議題關閉語意 | 隱藏入口；**進行中的對話不受影響** | 實驗資料不會斷在一半，對話後問卷仍填得完 |
| 配對逾時 | 等 N 分鐘後**詢問**使用者是否改跟 AI 對話 | 受試者意願被尊重，且資料歸屬乾淨（有明確的 fallback 紀錄） |
| 門檻改動 | 舊 `UserStanceProfile` 不重算，只影響新問卷 | 不追溯改寫已發生的實驗資料 |
| 角色維度 | 只套在**入口模式**與**議題開關**；門檻全站一組 | 研究者與受試者用不同門檻算出的 stance 無法互相比較 |

「角色」定義沿用既有的 `IsResearcher`：屬於「研究者」Django Group 即為 researcher，其餘為 participant
（`api/permissions.py:14`）。

## 5. 資料模型

新增兩個 model（`api/models.py`）：

```python
class PlatformDisplaySetting(models.Model):
    """全站顯示設定。刻意只允許一列（pk=1），用 load() 取得。"""

    class EntryMode(models.TextChoices):
        MIXED = "mixed", "混合入口"
        SPLIT = "split", "分開入口"

    participant_entry_mode = models.CharField(
        max_length=16, choices=EntryMode.choices, default=EntryMode.MIXED
    )
    researcher_entry_mode = models.CharField(
        max_length=16, choices=EntryMode.choices, default=EntryMode.SPLIT
    )
    match_fallback_timeout_minutes = models.PositiveIntegerField(default=5)
    updated_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(auto_now=True)
```

```python
class TopicDisplayOverride(models.Model):
    """單一議題的顯示覆寫。沒有對應列 = 全部用程式碼預設值。"""

    topic_id = models.IntegerField(unique=True)
    visible_to_participant = models.BooleanField(default=True)
    visible_to_researcher = models.BooleanField(default=True)
    # null = 沿用 SURVEY_CONFIGS 裡的預設門檻
    support_threshold = models.FloatField(null=True, blank=True)
    oppose_threshold = models.FloatField(null=True, blank=True)
    updated_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(auto_now=True)
```

```python
class DialogueEntryAssignment(models.Model):
    """混合入口把某位使用者分到哪一組。每人每議題一筆。

    這同時是把關依據與實驗資料：可以回答「這位受試者被指派到哪組、
    當時的 stance 是多少、有沒有因為配對逾時而轉去 AI」。
    """

    class Route(models.TextChoices):
        AI = "ai", "AI 對話"
        MATCH = "match", "真人配對"

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    topic_id = models.IntegerField()
    route = models.CharField(max_length=8, choices=Route.choices)
    stance_score = models.DecimalField(max_digits=4, decimal_places=2)
    stance_category = models.CharField(max_length=16)
    # 指派當下生效的門檻，供日後分析對照（見 §9）
    support_threshold = models.FloatField()
    oppose_threshold = models.FloatField()
    entry_mode_at_assignment = models.CharField(max_length=16)
    assigned_at = models.DateTimeField(auto_now_add=True)
    fallback_offered_at = models.DateTimeField(null=True, blank=True)
    fallback_accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "topic_id"], name="uniq_entry_assignment_user_topic"
            )
        ]
```

重新填寫問卷時，同一 `(user, topic_id)` 的指派以 `update_or_create` 覆寫，並清空 fallback 欄位。

## 6. 讀取層

新增 `api/display_settings.py`，作為**所有**顯示設定的唯一讀取入口：

```python
def get_entry_mode(*, is_researcher: bool) -> str
def visible_topics(*, is_researcher: bool) -> list[dict]     # 取代 get_dialogue_topics() 的直接呼叫
def is_topic_visible(*, topic_id: int, is_researcher: bool) -> bool
def get_stance_thresholds(*, topic_id: int) -> tuple[float, float]  # (support, oppose)
def get_match_fallback_timeout_seconds() -> int
```

**merge 規則**：`TopicDisplayOverride` 有列且欄位非 null → 用覆寫值；否則用 `SURVEY_CONFIGS` /
程式碼預設值。

**接點**：`_get_survey_scoring_config()`（`api/views.py:291`）改成從 `get_stance_thresholds()` 取
`support_threshold` / `oppose_threshold`，其餘不動。因為它是唯一讀門檻的地方且無快取，這一處改完，
所有既有呼叫點（`_resolve_stance_category`、`_compute_user_stance_score` 等 8 處）自動生效。

**約束**：實作後不得有任何地方直接讀 `TOPIC_CONFIGS[...]` 或 `SURVEY_CONFIGS[...]["stance_rules"]`
來做可見性或門檻判斷，一律走本模組。

## 7. API

### 7.1 設定管理（研究者專用，`IsResearcher`）

| Method | Path | 說明 |
|---|---|---|
| GET | `/api/settings/display/` | 回傳全站設定 + 每個議題的有效值（含是否為覆寫） |
| PATCH | `/api/settings/display/` | 更新全站設定（入口模式、逾時分鐘） |
| PATCH | `/api/settings/display/topics/<topic_id>/` | 更新單一議題的開關與門檻覆寫 |

`GET` 回傳每個議題時同時給 `effective` 與 `is_overridden`，讓設定頁能顯示「目前值 / 是否已被改過 /
程式碼預設是多少」，並提供「還原預設」（刪除覆寫欄位，設回 null）。

驗證規則：

- `oppose_threshold` 必須 `<` `support_threshold`，且兩者都要落在該議題量表範圍內（`scale.min`～`scale.max`）。
- `match_fallback_timeout_minutes` 範圍 1–120。
- 改門檻的請求回應中附帶警示字串，說明「此變更只影響之後填寫的問卷，既有資料不會重算」。

### 7.2 議題清單（既有端點，行為變更）

`GET /api/dialogue/topics/` 改為依請求者角色過濾：
不可見的議題**不出現在回應中**（隱藏，不是標記為關閉）。

> 注意：此端點目前使用 `JWTStatelessUserAuthentication`（`api/views.py:878`），而 `TokenUser.groups`
> 是 `EmptyManager`——直接用它判斷研究者身分會**永遠得到 False**。本端點必須改用預設的
> `JWTAuthentication`（拿真正的 User）才能正確分角色。這是 code review 已記錄的地雷，本次踩到，一併處理。

### 7.3 混合入口

**`POST /api/dialogue/entry/`** — 一般使用者的唯一入口。

Request：`{ topic_id, survey_answers, survey_open_answers }`

行為：

1. 議題對此角色不可見 → `404`「找不到這個議題。」（不洩漏其存在）。
2. 後端計算 `stance_score` 與 `stance_category`（沿用 `_compute_user_stance_score` /
   `_resolve_stance_category`，此時已吃到覆寫門檻）。
3. `update_or_create` 寫入 `UserStanceProfile` 與 `DialogueEntryAssignment`。
4. 依 `route` 分派，**直接複用既有服務**：
   - `route=ai` → 走 `DialogueSessionCreateView` 現有的 session 建立流程，回傳同樣的 session payload。
   - `route=match` → 走 `enqueue_for_matching()`，回傳同樣的 matching state payload。

`DialogueSessionCreateView` 目前要求前端送 `topic_title` / `topic_description` /
`user_initial_argument`。混合入口**不接受**這些欄位，一律由後端從 `TOPIC_CONFIGS[topic_id]` 取，
`user_initial_argument` 取問卷 Q9 的開放式答案。少一組可被客戶端竄改的輸入。

Response：`{ route: "ai" | "match", ...(對應既有的 payload) }`

`route` 判定沿用既有的 `_can_enter_human_matching(stance_category)`——中立無配對候選 → `ai`，
其餘 → `match`。這樣分流規則只有一份定義。

**`POST /api/dialogue/entry/fallback/`** — 配對等太久，使用者同意改跟 AI 對話。

Request：`{ topic_id }`

行為：驗證該使用者的 assignment 為 `route=match`，且從 `MatchQueueEntry.waiting_started_at`
**當場重算**的等待時間已達逾時，否則 `400`。通過則設 `fallback_accepted_at`、取消佇列中的
`MatchQueueEntry`，並用 `UserStanceProfile` 裡**已存的**問卷答案建立 AI session（不重新問卷），
回傳 session payload。

> 驗證刻意採「當場重算」而非檢查 `fallback_offered_at` 是否已寫入：後者會讓這個端點依賴前端
> 先輪詢過 status，多一個隱性順序耦合。`fallback_offered_at` 只作為研究紀錄（第一次被提示的時間）。

### 7.4 逾時提示

`GET /api/matching/status/` 的回應新增：

```json
"fallback_offer": {
  "available": true,
  "waited_seconds": 312,
  "timeout_seconds": 300
}
```

`available` 由 `MatchQueueEntry.waiting_started_at` 加上逾時設定推算。第一次回傳 `available: true` 時，
後端順手寫入 `fallback_offered_at`。前端輪詢到 `available: true` 就跳出詢問對話框。

分開入口（研究者）不回傳此欄位。

## 8. 強制把關

**只在請求者的有效 `entry_mode == "mixed"` 時生效**；`split` 時維持現行行為，研究者不受影響。

| 端點 | 規則 |
|---|---|
| `POST /api/matching/join/` | 需存在 assignment 且 `route=match`，否則 `403` |
| `POST /api/dialogue/sessions/` | 需存在 assignment 且（`route=ai` 或 `fallback_accepted_at` 非 null），否則 `403` |

錯誤訊息一律為「請從議題頁面開始對話。」，不揭露分流規則本身——否則受試者可以反推自己被分到哪組，
影響作答。

前端在混合模式下不會產生這些直接呼叫，把關是防手改網址／直接打 API，屬於實驗完整性防線。

## 9. 修正：顯示路徑的 stance category 重算

`_resolve_stance_category()` 目前在三處**顯示**路徑被即時呼叫，用 `stance_score` 重算分類：
`api/views.py:248`、`:1014`、`:1176`。

問題：門檻一改，舊對話畫面上的立場分類會跟著改變，直接違反「舊資料不動」的決策。

修正：這三處改為**優先使用已儲存的分類**——`DialogueEntryAssignment.stance_category`（若有）、
其次 `UserStanceProfile.stance_category`，兩者都沒有時才回退到即時計算（相容舊資料）。

`DialogueEntryAssignment` 同時保存指派當下的 `support_threshold` / `oppose_threshold`，
讓日後分析能區分「這筆樣本是用哪組門檻分流的」，不必靠設定變更的記憶。

## 10. 前端變更

| 檔案 | 變更 |
|---|---|
| `components/IssueCard.jsx` | 依 `entry_mode` 決定渲染：`split` 維持每議題兩張卡；`mixed` 每議題**一張卡**，導向 `/topic/<id>`（不帶 `mode`） |
| `pages/TopicChat.jsx` | 無 `mode` 參數時進入混合流程：先問卷 → `POST /api/dialogue/entry/` → 依回傳 `route` 切換到既有的 AI 或配對 UI。既有的 `?mode=` 分支原封保留給研究者 |
| `pages/TopicChat.jsx` | 配對等待畫面依 `fallback_offer.available` 跳出「目前沒有合適的對象，要改成和 AI 對話嗎？」對話框；同意 → `POST /api/dialogue/entry/fallback/` |
| `pages/SettingsPage.jsx` | 新增「顯示設定」面板：入口模式（兩種角色各一）、逾時分鐘、議題清單（每議題兩個可見性開關 + 兩個門檻欄位 + 還原預設） |
| `App.jsx` | 議題清單改由後端過濾，前端不再自行判斷可見性 |

入口模式由後端 `GET /api/settings/display/` 或既有的使用者資訊提供，**不從 JWT claim 讀**——
`is_researcher` claim 在降級後仍會存活 7 天（見 code review），不適合當作分流依據。

## 11. 錯誤處理

| 情況 | 處理 |
|---|---|
| 議題對此角色不可見 | `404`「找不到這個議題。」（隱藏即隱藏） |
| 混合模式下直接打 join/sessions | `403`「請從議題頁面開始對話。」 |
| 門檻覆寫不合法（oppose ≥ support、超出量表範圍） | `400` 附具體說明 |
| `PlatformDisplaySetting` 尚未建立 | `load()` 以 `get_or_create(pk=1)` 建立預設列 |
| 議題進行中被關閉 | 既有 session／房間照常運作（那些端點以 `session_id` / `room_id` 定位，不經過議題清單）；只有 `GET /api/dialogue/topics/` 與 `POST /api/dialogue/entry/` 受影響。對話後問卷、Part F、歷史回顧一律不受影響 |
| fallback 未達逾時就呼叫 | `400`「尚未達到等待時間。」 |

## 12. 測試計畫

**overlay 讀取層**（`api/tests_display_settings.py`）

- 無覆寫時 `get_stance_thresholds()` 回傳 `SURVEY_CONFIGS` 的預設值
- 有覆寫時回傳覆寫值；單邊覆寫（只設 support）時另一邊仍用預設
- `visible_topics()` 依角色過濾正確
- 覆寫門檻後 `_resolve_stance_category()` 的分界點跟著移動

**設定 API**

- 非研究者 `403`（三個端點）
- `oppose >= support` 被擋
- 逾時分鐘超出 1–120 被擋
- 還原預設（設回 null）後回到程式碼預設值

**混合入口分流**

- 中立分數 → `route=ai`，回傳 session payload
- 極端分數 → `route=match`，回傳 matching payload
- 不可見議題 → `404`
- 重填問卷 → assignment 被覆寫且 fallback 欄位清空

**把關**

- 混合模式下無 assignment 直接打 `/api/matching/join/` → `403`
- 混合模式下 `route=match` 的使用者直接打 `/api/dialogue/sessions/` → `403`
- 同一使用者接受 fallback 後，同樣的呼叫 → `200`
- 研究者（`split`）打兩個端點都不受影響

**fallback**

- 未達逾時 `fallback_offer.available` 為 false；達到後為 true 並寫入 `fallback_offered_at`
- 接受 fallback → 佇列取消、AI session 建立、**沿用已存問卷答案**（不需重填）
- 未達逾時就呼叫 fallback 端點 → `400`

**舊資料不動**

- 改門檻後，既有 `UserStanceProfile.stance_category` 不變
- 改門檻後，既有對話的顯示分類不變（§9 的修正）

## 13. 建議的實作分期

本 spec 涵蓋的範圍偏大，建議切成兩個可獨立驗收的階段：

**階段一：設定層** — §5 兩個設定 model、§6 讀取層、§7.1 設定 API、§7.2 議題清單過濾（含
`JWTStatelessUserAuthentication` 修正）、§9 顯示路徑修正、SettingsPage 面板。
做完即可獨立驗收：Supervisor 能開關議題、調門檻，前端清單跟著變，舊資料不受影響。

**階段二：混合入口** — `DialogueEntryAssignment`、§7.3 分流端點、§7.4 逾時提示、§8 把關、
前端 IssueCard / TopicChat 的混合流程。
依賴階段一的 `get_entry_mode()`。

## 14. 後續（不在本次範圍）

- `TOPIC_CONFIGS` / `SURVEY_CONFIGS` 遷入 DB：議題數量再長就必須做，屆時本次的 overlay 可整併回去。
- 設定變更的稽核紀錄：目前只有 `updated_by` / `updated_at`（最後一次）。完整的變更歷史與
  帳號管理的稽核需求應一起設計（見 code review P3）。
- 針對個別帳號指定入口模式（實驗組／對照組手動分派）。
