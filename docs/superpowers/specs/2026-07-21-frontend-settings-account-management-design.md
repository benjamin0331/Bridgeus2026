# 前端設定介面 — 研究者帳號管理面板 設計文件

> 日期：2026-07-21
> 狀態：已核准（brainstorming），待寫實作計畫
> 相關：延續 [2026-07-21-supervisor-account-management-design.md](2026-07-21-supervisor-account-management-design.md)（Django-admin 版），本文件把帳號管理搬到前端。

## 背景

上一輪讓「研究者」Django Group 成員（Supervisor）能透過 **Django Admin** 管理帳號。使用者希望改用**前端介面**：Sidebar 的齒輪圖示點下去進到設定頁，直接操作帳號管理，不必開 Django admin。

現況：
- Sidebar 的 `settings.png`（[Sidebar.jsx:55](../../../frontend/src/components/Sidebar.jsx)）是純靜態 `<img>`，**沒有 onClick**，點了沒反應。
- 後端**沒有任何帳號管理的 REST API**，帳號管理只存在於 Django admin。
- 前端研究者專用頁 pattern：page 元件 + `App.jsx` route + Sidebar 按鈕（`isResearcher` 才顯示），後端 `IsResearcher` 把關；`isResearcher` 來自 JWT 的 `is_researcher` claim（[LoginPage.jsx](../../../frontend/src/pages/LoginPage.jsx)、[serializers.py](../../../backend/api/serializers.py) `BridgeUsTokenObtainPairSerializer`）。
- `RESEARCHER_GROUP_NAME = "研究者"`；加入/移出該 Group 會由上一輪的 `m2m_changed` signal 自動連動 `is_staff`（[api/signals.py](../../../backend/api/signals.py)）。
- 密碼驗證：專案已設定 `AUTH_PASSWORD_VALIDATORS`（min length、UserAttributeSimilarity、common/numeric，見 [settings.py](../../../backend/BridgeUs_Django/settings.py)），新增/重設密碼要套用這組驗證。

## 目標

1. Sidebar 齒輪可點，導到 `/settings`。
2. `/settings`：研究者看到帳號管理面板；非研究者看到「尚無設定項」佔位（之後一般個人設定的預留入口）。
3. 帳號管理面板支援：**新增帳號**、**停用/啟用**、**升為/取消研究者**、**重設某帳號密碼**。
4. 後端提供對應 REST API，全部用 `IsResearcher` 把關。

## 不做的事（Out of scope）

- 一般個人設定（改自己的密碼、顯示名稱）。這輪只放佔位。
- 真正刪除帳號（沿用先前決定，只停用）。
- 編輯帳號的 email / 顯示名稱 / first_name 等 profile 欄位。
- 不動 Django admin 版（兩者並存；admin 仍可用）。

## 安全護欄（已核准）

這是新的、對前端開放的權限操作面，比 Django-admin 那輪嚴一點：

1. **不能對 superuser 帳號動作**（停用、改密碼、升降研究者都拒絕）——否則任何研究者都能重設 superuser 密碼接管系統。回 403 並附訊息。
2. **不能停用自己、也不能取消自己的研究者身分**——避免把自己鎖在外面。回 400 並附訊息。
3. 其餘（研究者之間互改、對一般受試者操作）維持開放，符合先前「不要太嚴格」取向。

## 後端設計

全部放在既有 `api` app，路徑前綴 `accounts/`（沿用現有複數名詞風格），權限 `IsResearcher`。

### Serializers（`api/serializers.py`）

- `AccountListSerializer`（唯讀）：`id`、`username`、`is_active`、`is_researcher`（用 `SerializerMethodField`，判斷是否屬於「研究者」Group）、`is_superuser`（唯讀，給前端決定要不要 disable 該列的操作鈕）、`last_login`、`date_joined`。
- `AccountCreateSerializer`：`username`（必填、唯一）、`password`（必填、`write_only`，用 `django.contrib.auth.password_validation.validate_password` 驗證）、`is_researcher`（`BooleanField`，`required=False`，`default=False`）。`create()` 用 `User.objects.create_user(...)`；若 `is_researcher` 為真，建立後把使用者加入「研究者」Group（signal 會處理 `is_staff`）。
- `AccountUpdateSerializer`：`is_active`（`required=False`）、`is_researcher`（`required=False`）。只更新有帶的欄位。改 `is_researcher` = 加入/移出「研究者」Group。
- `PasswordResetSerializer`：`password`（必填、`write_only`、`validate_password`）。

### Views（`api/views.py`）

