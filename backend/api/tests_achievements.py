"""
pytest tests for the achievement system.

Run from backend/:
    DB_ENGINE=sqlite uv run pytest api/tests_achievements.py -v
"""
from datetime import datetime, timedelta, timezone as dt_timezone

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from api.godot_tickets import issue_ticket

from api.achievement_rules import RULES, evaluate
from api.achievements import (
    ALL_ACHIEVEMENTS_CODE,
    CATALOG,
    CATALOG_BY_CODE,
    CATEGORY_TITLES,
    CLEAN_DIALOGUE_COUNT,
    COMPLETE_FLOW_COUNT,
    MULTI_CHANGE_COUNT,
    RETURNING_DAYS,
    SAME_DAY_COUNT,
    STANCE_CHANGE_THRESHOLD,
    SURVEY_PAIR_COUNT,
    TALKATIVE_TURNS,
    VETERAN_COUNT,
)
from api.models import (
    AIConversation,
    DialogueMatch,
    DialogueSessionRecord,
    GodotEntryTicket,
    MatchMessage,
    PostDialogueResponse,
    Title,
    UserAchievement,
    UserTitle,
)

User = get_user_model()


@pytest.mark.django_db
def test_user_achievement_is_unique_per_user_and_code():
    user = User.objects.create_user(username="u1", password="pw")
    UserAchievement.objects.create(user=user, code="first_login")

    with pytest.raises(IntegrityError):
        UserAchievement.objects.create(user=user, code="first_login")


@pytest.mark.django_db
def test_user_achievement_starts_unnotified():
    user = User.objects.create_user(username="u1", password="pw")
    row = UserAchievement.objects.create(user=user, code="first_login")

    assert row.notified_at is None
    assert row.unlocked_at is not None


def test_catalog_codes_are_unique():
    codes = [d.code for d in CATALOG]
    assert len(codes) == len(set(codes))


def test_catalog_has_seventeen_achievements():
    assert len(CATALOG) == 17


def test_every_catalog_category_has_a_title():
    for definition in CATALOG:
        assert definition.category in CATEGORY_TITLES


def test_meta_achievement_is_last_in_catalog():
    # 「一路同行」排最後是為了成就頁的顯示順序（CATALOG 順序 = 顯示順序）。
    # evaluate() 對它是直接 continue、迴圈後另外判定，所以順序不影響判定正確性。
    assert CATALOG[-1].code == ALL_ACHIEVEMENTS_CODE


def test_catalog_by_code_covers_every_definition():
    assert set(CATALOG_BY_CODE) == {d.code for d in CATALOG}
    assert CATALOG_BY_CODE["first_login"].name == "初來乍到"


def _post_response(user, *, condition, topic_id=102, **overrides):
    """建一筆最小可用的後測紀錄。Likert 全填 4（中立），需要立場位移的測試自行覆蓋。"""
    fields = {f"post_likert_{i}": 4 for i in range(1, 9)}
    fields.update(
        {
            "exp_stance_change_1": 4,
            "exp_stance_change_2": 4,
            "exp_quality_1": 4,
            "exp_quality_2": 4,
            "exp_reflection_1": 4,
            "exp_reflection_2": 4,
            "exp_comprehension_1": 4,
            "ccnd_attention": 4,
            "ccnd_awareness": 4,
            "ccnd_influence": 4,
            "post_open_comprehension": "測試用回答，長度足夠。",
        }
    )
    fields.update(overrides)
    return PostDialogueResponse.objects.create(
        user=user,
        topic_id=topic_id,
        experiment_condition=condition,
        **fields,
    )


@pytest.mark.django_db
def test_evaluate_unlocks_first_login_for_any_user():
    user = User.objects.create_user(username="u1", password="pw")

    newly = evaluate(user)

    assert "first_login" in newly
    assert UserAchievement.objects.filter(user=user, code="first_login").exists()


@pytest.mark.django_db
def test_evaluate_is_idempotent():
    user = User.objects.create_user(username="u1", password="pw")
    evaluate(user)

    newly = evaluate(user)

    assert "first_login" not in newly
    assert UserAchievement.objects.filter(user=user, code="first_login").count() == 1


