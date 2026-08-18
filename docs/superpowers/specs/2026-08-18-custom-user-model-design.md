# Custom User Model 遷移 — 設計文件

> 日期：2026-08-18
> 狀態：已核准；實作計畫見 [../plans/2026-08-18-custom-user-model-migration.md](../plans/2026-08-18-custom-user-model-migration.md)
> 前置報告：`/Users/light/project/0822報告/2026-08-18_帳號與認證審查_待解決事項報告.md`

## 背景

系統從未設定 `AUTH_USER_MODEL`，全專案使用 Django 內建的 `django.contrib.auth.models.User`。目前帳號只能由「研究者」透過 [api/views.py](../../../backend/api/views.py) 的 `AccountListCreateView` 代為開立（見 [2026-07-21-supervisor-account-management-design.md](2026-07-21-supervisor-account-management-design.md)、[2026-07-21-frontend-settings-account-management-design.md](2026-07-21-frontend-settings-account-management-design.md)）。

下一步要開放**受試者自助註冊**（完全公開註冊，email 驗證先不做但需預留接口）。這需要 User 具備 email（唯一）等欄位，而 Django 內建 User 的 `email` 是選填且不唯一。

**時機是關鍵。** Django 官方不支援在 migration 已存在後更換 `AUTH_USER_MODEL`。目前 `settings.AUTH_USER_MODEL` 的外鍵已散佈在 `api`、`chat`、`apps.summary` 三個 app 共 20 處，且 prod Postgres 已有不可清空的正式實驗資料。每多累積一批資料，遷移成本就再往上一階。

## 目標

把 User model 換成專案自有的 `accounts.User`，並加入自助註冊所需的四個欄位，**且不遺失任何既有資料、不破壞既有功能**。

## 不做的事（Out of scope）

這份 spec 只做 model 遷移本身。以下各自獨立，刻意不綁進這次的高風險 migration：

- **註冊 endpoint 本身**、email 驗證信、忘記密碼流程 —— 下一份 spec。本次只把欄位準備好。
- **登入限流、`DEFAULT_PERMISSION_CLASSES`、WebSocket 的 `is_active` 檢查** —— 見前置報告第 2.2、3.1、3.2、3.4 節，各自獨立處理。
- **不動 `IsResearcher` 與「研究者」Group 的既有判斷邏輯** —— 仍然是「看 Group 不看 `is_staff`」。
- **不動 `api/signals.py` 的 Group ↔ `is_staff` 同步**。
- **不改對話室的匿名設計** —— 見下方 `display_name` 的邊界約束。
- **不做欄位以外的權限模型調整**。

## 限制條件

1. **prod Postgres 已有正式實驗資料，不可清空** → 不能砍掉 migration 重新生成。這是唯一鎖死作法的硬限制。

2. **最小化 schema 變更** → 不是為了效能，而是為了**風險與回滾面**。「建新表 → 搬 20 個外鍵 → 刪舊表」一旦中途失敗，資料處於半搬遷狀態、極難回復；而本方案真正產生 DDL 的只有一步，回滾就是還原 dump。停機視窗也因此短。

3. **dev 與 CI 走 SQLite，prod 走 Postgres** → 所有 migration 必須在兩種後端都正確。

**關於 Cloudflare tunnel 延遲**：那是**本機開發環境連線 prod Postgres** 時的狀況，migration 實際上是在伺服器本機執行、不受此影響。這一點對本 spec 的唯一影響在測試策略 —— 見第 9 節，測試 fixture 要維持小量，不要為了逼真而灌大量測資。

## 設計

### 0. 先釐清：不新增資料庫，也不新增資料表

這個遷移**不建立新的 database，也不建立新的 table**。既有的 Postgres、既有的 `auth_user` 表、既有的每一筆帳號資料，全部原地保留繼續使用。

