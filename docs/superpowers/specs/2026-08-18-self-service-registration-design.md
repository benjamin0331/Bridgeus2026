# 受試者自助註冊 — 設計文件

> 日期：2026-08-18
> 狀態：已核准（brainstorming 階段），待寫實作計畫
> 前置：[2026-08-18-custom-user-model-design.md](2026-08-18-custom-user-model-design.md)（已完成並上線）
> 審查報告：`/Users/light/project/0822報告/2026-08-18_帳號與認證審查_待解決事項報告.md`

## 背景

目前帳號只能由「研究者」透過 `POST /api/accounts/`（`IsResearcher` 把關）代為開立。要讓受試者自己註冊，前置工作已經全部完成：

| 前置項目 | 狀態 | commit |
|---|---|---|
| `accounts.User` 具備 `email`（unique、可空）、`email_verified_at`、`is_research_subject`、`display_name` | 已上線 | `efa321f` / `57f28b2` |
| 登入端點限流、`DEFAULT_PERMISSION_CLASSES` fail-closed、密碼 validator 補傳 `user`、username 唯一性 race | 已完成 | `bba6625` |
| 停用帳號在所有入口一致生效、`UPDATE_LAST_LOGIN` | 已完成 | `abb7200` |

`register` 這個 throttle scope 也已經在 `settings.REST_FRAMEWORK.DEFAULT_THROTTLE_RATES` 裡備好，本 spec 只需要把它掛上新的 view。

## 目標

讓受試者用瀏覽器自行完成註冊，取得可立即使用平台的帳號，並在註冊當下留下研究同意紀錄。

## 不做的事（Out of scope）

以下四項都卡在**專案沒有任何 email 寄送基礎建設**（無 `EMAIL_BACKEND`、無 `send_mail` 使用），不在本 spec：

- **email 驗證信** —— `email_verified_at` 維持永遠是 `None`，欄位已經在，之後要做不用動 migration。
- **忘記密碼** —— 受試者忘記密碼的唯一出路是找研究者用 `POST /api/accounts/<pk>/reset-password/` 重設。60 人規模可承受，但這是明確的營運成本，不是被遺漏的功能。
- **email 變更** —— 註冊後 email 不可自助修改。
- **以 email 登入** —— 登入識別維持 `username`。既有 19 個帳號的 email 都是 `NULL`，改用 email 登入會讓他們全部進不來。

另外不做：

- **不動既有的 `POST /api/accounts/`**（研究者代開帳號）—— 兩條路徑平行存在。研究者代開的帳號不會有 `consent_version`，也維持 `is_research_subject=False`。
- **不做 token blacklist**（審查報告 3.3）—— 那與「忘記密碼」綁定，兩者一起做才有意義。

## 限制條件

1. **沒有 SMTP** —— 決定了上面整組 out of scope。
2. **受試者會在同一個場地集體註冊** —— 直接影響限流設計，見第 6 節。
3. **同意書全文由研究團隊提供** —— 本 spec 定義承載它的機制（一個版本常數 + 一個可連結的全文頁面），文字內容不在工程範圍。

## 設計

### 1. 邊界：註冊放在 `accounts` app

新增三個檔案，掛 `POST /api/register/`：

| 檔案 | 責任 |
|---|---|
| `backend/accounts/serializers.py` | `RegistrationSerializer` |
| `backend/accounts/views.py` | `RegistrationView` |
| `backend/accounts/urls.py` | 路由，由 `BridgeUs_Django/urls.py` include |

**不放進 `api/`**：那裡的 `views.py` 已 4400 行、`serializers.py` 820 行，再長下去不利於閱讀與修改。而 `accounts` 本來就擁有 `User` model，註冊是它的職責。

### 2. `consent_version` 欄位（新 migration）

```python
# accounts/models.py，加在 display_name 之後
    # 使用者註冊時同意的研究說明版本（CONSENT_VERSION）。
    # 空字串 = 沒有同意紀錄——研究者代開的帳號與遷移前的既有帳號都是這一類，
    # 它們本來就不該進入分析樣本。
    consent_version = models.CharField(max_length=32, blank=True)
```

`accounts/migrations/0004_user_consent_version.py`：單一 `AddField`，`default=""`。既有 19 個帳號留空字串。

