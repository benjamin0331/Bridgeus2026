# Custom User Model 正式遷移 Runbook

> 日期：2026-08-18
> 狀態：**演練已通過，等待人工執行正式遷移**
> 設計：[../specs/2026-08-18-custom-user-model-design.md](../specs/2026-08-18-custom-user-model-design.md)
> 計畫：[2026-08-18-custom-user-model-migration.md](2026-08-18-custom-user-model-migration.md)
> 程式碼：commit `efa321f`（model 交接）＋ `57f28b2`（欄位與遷移工具）

---

## 目標資料庫

**`bridgeus_test`** —— 這是現用的正式資料庫（`.env` 的 `DB_NAME`）。

不要弄錯對象：同一台伺服器上還有 `bridgeus`（api migration 停在 `0022`、最後活動 2026-07-30，是舊副本）與 `bridgeus_test_0603`。以 2026-08-18 查證：

| 資料庫 | 帳號 | AI 對話 | 最後活動 | api migration |
|---|---|---|---|---|
| **`bridgeus_test`** | 19 | 497 | 2026-08-17 | `0027_favorite` |
| `bridgeus` | 6 | 68 | 2026-07-30 | `0022_...` |

連線經 Cloudflare tunnel 轉到 `127.0.0.1:5432`。**本機沒有 Postgres**，因此演練庫也建在同一台伺服器上。

`pg_dump` / `pg_restore` / `createdb` 不在 PATH，位於 `/opt/homebrew/opt/libpq/bin/`。

---

## 演練結果（2026-08-18）

以 `bridgeus_test` 的 `pg_dump` 快照（1.9 MB，`-Fc` 格式）還原成 `bridgeus_rehearsal` 後執行完整遷移。

### 遷移前後對照

| 指標 | 遷移前 | 遷移後 | 判定 |
|---|---|---|---|
| `auth_user` 筆數 | 19 | 19 | 一筆不差 |
| `email <> ''` 的筆數 | 0 | — | 無重複值，UNIQUE 加得上去 |
| `email IS NULL` 的筆數 | 0 | 19 | 回填成功 |
| `django_content_type` 的 user 列 | `auth\|user` | `accounts\|user` | 原地轉移成功 |
| `api_aiconversation` 筆數 | 497 | 497 | 未受影響 |
| `django_admin_log` 筆數 | 5 | 5 | 未斷 |
| `auth_user.id` 型別 | `integer` | `integer` | **未變成 bigint** |

### 三個新欄位

```
display_name          character varying           nullable=NO
email_verified_at     timestamp with time zone    nullable=YES
is_research_subject   boolean                     nullable=NO
email                 character varying           nullable=YES  ← 由 NOT NULL 改為可空
```

### 權限轉移

「研究者」Group 的三個權限全部跟著轉移到 `accounts`，且**未**取得 `delete_user`（刻意不開放刪除帳號的既有決定沒有被推翻）：

```
add_user     -> accounts
change_user  -> accounts
view_user    -> accounts
```

### 功能驗證

在演練庫上實測（取 id=1 的帳號，該帳號屬於「研究者」Group）：

- `get_user_model()` 回傳 `accounts.User`，`db_table` 為 `auth_user`
- 既有密碼雜湊演算法 `pbkdf2_sha256` 完整保留
- 重設密碼後 `authenticate()` 成功 → 認證路徑正常
- `user_is_researcher()` 回傳 `True` → 權限判斷正常
- 外鍵關聯查詢正常：該帳號 27 筆 AI 對話、全體 34 份後測問卷、5 位研究者

### 順帶套用的 migration

演練中 `migrate` 同時套用了成就系統的兩支：

```
accounts.0002_repoint_user_content_type   OK
accounts.0003_user_registration_fields    OK
api.0028_userachievement                  OK
api.0029_dialoguesessionrecord_profanity_only_total  OK
```

**這兩者無法分開。** 工作區的程式碼已切換到 `accounts.User`，對正式資料庫執行 `migrate` 必然一次套用兩件事。若要單獨只上成就系統，必須先 checkout 到 `c5f0f4f`。

---

## 正式遷移程序

### 前置

- 確認要動的是 **`bridgeus_test`**
- 確認沒有受試者正在使用平台（最後活動 2026-08-17，安排一個低流量時段）
- 準備好 `export PATH="/opt/homebrew/opt/libpq/bin:$PATH"` 與 `PGHOST`/`PGPORT`/`PGUSER`/`PGPASSWORD`

### 步驟

