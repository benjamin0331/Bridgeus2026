"""Per-user 送訊速率限制與輸入冷卻狀態。

與 `input_gate` 分開的理由：input_gate 必須是純規則、零 I/O，才能安全放在
WebSocket 的同步路徑上；這裡要碰快取（prod 為 Redis，dev 為 LocMem），
是 I/O。兩者的觸發條件也不同——rate limit **獨立於內容判斷之外**，
擋的是自動化灌訊息，不管內容是否有意義。

限制對 H-H 與 H-AI 兩種對話室同時生效。

快取後端由 settings 決定（`USE_REDIS_CACHE=1` → RedisCache）。多 worker
部署必須開 Redis，否則每個 worker 各自計數，實際上限會變成 N 倍。
"""

import time

from asgiref.sync import sync_to_async
from django.core.cache import cache

from apps.matching.services.input_gate import (
    COOLDOWN_SECONDS,
    MAX_MESSAGES_PER_MINUTE,
    MIN_SEND_INTERVAL_SECONDS,
)

_WINDOW_SECONDS = 60


def _last_send_key(user_id: int) -> str:
    return f"input_rate:last:{user_id}"


def _window_key(user_id: int) -> str:
    return f"input_rate:window:{user_id}"


def _cooldown_key(scope: str) -> str:
    return f"input_cooldown:{scope}"


def check_rate_limit(user_id: int) -> dict:
    """回傳 {"allowed": bool, "reason": str|None, "retry_after": float}。

    reason: "too_fast"（低於最小間隔）/ "too_many"（超過每分鐘上限）。

    這裡的 read-modify-write 不是原子的。單一使用者短時間連打時，最壞情況
    是多放行一兩則——對「防自動化灌訊息」這個目的可以接受，換取不需要
    Lua script 或分散式鎖。
    """
    now = time.time()

    last_send = cache.get(_last_send_key(user_id))
    if last_send is not None:
        elapsed = now - float(last_send)
        if elapsed < MIN_SEND_INTERVAL_SECONDS:
            return {
                "allowed": False,
                "reason": "too_fast",
                "retry_after": round(MIN_SEND_INTERVAL_SECONDS - elapsed, 2),
            }

    window_key = _window_key(user_id)
    # add() 只在 key 不存在時寫入，所以視窗的 TTL 由該分鐘第一則訊息決定，
    # 不會被後續訊息延長成滑動視窗。
    cache.add(window_key, 0, timeout=_WINDOW_SECONDS)
    try:
        sent_this_minute = cache.incr(window_key)
    except ValueError:
        # key 剛好在 add() 與 incr() 之間過期。
        cache.set(window_key, 1, timeout=_WINDOW_SECONDS)
        sent_this_minute = 1

    if sent_this_minute > MAX_MESSAGES_PER_MINUTE:
        return {
            "allowed": False,
            "reason": "too_many",
            "retry_after": float(_WINDOW_SECONDS),
        }

    cache.set(_last_send_key(user_id), now, timeout=_WINDOW_SECONDS)
    return {"allowed": True, "reason": None, "retry_after": 0.0}


def start_cooldown(scope: str, seconds: int = COOLDOWN_SECONDS) -> int:
    """啟動輸入冷卻。scope 建議用 f"ai:{session_id}:{user_id}" 之類的字串。"""
    cache.set(_cooldown_key(scope), time.time() + seconds, timeout=seconds)
    return seconds


def cooldown_remaining(scope: str) -> int:
    """剩餘冷卻秒數；未在冷卻中回傳 0。"""
    expires_at = cache.get(_cooldown_key(scope))
    if expires_at is None:
        return 0
    remaining = float(expires_at) - time.time()
    return int(remaining) + 1 if remaining > 0 else 0


def clear_cooldown(scope: str) -> None:
    cache.delete(_cooldown_key(scope))


def reset_rate_limit(user_id: int) -> None:
    """僅供測試使用——清掉某使用者的速率狀態。"""
    cache.delete(_last_send_key(user_id))
    cache.delete(_window_key(user_id))


acheck_rate_limit = sync_to_async(check_rate_limit, thread_sensitive=False)
astart_cooldown = sync_to_async(start_cooldown, thread_sensitive=False)
acooldown_remaining = sync_to_async(cooldown_remaining, thread_sensitive=False)
