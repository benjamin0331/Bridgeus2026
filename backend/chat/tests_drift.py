"""
Tests for chat.services.drift (stance-drift tracking) and the
consumer's 200-char cumulative trigger.

Embedding construction:
  unit_vec(0) = strongly pro-nuclear initial stance
  unit_vec(1) = strongly anti-nuclear / shifted stance
  Messages near unit_vec(0) → small drift from initial
  Messages near unit_vec(1) → large drift from initial
"""

import asyncio

import numpy as np
import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.test import override_settings

from chat.models import Conversation, Message, StanceDrift
from chat.services.drift import DIRECTION_THRESHOLD, acalculate_drift, calculate_drift

User = get_user_model()

TEST_CHANNEL_LAYERS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
}

create_user = sync_to_async(User.objects.create_user, thread_sensitive=True)


def _unit(dim: int) -> list[float]:
    v = np.zeros(384, dtype=np.float32)
    v[dim] = 1.0
    return v.tolist()


def _perturb(base: list[float], std: float = 0.01, seed: int = 0) -> list[float]:
    rng = np.random.default_rng(seed)
    return (np.array(base) + rng.normal(0, std, 384)).tolist()


def _make_user(username):
    return User.objects.create_user(username=username, password="x")


def _make_conv(user_a, user_b, a_initial=None, b_initial=None):
    return Conversation.objects.create(
        topic_id=1, user_a=user_a, user_b=user_b,
        session_number=1, status=Conversation.Status.ACTIVE,
        user_a_initial_embedding=a_initial,
        user_b_initial_embedding=b_initial,
    )


def _make_msg(conv, sender, embedding=None, content="."):
    return Message.objects.create(
        conversation=conv, sender=sender, content=content, embedding=embedding,
    )


# ---------- return shape ----------

@pytest.mark.django_db
def test_calculate_drift_return_shape():
    alice = _make_user("dr_shape_a")
    bob = _make_user("dr_shape_b")
    conv = _make_conv(alice, bob, a_initial=_unit(0))
    _make_msg(conv, alice, _perturb(_unit(0)))

    r = calculate_drift(conv.id, alice.id)
    assert set(r.keys()) == {"drift_value", "direction"}
    assert isinstance(r["drift_value"], float)
    assert r["direction"] in {"approaching", "diverging", "stable"}


# ---------- early-return cases ----------

@pytest.mark.django_db
def test_no_initial_embedding_returns_zero():
    alice = _make_user("dr_noinit_a")
    bob = _make_user("dr_noinit_b")
    conv = _make_conv(alice, bob)  # no initial embeddings
    _make_msg(conv, alice, _perturb(_unit(0)))

    r = calculate_drift(conv.id, alice.id)
    assert r["drift_value"] == 0.0
    assert r["direction"] == "stable"
    assert StanceDrift.objects.filter(conversation=conv, user=alice).count() == 0


@pytest.mark.django_db
def test_no_messages_in_interval_returns_zero():
    alice = _make_user("dr_nomsg_a")
    bob = _make_user("dr_nomsg_b")
    conv = _make_conv(alice, bob, a_initial=_unit(0))
    # No messages with embeddings

    r = calculate_drift(conv.id, alice.id)
    assert r["drift_value"] == 0.0
    assert StanceDrift.objects.filter(conversation=conv, user=alice).count() == 0


@pytest.mark.django_db
def test_messages_without_embeddings_skipped():
    alice = _make_user("dr_nullemb_a")
    bob = _make_user("dr_nullemb_b")
    conv = _make_conv(alice, bob, a_initial=_unit(0))
    _make_msg(conv, alice, embedding=None)  # no embedding

    r = calculate_drift(conv.id, alice.id)
    assert r["drift_value"] == 0.0


# ---------- first measurement → always stable ----------

@pytest.mark.django_db
def test_first_measurement_direction_is_stable():
    alice = _make_user("dr_first_a")
    bob = _make_user("dr_first_b")
    conv = _make_conv(alice, bob, a_initial=_unit(0))
    _make_msg(conv, alice, _perturb(_unit(1)))  # already far from initial

    r = calculate_drift(conv.id, alice.id)
    assert r["direction"] == "stable", (
        "First measurement has no previous to compare against — must be 'stable'"
    )


# ---------- direction: approaching (drift increases) ----------

@pytest.mark.django_db
def test_direction_approaching_when_drift_increases():
    """
    Round 1: Alice near initial stance (low drift).
    Round 2: Alice far from initial stance (high drift).
    diff = drift2 - drift1 > DIRECTION_THRESHOLD → "approaching"
    """
    alice = _make_user("dr_approach_a")
    bob = _make_user("dr_approach_b")
    conv = _make_conv(alice, bob, a_initial=_unit(0))

    # Round 1: messages close to initial (dim 0)
    for i in range(3):
        _make_msg(conv, alice, _perturb(_unit(0), std=0.005, seed=i))
    r1 = calculate_drift(conv.id, alice.id)
    print(f"\n  round1 drift={r1['drift_value']:.4f}")
    assert r1["drift_value"] < 0.1

    # Round 2: messages shifted toward dim 1 (far from initial)
    for i in range(3):
        _make_msg(conv, alice, _perturb(_unit(1), std=0.005, seed=i + 10))
    r2 = calculate_drift(conv.id, alice.id)
    print(f"  round2 drift={r2['drift_value']:.4f}, direction={r2['direction']}")

    assert r2["drift_value"] > 0.5
    assert r2["direction"] == "approaching"


