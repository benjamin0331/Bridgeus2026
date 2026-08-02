"""Live smoke test for the <reply> output contract.

Excluded from CI (`-m 'not live'` in pyproject). Run explicitly:

    cd backend && uv run pytest api/tests_live_contract.py -m live -v -s

This is the only test that exercises the real model. Everything else feeds
hand-written payloads through ReplyStreamGate, which proves the gate parses
correctly but says nothing about whether the model honours the contract.

25 rounds:
  - 10 收斂/聚焦信號   — the condition that produced the first leak shape
  - 10 開啟新方向      — control
  -  5 長政策文本      — the condition that produces the second leak shape
                         (judgment language written INSIDE <reply>); short
                         prompts never reproduce it.

Side effect: every clean reply is written to
apps/matching/tests/fixtures/live_reply_samples.json, which is the
false-positive corpus for _JUDGMENT_LEAK_PATTERNS. Keeping that corpus made of
real replies is the only thing standing between a too-greedy pattern and a
participant being shown an error for a perfectly good turn.
"""

import json
import os
from pathlib import Path

import pytest

from api.consumers import _CONTRACT_CORRECTION
from apps.matching.services.ai_agent import (
    DialogueAgent,
    DialoguePhase,
    DialogueSession,
    ReplyStreamGate,
    detect_judgment_leak,
    salvage_reply,
)

pytestmark = pytest.mark.live

_FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "apps" / "matching" / "tests" / "fixtures" / "live_reply_samples.json"
)

# 10 收斂/聚焦信號 — 第一種洩漏形態（三之二節判定為「收斂」）的觸發條件
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

# 10 開啟新方向 — 對照組
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

# 5 長政策文本 — 第二種洩漏形態的觸發條件。
# 第 1 則是實際觸發回報 bug 的那段使用者輸入。
LONG_TEXT_PROMPTS = [
    (
        "我跟你說一下台灣現在的政策現況。2025 年 5 月 17 日核三廠 2 號機依照"
        "《核子反應器設施管制法》屆期停止運轉，台灣正式進入非核家園狀態。"
        "但同年 5 月 13 日立法院三讀通過《核管法》第 6 條修正案，把運轉執照"
        "年限從 40 年上限改為可申請延役，每次最長 20 年，而且已經停機的機組"
        "也能申請。接著 8 月 23 日重啟核三公投雖然同意票多於不同意票，但因為"
        "沒有達到門檻所以不通過。台電後來在 2025 年 11 月向核安會提出核二、"
        "核三的自主安全檢視報告，經濟部長也說評估後認為具再運轉可行性。"
        "所以現在的情況是，法律上路徑打開了，但實際上還沒有任何一部機組重啟。"
    ),
    (
        "關於核廢料我查了一些資料。台灣目前低放射性廢棄物暫存在蘭嶼貯存場和"
        "各核電廠內，蘭嶼的 10 萬桶從 1982 年放到現在超過 40 年，經濟部 2016 年"
        "就承諾要遷出但至今沒有場址。高放射性的用過燃料棒則全部放在各廠的燃料池"
        "和乾式貯存設施，核一廠的乾貯設施 2024 年才通過新北市水土保持審查。"
        "《放射性物料管理法》規定最終處置場址要在 2055 年前完成，但選址條例"
        "至今沒有立法。你怎麼看這種「先延役再說，處置場以後再想」的做法？"
    ),
    (
        "我想討論電力結構的實際數字。根據台電 2024 年的資料，台灣發電結構中"
        "燃氣約 42%、燃煤約 39%、再生能源約 11%、核能在核三除役前約 3%。"
        "政府 2050 淨零路徑規劃再生能源要到 60-70%，氫能 9-12%，火力加"
        "碳捕捉 20-27%。但 2024 年光電裝置容量成長已經明顯趨緩，離岸風電"
        "第三階段的區塊開發也有廠商因為成本問題退出。同時台積電一家的用電量"
        "就佔全台約 8%，而且還在蓋新廠。在這個結構下，把 3% 的核能拿掉或加回來，"
        "對整體減碳到底有多少實質影響？"
    ),
    (
        "國際上的比較我覺得很值得談。德國 2023 年 4 月關閉最後三座核電廠，"
        "2024 年再生能源佔比達到約 59%，但同時燃煤發電在 2022-2023 年一度回升，"
        "電價也是歐洲最高的幾個國家之一。日本福島事故後全面停機，但到 2024 年"
        "已經重啟 12 部機組，並在第七次能源基本計畫中把核能定位為「重要的"
        "脫碳電源」。法國核能佔比約 65%，但 2022 年因為應力腐蝕問題有超過"
        "一半機組停機，還一度變成電力淨進口國。這三個國家的路徑差異這麼大，"
        "台灣應該參考哪一個？"
    ),
    (
        "我想問一個制度面的問題。核安會在 2025 年的角色其實有點尷尬：一方面"
        "它是獨立管制機關，要對台電提出的自主安全檢視報告做技術審查；另一方面"
        "經濟部和台電已經先對外表達「具再運轉可行性」的立場，行政院也表示尊重"
        "公投結果會積極評估。從管制獨立性的角度看，當被管制者和上級機關都已經"
        "公開表態之後，管制機關實質上還有多少說不的空間？這跟核四當年"
        "封存前的決策過程有什麼不同嗎？"
    ),
]