要動的其實不是資料，是**程式層的歸屬**：目前 `User` 類別定義在 `django/contrib/auth/models.py`，那是 Django 套件自己的程式碼，我們無法在別人的套件裡加欄位。要加 `email_verified_at` 這類欄位，唯一辦法是讓 model 定義變成本 repo 裡的 class —— 這就是 `accounts/models.py` 的用途：**同一張表的新定義檔**。

整個遷移對資料庫做的事，全部只有四項：

| # | 動作 | 影響列數 |
|---|---|---|
| 1 | `INSERT` 一列到 `django_migrations` | 1 |
| 2 | `UPDATE` `django_content_type.app_label`（`auth` → `accounts`） | 1 |
| 3 | `UPDATE` `email`（`''` → `NULL`） | 既有帳號數（本機為 19） |
| 4 | `ALTER TABLE` 加 3 欄 + `email` 加 UNIQUE | 0（僅 schema） |

沒有 `CREATE TABLE`、沒有 `DROP TABLE`、沒有資料搬遷。價值在於**回滾面極小**：真正動到 schema 的只有第 4 項，前三項就算全部回退也只是幾列 metadata。

真正的風險不是「資料會不會壞」，而是「改完之後 Django 認不認得舊資料」—— 因此第 10 節要求先以 `pg_dump` 在本機完整演練。

### 1. 新增 `backend/accounts/` app

```python
# backend/accounts/models.py
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """接管既有 auth_user 資料表的專案自有 User。

    db_table 指回 "auth_user" 是整個遷移方案的核心，見設計文件第 2 節。
    """

    # DB 層允許 NULL：既有帳號都是研究者代開、沒有 email，不能強制 NOT NULL。
    # 「註冊必填」只在 RegistrationSerializer 強制（下一份 spec）。
    email = models.EmailField("email address", unique=True, null=True, blank=True)

    # 先永遠是 None。之後要做驗證信時不用再動 migration。
    email_verified_at = models.DateTimeField(null=True, blank=True)

    # 區分「正式受試者」與「公開註冊的一般使用者」，供研究分析篩選。
    is_research_subject = models.BooleanField(default=False)

    # 只在設定頁與知識庫署名可見，不進對話室、不進 Godot。見第 7 節。
    display_name = models.CharField(max_length=50, blank=True)

    class Meta(AbstractUser.Meta):
        db_table = "auth_user"
```

上面是**最終狀態**。實作時不能一次到位：`0001_initial` 必須是 `auth.User` 的精確複製品（只有 `class User(AbstractUser)` 與 `Meta.db_table`，**沒有那四個欄位**），欄位留到 `0003` 才加。理由見第 6b 節 (a)。

**為什麼是新的 `accounts` app 而不是塞進 `api`**：`api` 已有 27 支 migration、`models.py` 超過 1000 行。User 是被三個 app 依賴的基礎 model，放在獨立的小 app 邊界更清楚，也符合遷移配方需要「User 所在 app 的 initial migration 排在最前面」的要求。

### 2. `db_table = "auth_user"` 為什麼是核心

新 model 的資料表名稱與既有表**完全相同**，因此：

- 那 20 個外鍵在 DB 層指向的目標一個位元組都沒變 → **零 DDL**，完全沒有搬遷風險。
- M2M 中介表名稱由 Django 以 `<db_table>_<field_name>` 推導，得到 `auth_user_groups` 與 `auth_user_user_permissions` —— **與 Django 內建的現有表名一致**。這一點是隱藏的支柱：若 `db_table` 用了別的名字，這兩張表就會對不上。
- 主鍵、序列、`django_admin_log.user_id` 的外鍵全部原封不動。

### 3. 那 20 個外鍵不需要任何 migration（已查證）

Django 的 `ForeignKey.deconstruct()` 對指向 swappable model 的外鍵一律輸出 `settings.AUTH_USER_MODEL`，**與 `models.py` 裡怎麼寫無關**。因此即使 [api/models.py:754](../../../backend/api/models.py) 寫的是 `ForeignKey(User, ...)`，產生的 migration 仍是：

