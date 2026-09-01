"""
pytest tests for Godot 綁定房的問卷裁決與收尾（階段五）。

Run from backend/:
    pytest api/tests_godot_adjudication.py -v
"""
import threading
import uuid
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient

from api.models import DialogueMatch, MatchQueueEntry, UserStanceProfile

User = get_user_model()

SUPPORT_ANSWERS = {str(i): (1 if i in {2, 4, 5, 6} else 7) for i in range(1, 9)}
OPPOSE_ANSWERS = {str(i): (7 if i in {2, 4, 5, 6} else 1) for i in range(1, 9)}
NEUTRAL_ANSWERS = {str(i): 4 for i in range(1, 9)}


def _make_users():
    return (
        User.objects.create_user(username="ua", password="pw"),
        User.objects.create_user(username="ub", password="pw"),
    )


def _godot_match(user_a, user_b, *, topic_id=102, room_id="room-adj",
                 deadline_offset_seconds=3600):
    """建一間跟 GodotMatchRoomView 產出形狀相同的 Godot 綁定房。

    預設期限刻意給得比正式的 300 秒寬鬆很多：測試裡的 _submit_survey 會實際跑
    embedding 模型，機器負載高時單一測試可能耗掉數分鐘。用 300 秒的話，期限會在
    測試執行途中真的到期，裁決正確地把房作廢，測試卻是因為時鐘而不是因為邏輯失敗
    ——曾經因此看過四個原本綠燈的測試同時變紅。要驗逾時行為的測試一律自己傳負值。
    """
    return DialogueMatch.objects.create(
        topic_id=topic_id,
        user_a=user_a,
        user_b=user_b,
        matching_algorithm_version="godot_manual",
        room_id=room_id,
        status=DialogueMatch.Status.ACTIVE,
        stats={
            "binding": {
                "source": "godot",
                "survey_deadline": (
                    timezone.now() + timedelta(seconds=deadline_offset_seconds)
                ).isoformat(),
            }
        },
    )


GODOT_PRESENCE_TIMEOUT = 45


@pytest.fixture(autouse=True)
def _pin_presence_timeout(monkeypatch):
    """實作在呼叫當下讀環境變數；測試裡的 GODOT_PRESENCE_TIMEOUT 是寫死的。
    不釘住的話，環境裡若設了這個變數，相關測試會靜默地改變語意。"""
    monkeypatch.setenv("GODOT_PRESENCE_TIMEOUT_SECONDS", str(GODOT_PRESENCE_TIMEOUT))


def _submit_survey(user, answers, *, topic_id=102, open_text="我的看法是……"):
    client = APIClient()
    client.force_authenticate(user=user)
    return client.post(
        "/api/matching/godot-survey/",
        {
            "topic_id": topic_id,
            "survey_answers": answers,
            "survey_open_answers": {"Q9": open_text},
        },
        format="json",
    )


@pytest.mark.django_db
def test_metrics_stay_zero_until_both_sides_submit():
    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-metrics-1")

    _submit_survey(user_a, SUPPORT_ANSWERS)

    match.refresh_from_db()
    assert match.match_score == 0    # 只有一邊，還算不出來


@pytest.mark.django_db
def test_metrics_computed_once_both_sides_submit():
    """補算是事後描述指標，不是配對依據——但不能永遠留 0，那跟真值無法區分。"""
    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-metrics-2")

    _submit_survey(user_a, SUPPORT_ANSWERS, open_text="我強烈支持，因為安全無虞。")
    _submit_survey(user_b, OPPOSE_ANSWERS, open_text="我強烈反對，因為風險太高。")

    match.refresh_from_db()
    assert match.likert_distance > 0
    assert match.match_score > 0
    # semantic_distance 取決於 embedding 模型，不斷言確切值，只確認有被寫入
    assert match.semantic_distance is not None


def _mark_seen(match, user, *, seconds_ago=0):
    """直接寫 presence，模擬「這個人最後一次輪詢是幾秒前」。"""
    from apps.matching.services.matcher import (
        _presence_state,
        _save_presence_state,
    )

    presence = _presence_state(match)
    presence["participants"][str(user.id)] = {
        "connected": True,
        "last_seen": (timezone.now() - timedelta(seconds=seconds_ago)).isoformat(),
        "disconnected_at": None,
    }
    _save_presence_state(match, presence)
    match.refresh_from_db()
    return match


