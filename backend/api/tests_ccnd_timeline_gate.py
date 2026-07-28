"""pytest for the CCND timeline gate, bornNodes, and the staff-only analysis API.

The gate is an experiment-design control, not a nicety: a participant who can
replay their own CCND before answering the CCND self-report items (questionnaire
C3 and Part F's F4 ux_ccnd) has seen the construct being measured. These tests
pin the rule down.

Run from backend/:
    pytest api/tests_ccnd_timeline_gate.py -v
"""
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from api.models import (
    AIConversation,
    CCNDTimelineUnlock,
    DialogueSessionRecord,
    PlatformFeedback,
    PostDialogueResponse,
)

User = get_user_model()

SESSION_ID = "sess-ccnd-gate"
BORN_MESSAGE_ID = "101"


def _tree_state(recorded_at: str):
    """An analysed H-AI tree where turn BORN_MESSAGE_ID gave birth to one node."""
    return {
        "version": 2,
        "mode": "ai_user_tree",
        "anchors": [],
        "participants": {
            "user": {
                "ownerKey": "user",
                "treeData": {
                    "id": "root",
                    "name": "核電",
                    "type": "root",
                    "children": [
                        {
                            "id": "anchor_safety",
                            "name": "核能安全",
                            "type": "anchor",
                            "hiddenUntilUsed": False,
                            "children": [
                                {
                                    "id": "agent_1",
                                    "name": "事故風險",
                                    "type": "point",
                                    "children": [],
                                    "messages": [
                                        {
                                            "text": "核電廠事故風險太高",
                                            "stance": "反對",
                                            "confidence": 0.9,
                                            "mode": "new",
                                            "recordedAt": recorded_at,
                                            "sourceMessageId": BORN_MESSAGE_ID,
                                            "sourceTimestamp": recorded_at,
                                            "sourceParticipant": "1",
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                },
                "analyzedSourceIds": [BORN_MESSAGE_ID],
                "analysisHistory": [
                    {
                        "sourceId": BORN_MESSAGE_ID,
                        "sourceType": "ai_user_prompt",
                        "analyzedAt": recorded_at,
                        "appliedItems": [],
                        "invalidItems": [],
                    }
                ],
            }
        },
    }


@pytest.fixture
def user(db):
    return User.objects.create_user(username="p1", password="pw")


@pytest.fixture
def client(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


@pytest.fixture
def session(db, user):
    """A finished H-AI conversation with one analysed turn."""
    now = timezone.now()
    record = DialogueSessionRecord.objects.create(
        user=user,
        session_id=SESSION_ID,
        topic_id=101,
        topic_title="核能發電",
        collection_name="nuclear_energy_all",
        semantic_tree_state=_tree_state(now.isoformat()),
        status=DialogueSessionRecord.Status.CLOSED,
        last_activity_at=now,
    )
    # the turn the tree was built from — id must match BORN_MESSAGE_ID
    turn = AIConversation.objects.create(
        user=user,
        session_id=SESSION_ID,
        topic_id=101,
        user_prompt="核電廠事故風險太高",
        ai_response="…",
    )
    record.semantic_tree_state = _tree_state(now.isoformat())
    # rewrite ids so the fixture's source ids match the real turn id
    state = record.semantic_tree_state
    owner = state["participants"]["user"]
    owner["treeData"]["children"][0]["children"][0]["messages"][0]["sourceMessageId"] = str(turn.id)
    owner["analyzedSourceIds"] = [str(turn.id)]
    owner["analysisHistory"][0]["sourceId"] = str(turn.id)
    record.semantic_tree_state = state
    record.save(update_fields=["semantic_tree_state"])
    record.turn_id = str(turn.id)
    return record


def _complete_questionnaire(user, *, session_id):
    """Full M6 flow: questionnaire row + Part F (what actually opens the gate)."""
    response = PostDialogueResponse.objects.create(
        user=user,
        topic_id=101,
        session_id=session_id,
        experiment_condition="ai",
        **{f"post_likert_{i}": 4 for i in range(1, 9)},
        exp_stance_change_1=4, exp_stance_change_2=4,
        exp_quality_1=4, exp_quality_2=4,
        exp_reflection_1=4, exp_reflection_2=4,
        ccnd_attention=4, ccnd_awareness=4, ccnd_influence=4,
        post_open_comprehension="x" * 60,
        consent_confirmed=True,
    )
    PlatformFeedback.objects.create(
        response=response,
        ux_matching=5, ux_chatroom=5, ux_nlp_intervention=5,
        ux_ccnd=5, ux_overall=5, nps_score=8,
    )
    return response


def _timeline_url(session):
    return (
        f"/api/history/conversations/ai/{session.session_id}"
        f"/semantic-tree/timeline/?as_of_message_id={session.turn_id}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# gate
# ─────────────────────────────────────────────────────────────────────────────

def test_timeline_locked_before_questionnaire(client, session):
    res = client.get(_timeline_url(session))
    assert res.status_code == 403
    assert res.data["timeline_unlocked"] is False
    assert res.data["timeline_lock_reason"] == "questionnaire_incomplete"


def test_timeline_still_locked_after_questionnaire_without_part_f(client, session, user):
    """C3 answered but F4 (Part F ux_ccnd) still pending -> must stay locked."""
    PostDialogueResponse.objects.create(
        user=user,
        topic_id=101,
        session_id=SESSION_ID,
        experiment_condition="ai",
        **{f"post_likert_{i}": 4 for i in range(1, 9)},
        exp_stance_change_1=4, exp_stance_change_2=4,
        exp_quality_1=4, exp_quality_2=4,
        exp_reflection_1=4, exp_reflection_2=4,
        ccnd_attention=4, ccnd_awareness=4, ccnd_influence=4,
        post_open_comprehension="x" * 60,
        consent_confirmed=True,
    )
    assert client.get(_timeline_url(session)).status_code == 403


def test_timeline_unlocks_after_part_f(client, session, user):
    _complete_questionnaire(user, session_id=SESSION_ID)
    res = client.get(_timeline_url(session))
    assert res.status_code == 200
    assert res.data["asOfMessageId"] == session.turn_id


def test_timeline_unlocks_with_researcher_override(client, session, user):
    CCNDTimelineUnlock.objects.create(
        user=user,
        kind="ai",
        conversation_id=SESSION_ID,
        reason="participant abandoned questionnaire",
    )
    assert client.get(_timeline_url(session)).status_code == 200


def test_timeline_auto_unlocks_after_12h(client, session):
    session.last_activity_at = timezone.now() - timedelta(hours=13)
    session.save(update_fields=["last_activity_at"])
    assert client.get(_timeline_url(session)).status_code == 200


def test_timeline_still_locked_11h_after_end(client, session):
    session.last_activity_at = timezone.now() - timedelta(hours=11)
    session.save(update_fields=["last_activity_at"])
    assert client.get(_timeline_url(session)).status_code == 403


def test_history_detail_exposes_lock_state(client, session, user):
    detail_url = f"/api/history/conversations/ai/{SESSION_ID}/"
    assert client.get(detail_url).data["timeline_unlocked"] is False

    _complete_questionnaire(user, session_id=SESSION_ID)
    assert client.get(detail_url).data["timeline_unlocked"] is True


# ─────────────────────────────────────────────────────────────────────────────
# bornNodes (option C)
# ─────────────────────────────────────────────────────────────────────────────

def test_timeline_reports_born_nodes(client, session, user):
    _complete_questionnaire(user, session_id=SESSION_ID)
    res = client.get(_timeline_url(session))
    assert res.status_code == 200
    born = res.data["bornNodes"]
    assert [n["name"] for n in born] == ["事故風險"]
    assert born[0]["id"] == "agent_1"


# ─────────────────────────────────────────────────────────────────────────────
# staff-only snapshot analysis (option A)
# ─────────────────────────────────────────────────────────────────────────────

def _analysis_url(session_id=SESSION_ID):
    return f"/api/history/conversations/ai/{session_id}/semantic-tree/snapshot-analysis/"


def test_snapshot_analysis_forbidden_for_participant(client, session, user):
    """Even a participant who finished everything must not see the DVs."""
    _complete_questionnaire(user, session_id=SESSION_ID)
    assert client.get(_analysis_url()).status_code == 403


def test_snapshot_analysis_allowed_for_staff(db, session):
    staff = User.objects.create_user(username="researcher", password="pw", is_staff=True)
    api = APIClient()
    api.force_authenticate(user=staff)

    res = api.get(_analysis_url())
    assert res.status_code == 200
    assert res.data["kind"] == "ai"
    subjects = res.data["subjects"]
    assert len(subjects) == 1
    summary = subjects[0]["summary"]
    # one lit micro node under one anchor -> 1/6 macro coverage
    assert summary["final_micro_count"] == 1
    assert summary["final_macro_count"] == 1
    assert summary["macro_denominator"] == 6


def test_snapshot_analysis_404_for_unknown_conversation(db):
    staff = User.objects.create_user(username="r2", password="pw", is_staff=True)
    api = APIClient()
    api.force_authenticate(user=staff)
    assert api.get(_analysis_url("nope")).status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# participant-facing insights (same numbers, gated + subject-scoped + partner-stripped)
# ─────────────────────────────────────────────────────────────────────────────

def _insights_url(session_id=SESSION_ID):
    return f"/api/history/conversations/ai/{session_id}/ccnd-insights/"


def test_insights_locked_before_part_f(client, session):
    assert client.get(_insights_url()).status_code == 403


def test_insights_unlocked_after_part_f(client, session, user):
    _complete_questionnaire(user, session_id=SESSION_ID)
    res = client.get(_insights_url())
    assert res.status_code == 200
    summary = res.data["summary"]
    assert summary["final_macro_count"] == 1
    assert summary["macro_denominator"] == 6
    assert summary["final_micro_count"] == 1
    # raw layer for the expandable section
    assert [e["node_name"] for e in res.data["novelty"]["first_appearance"]] == ["事故風險"]
    assert "pairs" in res.data["similarity"]


def test_insights_never_leaks_partner_side(client, session, user):
    """A participant must not be handed the other person's cognitive map."""
    _complete_questionnaire(user, session_id=SESSION_ID)
    res = client.get(_insights_url())
    assert res.status_code == 200
    assert "partner_side" not in res.data


@pytest.mark.django_db
def test_insights_subject_is_the_requesting_user_not_user_a():
    """H-H: user_b must get user_b's own analysis, not analyze_conversation_ccnd's
    user_a default."""
    from api.models import DialogueMatch

    user_a = User.objects.create_user(username="a", password="pw")
    user_b = User.objects.create_user(username="b", password="pw")
    now = timezone.now()
    room_id = "room-subject-test"

    def side(anchor_id, anchor_name, node_name):
        return {
            "ownerKey": "x",
            "treeData": {
                "id": "root", "name": "核電", "type": "root",
                "children": [{
                    "id": anchor_id, "name": anchor_name, "type": "anchor",
                    "hiddenUntilUsed": False,
                    "children": [{
                        "id": "agent_1", "name": node_name, "type": "point",
                        "children": [],
                        "messages": [{
                            "text": node_name, "stance": "中立", "mode": "new",
                            "recordedAt": now.isoformat(),
                            "sourceMessageId": "1",
                            "sourceTimestamp": now.isoformat(),
                        }],
                    }],
                }],
            },
            "analyzedSourceIds": ["1"],
            "analysisHistory": [{"sourceId": "1", "analyzedAt": now.isoformat()}],
        }

    match = DialogueMatch.objects.create(
        topic_id=101, user_a=user_a, user_b=user_b,
        user_a_score=6, user_b_score=2,
        room_id=room_id,
        status=DialogueMatch.Status.CLOSED,
        closed_at=now,
        stats={"semantic_tree": {
            "version": 2, "mode": "participant_trees", "anchors": [],
            "participants": {
                "user_a": side("anchor_safety", "核能安全", "A的節點"),
                "user_b": side("anchor_waste", "核廢處理", "B的節點"),
            },
        }},
    )
    CCNDTimelineUnlock.objects.create(user=user_b, kind="match", conversation_id=room_id)

    api = APIClient()
    api.force_authenticate(user=user_b)
    res = api.get(f"/api/history/conversations/match/{match.room_id}/ccnd-insights/")

    assert res.status_code == 200
    names = [e["node_name"] for e in res.data["novelty"]["first_appearance"]]
    assert names == ["B的節點"]          # own side
    assert "A的節點" not in str(res.data)  # partner's side nowhere in the payload
