# HANDOFF P4

## Scope

Part 4b mechanical move only. Helpers from `api/views.py` were moved according to `backend/docs/refactor/p4_move_map.md`; call sites and test patch lookups were updated to the new service modules. No helper function behavior was intentionally changed.

`backend/docs/refactor/handoffs/HANDOFF_P3.md` was not present in this checkout, so P3-specific test commands could not be read. Available handoff files were only `HANDOFF_P0.md` and `_TEMPLATE.md`.

## Moved

### `api/services/dialogue_session.py`

- `_session_cache_key`
- `_cache_dialogue_session_record`
- `_rebuild_session_state_from_turns`
- `_dialogue_session_cache_payload_from_record`
- `_persist_dialogue_session_record`
- `_restore_dialogue_session_record_for_user`
- `_dialogue_session_response_payload`
- `_update_ai_session_stance_drift`
- `_build_topic_config`
- `_get_dialogue_runtime`
- `get_dialogue_agent`

### `api/services/stance_scoring.py`

- `_get_survey_scoring_config`
- `_get_open_answer`
- `_compute_user_stance_score`
- `_resolve_stance_category`
- `_resolve_stances`
- `_resolve_open_answers`

### `api/services/history.py`

- `_semantic_tree_root_name`
- `_semantic_tree_root_name_for_topic_id`
- `_get_history_ai_record_for_user`
- `_history_ai_turns`
- `_history_ai_messages`
- `_history_match_messages`
- `_history_ai_summary`
- `_history_match_summary`
- `_history_ai_detail`
- `_history_match_detail`

### `api/services/room_state.py`

- `_get_other_user`
- `_match_presence_fields`
- `_build_matching_state_payload`
- `_room_match_state_status`
- `_get_latest_room_stance_drift`
- `_build_room_messages_payload`
- `_get_room_match_for_user`
- `_touch_room_match_for_user_activity`

## Not Moved

- `_get_dialogue_session_record_for_user` remains in `api/views.py` as specified by the move map because it builds DRF `Response` objects.
- No helper was left in `api/views.py` because of a newly discovered circular import.

## Import Updates

- `api/views.py` imports helpers directly from `api.services.dialogue_session`, `api.services.stance_scoring`, `api.services.history`, and `api.services.room_state`.
- `api/consumers.py` now imports dialogue session helpers from `api.services.dialogue_session`, not `api.views`.
- Test patch/import lookups were updated for:
  - `_resolve_stance_category` -> `api.services.stance_scoring`
  - `build_q9_embedding` lookup used by `_build_topic_config` -> `api.services.dialogue_session`
  - websocket `get_dialogue_agent` patch -> `api.services.dialogue_session`

## Verification

Passed:

```bash
uv run python manage.py check
DB_ENGINE=sqlite uv run python manage.py test api --keepdb --noinput
DB_ENGINE=sqlite uv run pytest api/tests_websocket.py
rg "from api.views import" backend/api/consumers.py
```

`rg "from api.views import" backend/api/consumers.py` returned no matches.

Not green:

```bash
DB_ENGINE=sqlite uv run pytest api/tests_websocket.py chat/tests*.py
```

This fails during collection in the legacy `chat/tests*.py` files because `chat.models` no longer exports `AISuggestion`, `Conversation`, or `StanceDrift`. The failure happens before running tests and is not from the service extraction import changes.
