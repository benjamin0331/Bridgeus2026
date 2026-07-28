# 對話結算畫面 — 數值使用對照

**畫面**：`SettlementReceipt`（`frontend/src/pages/SettlementReceipt.jsx`），後測問卷送出後顯示。
**風格**：紙質收據 / 明細風 —— 米色紙感、細分隔線、襯線標題、印章。簡潔優雅、有分享價值。
**互動流程**：送出後測 → 出現**信封** → 點擊有**開封動畫** → 滑出分頁收據明細 → 可翻頁 → 按分享鍵匯出**整份長圖 PNG** 下載。
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

> 無 `s_pre`（沒前測）時：只顯示 `s_post`，隱藏移動量/去極化兩區。

### Page 2 — 思辨投入
| 畫面元件 | 用到的欄位 | 說明 |
|---|---|---|
| S/A/B/C 評級印章 ＋ 頭銜 | `exp_reflection_1/2`、`exp_quality_1/2`、`ccnd_attention`、`ccnd_awareness`、`ccnd_influence` | 7 個平均 → 評級（見第三節）|
| 7 軸思辨投入雷達圖 | 同上 7 個欄位 | 每軸一項，1–7 |

> **只用這 7 個投入型欄位**，刻意不含 `exp_stance_change`（與立場方向有關，會偏誤實驗）。

### Page 3 — 對話花絮
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
- **7 個投入型量表（exp_reflection×2 / exp_quality×2 / ccnd×3）** 📈 → **雷達圖 / 蜘蛛網圖**最貼切，一眼看出「思辨投入」的形狀；或做**橫向長條列**（每項一條 1–7）。目前結算就是拿這 7 個算平均給評級。
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

## 二、目前結算畫面實際用到的欄位（11 個）

| 欄位 | 用途 |
|---|---|
| `s_pre` | 顯示「對話前立場」（count-up 動畫）|
| `s_post` | 顯示「對話後立場」（count-up 動畫）|
| `delta_s` | 顯示「立場移動量」 |
| `stance_centrism` | 顯示「去極化指標」 |
| `exp_reflection_1` / `_2` | 評級計算（思辨投入度）|
| `exp_quality_1` / `_2` | 評級計算 |
| `ccnd_attention` / `awareness` / `influence` | 評級計算 |

組長的 stance metrics（`s_pre`/`s_post`/`delta_s`/`stance_centrism`）**4 個全用到**。

---

## 三、評級規則（思辨投入度）

取上表 7 個**與立場方向無關**的投入型量表平均，對應 S/A/B/C：

| 平均分 | 評級 | 頭銜 |
|---|---|---|
| ≥ 6.0 | S | 深度思辨者 |
| ≥ 5.0 | A | 用心對話者 |
| ≥ 4.0 | B | 認真參與者 |
| < 4.0 | C | 初心探索者 |

- 程式位置：`GRADE_TIERS` / `ENGAGEMENT_FIELDS` / `computeEngagementGrade()`。
- 門檻為自評量表偏高的經驗值，可自由調整。
- 7 個欄位全 null 時退回固定「✓ 完成對話」徽章。

---

## 四、刻意不使用的欄位（避免實驗偏誤 / 尚未用到）

| 欄位 | 為何不用 |
|---|---|
| `exp_stance_change_1` / `_2` | **刻意排除**：衡量「主觀立場改變」。納入評級等於獎勵受試者改立場，污染去極化實驗。 |
| `opponent_judgment` | 未用。可做 H-AI「猜對手」揭曉彩蛋。 |
| `experiment_condition` | 未用。可用來分流結算文案。 |
| `discomfort_flag` / `consent_confirmed` | 倫理 / Debriefing 用，非結算。 |
| `post_open_comprehension` / `post_open_feedback` | 開放題，走 NLP，非結算展示。 |
| `post_likert_1`～`8` | 原始題項，已聚合成 `s_post`。 |
| `pre_question_map` / `id` / `topic_id` / `session_id` / `room_id` / `created_at` | Metadata。 |

---

## 五、設計原則備註

1. **評級不綁立場方向**：通關成就感來自「思辨投入」，不是「有沒有改變立場」，避免受試者為分數刻意改立場。
2. **立場數值中性呈現**：`delta_s` / `stance_centrism` 只呈現軌跡，畫面明示「沒有好壞之分」。
3. **零後端改動**：全部消費現有 `POST /api/post-questionnaire/` 回傳。
