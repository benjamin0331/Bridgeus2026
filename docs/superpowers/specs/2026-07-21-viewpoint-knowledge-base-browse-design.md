# 觀點知識庫瀏覽（一般使用者）— 設計文件

> 日期：2026-07-21
> 範圍：M6 觀點知識庫（`apps/summary`）現有的研究者審核流程已完成並上線，本設計新增「一般使用者瀏覽已核准觀點」的功能。

## 背景

`ViewpointNode` 的品質篩選、去重、寫入 pipeline（`apps/summary/pipeline/`）已經接線完成，會在 H-H 配對房關閉時自動觸發（`apps/matching/services/matcher.py` 的 `transaction.on_commit`）。研究者可透過 `/viewpoint-review` 頁面（`IsResearcher` 權限）核准或退回候選觀點。

但目前**沒有任何管道讓一般使用者看到已核准的觀點**。前端 Sidebar 上原本保留給觀點知識庫的星星圖示（`/star.png`）只是一張靜態圖片，沒有 `onClick`、沒有路由、從未被實作。本設計要補上這一段：一般使用者可瀏覽的公開觀點知識庫頁面。

## 決策摘要

| 問題 | 決定 |
|------|------|
| 瀏覽範圍 | 所有議題都能瀏覽，不限於使用者自己聊過的議題 |
| 存取權限 | 只要登入即可（`IsAuthenticated`），不需額外條件、不開放未登入訪客 |
| 瀏覽介面 | 清單／卡片瀏覽（議題 → 分類 tab → 觀點卡片），不採用 CCND 力導向圖瀏覽 |
| 卡片內容 | 發言原文（`user_input_text`） + 對方回應（`ai_response_text`）；不顯示 `citation_count`、`composite_score` |
| 卡片排序 | 依內部評分 `composite_score` 由高到低（分數本身不對外顯示，只用來排序） |

## 後端 API 設計

新增兩個唯讀端點，與現有審核用端點（`ViewpointReviewListView`/`ViewpointReviewDecisionView`，權限 `IsResearcher`）完全分開，避免公開瀏覽跟審核專用欄位混在同一份 serializer 裡維護。

### 1. `GET /api/viewpoints/topics/`

- 權限：`IsAuthenticated`
- 回傳所有 `api.dialogue_topics.TOPIC_CONFIGS` 裡定義的議題，依 `display_order` 排序，並附上該議題的分類（anchors，供前端直接產生 tabs，不需要前端另外解析 `TOPIC_CONFIGS`）：
  ```json
  [
    {
      "topic_id": 102,
      "title": "台灣核能議題討論",
      "approved_count": 12,
      "anchors": [
        {"id": "anchor_safety", "name": "核能安全"},
        {"id": "anchor_economy", "name": "經濟成本"}
      ]
    },
    {"topic_id": 103, "title": "女性義務兵役討論", "approved_count": 0, "anchors": [...]}
  ]
  ```
- `approved_count` = 該 `topic_id` 底下 `review_status=approved` 的 `ViewpointNode` 數量
- `approved_count` 為 0 的議題**仍然列出**，不隱藏（見「邊界情況」）
- `anchors` 直接呼叫 `apps.matching.services.semantic_tree.get_topic_anchors(topic_id)`，與 `dimension_label` 查表用同一個函式，確保兩個端點的分類名稱永遠一致

### 2. `GET /api/viewpoints/?topic_id=<id>&dimension=<anchor_id>`

- 權限：`IsAuthenticated`
- `topic_id` 必填；`dimension` 選填（不帶則回傳該議題所有分類的觀點）
- 只回傳 `review_status=approved` 的節點
- 排序：`-composite_score`
- Serializer（新增 `ViewpointPublicSerializer`，與 `ViewpointNodeReviewSerializer` 分開）欄位：
  - `id`
  - `dimension`（anchor id，如 `anchor_safety`）
  - `dimension_label`（人類可讀名稱，查 `apps.matching.services.semantic_tree.get_topic_anchors(topic_id)` 對照表得出，如「核能安全」）
  - `user_input_text`
  - `ai_response_text`
- **不回傳**：`embedding`、`score_detail`、`composite_score`、`citation_count`、`review_status`、`reviewed_by`、`review_notes` 等審核/內部欄位

### 權限與檔案落點

