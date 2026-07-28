"""
pytest tests for Godot 一次性入場券（服務層 + 端點）。

Run from backend/:
    pytest api/tests_godot_tickets.py -v
"""
import threading
import uuid
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from api.godot_tickets import TICKET_TTL_SECONDS, issue_ticket, redeem_ticket
from api.models import GodotEntryTicket

User = get_user_model()


@pytest.mark.django_db
def test_issue_ticket_creates_unredeemed_ticket_with_ttl():
    user = User.objects.create_user(username="u1", password="pw")

    ticket = issue_ticket(user=user)

    assert ticket.user_id == user.id
    assert ticket.redeemed_at is None
    assert len(ticket.token) == 43  # token_urlsafe(32) 固定產出 43 字元；長度不等於熵，這裡只是釘住格式
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
def test_redeem_with_now_before_expiry_succeeds():
    """釘住 redeem_ticket 的 now 參數確實是過期判斷用的那個時間點。"""
    user = User.objects.create_user(username="u1", password="pw")
    ticket = issue_ticket(user=user)

    past = ticket.expires_at - timedelta(seconds=1)
    assert redeem_ticket(token=ticket.token, now=past) is not None


@pytest.mark.django_db
def test_redeem_with_now_after_expiry_fails():
    user = User.objects.create_user(username="u1", password="pw")
    ticket = issue_ticket(user=user)

    future = ticket.expires_at + timedelta(seconds=1)
    assert redeem_ticket(token=ticket.token, now=future) is None


@pytest.mark.django_db
def test_redeem_unknown_token_fails():
    assert redeem_ticket(token="does-not-exist") is None


@pytest.mark.django_db
def test_redeem_empty_token_fails():
    assert redeem_ticket(token="") is None


@pytest.mark.django_db(transaction=True)
def test_concurrent_redeem_only_one_succeeds():
    """兩個 peer 同時送同一張券，只能有一個拿到身份。

    一次性語意靠 filter().update() 的 compare-and-swap 保證原子性；沒有 CAS 的話
    兩邊都可能讀到 redeemed_at is None 而同時成功。需要 transaction=True 才有真實的
    commit 邊界。

    barrier 只保證兩條執行緒同時「起跑」，不保證同時「執行到臨界區」——排程器仍然
    可能讓其中一條先跑完，這樣即使原子性被拿掉，測試也可能因為運氣好而通過。用 20
    次迭代把這個機率壓低，讓迴歸有很高機率被抓到。
    """
    for _ in range(20):
        user = User.objects.create_user(username=f"u{uuid.uuid4().hex}", password="pw")
        ticket = issue_ticket(user=user)
        results = []
        errors = []
        barrier = threading.Barrier(2)

        def worker():
            try:
                barrier.wait(timeout=5)
                results.append(redeem_ticket(token=ticket.token))
            except Exception as exc:  # noqa: BLE001 - 蒐集起來在主執行緒重新拋出
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        if errors:
            raise errors[0]

        assert len(results) == 2
        assert sum(1 for result in results if result is not None) == 1
        assert GodotEntryTicket.objects.get(pk=ticket.pk).redeemed_at is not None


_SERVICE_TOKEN = "svc-token"


def _service_client(token=_SERVICE_TOKEN):
    client = APIClient()
    client.credentials(HTTP_X_GODOT_SERVICE_TOKEN=token)
    return client


@pytest.mark.django_db
def test_issue_endpoint_requires_authentication():
    response = APIClient().post("/api/godot/tickets/", {}, format="json")

    assert response.status_code == 401


@pytest.mark.django_db
def test_issue_endpoint_returns_ticket_with_real_jwt():
    """用真 JWT 而非 force_authenticate——後者不走 authentication 流程，
    驗證不到這個 view 實際上用哪個 authentication class。"""
    user = User.objects.create_user(username="u1", password="pw")
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")

    response = client.post("/api/godot/tickets/", {}, format="json")

    assert response.status_code == 201
    # expires_in 是從 ticket.expires_at 動態算出來的（用另一個 timezone.now() 呼叫），
    # 跟建立當下的 TICKET_TTL_SECONDS 之間必然有微小、非負的時間差，所以只能給
    # 容許範圍、不能斷言完全相等。
    assert TICKET_TTL_SECONDS - 5 <= response.data["expires_in"] <= TICKET_TTL_SECONDS
    assert GodotEntryTicket.objects.get(token=response.data["ticket"]).user_id == user.id


@override_settings(GODOT_SERVICE_TOKEN=_SERVICE_TOKEN)
@pytest.mark.django_db
def test_redeem_endpoint_returns_user_id():
    user = User.objects.create_user(username="u1", password="pw")
    ticket = issue_ticket(user=user)

    response = _service_client().post(
        "/api/godot/tickets/redeem/", {"ticket": ticket.token}, format="json"
    )

    assert response.status_code == 200
    assert response.data == {"user_id": user.id}


@override_settings(GODOT_SERVICE_TOKEN=_SERVICE_TOKEN)
@pytest.mark.django_db
def test_redeem_endpoint_rejects_wrong_service_token():
    user = User.objects.create_user(username="u1", password="pw")
    ticket = issue_ticket(user=user)

    response = _service_client("wrong").post(
        "/api/godot/tickets/redeem/", {"ticket": ticket.token}, format="json"
    )

    assert response.status_code == 403
    assert GodotEntryTicket.objects.get(pk=ticket.pk).redeemed_at is None


@override_settings(GODOT_SERVICE_TOKEN=_SERVICE_TOKEN)
@pytest.mark.django_db
def test_redeem_endpoint_ignores_stale_authorization_header():
    """authentication_classes 必須清空：預設的 JWTAuthentication 遇到壞掉的
    Authorization header 會先丟 401，根本輪不到服務金鑰驗證。"""
    user = User.objects.create_user(username="u1", password="pw")
    ticket = issue_ticket(user=user)
    client = APIClient()
    client.credentials(
        HTTP_X_GODOT_SERVICE_TOKEN=_SERVICE_TOKEN,
        HTTP_AUTHORIZATION="Bearer garbage.token.value",
    )

    response = client.post(
        "/api/godot/tickets/redeem/", {"ticket": ticket.token}, format="json"
    )

    assert response.status_code == 200
    assert response.data["user_id"] == user.id


@override_settings(GODOT_SERVICE_TOKEN=_SERVICE_TOKEN)
@pytest.mark.django_db
def test_redeem_endpoint_rejects_used_ticket():
    user = User.objects.create_user(username="u1", password="pw")
    ticket = issue_ticket(user=user)
    redeem_ticket(token=ticket.token)

    response = _service_client().post(
        "/api/godot/tickets/redeem/", {"ticket": ticket.token}, format="json"
    )

    assert response.status_code == 400
    assert response.data == {"detail": "入場券無效。"}


@override_settings(GODOT_SERVICE_TOKEN=_SERVICE_TOKEN)
@pytest.mark.django_db
@pytest.mark.parametrize("payload", [{}, {"ticket": ""}, {"ticket": 123}, {"ticket": None}])
def test_redeem_endpoint_rejects_missing_and_non_string_ticket(payload):
    response = _service_client().post(
        "/api/godot/tickets/redeem/", payload, format="json"
    )
    assert response.status_code == 400
    assert response.data == {"detail": "入場券無效。"}


@pytest.mark.django_db
def test_guest_login_endpoint_is_gone():
    """訪客登入會產生無主帳號（user_id 對不到真實受試者），已移除。
    見 integration spec §5.4。"""
    response = APIClient().post("/api/guest/", {"nickname": "訪客"}, format="json")

    assert response.status_code == 404
