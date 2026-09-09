# BridgeUs API Specification

> **Version**: 0.1.0 (Draft)
> **Base URL**: `/api/v1/`
> **Auth**: Bearer JWT (`Authorization: Bearer <token>`)

---

## M1 — User Auth

### POST `/auth/register/`
Register a new user.

**Request**
```json
{
  "username": "string",
  "email": "string",
  "password": "string"
}
```

**Response** `201`
```json
{
  "id": "integer",
  "display_name": "string",
  "access": "string",
  "refresh": "string"
}
```

---

### POST `/api/token/`
Obtain JWT tokens.

**Request**
```json
{ "username": "string", "password": "string" }
```

The `username` field accepts **either the account's username or its email**
(case-insensitive; email is unique). A value containing `@` is resolved to a
username only when no account literally has that username, so a username that
looks like an email still logs into its own account.

**Response** `200`
```json
{ "access": "string", "refresh": "string" }
```

The access token carries an extra `is_researcher` claim. The response does not
include the username — a client that logged in by email should call
`GET /api/me/` to learn it.

---

### POST `/token/refresh/`
Refresh access token.

**Request**
```json
{ "refresh": "string" }
```

---

### GET / PATCH `/api/me/`
Own identity and profile. Auth required; always acts on `request.user`.

**Response** `200`
```json
{
  "id": "integer",
  "username": "string",
  "display_name": "string",
  "email": "string",
  "is_researcher": "boolean",
  "entry_mode": "mixed | split",
  "onboarding_completed": "boolean"
}
```

`email` is `""` when unset (researcher-created accounts have none). `is_researcher`
and `entry_mode` are read from the DB on every call, not from the JWT claim.
`onboarding_completed` is `User.onboarding_completed_at is not None` — the frontend
only auto-opens the onboarding tour when it comes back explicitly `false`.

**PATCH request** — partial; only these three fields are writable.
```json
{ "display_name": "string", "email": "string", "onboarding_completed": "boolean" }
```

`display_name` may be blank (means "do not show"); `email` may not — clearing it
would close off the future email-reset path. Email uniqueness is checked
case-insensitively, excluding yourself. `username` and any role field are ignored
rather than applied. Changing the email resets `email_verified_at`.

`onboarding_completed: true` stamps `onboarding_completed_at` **only if it is still
NULL**, so the timestamp always records the first completion — replaying the tour
from the settings page re-sends `true` and does not overwrite it. Sending `false`
clears the timestamp, which makes the tour auto-open again on the next login.

**Errors** `400` field errors on `display_name` / `email`.

---

### POST `/api/me/password/`
Signed-in user changes their own password. Auth required; rate limited per user
(`THROTTLE_CHANGE_PASSWORD`, default `5/min`).

**Request**
```json
{
  "old_password": "string",
  "new_password": "string",
  "new_password_confirm": "string"
}
```

**Response** `200`
```json
{ "detail": "密碼已更新。", "access": "string", "refresh": "string" }
```

Every refresh token the account already had is blacklisted, so other devices are
signed out; the fresh pair in the response keeps the caller signed in. Access
tokens issued earlier are not revoked and stay valid until they expire.

**Errors** `400` field errors on `old_password` / `new_password` /
`new_password_confirm`; `429` when throttled.

---

### POST `/api/password-reset/request/`
Forgot-password step 1. Public (no auth). Sends a 6-digit code to the account's
email. Rate limited per IP (`THROTTLE_PASSWORD_RESET_REQUEST`, default `5/hour`).

**Request**
```json
{ "email": "string" }
```

**Response** `200`
```json
{ "detail": "驗證碼已寄出。" }
```

Sent only when the email matches an account that is active and has an email
address. Requesting a new code invalidates any previous unused code for that
account. Codes expire after `PASSWORD_RESET_CODE_TTL_MINUTES` (default 10) and
are single-use.