@pytest.mark.django_db
def test_evaluate_unlocks_first_hh_dialogue_only_after_an_hh_response():
    user = User.objects.create_user(username="u1", password="pw")
    evaluate(user)
    assert not UserAchievement.objects.filter(user=user, code="first_hh_dialogue").exists()

    _post_response(user, condition=PostDialogueResponse.ExperimentCondition.HH)
    newly = evaluate(user)

    assert "first_hh_dialogue" in newly


@pytest.mark.django_db
def test_ai_response_does_not_unlock_the_hh_achievement():
    user = User.objects.create_user(username="u1", password="pw")
    _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)

    evaluate(user)

    assert not UserAchievement.objects.filter(user=user, code="first_hh_dialogue").exists()


@pytest.mark.django_db
def test_stance_changed_needs_delta_over_threshold():
    user = User.objects.create_user(username="u1", password="pw")
    response = _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)
    response.delta_s_value = STANCE_CHANGE_THRESHOLD / 2
    response.save(update_fields=["delta_s_value"])

    evaluate(user)
    assert not UserAchievement.objects.filter(user=user, code="stance_changed_once").exists()

    response.delta_s_value = STANCE_CHANGE_THRESHOLD
    response.save(update_fields=["delta_s_value"])
    evaluate(user)

    assert UserAchievement.objects.filter(user=user, code="stance_changed_once").exists()


@pytest.mark.django_db
def test_negative_drift_also_counts_as_change():
    # Δs 是有號的（正=偏支持、負=偏反對）。往哪邊挪都算「有變化」。
    user = User.objects.create_user(username="u1", password="pw")
    response = _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)
    response.delta_s_value = -STANCE_CHANGE_THRESHOLD
    response.save(update_fields=["delta_s_value"])

    evaluate(user)

    assert UserAchievement.objects.filter(user=user, code="stance_changed_once").exists()


@pytest.mark.django_db
def test_stance_held_needs_a_measured_but_small_delta():
    user = User.objects.create_user(username="u1", password="pw")
    response = _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)
    response.delta_s_value = 0.0
    response.save(update_fields=["delta_s_value"])

    evaluate(user)

    assert UserAchievement.objects.filter(user=user, code="stance_held_once").exists()


@pytest.mark.django_db
def test_null_delta_unlocks_neither_stance_achievement():
    # 沒填前測 → delta_s_value 是 NULL，代表「沒量到」，不是「沒變化」。
    user = User.objects.create_user(username="u1", password="pw")
    _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)

    evaluate(user)

    assert not UserAchievement.objects.filter(user=user, code="stance_changed_once").exists()
    assert not UserAchievement.objects.filter(user=user, code="stance_held_once").exists()


@pytest.mark.django_db
def test_stance_changed_many_needs_multiple_changed_dialogues():
    user = User.objects.create_user(username="u1", password="pw")
    for _ in range(MULTI_CHANGE_COUNT - 1):
        response = _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)
        response.delta_s_value = 1.0
        response.save(update_fields=["delta_s_value"])
    evaluate(user)
    assert not UserAchievement.objects.filter(user=user, code="stance_changed_many").exists()

    response = _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)
    response.delta_s_value = 1.0
    response.save(update_fields=["delta_s_value"])
    evaluate(user)

    assert UserAchievement.objects.filter(user=user, code="stance_changed_many").exists()


@pytest.mark.django_db
def test_survey_pairs_counts_responses_with_a_pre_snapshot():
    user = User.objects.create_user(username="u1", password="pw")
    for _ in range(SURVEY_PAIR_COUNT):
        response = _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)
        response.s_pre = 4.0
        response.save(update_fields=["s_pre"])

    evaluate(user)

    assert UserAchievement.objects.filter(user=user, code="survey_pairs").exists()


@pytest.mark.django_db
def test_talkative_unlocks_from_a_long_ai_session():
    user = User.objects.create_user(username="u1", password="pw")
    for i in range(TALKATIVE_TURNS):
        AIConversation.objects.create(
            user=user, session_id="s1", user_prompt=f"第 {i} 則發言"
        )

    evaluate(user)

    assert UserAchievement.objects.filter(user=user, code="talkative").exists()


@pytest.mark.django_db
def test_talkative_does_not_unlock_from_turns_spread_across_sessions():
    # 「高輪數對話」是單場的性質，不是總量。
    user = User.objects.create_user(username="u1", password="pw")
    for i in range(TALKATIVE_TURNS):
        AIConversation.objects.create(
            user=user, session_id=f"s{i}", user_prompt=f"第 {i} 則發言"
        )

    evaluate(user)

    assert not UserAchievement.objects.filter(user=user, code="talkative").exists()


