"""Godot 大廳一次性入場券的發放與兌換。

為什麼不是直接把主功能的 access token 交給 Godot：見
docs/superpowers/specs/2026-07-28-godot-identity-and-match-binding-design.md §D1。
"""
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from api.models import GodotEntryTicket

TICKET_TTL_SECONDS = 60


def issue_ticket(*, user, now=None) -> GodotEntryTicket:
    """發一張新券給 user。券是短效的，過期就重發，不做回收。"""
    current_time = now or timezone.now()
    return GodotEntryTicket.objects.create(
        token=secrets.token_urlsafe(32),
        user=user,
        expires_at=current_time + timedelta(seconds=TICKET_TTL_SECONDS),
    )


def redeem_ticket(*, token: str, now=None):
    """兌換一張券，回傳對應的 User；無效（不存在／已用過／逾期）一律回 None。

    不區分無效的原因——呼叫端是 Godot server，知道細節沒有用處，而區分了就等於
    給探測者一個 oracle。

    一次性必須靠行鎖：兩個 peer 同時送同一張券時，沒有 select_for_update 的話
    兩邊都會讀到 redeemed_at is None 而同時成功。of=("self",) 只鎖 ticket 這一列，
    不去鎖 join 進來的 user 列（沒必要，也避免拖慢其他碰 user 的交易）。
    """
    if not token:
        return None

    current_time = now or timezone.now()
    with transaction.atomic():
        ticket = (
            GodotEntryTicket.objects.select_for_update(of=("self",))
            .select_related("user")
            .filter(
                token=token,
                redeemed_at__isnull=True,
                expires_at__gt=current_time,
            )
            .first()
        )
        if ticket is None:
            return None
        ticket.redeemed_at = current_time
        ticket.save(update_fields=["redeemed_at"])
        return ticket.user