> 註：這個欄位在 custom user model 的設計階段就已選定，但實作時被遺漏，因此補在這裡。它是純新增欄位，不影響既有資料。

### 3. 同意書版本常數

```python
# backend/accounts/consent.py
"""研究同意說明的版本。

放程式碼常數而不是資料庫，理由與 api.dialogue_topics.TOPIC_CONFIGS、
api.achievements.CATALOG 一致：這是研究設計的一部分，不是使用者產生的內容，
沒有「線上新增一個版本」的需求。

改版規則：同意書內文一旦有實質變動就換一個新字串，不要沿用。
User.consent_version 存的是簽署當下的值，改這裡不會動到既有紀錄。
"""

CONSENT_VERSION = "2026-08-v1"

# 全文頁面的路徑，供前端連結。內容由研究團隊提供。
CONSENT_DOCUMENT_PATH = "/consent"
```

### 4. 註冊契約

```
POST /api/register/
Content-Type: application/json
（AllowAny，掛 register throttle scope）

{
  "username": "subject01",
  "email": "subject01@example.com",
  "password": "...",
  "password_confirm": "...",
  "display_name": "小明",        // 選填，可省略或空字串
  "consent": true
}

201 Created
{
  "user": {
    "id": 20,
    "username": "subject01",
    "display_name": "小明",
    "is_researcher": false
  },
  "access": "...",
  "refresh": "..."
}
```

**註冊成功直接發 token**（自動登入）。受試者少一道手續，降低流失。

**token 必須用 `BridgeUsTokenObtainPairSerializer.get_token(user)` 產生**，不可自己組 `RefreshToken.for_user(user)` —— 後者不會帶 `is_researcher` claim，前端 `getAccessTokenPayload()` 會讀不到，導致剛註冊的使用者與登入的使用者拿到結構不同的 token。

### 5. 驗證規則

| 欄位 | 規則 |
|---|---|
| `username` | 必填；`UnicodeUsernameValidator`；`iexact` 唯一（沿用 `AccountCreateSerializer` 的既有判準，理由見審查報告 4.2） |
| `email` | 必填；`EmailField`；`iexact` 唯一 |
| `password` | 必填；`dj_validate_password(value, user=User(username=..., email=...))` |
| `password_confirm` | 必填；必須與 `password` 相同 |
| `display_name` | 選填；`max_length=50`；未填時存空字串 |
| `consent` | 必填；**必須為 `true`**，否則 400 |

**密碼驗證要同時傳 `username` 與 `email`**：`UserAttributeSimilarityValidator` 預設比對 `username`、`first_name`、`last_name`、`email` 四個屬性，把 email 一起帶上才擋得住「密碼跟自己的信箱很像」。

**`consent_version` 由後端寫入，不接受前端傳值** —— 讓 client 宣稱自己同意了哪一版，等於讓受試者自己填寫實驗紀錄。序列化器不定義這個欄位，`create()` 直接寫 `CONSENT_VERSION`。

**建立使用者要包 `transaction.atomic()`** 並捕捉 `IntegrityError` 轉 400，理由與 `AccountCreateSerializer` 相同（`exists()` 檢查與 `create_user()` 之間的空窗；Postgres 在 `IntegrityError` 後交易會進入 aborted 狀態，需要 savepoint）。username 與 email 兩個唯一約束都要能正確歸因到對應欄位。

### 6. `is_research_subject` 的語意

自助註冊一律設 `is_research_subject=True`。

語意定義為**「自助註冊且留下同意紀錄的人」**。既有 19 個帳號與日後研究者代開的帳號維持 `False` —— 它們是研究團隊與測試帳號，本來就不該進分析樣本。

這讓資料分析時 `User.objects.filter(is_research_subject=True)` 直接就是有效樣本，不必再回頭比對名單。

### 7. 限流：預設值必須容得下一整班

`register` scope 預設由 `5/hour` 改為 **`60/hour`**。

**理由**：DRF 對未認證請求依 **IP** 計數。受試者會在同一個場地集體註冊，共用 NAT 出口 IP —— `5/hour` 會讓第 6 位受試者開始就註冊失敗，直接毀掉一次資料收集。

這是刻意放寬的安全取捨：本平台不是公開商業服務、URL 未對外宣傳，真正要防的是隨機掃描而非有組織的濫用。`THROTTLE_REGISTER` 環境變數已存在，真的遇到濫用可以隨時調低而不需改程式碼。

