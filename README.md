努力吧各位

T_T

1.命名原則 

全小寫與連字號 (Kebab-case)：檔案名稱一律使用小寫，單字間以 - 分隔。避免不同作業系統（Windows vs. Mac/Linux）對大小寫敏感度的 Bug。
 
 語義化優先：捨棄 test1.py 或 final_v2.docx 這種低頻訊號。

 禁止中文：所有路徑與檔名禁止出現中文，防止環境編碼炸裂。
---

2.程式碼命名邏輯 (Codebase Architecture)針對 bridge² 的 Django 架構 ，我們採用「模組導向」的命名方式：

 後端 (Python/Django) 
     Models: models-[module].py (例如: models-user.py, models-chat.py )
     Views: views-[function].py (例如: views-auth.py, views-rag.py )
     Utils (工具類)：utils-nlp.py , utils-vector-db.py 
 
 前端 (HTML/CSS/JS) 
     Components: comp-[name].html (例如: comp-chat-bubble.html)
     Styles: style-[page].css (例如: style-war-room.css )
     Scripts (D3.js)：d3-[purpose].js (例如: d3-topology-graph.js )

---

3.圖片與資產命名 (Assets Convention)
陳彩希從 Figma 匯出的圖片或 UI 元素，請統一依照以下格式 ：[類別]-[頁面]-[描述]-[版本].[副檔名]類別縮寫：img (圖片), icon (圖標), bg (背景), ui (介面截圖)。範例：img-landing-hero-v1.pngui-war-room-mockup-v2.jpg icon-user-avatar-default.svg4. 
 文件與報告命名 (Documentation Registry)這部分主要是為了 3/10 前的格式轉換與後續的論文儲備 ：[類別]-[日期]-[主題].[副檔名]類別：plan (計畫書), report (進度報告), manual (手冊), spec (規格書)。範例：plan-20260310-final-standard.docx (給系上的最終格式)spec-api-json-schema.md (前後端對齊協定)report-weekly-lai-01.pdf (賴則名的進度週報 )

4.GitHub 目錄結構建議 (The Map)Plaintext

bridge-square/

├── docs/               # 所有文檔與計畫書 [cite: 214]

├── src/                # 程式源碼

│   ├── backend/        # Django 邏輯與 RAG 引擎 [cite: 94, 157]

│   ├── frontend/       # HTML/CSS/JS 

│   └── data/           # 文本切片與預處理語料 

├── assets/             # 圖片、Figma 導出、UI 素材 

├── tests/              # 黃筱筑的測試腳本與數據驗證 

└── .gitignore          # 忽略隱私與暫存檔 (如 .env, pycache)
