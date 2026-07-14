# HANDOFF_P6 — cleanup package

Branch: `refactor/core-p6-cleanup`
Base requested: current `feat/Light`

## Scope commits

1. `8b66a8e refactor: centralize env helpers`
   - Added `core/env.py`.
   - Centralized `_env_bool`, dialogue session TTL, dialogue-session cache key builder, and anonymous display constants.
   - Updated settings, matching algorithm, H-H AI assist, REST views, consumers, and serializers to use the shared helpers/constants.

2. `dd47988 refactor: require semantic tree topic anchors`
   - Added explicit topic config accessors in `api/dialogue_topics.py`.
   - Removed semantic-tree global nuclear anchor/description fallback.
   - Changed semantic-tree state construction and analysis to require topic-provided anchors.
   - Changed semantic tree root name lookup to require `TOPIC_CONFIGS` title.
   - Added tests proving unknown topic and missing anchors raise explicit errors.

3. `4eff1a5 refactor: remove dead issue demo code`
   - Removed dead `backend/main.py`.
   - Removed demo `Issue` model, `/api/issues/` URL, and `IssueListCreateView`.
   - Added `api/migrations/0012_delete_issue.py`.
   - Removed unused `Issue`, `MatchMessageSerializer`, and model `User` imports.
   - Updated `DialoguePhase.from_turn_count` docs to reflect production use.

4. `docs: clarify backend runtime limits` (same commit as this handoff)
   - Documented SQLite + pgvector limitations in `backend/README.md`.
   - Aligned root `CLAUDE.md` backend version with settings (`Django 6.0.4`, Python 3.12+).

## Deletion list

- `backend/main.py`
- `api.models.Issue`
- `api.views.IssueListCreateView`
- `api.urls` route `path('issues/', ...)`
- `api.views` unused imports: `Issue`, `MatchMessageSerializer`
- `api.models` unused import: `django.contrib.auth.models.User`

## Validation

Passed:

- `DB_ENGINE=sqlite uv run python manage.py check`
- `DB_ENGINE=sqlite uv run python manage.py test api --keepdb --noinput`
- `DB_ENGINE=sqlite uv run pytest api/tests_websocket.py -q`
- `DB_ENGINE=sqlite uv run pytest apps/matching -q`
- `DB_ENGINE=sqlite uv run pytest chat/tests_embedding.py chat/tests_warm_nlp_models.py -q`
- `npm run lint`
- `npm run build`
- `_env_bool` source grep excluding refactor docs: only `backend/core/env.py`

Known pre-existing test gap:

- `DB_ENGINE=sqlite uv run pytest api/tests_websocket.py chat/tests*.py -q` fails during collection of legacy `chat/tests_*.py` modules because they import removed/absent models (`Conversation`, `Message`, `AISuggestion`, `StanceDrift`) from `chat.models`. `api/tests_websocket.py` passes when run directly, and the currently aligned chat tests listed above pass.

## Notes

- Literal grep across all `backend/` still sees the Part 6 acceptance text inside `backend/docs/refactor/EXECUTION_PLAN.md`; source grep excluding `backend/docs/**` has the intended single `_env_bool` definition.
- `backend/docs/refactor/p4_move_map.md` was absent on this branch, so it was read from `refactor/p7-integration` for orientation only. Actual edits followed the current `refactor/core-p6-cleanup` code shape.