- `AccountListCreateView(ListCreateAPIView)`：
  - `permission_classes = [IsResearcher]`
  - `GET`：列出所有帳號，`AccountListSerializer`，排序 `-date_joined`（或 `-last_login`）。
  - `POST`：`AccountCreateSerializer` 建立帳號，回 `AccountListSerializer` 形狀。
- `AccountDetailView(APIView)`：
  - `permission_classes = [IsResearcher]`
  - `PATCH /accounts/<int:pk>/`：套用護欄（目標是 superuser → 403；目標是自己且要停用或取消研究者 → 400），用 `AccountUpdateSerializer` 更新，回 `AccountListSerializer`。
- `AccountPasswordResetView(APIView)`：
  - `permission_classes = [IsResearcher]`
  - `POST /accounts/<int:pk>/reset-password/`：目標是 superuser → 403；`PasswordResetSerializer` 驗證後 `user.set_password(...)` + `save()`。回 200 簡單訊息（不回密碼）。

護欄判斷共用一個小 helper（例如 `_guard_account_target(request_user, target, *, action)`）避免重複。

### URLs（`api/urls.py`）

```
path('accounts/', views.AccountListCreateView.as_view()),
path('accounts/<int:pk>/', views.AccountDetailView.as_view()),
path('accounts/<int:pk>/reset-password/', views.AccountPasswordResetView.as_view()),
```

## 前端設計

### Sidebar（`src/components/Sidebar.jsx`）
把齒輪那個靜態 `<img src="/settings.png">` 換成 `<button className="sidebar-icon-btn" onClick={() => handleNavigate('/settings')}>`（內含同一張 icon），比照相鄰的成就/歷史按鈕寫法。齒輪對所有人可見。

### Route（`src/App.jsx`）
新增 `<Route path="/settings" element={<SettingsPage user={user} />} />`。與 `/viewpoint-review` 同層，任何登入者連得到，實際控制在後端。

### SettingsPage（`src/pages/SettingsPage.jsx` + `.css`）
- 若 `!user.isResearcher`：顯示「尚無設定項」佔位卡片。
- 若 `user.isResearcher`：帳號管理面板，排版風格沿用 `ViewpointReviewPage`（清單 + 操作）：
  - **帳號清單**：以表格/卡片列出 username、狀態（啟用/停用）、是否研究者、最後登入、建立時間；每列操作鈕：停用/啟用切換、升/取消研究者切換、重設密碼。`is_superuser` 的列把操作鈕 disable（前端提示；後端也會擋）。
  - **新增帳號**：表單（username、初始密碼、是否研究者 checkbox），送 `POST /api/accounts/`。
  - **重設密碼**：點某列「重設密碼」→ 跳出輸入框（inline 或 modal）→ `POST /api/accounts/<id>/reset-password/`。
  - 每個操作後重新載入清單（或就地更新該列）。錯誤（403/400/驗證失敗）顯示後端 `detail` 或欄位訊息。
- API 一律透過既有 `src/api/client.js`（帶 JWT、自動 refresh）。

## 測試計畫

### 後端（`api/tests_account_management.py`）
- 權限：非研究者 `GET/POST/PATCH/reset-password` 皆 403；研究者可用。
- 建立：研究者建一般帳號（預設非研究者、`is_active=True`）；建研究者帳號 → 目標進入「研究者」Group 且 `is_staff=True`（signal 生效）；密碼太弱 → 400（validate_password）；重複 username → 400。
- 更新：停用/啟用切換；升為研究者（加入 Group、`is_staff` 連動）、取消研究者（移出 Group、`is_staff` 連動、非 superuser）。
- 護欄：對 superuser PATCH/reset-password → 403；停用自己 → 400；取消自己研究者 → 400。
- 重設密碼：成功後舊密碼失效、新密碼可登入（或 `check_password`）；太弱 → 400。

### 前端
沿用專案現有前端測試慣例（若有）；至少手動驗證：齒輪可點、非研究者看到佔位、研究者看到面板且四種操作可用、superuser 列操作鈕 disable。

## 影響範圍

- 後端：`api/serializers.py`、`api/views.py`、`api/urls.py` 新增；`api/permissions.py`（`IsResearcher` 沿用，不改）；新測試檔。
- 前端：新增 `src/pages/SettingsPage.jsx`、`SettingsPage.css`；改 `src/App.jsx`（route）、`src/components/Sidebar.jsx`（齒輪按鈕）。
- 不動 Django admin 版帳號管理、不動 `IsResearcher`、不動 signal/migration。
