# M6 觀點知識庫 — 審核 pipeline 調整（Step 3 移除 + Step 2 新門檻）

> 日期：2026-09-10
> 範圍：`backend/apps/summary/pipeline/`（Step 1–2 篩選）、`backend/apps/summary/pipeline/assemble.py`（組資料層）
> **沒有 migration**：`composite_score` / `score_detail` / `quality_score` 三個欄位保留（都 nullable），只是新資料不再填。
> **狀態：工作區未 commit**（7 個檔案）。只跑過 M6 pipeline 相關的測試檔（全綠，約 51 個），沒跑完整後端 suite、沒跑前端 build。

---

## 一、這次做的改變

### A. Step 3「加權評分排序」整段移除

`quality_filter.py`：

| 移除 | 說明 |
|---|---|
| `W_SEMANTIC / W_STANCE / W_LEXICAL / W_LENGTH` | 加權評分權重（0.35 / 0.35 / 0.2 / 0.1） |
| `TOP_N = 15` | 每場對話最多送幾筆進人工終審 |
| `_normalize()` | min-max 正規化 |
| `score_and_rank()` | 計算 `composite_score` + `score_detail`、依分數降序、取 Top 15 |
| `_select_balanced_by_side()` | 在 Top 15 內保證雙方立場都有代表 |

`run_pipeline()`：

- 簽章由 `run_pipeline(messages, top_n=TOP_N, *, turn_count_side=None)` 改成 `run_pipeline(messages, *, turn_count_side=None)`（**拿掉 `top_n` 參數**）。
- 通過 Step 1 後直接 `return extract_valuable_pairs(messages)` —— **不排序、不截斷、不做雙方平衡**，Step 2 過門檻的配對全部原樣進 Step 4。
- 回傳的配對 dict **不再含 `composite_score` / `score_detail`**。

`assemble.py`：

- 移除 `_quality_score()`（原本 = Step 3 選中配對 `composite_score` 的平均）。
- `run_pipeline_for_match()` / `run_pipeline_for_session()`：
  - `ranked` 變數改名 `pairs`。
  - `write_dialogue_summary(...)` 的 `"quality_score"` 一律傳 `None`。
  - `write_viewpoint(...)` 的 payload **拿掉 `composite_score` / `score_detail`**（`write_viewpoint` 本來就用 `.get()` 帶預設 `None` / `{}`）。

`scripts/seed_knowledge_base.py`：對齊新的 `run_pipeline()` 輸出（拿掉 `top_n=3`、`quality_score` 改 `None`、不再讀 `composite_score` / `score_detail`）。

### B. Step 2 新增兩道逐則門檻

`extract_valuable_pairs()` 每則發言要**全部通過**才會成為候選觀點。原本 4 道（長度、觀點推進度、單則攻擊比例、有對方回應可配對），現在多 2 道：

| 門檻 | 常數 / 來源 | 判定 |
|---|---|---|
| **未觸發離題** | `msg["is_off_topic"]`（bool，由組資料層標記） | `is_off_topic` 為真 → `continue`（不收錄）。放在迴圈最前面（最便宜） |
| **詞彙豐富度 ≥ 30%** | `MIN_LEXICAL_RICHNESS = 0.3` | `_lexical_richness(content) < 0.3` → `continue`。`_lexical_richness` = jieba 斷詞後 `不重複詞 / 總詞`。放在其他便宜門檻之後（jieba 較貴） |

`_lexical_richness()` 是 Step 3 移除時一起被刪掉、現在**重新加回來當 Step 2 的硬門檻**（原本是 Step 3 裡 0.2 權重的評分項，不是門檻）。

### C. 離題判定（`assemble.py` 新增）

組資料層在 `build_messages_for_match()` / `build_messages_for_session()` 幫每則**使用者發言**算好 `is_off_topic` 旗標：

```
_topic_off_topic_context(topic_id) -> (anchor_embedding | None, threshold)
_message_is_off_topic(embedding, anchor_embedding, threshold) -> bool
```

- `_topic_off_topic_context`：沿用 **M4 對話室即時離題偵測的同一組設定** ——
  `TOPIC_CONFIGS[topic_id]["off_topic_detection"]` 的 `anchor_text`（沒有就 fallback 到 `topic_description`）與 `threshold`（預設 `0.35`）。
  透過 `apps.matching.services.topic_relevance.get_topic_relevance_policy` / `get_topic_anchor_embedding`（後者 `@lru_cache`，整個 process 每個 anchor 只 embed 一次）。
  該議題沒設定 anchor 文字時回 `(None, ...)` → 呼叫端一律判定「沒離題」。