```python
# api/migrations/0009_issue.py:25
('author', models.ForeignKey(..., to=settings.AUTH_USER_MODEL)),
```

查證方式：對 `api/`、`chat/`、`apps/summary/` 的 23 支 migration 檔搜尋硬編的 `to='auth.user'`，**零命中**。

**結論：改 `AUTH_USER_MODEL` 之後，`makemigrations` 對 `chat`、`apps.summary` 不會產生任何 operation，對 `api` 也只在下述例外情況下才會。** 這三個 app 的 migration 完全不需要動，也不需要 `SeparateDatabaseAndState`。

**例外（實作時實測發現）**：`api/models.py` 有 3 個外鍵是寫成 `ForeignKey(User)`（直接引用匯入的類別），而非字串或設定參照 —— 第 754（`Issue.author`）、786（`UserTitle.user`）、811（`IssueReaction.reactor`）行。切換 `AUTH_USER_MODEL` 之後 `auth.User` 成為 swapped-out model，這三個欄位會：

1. 讓 system check 直接失敗：`fields.E301 Field defines a relation with the model 'auth.User', which has been swapped out.`
2. 讓 `makemigrations` 想產生 3 個 `AlterField`

把它們改成 `settings.AUTH_USER_MODEL` 之後兩個問題同時消失，且**不會**產生任何 migration —— 既有 migration 記錄的正是這個形式。因此這是純粹的 `models.py` 寫法修正，必須**在切換設定的同一步完成**，否則 Django 連載入都失敗。

實作計畫需驗證這一點：切換設定後執行 `makemigrations --check --dry-run`，預期除了 `accounts` 之外沒有任何待生成的 migration。

### 4. 障礙一：`check_consistent_history` 的 chicken-and-egg

`api/migrations/0001_initial.py` 透過 `migrations.swappable_dependency(settings.AUTH_USER_MODEL)` 宣告依賴，切換設定後這會解析成 `accounts.__first__` → `accounts.0001_initial`。

prod 上 `api.0001_initial` **已套用**、`accounts.0001_initial` **未套用**，於是 `django/db/migrations/loader.py:344` 的 `check_consistent_history` 會拋出：

```
InconsistentMigrationHistory: Migration api.0001_initial is applied before
its dependency accounts.0001_initial on database 'default'.
```

**而且 `migrate accounts 0001 --fake` 也救不了**：`check_consistent_history` 在 `django/core/management/commands/migrate.py:118` 是**無條件**執行的，早於 `--fake` 的任何處理。這是 chicken-and-egg。

**解法**：在執行任何 `migrate` 之前，直接寫入 `django_migrations` 的紀錄列：

```sql
INSERT INTO django_migrations (app, name, applied)
VALUES ('accounts', '0001_initial', NOW());
```

以一支冪等的管理指令包裝（`accounts/management/commands/bootstrap_user_swap.py`），先檢查該列是否已存在再決定是否插入，並在 SQLite 與 Postgres 都可執行。

全新資料庫（dev / CI / 測試）不需要這一步 —— `accounts.0001_initial` 會照正常順序真正執行。指令必須偵測並在該情況下不做事。

### 5. 障礙二：換 app 會打斷研究者的 Django Admin 權限

[api/migrations/0017_grant_researcher_admin_permissions.py](../../../backend/api/migrations/0017_grant_researcher_admin_permissions.py) 把 `add_user` / `change_user` / `view_user` 指派給「研究者」Group。那些 `Permission` 列綁在 `ContentType(app_label="auth", model="user")`。

User 搬到 `accounts` 後，`post_migrate` 會另外建立一組綁在 `ContentType(app_label="accounts", model="user")` 的新權限，而 Group 手上那組舊的隨即失效 —— Django Admin 檢查的是 `user.has_perm("accounts.change_user")`。**結果是研究者的帳號管理功能整個失效。**