LIVE_PROMPTS = (
    [("convergence", p) for p in CONVERGENCE_PROMPTS]
    + [("new-direction", p) for p in NEW_DIRECTION_PROMPTS]
    + [("long-text", p) for p in LONG_TEXT_PROMPTS]
)

TAG_MARKERS = ["<judgment>", "</judgment>", "<reply>", "</reply>"]

# Not in _JUDGMENT_LEAK_PATTERNS, but plausible judgment vocabulary the
# compressed 第十節 format now teaches the model ("開新方向", the A–E codes, the
# pipe-delimited shape). Reported, never asserted on: the point is to find out
# empirically whether the pattern list has a hole, rather than widening it on a
# hunch and buying false positives.
CANDIDATE_MARKERS = [
    "開新方向",
    "型別",
    "結構型別",
    "本輪選用",
    "收斂|",
    "開新方向|",
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
    session.dialogue_phase = DialoguePhase.from_turn_count(turn_count_hint)
    return session


async def _one_round(agent, session, correction: str = ""):
    gate = ReplyStreamGate()
    raw = ""
    async for chunk in agent.astream_respond(session, correction=correction):
        raw += chunk
        gate.feed(chunk)
    _, ok = gate.finish()
    return gate, ok, raw


def _write_corpus(samples):
    payload = {
        "_comment": (
            "Real agent replies used as the false-positive corpus for "
            "_JUDGMENT_LEAK_PATTERNS. Regenerated by "
            "api/tests_live_contract.py (run with -m live). Every entry must "
            "be a reply a participant legitimately saw or should have seen."
        ),
        "samples": samples,
    }
    with open(_FIXTURE_PATH, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


@pytest.mark.asyncio
async def test_live_output_contract_holds_over_25_rounds():
    if not os.getenv("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    agent = DialogueAgent(collection_name="nuclear_energy_all")

    failures = []          # contract never satisfied, even after correction
    corrected = []         # first attempt leaked, corrected retry succeeded
    tag_leaks = []         # tag markers survived into the visible reply
    candidate_hits = []    # near-miss vocabulary, reported not asserted
    samples = []           # clean replies → false-positive corpus

    for index, (label, prompt) in enumerate(LIVE_PROMPTS):
        session = _make_session(prompt, turn_count_hint=(index % 12) + 1)
        gate, ok, raw = await _one_round(agent, session)

        first_leak = gate.leak_pattern
        retried = False
        salvaged = None

        if not ok:
            # Level 1: corrected retry, exactly as production does it.
            retried = True
            gate, ok, raw = await _one_round(
                agent, session, correction=_CONTRACT_CORRECTION
            )
            if not ok:
                # Level 2: salvage.
                salvaged = salvage_reply(gate.reply)

        visible = gate.reply if ok else salvaged

        status = "ok"
        if not ok and salvaged is not None:
            status = "salvaged"
        elif not ok:
            status = "FAILED"
            failures.append((index + 1, label, prompt, gate.leak_pattern, raw))

        if retried and ok:
            corrected.append((index + 1, label, first_leak))

        if visible:
            hit = [marker for marker in TAG_MARKERS if marker in visible]
            if hit:
                tag_leaks.append((index + 1, label, hit, visible))
            near = [marker for marker in CANDIDATE_MARKERS if marker in visible]
            if near:
                candidate_hits.append((index + 1, label, near, visible))
            if ok:
                samples.append(
                    {
                        "source": f"live round {index + 1} ({label})",
                        "reply": visible,
                    }
                )

        print(
            f"[{index + 1:2d}/{len(LIVE_PROMPTS)}] {label:13s} "
            f"status={status:8s} retried={str(retried):5s} "
            f"first_leak={first_leak!r:16s} "
            f"judgment={len(gate.judgment):4d}ch "
            f"reply={len(visible or ''):4d}ch"
        )

    satisfied = len(LIVE_PROMPTS) - len(failures)
    rate = satisfied / len(LIVE_PROMPTS)
    print(f"\ncontract satisfaction rate: {rate:.0%} "
          f"({satisfied}/{len(LIVE_PROMPTS)})")
    print(f"corrected retries: {len(corrected)}  → {corrected}")

    long_text_start = len(CONVERGENCE_PROMPTS) + len(NEW_DIRECTION_PROMPTS)
    long_text_failures = [f for f in failures if f[0] > long_text_start]
    print(f"long-text rounds failing: {len(long_text_failures)}/5")

    for round_no, label, prompt, pattern, raw in failures:
        print(f"\n--- FAILED round {round_no} ({label}) pattern={pattern!r}")
        print(f"prompt: {prompt[:80]}…")
        print(f"raw output:\n{raw}")

    for round_no, label, hit, visible in tag_leaks:
        print(f"\n--- TAG LEAK round {round_no} ({label}) markers={hit}")
        print(visible)

    print(f"\nnear-miss vocabulary (NOT in the pattern list): "
          f"{len(candidate_hits)} rounds")
    for round_no, label, near, visible in candidate_hits:
        print(f"--- round {round_no} ({label}) candidates={near}")
        print(visible)

    if samples:
        _write_corpus(samples)
        print(f"\nwrote {len(samples)} clean replies to {_FIXTURE_PATH}")

    assert not tag_leaks, f"{len(tag_leaks)} rounds leaked tag markers"
    assert not failures, (
        f"{len(failures)}/{len(LIVE_PROMPTS)} rounds could not satisfy the "
        f"contract even after a corrected retry"
    )
