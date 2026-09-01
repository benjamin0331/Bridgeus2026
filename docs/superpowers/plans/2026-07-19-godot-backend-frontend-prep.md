# Godot Backend + Frontend Prep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the three P1 backend endpoints (titles, issue reactions, service-token match-room creation) and the frontend `/chat` iframe + token handoff that `godot-backend-integration.md` and `godot-web-deployment-spec.md` call for, so that — combined with the already-completed Godot-side work (`docs/superpowers/plans/2026-07-19-godot-deployment-readiness.md`) — only the actual Web export and the infra rollout (systemd + Cloudflare Tunnel) remain before going live. The user explicitly deferred the Web export to last and explicitly excluded infra prep from this round (see the two `AskUserQuestion` answers this plan is scoped from).

**Architecture:** Backend gets two small new models (`Title`/`UserTitle`, `IssueReaction`) plus a new permission class (`IsGodotServiceToken`) that gates one new endpoint (`POST /api/godot/match-rooms/`) by a shared secret instead of a user JWT — mirroring the existing `IsResearcher` permission pattern. Frontend gets one new page component (`GodotLobby.jsx`) that replaces the dead `/chat` route with an iframe and hands off the access token, decoded `user_id`, and same-origin API base to the embedded Godot build, following the same JWT-decode helper (`getAccessTokenPayload`) `LoginPage.jsx` already uses for `is_researcher`.

**Tech Stack:** Django 5 / DRF / pytest-django (backend — **fully runnable and testable in this environment**, unlike the Godot work: `python -c "import django"` succeeds, `pytest` is configured via `pyproject.toml`). React 18 / Vite (frontend — no test suite exists, `npm run lint` and `npm run build` are the available verification commands).

**Scope boundary (confirmed via AskUserQuestion before starting):** In scope: backend's 3 endpoints + `GODOT_SERVICE_TOKEN` env var; frontend's `/chat` iframe + token handoff. **Out of scope, not touched by this plan:** the headless-server systemd unit / Cloudflare Tunnel ingress config (user explicitly did not select this), and the actual Godot Web export (user explicitly said to do this last, separately).

**One correction to the source spec, discovered during research (see Task 6):** `godot-web-deployment-spec.md`'s `redirect_url` example (`/dialogue/room/...`) does not match the actual frontend routing. There is no standalone `/dialogue/room/<room_id>` route — the real chat surface is `TopicChat.jsx` mounted at `/topic/:id`, which internally polls `GET /api/matching/status/?topic_id=` to discover a `DialogueMatch` and only enters chat mode when the URL has `?mode=match` (`frontend/src/pages/TopicChat.jsx:212-217`, `frontend/src/App.jsx:156-165`). Task 6 uses `/topic/<topic_id>?mode=match` instead.

---

## Task 1: `Title` / `UserTitle` models

**Files:**
- Modify: `backend/api/models.py` (insert after the `Issue` model, `:582-594`)
- Modify: `backend/api/admin.py`
- Generate: `backend/api/migrations/0014_title_usertitle.py` (exact name TBD by `makemigrations` — see Step 3)

- [ ] **Step 1: Add the models**

In `backend/api/models.py`, immediately after `Issue`'s `__str__` method and its trailing blank line (right before `class CCNDTimelineUnlock(models.Model):`), insert:

```python
class Title(models.Model):
    """一個頭銜的定義。擁有/解鎖關係另存在 UserTitle——由主功能的成就系統
    決定誰擁有什麼，這裡只是頭銜本身的名稱與預設顏色。"""
    name = models.CharField(max_length=50, unique=True)
    color = models.CharField(max_length=7, null=True, blank=True)  # 預設 hex 色，UserTitle.color 可覆蓋

    def __str__(self):
        return self.name


class UserTitle(models.Model):
    """使用者擁有某個頭銜的紀錄，外加是否為目前選擇顯示、以及玩家自訂顏色。
    一個使用者同時只能選一個頭銜——由 view 端在同一個 transaction 內先清掉
    舊選擇再設新的來保證，沒有用 DB 層的 partial unique index（SQLite 相容性）。"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="owned_titles")
    title = models.ForeignKey(Title, on_delete=models.CASCADE, related_name="holders")
    unlocked_at = models.DateTimeField(auto_now_add=True)
    is_selected = models.BooleanField(default=False)
    color = models.CharField(max_length=7, null=True, blank=True)  # 玩家自訂色，蓋過 Title.color

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "title"], name="user_title_unique"),
        ]

    def __str__(self):
        return f"{self.user_id}:{self.title.name}"


```

- [ ] **Step 2: Register in admin**

In `backend/api/admin.py`, add `Title` and `UserTitle` to the existing import block:

```python
from .models import (
    AIConversation,
    CCNDTimelineUnlock,
    DialogueMatch,
    MatchAISuggestion,
    MatchMessage,
    MatchQueueEntry,
    MatchStanceDrift,
    Title,
    UserStanceProfile,
    UserTitle,
)
```

