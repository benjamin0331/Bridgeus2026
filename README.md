# 概念認知網路圖實作

這個專案用「文本嵌入 + 句子摘要」來構建概念網路，並用 D3 力導向圖可視化語意距離。

#目前施工中，似乎被我搞到歹去，目前還只能在colab上運行

## 一、專案功能
1. 將問卷/評論句子擷取為核心觀點。
2. 計算觀點與議題的相似度，建節點與連線。
3. 輸出 `data.json`，可直接在 `test.html` 用 D3 呈現互動圖表。

## 二、主要檔案
- `test.html`：前端網路圖可視化（讀 `data.json`，拖曳、縮放、調整距離）。
- `data.json`：網路資料格式（nodes + links）。
- `概念認知網路圖還沒面目全非版.py`：核心生成腳本（ollama 摘要 + SentenceTransformer 文字嵌入 + 連線生成邏輯）。
- `句子分割.py`：文字斷句/前處理工具（如果要自訂斷句可改）。
- `測試用句子.txt`：測試樣本。

## 三、快速使用
1. 安裝 Python 環境（建議 Python 3.10+）。
2. 若要用後端生成 `data.json`：
   - 安裝必要套件（`sentence-transformers`、`scikit-learn`、`ollama` 等）。
   - 在 `概念認知網路圖還沒面目全非版.py` 執行。
3. 開啟 `test.html`（或 `python -m http.server 8000` 後瀏覽 `http://localhost:8000/test.html`）。

## 四、`test.html` 用法重點
- 讀取 `data.json` 的 `nodes` 與 `links`。
- 每個節點用 `circle` + `text`，根據 `size` 繪製。
- 左右滑桿在控制面板動態生成，可調整連線距離。
- 可用滑鼠拖節點，圖會重新排版。

## 五、`data.json` 格式（範例）
```json
{
  "target_issue": "核電",
  "nodes": [
    {"id": "核電", "size": 60, "group": 0, "relevance_score": 1.0},
    {"id": "SMR 小型核反應爐的安全性", "size": 65, "group": 1}
  ],
  "links": [
    {"source": "核電", "target": "SMR 小型核反應爐的安全性", "value": 0.683, "distance": 208.46}
  ]
}
```

## 六、後端生成說明（`概念認知網路圖還沒面目全非版.py`）
- 先用 ollama + prompt 擷取關鍵觀點。
- 用 SentenceTransformer 計算 `target_issue` 與各觀點的 cosine 相似度。
- 依相似度決定是否保留觀點、連接目標、群組、視覺距離。
- 生成 `nodes` 與 `links`，輸出 `data.json`。

## 七、可調參數
- `branch_threshold`：觀點與議題相似度下限，低於此值不納入。
- `stop_threshold`：與現有節點太相近時合併（少節點）機制。
- `visual_distance` 計算公式：`(1 - sim) * 500 + 50`（可調權重）。

## 八、擴充方向
- 加入「點擊節點顯示原始句子」。
- 加入「議題分類與顏色對應」。
- 改為後端 API（可對大量句子批量生成）。
- 用更大中文 embedding 模型提升語意判斷。

## 九、注意事項
- 現在前端是靜態讀取 `data.json`，若要即時互動需要加後端 API。
- ollama prompt 內容與規則變動會影響摘要結果。要穩定請鎖定 prompt。
- 若執行 Python 出錯，先確認依賴套件版本與模型下載狀態。
