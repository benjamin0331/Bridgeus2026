# 對話結算畫面 — 數值使用對照

**畫面**：`SettlementReceipt`（`frontend/src/pages/SettlementReceipt.jsx`），後測問卷送出後顯示。
**風格**：紙質收據 / 明細風 —— 米色紙感、細分隔線、襯線標題、印章。簡潔優雅、有分享價值。
**互動流程**：送出後測 → 出現**信封** → 點擊有**開封動畫** → 滑出分頁收據明細 → 可翻頁 → 按分享鍵匯出**整份長圖 PNG** 下載。
**頁數**：兩頁（立場軌跡 / 對話花絮）。曾有第三頁「思辨投入」（S/A/B/C 評級＋雷達圖），已於 2026-09 移除，理由見文末附錄。
**資料來源**：`POST /api/post-questionnaire/` 的回傳（`PostDialogueResponseOutputSerializer`）＋ 讚倒讚另打 `GET /api/message-reactions/`。**純前端消費，不新增後端欄位。**
**技術**：圖表全手刻 inline SVG（無圖表套件）；PNG 匯出用 `html-to-image` 的 `toPng`（把全部分頁疊進隱藏長容器一次截圖）。

> 📌 本文件為結算功能的即時規格，結算畫面一有更動就同步更新整份。

## 分頁內容 → 對應組長數值（逐項）

每頁上的每個視覺元件，對應到組長回傳的哪個欄位：

### Page 1 — 立場軌跡
| 畫面元件 | 用到的欄位 | 說明 |
|---|---|---|
| 對話前 / 對話後 大字（count-up）| `s_pre`、`s_post` | 前後立場分數，1–7 |
| 1–7 立場刻度尺（前灰點→後印章點＋箭頭）| `s_pre`、`s_post` | 4 分處標「中立」 |
| 「立場移動量」數字 ＋ 有向長條 | `delta_s` | 以 0 為中心，右=偏支持、左=偏反對 |
| 「去極化指標」數字 ＋ 雙向 meter | `stance_centrism` | 左=更靠近中立、右=更極化 |
| **四面向立場拆解**（4 條以 4 為中心的長條）| `post_likert_1`～`8` | 見下方面向表；聚合的 `s_post` 只說「整體偏哪邊」，這一區說「偏在哪個面向」|

> 無 `s_pre`（沒前測）時：只顯示 `s_post`，隱藏移動量/去極化兩區；**面向拆解仍然顯示**（它只用後測 8 題）。

**四面向的算法**（面向與正／反向標註出自 `post_questionnaire_v1.1.md` Part C-1；
post_likert 對前測題號依後端 `POST_LIKERT_TO_PRE_QUESTION = {1:8, 2:5, 3:3, 4:7, 5:1, 6:4, 7:6, 8:2}`）：

| 面向 | 正向題 | 反向題 | 分數 |
|---|---|---|---|
| 安全 | `post_likert_5`（C1-5 / 前測 Q1）| `post_likert_2`（C1-2 / Q5）| (正 + (8 − 反)) / 2 |
| 經濟 | `post_likert_3`（C1-3 / Q3）| `post_likert_7`（C1-7 / Q6）| 同上 |
| 環境 | `post_likert_4`（C1-4 / Q7）| `post_likert_8`（C1-8 / Q2）| 同上 |
| 替代方案 | `post_likert_1`（C1-1 / Q8）| `post_likert_6`（C1-6 / Q4）| 同上 |

反向還原用組長自己的 `score = 8 - raw`，所以四個面向分數與 `s_post` 同尺度：1–7、4=中立、高=偏支持核電。

> ⚠️ **只對議題 102（核能）成立**。面向歸類與反向旗標是各議題的前測設定（後端 `dialogue_topics`），
> 前端拿不到，所以 `topic_id !== 102` 時整區不顯示。要支援議題 103 有兩條路：前端補一份 103 的面向表，
> 或（較好）後端在 payload 裡帶上每題的面向與反向旗標。

### Page 2 — 對話花絮
| 畫面元件 | 用到的欄位 | 說明 |
|---|---|---|
| 「對話對象」標籤（AI／真人）| `experiment_condition` | `ai` → AI、`hh` → 真人 |
| 「你的判斷」猜對手揭曉 | `opponent_judgment` | 僅 H-AI 顯示（1=猜真人、2=猜 AI、3=不確定）|
| 讚 / 倒讚 計數甜甜圈 | **`GET /api/message-reactions/`**（非後測 payload）| 統計本場你按的 👍/👎 數；抓不到就顯示「沒有紀錄」|

## 圖表配色（已過 dataviz 驗證器）
米色紙面 `#f7f3f0` 上：印章 terracotta `#b05540`、綠 `#4c7a3c`、暖灰 `#8a7d6f`、墨 `#2f2722`。
- 驗證器擋掉的：teal `#3f7a86`（讀成灰，棄用）、綠↔terracotta 色盲太近（甜甜圈改綠 vs 暖灰＋文字標籤，識別不靠顏色）、gold 對比不足（不當填色）。
- **無 emoji**：對象標籤純文字、甜甜圈用色塊＋「讚／倒讚」文字。