@pytest.mark.django_db
def test_talkative_unlocks_from_a_long_hh_room():
    user = User.objects.create_user(username="u1", password="pw")
    other = User.objects.create_user(username="u2", password="pw")
    match = DialogueMatch.objects.create(
        topic_id=102, user_a=user, user_b=other, room_id="room-1"
    )
    for i in range(TALKATIVE_TURNS):
        MatchMessage.objects.create(match=match, sender=user, content=f"第 {i} 則")

    evaluate(user)

    assert UserAchievement.objects.filter(user=user, code="talkative").exists()


@pytest.mark.django_db
def test_complete_flows_counts_post_responses():
    user = User.objects.create_user(username="u1", password="pw")
    for _ in range(COMPLETE_FLOW_COUNT):
        _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)

    evaluate(user)

    assert UserAchievement.objects.filter(user=user, code="complete_flows").exists()


@pytest.mark.django_db
def test_same_day_dialogues_needs_multiple_on_one_day():
    user = User.objects.create_user(username="u1", password="pw")
    for _ in range(SAME_DAY_COUNT):
        _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)

    evaluate(user)

    assert UserAchievement.objects.filter(user=user, code="same_day_dialogues").exists()


@pytest.mark.django_db
def test_dialogues_on_different_days_do_not_count_as_same_day():
    # 沒有「按天分組」的爛實作（純 count >= N）會在這裡露餡。
    user = User.objects.create_user(username="u1", password="pw")
    for _ in range(SAME_DAY_COUNT):
        _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)
    now = timezone.now()
    for offset, response in enumerate(PostDialogueResponse.objects.filter(user=user)):
        PostDialogueResponse.objects.filter(pk=response.pk).update(
            created_at=now - timedelta(days=offset)
        )

    evaluate(user)

    assert not UserAchievement.objects.filter(
        user=user, code="same_day_dialogues"
    ).exists()


@pytest.mark.django_db
def test_same_day_uses_taipei_midnight_not_utc_midnight():
    """UTC 同一天、台北跨午夜 → 不算同一天。

    2026-08-18 10:00 UTC = 台北 08/18 18:00
    2026-08-18 17:00 UTC = 台北 08/19 01:00
    少了 tzinfo 的舊寫法會把這兩場錯判成同一天。
    """
    user = User.objects.create_user(username="u1", password="pw")
    for _ in range(2):
        _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)
    stamps = [
        datetime(2026, 8, 18, 10, 0, tzinfo=dt_timezone.utc),
        datetime(2026, 8, 18, 17, 0, tzinfo=dt_timezone.utc),
    ]
    for stamp, response in zip(stamps, PostDialogueResponse.objects.filter(user=user)):
        PostDialogueResponse.objects.filter(pk=response.pk).update(created_at=stamp)

    evaluate(user)

    assert not UserAchievement.objects.filter(
        user=user, code="same_day_dialogues"
    ).exists()


@pytest.mark.django_db
def test_same_day_spans_utc_midnight_when_taipei_day_is_shared():
    """台北同一天、UTC 跨午夜 → 算同一天。

    2026-08-17 20:00 UTC = 台北 08/18 04:00
    2026-08-18 01:00 UTC = 台北 08/18 09:00
    這是上一個測試的反方向：把時區設成 UTC 或其他時區都會讓這裡失敗。
    """
    user = User.objects.create_user(username="u1", password="pw")
    for _ in range(2):
        _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)
    stamps = [
        datetime(2026, 8, 17, 20, 0, tzinfo=dt_timezone.utc),
        datetime(2026, 8, 18, 1, 0, tzinfo=dt_timezone.utc),
    ]
    for stamp, response in zip(stamps, PostDialogueResponse.objects.filter(user=user)):
        PostDialogueResponse.objects.filter(pk=response.pk).update(created_at=stamp)

    evaluate(user)

    assert UserAchievement.objects.filter(
        user=user, code="same_day_dialogues"
    ).exists()


