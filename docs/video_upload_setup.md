# 知識庫影片上傳 — 部署設定

> 對應改動：影片 `url` 改存**根相對路徑**（`/media/kb_videos/…`），不再把上傳當下的
> host/scheme 寫死進 DB。因此每個環境都要讓 `/media/` 這條路徑能打到 Django 後端。

相關程式碼：

| 檔案 | 角色 |
|------|------|
| `backend/apps/summary/models.py` `VideoRecommendation` | `video_file` = `FileField(upload_to="kb_videos/%Y/%m/")`；`url` 下游唯一讀取欄位，型別是 `CharField` 而非 `URLField`（存的是相對路徑，URLValidator 會擋） |
| `backend/apps/summary/models.py` pre_save／post_delete signal | 換檔與刪除時把磁碟上的舊檔一併移除 |
| `backend/api/serializers.py` `validate_video_file` | 副檔名白名單 + 大小上限 |
| `backend/api/views.py` `_fill_video_url_from_file` | 上傳後自動把 `url` 補成 `instance.video_file.url`（根相對路徑） |
| `backend/BridgeUs_Django/urls.py` `^media/…` → `serve_media` | Range-aware 檔案服務，DEBUG / 非 DEBUG 都掛 |
| `backend/BridgeUs_Django/media_views.py` `serve_media` | 支援 HTTP Range，`<video>` 才能拖時間軸 |
| `frontend/src/pages/KnowledgeBase.jsx` | `<video src={playingVideo.url}>` |

上傳端點（皆 `IsResearcher`）：

- `POST   /api/summary/videos/admin/`（新增，multipart）
  欄位：`title`（必填）、`video_file`／`url` 擇一（兩者皆空會被 serializer 的
  `validate()` 擋下）、`thumbnail_url`、`description`、`topic_id`、
  `stance_direction`（`support`／`neutral`／`oppose`，預設 `neutral`）、
  `is_published`、`display_order`。設定頁的上傳表單送出 `title`、`video_file`、
  `thumbnail_url`、`description`、`topic_id`（選填）、`stance_direction`。
- `PATCH  /api/summary/videos/admin/<pk>/`（更新／重新上傳／發布切換）
- `DELETE /api/summary/videos/admin/<pk>/`（刪除該筆；磁碟上的檔案本體會由
  `apps/summary/models.py` 的 post_delete signal 一併移除）
- `GET    /api/summary/videos/admin/`（研究者清單，含未發布）
- `GET    /api/summary/videos/`（公開清單，只回 `is_published=True`）

---

## 0. 大小與格式限制（兩層，要一起調）

| 層 | 設定 | 預設 | 超過時使用者看到什麼 |
|----|------|------|--------------------|
| nginx | `client_max_body_size`（`frontend/nginx/default.conf.template`） | `220m` | 沒有 JSON body 的 413；前端已特別處理成「檔案太大，被伺服器擋下來了」 |
| Django | `KB_VIDEO_MAX_MB`（`backend/.env`） | `200` | `400` + 「影片檔太大：xxx MB，上限是 200 MB，請先壓縮再上傳。」 |

nginx 的值**刻意設得比 Django 大一些**：讓只超過一點點的檔案能走到 Django、拿到
講得清楚的訊息，而不是 nginx 那個沒有內容的 413 頁。調整時兩邊要一起改，否則
使用者會拿到錯誤的那一種錯誤訊息。

副檔名白名單 `KB_VIDEO_ALLOWED_EXTENSIONS`（預設 `mp4,webm,mov,m4v`）由
`VideoRecommendationAdminSerializer.validate_video_file()` 強制。這不只是格式
檢查——`MEDIA_ROOT` 底下的檔案是由站台自己的 origin 提供的，放行 `.html`／`.svg`
這類會被瀏覽器當文件執行的格式，等於讓任何研究者帳號在正式站掛上可執行的頁面
（儲存型 XSS）。`serve_media` 另外一律送出 `X-Content-Type-Options: nosniff` 作為
第二道。

---

## 1. 後端（`backend/.env`）

**新增兩個選填環境變數**（`KB_VIDEO_MAX_MB`、`KB_VIDEO_ALLOWED_EXTENSIONS`，見上一節；
不設就用預設值）。除此之外 確認以下既有值即可（`settings.py` 內建預設，通常不必寫進 `.env`）：

```env
# MEDIA_URL 寫死在 settings.py；MEDIA_ROOT 可用環境變數覆寫
# MEDIA_URL  = /media/
MEDIA_ROOT=/srv/bridgeus_media
```

**`MEDIA_ROOT` 在正式機一定要設成 checkout 之外的絕對路徑。** 預設值
`<backend>/media` 會跟著 checkout 走：DB 存的是全域路徑 `/media/kb_videos/…`，
檔案卻落在寫檔那個 process 的 `BASE_DIR` 底下。只要「收到上傳的 process」跟
「之後對外服務的 process」不是同一個目錄（多份 checkout、換分支、重建容器而
沒掛 volume），播放就會 404，而檔案其實還在原本那個目錄裡。設成共用的絕對
路徑之後，換分支或重新部署都不會再弄丟已上傳的影片。

檔案系統：

- `backend/media/` 目錄由 Django 於首次上傳時自動建立；正式機請確認 **執行 uvicorn 的使用者對該目錄有寫入權限**。
- `backend/media/` 已在 `.gitignore`（`media/`），不進版控。
- 備份策略：`backend/media/kb_videos/` 需納入伺服器備份範圍（DB 只存路徑，檔案本體在這裡）。
- 之後若改用物件儲存（S3/GCS），改的是 `settings.STORAGES["default"]`——本專案跑
  Django 6.0，舊的 `DEFAULT_FILE_STORAGE` 設定在 5.1 已移除，寫了不會報錯但完全
  不生效：

  ```python
  STORAGES = {
      "default": {"BACKEND": "storages.backends.s3.S3Storage"},
      "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
  }
  ```

  （順帶一提，`settings.py:257` 現存的 `STATICFILES_STORAGE` 同樣是 5.1 移除的
  舊名，目前是失效狀態；真的改成 `STORAGES` 時要一起收進上面這個 dict。）

  換過去之後 `urls.py` 的 `^media/…` 路由與 `serve_media` 可一併移除（物件儲存
  原生支援 Range）。

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

`client_max_body_size 220m;` 已經寫在 template 的 `server {}` 內（見 §0）。
要調整上限時記得跟 `KB_VIDEO_MAX_MB` 一起改。

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
| 上傳回 `413` | 檔案超過 nginx 的 `client_max_body_size`（已設 `220m`） | 確認檔案大小；真要放寬就同時調 nginx 與 `KB_VIDEO_MAX_MB`（§0） |
| 上傳回 `400`「影片檔太大」 | 超過 `KB_VIDEO_MAX_MB` | 壓縮影片，或調高該值並同步放寬 nginx |
| 上傳回 `400`「只接受這些格式」 | 副檔名不在白名單 | 轉成 mp4／webm，或調整 `KB_VIDEO_ALLOWED_EXTENSIONS` |
| 上傳回 `500`，log 有 `PermissionError` | `backend/media/` 不可寫 | 修正目錄擁有者／權限為執行 uvicorn 的使用者 |
| 舊資料的 `url` 還是 `http://127.0.0.1:8005/media/…` | 改動前上傳的殘留絕對網址 | 手動把這些列的 `url` 改成去掉 `http://host:port` 的相對路徑（或清空後重新 `PATCH` 觸發自動補值） |