@pytest.mark.django_db
def test_gate_keeps_room_while_both_recently_seen():
    from apps.matching.services.matcher import resolve_godot_survey_gate

    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-gate-ok")
    _mark_seen(match, user_a, seconds_ago=2)
    _mark_seen(match, user_b, seconds_ago=3)

    resolved = resolve_godot_survey_gate(match=match)

    assert resolved.status == DialogueMatch.Status.ACTIVE


@pytest.mark.django_db
def test_never_seen_participant_is_not_treated_as_left():
    """房間剛建立時兩人的 last_seen 都是 None——那是「還沒出現」不是「離開」。

    誤判這個會讓剛跳轉進來、還沒開始輪詢的人被當場判出局，房間當場作廢。
    """
    from apps.matching.services.matcher import resolve_godot_survey_gate

    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-gate-fresh")

    resolved = resolve_godot_survey_gate(match=match)

    assert resolved.status == DialogueMatch.Status.ACTIVE


@pytest.mark.django_db
def test_partner_gone_cancels_room():
    from apps.matching.services.matcher import resolve_godot_survey_gate

    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-gate-left")
    _mark_seen(match, user_a, seconds_ago=1)
    _mark_seen(match, user_b, seconds_ago=GODOT_PRESENCE_TIMEOUT + 10)

    resolved = resolve_godot_survey_gate(match=match)

    assert resolved.status == DialogueMatch.Status.CANCELLED
    assert resolved.stats["binding"]["cancel_reason"] == "godot_partner_left"


@pytest.mark.django_db
def test_deadline_expiry_cancels_room():
    from apps.matching.services.matcher import resolve_godot_survey_gate

    user_a, user_b = _make_users()
    match = _godot_match(
        user_a, user_b, room_id="room-gate-timeout", deadline_offset_seconds=-10
    )
    _mark_seen(match, user_a, seconds_ago=1)
    _mark_seen(match, user_b, seconds_ago=1)

    resolved = resolve_godot_survey_gate(match=match)

    assert resolved.status == DialogueMatch.Status.CANCELLED
    assert resolved.stats["binding"]["cancel_reason"] == "godot_survey_timeout"


@pytest.mark.django_db
def test_both_sides_gone_and_expired_reports_timeout_not_partner_left():
    """兩位都不在、而且已經逾期 → 原因是逾時，不是「對方已退出」。

    「對方已退出」的前提是還有人留在現場等他。兩個人都關掉網頁的時候沒有「對方」
    這個角色，那句話對誰都不成立。這不是文字潔癖：這正是 close_expired_godot_matches
    抓到的房的形狀（有人在輪詢的話那個請求早就把房裁決掉了），裁決若無條件先判離開，
    godot_survey_timeout 這個原因就永遠不會從清理指令那條路發出，兩位使用者一律被
    告知「對方已退出配對」，stats 裡記下的原因也跟著錯，研究資料分不出這兩種情況。

    邊界的另一半由 test_partner_gone_cancels_room 釘著（一個在、一個不在，仍然是
    partner_left）——修好這邊不能把那邊一起改掉。
    """
    from apps.matching.services.matcher import resolve_godot_survey_gate

    user_a, user_b = _make_users()
    match = _godot_match(
        user_a, user_b, room_id="room-gate-both-gone", deadline_offset_seconds=-10
    )
    _mark_seen(match, user_a, seconds_ago=GODOT_PRESENCE_TIMEOUT + 10)
    match = _mark_seen(match, user_b, seconds_ago=GODOT_PRESENCE_TIMEOUT + 10)

    resolved = resolve_godot_survey_gate(match=match)

    assert resolved.status == DialogueMatch.Status.CANCELLED
    assert resolved.stats["binding"]["cancel_reason"] == "godot_survey_timeout"