Then append at the end of the file:

```python


@admin.register(Title)
class TitleAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "color")
    search_fields = ("name",)


@admin.register(UserTitle)
class UserTitleAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "title", "is_selected", "unlocked_at")
    list_filter = ("is_selected",)
    search_fields = ("user__username", "title__name")
```

- [ ] **Step 3: Generate and apply the migration**

```bash
cd /Users/light/code/backend
source .venv/bin/activate
python manage.py makemigrations api
```
Expected: a new file reported, something like `api/migrations/0014_title_usertitle.py`.

```bash
python manage.py migrate api
```
Expected: `Applying api.0014_title_usertitle... OK` (or whatever name was generated).

- [ ] **Step 4: Verify**

```bash
python manage.py check
```
Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 5: Commit**

```bash
git add backend/api/models.py backend/api/admin.py backend/api/migrations/
git commit -m "$(cat <<'EOF'
feat(backend): add Title/UserTitle models for Godot lobby banners

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `GET`/`POST /api/titles/me/`

**Files:**
- Create: `backend/api/tests_titles.py`
- Modify: `backend/api/views.py` (new import, new view class)
- Modify: `backend/api/urls.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/api/tests_titles.py`:

```python
"""
pytest tests for GET/POST /api/titles/me/

Run from backend/:
    pytest api/tests_titles.py -v
"""
import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from api.models import Title, UserTitle

User = get_user_model()


@pytest.mark.django_db
def test_titles_me_empty_when_no_titles_owned():
    user = User.objects.create_user(username="u1", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/titles/me/")

    assert response.status_code == 200
    assert response.data == {"owned": [], "selected_id": None, "color": None}


@pytest.mark.django_db
def test_titles_me_lists_owned_and_selected():
    user = User.objects.create_user(username="u1", password="pw")
    t1 = Title.objects.create(name="探索者")
    t2 = Title.objects.create(name="傾聽者", color="#112233")
    UserTitle.objects.create(user=user, title=t1)
    UserTitle.objects.create(user=user, title=t2, is_selected=True)
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/titles/me/")

    assert response.status_code == 200
    assert response.data["selected_id"] == t2.id
    assert response.data["color"] == "#112233"
    assert {row["id"] for row in response.data["owned"]} == {t1.id, t2.id}


@pytest.mark.django_db
def test_set_title_rejects_unowned_title():
    user = User.objects.create_user(username="u1", password="pw")
    other_title = Title.objects.create(name="不屬於我")
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post("/api/titles/me/", {"title_id": other_title.id}, format="json")

    assert response.status_code == 403
    assert not UserTitle.objects.filter(user=user).exists()


@pytest.mark.django_db
def test_set_title_selects_owned_title_and_overwrites_color():
    user = User.objects.create_user(username="u1", password="pw")
    title = Title.objects.create(name="探索者")
    UserTitle.objects.create(user=user, title=title)
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(
        "/api/titles/me/", {"title_id": title.id, "color": "#abcdef"}, format="json"
    )

    assert response.status_code == 200
    assert response.data["selected_id"] == title.id
    assert response.data["color"] == "#abcdef"


@pytest.mark.django_db
def test_set_title_null_clears_selection():
    user = User.objects.create_user(username="u1", password="pw")
    title = Title.objects.create(name="探索者")
    UserTitle.objects.create(user=user, title=title, is_selected=True)
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post("/api/titles/me/", {"title_id": None}, format="json")

    assert response.status_code == 200
    assert response.data["selected_id"] is None
```

- [ ] **Step 2: Run and confirm the tests fail (route doesn't exist yet)**

```bash
cd /Users/light/code/backend && source .venv/bin/activate
pytest api/tests_titles.py -v
```
Expected: all 5 tests FAIL with 404 (no `/api/titles/me/` route registered yet).

- [ ] **Step 3: Add the view**

In `backend/api/views.py`, add `Title` and `UserTitle` to the existing `.models` import block (`:23-33`):

```python
from .models import (
    AIConversation,
    DialogueMatch,
    DialogueSessionRecord,
    DiscomfortReport,
    Issue,
    MatchStanceDrift,
    PlatformFeedback,
    PostDialogueResponse,
    Title,
    UserStanceProfile,
    UserTitle,
)
```

Add `from django.db import transaction` to the top-level imports (alongside the existing `from django.db.models import Q` at `:9`):

```python
from django.db import transaction
from django.db.models import Q
```

Then append the new view class at the end of `views.py` (after `IssueListCreateView`):

```python


class TitleMeView(APIView):
    """GET/POST /api/titles/me/ — 玩家在 Godot 大廳看/選自己擁有的頭銜。
    頭銜本身怎麼解鎖由主功能成就系統決定（見 UserTitle 模型註解），這裡只管
    「我有哪些、目前選哪個」。契約見 godot-backend-integration.md §3.1。"""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        owned = UserTitle.objects.filter(user=request.user).select_related("title")
        selected = next((ut for ut in owned if ut.is_selected), None)
        return Response(
            {
                "owned": [{"id": ut.title_id, "name": ut.title.name} for ut in owned],
                "selected_id": selected.title_id if selected else None,
                "color": (selected.color or selected.title.color) if selected else None,
            }
        )

    def post(self, request):
        title_id = request.data.get("title_id")
        color = request.data.get("color")

        target = None
        if title_id is not None:
            try:
                target = UserTitle.objects.get(user=request.user, title_id=title_id)
            except UserTitle.DoesNotExist:
                return Response(
                    {"detail": "尚未擁有這個頭銜。"},
                    status=status.HTTP_403_FORBIDDEN,
                )

        # 驗證擁有權先於任何寫入——避免「清掉舊選擇後才發現目標無效」把使用者
        # 的選擇狀態意外清空。
        with transaction.atomic():
            UserTitle.objects.filter(user=request.user, is_selected=True).update(
                is_selected=False
            )
            if target is not None:
                if color:
                    target.color = color
                target.is_selected = True
                target.save(update_fields=["is_selected", "color"])

        return self.get(request)
```

- [ ] **Step 4: Add the URL**

In `backend/api/urls.py`, add before the closing `]`:

```python
    path('titles/me/', views.TitleMeView.as_view()),