# ---------- direction: diverging (drift decreases) ----------

@pytest.mark.django_db
def test_direction_diverging_when_drift_decreases():
    """
    Round 1: Alice far from initial (high drift).
    Round 2: Alice returns near initial (low drift).
    diff < -DIRECTION_THRESHOLD → "diverging"
    """
    alice = _make_user("dr_diverg_a")
    bob = _make_user("dr_diverg_b")
    conv = _make_conv(alice, bob, a_initial=_unit(0))

    # Round 1: far from initial
    for i in range(3):
        _make_msg(conv, alice, _perturb(_unit(1), std=0.005, seed=i))
    r1 = calculate_drift(conv.id, alice.id)

    # Round 2: back to initial
    for i in range(3):
        _make_msg(conv, alice, _perturb(_unit(0), std=0.005, seed=i + 20))
    r2 = calculate_drift(conv.id, alice.id)
    print(f"\n  round1={r1['drift_value']:.4f} round2={r2['drift_value']:.4f} dir={r2['direction']}")

    assert r2["direction"] == "diverging"


# ---------- DB side effects ----------

@pytest.mark.django_db
def test_stance_drift_record_created():
    alice = _make_user("dr_db_a")
    bob = _make_user("dr_db_b")
    conv = _make_conv(alice, bob, a_initial=_unit(0))
    _make_msg(conv, alice, _perturb(_unit(0)))

    assert StanceDrift.objects.filter(conversation=conv, user=alice).count() == 0
    calculate_drift(conv.id, alice.id)
    assert StanceDrift.objects.filter(conversation=conv, user=alice).count() == 1


@pytest.mark.django_db
def test_multiple_calls_create_multiple_records():
    alice = _make_user("dr_multi_a")
    bob = _make_user("dr_multi_b")
    conv = _make_conv(alice, bob, a_initial=_unit(0))

    for call in range(3):
        _make_msg(conv, alice, _perturb(_unit(0), seed=call * 10))
        calculate_drift(conv.id, alice.id)

    assert StanceDrift.objects.filter(conversation=conv, user=alice).count() == 3


# ---------- interval scoping ----------

@pytest.mark.django_db
def test_interval_only_includes_messages_after_last_measurement():
    """
    After the first calculate_drift call, subsequent calls should only
    use messages created after that measurement, not previous ones.
    """
    alice = _make_user("dr_interval_a")
    bob = _make_user("dr_interval_b")
    conv = _make_conv(alice, bob, a_initial=_unit(0))

    # Round 1: near initial
    _make_msg(conv, alice, _unit(0))
    r1 = calculate_drift(conv.id, alice.id)

    # Round 2: far from initial — only these new messages should be used
    _make_msg(conv, alice, _unit(1))
    r2 = calculate_drift(conv.id, alice.id)
    print(f"\n  r1={r1['drift_value']:.4f}, r2={r2['drift_value']:.4f}")

    # r2 is based only on the unit(1) message, so drift should be ~1.0
    assert r2["drift_value"] > 0.8


# ---------- async wrapper ----------

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_acalculate_drift_wrapper():
    alice = await create_user(username="dr_async_a", password="x")
    bob = await create_user(username="dr_async_b", password="x")
    conv = await Conversation.objects.acreate(
        topic_id=1, user_a=alice, user_b=bob,
        session_number=1, status=Conversation.Status.ACTIVE,
        user_a_initial_embedding=_unit(0),
    )
    await Message.objects.acreate(
        conversation=conv, sender=alice, content=".",
        embedding=_perturb(_unit(0)),
    )

    r = await acalculate_drift(conv.id, alice.id)
    assert isinstance(r["drift_value"], float)
    count = await StanceDrift.objects.filter(conversation=conv, user=alice).acount()
    assert count == 1


# ---------- consumer 200-char trigger ----------

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_200_char_trigger_creates_drift_record():
    """
    Send a 200+ char message via WebSocket.
    Consumer should fire _run_periodic_analysis() which calls calculate_drift().
    A StanceDrift record must appear in the DB within the timeout.

    Pre-seed 3 messages with embeddings so calculate_drift has data to work with,
    independent of whether the current message's NLP task completes first.
    """
    from take_a_bridge.asgi import application

    alice = await create_user(username="trig_alice", password="x")
    bob = await create_user(username="trig_bob", password="x")
    conv = await Conversation.objects.acreate(
        topic_id=1, user_a=alice, user_b=bob,
        session_number=1, status=Conversation.Status.ACTIVE,
        user_a_initial_embedding=_unit(0),
    )

    # Pre-seed messages with embeddings so calculate_drift finds data immediately
    rng = np.random.default_rng(42)
    for _ in range(3):
        await Message.objects.acreate(
            conversation=conv, sender=alice, content="seeded",
            embedding=rng.random(384).tolist(),
        )

    comm = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
    await comm.connect()

    # 201 ASCII chars — reliable cross-platform, consumer counts len() not bytes
    long_msg = "nuclear energy stance " * 10  # 220 chars
    assert len(long_msg) >= 200
    await comm.send_json_to({"content": long_msg})
    await comm.receive_json_from(timeout=5)  # consume relay echo

    # Poll for StanceDrift (periodic analysis is a background task; allow up to 30s)
    found = False
    for _ in range(30):
        await asyncio.sleep(1)
        count = await StanceDrift.objects.filter(
            conversation=conv, user=alice
        ).acount()
        if count > 0:
            found = True
            break

    assert found, "StanceDrift should be created within 30s of 200-char trigger"

    await comm.disconnect()
