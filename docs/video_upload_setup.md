# 知識庫影片上傳 — 部署設定

> 對應改動：影片 `url` 改存**根相對路徑**（`/media/kb_videos/…`），不再把上傳當下的
> host/scheme 寫死進 DB。因此每個環境都要讓 `/media/` 這條路徑能打到 Django 後端。

相關程式碼：

| 檔案 | 角色 |
|------|------|
| `backend/apps/summary/models.py` `VideoRecommendation` | `video_file` = `FileField(upload_to="kb_videos/%Y/%m/")`；`url` 下游唯一讀取欄位 |
| `backend/api/views.py` `_fill_video_url_from_file` | 上傳後自動把 `url` 補成 `instance.video_file.url`（根相對路徑） |
| `backend/BridgeUs_Django/urls.py` `^media/…` → `serve_media` | Range-aware 檔案服務，DEBUG / 非 DEBUG 都掛 |
| `backend/BridgeUs_Django/media_views.py` `serve_media` | 支援 HTTP Range，`<video>` 才能拖時間軸 |
| `frontend/src/pages/KnowledgeBase.jsx` | `<video src={playingVideo.url}>` |

上傳端點（皆 `IsResearcher`）：

- `POST   /api/summary/videos/admin/`（新增，multipart，欄位 `title` + `video_file`）
- `PATCH  /api/summary/videos/admin/<pk>/`（更新／重新上傳／發布切換）
- `GET    /api/summary/videos/admin/`（研究者清單，含未發布）
- `GET    /api/summary/videos/`（公開清單，只回 `is_published=True`）

---

## 1. 後端（`backend/.env`）

**沒有新增環境變數。** 確認以下既有值即可（`settings.py` 內建預設，通常不必寫進 `.env`）：

```env
# settings.py 已寫死，列出供對照
# MEDIA_URL  = /media/
# MEDIA_ROOT = <backend>/media
```

檔案系統：

- `backend/media/` 目錄由 Django 於首次上傳時自動建立；正式機請確認 **執行 uvicorn 的使用者對該目錄有寫入權限**。
- `backend/media/` 已在 `.gitignore`（`media/`），不進版控。
- 備份策略：`backend/media/kb_videos/` 需納入伺服器備份範圍（DB 只存路徑，檔案本體在這裡）。
- 之後若改用物件儲存（S3/GCS），把 `DEFAULT_FILE_STORAGE` 換掉即可，`urls.py` 的 `^media/…` 路由與 `serve_media` 可一併移除（物件儲存原生支援 Range）。

---

## 2. 本機開發

`frontend/vite.config.js` 已新增 `/media` proxy，指向 `VITE_PROXY_TARGET`（預設 `http://127.0.0.1:8005`）。

```bash
# 後端（要能服務 /media/，用 uvicorn 或 runserver 都可）
cd backend
uv run uvicorn BridgeUs_Django.asgi:application --host 0.0.0.0 --port 8005

# 前端
cd frontend
npm run dev        # http://localhost:5173，/api 與 /media 都會 proxy 到 8005
```

改了 `vite.config.js` 要**重啟 `npm run dev`** 才生效。

---

## 3. Docker（`frontend/nginx/default.conf.template`）

已新增：

```nginx
location /media/ {
  proxy_pass http://${BACKEND_HOST}:${BACKEND_PORT};
  proxy_http_version 1.1;
  proxy_set_header Host $host;
  proxy_set_header X-Real-IP $remote_addr;
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_set_header X-Forwarded-Proto $scheme;
}
```

需要的環境變數（`frontend/docker-compose.yml` 既有，沿用即可）：

| 變數 | 說明 |
|------|------|
| `BACKEND_HOST` | 後端容器／主機名（如 `host.docker.internal`） |
| `BACKEND_PORT` | 後端埠（`8005`） |

重建前端容器讓 template 重新展開：

```bash
cd frontend
docker compose up --build
```

上傳大檔時若 nginx 回 `413 Request Entity Too Large`，在該 `server {}` 內加：

```nginx
client_max_body_size 500m;   # 依允許的最大影片大小調整
```

---

## 4. 正式站 — Cloudflare Tunnel ingress（**需手動設定，不在此 repo**）

整個 stack 在同一個網域下用路徑分流。`/media/` 必須跟 `/api/`、`/ws/` 一樣轉到 Django 後端。

`cloudflared` 的 `config.yml` ingress 範例（依實際 service 位址調整）：

```yaml
ingress:
  - hostname: dev.bridgeus.work
    path: ^/(api|ws|media)/
    service: http://127.0.0.1:8005
  - hostname: dev.bridgeus.work
    path: ^/godot-ws
    service: ws://127.0.0.1:8085
  - hostname: dev.bridgeus.work
    service: http://127.0.0.1:8080      # 前端靜態站（含 SPA fallback）
  - service: http_status:404
```

> 用 dashboard 設 ingress 的話，新增一條 **Path = `/media/`（或 regex `^/media/`）→ 後端 service**，
> 位置排在「前端 catch-all」規則之前。

Django `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS` 內含 `dev.bridgeus.work` 即可（`backend/.env` 現況已包含）。

---

## 5. 驗證清單

上線後依序確認：

- [ ] 以研究者帳號登入 → 設定頁「影片管理」→ 上傳一支小 mp4 → 回應 `201`，清單出現該筆。
- [ ] DB 檢查：`VideoRecommendation.url` 形如 `/media/kb_videos/2026/08/xxx.mp4`（**開頭是 `/media/`，沒有 `http(s)://` 與主機名**）。
- [ ] 瀏覽器直接開 `https://<站台>/media/kb_videos/.../xxx.mp4` → 能下載／播放，回應 header 有 `Accept-Ranges: bytes`。
- [ ] 知識庫首頁把該影片設為發布 → 前台 `<video>` 能播放、可拖時間軸、有聲音。
- [ ] 拖時間軸時 DevTools Network 看到對該檔案的請求回 `206 Partial Content`。

## 疑難排解

| 症狀 | 原因 | 處理 |
|------|------|------|
| `<video>` 404 | `/media/` 沒轉到後端 | 檢查對應環境的 proxy/ingress（§2–4） |
| 有畫面沒聲音、不能拖時間軸 | Range 請求沒被正確處理 | 確認走的是 Django `serve_media` 或原生支援 Range 的 nginx／物件儲存，中間 proxy 沒吃掉 `Range`／`Content-Range` header |
| 上傳回 `413` | nginx `client_max_body_size` 太小 | §3 加大 |
| 上傳回 `500`，log 有 `PermissionError` | `backend/media/` 不可寫 | 修正目錄擁有者／權限為執行 uvicorn 的使用者 |
| 舊資料的 `url` 還是 `http://127.0.0.1:8005/media/…` | 改動前上傳的殘留絕對網址 | 手動把這些列的 `url` 改成去掉 `http://host:port` 的相對路徑（或清空後重新 `PATCH` 觸發自動補值） |