---

## 零、組長提供的數值總覽（可直接調用）

一場對話結束、送出後測後，後端一次回傳以下 34 個欄位。依性質分五組：

### A. 立場分數（M6 stance metrics，組長本次新增計算）
| 欄位 | 型別 / 範圍 | 意義 |
|---|---|---|
| `s_pre` | float 1–7（4=中立）| 對話前立場分數快照 |
| `s_post` | float 1–7 | 對話後立場分數 |
| `delta_s` | float，= s_post − s_pre | 立場移動量；正=偏支持、負=偏反對 |
| `stance_centrism` | float，= \|s_post−4\|−\|s_pre−4\| | 去極化指標；**< 0 = 更靠近中立** |

### B. 體驗量表 Part C-2（各 1–7 Likert）
| 欄位 | 意義 |
|---|---|
| `exp_stance_change_1` / `_2` | 主觀立場改變自覺 |
| `exp_quality_1` / `_2` | 對話品質感知 |
| `exp_reflection_1` / `_2` | 自我反思 / 元認知 |

### C. CCND 影響評估 Part C-3（各 1–7 Likert）
| 欄位 | 意義 |
|---|---|
| `ccnd_attention` | 注意力門檻 |
| `ccnd_awareness` | 認知差異覺察 |
| `ccnd_influence` | 表達 / 思考調整 |

### D. 原始題項與分類
| 欄位 | 型別 | 意義 |
|---|---|---|
| `post_likert_1`～`8` | 各 1–7 | Part C-1 立場重測 8 題原始分（部分反向計分，聚合成 `s_post`）|
| `opponent_judgment` | 1/2/3 或 null | 猜對手：1=真人、2=AI、3=不確定（H-AI 才有，H-H 為 null）|
| `experiment_condition` | `ai` / `hh` | 這場是跟 AI 還是真人 |
| `discomfort_flag` | bool | 是否回報不適 |
| `consent_confirmed` | null/true/false | Debriefing 同意狀態 |

### E. 開放題與 metadata
| 欄位 | 意義 |
|---|---|
| `post_open_comprehension` | D1 對立觀點陳述（走 NLP 向量化）|
| `post_open_feedback` | D2 自由回饋 |
| `pre_question_map` | 後測題 → 前測題對照表 |
| `id` / `topic_id` / `session_id` / `room_id` / `created_at` | Metadata |

### F. 另有：讚倒讚（獨立資料，非本 payload）
`MessageReaction` 表，記錄「使用者對每則對方訊息按的 👍(1)/👎(-1)」，**私人可見**。要另打 `GET /api/message-reactions/` 取得，不在後測回傳裡。

---

## 一、每個數值能做什麼（含圖形建議）

標記：📈 適合做圖 ／ 🔢 單一數值（適合大字/徽章）／ 🔤 文字。

### 立場分數（A 組）— 結算的視覺主角
- **`s_pre` + `s_post`** 📈 → 最適合做**「1–7 立場軸」**：一條水平刻度尺，4 分處標「中立」，前/後兩個點 + 連線箭頭，直接看出往哪移動。也可做**儀表板 gauge**（指針從 pre 掃到 post）。
- **`delta_s`** 🔢📈 → **有向長條**（以 0 為中心向左右延伸）或 **上升/下降箭頭 + 數字**。單值也適合當大字。
- **`stance_centrism`** 📈 → **以「中立」為中心的雙向 meter**：向中間 = 去極化（綠）、向兩端 = 更極化（紅）。或用一條「離中立距離」的前後對比條。

### 體驗 + CCND 量表（B、C 組，共 7 個投入型 + 2 個立場改變型）
- **7 個投入型量表（exp_reflection×2 / exp_quality×2 / ccnd×3）** 📈 → **雷達圖 / 蜘蛛網圖**最貼切，一眼看出「思辨投入」的形狀；或做**橫向長條列**（每項一條 1–7）。目前結算**先按組長的 subscale 公式兩題兩題平均**，畫成「對話品質／自我反思／CCND 效果」三軸；`ccnd_attention` 因為是注意力門檻而不計分（見第三節）。
- **`exp_stance_change_1/2`** 🔢 → 可單獨顯示「主觀覺得自己變了多少」，但**不建議進評級**（見第四節，會偏誤實驗）。

### 原始題項（D 組）
- **`post_likert_1`～`8`** 📈 → 若之後也拿得到前測 8 題，可做**啞鈴圖 / 斜線圖 (slope chart)**：8 個題項各畫「前→後」兩點連線，看哪幾題鬆動最多。單看後測 8 題則可做長條。
- **`opponent_judgment`** 🔢 → H-AI 專屬**「猜對手」揭曉**：猜對/猜錯一個 badge。全體資料可做**圓餅圖**（多少人以為是真人）。
- **`experiment_condition`** 🔤 → 圖示/標籤（🤖 vs 🧑），可用來分流結算文案，本身不成圖。
- **`discomfort_flag` / `consent_confirmed`** 🔤 → 倫理流程用，不做結算圖。

