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

### WebSocket `ws/dialogue/<room_id>/`
Real-time dialogue. Messages:

```json
// Client → Server
{ "type": "chat.message", "content": "string" }

// Server → Client
{ "type": "chat.message", "sender": "string", "content": "string", "timestamp": "ISO8601" }
{ "type": "system.alert", "message": "string" }
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
