"""Environment helpers and shared backend constants."""

import os


SESSION_TTL_SECONDS = 60 * 60 * 12
ANONYMOUS_MATCH_USER_NAME = "匿名對話者"
ANONYMOUS_MESSAGE_SENDER_NAME = "匿名使用者"


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def dialogue_session_cache_key(session_id: str) -> str:
    return f"dialogue_session:{session_id}"