@pytest.mark.django_db
def test_command_reports_timeout_on_a_room_that_is_genuinely_old():
    """跟 test_command_cancels_expired_unattended_room 同一件事，但房是「真的舊」。

    那支測試的房是當場建的，created_at 就是現在，所以
    _godot_participant_is_gone 的 created_at 寬限期還沒過（last_seen 是 None 時
    改用 created_at 起算），兩位都算「在」，逾時判斷自然跑得到。正式環境不是這樣：
    期限 300 秒，指令抓到房的時候 created_at 至少是 300 秒前，兩位一定都超過 45 秒
    的存在門檻——也就是說原本那支測試綠燈，線上仍然全部報成 partner_left。
    """
    from django.core.management import call_command

    user_a, user_b = _make_users()
    match = _godot_match(
        user_a, user_b, room_id="room-cmd-old", deadline_offset_seconds=-60
    )
    # created_at 是 auto_now_add，只能建完再用 queryset update 覆寫。
    DialogueMatch.objects.filter(pk=match.pk).update(
        created_at=timezone.now() - timedelta(seconds=400)
    )

    call_command("close_expired_godot_matches")

    match.refresh_from_db()
    assert match.status == DialogueMatch.Status.CANCELLED
    assert match.stats["binding"]["cancel_reason"] == "godot_survey_timeout"