@pytest.mark.django_db
def test_returning_days_counts_distinct_days_not_total_dialogues():
    user = User.objects.create_user(username="u1", password="pw")
    # 同一天做滿 RETURNING_DAYS 場，不該解鎖。
    for _ in range(RETURNING_DAYS):
        _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)
    evaluate(user)
    assert not UserAchievement.objects.filter(user=user, code="returning_days").exists()

    # 改成散在不同天。created_at 是 auto_now_add，只能建立後再覆寫。
    responses = list(PostDialogueResponse.objects.filter(user=user))
    now = timezone.now()
    for offset, response in enumerate(responses):
        PostDialogueResponse.objects.filter(pk=response.pk).update(
            created_at=now - timedelta(days=offset)
        )
    evaluate(user)

    assert UserAchievement.objects.filter(user=user, code="returning_days").exists()


@pytest.mark.django_db
def test_returning_days_uses_taipei_days_not_utc_days():
    """台北日數比 UTC 日數少一天時，不該解鎖。

    第一筆刻意落在台北 08/18 凌晨（UTC 還是 08/17），跟第二筆同屬台北 08/18，
    但在 UTC 下是兩個不同的日子。於是：
        台北不重複日數 = RETURNING_DAYS - 1  → 不該解鎖
        UTC  不重複日數 = RETURNING_DAYS      → 少了 tzinfo 就會錯誤解鎖

    這一條鎖的是「多發成就」那個方向，比 same_day 的漏發更難在正式環境察覺。
    """
    user = User.objects.create_user(username="u1", password="pw")
    for _ in range(RETURNING_DAYS):
        _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)

    # UTC 08/17 17:00 = 台北 08/18 01:00；UTC 08/18 10:00 = 台北 08/18 18:00
    stamps = [
        datetime(2026, 8, 17, 17, 0, tzinfo=dt_timezone.utc),
        datetime(2026, 8, 18, 10, 0, tzinfo=dt_timezone.utc),
    ]
    # 其餘每筆各自一個台北日，都在當地 18:00（不跨日界線）
    for offset in range(1, RETURNING_DAYS - 1):
        stamps.append(
            datetime(2026, 8, 18 + offset, 10, 0, tzinfo=dt_timezone.utc)
        )
    for stamp, response in zip(stamps, PostDialogueResponse.objects.filter(user=user)):
        PostDialogueResponse.objects.filter(pk=response.pk).update(created_at=stamp)

    evaluate(user)

    assert not UserAchievement.objects.filter(
        user=user, code="returning_days"
    ).exists()


@pytest.mark.django_db
def test_veteran_dialogues_counts_total():
    user = User.objects.create_user(username="u1", password="pw")
    for _ in range(VETERAN_COUNT):
        _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)

    evaluate(user)

    assert UserAchievement.objects.filter(user=user, code="veteran_dialogues").exists()


@pytest.mark.django_db
def test_godot_entry_needs_a_redeemed_ticket():
    user = User.objects.create_user(username="u1", password="pw")
    GodotEntryTicket.objects.create(
        token="t1", user=user, expires_at=timezone.now() + timedelta(minutes=1)
    )

    evaluate(user)
    assert not UserAchievement.objects.filter(user=user, code="first_godot_entry").exists()

    GodotEntryTicket.objects.filter(token="t1").update(redeemed_at=timezone.now())
    evaluate(user)

    assert UserAchievement.objects.filter(user=user, code="first_godot_entry").exists()


@pytest.mark.django_db
def test_meta_achievement_needs_every_other_achievement():
    user = User.objects.create_user(username="u1", password="pw")
    others = [d.code for d in CATALOG if d.code != ALL_ACHIEVEMENTS_CODE]
    for code in others[:-1]:
        UserAchievement.objects.create(user=user, code=code)

    evaluate(user)
    assert not UserAchievement.objects.filter(
        user=user, code=ALL_ACHIEVEMENTS_CODE
    ).exists()

    UserAchievement.objects.create(user=user, code=others[-1])
    newly = evaluate(user)

    assert ALL_ACHIEVEMENTS_CODE in newly


# 全部成就都有規則了。這個集合留著，是為了讓下一個「先加目錄、規則晚一期才做」
# 的成就有地方登記，而不必動守門測試本身。
PENDING_RULES: set[str] = set()


def test_every_catalog_entry_has_a_rule_or_is_explicitly_pending():
    for definition in CATALOG:
        if definition.code == ALL_ACHIEVEMENTS_CODE:
            continue          # meta 成就由 evaluate() 直接處理，沒有 predicate
        assert definition.code in RULES or definition.code in PENDING_RULES, (
            f"{definition.code} 既沒有規則也沒有列在 PENDING_RULES"
        )