```

- [ ] **Step 5: Run and confirm the tests pass**

```bash
pytest api/tests_titles.py -v
```
Expected: all 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/api/tests_titles.py backend/api/views.py backend/api/urls.py
git commit -m "$(cat <<'EOF'
feat(backend): add GET/POST /api/titles/me/ for Godot lobby banners

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `IssueReaction` model

**Files:**
- Modify: `backend/api/models.py` (insert after `UserTitle`, before `CCNDTimelineUnlock`)
- Modify: `backend/api/admin.py`
- Generate: `backend/api/migrations/0015_issuereaction.py` (exact name TBD)

- [ ] **Step 1: Add the model**

In `backend/api/models.py`, right after the `UserTitle` class added in Task 1 (still before `class CCNDTimelineUnlock`), insert:

```python
class IssueReaction(models.Model):
    """一位讀者對一則議題的表情回復（5 選 1，emoji 圖在 Godot 端，這裡只存
    int index）。一人一議題一個，重送 = 覆蓋（見 views.IssueReactionsView）。"""
    issue = models.ForeignKey(Issue, on_delete=models.CASCADE, related_name="reactions")
    reactor = models.ForeignKey(User, on_delete=models.CASCADE, related_name="issue_reactions")
    emoji_index = models.PositiveSmallIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["issue", "reactor"], name="issue_reaction_one_per_reader"
            ),
        ]

    def __str__(self):
        return f"issue={self.issue_id} reactor={self.reactor_id} idx={self.emoji_index}"


```

- [ ] **Step 2: Register in admin**

In `backend/api/admin.py`, add `IssueReaction` to the import block (alongside `Title`/`UserTitle` from Task 1):

```python
from .models import (
    AIConversation,
    CCNDTimelineUnlock,
    DialogueMatch,
    IssueReaction,
    MatchAISuggestion,
    MatchMessage,
    MatchQueueEntry,
    MatchStanceDrift,
    Title,
    UserStanceProfile,
    UserTitle,
)
```

Append at the end of the file:

```python


@admin.register(IssueReaction)
class IssueReactionAdmin(admin.ModelAdmin):
    list_display = ("id", "issue", "reactor", "emoji_index", "created_at")
    search_fields = ("issue__title", "reactor__username")
```

- [ ] **Step 3: Generate and apply the migration**

```bash
cd /Users/light/code/backend && source .venv/bin/activate
python manage.py makemigrations api
python manage.py migrate api
```
Expected: a new migration (e.g. `0015_issuereaction.py`) applies cleanly.

- [ ] **Step 4: Verify**

```bash
python manage.py check
```
Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 5: Commit**

```bash
git add backend/api/models.py backend/api/admin.py backend/api/migrations/
git commit -m "$(cat <<'EOF'
feat(backend): add IssueReaction model for Godot issue emoji reactions

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `GET`/`POST /api/issues/<issue_id>/reactions/`

**Files:**
- Create: `backend/api/tests_issue_reactions.py`
- Modify: `backend/api/views.py`
- Modify: `backend/api/urls.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/api/tests_issue_reactions.py`:

