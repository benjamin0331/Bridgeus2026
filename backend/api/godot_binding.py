"""Godot 綁定房的狀態判斷。

「這個人填過前測問卷沒有」在本專案只有一個合法判斷方式：他有沒有一筆對應這間房
的 MATCHED `MatchQueueEntry`（見 spec §D5、§10）。**不要**改用「分數是不是某個
特定值」之類的比對——真實分數可能剛好等於任何佔位值，而且那種判斷會散落在裁決、
API payload、資料分析三個地方各長一份。
"""
from api.models import MatchQueueEntry

BINDING_STATS_KEY = "binding"


def godot_binding_info(match) -> dict | None:
    """回傳這間房的 binding 資訊；不是 Godot 綁定房就回 None。"""
    if match is None:
        return None
    stats = match.stats if isinstance(match.stats, dict) else {}
    binding = stats.get(BINDING_STATS_KEY)
    if not isinstance(binding, dict) or binding.get("source") != "godot":
        return None
    return binding


def survey_deadline_of(match):
    """回傳這間 Godot 房的問卷期限（datetime）；不是綁定房或沒記期限就回 None。"""
    binding = godot_binding_info(match)
    if binding is None:
        return None
    from django.utils.dateparse import parse_datetime
    from django.utils import timezone as dj_timezone

    raw = binding.get("survey_deadline")
    if not raw:
        return None
    parsed = parse_datetime(raw) if isinstance(raw, str) else raw
    if parsed is None:
        return None
    if dj_timezone.is_naive(parsed):
        return dj_timezone.make_aware(parsed, dj_timezone.get_current_timezone())
    return parsed


def binding_cancel_reason(match) -> str | None:
    """房間若因裁決作廢，回傳原因代碼；否則 None。"""
    binding = godot_binding_info(match)
    return binding.get("cancel_reason") if binding else None


def match_pretest_state(match) -> dict:
    """這間房兩位參與者各自填完前測問卷了沒。"""
    completed = set(
        MatchQueueEntry.objects.filter(
            match=match,
            status=MatchQueueEntry.Status.MATCHED,
        ).values_list("user_id", flat=True)
    )
    user_a_done = match.user_a_id in completed
    user_b_done = match.user_b_id in completed
    return {
        "user_a_done": user_a_done,
        "user_b_done": user_b_done,
        "both_done": user_a_done and user_b_done,
    }
