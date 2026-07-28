# 後端身份/權限 Code Review（2026-07-26）

> 狀態：**已記錄，暫不修改**（2026-07-26 決定）。等 supervisor 帳號控制那一輪工作再一併處理。
> 範圍：`backend/api/{permissions,signals,admin,serializers,views}.py`、`BridgeUs_Django/settings.py`、
> `api/consumers.py`、`api/migrations/0013`、`0017`。
> P0 與 P1 的 WS 部分是**實測確認**（臨時 probe test 跑過後刪除），不是推測。

## 做得好的部分（別在重構時弄壞）

- 對話/房間/歷史端點的 ownership scoping 一致：全部以 `request.user` 或 `_get_room_match_for_user` 收斂，
  沒有 IDOR。
- `IsGodotServiceToken`（`api/permissions.py:31`）fail-closed（未設 token 就永遠拒絕）+ 常數時間比對，
  且有註解說明為何要 encode 成 bytes。
- 「研究者」Group 與 `is_staff` 刻意分離的理由有寫在 `api/permissions.py` docstring。
- `/api/accounts/*` 有護欄（不能動 superuser、不能停用/降級自己）也有測試
  （`api/tests_account_management.py`、`api/tests_supervisor_admin.py`）。

---

## P0 — Django Admin 完全繞過 REST 的所有護欄（權限提升）

`api/migrations/0017_grant_researcher_admin_permissions.py` 把 `add_user/change_user/view_user` 授給
「研究者」Group，`api/signals.py` 再自動 `is_staff=True`。但 `api/admin.py:28` 的
`BridgeUsUserAdmin` 只改了 `list_display`——Django 內建 `UserAdmin` 的 fieldsets 含
`is_superuser`/`groups`/`user_permissions`，且只要有 `change_user` 就能用改密碼表單。

實測結果：

```
admin change form status: 200
is_superuser after self-edit: True          ← 研究者自我提升為 superuser
superuser password-change form status: 200
superuser password taken over: True         ← 研究者接管 superuser 帳號
```

因此任何研究者都能繞過下列所有 REST 護欄：

| 動作 | REST API | Django Admin |
|---|---|---|
| 把自己設成 `is_superuser` | 不可能（沒這欄位） | ✅ |
| 改 superuser 的密碼 | 403（`views.py:1629`） | ✅ |
| 停用 superuser | 403（`views.py:1582`） | ✅ |
| 刪帳號 | 刻意不提供（0017 不給 `delete_user`） | ✅ 自己勾 `user_permissions` 即可 |

0017 特意不給 `delete_user`、REST 特意擋 superuser——這兩個決定目前都無法真正生效。

**兩個修法（建議第 2 案）**

1. 硬化 `BridgeUsUserAdmin`：覆寫 `get_fieldsets`/`get_readonly_fields` 對非 superuser 隱藏
   `is_superuser`+`user_permissions`、覆寫 `has_change_permission` 拒絕 superuser 目標、
   擋掉 `user_change_password`。要蓋的洞多、容易漏。
2. **收掉 admin 這條路**：新 migration 反轉 0017（收回 Group 的 admin 權限）、移除 `signals.py`
   的 `is_staff` 連動，讓前端設定頁 + `/api/accounts/*` 成為唯一帳號管理入口。
   一條路徑、一組護欄、可測試。

---

## P1 — 停用帳號踢不掉 WebSocket（實測確認）

`api/consumers.py:76` `_authenticate_access_token` 只解 token + 撈 user，沒查 `is_active`：

```
WS auth resolved user for deactivated account: probe_ws_a
resolved.is_active: False
```

HTTP 有保護（simplejwt `CHECK_USER_IS_ACTIVE` 預設 True），`/api/token/refresh/` 也有
（`USER_AUTHENTICATION_RULE`），但 WS 沒有。因為刪除刻意不開放，`is_active=False` 就是唯一的
移除手段——被停用的參與者還能繼續待在對話室/配對房，最長 60 分鐘（`ACCESS_TOKEN_LIFETIME`）。

修法：`_authenticate_access_token` 內加 `if not user.is_active: return None`。

## P1 — 重設密碼不會失效既有 token

`views.py:1619` `AccountPasswordResetView` 換了密碼，但舊 access（60 分）與 refresh（7 天）照用；
`token_blacklist` 沒裝、`ROTATE_REFRESH_TOKENS` 是 False。若「被盜用就改密碼」是救援手段，目前無效。