`login` scope 維持 `10/min`，不受影響（那是 per-IP 但時間窗短，集體登入不會撞到）。

### 8. `MeView` 補 `display_name`

`GET /api/me/` 的回應加上 `display_name`，讓前端設定頁不必另外開一支端點。這是既有 view 的單欄位擴充，不改變其他行為。

### 9. 前端

| 檔案 | 改動 |
|---|---|
| `frontend/src/pages/RegisterPage.jsx` + `.css` | 新增註冊頁 |
| `frontend/src/api/auth.js` | 新增 `register()` |
| `frontend/src/App.jsx` | 加 `/register` 路由（未登入可達） |
| `frontend/src/pages/LoginPage.jsx` | 加「還沒有帳號？立即註冊」連結 |

**註冊成功後的處理與 `LoginPage` 一致**：把 `access`／`refresh`／`bridgeus_user` 寫入 localStorage，呼叫 `setUser()`，導向 `/`。這段邏輯目前在 `LoginPage.jsx` 內，註冊頁會需要同一份 —— 抽成 `src/api/auth.js` 的一個共用函式，兩邊都用它，避免兩處各自維護「登入成功要做什麼」。

**錯誤處理**：
- `400` —— 逐欄位顯示後端訊息（username／email 已使用、密碼太弱、兩次密碼不一致、未勾同意）
- `429` —— 「註冊嘗試過於頻繁，請稍候再試」
- `5xx` / 網路 —— 沿用 `LoginPage` 既有的兩段式訊息

**同意方塊**：未勾選時送出按鈕停用，並連結到同意書全文（`CONSENT_DOCUMENT_PATH`）。全文內容由研究團隊提供，工程上只需要一個可連結的頁面。

## 測試計畫

`backend/accounts/tests_registration.py`：

**成功路徑**
- 註冊成功回 201，且回應的 `access` 可直接用於 `GET /api/me/`
- `consent_version` 寫入的是 `CONSENT_VERSION` 常數值
- `is_research_subject` 為 `True`
- `display_name` 省略時存空字串
- access token 帶有 `is_researcher` claim（值為 `false`）

**拒絕路徑**
- `consent` 為 `false` 或缺漏 → 400
- 兩次密碼不一致 → 400
- 密碼等於 username → 400
- 密碼與 email 太相似 → 400
- username 重複（含只差大小寫）→ 400
- email 重複（含只差大小寫）→ 400
- 前端傳入 `consent_version` 或 `is_research_subject` → 被忽略，不影響落庫值

**限流**
- 超過 `register` scope 上限回 429（比照 `tests_auth_hardening.py` 的作法直接 patch `ScopedRateThrottle.THROTTLE_RATES`，**不可用 `override_settings`** —— `SimpleRateThrottle` 在 class body 就把 `THROTTLE_RATES` 綁成類別屬性，`override_settings` 影響不到它，測試會安靜地跑在正式值上）

**既有行為不受影響**
- `POST /api/accounts/`（研究者代開）建立的帳號 `consent_version` 為空字串、`is_research_subject` 為 `False`
- `GET /api/me/` 回傳 `display_name`

前端：`npm run lint` 與 `npm run build` 通過。

## 風險

| 風險 | 緩解 |
|---|---|
| 沒有忘記密碼，受試者卡住就得找研究者 | 已知並接受；註冊頁的密碼欄位提供「顯示密碼」切換，降低打錯機率 |
| email 打錯不會被發現 | 已知；日後做驗證信時可用 `email_verified_at IS NULL` 找出全部未驗證帳號 |
| 限流放寬到 60/hour 後被濫用 | `THROTTLE_REGISTER` 可即時調低；帳號可停用（停用已在所有入口生效） |
| 同意書全文未就緒導致無法上線 | 工程與內容解耦：頁面先放佔位文字即可完成實作與測試，內容替換不需改程式碼 |

## 後續

本 spec 完成後，剩餘的帳號相關工作只有兩項，且互相獨立：

1. **email 驗證 + 忘記密碼** —— 需要先決定 SMTP 方案（自架、SendGrid、或校內信箱轉發）。決定之後兩者可一起做，因為共用同一套寄信與一次性 token 機制。
2. **token blacklist**（審查報告 3.3）—— 與忘記密碼綁定，一起做才有意義。