```python
"""
pytest tests for GET/POST /api/issues/<issue_id>/reactions/

Run from backend/:
    pytest api/tests_issue_reactions.py -v
"""
import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from api.models import Issue, IssueReaction

User = get_user_model()


@pytest.mark.django_db
def test_react_creates_reaction_and_get_reflects_it():
    author = User.objects.create_user(username="author", password="pw")
    reader = User.objects.create_user(username="reader", password="pw")
    issue = Issue.objects.create(author=author, title="核能", body="...")
    client = APIClient()
    client.force_authenticate(user=reader)

    response = client.post(
        f"/api/issues/{issue.id}/reactions/", {"emoji_index": 3}, format="json"
    )

    assert response.status_code == 200
    assert response.data["counts"] == {"3": 1}
    assert response.data["mine"] == 3


@pytest.mark.django_db
def test_react_again_overwrites_previous_choice_not_duplicates():
    author = User.objects.create_user(username="author", password="pw")
    reader = User.objects.create_user(username="reader", password="pw")
    issue = Issue.objects.create(author=author, title="核能", body="...")
    client = APIClient()
    client.force_authenticate(user=reader)
    client.post(f"/api/issues/{issue.id}/reactions/", {"emoji_index": 1}, format="json")

    response = client.post(
        f"/api/issues/{issue.id}/reactions/", {"emoji_index": 4}, format="json"
    )

    assert response.status_code == 200
    assert response.data["counts"] == {"4": 1}
    assert IssueReaction.objects.filter(issue=issue, reactor=reader).count() == 1


@pytest.mark.django_db
def test_react_rejects_out_of_range_emoji_index():
    author = User.objects.create_user(username="author", password="pw")
    issue = Issue.objects.create(author=author, title="核能", body="...")
    client = APIClient()
    client.force_authenticate(user=author)

    response = client.post(
        f"/api/issues/{issue.id}/reactions/", {"emoji_index": 9}, format="json"
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_reactions_404_for_missing_issue():
    user = User.objects.create_user(username="u1", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/issues/999999/reactions/")

    assert response.status_code == 404
```

- [ ] **Step 2: Run and confirm the tests fail**