最省事的解法：`settings.py:261` `SIMPLE_JWT` 加 `"CHECK_REVOKE_TOKEN": True`（simplejwt 會把密碼
hash 的 md5 塞進 token，改密碼後舊 token 自動失效）。注意只作用在 HTTP，WS 那邊要一起補。

---

## P2

- **`is_researcher` claim 降級後仍活 7 天** — `serializers.py:38` 把 claim 設在 refresh token 上，
  `RefreshToken.access_token` 會複製所有 claim（`no_copy_claims` 只排除 type/exp/jti/iat）。
  被降級的人前端仍看得到研究者 UI 直到 refresh token 過期。後端會擋（`IsResearcher` 查 DB），
  所以只是 UI 混淆。建議加 `/api/me/` 回傳即時旗標，或讓設定頁把 403 當成「你被降級了，請重新登入」。
- **密碼驗證沒帶 `user=`，`UserAttributeSimilarityValidator` 空轉** — `serializers.py:518` 與 `:545`
  都是 `dj_validate_password(value)`。`settings.py:216` 掛了這個 validator 但沒 user 就完全不檢查，
  結果密碼可以等於帳號名。建帳號時傳 `user=User(username=...)`，重設時傳 `user=target`。
- **Guest 端點：未認證就能無限建真 User** — `views.py:2262` `GuestLoginView` `AllowAny` +
  `create_user`，且 `settings.py:255` 沒有任何 throttle。這些 row 會直接出現在 supervisor 的帳號清單
  （`views.py:1544` 回傳全部 user、無分頁、無過濾）。建議：給 guest 可辨識標記（旗標或 `guest` Group）、
  清單加分頁 + `?q=`/`?role=` 過濾、`/api/guest/` 與 `/api/token/` 加 throttle、
  寫清理舊 guest 的 management command。
- **DRF 沒有預設權限（fail-open）** — `settings.py:255` 只設了 authentication。目前每個 view 都有寫
  `permission_classes`（只有 token view 沒寫，合理），但之後加 supervisor 端點漏一個就是全公開。
  建議加 `'DEFAULT_PERMISSION_CLASSES': ('rest_framework.permissions.IsAuthenticated',)`，
  公開端點明確 opt out。

## P3

- **研究者之間沒有階層** — `views.py:1571` 的護欄只有「不是 superuser」「不是自己」。研究者 A 可以重設
  研究者 B 的密碼然後登入成 B，也可以把其他所有研究者降級。5 人團隊或許可接受，但這表示
  「Supervisor」和 superuser 之間實質沒有分界——加更多控制前值得先明確決定。
- **沒有稽核軌跡** — 建帳號/停用/升降/重設密碼都不留紀錄（admin 有 LogEntry，REST 端點沒有）。
  人體實驗研究（IRB）大概會需要「誰在何時停用了參與者 X」。一個小 `AccountAuditLog`
  （actor/target/action/at/note）就夠。
- **`post_clear` 未處理** — `signals.py:23` 刻意跳過。今天沒問題（admin widget 與 REST 都走
  add/remove），但若日後有程式呼叫 `user.groups.clear()`，`is_staff` 會殘留 True。
- **三個 view 用 `JWTStatelessUserAuthentication`**（`views.py:878`、`887`、`911`）—
  `TokenUser.is_active` 寫死 True，被停用的人仍讀得到議題/問卷/自己的立場檔（影響小）。
  更要注意的是 `TokenUser.groups` 是 `EmptyManager`——新的研究者端點若照抄這個 pattern，
  `IsResearcher` 會**無聲地永遠拒絕**，很難 debug。
- **小問題** — `validate_username`（`serializers.py:513`）的重複檢查有 race，併發建立會變 500 而非
  400；Django 預設 User 的 username 區分大小寫，`Alice`/`alice` 可共存，研究名冊建議正規化。
  `AccountUpdateSerializer`（`serializers.py:537`）兩個欄位都 optional，`PATCH {}` 會回 200 但什麼都沒做。

---

## 建議的處理順序（等要動的時候）

1. 決定 P0 走哪一案（建議收掉 admin 這條路，設定頁成為唯一入口）。
2. 補 P1 兩個 token/停用漏洞。
3. 在乾淨的單一路徑上加帳號清單分頁/過濾、guest 隔離、稽核軌跡。