**解法**：`accounts/migrations/0002_repoint_user_content_type.py`，把既有那一列 ContentType 的 `app_label` 由 `auth` 改為 `accounts`，而非任其新建：

```python
def repoint(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    # 冪等：全新 DB 上 accounts/user 已由 post_migrate 建立、auth/user 不存在。
    if ContentType.objects.filter(app_label="accounts", model="user").exists():
        return
    ContentType.objects.filter(app_label="auth", model="user").update(
        app_label="accounts"
    )
```

如此 `Permission` 列、Group 的權限指派、以及 `django_admin_log` 的既有紀錄全部**原地跟著轉移**，不需搬動任何資料。

`(app_label, model)` 上有 unique 約束，因此上面的存在性檢查是必要的而非防禦性冗餘。反向函式把 `app_label` 改回 `auth`。

執行後 `post_migrate` 的 `create_permissions` 會發現 `accounts.User` 的預設權限已存在（content_type 與 codename 都吻合），成為 no-op。

### 6. 障礙三：`email` 不能直接設成 `unique=True NOT NULL`

**實測證據**：本機 DB 的 19 個帳號，`email` **全部是空字串**（含 superuser）。原因是 `AccountCreateSerializer.create()` 只傳 `username` 與 `password`，email 一律留空。直接加 UNIQUE 會立刻撞重複值。

**解法**：DB 層 `null=True, unique=True`。Postgres 與 SQLite 的 UNIQUE 都不將多個 NULL 視為相撞，因此既有帳號可全部保留為 NULL。「必填」只在註冊 serializer 強制。

`accounts/migrations/0003_user_registration_fields.py` 的順序必須是三步，不能簡化成兩步：

1. `AlterField`：`email` 改為 `null=True, blank=True`（**此時還不能加 `unique`**）
2. `RunPython`：把既有 `email = ''` 改成 `NULL`（含反向：`NULL` → `''`）
3. `AlterField`：`email` 加上 `unique=True`
4. `AddField` × 3：`email_verified_at`、`is_research_subject`、`display_name`

兩個約束互相牽制：**回填要寫 NULL，欄位得先允許 NULL**；而 **UNIQUE 必須等回填完才能加**，否則既有帳號的空字串 email 會互相相撞。

⚠️ **這個順序錯了在任何測試資料庫上都測不出來** —— 全新資料庫零筆帳號，`filter(email="")` 匹配到零列、靜默成功。只有在既有資料庫上才會炸：實測對 `backend/db.sqlite3` 的副本（4 個帳號、email 全為空字串）執行錯誤順序，得到 `IntegrityError: NOT NULL constraint failed: auth_user.email`。順序不變式已由 `accounts/tests_migration_guards.py` 的 `EmailBackfillOperationOrderTests` 釘住。

### 6b. 撰寫實作計畫時追加發現的四個 blocker

以下四項在初版設計中被遺漏，任何一項沒處理都會讓遷移失敗：

**（a）`accounts.0001_initial` 必須是 `auth.User` 的精確複製品，不可含新欄位。**
prod 上這支 migration 會被視為已套用而跳過，若它包含新欄位，prod 的資料表就永遠不會長出那些欄位。因此實作順序必須是：先只寫 `class User(AbstractUser)` + `db_table` 產生 `0001`，之後才加欄位產生 `0003`。

**（b）`default_auto_field` 必須明確設為 `AutoField`。**
Django 6 的全域預設是 `BigAutoField`（`django/conf/global_settings.py:440`），而既有 `auth_user.id` 是 `django.contrib.auth` 用 `AutoField` 建的 `integer`（`django/contrib/auth/apps.py:14`）。本專案未覆寫 `DEFAULT_AUTO_FIELD`，若 `accounts/apps.py` 不明確指定，PK 型別會從 `integer` 悄悄變成 `bigint` —— prod 因為 fake 掉 `0001` 而不會報錯，但 model 狀態與實際資料表就此不一致。

