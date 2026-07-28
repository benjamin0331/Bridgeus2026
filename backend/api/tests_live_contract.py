"""Live smoke test for the <reply> output contract.

Excluded from CI (`-m 'not live'` in pyproject). Run explicitly:

    cd backend && uv run pytest api/tests_live_contract.py -m live -v -s

This is the only test that exercises the real assistant prefill: every other
test feeds a hand-written payload through ReplyStreamGate, which proves the
gate parses correctly but says nothing about whether the model actually honours
the contract. 20 rounds, half of them convergence/focus signals (the exact
condition that produced the original leak) and half opening new directions.
"""

import os

import pytest

from apps.matching.services.ai_agent import (
    DialogueAgent,
    DialoguePhase,
    DialogueSession,
    ReplyStreamGate,
)

pytestmark = pytest.mark.live

# 10 收斂/聚焦信號 — 這是原始 bug 的觸發條件（三之二節判定為「收斂」）
CONVERGENCE_PROMPTS = [
    "所以優勢就是比較便宜嗎",
    "我想講的就是核廢料要放哪裡，不是別的",
    "我不做這個假設，我不是決策者",
    "你剛剛說成本比較低，是這樣嗎？",
    "我的重點是安全，你認同嗎",
    "先說清楚，你是說台灣現在缺電對吧",
    "就是這個，除役成本你算進去了沒有",
    "我要說的是，風險不能只看機率",
    "所以你的意思是核電比燃煤乾淨，對不對",
    "我說的就是地震，其他先不談",
]

# 10 開啟新方向 — 對照組，確保契約在非收斂情境下同樣成立
NEW_DIRECTION_PROMPTS = [
    "核能發電的成本結構到底是怎麼算的？",
    "德國廢核之後電價變化如何？",
    "再生能源在台灣的佔比有機會到多少",
    "小型模組化反應爐是不是比較安全",
    "核廢料最終處置場國際上有成功案例嗎",
    "台積電用電量對能源政策的影響有多大",
    "如果不用核電，減碳目標還做得到嗎",
    "核電廠延役的技術門檻在哪裡",
    "電網韌性跟發電方式有關係嗎",
    "為什麼有些環保團體反而支持核電",
]

LIVE_PROMPTS = CONVERGENCE_PROMPTS + NEW_DIRECTION_PROMPTS

LEAK_MARKERS = [
    "<judgment>",
    "</judgment>",
    "<reply>",
    "</reply>",
    "內部確認",
    "內部判定",
    "判定為",
    "強制使用",
    "承接深化型",
]


def _make_session(user_message: str, turn_count_hint: int) -> DialogueSession:
    session = DialogueSession(
        topic="核能政策",
        topic_description="台灣是否應重啟核電廠以應對能源轉型與減碳需求",
        agent_stance="支持重啟核電",
        agent_stance_summary="核電是兼顧減碳與穩定供電的務實選擇",
        user_stance_label="反對核電",
        user_stance_score=2.0,
        user_initial_argument="我認為核安風險與核廢料問題讓核電不適合台灣。",
    )
    session.add_user_message(user_message)
    # Exercise all three phases across the 20 rounds.
    session.dialogue_phase = DialoguePhase.from_turn_count(turn_count_hint)
    return session


@pytest.mark.asyncio
async def test_live_output_contract_holds_over_20_rounds():
    if not os.getenv("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    agent = DialogueAgent(collection_name="nuclear_energy_all")

    failures = []
    leaks = []

    for index, prompt in enumerate(LIVE_PROMPTS):
        # Cycle through engagement (1-3) / confrontation (4-8) / convergence (9+).
        session = _make_session(prompt, turn_count_hint=(index % 12) + 1)
        gate = ReplyStreamGate()
        streamed = ""
        raw = ""

        async for chunk in agent.astream_respond(session):
            raw += chunk
            streamed += gate.feed(chunk)
        tail, ok = gate.finish()
        streamed += tail

        label = "convergence" if index < len(CONVERGENCE_PROMPTS) else "new-direction"

        if not ok:
            failures.append((index + 1, label, prompt, raw))
            continue

        hit = [marker for marker in LEAK_MARKERS if marker in streamed]
        if hit:
            leaks.append((index + 1, label, prompt, hit, streamed))

        print(f"[{index + 1:2d}/{len(LIVE_PROMPTS)}] {label:14s} ok={ok} "
              f"judgment={len(gate.judgment):4d}ch reply={len(streamed):4d}ch")

    rate = (len(LIVE_PROMPTS) - len(failures)) / len(LIVE_PROMPTS)
    print(f"\ncontract satisfaction rate: {rate:.0%} "
          f"({len(LIVE_PROMPTS) - len(failures)}/{len(LIVE_PROMPTS)})")

    for round_no, label, prompt, raw in failures:
        print(f"\n--- FAILED round {round_no} ({label}) prompt={prompt!r}")
        print(f"raw output:\n{raw}")

    for round_no, label, prompt, hit, streamed in leaks:
        print(f"\n--- LEAK round {round_no} ({label}) prompt={prompt!r} markers={hit}")
        print(f"streamed:\n{streamed}")

    assert not failures, f"{len(failures)}/{len(LIVE_PROMPTS)} rounds violated the contract"
    assert not leaks, f"{len(leaks)} rounds leaked judgment markers into the reply"