```bash
cd /Users/light/code/backend && source .venv/bin/activate
pytest api/tests_issue_reactions.py -v
```
Expected: all 4 tests FAIL with 404 (route doesn't exist yet).

- [ ] **Step 3: Add the view**

In `backend/api/views.py`, add `IssueReaction` to the `.models` import block (extending what Task 2 already added):

```python
from .models import (
    AIConversation,
    DialogueMatch,
    DialogueSessionRecord,
    DiscomfortReport,
    Issue,
    IssueReaction,
    MatchStanceDrift,
    PlatformFeedback,
    PostDialogueResponse,
    Title,
    UserStanceProfile,
    UserTitle,
)
```

Append at the end of `views.py` (after `TitleMeView`):

```python


class IssueReactionsView(APIView):
    """GET/POST /api/issues/<issue_id>/reactions/ — 議題表情回復（5 選 1）。
    upsert：同一 reactor 對同一 issue 再送 = 覆蓋，不是疊加。契約見
    godot-backend-integration.md §3.2。"""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, issue_id: int):
        if not Issue.objects.filter(pk=issue_id).exists():
            return Response(
                {"detail": "找不到這個議題。"}, status=status.HTTP_404_NOT_FOUND
            )
        counts: dict[str, int] = {}
        for idx in IssueReaction.objects.filter(issue_id=issue_id).values_list(
            "emoji_index", flat=True
        ):
            counts[str(idx)] = counts.get(str(idx), 0) + 1
        mine = (
            IssueReaction.objects.filter(issue_id=issue_id, reactor=request.user)
            .values_list("emoji_index", flat=True)
            .first()
        )
        return Response({"counts": counts, "mine": mine})

    def post(self, request, issue_id: int):
        try:
            issue = Issue.objects.get(pk=issue_id)
        except Issue.DoesNotExist:
            return Response(
                {"detail": "找不到這個議題。"}, status=status.HTTP_404_NOT_FOUND
            )
        emoji_index = request.data.get("emoji_index")
        if not isinstance(emoji_index, int) or not (0 <= emoji_index <= 4):
            return Response(
                {"detail": "emoji_index 必須是 0-4 的整數。"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        IssueReaction.objects.update_or_create(
            issue=issue,
            reactor=request.user,
            defaults={"emoji_index": emoji_index},
        )
        return self.get(request, issue_id)
```

- [ ] **Step 4: Add the URL**

In `backend/api/urls.py`, add after the `titles/me/` line from Task 2:

```python
    path(
        'issues/<int:issue_id>/reactions/',
        views.IssueReactionsView.as_view(),
    ),
```

- [ ] **Step 5: Run and confirm the tests pass**

```bash
pytest api/tests_issue_reactions.py -v
```
Expected: all 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/api/tests_issue_reactions.py backend/api/views.py backend/api/urls.py
git commit -m "$(cat <<'EOF'
feat(backend): add GET/POST /api/issues/<id>/reactions/ for Godot emoji reactions

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `GODOT_SERVICE_TOKEN` setting + `IsGodotServiceToken` permission

**Files:**
- Modify: `backend/BridgeUs_Django/settings.py`
- Modify: `backend/api/permissions.py`
- Modify: `backend/.env.example`

- [ ] **Step 1: Add the setting**

In `backend/BridgeUs_Django/settings.py`, immediately after the `JWT_SIGNING_KEY` line (`:63`), insert:

```python
# 給 Godot 常駐 headless server 呼叫 /api/godot/match-rooms/ 用的共用密鑰
# （不是 user JWT）。空字串 = 該端點永遠拒絕（fail closed）。見
# godot-web-deployment-spec.md §4「服務金鑰建房契約」。
GODOT_SERVICE_TOKEN = os.getenv("GODOT_SERVICE_TOKEN", "")
```

- [ ] **Step 2: Add the permission class**

In `backend/api/permissions.py`, add these imports at the top (before `from rest_framework.permissions import BasePermission`):

```python
import hmac

from django.conf import settings
from rest_framework.permissions import BasePermission
```

Then append at the end of the file:

```python


class IsGodotServiceToken(BasePermission):
    """Godot 常駐 headless server 專用：驗證 X-Godot-Service-Token 標頭，不需要
    （也不接受）user JWT——呼叫者是 server，不代表任何一位玩家，所以不套
    「requester 必須是參與者」這類限制。見 godot-web-deployment-spec.md §4。
    """

    message = "缺少或錯誤的服務金鑰。"

    def has_permission(self, request, view):
        configured = getattr(settings, "GODOT_SERVICE_TOKEN", "") or ""
        if not configured:
            return False
        provided = request.META.get("HTTP_X_GODOT_SERVICE_TOKEN", "")
        return hmac.compare_digest(provided, configured)
```

- [ ] **Step 3: Document the env var**

In `backend/.env.example`, append after the last line (`EMBEDDING_MODEL=...`):

```
GODOT_SERVICE_TOKEN=replace-me
```

- [ ] **Step 4: Verify**

```bash
cd /Users/light/code/backend && source .venv/bin/activate
python manage.py check
python -c "from api.permissions import IsGodotServiceToken; print(IsGodotServiceToken)"
```
Expected: check passes, import succeeds and prints the class.

- [ ] **Step 5: Commit**

```bash
git add backend/BridgeUs_Django/settings.py backend/api/permissions.py backend/.env.example
git commit -m "$(cat <<'EOF'
feat(backend): add GODOT_SERVICE_TOKEN setting and IsGodotServiceToken permission

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: `POST /api/godot/match-rooms/`

**Files:**
- Create: `backend/api/tests_godot_match_rooms.py`
- Modify: `backend/api/views.py`
- Modify: `backend/api/urls.py`

**Reminder of the correction from the plan header:** `redirect_url` is `/topic/<topic_id>?mode=match`, not `/dialogue/room/<room_id>` — verified against `frontend/src/App.jsx:156-165` and `frontend/src/pages/TopicChat.jsx:212-217,747` (the frontend's own polling of `GET /api/matching/status/?topic_id=` is what actually surfaces the `room_id` once `DialogueMatch` exists — a Godot-created match doesn't need any special "attach" step for the survey either, since `DialogueSurveyView`/`get_dialogue_survey(topic_id)` already look the survey up by `topic_id` independent of any match, per `backend/api/views.py:832-845`).

- [ ] **Step 1: Write the failing tests**

Create `backend/api/tests_godot_match_rooms.py`:

```python
"""
pytest tests for POST /api/godot/match-rooms/

Run from backend/:
    pytest api/tests_godot_match_rooms.py -v
"""
import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIClient

from api.models import DialogueMatch

User = get_user_model()


@pytest.mark.django_db
@override_settings(GODOT_SERVICE_TOKEN="test-token-123")
def test_match_room_creates_active_dialogue_match():
    user_a = User.objects.create_user(username="a", password="pw")
    user_b = User.objects.create_user(username="b", password="pw")
    client = APIClient()

    response = client.post(
        "/api/godot/match-rooms/",
        {"topic": "nuclear_energy", "topic_id": 102, "user_ids": [user_a.id, user_b.id]},
        format="json",
        HTTP_X_GODOT_SERVICE_TOKEN="test-token-123",
    )

    assert response.status_code == 201
    assert response.data["redirect_url"] == "/topic/102?mode=match"
    match = DialogueMatch.objects.get(room_id=response.data["room_id"])
    assert match.status == DialogueMatch.Status.ACTIVE
    assert match.matching_algorithm_version == "godot_manual"
    assert {match.user_a_id, match.user_b_id} == {user_a.id, user_b.id}


@pytest.mark.django_db
@override_settings(GODOT_SERVICE_TOKEN="test-token-123")
def test_match_room_rejects_wrong_token():
    user_a = User.objects.create_user(username="a", password="pw")
    user_b = User.objects.create_user(username="b", password="pw")
    client = APIClient()

    response = client.post(
        "/api/godot/match-rooms/",
        {"topic": "nuclear_energy", "topic_id": 102, "user_ids": [user_a.id, user_b.id]},
        format="json",
        HTTP_X_GODOT_SERVICE_TOKEN="wrong",
    )

    assert response.status_code == 403


@pytest.mark.django_db
@override_settings(GODOT_SERVICE_TOKEN="")
def test_match_room_fails_closed_when_token_unconfigured():
    user_a = User.objects.create_user(username="a", password="pw")
    user_b = User.objects.create_user(username="b", password="pw")
    client = APIClient()

    response = client.post(
        "/api/godot/match-rooms/",
        {"topic": "nuclear_energy", "topic_id": 102, "user_ids": [user_a.id, user_b.id]},
        format="json",
        HTTP_X_GODOT_SERVICE_TOKEN="",
    )

    assert response.status_code == 403


@pytest.mark.django_db
@override_settings(GODOT_SERVICE_TOKEN="test-token-123")
def test_match_room_rejects_unknown_topic_id():
    user_a = User.objects.create_user(username="a", password="pw")
    user_b = User.objects.create_user(username="b", password="pw")
    client = APIClient()

    response = client.post(
        "/api/godot/match-rooms/",
        {"topic": "nope", "topic_id": 999, "user_ids": [user_a.id, user_b.id]},
        format="json",
        HTTP_X_GODOT_SERVICE_TOKEN="test-token-123",
    )

    assert response.status_code == 400


@pytest.mark.django_db
@override_settings(GODOT_SERVICE_TOKEN="test-token-123")
def test_match_room_rejects_same_user_twice():
    user_a = User.objects.create_user(username="a", password="pw")
    client = APIClient()

    response = client.post(
        "/api/godot/match-rooms/",
        {"topic": "nuclear_energy", "topic_id": 102, "user_ids": [user_a.id, user_a.id]},
        format="json",
        HTTP_X_GODOT_SERVICE_TOKEN="test-token-123",
    )

    assert response.status_code == 400
```

- [ ] **Step 2: Run and confirm the tests fail**

```bash
cd /Users/light/code/backend && source .venv/bin/activate
pytest api/tests_godot_match_rooms.py -v
```
Expected: all 5 tests FAIL with 404 (route doesn't exist yet).

- [ ] **Step 3: Add the view**

In `backend/api/views.py`, add `from decimal import Decimal` to the top-level imports (alongside `from uuid import uuid4` at `:5`):

```python
from decimal import Decimal
from uuid import uuid4
```

Add `IsGodotServiceToken` to the existing `from .permissions import IsResearcher` line (`:21`):

```python
from .permissions import IsGodotServiceToken, IsResearcher
```

Append at the end of `views.py` (after `IssueReactionsView`):

```python


class GodotMatchRoomView(APIView):
    """POST /api/godot/match-rooms/ — 給常駐 headless Godot server 呼叫，把兩位
    已在主功能登入的玩家直接配成一間議題聊天室，不走 M3 立場配對佇列
    （MatchingJoinView/enqueue_for_matching）。契約見
    godot-backend-integration.md §3.3；身份用共用服務金鑰而非 user JWT，見
    godot-web-deployment-spec.md §4。"""

    authentication_classes = []
    permission_classes = [IsGodotServiceToken]

    def post(self, request):
        topic_id = request.data.get("topic_id")
        user_ids = request.data.get("user_ids")

        if not isinstance(topic_id, int) or topic_id not in TOPIC_CONFIGS:
            return Response(
                {"detail": "topic_id 無效。"}, status=status.HTTP_400_BAD_REQUEST
            )
        if (
            not isinstance(user_ids, list)
            or len(user_ids) != 2
            or user_ids[0] == user_ids[1]
        ):
            return Response(
                {"detail": "user_ids 需為兩個不同的使用者 id。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user_a = User.objects.get(pk=user_ids[0])
            user_b = User.objects.get(pk=user_ids[1])
        except User.DoesNotExist:
            return Response(
                {"detail": "找不到其中一位使用者。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Godot 木樁配對只做「同議題湊一對」，不跑 M3 立場向量配對，所以沒有
        # 真實 stance score 可用——user_a_score/user_b_score 只是滿足 DB 1-7
        # constraint 的中性佔位值。matching_algorithm_version 標成
        # "godot_manual"，方便日後分析時跟真正演算法配對的資料分開看。
        match = DialogueMatch.objects.create(
            topic_id=topic_id,
            user_a=user_a,
            user_b=user_b,
            user_a_score=Decimal("4.00"),
            user_b_score=Decimal("4.00"),
            matching_algorithm_version="godot_manual",
            room_id=uuid4().hex,
            status=DialogueMatch.Status.ACTIVE,
        )

        return Response(
            {
                "room_id": match.room_id,
                # 前端沒有獨立的 /dialogue/room/<id> 路由——配對聊天室其實是
                # TopicChat.jsx 掛在 /topic/<topic_id>?mode=match，內部再用
                # GET /api/matching/status/?topic_id= 找到這筆 DialogueMatch。
                "redirect_url": f"/topic/{topic_id}?mode=match",
            },
            status=status.HTTP_201_CREATED,
        )
```

- [ ] **Step 4: Add the URL**

In `backend/api/urls.py`, add after the `issues/<int:issue_id>/reactions/` entry from Task 4:

```python
    path('godot/match-rooms/', views.GodotMatchRoomView.as_view()),
```

- [ ] **Step 5: Run and confirm the tests pass**

```bash
pytest api/tests_godot_match_rooms.py -v
```
Expected: all 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/api/tests_godot_match_rooms.py backend/api/views.py backend/api/urls.py
git commit -m "$(cat <<'EOF'
feat(backend): add POST /api/godot/match-rooms/ for service-token match creation

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Full backend verification pass

**Files:** none (verification only)

- [ ] **Step 1: Run every new test file together**

```bash
cd /Users/light/code/backend && source .venv/bin/activate
pytest api/tests_titles.py api/tests_issue_reactions.py api/tests_godot_match_rooms.py -v
```
Expected: 14 tests, all PASS.

- [ ] **Step 2: Run the pre-existing `IsResearcher`-adjacent test file to confirm no regression on shared imports**

```bash
pytest api/tests_viewpoint_review.py -v
```
Expected: unchanged pass/fail status versus before this plan (this file exercises `IsResearcher`, which now shares `api/permissions.py` with the new `IsGodotServiceToken` — confirms the shared-file edit didn't break the existing permission).

- [ ] **Step 3: `manage.py check` one more time**

```bash
python manage.py check
```
Expected: `System check identified no issues (0 silenced).`

No commit for this task — it's a verification checkpoint, not a code change.

---

## Task 8: Frontend `/chat` → `GodotLobby` iframe + token handoff

**Files:**
- Create: `frontend/src/pages/GodotLobby.jsx`
- Create: `frontend/src/pages/GodotLobby.css`
- Modify: `frontend/src/App.jsx`

- [ ] **Step 1: Create the component**

Create `frontend/src/pages/GodotLobby.jsx`:

```jsx
import { useRef } from 'react';
import api, { getAccessTokenPayload } from '../api/client';
import './GodotLobby.css';

// 把主功能登入的 access token / user_id / API base 交給嵌入的 Godot 大廳。
// 同源部署（見 godot-web-deployment-spec.md §0 拓樸表）下可以直接設 iframe
// window，Godot 端 Backend.gd::acquire_token_from_host() 在 _ready() 就會讀
// window.bridgeus_token / window.bridgeus_user_id（見同檔 §2）。
export default function GodotLobby() {
  const iframeRef = useRef(null);

  const handleLoad = () => {
    const frameWindow = iframeRef.current?.contentWindow;
    if (!frameWindow) return;

    const token = localStorage.getItem('access') || '';
    const userId = getAccessTokenPayload()?.user_id ?? 0;
    const apiBase = `${api.defaults.baseURL || ''}/api`;

    frameWindow.bridgeus_token = token;
    frameWindow.bridgeus_user_id = userId;
    frameWindow.bridgeus_api_base = apiBase;
    // window.bridgeus_ws_url 故意不設：常駐 Godot server 的正式網域還沒決定
    // （infra 這輪明確不做，見部署規格 §0/§4），Godot 端 _resolve_connection_settings()
    // 讀不到就會退回本機位址——之後 infra 定案再回來補這個值即可。
  };

  return (
    <div className="godot-lobby">
      <iframe
        ref={iframeRef}
        src="/godot/index.html"
        onLoad={handleLoad}
        allow="microphone"
        className="godot-lobby-frame"
        title="BridgeUs 虛擬大廳"
      />
    </div>
  );
}
```

Create `frontend/src/pages/GodotLobby.css`:

```css
.godot-lobby {
  width: 100%;
  height: 100%;
  display: flex;
}

.godot-lobby-frame {
  width: 100%;
  height: 100%;
  border: 0;
  flex: 1;
}
```

- [ ] **Step 2: Wire up the route**

In `frontend/src/App.jsx`, add the import alongside the other page imports (`:8-16`):

```jsx
import ViewpointReviewPage from './pages/ViewpointReviewPage'
import GodotLobby from './pages/GodotLobby'
```

Replace the dead `/chat` route:

```jsx
            <Route path="/chat" element={<div className="empty-page-message">Godot還在排隊</div>} />
```
with:
```jsx
            <Route path="/chat" element={<GodotLobby />} />
```

(No extra auth guard needed: this route lives inside the `<Routes>` block at `App.jsx:143+`, which only renders once `if (!user) return <Routes>...</Routes>` at `:122-128` has already passed — every route here, `/chat` included, is already gated on being logged in.)

- [ ] **Step 3: Verify**

```bash
cd /Users/light/code/frontend
npm run lint
```
Expected: no new lint errors (pre-existing warnings in untouched files are fine, but `GodotLobby.jsx`/`App.jsx` diffs must be clean).

```bash
npm run build
```
Expected: build succeeds (confirms the JSX is syntactically valid and imports resolve — there is no Godot dev server or `/godot/index.html` to actually load yet, since the Web export is deliberately deferred; this only proves the React side is wired correctly).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/GodotLobby.jsx frontend/src/pages/GodotLobby.css frontend/src/App.jsx
git commit -m "$(cat <<'EOF'
feat(frontend): replace dead /chat route with Godot iframe + token handoff

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Self-check against the three spec documents + update gap-analysis

**Files:**
- Modify: `/Users/light/project/0723報告/godot修改/godot-backend-gap-analysis.md` (status table only)

- [ ] **Step 1: Dump the diff for review**

```bash
git -C /Users/light/code diff 9f7fcd7 -- backend/ frontend/
```

- [ ] **Step 2: Check against `godot-backend-integration.md`**

Confirm §3.1 (titles), §3.2 (reactions), §3.3 (match-rooms) each have their `GET`/`POST` contract matched field-for-field: `owned`/`selected_id`/`color` for titles; `counts`/`mine` for reactions; `room_id`/`redirect_url` for match-rooms (with the corrected `redirect_url`, not the spec's literal example).

- [ ] **Step 3: Check against `godot-web-deployment-spec.md` §4's service-token contract**

Confirm: `X-Godot-Service-Token` header name matches exactly what `Backend.gd`'s `_post_with_service_token` already sends (verify: `grep -n "X-Godot-Service-Token" /Users/light/code/godot/Globals/Backend.gd /Users/light/code/backend/api/permissions.py` — both sides must use the identical header name); `GODOT_SERVICE_TOKEN` env var name matches what `Backend.gd`'s `_ready()` reads via `OS.get_environment("GODOT_SERVICE_TOKEN")`.

- [ ] **Step 4: Update `godot-backend-gap-analysis.md`'s status table**

In `/Users/light/project/0723報告/godot修改/godot-backend-gap-analysis.md`, update the 總覽表 rows for titles, reactions, and match-rooms from 🔴/🔴/🔴 to 🟢 (built, this session), and update §8's Godot-side method table — `request_topic_match` was already ✅ from the prior plan; the backend side it calls now exists too. Add a one-line note referencing this plan file (`docs/superpowers/plans/2026-07-19-godot-backend-frontend-prep.md`) for full detail, mirroring how the file already references the Godot-deployment-readiness plan.

- [ ] **Step 5: Report remaining blockers to the user**

State explicitly what's still not done and explicitly out of scope for both plans combined:
1. Actual Web export (`export_presets.cfg` + running it) — user said to do this last.
2. Standing up the headless server (systemd) + Cloudflare Tunnel ingress for `/godot-ws` and `/godot/*` — user explicitly excluded this from this round.
3. `window.bridgeus_ws_url` is intentionally left unset in `GodotLobby.jsx` until #2 produces a real domain.
4. Nobody has set the real `GODOT_SERVICE_TOKEN` value anywhere yet — `.env.example` only documents the variable name; an actual secret still needs generating and setting in the real `.env` (not committed) before the headless server can successfully call `match-rooms/`.

No commit for this task — it's a review/documentation checkpoint.

---

## Self-review notes (completed during planning, not a task to execute)

- **Spec coverage:** all three P1 backend endpoints from `godot-backend-gap-analysis.md` §5 are covered (Tasks 1-6); the frontend iframe handoff from `godot-web-deployment-spec.md` §2 is covered (Task 8); the service-token contract from §4 is covered (Task 5-6). Infra (§4's systemd/Cloudflare) and the Web export (§1) are excluded per the user's explicit answers to the scoping question — not silently dropped.
- **No placeholders:** every step shows exact model/view/test code, not descriptions. The one intentionally-unset value (`bridgeus_ws_url`) is documented as unset with the reason (infra domain not decided, excluded from this round) rather than filled with a guessed URL.
- **Type/name consistency checked:** `GODOT_SERVICE_TOKEN` (Task 5's setting) is exactly the name `IsGodotServiceToken` reads (Task 5) and exactly the name already read by `godot/Globals/Backend.gd`'s `_ready()` from the prior plan — verified by Task 9's grep step. `X-Godot-Service-Token` header name is identical on both sides (already sent by `Backend.gd`'s `_post_with_service_token`, now checked by `IsGodotServiceToken`). `redirect_url`'s `/topic/<topic_id>?mode=match` format matches `TopicChat.jsx`'s actual `mode` query-param parsing exactly (verified against source, not assumed from the spec document). `Title`/`UserTitle`/`IssueReaction` field names (`owned`, `selected_id`, `color`, `counts`, `mine`, `emoji_index`) are consistent between the models, views, and tests throughout.