**（c）`api/migrations/0017` 會讓每一次全新 `migrate` 失敗。**
它做 `ContentType.objects.get(app_label="auth", model="user")`，而 `AppConfig.get_models()` 預設排除 swapped model（`django/apps/config.py:258`）。換掉 `AUTH_USER_MODEL` 之後 `auth/user` 這個 ContentType 根本不會被建立，`get()` 直接拋 `DoesNotExist`。**每一次跑測試都會炸。**

修法是把該 migration 改成 swappable-aware：從 `settings.AUTH_USER_MODEL` 推出 `(app_label, model)`，並讓 `create_permissions` 同時涵蓋 `auth` 與 user model 所在的 app。

**（d）`admin.site.unregister(User)` 必須刪除。**
`admin.site.register` 對 swapped model 會靜默忽略（`django/contrib/admin/sites.py:135`），因此換掉之後 `django.contrib.auth.admin` 不會註冊任何 User，[api/admin.py:31](../../../backend/api/admin.py) 的 unregister 會拋 `NotRegistered`。本次一併把 User 的 admin 註冊搬到 `accounts/admin.py`，與 model 定義放在一起。

### 7. `display_name` 的邊界約束

現有設計刻意隱藏對話身份：對話室以 `ANONYMOUS_MATCH_USER_NAME`（[api/views.py:123](../../../backend/api/views.py)）取代真實名稱，`MatchMessageSerializer.get_sender_name` 據此輸出；`GodotTicketRedeemView` 的 docstring 明確寫明「沒有理由把登入帳號的識別字串交給遊戲端，大廳日後若需要顯示名稱應該從稱號系統（`/api/titles/me/`）另外取」。

因此 `display_name` 的可見範圍限定為：

| 位置 | 可見 |
|---|---|
| `/api/me/` | 是 |
| 前端設定頁 | 是 |
| 知識庫觀點署名 | 是 |
| `MatchMessageSerializer` | **否** |
| `/api/godot/tickets/redeem/` 回應 | **否** |
| `/api/godot/match-rooms/` 回應 | **否** |

後三項各寫一個 regression test 釘住（見第 9 節）。

若日後真正需要「大廳顯示名稱」，應走稱號系統而非這個欄位。

### 8. 需要改動的既有程式碼

**直接 import 內建 User 的非測試檔（共 3 個）**：

| 檔案 | 現況 | 改為 |
|---|---|---|
| [api/models.py:2](../../../backend/api/models.py) | `from django.contrib.auth.models import User` | `settings.AUTH_USER_MODEL`（三處 FK：第 747、779、804 行） |
| [api/views.py:10](../../../backend/api/views.py) | `from django.contrib.auth.models import Group, User` | `Group` 保留；`User` 改 `get_user_model()` |
| [api/serializers.py:4](../../../backend/api/serializers.py) | `from django.contrib.auth.models import Group, User` | 同上；`AccountListSerializer.Meta.model` 一併換 |

其餘 20 個外鍵早已使用 `settings.AUTH_USER_MODEL`，不需更動。

**Admin**：[api/admin.py:31](../../../backend/api/admin.py) 的 `admin.site.unregister(User)` 需指向新 model；`BridgeUsUserAdmin` 需把四個新欄位加入 `fieldsets`，否則研究者在 admin 看不到也改不了 `is_research_subject`。`add_fieldsets` 一併處理。

**測試檔**：多個測試檔 `from django.contrib.auth.models import User`。這些會因為 `AUTH_USER_MODEL` 改變而拿到錯誤的 model，須一併改為 `get_user_model()`。實作時以搜尋逐一處理。

**INSTALLED_APPS**：`accounts` 必須加入，且需排在 `api` 之前以確保 app registry 順序穩定。

### 9. 測試計畫

新增 `backend/accounts/tests_user_model.py`：

