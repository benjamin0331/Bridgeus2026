"""
pytest tests for Godot 一次性入場券（服務層 + 端點）。

Run from backend/:
    pytest api/tests_godot_tickets.py -v
"""
import threading
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.utils import timezone

from api.godot_tickets import TICKET_TTL_SECONDS, issue_ticket, redeem_ticket
from api.models import GodotEntryTicket

User = get_user_model()


@pytest.mark.django_db
def test_issue_ticket_creates_unredeemed_ticket_with_ttl():
    user = User.objects.create_user(username="u1", password="pw")

    ticket = issue_ticket(user=user)

    assert ticket.user_id == user.id
    assert ticket.redeemed_at is None
    assert len(ticket.token) >= 32
    delta = ticket.expires_at - timezone.now()
    assert timedelta(seconds=TICKET_TTL_SECONDS - 5) < delta <= timedelta(
        seconds=TICKET_TTL_SECONDS
    )


@pytest.mark.django_db
def test_issue_ticket_tokens_are_unique():
    user = User.objects.create_user(username="u1", password="pw")

    tokens = {issue_ticket(user=user).token for _ in range(5)}

    assert len(tokens) == 5


@pytest.mark.django_db
def test_redeem_returns_user_and_marks_redeemed():
    user = User.objects.create_user(username="u1", password="pw")
    ticket = issue_ticket(user=user)

    redeemed = redeem_ticket(token=ticket.token)

    assert redeemed is not None
    assert redeemed.id == user.id
    ticket.refresh_from_db()
    assert ticket.redeemed_at is not None


@pytest.mark.django_db
def test_redeem_twice_fails_the_second_time():
    """一次性：第二次兌換必須失敗，否則同一張券可以讓兩個 peer 冒充同一人。"""
    user = User.objects.create_user(username="u1", password="pw")
    ticket = issue_ticket(user=user)

    assert redeem_ticket(token=ticket.token) is not None
    assert redeem_ticket(token=ticket.token) is None


@pytest.mark.django_db
def test_redeem_expired_ticket_fails():
    user = User.objects.create_user(username="u1", password="pw")
    ticket = issue_ticket(user=user)
    ticket.expires_at = timezone.now() - timedelta(seconds=1)
    ticket.save(update_fields=["expires_at"])

    assert redeem_ticket(token=ticket.token) is None


@pytest.mark.django_db
def test_redeem_unknown_token_fails():
    assert redeem_ticket(token="does-not-exist") is None


@pytest.mark.django_db
def test_redeem_empty_token_fails():
    assert redeem_ticket(token="") is None


@pytest.mark.django_db(transaction=True)
def test_concurrent_redeem_only_one_succeeds():
    """兩個 peer 同時送同一張券，只能有一個拿到身份。

    一次性語意靠 select_for_update 的行鎖保證；沒有鎖的話兩邊都會讀到
    redeemed_at is None 而同時成功。需要 transaction=True 才有真實的 commit 邊界。
    """
    user = User.objects.create_user(username="u1", password="pw")
    ticket = issue_ticket(user=user)
    results = []
    barrier = threading.Barrier(2)

    def worker():
        try:
            barrier.wait()
            results.append(redeem_ticket(token=ticket.token))
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 2
    assert sum(1 for result in results if result is not None) == 1
    assert GodotEntryTicket.objects.get(pk=ticket.pk).redeemed_at is not None