### 開放題（E 組）
- **`post_open_comprehension`** 📈 → 走 NLP 後可做**語意距離長條**（前後觀點陳述變化量），或詞雲。屬 M5/M6 分析，非即時結算輕量圖。
- 其餘 metadata：不做圖。

### 讚倒讚（F）
- 📈 → **👍/👎 計數甜甜圈或對比條**（「這場你給了 N 讚 / M 倒讚」）。可當結算彩蛋。⚠️ 只能算「你給出去的」，做不出「被讚幾次」（私人、無人能按你）。

---

## 二、目前結算畫面實際用到的欄位（6 個）

| 欄位 | 用途 |
|---|---|
| `s_pre` | Page 1 「對話前立場」（count-up 動畫）＋ 刻度尺灰點 |
| `s_post` | Page 1 「對話後立場」＋ 刻度尺印章點 |
| `delta_s` | Page 1 「立場移動量」＋ 有向長條 |
| `stance_centrism` | Page 1 「去極化指標」＋ 雙向 meter |
| `experiment_condition` | Page 2 「對話對象」標籤（`ai` → AI、`hh` → 真人）|
| `opponent_judgment` | Page 2 「你的判斷」揭曉，僅 H-AI 顯示 |

另外 Page 2 的讚／倒讚甜甜圈打 `GET /api/message-reactions/`，不在後測 payload 裡。

組長的 stance metrics（`s_pre`/`s_post`/`delta_s`/`stance_centrism`）**4 個全用到**，
其餘 28 個欄位目前都沒進結算畫面（見第三節）。

---

## 三、目前未進結算畫面的欄位

| 欄位 | 為何不用 |
|---|---|
| `exp_stance_change_1` / `_2` | 未用。若之後要呈現，組長給它定的用途是「與 ΔS 交叉驗證（主觀感知 vs. 實測變化）」，但**不可進任何評分**——納入等於獎勵受試者改立場，污染去極化實驗。 |
| `exp_quality_1` / `_2`、`exp_reflection_1` / `_2` | 未用。Part C-2 的兩個 subscale（對話品質感知、自我反思／元認知），公式見該文件。 |
| `ccnd_attention` / `_awareness` / `_influence` | 未用。Part C-3；其中 `ccnd_attention` 組長定義為注意力**門檻**（`< 4` → CCND 分析 invalid），不是計分項。 |
| `post_likert_1`～`8` | 未用。Part C-1 的 8 題原始分，已聚合成 `s_post`。附面向與正／反向標註，可拆出「安全／經濟／環境／替代方案」四個面向分數（僅議題 102 有對照）。 |
| `discomfort_flag` / `consent_confirmed` | 倫理 / Debriefing 用，非結算。 |
| `post_open_comprehension` / `post_open_feedback` | 開放題，走 NLP，非結算展示。 |
| `pre_question_map` / `id` / `topic_id` / `session_id` / `room_id` / `created_at` | Metadata。 |

---

## 四、設計原則備註

1. **評級不綁立場方向**：通關成就感來自「思辨投入」，不是「有沒有改變立場」，避免受試者為分數刻意改立場。
2. **立場數值中性呈現**：`delta_s` / `stance_centrism` 只呈現軌跡，畫面明示「沒有好壞之分」。
3. **零後端改動**：全部消費現有 `POST /api/post-questionnaire/` 回傳。

---

## 附錄、已移除的「思辨投入」頁（2026-09）

曾有一頁 S/A/B/C 評級印章 ＋ 思辨投入雷達圖，取 Part C-2／C-3 的量表計分。已整頁移除，
連同 `GRADE_TIERS` / `ENGAGEMENT_AXES` / `subscaleScore` / `computeGrade` / `EngagementRadar`
與四面向拆解、自評 vs 實測兩區。

移除理由與當時查到的事實，留給日後要重做的人：

- **四級門檻 4/5/6 可以推導**：4 是組長反向計分 `score = 8 - raw` 的不動點（解 `8-x=x`）；
  他用 `|S-4|` 量極端程度，該值在 1–7 的值域為 0–3，故中點之上恰三個整數單位，
  加上中點以下共四區。所以切點不是隨手取的。
- **但「要不要分級」與四個頭銜的命名沒有任何依據**，組長兩份問卷文件裡「評級／分級／
  等級／頭銜」零命中，是前端自行發明的遊戲化元素。
- **`ccnd_attention`（C3-1）不該計入平均**：他定義為注意力門檻（`< 4` → invalid），
  混進平均會讓「完全沒看 CCND 但其餘皆滿分」的人照樣拿最高級，與該規則矛盾。
- **同一 subscale 的兩題不該拆成兩軸**：組長本來就設計成每個構念兩題，拆開畫等於
  同一件事量兩次，權重也變兩倍。要畫應先照他的公式兩題平均。

要重做的話，建議先向組長取得分級規格（幾級、切點、名稱），而不是再自行推定。
