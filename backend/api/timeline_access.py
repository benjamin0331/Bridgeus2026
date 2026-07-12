"""Access gate for the participant-facing CCND timeline.

Experiment-design constraint (this is the whole point of the module): a
participant must not replay their own CCND tree before they have answered the
CCND self-report items. Those live in TWO places in the M6 flow —

    1. post-dialogue questionnaire  -> Part C3  (CCND 認知評估)
    2. Part F platform feedback     -> F4 ux_ccnd (概念認知網路圖)

— so the gate only opens once the WHOLE flow is done, i.e. the conversation's
PostDialogueResponse exists *and* carries a PlatformFeedback. Gating only on the
questionnaire would still leave F4 open to contamination.

Two escape hatches stop an abandoned questionnaire from locking a participant
out of their own history forever:
  - a researcher-issued CCNDTimelineUnlock override, and
  - an automatic unlock 12h after the conversation ended.

Timestamps here are about *access control*, not analysis. Do not confuse them
with the analysis time axis: all temporal CCND analysis keys off
``sourceTimestamp`` (original message time), never ``recordedAt``/``analyzedAt``.
"""

from datetime import timedelta

from django.utils import timezone

from .models import CCNDTimelineUnlock, PostDialogueResponse

AUTO_UNLOCK_AFTER = timedelta(hours=12)

KIND_AI = "ai"
KIND_MATCH = "match"

LOCKED_DETAIL = (
    "請先完成對話後問卷（含平台體驗回饋）才能回顧這場對話的概念認知網路圖。"
)

# reasons
REASON_QUESTIONNAIRE = "questionnaire_complete"
REASON_OVERRIDE = "researcher_override"
REASON_AUTO = "auto_unlock_elapsed"
REASON_LOCKED = "questionnaire_incomplete"


def _conversation_filter(kind: str, conversation_id: str) -> dict:
    """PostDialogueResponse links to a conversation by session_id (H-AI) or
    room_id (H-H)."""
    if kind == KIND_AI:
        return {"session_id": conversation_id}
    return {"room_id": conversation_id}


def conversation_ended_at(kind: str, conversation):
    """When the conversation stopped, for the 12h auto-unlock clock.

    H-AI sessions always carry last_activity_at. H-H matches only have
    closed_at, which is NULL while the room is still open — an open room has not
    ended, so it never auto-unlocks.
    """
    if conversation is None:
        return None
    if kind == KIND_AI:
        return getattr(conversation, "last_activity_at", None)
    return getattr(conversation, "closed_at", None)


def timeline_unlock_state(*, user_id: int, kind: str, conversation_id: str, conversation=None) -> dict:
    """Whether this participant may replay the CCND timeline of this conversation.

    Returns ``{"unlocked": bool, "reason": str, "unlocks_at": datetime|None}``.
    ``unlocks_at`` is when the 12h auto-unlock will fire (None if the
    conversation has not ended, or if it is already unlocked for another
    reason).
    """
    finished_questionnaire = PostDialogueResponse.objects.filter(
        user_id=user_id,
        platform_feedback__isnull=False,
        **_conversation_filter(kind, conversation_id),
    ).exists()
    if finished_questionnaire:
        return {"unlocked": True, "reason": REASON_QUESTIONNAIRE, "unlocks_at": None}

    has_override = CCNDTimelineUnlock.objects.filter(
        user_id=user_id,
        kind=kind,
        conversation_id=conversation_id,
    ).exists()
    if has_override:
        return {"unlocked": True, "reason": REASON_OVERRIDE, "unlocks_at": None}

    ended_at = conversation_ended_at(kind, conversation)
    unlocks_at = (ended_at + AUTO_UNLOCK_AFTER) if ended_at else None
    if unlocks_at and timezone.now() >= unlocks_at:
        return {"unlocked": True, "reason": REASON_AUTO, "unlocks_at": None}

    return {"unlocked": False, "reason": REASON_LOCKED, "unlocks_at": unlocks_at}
