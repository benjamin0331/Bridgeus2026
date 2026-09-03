"""Both DialogueAgent reply paths must send one and the same prompt.

The WebSocket stream (`astream_respond`) and the REST fallback (`respond`)
used to assemble the prompt separately — a template on one side, string
substitution on the other — so a new prompt variable could silently reach only
one of them. These tests pin the two paths to `_build_prompt()`.
"""
import asyncio

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from apps.matching.services.ai_agent import DialogueAgent, DialogueSession

_PROMPT = (
    "議題：{topic}（{topic_description}）\n"
    "代理人立場：{agent_stance} — {agent_stance_summary}\n"
    "使用者：{user_stance_label} / {user_stance_score} / {user_initial_argument}\n"
    "知識：{rag_context}\n"
    "歷史：{conversation_history}\n"
    "輪次：{turn_count} 階段：{dialogue_phase} 模式：{user_reasoning_mode}\n"
    '輸出範例（字面大括號，不是變數）：{"reply": "…"}\n'
)


class _StubRetriever:
    def invoke(self, query):
        return []


def _agent() -> DialogueAgent:
    """Build an agent without touching Chroma, embeddings, or the network."""
    agent = DialogueAgent.__new__(DialogueAgent)
    agent._system_prompt_raw = _PROMPT
    agent._retriever = _StubRetriever()
    agent._max_history_turns = 20
    agent._temperature = 0.3
    agent._collection_name = "test_collection"
    return agent


def _session() -> DialogueSession:
    session = DialogueSession(
        topic="核能政策",
        topic_description="台灣是否應重啟核電廠",
        agent_stance="支持重啟核電",
        agent_stance_summary="核電兼顧減碳與穩定供電",
        user_stance_label="反對核電",
        user_stance_score=2.0,
        user_initial_argument="核廢料無處可去",
    )
    session.add_user_message("核電風險太高了吧？")
    return session


def test_build_prompt_substitutes_every_placeholder():
    system_text, user_text = _agent()._build_prompt(_session())

    assert "核能政策" in system_text
    assert "支持重啟核電" in system_text
    assert "反對核電" in system_text
    for placeholder in ("{topic}", "{rag_context}", "{turn_count}", "{dialogue_phase}"):
        assert placeholder not in system_text
    assert user_text == "核電風險太高了吧？"


def test_build_prompt_keeps_literal_braces_in_the_prompt_body():
    """A template engine would choke on the JSON example; substitution must not."""
    system_text, _ = _agent()._build_prompt(_session())
    assert '{"reply": "…"}' in system_text


def test_build_prompt_appends_correction_to_user_text_only():
    system_text, user_text = _agent()._build_prompt(_session(), correction="（請重寫）")

    assert user_text == "核電風險太高了吧？（請重寫）"
    assert "（請重寫）" not in system_text


def test_respond_sends_exactly_what_build_prompt_returned():
    agent = _agent()
    sent = {}

    def _record(messages):
        sent["system"] = messages[0].content
        sent["human"] = messages[1].content
        return AIMessage(content="ok")

    agent._llm = RunnableLambda(_record)
    assert agent.respond(_session(), correction="（請重寫）") == "ok"

    expected_system, expected_user = agent._build_prompt(_session(), correction="（請重寫）")
    assert sent["system"] == expected_system
    assert sent["human"] == expected_user


def test_astream_respond_sends_exactly_what_build_prompt_returned(monkeypatch):
    import anthropic

    sent = {}

    class _FakeStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        @property
        async def text_stream(self):  # pragma: no cover - replaced below
            raise AssertionError("unused")

    class _Stream(_FakeStream):
        def __init__(self, kwargs):
            sent.update(kwargs)

        @property
        def text_stream(self):
            async def _gen():
                yield "ok"

            return _gen()

    class _Messages:
        def stream(self, **kwargs):
            return _Stream(kwargs)

    class _FakeClient:
        def __init__(self, *a, **kw):
            self.messages = _Messages()

    monkeypatch.setattr(anthropic, "AsyncAnthropic", _FakeClient)

    agent = _agent()

    async def _drain():
        return [chunk async for chunk in agent.astream_respond(_session(), correction="（請重寫）")]

    assert asyncio.run(_drain()) == ["ok"]

    expected_system, expected_user = agent._build_prompt(_session(), correction="（請重寫）")
    assert sent["system"][0]["text"] == expected_system
    assert sent["messages"] == [{"role": "user", "content": expected_user}]