- `_message_is_off_topic`：這則發言**自己的** embedding 與議題錨點的 cosine similarity `< threshold` 就算離題。缺 embedding 或缺 anchor → 回 `False`（訊號缺失不擋發言）。
- **注意這是「單則 vs 錨點」的判定，不是 M4 的滾動窗口（最近 3 則實質發言平均向量）觸發的重播。** 兩者可能不一致。

H-AI（`build_messages_for_session`）只在 `side="a"`（使用者發言）的 dict 加 `is_off_topic`；AI 回覆本來就不會變觀點。

### D. 測試

| 檔案 | 改動 |
|---|---|
| `apps/summary/tests_pipeline.py` | 刪 `ScoreAndRankBalanceTests`（4 個）+ `_pair` helper；新增 `ExtractValuablePairsGateTests`（`SimpleTestCase`，免 DB，4 個）：重複灌水被擋 / 離題旗標被擋 / 正常在題發言保留 / 缺 `is_off_topic` key 不誤擋 |
| `apps/summary/tests_assemble.py` | 新增 `OffTopicFlagTests`（2 個）：patch `_topic_off_topic_context` 成可控錨點，實測 `build_messages_for_match` 正確標記 在題 / 離題 / 缺 embedding / 無錨點 |
| `api/tests_viewpoint_review_hai.py` | 加 class 級 autouse fixture，把 `_message_is_off_topic` mock 成 `False`（見下方整合注意 §8） |
| `apps/matching/tests_m6_trigger_on_close.py` | `setUp` 加同樣的 mock（理由同上） |

---

## 二、後端整合這次改變需要注意的點

### 1. 沒有 migration，但有**資料語意變化**

- `ViewpointNode.composite_score` / `score_detail`、`DialogueSummary.quality_score`：**新資料一律為 `None` / `{}`**。欄位還在，別的地方讀到要能吃 `None`。
- 舊資料（這次上線前建立的節點）仍有真值 → **審核清單與公開頁會混合「有分數的舊節點」與「無分數的新節點」**。

### 2. `quality_score` 的下游

`DialogueSummary.quality_score` 從此不再有值。上線前請 grep 一次專案 + 前端 + 任何報表 / 匯出 / 分析腳本，確認沒有地方假設它是非空。

### 3. 審核佇列量會明顯變大

- 以前每場對話最多 **15 筆**候選進 `pending`。現在 **只要過 Step 2 的六道門檻就全收**，一場深度對話可能 20–30+ 筆。
- 研究者人工終審工作量放大。
- Step 4 去重每筆都要 `get_embedding()`（Sentence-Transformers）+ pgvector 查詢，而且是在對話結束（H-H 關房 / H-AI 送出後問卷）的 `transaction.on_commit` 裡**同步**執行 → 對話結束的回應時間變長。若量大到有感，考慮把 pipeline 丟到背景任務。

### 4. 審核清單排序退化

`ViewpointReviewListView` 是 `order_by("-composite_score", "-created_at")`。新節點 `composite_score = NULL`（PostgreSQL DESC 時 NULL 排最前）→ 佇列實際變成純 `-created_at`（新的在上）。若要「先審最有價值的」那種排序，需要另外定義新的排序訊號。

### 5. 公開頁排序 tiebreak

`_approved_viewpoints_queryset` 是 `order_by("-favorite_count", "-_score")`，其中 `_score = Coalesce(composite_score, 0.0)`。新節點 `_score = 0.0` → **同收藏數時永遠輸給有舊分數的節點**。短期可接受；主排序鍵是 `favorite_count`，影響有限。

### 6. 新的模組相依

`apps.summary.pipeline.assemble` 現在 import `apps.matching.services.topic_relevance`（`get_topic_relevance_policy` / `get_topic_anchor_embedding`）。已驗證**無循環 import**。代表 M6 pipeline 現在會拉進 M3 的 topic-relevance 模組，並在每次 pipeline 執行時 embed 一次議題錨點（`@lru_cache`，每 process 每 anchor 只算一次）。

### 7. 離題門檻的設定來源 —— 每個議題要設 anchor

離題門檻讀 `TOPIC_CONFIGS[topic_id]["off_topic_detection"]` 的 `anchor_text` / `threshold`（跟 M4 即時離題提示同一把尺，`threshold` 預設 0.35）。

