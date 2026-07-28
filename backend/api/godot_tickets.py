"""Godot 大廳一次性入場券的發放與兌換。

為什麼不是直接把主功能的 access token 交給 Godot：見
docs/superpowers/specs/2026-07-28-godot-identity-and-match-binding-design.md §D1。
"""
import logging
import secrets
from datetime import timedelta

from django.utils import timezone

from api.models import GodotEntryTicket

logger = logging.getLogger(__name__)

TICKET_TTL_SECONDS = 60


def issue_ticket(*, user, now=None) -> GodotEntryTicket:
    """發一張新券給 user。券是短效的，過期就重發，不做回收。

    同一個 user 同時持有多張未兌換的券是合法的（例如兩個瀏覽器分頁各自發了一張）
    ——威脅模型防的是冒充「別人」，不是自己多留幾張備用券。
    """
    current_time = now if now is not None else timezone.now()
    return GodotEntryTicket.objects.create(
        token=secrets.token_urlsafe(32),
        user=user,
        expires_at=current_time + timedelta(seconds=TICKET_TTL_SECONDS),
    )


def _log_redeem_failure(*, token: str, now) -> None:
    """兌換失敗時把真正的原因記在 server log。回傳值仍然不區分原因（見 redeem_ticket），
    但沒有這段的話，線上出問題時「玩家進不了大廳」完全無從查起。"""
    ticket = GodotEntryTicket.objects.filter(token=token).first()
    if ticket is None:
        reason = "查無此券"
    elif ticket.redeemed_at is not None:
        reason = "已兌換過"
    else:
        reason = "已逾期"
    logger.info("Godot 入場券兌換失敗（%s）", reason)


def redeem_ticket(*, token: str, now=None):
    """兌換一張券，回傳對應的 User；無效（不存在／已用過／逾期）一律回 None。

    不區分無效的原因——Godot server 收到 None 之後不管是哪一種情況都是同一個動作
    （disconnect_peer），區分了也沒有消費者去用這個資訊。詳細原因改記在 server log
    （見 _log_redeem_failure），供事後排查用。

    一次性靠 compare-and-swap 而不是 select_for_update 讀鎖再寫：一句有 WHERE 條件
    的 UPDATE 在任何後端、任何隔離等級下都是單一陳述式、天生原子；select_for_update
    則在 SQLite 上會被 Django 靜默略過（Django 6 的 SQLCompiler 只在
    features.has_select_for_update 為真時才組出 FOR UPDATE 子句，SQLite 這個 flag
    是 False），沒有鎖卻也不報錯，一次性保證就這樣悄悄消失。專案預設走 postgres，
    但沒設 .env 的環境會退回 sqlite，CAS 在所有後端行為一致，不依賴這個假設。
    """
    if not token:
        return None

    current_time = now if now is not None else timezone.now()
    updated = GodotEntryTicket.objects.filter(
        token=token,
        redeemed_at__isnull=True,
        expires_at__gt=current_time,
    ).update(redeemed_at=current_time)
    if not updated:
        _log_redeem_failure(token=token, now=current_time)
        return None
    return GodotEntryTicket.objects.select_related("user").get(token=token).user
