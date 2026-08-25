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

### POST `/auth/login/`
Obtain JWT tokens.

**Request**
```json
{ "username": "string", "password": "string" }
```

**Response** `200`
```json
{ "access": "string", "refresh": "string" }
```

---

### POST `/token/refresh/`
Refresh access token.

**Request**
```json
{ "refresh": "string" }
```

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