**1. 公告停機視窗**（預估 5 分鐘以內；演練中遷移本身不到 1 分鐘）

**2. 完整備份 —— 這是唯一的回滾依據**

```bash
pg_dump -d bridgeus_test -Fc -f ~/bridgeus_test_$(date +%Y%m%d_%H%M).dump
ls -lh ~/bridgeus_test_*.dump
```

記下檔案路徑。**沒有這個檔案就不要往下做。**

**3. 記錄遷移前基準值**

```bash
psql -d bridgeus_test -tAc "select count(*) from auth_user;"
psql -d bridgeus_test -tAc "select email, count(*) from auth_user where email <> '' group by email having count(*) > 1;"
psql -d bridgeus_test -tAc "select count(*) from api_aiconversation;"
```

**第二個查詢若有輸出（重複的非空 email），停止。** 必須先人工處理重複值，`0003` 的 UNIQUE 才加得上去。演練時該查詢為空。

**4. 部署程式碼，先不要 migrate**

部署到 `57f28b2`（或之後含此兩個 commit 的版本）。

**5. 寫入 bootstrap 紀錄列**

```bash
cd backend
uv run python manage.py bootstrap_user_swap
```

預期輸出：`已寫入 accounts.0001_initial 的套用紀錄，現在可以執行 migrate。`

不跑這一步的話，下一步會失敗於
`InconsistentMigrationHistory: Migration admin.0001_initial is applied before its dependency accounts.0001_initial`。
而 `migrate --fake` 救不了 —— `check_consistent_history` 在 `migrate.py:118` 是無條件執行、早於 `--fake` 的處理。

**6. 執行遷移**

```bash
uv run python manage.py migrate --noinput
```

預期套用 `accounts.0002`、`accounts.0003`、`api.0028`、`api.0029`。

**7. 驗證（照演練的對照表逐項比對）**

```bash
psql -d bridgeus_test -tAc "select count(*) from auth_user;"                       # 應與步驟 3 相同
psql -d bridgeus_test -tAc "select count(*) from auth_user where email is null;"   # 應等於帳號總數
psql -d bridgeus_test -tAc "select app_label, model from django_content_type where model='user';"  # accounts|user
psql -d bridgeus_test -tAc "select count(*) from api_aiconversation;"              # 應與步驟 3 相同
psql -d bridgeus_test -tAc "select p.codename, ct.app_label from auth_permission p
  join django_content_type ct on p.content_type_id=ct.id
  join auth_group_permissions gp on gp.permission_id=p.id
  join auth_group g on g.id=gp.group_id
  where g.name='研究者' and ct.model='user';"                                       # 三列，皆為 accounts
```

**8. 功能抽查**

- 用一個既有帳號登入（`POST /api/token/`）—— 密碼不變，應該直接可用
- `GET /api/me/` 回傳正確的 `is_researcher`
- 研究者開啟 `/admin/accounts/user/`，確認能看到帳號列表與四個新欄位
- 前端設定頁的帳號管理面板（`GET /api/accounts/`）正常
- `/admin/` 首頁的「最近的動作」仍顯示既有紀錄

**9. 恢復服務**

---

## 回滾

```bash
dropdb bridgeus_test && createdb bridgeus_test
pg_restore -d bridgeus_test ~/bridgeus_test_<timestamp>.dump
```

並把程式碼退回 `c5f0f4f`（`efa321f` 之前）。

回滾面很小：`accounts.0001` 是 fake 的、`0002` 只改一列 metadata，真正產生 DDL 的只有 `0003`（`email` 改可空 + 加 UNIQUE + 三個 `AddField`）與 `api.0028/0029`（建一張表 + 加一欄）。

---

## 已知的無害警告

`pg_dump` 是 18.3、伺服器是 PostgreSQL 14.22，`pg_restore` 會出現：

```
ERROR: unrecognized configuration parameter "transaction_timeout"
warnings: errors ignored on restore: 1
```

這是 `SET transaction_timeout = 0;` 在 PG 14 不存在，只影響那一句 SET，資料完整還原（演練時 19 個帳號一筆不差）。

---

## 遷移之後

`accounts.User` 已具備 `email`（unique、可空）、`email_verified_at`、`is_research_subject`、`display_name`。下一步是自助註冊 endpoint，另有 spec。

在開放註冊之前必須先完成（見帳號審查報告）：登入端點限流、`DEFAULT_PERMISSION_CLASSES` 改 fail-closed、密碼 validator 補傳 `user`、username 唯一性 race。
