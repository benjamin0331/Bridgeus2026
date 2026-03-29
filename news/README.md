# News Crawler

這個專案會用 `Gemini` 擴充關鍵字，再抓取 `Google News RSS` 與新聞內文，最後輸出成 JSON。

## 需求

- Python 3.11+
- Windows PowerShell

## 安裝

```powershell
pip install -r requirements.txt
```

## API Key 設定

專案現在會自動讀取根目錄的 `.env` 檔案。

如果你要自行設定，建立 `.env`：

```dotenv
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-2.5-flash
```

也可以參考：

- [.env.example](/z:/news/.env.example)

## 執行

用 PowerShell 執行：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_crawler.ps1 台灣 重啟核四
```

或直接執行：

```powershell
python .\crawler.py 台灣 重啟核四
```

如果要帶其他參數：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_crawler.ps1 台灣 重啟核四 --max-expanded-keywords 10 --max-news-per-keyword 3 --max-total-results 15
```

## 常用參數

- `--output-dir`: 輸出資料夾，預設 `output`
- `--max-expanded-keywords`: AI 擴展關鍵字數量，預設 `20`
- `--max-news-per-keyword`: 每個關鍵字最多抓幾篇，預設 `5`
- `--max-total-results`: 總輸出上限
- `--sleep-sec`: 每次抓取之間等待秒數，預設 `1.5`
- `--gemini-model`: 指定 Gemini model

## 輸出

輸出會放在 `output/`，通常包含：

```text
output/
  <seed>_YYYYMMDD_HHMMSS_expanded_keywords.json
  <seed>_YYYYMMDD_HHMMSS_news_results.json
```

## 主要檔案

- [crawler.py](/z:/news/crawler.py)
- [run_crawler.ps1](/z:/news/run_crawler.ps1)
- [run_crawler.py](/z:/news/run_crawler.py)
- [env_utils.py](/z:/news/env_utils.py)
- [gemini_keyword_generator.py](/z:/news/gemini_keyword_generator.py)
