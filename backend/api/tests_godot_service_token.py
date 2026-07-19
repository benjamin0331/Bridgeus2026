"""
pytest tests for the IsGodotServiceToken permission class.

The endpoint that uses it (POST /api/godot/match-rooms/) arrives in Task 6;
these tests exercise the permission class directly so its fail-closed
behaviour is pinned down independently of any view.

Run from backend/:
    pytest api/tests_godot_service_token.py -v
"""
from django.test import override_settings
from rest_framework.test import APIRequestFactory

from api.permissions import IsGodotServiceToken


def _request(token=None):
    factory = APIRequestFactory()
    headers = {}
    if token is not None:
        headers["HTTP_X_GODOT_SERVICE_TOKEN"] = token
    return factory.post("/api/godot/match-rooms/", {}, format="json", **headers)


@override_settings(GODOT_SERVICE_TOKEN="correct-token")
def test_correct_token_is_allowed():
    assert IsGodotServiceToken().has_permission(_request("correct-token"), None) is True


@override_settings(GODOT_SERVICE_TOKEN="correct-token")
def test_wrong_token_is_denied():
    assert IsGodotServiceToken().has_permission(_request("wrong-token"), None) is False


@override_settings(GODOT_SERVICE_TOKEN="correct-token")
def test_missing_header_is_denied():
    assert IsGodotServiceToken().has_permission(_request(None), None) is False


@override_settings(GODOT_SERVICE_TOKEN="")
def test_unconfigured_secret_fails_closed_even_with_matching_empty_header():
    """沒設定金鑰時一律拒絕——不能因為兩邊都是空字串就放行。"""
    assert IsGodotServiceToken().has_permission(_request(""), None) is False


@override_settings(GODOT_SERVICE_TOKEN="")
def test_unconfigured_secret_denies_any_token():
    assert IsGodotServiceToken().has_permission(_request("anything"), None) is False


@override_settings(GODOT_SERVICE_TOKEN="correct-token")
def test_token_prefix_is_not_accepted():
    """避免用 startswith 之類的比對寫法。"""
    assert IsGodotServiceToken().has_permission(_request("correct"), None) is False
    assert IsGodotServiceToken().has_permission(_request("correct-token-extra"), None) is False
