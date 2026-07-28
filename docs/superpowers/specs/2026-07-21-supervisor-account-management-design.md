# Supervisor 帳號管理 — 設計文件

> 日期：2026-07-21
> 狀態：已核准（brainstorming 階段），待寫實作計畫

## 背景

目前系統只有兩種身分：一般使用者（受試者）與「研究者」Django Group（`RESEARCHER_GROUP_NAME = "研究者"`，見 [api/permissions.py](../../../backend/api/permissions.py)，由 [0013_create_researcher_group.py](../../../backend/api/migrations/0013_create_researcher_group.py) 建立）。`IsResearcher` 權限判斷用 Group 而非 `is_staff`，兩者刻意解耦——研究者不會因為有 `IsResearcher` 權限就自動能登入 `/admin/`。

沒有任何帳號管理介面：無法新增帳號、停用帳號、查看帳號列表。

## 目標

讓「研究者」Group 成員（= Supervisor）可以透過 Django Admin：
1. 新增帳號
2. 停用帳號
3. 編輯帳號資料
4. 查看帳號列表與最後登入時間
5. 把其他使用者加入「研究者」Group，使其也成為 Supervisor

不新增獨立的「Supervisor」角色層級——直接擴充現有「研究者」Group 的能力。

## 不做的事（Out of scope）

- **不開放刪除帳號**：`User` 被大量實驗資料（`AIConversation`、`MatchMessage`、問卷回應等）引用，cascade 刪除會不可逆地清掉實驗數據。這次只開放「停用」（`is_active=False`）。需要真正刪除時，由現有的 superuser 帳號在 shell 或 Django admin 手動操作，不在 Supervisor 的日常介面裡提供。
- **不做權限邊界限縮**：沿用 Django 預設 `UserAdmin`，Supervisor 可以看到並編輯 `is_superuser`、`user_permissions` 等欄位。不另外隱藏或限制這些欄位。
- **不做獨立登入稽核表**：沿用 `User.last_login`（Django 內建），不建立逐筆登入事件記錄。
- **不動一般受試者的自助註冊流程**：Supervisor 用 admin 新增帳號，跟受試者自助註冊是兩條平行路徑，互不影響、互不取代。
- **不動前端**：純後端／Django Admin 範圍的改動。
- **不動 `IsResearcher` 現有的權限判斷邏輯**：仍然是「看 Group 不看 is_staff」。

## 設計

### 1. Django Admin 權限指派（migration）

新增一支 migration，比照 [0013_create_researcher_group.py](../../../backend/api/migrations/0013_create_researcher_group.py) 的手寫風格，把以下權限指派給「研究者」Group：

- `auth.add_user`
- `auth.change_user`
- `auth.view_user`
- `auth.view_group`

**不**指派 `auth.delete_user`（呼應「不開放刪除」）。

`RunPython` 需提供 reverse function（移除這些權限指派），維持與現有 migration 一致的可逆性慣例。

### 2. Group ↔ `is_staff` 自動同步（signal）

新增 signal 檔案（例如 `backend/api/signals.py`，於 app `ready()` 中註冊），監聽 `User.groups.through` 的 `m2m_changed`：

- 當「研究者」Group 的成員發生變化（`post_add` / `post_remove`）：
  - 加入該 Group 的使用者 → 若 `is_staff` 不是 `True`，設為 `True`（讓其能登入 `/admin/`）。
  - 移出該 Group 的使用者 → 若該使用者 `is_superuser` 為 `False`，把 `is_staff` 設回 `False`；若 `is_superuser` 為 `True` 則不動（避免影響真正的 superuser 帳號）。

行為只針對「研究者」這個 Group 名稱觸發，其他 Group 的異動不受影響。

### 3. 帳號列表可視性

Django 內建 `UserAdmin` 的 list view 預設欄位已包含使用者名稱、email、`is_staff`、`is_active`；額外把 `last_login` 加進 `list_display`（可依此欄排序），滿足「查看帳號列表與登入紀錄」的需求，不需要客製化 `ModelAdmin` 之外的邏輯。

若專案目前未自訂 `UserAdmin`（沿用 Django 預設註冊），需要在 `backend/api/admin.py`（或適當位置）unregister 預設 `UserAdmin` 再 register 一個加了 `last_login` 到 `list_display` 的版本。

### 4. 新帳號預設值

Supervisor 在 admin 新增帳號時，沿用 Django admin 新增使用者表單的預設行為：新帳號預設不勾「研究者」Group、`is_staff=False`（即一般受試者身分）。需要建立研究者/Supervisor 帳號時，Supervisor 手動勾選「研究者」Group，交由上述 signal 自動連動 `is_staff`。

## 測試計畫

在 `backend/api/tests.py` 或新檔案（例如 `backend/api/tests_supervisor_admin.py`）覆蓋：

1. **Signal 行為**
   - 使用者被加入「研究者」Group → `is_staff` 變 `True`
   - 使用者被移出「研究者」Group → `is_staff` 變 `False`
   - `is_superuser=True` 的使用者被移出「研究者」Group → `is_staff` 維持 `True`（不受影響）
   - 加入/移出其他（非研究者）Group → `is_staff` 不受影響

2. **Migration 授權的權限**
   - 「研究者」Group 成員擁有 `auth.add_user` / `auth.change_user` / `auth.view_user` / `auth.view_group`
   - 「研究者」Group 成員**沒有** `auth.delete_user`

3. **Admin list_display**
   - （可選、輕量）確認自訂後的 `UserAdmin.list_display` 包含 `last_login`

## 影響範圍

- `backend/api/permissions.py`：不變動，沿用既有 `RESEARCHER_GROUP_NAME`
- 新增：`backend/api/signals.py`（或等效位置）
- 新增：一支 migration（授權 Group 權限）
- 新增或修改：`backend/api/admin.py`（自訂 `UserAdmin.list_display`）
- 新增：測試檔案
- 不動前端、不動 `IsResearcher`、不動受試者自助註冊流程