@pytest.mark.django_db(transaction=True)
def test_concurrent_cancel_notice_marks_do_not_clobber_each_other():
    """兩位參與者同時輪詢到同一間作廢的房，兩個人都要留下「已通知」的記錄。

    名單住在 stats 這個 JSON 欄位裡，讀改寫必須在 select_for_update 之內。不然兩邊
    各自從自己的快照讀到還沒有對方的名單、各自只加自己、再整包寫回——後寫的贏，
    前一位的標記就這樣消失，他下一次輪詢會再收到一次同樣的作廢通知。前端輪詢是
    3 秒一次，兩個人同時撞上完全是日常。

    需要 transaction=True 才有真實的 commit 邊界與真實的列鎖。barrier 只保證同時
    起跑、不保證同時進臨界區，所以跑多輪把漏抓的機率壓低（做法沿用
    tests_godot_tickets.py::test_concurrent_redeem_only_one_succeeds）。
    """
    from api.godot_binding import mark_cancel_notice_seen, pending_cancel_notice_for
    from apps.matching.services.matcher import resolve_godot_survey_gate

    for i in range(10):
        user_a = User.objects.create_user(
            username=f"ca{uuid.uuid4().hex}", password="pw"
        )
        user_b = User.objects.create_user(
            username=f"cb{uuid.uuid4().hex}", password="pw"
        )
        match = _godot_match(
            user_a, user_b, room_id=f"room-notice-{i}", deadline_offset_seconds=-10
        )
        _mark_seen(match, user_a, seconds_ago=1)
        match = _mark_seen(match, user_b, seconds_ago=1)
        cancelled = resolve_godot_survey_gate(match=match)
        assert cancelled.status == DialogueMatch.Status.CANCELLED

        errors = []
        barrier = threading.Barrier(2)

        def worker(user_id, pk=match.pk):
            try:
                barrier.wait(timeout=5)
                # 各自讀自己那份快照，模擬兩個平行的 HTTP 請求。
                mark_cancel_notice_seen(DialogueMatch.objects.get(pk=pk), user_id)
            except Exception as exc:  # noqa: BLE001 - 蒐集起來在主執行緒重新拋出
                errors.append(exc)
            finally:
                connection.close()

        threads = [
            threading.Thread(target=worker, args=(user_a.id,)),
            threading.Thread(target=worker, args=(user_b.id,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        if errors:
            raise errors[0]

        fresh = DialogueMatch.objects.get(pk=match.pk)
        assert pending_cancel_notice_for(fresh, user_a.id) is None
        assert pending_cancel_notice_for(fresh, user_b.id) is None


@pytest.mark.django_db
def test_deadline_expiry_does_not_cancel_when_both_completed():
    """逾時只針對「還沒填完」的房。都填完了就進聊天室，期限不再有意義。

    前置要照真實順序走：先在期限內讓兩人都送出，**之後**才把期限改成過去。
    若一開始就給過期的期限，第一個人送出時裁決就會正確地作廢房間（那時確實
    「期限已過且還有人沒填完」），第二個人根本送不進來——那驗的是另一回事。
    """
    from apps.matching.services.matcher import resolve_godot_survey_gate

    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-gate-done")
    _submit_survey(user_a, SUPPORT_ANSWERS)
    _submit_survey(user_b, OPPOSE_ANSWERS)
    match.refresh_from_db()
    assert match.status == DialogueMatch.Status.ACTIVE   # 前置成立才有意義

    # 兩人都填完之後才讓期限過期。
    stats = dict(match.stats)
    binding = dict(stats["binding"])
    binding["survey_deadline"] = (timezone.now() - timedelta(seconds=10)).isoformat()
    stats["binding"] = binding
    match.stats = stats
    match.save(update_fields=["stats"])

    resolved = resolve_godot_survey_gate(match=match)

    assert resolved.status == DialogueMatch.Status.ACTIVE


@pytest.mark.django_db
def test_survivor_with_extreme_stance_returns_to_queue():
    """已填問卷、立場極端的人退回一般配對佇列（§D3 壓抑的分流在此恢復）。"""
    from apps.matching.services.matcher import resolve_godot_survey_gate

    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-gate-survivor")
    _submit_survey(user_a, SUPPORT_ANSWERS)
    match.refresh_from_db()
    _mark_seen(match, user_a, seconds_ago=1)
    _mark_seen(match, user_b, seconds_ago=GODOT_PRESENCE_TIMEOUT + 10)

    resolve_godot_survey_gate(match=match)

    assert MatchQueueEntry.objects.filter(
        user=user_a, topic_id=102, status=MatchQueueEntry.Status.MATCHING
    ).exists()


@pytest.mark.django_db
def test_survivor_with_neutral_stance_routed_to_ai():
    from api.models import DialogueEntryAssignment
    from apps.matching.services.matcher import resolve_godot_survey_gate

    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-gate-neutral")
    _submit_survey(user_a, NEUTRAL_ANSWERS)
    match.refresh_from_db()
    _mark_seen(match, user_a, seconds_ago=1)
    _mark_seen(match, user_b, seconds_ago=GODOT_PRESENCE_TIMEOUT + 10)

    resolve_godot_survey_gate(match=match)

    assignment = DialogueEntryAssignment.objects.get(user=user_a, topic_id=102)
    assert assignment.route == DialogueEntryAssignment.Route.AI


@pytest.mark.django_db
def test_gate_ignores_non_godot_match():
    from apps.matching.services.matcher import resolve_godot_survey_gate

    user_a, user_b = _make_users()
    match = DialogueMatch.objects.create(
        topic_id=102, user_a=user_a, user_b=user_b,
        user_a_score=5, user_b_score=3,
        room_id="room-normal-gate", status=DialogueMatch.Status.ACTIVE,
    )

    resolved = resolve_godot_survey_gate(match=match)

    assert resolved.status == DialogueMatch.Status.ACTIVE


@pytest.mark.django_db
def test_viewer_is_not_judged_by_own_stale_last_seen():
    """輪詢者自己的 last_seen 在裁決當下還沒更新（那是裁決之後才做的）。
    不排除他的話，載入慢、第一次輪詢就超過門檻的人會在抵達瞬間把房間判掉。"""
    from apps.matching.services.matcher import resolve_godot_survey_gate

    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-gate-self")
    # A 從沒出現過（last_seen 為 None），B 剛剛才輪詢過。
    _mark_seen(match, user_b, seconds_ago=1)
    # 把建房時間推到門檻之外，模擬 A 載入很久才第一次輪詢。
    DialogueMatch.objects.filter(pk=match.pk).update(
        created_at=timezone.now() - timedelta(seconds=GODOT_PRESENCE_TIMEOUT + 10)
    )
    match.refresh_from_db()

    resolved = resolve_godot_survey_gate(match=match, viewer_user_id=user_a.id)

    assert resolved.status == DialogueMatch.Status.ACTIVE


@pytest.mark.django_db
def test_status_reports_partner_left_after_cancellation():
    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-status-left")
    _submit_survey(user_a, SUPPORT_ANSWERS)
    match.refresh_from_db()
    _mark_seen(match, user_a, seconds_ago=1)
    _mark_seen(match, user_b, seconds_ago=GODOT_PRESENCE_TIMEOUT + 10)

    client = APIClient()
    client.force_authenticate(user=user_a)
    response = client.get("/api/matching/status/?topic_id=102")

    assert response.status_code == 200
    assert response.data["binding_cancel_reason"] == "godot_partner_left"


@pytest.mark.django_db
def test_survey_submission_rejected_after_room_cancelled():
    """房已作廢就不該再收問卷——寫進去沒有意義，還會讓使用者以為送出成功。"""
    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-submit-after-cancel")
    _mark_seen(match, user_a, seconds_ago=1)
    _mark_seen(match, user_b, seconds_ago=GODOT_PRESENCE_TIMEOUT + 10)

    response = _submit_survey(user_a, SUPPORT_ANSWERS)

    assert response.status_code == 409
    assert response.data["binding_cancel_reason"] == "godot_partner_left"
    assert not UserStanceProfile.objects.filter(user=user_a, topic_id=102).exists()


@pytest.mark.django_db
def test_cancel_reason_is_reported_once_then_clears():
    """作廢原因是一次性訊號：裁決發生的那次輪詢帶出來，之後就沒有了。
    前端必須在收到當下反應（見 TopicChat 的處理）。"""
    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-reason-once")
    _submit_survey(user_a, SUPPORT_ANSWERS)
    match.refresh_from_db()
    _mark_seen(match, user_a, seconds_ago=1)
    _mark_seen(match, user_b, seconds_ago=GODOT_PRESENCE_TIMEOUT + 10)

    client = APIClient()
    client.force_authenticate(user=user_a)
    first = client.get("/api/matching/status/?topic_id=102")
    second = client.get("/api/matching/status/?topic_id=102")

    assert first.data["binding_cancel_reason"] == "godot_partner_left"
    assert second.data["binding_cancel_reason"] is None


@pytest.mark.django_db
def test_room_messages_blocked_until_both_pretests_done():
    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-gate-msg")
    _submit_survey(user_a, SUPPORT_ANSWERS)

    client = APIClient()
    client.force_authenticate(user=user_a)
    response = client.get(f"/api/matching/rooms/{match.room_id}/messages/")

    assert response.status_code == 409


@pytest.mark.django_db
def test_room_messages_allowed_once_both_pretests_done():
    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-gate-msg-ok")
    _submit_survey(user_a, SUPPORT_ANSWERS)
    _submit_survey(user_b, OPPOSE_ANSWERS)

    client = APIClient()
    client.force_authenticate(user=user_a)
    response = client.get(f"/api/matching/rooms/{match.room_id}/messages/")

    assert response.status_code == 200


@pytest.mark.django_db
def test_room_message_post_blocked_until_both_pretests_done():
    """POST 也要擋——只擋 GET 的話，繞過 UI 直接送訊息照樣寫得進去。"""
    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-gate-msg-post")
    _submit_survey(user_a, SUPPORT_ANSWERS)

    client = APIClient()
    client.force_authenticate(user=user_a)
    response = client.post(
        f"/api/matching/rooms/{match.room_id}/messages/",
        {"content": "前測還沒做完就想聊天"},
        format="json",
    )

    assert response.status_code == 409


@pytest.mark.django_db
def test_normal_match_room_is_not_gated():
    """一般配對房建房時就有分數，沒有「前測未完成」這種狀態，不該被擋。"""
    user_a, user_b = _make_users()
    match = DialogueMatch.objects.create(
        topic_id=102, user_a=user_a, user_b=user_b,
        user_a_score=5, user_b_score=3,
        room_id="room-normal-msg", status=DialogueMatch.Status.ACTIVE,
    )

    client = APIClient()
    client.force_authenticate(user=user_a)
    response = client.get(f"/api/matching/rooms/{match.room_id}/messages/")

    assert response.status_code == 200


@pytest.mark.django_db
def test_command_cancels_expired_unattended_room():
    from django.core.management import call_command

    user_a, user_b = _make_users()
    match = _godot_match(
        user_a, user_b, room_id="room-cmd-1", deadline_offset_seconds=-60
    )

    call_command("close_expired_godot_matches")

    match.refresh_from_db()
    assert match.status == DialogueMatch.Status.CANCELLED
    assert match.stats["binding"]["cancel_reason"] == "godot_survey_timeout"


@pytest.mark.django_db
def test_command_dry_run_writes_nothing():
    from django.core.management import call_command

    user_a, user_b = _make_users()
    match = _godot_match(
        user_a, user_b, room_id="room-cmd-2", deadline_offset_seconds=-60
    )

    call_command("close_expired_godot_matches", "--dry-run")

    match.refresh_from_db()
    assert match.status == DialogueMatch.Status.ACTIVE


@pytest.mark.django_db
def test_command_leaves_rooms_within_deadline():
    from django.core.management import call_command

    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-cmd-3")

    call_command("close_expired_godot_matches")

    match.refresh_from_db()
    assert match.status == DialogueMatch.Status.ACTIVE


@pytest.mark.django_db
def test_command_leaves_completed_rooms_alone():
    """雙方都填完的房已經在對話了，逾期與否都不該被指令收掉。"""
    from django.core.management import call_command

    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-cmd-4")
    _submit_survey(user_a, SUPPORT_ANSWERS)
    _submit_survey(user_b, OPPOSE_ANSWERS)
    # 兩人都填完之後才讓期限過期（順序不能反：一開始就過期的話，第一個人
    # 送出時裁決就會正確地把房收掉，第二個人根本送不進來）。
    match.refresh_from_db()
    stats = dict(match.stats)
    binding = dict(stats["binding"])
    binding["survey_deadline"] = (timezone.now() - timedelta(seconds=60)).isoformat()
    stats["binding"] = binding
    match.stats = stats
    match.save(update_fields=["stats"])

    call_command("close_expired_godot_matches")

    match.refresh_from_db()
    assert match.status == DialogueMatch.Status.ACTIVE


@pytest.mark.django_db
def test_command_leaves_normal_matches_alone():
    """一般配對房沒有 binding，不在這支指令的管轄範圍。"""
    from django.core.management import call_command

    user_a, user_b = _make_users()
    match = DialogueMatch.objects.create(
        topic_id=102, user_a=user_a, user_b=user_b,
        user_a_score=5, user_b_score=3,
        room_id="room-cmd-normal", status=DialogueMatch.Status.ACTIVE,
    )

    call_command("close_expired_godot_matches")

    match.refresh_from_db()
    assert match.status == DialogueMatch.Status.ACTIVE


@pytest.mark.django_db
def test_survey_write_is_rejected_if_room_cancelled_after_gate_check():
    """檢查與寫入之間的窗口：模擬「裁決檢查通過後，房間才被取消」。

    record_godot_survey 必須在鎖內重驗，否則會寫出「問卷成功但房已作廢、人也沒被
    重新分流」的孤兒狀態。
    """
    from apps.matching.services.matcher import record_godot_survey

    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-race-write")
    # 模擬窗口期間房間被別人取消（清理指令或另一位的輪詢）。
    DialogueMatch.objects.filter(pk=match.pk).update(
        status=DialogueMatch.Status.CANCELLED
    )

    result = record_godot_survey(
        user=user_a,
        match=match,           # 呼叫端手上仍是那份過期的 ACTIVE 快照
        topic_id=102,
        stance_score=6.0,
        stance_category="support",
        survey_answers=SUPPORT_ANSWERS,
        survey_open_answers={"Q9": "測試"},
    )

    assert result is None       # 寫不進去
    assert not MatchQueueEntry.objects.filter(user=user_a, match=match).exists()
    match.refresh_from_db()
    assert match.user_a_score is None


@pytest.mark.django_db
def test_survey_write_rejected_for_non_participant():
    """鎖內也要確認身份：呼叫端傳錯 match 不該寫得進去。"""
    from apps.matching.services.matcher import record_godot_survey

    user_a, user_b = _make_users()
    outsider = User.objects.create_user(username="uc", password="pw")
    match = _godot_match(user_a, user_b, room_id="room-race-outsider")

    result = record_godot_survey(
        user=outsider,
        match=match,
        topic_id=102,
        stance_score=6.0,
        stance_category="support",
        survey_answers=SUPPORT_ANSWERS,
        survey_open_answers={"Q9": "測試"},
    )

    assert result is None
    assert not MatchQueueEntry.objects.filter(user=outsider).exists()


@pytest.mark.django_db
def test_return_to_normal_does_not_touch_other_rooms_credentials():
    """退回一般模式只能取消「本次被裁決那間房」的 queue entry。

    MATCHED entry 是 match_pretest_state 判斷「填過前測問卷」的憑證，波及其他房
    等於讓歷史房間的前測紀錄憑空消失。
    """
    from api.godot_binding import match_pretest_state
    from apps.matching.services.matcher import resolve_godot_survey_gate

    user_a, user_b = _make_users()
    # 同一位使用者、同一個議題的另一間（較早的）房，雙方都已填完。
    old_match = _godot_match(user_a, user_b, room_id="room-old-done")
    _submit_survey(user_a, SUPPORT_ANSWERS)
    _submit_survey(user_b, OPPOSE_ANSWERS)
    old_match.refresh_from_db()
    assert match_pretest_state(old_match)["both_done"] is True
    DialogueMatch.objects.filter(pk=old_match.pk).update(
        status=DialogueMatch.Status.CLOSED
    )

    # 新的一間房，A 填完、B 離開 → 裁決作廢 → A 退回一般模式。
    new_match = _godot_match(user_a, user_b, room_id="room-new-cancel")
    _submit_survey(user_a, SUPPORT_ANSWERS)
    new_match.refresh_from_db()
    _mark_seen(new_match, user_a, seconds_ago=1)
    _mark_seen(new_match, user_b, seconds_ago=GODOT_PRESENCE_TIMEOUT + 10)

    resolve_godot_survey_gate(match=new_match)

    # 舊房的憑證必須毫髮無傷。
    old_match.refresh_from_db()
    assert match_pretest_state(old_match)["both_done"] is True


@pytest.mark.django_db
def test_both_participants_each_receive_cancel_notice_once():
    """通知要逐人確認：兩位各自收到剛好一次，不是只有觸發裁決的那個人。"""
    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-notice-both")
    _submit_survey(user_a, SUPPORT_ANSWERS)
    match.refresh_from_db()
    _mark_seen(match, user_a, seconds_ago=1)
    _mark_seen(match, user_b, seconds_ago=GODOT_PRESENCE_TIMEOUT + 10)

    client_a = APIClient()
    client_a.force_authenticate(user=user_a)
    client_b = APIClient()
    client_b.force_authenticate(user=user_b)

    # A 的輪詢觸發裁決並拿到原因。
    a_first = client_a.get("/api/matching/status/?topic_id=102")
    assert a_first.data["binding_cancel_reason"] == "godot_partner_left"
    # A 再次輪詢就不該重複收到。
    a_second = client_a.get("/api/matching/status/?topic_id=102")
    assert a_second.data["binding_cancel_reason"] is None

    # B 沒有觸發裁決，但仍然要收到一次。
    b_first = client_b.get("/api/matching/status/?topic_id=102")
    assert b_first.data["binding_cancel_reason"] == "godot_partner_left"
    b_second = client_b.get("/api/matching/status/?topic_id=102")
    assert b_second.data["binding_cancel_reason"] is None


@pytest.mark.django_db
def test_command_cancellation_still_notifies_both():
    """清理指令取消時沒有任何請求在場，兩位之後輪詢都要收得到。"""
    from django.core.management import call_command

    user_a, user_b = _make_users()
    match = _godot_match(
        user_a, user_b, room_id="room-notice-cmd", deadline_offset_seconds=-60
    )

    call_command("close_expired_godot_matches")
    match.refresh_from_db()
    assert match.status == DialogueMatch.Status.CANCELLED

    for user in (user_a, user_b):
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get("/api/matching/status/?topic_id=102")
        assert response.data["binding_cancel_reason"] == "godot_survey_timeout"
