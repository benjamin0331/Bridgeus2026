"""Godot 綁定房的狀態判斷。

「這個人填過前測問卷沒有」在本專案只有一個合法判斷方式：他有沒有一筆對應這間房
的 MATCHED `MatchQueueEntry`（見 spec §D5、§10）。**不要**改用「分數是不是某個
特定值」之類的比對——真實分數可能剛好等於任何佔位值，而且那種判斷會散落在裁決、
API payload、資料分析三個地方各長一份。
"""
from django.db import transaction

from api.models import DialogueMatch, MatchQueueEntry

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


def pending_cancel_notice_for(match, user_id: int) -> str | None:
    """這位使用者還沒被告知過的作廢原因；沒有就回 None。

    作廢原因必須兩位都收得到，而觸發裁決的只會是其中一個請求（清理指令取消時
    甚至一個都沒有）。所以改成持久化 + 逐人確認：名單記在 stats.binding，
    每個人各自讀到一次。
    """
    binding = godot_binding_info(match)
    if binding is None:
        return None
    reason = binding.get("cancel_reason")
    if not reason:
        return None
    notified = binding.get("notified_user_ids") or []
    return None if user_id in notified else reason


def mark_cancel_notice_seen(match, user_id: int) -> None:
    """把這位使用者記成已通知（冪等）。

    名單要在鎖內重讀再寫。兩位參與者的輪詢（每 3 秒一次）會打到同一間作廢的房，
    各自從自己那份快照讀到還沒有對方 id 的名單、各自只加自己、再整包寫回 stats
    ——後寫的贏，前一位的標記就這樣消失，他下一次輪詢會再收到一次同樣的作廢
    通知。stats 的其他寫入者（matcher.py 的 _save_presence_state、
    resolve_godot_survey_gate）都是先 select_for_update 重讀才寫，這裡不該是
    唯一的例外。
    """
    # 鎖外先擋掉大多數呼叫：這支在每次輪詢都會被問到，沒有作廢原因時不值得
    # 為了確認「沒事」而開一個 transaction。
    if godot_binding_info(match) is None or not binding_cancel_reason(match):
        return

    with transaction.atomic():
        locked = DialogueMatch.objects.select_for_update().get(pk=match.pk)
        binding = godot_binding_info(locked)
        if binding is None or not binding.get("cancel_reason"):
            return
        notified = list(binding.get("notified_user_ids") or [])
        if user_id in notified:
            return
        notified.append(user_id)
        stats = dict(locked.stats or {})
        new_binding = dict(stats.get(BINDING_STATS_KEY) or {})
        new_binding["notified_user_ids"] = notified
        stats[BINDING_STATS_KEY] = new_binding
        locked.stats = stats
        locked.save(update_fields=["stats"])

    # 呼叫端手上那份快照跟著更新，免得同一個請求後面又讀到舊名單。
    match.stats = locked.stats


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