- `AUTH_USER_MODEL` 指向 `accounts.User`，且 `get_user_model()._meta.db_table == "auth_user"`
- M2M 中介表名為 `auth_user_groups`、`auth_user_user_permissions`
- 建立使用者、加入「研究者」Group，`api/signals.py` 的 `is_staff` 同步仍正常
- `email` 可為 NULL 且多筆 NULL 不衝突；兩筆相同的非空 email 會被拒
- `email=''` 的舊資料經 migration 後成為 NULL

新增 `backend/accounts/tests_migration_guards.py`：

- ContentType repoint 後，「研究者」Group 仍持有 `accounts.add_user` / `change_user` / `view_user`
- repoint 函式在「已存在 `accounts/user`」時為 no-op（冪等）
- `bootstrap_user_swap` 指令在全新 DB 上不做事

`display_name` 邊界（可放在既有測試檔）：

- `MatchMessageSerializer` 的輸出不含 `display_name`
- `/api/godot/tickets/redeem/` 回應只有 `user_id`
- `/api/godot/match-rooms/` 回應不含 `display_name`

**測試資料量原則**：所有新增測試的 fixture 維持最小可行規模（每個案例 1–3 個 User）。本機開發環境經 tunnel 連 prod Postgres，灌大量測資只會拖慢每一次 iteration，對驗證正確性沒有任何幫助 —— 這些 migration 的行為與資料筆數無關。

**回歸**：既有 259 個後端測試必須全數通過，特別是 `tests_account_management.py`（23 個）、`tests_supervisor_admin.py`、`tests_token_is_researcher_claim.py`。

> 注意：後端測試共用同一個 Postgres test DB，**不可同時跑兩個 `manage.py test`**。

### 10. 上線與回滾

**演練（必要前置）**

1. `pg_dump` 取得 prod 資料，還原到本機 Postgres
2. 在該副本上完整執行下方 prod 程序
3. 驗證清單：
   - 既有帳號能以原密碼登入（`/api/token/`）
   - 研究者的 Django Admin 帳號管理仍可用（新增／編輯使用者）
   - 前端設定頁的 `/api/accounts/*` 面板正常
   - 20 個外鍵的關聯查詢正常（抽查對話紀錄、問卷回應、稱號）
   - `django_admin_log` 的既有紀錄仍可在 admin 顯示

**prod 程序**

1. 公告短暫停機視窗
2. `pg_dump` 完整備份
3. 部署含 `accounts` app 與新 `AUTH_USER_MODEL` 的程式碼，**先不執行 migrate**
4. 執行 `bootstrap_user_swap`（寫入 `django_migrations` 那一列）
5. 執行 `migrate`（套用 `accounts.0002`、`0003`）
6. 依驗證清單複查
7. 恢復服務

**回滾**：還原步驟 2 的 dump 並退回前一版程式碼。因為 `accounts.0001` 與 `0002` 完全沒有 schema 變更，實際產生 DDL 的只有 `0003`，回滾面很小。

## 風險與未決事項

| 風險 | 緩解 |
|---|---|
| prod 的 email 欄位可能有非空值（本機全空，prod 未實測） | 演練時先 `SELECT count(*) FROM auth_user WHERE email <> ''` 並檢查重複；有重複則需先人工處理 |
| `makemigrations` 產生預期外的 operation | 實作計畫第一步就跑 `makemigrations --check --dry-run` 確認 |
| 遺漏的 `from django.contrib.auth.models import User` | 全域搜尋 + 259 個既有測試把關 |

## 後續

本 spec 完成後，接續兩份工作，各自獨立：

1. **自助註冊 endpoint**（含 email 驗證接口、忘記密碼）—— 需要本 spec 先落地
2. **認證安全基礎**（登入限流、`DEFAULT_PERMISSION_CLASSES`、`is_active` 檢查）—— 與本 spec 無依賴關係，可並行
