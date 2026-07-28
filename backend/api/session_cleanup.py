"""找出「該關而沒關」的 AI 對話 session。

歷史上有兩個地方會漏掉關閉 DialogueSessionRecord，導致 status 永遠停在
active：

  1. 送出後測問卷時沒有關閉對應的 session（已於 PostDialogueResponseView 修正）
  2. 建立新 session 時沒有關閉同 user+topic 的舊 session（已於
     DialogueSessionCreateView 修正）

修正只對之後的新資料生效，既有的殘留仍會讓 /api/dialogue/sessions/latest/
一直回報「有對話可以繼續」。這個模組提供判定規則，給 data migration 與
manage.py 指令共用，兩邊行為保證一致。

判定規則（每組 user+topic）：
  A. 除了最新一筆以外的 active session —— 已被「開始新對話」取代，
     latest 永遠撈不到它們，留著只會在最新那筆被關掉後浮上來。
  B. 最新那筆若已經有對應的後測問卷 —— 那場對話已經正式結束。

刻意不碰的：最新且沒有後測問卷的 session。那是使用者真的還在進行、
應該要能繼續的對話。
"""

from collections import defaultdict


def find_closable_session_ids(*, DialogueSessionRecord, PostDialogueResponse):
    """回傳應該被關閉的 session_id 清單。純查詢，不寫入任何資料。

    兩個 model 由呼叫端傳入，data migration 才能用 apps.get_model() 取得的
    歷史版本 model，而不是直接 import 現在的 api.models。
    """
    active = list(
        DialogueSessionRecord.objects.filter(status="active")
        .order_by("-last_activity_at", "-id")
        .values_list("id", "session_id", "user_id", "topic_id")
    )
    if not active:
        return []

    answered = {
        session_id
        for session_id in PostDialogueResponse.objects.values_list(
            "session_id", flat=True
        )
        if session_id
    }

    grouped = defaultdict(list)
    for _pk, session_id, user_id, topic_id in active:
        grouped[(user_id, topic_id)].append(session_id)

    closable = []
    for session_ids in grouped.values():
        newest, superseded = session_ids[0], session_ids[1:]
        closable.extend(superseded)  # A：已被新對話取代
        if newest in answered:
            closable.append(newest)  # B：已填後測問卷，對話已結束
    return closable
