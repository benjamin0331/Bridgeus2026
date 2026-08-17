"""Input gate 計數的持久化，H-AI 與 H-H 共用介面。

計數必須落庫（不是只放快取），因為 `invalid_input_count` 與衍生的
`invalid_ratio` 是實驗資料——研究端要能匯出，作為受試者樣本排除的依據。
**這裡不自動排除任何樣本**，只產出欄位。

H-AI 掛在 `DialogueSessionRecord`，H-H 掛在 `MatchInputGateStat`。
兩邊欄位同名，`finalize_*` 產出的指標定義也相同。

所有遞增都走 `F()` 表達式，避免兩個 worker 同時處理同一位使用者時互相覆蓋。
"""

from asgiref.sync import sync_to_async
from django.db.models import F


# ═══════════════════════════════════════════════════════════
# H-AI — DialogueSessionRecord
# ═══════════════════════════════════════════════════════════

def record_ai_attempt(session_id: str, *, blocked: bool, profanity: bool = False) -> int:
    """記一次送出嘗試，回傳更新後的 `invalid_input_count`。

    blocked=True  → 攔截計數 +1（連續計數與累計數同時 +1）
    blocked=False → 連續計數歸零（累計數不動）
    profanity=True → 這次攔截的原因是單字粗口，另外再 +1 到 profanity_only_total。
                     只在 blocked=True 時有意義。
    """
    if profanity and not blocked:
        raise ValueError("profanity 只在 blocked=True 時有意義")

    from api.models import DialogueSessionRecord

    queryset = DialogueSessionRecord.objects.filter(session_id=session_id)
    if blocked:
        updates = {
            "input_attempt_total": F("input_attempt_total") + 1,
            "invalid_input_total": F("invalid_input_total") + 1,
            "invalid_input_count": F("invalid_input_count") + 1,
        }
        if profanity:
            updates["profanity_only_total"] = F("profanity_only_total") + 1
        queryset.update(**updates)
    else:
        queryset.update(
            input_attempt_total=F("input_attempt_total") + 1,
            invalid_input_count=0,
        )
    return (
        queryset.values_list("invalid_input_count", flat=True).first() or 0
    )


def finalize_ai_session_metrics(session_id: str) -> dict | None:
    """對話結束時計算並儲存 `invalid_ratio` 與 `substantive_turn_count`。

    invalid_ratio          = 攔截訊息數 / 總送出嘗試數
    substantive_turn_count = 通過閘門且非短回應的發言數
    """
    from api.models import AIConversation, DialogueSessionRecord
    from apps.matching.services.input_gate import is_substantive_message

    record = DialogueSessionRecord.objects.filter(session_id=session_id).first()
    if record is None:
        return None

    attempts = record.input_attempt_total
    record.invalid_ratio = (
        round(record.invalid_input_total / attempts, 4) if attempts else None
    )
    # 被攔截的訊息從未寫進 AIConversation，所以這裡掃到的都是通過閘門的發言。
    record.substantive_turn_count = sum(
        1
        for prompt in AIConversation.objects.filter(
            user_id=record.user_id,
            session_id=session_id,
        ).values_list("user_prompt", flat=True)
        if is_substantive_message(prompt or "")
    )
    record.save(update_fields=["invalid_ratio", "substantive_turn_count"])
    return {
        "invalid_input_total": record.invalid_input_total,
        "input_attempt_total": attempts,
        "invalid_ratio": record.invalid_ratio,
        "substantive_turn_count": record.substantive_turn_count,
    }


# ═══════════════════════════════════════════════════════════
# H-H — MatchInputGateStat
# ═══════════════════════════════════════════════════════════

def record_match_attempt(match_id: int, user_id: int, *, blocked: bool) -> int:
    """記一次送出嘗試，回傳更新後的 `invalid_input_count`。"""
    from api.models import MatchInputGateStat

    MatchInputGateStat.objects.get_or_create(match_id=match_id, user_id=user_id)
    queryset = MatchInputGateStat.objects.filter(
        match_id=match_id, user_id=user_id
    )
    if blocked:
        queryset.update(
            input_attempt_total=F("input_attempt_total") + 1,
            invalid_input_total=F("invalid_input_total") + 1,
            invalid_input_count=F("invalid_input_count") + 1,
        )
    else:
        queryset.update(
            input_attempt_total=F("input_attempt_total") + 1,
            invalid_input_count=0,
        )
    return (
        queryset.values_list("invalid_input_count", flat=True).first() or 0
    )


def finalize_match_metrics(match_id: int, user_id: int) -> dict | None:
    from api.models import MatchInputGateStat, MatchMessage
    from apps.matching.services.input_gate import is_substantive_message

    stat = MatchInputGateStat.objects.filter(
        match_id=match_id, user_id=user_id
    ).first()
    if stat is None:
        return None

    attempts = stat.input_attempt_total
    stat.invalid_ratio = (
        round(stat.invalid_input_total / attempts, 4) if attempts else None
    )
    stat.substantive_turn_count = sum(
        1
        for content in MatchMessage.objects.filter(
            match_id=match_id, sender_id=user_id
        ).values_list("content", flat=True)
        if is_substantive_message(content or "")
    )
    stat.save(update_fields=["invalid_ratio", "substantive_turn_count"])
    return {
        "invalid_input_total": stat.invalid_input_total,
        "input_attempt_total": attempts,
        "invalid_ratio": stat.invalid_ratio,
        "substantive_turn_count": stat.substantive_turn_count,
    }


arecord_ai_attempt = sync_to_async(record_ai_attempt, thread_sensitive=False)
arecord_match_attempt = sync_to_async(record_match_attempt, thread_sensitive=False)