**Errors** `404` `{ "detail": "這個信箱沒有註冊帳號。" }` when the email matches no
active account (the endpoint deliberately does not hide this — the register
endpoint already reveals which emails are taken); `400` when `email` is
malformed; `502` `{ "detail": "驗證碼寄送失敗，請稍後再試。" }` when the mail
server rejects the send (the code is voided); `429` when throttled.

---

### POST `/api/password-reset/confirm/`
Forgot-password step 2. Public (no auth). Verifies the code and sets a new
password. Rate limited per IP (`THROTTLE_PASSWORD_RESET_CONFIRM`, default
`10/hour`); additionally each code is voided after 5 wrong attempts.

**Request**
```json
{
  "email": "string",
  "code": "string (6 digits)",
  "new_password": "string",
  "new_password_confirm": "string"
}
```

**Response** `200`
```json
{ "detail": "密碼已更新，請用新密碼登入。" }
```

On success the account's existing refresh tokens are all blacklisted, and
`email_verified_at` is set if it was still null (receiving the mailed code proves
the address). No tokens are returned — the user signs in again with the new
password.

**Errors** `400` with `{ "detail": "驗證碼不正確或已失效，請重新索取。" }` when
the code is wrong, expired, already used, locked, or the email matches no active
account (same body in every case); `400` field errors on `code` /
`new_password` / `new_password_confirm` for malformed or weak input; `429` when
throttled.

---

### Email verification

Registration requires a verified email. The flow issues a 6-digit code (stored
as a SHA-256 hash only, single-use, expires after
`EMAIL_VERIFICATION_CODE_TTL_MINUTES` = 10, voided after 5 wrong attempts).

#### POST `/api/email-verification/request/`
Public (no auth). Used before the account exists. Rate limited per IP
(`THROTTLE_EMAIL_VERIFICATION_REQUEST`, default `60/hour` — matches `register`
because participants sign up together behind one NAT).

**Request** `{ "email": "string" }`

**Response** `200` `{ "detail": "驗證碼已寄出。" }`

**Errors** `400` `{ "email": ["這個 email 已經註冊過了。"] }` if the address is
already registered; `400` on a malformed email; `502` if the mail server rejects
the send (the code is voided); `429` when throttled.

#### POST `/api/email-verification/confirm/`
Public (no auth). Marks the address verified for the grace window
(`EMAIL_VERIFICATION_GRACE_MINUTES`, default 30), after which `POST /api/register/`
will accept it. A successful registration consumes that verification (deletes
the rows), so it cannot be reused for a second account.

**Request** `{ "email": "string", "code": "string (6 digits)" }`

**Response** `200` `{ "detail": "信箱驗證成功。" }`

**Errors** `400` `{ "detail": "驗證碼不正確或已失效，請重新索取。" }` (wrong /
expired / used / locked); `400` field error on `code`; `429` when throttled.

`POST /api/register/` now returns `400` on `email` with `"請先完成信箱驗證。"`
when the address has no verification within the grace window.

#### POST `/api/me/email/verify/request/` · POST `/api/me/email/verify/confirm/`
Auth required. Same codes, but for a signed-in user verifying the address
currently on their account (after adding or changing it in the settings page).
`request` takes no body and mails `request.user.email`; `confirm` takes
`{ "code": "..." }` and sets `User.email_verified_at`. `request` returns `400`
if there is no email on file or it is already verified. Rate limited per user
(same scopes as above).

`GET /api/me/` includes `"email_verified": bool`.

---

### GET `/api/registration/status/`
Public (no auth). Whether self-service registration is currently open.

**Response** `200`
```json
{ "open": true }
```

Backed by `PlatformDisplaySetting.registration_open` (see
`PATCH /api/settings/display/`). The register page and the login page read this
to show a "registration closed" notice / hide the register link. When it is
`false`, `POST /api/register/` and `POST /api/email-verification/request/` both
return `403` `{ "detail": "目前暫停開放註冊。" }` — checked before rate limiting,
so a closed endpoint does not consume the `register` throttle budget. Researcher
account creation (`POST /api/accounts/`) is unaffected.