- **某議題沒設 `anchor_text`（也沒 `topic_description`）→ M6 的離題門檻對該議題是 no-op**（所有發言都當在題）。要讓某議題在 M6 也有離題過濾，就得把 anchor 文字補上。
- 缺 `MatchMessage.embedding` / `AIConversation.embedding`（embedding 服務當下失敗）→ 該則一律當「在題」，不受離題把關（刻意，與既有 `ccnd_semantic_dist` 的處理一致）。

### 8. 用假 embedding 的測試會被離題門檻整批擋掉

離題門檻比對「發言 embedding」與「議題錨點的**真實** sentence-transformer 向量」。若測試 fixture 用手刻的正交假向量（`[1,0,0,…]` / `[0,1,0,…]`，常見於精準控制 `ccnd_semantic_dist`），跟真實錨點不在同一語意空間 → 會被整批誤判離題 → pipeline 產出 0 筆觀點。

- 這次已在 `tests_viewpoint_review_hai.py` 與 `tests_m6_trigger_on_close.py` 用
  `mock.patch("apps.summary.pipeline.assemble._message_is_off_topic", return_value=False)` 修掉。
- **之後任何跑 `run_pipeline_for_match` / `run_pipeline_for_session` 且用假 embedding 的新測試，都要比照 mock 掉這個函式。**

### 9. 兩個門檻值未校準

`MIN_LEXICAL_RICHNESS = 0.3`、離題 `threshold = 0.35`。邏輯正確，鬆緊度沒對真實 M6 語料驗過：

- 詞彙豐富度 0.3 是防灌水地板（`不要不要不要…` ≈ 0.07 被擋；正常句 ≈ 0.9+），但**可能誤傷「短又用詞精簡」的在題發言**。
- 建議：上線前拿一批真實已結束的 H-H / H-AI 對話跑一次 pipeline，看這兩道門檻各刷掉多少、刷掉的是不是真的該刷。

### 10. 尚未完成的驗證

- 只跑了 M6 pipeline 相關測試檔（`tests_pipeline` / `tests_assemble` / `tests_viewpoint_review` / `tests_viewpoint_review_hai` / `tests_m6_trigger_on_close`），全綠。
- **沒跑完整後端 test suite**（`assemble.py` 多的 import 理論上不影響其他模組，但沒完整驗證）。
- **沒有「真實 embedding + 真的有離題發言」跑完整 pipeline 的整合測試**（H-H / H-AI 都沒有）—— 零件都測了，整合的真實路徑沒測。
- 前端未 build / lint（clone 沒裝 `node_modules`）。

### 11. Rollback

`git checkout -- backend/apps/summary/pipeline/quality_filter.py backend/apps/summary/pipeline/assemble.py backend/scripts/seed_knowledge_base.py backend/apps/summary/tests_pipeline.py backend/apps/summary/tests_assemble.py backend/api/tests_viewpoint_review_hai.py backend/apps/matching/tests_m6_trigger_on_close.py`
即還原到含 Step 3 的版本。

---

## 附錄：本次 session 其他已 `push` 到 `feat/polarbear` 的 M6 相關 commit

這幾個已在遠端，跟上面「未 commit」的 pipeline 改動是分開的：

| commit | 內容 | 整合注意 |
|---|---|---|
| `394e073` `feat(m6)` | 公開觀點排序改用 `favorite_count`（小卡「被收藏 N 次」），`browse` 不帶 `topic_id` 不再 400（＝前端「全部」分類），`ViewpointBrowsePagination.max_page_size` 50→500，順修一個 stale 的影片 url 測試 | `_serialize_viewpoint_rows` 每筆多帶 `favorite_count`；`ViewpointHighlightSerializer` 多一個 required 欄位 |
| `0afd7e7` `feat(m6)` | 前端知識庫整包：「全部」分類、「觀看更多」每頁固定 6 張卡、跨頁快取 + 預抓 | 純前端 |
| `e0ddd47` `feat(m1)` | 研究參與說明搬進後端：`CONSENT_DOCUMENT` 常數 + `GET /api/consent/`（`AllowAny`），`CONSENT_VERSION` 2026-08-v1 → 2026-09-v1 | 新公開端點要過反向代理白名單；前端 `ConsentPage` 依賴它 |

另外 staging / prod 記得各自跑一次 `python manage.py backfill_hai_viewpoints`（H-AI 進 M6 審核的一次性回填，冪等）。