def test_no_orphan_rules():
    codes = {d.code for d in CATALOG}
    for code in RULES:
        assert code in codes, f"RULES 有 {code}，但 CATALOG 沒有"


@pytest.mark.django_db
def test_achievements_me_requires_authentication():
    client = APIClient()

    response = client.get("/api/achievements/me/")

    assert response.status_code == 401


@pytest.mark.django_db
def test_achievements_me_returns_all_categories_and_items():
    user = User.objects.create_user(username="u1", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/achievements/me/")

    assert response.status_code == 200
    assert [c["id"] for c in response.data["categories"]] == list(CATEGORY_TITLES)
    total = sum(len(c["items"]) for c in response.data["categories"])
    assert total == len(CATALOG)


@pytest.mark.django_db
def test_achievements_me_evaluates_on_read():
    # 打開成就頁本身就會結算——這是規則的最終安全網。
    user = User.objects.create_user(username="u1", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)

    client.get("/api/achievements/me/")

    assert UserAchievement.objects.filter(user=user, code="first_login").exists()


@pytest.mark.django_db
def test_achievements_me_reports_newly_unlocked_with_full_text():
    user = User.objects.create_user(username="u1", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/achievements/me/")

    newly = response.data["newly_unlocked"]
    assert [item["code"] for item in newly] == ["first_login"]
    # 對照目錄而不是寫死字串：文案本來就設計成非工程背景的人可以直接改，
    # 這裡要驗的是「payload 帶的是目錄的文案」，不是文案本身現在寫什麼。
    assert newly[0]["name"] == CATALOG_BY_CODE["first_login"].name
    assert newly[0]["title"] == CATALOG_BY_CODE["first_login"].title_name


@pytest.mark.django_db
def test_locked_items_are_marked_unlocked_false():
    user = User.objects.create_user(username="u1", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/achievements/me/")

    items = {i["code"]: i for c in response.data["categories"] for i in c["items"]}
    assert items["first_login"]["unlocked"] is True
    assert items["veteran_dialogues"]["unlocked"] is False
    assert items["veteran_dialogues"]["unlocked_at"] is None


@pytest.mark.django_db
def test_ack_marks_notifications_as_seen():
    user = User.objects.create_user(username="u1", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)
    client.get("/api/achievements/me/")

    response = client.post(
        "/api/achievements/ack/", {"codes": ["first_login"]}, format="json"
    )

    assert response.status_code == 200
    assert response.data == {"acknowledged": 1}
    again = client.get("/api/achievements/me/")
    assert again.data["newly_unlocked"] == []


@pytest.mark.django_db
def test_ack_is_idempotent():
    user = User.objects.create_user(username="u1", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)
    client.get("/api/achievements/me/")
    client.post("/api/achievements/ack/", {"codes": ["first_login"]}, format="json")

    response = client.post(
        "/api/achievements/ack/", {"codes": ["first_login"]}, format="json"
    )

    assert response.data == {"acknowledged": 0}


@pytest.mark.django_db
def test_ack_rejects_a_non_list_payload():
    user = User.objects.create_user(username="u1", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(
        "/api/achievements/ack/", {"codes": "first_login"}, format="json"
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_ack_requires_authentication():
    client = APIClient()

    response = client.post(
        "/api/achievements/ack/", {"codes": ["first_login"]}, format="json"
    )

    assert response.status_code == 401


@pytest.mark.django_db
@pytest.mark.parametrize("codes", [[None], {"a": 1}, [1], ["ok", 2]])
def test_ack_rejects_malformed_codes(codes):
    user = User.objects.create_user(username="u1", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post("/api/achievements/ack/", {"codes": codes}, format="json")

    assert response.status_code == 400


@pytest.mark.django_db
def test_ack_rejects_an_over_long_codes_list():
    user = User.objects.create_user(username="u1", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(
        "/api/achievements/ack/",
        {"codes": ["first_login"] * (len(CATALOG) + 1)},
        format="json",
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_ack_cannot_touch_another_users_rows():
    owner = User.objects.create_user(username="u1", password="pw")
    intruder = User.objects.create_user(username="u2", password="pw")
    UserAchievement.objects.create(user=owner, code="veteran_dialogues")
    client = APIClient()
    client.force_authenticate(user=intruder)

    response = client.post(
        "/api/achievements/ack/", {"codes": ["veteran_dialogues"]}, format="json"
    )

    assert response.data == {"acknowledged": 0}
    assert UserAchievement.objects.get(
        user=owner, code="veteran_dialogues"
    ).notified_at is None


# ═══════════════════════════════════════════════════════════
# 觸發點接線的端對端驗證
#
# 合法的後測 payload 借用 tests_post_questionnaire.py 的那一套（C1~E 五段全
# 齊），不重新發明——那個檔案是這份 payload 契約的權威來源。
# ═══════════════════════════════════════════════════════════

_HH_SESSION_ID = "achsessionid1234"
_HH_ROOM_ID = "achroomid45678"


def _hh_questionnaire_payload():
    return {
        "topic_id": 102,
        "room_id": _HH_ROOM_ID,
        "experiment_condition": "hh",
        "opponent_judgment": None,
        "post_likert_1": 6,
        "post_likert_2": 3,
        "post_likert_3": 4,
        "post_likert_4": 5,
        "post_likert_5": 6,
        "post_likert_6": 2,
        "post_likert_7": 3,
        "post_likert_8": 4,
        "exp_stance_change_1": 4,
        "exp_stance_change_2": 5,
        "exp_quality_1": 6,
        "exp_quality_2": 5,
        "exp_reflection_1": 3,
        "exp_reflection_2": 4,
        "exp_comprehension_1": 5,
        "ccnd_attention": 5,
        "ccnd_awareness": 4,
        "ccnd_influence": 3,
        "post_open_comprehension": (
            "這是一段超過五十個字的測試文字，用來驗證 D1 的最低字數限制是否正確運作。" * 2
        ),
        "post_open_feedback": "對話體驗良好。",
        "discomfort_flag": False,
    }


def _questionnaire_client():
    """建出一個能成功送出 H-H 後測的登入 client（連同它依賴的對話紀錄）。"""
    user = User.objects.create_user(username="u1", password="pw")
    partner = User.objects.create_user(username="u2", password="pw")
    DialogueSessionRecord.objects.create(
        user=user,
        session_id=_HH_SESSION_ID,
        topic_id=102,
        topic_title="測試議題",
        collection_name="nuclear_energy_all",
        session_state={"user_stance_score": 6.0, "history": []},
        last_activity_at=timezone.now(),
    )
    DialogueMatch.objects.create(
        topic_id=102,
        user_a=user,
        user_b=partner,
        user_a_score=6.0,
        user_b_score=2.0,
        room_id=_HH_ROOM_ID,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


@pytest.mark.django_db
def test_posting_the_questionnaire_endpoint_unlocks_achievements():
    """觸發點接線的端對端驗證：把 views.py 的 _safe_evaluate_achievements 呼叫
    刪掉，這個測試就會失敗。用 ORM 直接建紀錄再呼叫 evaluate() 的版本做不到
    這件事，那只是重測了規則本身。"""
    client, user = _questionnaire_client()

    response = client.post(
        "/api/post-questionnaire/", _hh_questionnaire_payload(), format="json"
    )

    assert response.status_code == 201, response.data
    assert UserAchievement.objects.filter(user=user, code="first_hh_dialogue").exists()


@override_settings(GODOT_SERVICE_TOKEN="svc-token")
@pytest.mark.django_db
def test_redeeming_a_godot_ticket_unlocks_achievements():
    """同上，驗的是 GodotTicketRedeemView 那一端的接線。呼叫者是 Godot server，
    帶服務金鑰而非使用者 JWT。"""
    user = User.objects.create_user(username="u1", password="pw")
    ticket = issue_ticket(user=user)
    client = APIClient()
    client.credentials(HTTP_X_GODOT_SERVICE_TOKEN="svc-token")

    response = client.post(
        "/api/godot/tickets/redeem/", {"ticket": ticket.token}, format="json"
    )

    assert response.status_code == 200
    assert UserAchievement.objects.filter(user=user, code="first_godot_entry").exists()


@pytest.mark.django_db
def test_safe_evaluate_returns_the_newly_unlocked_codes():
    from api import views

    user = User.objects.create_user(username="u1", password="pw")

    assert views._safe_evaluate_achievements(user) == ["first_login"]


@pytest.mark.django_db
def test_evaluate_failure_does_not_break_the_caller(monkeypatch):
    """評估壞掉不該讓後測送出跟著失敗——問卷答案比成就重要得多。

    monkeypatch 打在模組層全域上：_safe_evaluate_achievements 是在呼叫時才解析
    evaluate_achievements 這個名字，所以替換得掉。
    """
    from api import views

    def _boom(_user):
        raise RuntimeError("規則寫壞了")

    monkeypatch.setattr(views, "evaluate_achievements", _boom)
    client, user = _questionnaire_client()

    response = client.post(
        "/api/post-questionnaire/", _hh_questionnaire_payload(), format="json"
    )

    assert response.status_code == 201, response.data
    assert PostDialogueResponse.objects.filter(user=user).exists()


@pytest.mark.django_db
def test_unlocking_grants_the_mapped_title():
    call_command("seed_achievement_titles")
    user = User.objects.create_user(username="u1", password="pw")

    evaluate(user)

    assert UserTitle.objects.filter(user=user, title__name="築橋新手").exists()


@pytest.mark.django_db
def test_granted_titles_are_not_auto_selected():
    # UserTitle 有「一位使用者最多一個 is_selected」的 partial unique index。
    # 自動選取會在第二個頭銜到手時炸掉，而且也該由玩家自己決定要掛哪一個。
    call_command("seed_achievement_titles")
    user = User.objects.create_user(username="u1", password="pw")
    _post_response(user, condition=PostDialogueResponse.ExperimentCondition.HH)
    _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)

    evaluate(user)

    owned = set(
        UserTitle.objects.filter(user=user).values_list("title__name", flat=True)
    )
    assert {"築橋新手", "上橋新人", "智橋行者"} <= owned
    assert not UserTitle.objects.filter(user=user, is_selected=True).exists()


@pytest.mark.django_db
def test_unlocking_without_seeded_titles_still_works():
    # 沒跑過 seed 指令的環境（例如剛建好的測試庫）不該讓解鎖整個失敗。
    user = User.objects.create_user(username="u1", password="pw")

    newly = evaluate(user)

    assert "first_login" in newly
    assert not UserTitle.objects.filter(user=user).exists()


@pytest.mark.django_db
def test_seed_command_is_idempotent():
    call_command("seed_achievement_titles")
    call_command("seed_achievement_titles")

    assert Title.objects.filter(name="築橋新手").count() == 1


@pytest.mark.django_db
def test_browsing_the_knowledge_base_unlocks_the_achievement():
    user = User.objects.create_user(username="u1", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)

    client.get("/api/summary/viewpoints/browse/?topic_id=102")

    row = UserAchievement.objects.get(user=user, code="first_knowledge_base")
    # notified_at 必須是 NULL，否則這個成就永遠不會跳解鎖 toast——埋點走的是
    # 跟 evaluate() 不同的寫入路徑，這條斷言就是用來鎖住兩邊行為一致。
    assert row.notified_at is None


@pytest.mark.django_db
def test_a_rejected_browse_request_does_not_unlock():
    # 少帶 topic_id 會 400；沒真的看到知識庫就不該給成就。
    user = User.objects.create_user(username="u1", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/summary/viewpoints/browse/")

    assert response.status_code == 400
    assert not UserAchievement.objects.filter(
        user=user, code="first_knowledge_base"
    ).exists()


@pytest.mark.django_db
def test_browsing_again_does_not_resurface_the_notification():
    # 知識庫翻頁會一直打同一支端點；重複瀏覽不該重建列，也不該讓已經 ack 過的
    # 通知又冒出來（那會讓玩家每翻一頁就看一次同樣的 toast）。
    user = User.objects.create_user(username="u1", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)

    client.get("/api/summary/viewpoints/browse/?topic_id=102")
    client.get("/api/summary/viewpoints/browse/?topic_id=102")

    assert (
        UserAchievement.objects.filter(
            user=user, code="first_knowledge_base"
        ).count()
        == 1
    )

    client.post(
        "/api/achievements/ack/", {"codes": ["first_knowledge_base"]}, format="json"
    )
    client.get("/api/summary/viewpoints/browse/?topic_id=102")

    response = client.get("/api/achievements/me/")
    assert response.status_code == 200
    assert "first_knowledge_base" not in response.data["newly_unlocked"]


@pytest.mark.django_db
def test_seed_command_backfills_titles_for_already_unlocked_achievements():
    # evaluate() 會跳過已解鎖的成就，所以 _grant_titles 不會對它再跑一次——
    # 在 Title 建好之前解鎖的人，頭銜只能靠 seed 指令回填。
    user = User.objects.create_user(username="u1", password="pw")
    evaluate(user)
    assert UserAchievement.objects.filter(user=user, code="first_login").exists()
    assert not UserTitle.objects.filter(user=user).exists()

    call_command("seed_achievement_titles")

    assert UserTitle.objects.filter(user=user, title__name="築橋新手").exists()


@pytest.mark.django_db
def test_backfill_does_not_auto_select_or_duplicate():
    user = User.objects.create_user(username="u1", password="pw")
    evaluate(user)

    call_command("seed_achievement_titles")
    call_command("seed_achievement_titles")

    assert UserTitle.objects.filter(user=user, title__name="築橋新手").count() == 1
    assert not UserTitle.objects.filter(user=user, is_selected=True).exists()


# ═══════════════════════════════════════════════════════════
# P3：對話品質成就
#
# record_ai_attempt 本身的計數行為（含 verdict → profanity 的接線）測在
# api/tests_input_gate_ws.py，跟其他 gate-store 測試放同一家。
# ═══════════════════════════════════════════════════════════

def _dialogue_session(user, session_id, *, profanity_only_total=0, closed=True):
    return DialogueSessionRecord.objects.create(
        user=user,
        session_id=session_id,
        topic_id=102,
        topic_title="測試議題",
        collection_name="test",
        last_activity_at=timezone.now(),
        status=(
            DialogueSessionRecord.Status.CLOSED
            if closed
            else DialogueSessionRecord.Status.ACTIVE
        ),
        profanity_only_total=profanity_only_total,
    )


def _finished_session(user, session_id, *, profanity_only_total=0):
    """一場真的走完的對話：session 紀錄 + 對應的後測。"""
    record = _dialogue_session(
        user, session_id, profanity_only_total=profanity_only_total
    )
    _post_response(
        user,
        condition=PostDialogueResponse.ExperimentCondition.AI,
        session_id=session_id,
    )
    return record


@pytest.mark.django_db
def test_clean_dialogue_once_needs_a_finished_session_with_no_profanity():
    user = User.objects.create_user(username="u1", password="pw")
    _finished_session(user, "s1", profanity_only_total=1)
    evaluate(user)
    assert not UserAchievement.objects.filter(user=user, code="clean_dialogue_once").exists()

    _finished_session(user, "s2", profanity_only_total=0)
    evaluate(user)

    assert UserAchievement.objects.filter(user=user, code="clean_dialogue_once").exists()


@pytest.mark.django_db
def test_an_abandoned_session_does_not_count_as_a_clean_dialogue():
    """「開始新對話」會把舊的 ACTIVE session 批次標成 CLOSED（見
    views._close_superseded_dialogue_sessions）。那筆零訊息的殘骸也是零攻擊性，
    只看 status=CLOSED 的話，同一議題重開幾次就能白拿這兩個成就。
    """
    user = User.objects.create_user(username="u1", password="pw")
    # CLOSED、零攻擊性，但沒有後測——沒走完的對話。
    for i in range(CLEAN_DIALOGUE_COUNT + 1):
        _dialogue_session(user, f"abandoned{i}")

    evaluate(user)

    assert not UserAchievement.objects.filter(user=user, code="clean_dialogue_once").exists()
    assert not UserAchievement.objects.filter(user=user, code="clean_dialogue_many").exists()


@pytest.mark.django_db
def test_an_active_session_does_not_count_as_a_completed_clean_dialogue():
    user = User.objects.create_user(username="u1", password="pw")
    _dialogue_session(user, "s1", closed=False)

    evaluate(user)

    assert not UserAchievement.objects.filter(user=user, code="clean_dialogue_once").exists()


@pytest.mark.django_db
def test_clean_dialogue_many_needs_the_full_count():
    user = User.objects.create_user(username="u1", password="pw")
    for i in range(CLEAN_DIALOGUE_COUNT - 1):
        _finished_session(user, f"s{i}")

    evaluate(user)
    assert not UserAchievement.objects.filter(user=user, code="clean_dialogue_many").exists()

    _finished_session(user, "s-last")
    evaluate(user)

    assert UserAchievement.objects.filter(user=user, code="clean_dialogue_many").exists()