---

## M2 — Topic Selection & Stance Measurement

### GET `/stance/topics/`
List available discussion topics.

**Response** `200`
```json
[
  { "id": "integer", "slug": "string", "title": "string", "description": "string" }
]
```

---

### POST `/stance/questionnaire/`
Submit stance questionnaire (Likert + open-ended).

**Request**
```json
{
  "topic_id": "integer",
  "likert_responses": [{ "question_id": "integer", "score": "1-7" }],
  "open_response": "string"
}
```

**Response** `201`
```json
{
  "stance_score": "float",
  "stance_vector": "[float, ...]"
}
```

---

## M3 — Heterogeneous Matching & AI Agent Generation

### POST `/matching/request/`
Request matching with an opposing-stance partner.

**Request**
```json
{ "topic_id": "integer" }
```

**Response** `202`
```json
{ "match_id": "string", "status": "pending|matched|ai_assigned" }
```

---

### GET `/matching/status/<match_id>/`
Check matching status.

---

## M4 — Real-time Dialogue Room

### GET `/dialogue/rooms/<room_id>/`
Get dialogue room info.

### POST `/api/dialogue/sessions/<session_id>/opening/`
AI 開場（H-AI）。依前測開放式作答（Q9 自身觀點、Q10 對立觀點理解）產生開場白
與可討論方向，寫入 session history 的第一則 agent 訊息。**Idempotent**：history
非空時直接回傳現況，不會再生成。

**Response** `200`
```json
{
  "opening": {
    "content": "string",
    "directions": [{ "title": "string", "detail": "string" }],
    "source": "llm | fallback | existing"
  },
  "history": [{ "role": "agent", "content": "string" }]
}
```
`opening` 為 `null` 表示這場沒有開場（`AI_OPENING_ENABLED=0`、或生成失敗）。

### POST `/api/matching/rooms/<room_id>/opening/`
AI 開場（H-H）。同時參考兩位參與者的 Q9／Q10，產生**一房共用**的開場卡片，
存入 `MatchOpeningBrief`，並以 WebSocket `match_opening` 廣播給房內另一位。
開場內容不含任何一方的問卷原文，也不指出誰站哪一邊。

**Response** `200`
```json
{ "opening": { "content": "string", "directions": [], "source": "llm | fallback", "created_at": "ISO8601" }, "pending": false }
```
`pending: true` = 另一位參與者正在生成（同房鎖），本次不重複呼叫 LLM；
等 `match_opening` 廣播或下一次房間輪詢即可拿到。
`GET` 同路徑只讀不生成；`GET /api/matching/rooms/<room_id>/messages/` 的
payload 也帶 `opening` 欄位（尚未生成時為 `null`）。

### WebSocket `ws/dialogue/<room_id>/`
Real-time dialogue. Messages:

```json
// Client → Server
{ "type": "chat.message", "content": "string" }

// Server → Client
{ "type": "chat.message", "sender": "string", "content": "string", "timestamp": "ISO8601" }
{ "type": "system.alert", "message": "string" }
// 配對房：AI 開場廣播（由先進房那位觸發生成後送出）
{ "type": "match_opening", "opening": { "content": "string", "directions": [], "source": "string" } }
```

---

## M5 — NLP Analysis & CCND

### WebSocket `ws/ccnd/<session_id>/`
Receive CCND graph updates.

```json
// Server → Client (push)
{
  "type": "ccnd.update",
  "nodes": [{ "id": "string", "label": "string", "weight": "float" }],
  "edges": [{ "source": "string", "target": "string", "strength": "float" }]
}
```

---

## M6 — Post-Dialogue Summary

### GET `/summary/<session_id>/`
Get post-dialogue summary report.

**Response** `200`
```json
{
  "session_id": "string",
  "stance_shift": "float",
  "emotion_score_delta": "float",
  "summary_text": "string",
  "ccnd_snapshot": "object"
}
```

---

*Last updated: 2026-03-28. This is a living document — update before implementing.*