- 兩個 view 加進 `api/views.py`，比照 `ViewpointReviewListView` 的寫法（`generics.ListAPIView` / `APIView`）
- Serializer 加進 `api/serializers.py`
- Route 加進 `api/urls.py`
- 沿用既有 `IsAuthenticated`（DRF 內建），不需要新的 permission class

## 前端設計

### Sidebar 星星按鈕

`frontend/src/components/Sidebar.jsx`：把目前的

```jsx
<img src="/star.png" alt="Favorite" className="utility-icon" />
```

改成跟「成就」「歷史對話」一致的可點擊按鈕：

```jsx
<button
  className="sidebar-icon-btn"
  type="button"
  onClick={() => handleNavigate('/viewpoints')}
  aria-label="觀點知識庫"
  title="觀點知識庫"
>
  <img src="/star.png" alt="" className="utility-icon" />
</button>
```

### 新頁面 `ViewpointBrowsePage.jsx`

路徑：`frontend/src/pages/ViewpointBrowsePage.jsx`（+ 對應 `.css`），路由註冊在 `App.jsx`：

```jsx
<Route path="/viewpoints" element={<ViewpointBrowsePage />} />
```

不需要 `isResearcher` 限制（比照 `/history`、`/achievement`，任何登入使用者都能進）。

頁面結構：

1. **議題下拉選單**：進頁面時打 `GET /api/viewpoints/topics/`，預設選 `display_order` 最小的議題
2. **分類 tabs**：切到某議題後，直接用 `topics` API 回傳的該議題 `anchors` 陣列產生 tabs，不另外呼叫 API、不在前端重複解析 `TOPIC_CONFIGS`
3. **觀點卡片列表**：打 `GET /api/viewpoints/?topic_id=&dimension=`，卡片顯示發言原文 + 對方回應
4. **空狀態**：`approved_count` 為 0 的議題，或某個分類目前沒有卡片，顯示明確文字提示（如「這個分類還沒有已核准的觀點」），不留白

視覺風格沿用專案既有清單／卡片頁面（參考 `HistoryPage.css`／`AchievementPage.css`），不引入新的視覺語言。

## 資料流

```
進頁面
  → GET /api/viewpoints/topics/
  → 選定議題（預設第一個）
  → GET /api/viewpoints/?topic_id=<id>&dimension=<第一個分類>
  → 使用者切換分類 tab
  → 重打 GET /api/viewpoints/?topic_id=<id>&dimension=<新分類>
```

## 邊界情況

| 情況 | 處理方式 |
|------|---------|
| 議題目前 `approved_count=0` | 仍列在下拉選單中；選中後顯示空狀態提示，tabs 不禁用 |
| `dimension` 對不到任何 anchor | 不會發生——`write_viewpoint` 寫入時已用 `_validate_dimension` 擋掉，API 端不需再防禦 |
| 未登入使用者呼叫 API | 回 401，前端沿用現有全域 401 導回登入頁機制 |
| `ai_response_text` 為空字串 | 不會發生——`quality_filter.extract_valuable_pairs` 保證配對必有回應方文字才會被收錄 |

## 測試計畫

**後端**（新增測試，風格比照 `apps/summary/tests_pipeline.py`）：
- 未登入呼叫兩個端點 → 401
- `GET /api/viewpoints/topics/` 只回傳 `TOPIC_CONFIGS` 已定義議題，`approved_count` 計算正確
- `GET /api/viewpoints/` 只回傳 `review_status=approved` 的節點（`pending`/`rejected` 不出現）
- `topic_id`／`dimension` 過濾正確
- 排序依 `composite_score` 降冪
- 回傳欄位不含 `embedding`／`score_detail`／`composite_score`／`citation_count`／`review_status` 等內部欄位

**前端**：手動在瀏覽器跑一次
- Golden path：選議題 → 切分類 tab → 看到卡片
- 空狀態：選一個目前 `approved_count=0` 的議題／分類

## 不在本次範圍內

- `DialogueSummary.summary_text`／`stance_direction`／`viewpoint_summary` 等欄位的填值邏輯（CONTEXT.md 待辦，尚未拍板公式）
- H-AI（真人對 AI）對話進入知識庫 pipeline（目前只收 H-H）
- 觀點按讚／收藏等互動功能（超出「瀏覽」範圍，YAGNI）
- CCND 力導向圖瀏覽介面（本次選定清單／卡片方案）
