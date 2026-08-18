"""
AI Dialogue Agent Service (M3)

RAG-powered 對立立場對話代理人，根據知識庫中的權威資料
以使用者的對立立場進行結構化去極化對話。

核心功能：
- 從 ChromaDB 檢索與議題相關的知識
- 注入對話歷史，維持 stateful 多輪對話
- 根據 dialogue_phase 動態調整對話策略
- 支援立場反轉：接收 M2 的 stance score，以對立方身份回應
- System prompt 從外部檔案載入，迭代 prompt 不需改程式碼

Owner: 伍晨安 (Backend Developer)
Skeleton: Benjamin (PM)
"""

import asyncio
import os
import random
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from core.chroma_utils import ensure_chroma_dir_writable
from core.llm_provider import get_embeddings, get_llm


# ═══════════════════════════════════════════════════════════
# Response Chunking & Streaming
# ═══════════════════════════════════════════════════════════

_SENTENCE_END_RE = re.compile(r"(?<=[。！？…])\s*")


def split_into_chunks(text: str, max_chars: int = 30) -> list[str]:
    """Split Chinese replies into display chunks without cutting sentences."""
    sentences = [sentence for sentence in _SENTENCE_END_RE.split(text) if sentence.strip()]
    chunks: list[str] = []
    current = ""

    for sentence in sentences:
        if not current:
            current = sentence
        elif len(current) + len(sentence) <= max_chars:
            current += sentence
        else:
            chunks.append(current)
            current = sentence

    if current:
        chunks.append(current)

    return chunks


async def stream_chunks(
    chunks: list[str],
    send_message,
    send_typing=None,
    min_delay: float = 2.0,
    max_delay: float = 3.0,
) -> None:
    """Send pre-split chunks with a small typing delay between chunks."""
    for chunk in chunks:
        if send_typing:
            await send_typing()
        await asyncio.sleep(random.uniform(min_delay, max_delay))
        await send_message(chunk)

# ═══════════════════════════════════════════════════════════
# Prompt Loading
# ═══════════════════════════════════════════════════════════

# Prompt 檔案位置：相對於此檔案的路徑
# backend/apps/matching/services/ai_agent.py
# backend/apps/matching/prompts/system_prompt_v2.txt
_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
_DEFAULT_PROMPT_FILE = "system_prompt_v2.txt"


def load_system_prompt(filename: str = _DEFAULT_PROMPT_FILE) -> str:
    """
    從 prompts/ 目錄載入 system prompt。

    好處：
    - 改 prompt 不需要動程式碼，git diff 只會顯示 .txt 的變更
    - 未來不同議題可以用不同 prompt 檔案
    - 團隊協作時 prompt 和程式邏輯不會衝突

    Args:
        filename: prompt 檔案名稱

    Returns:
        prompt 模板字串（含 {variable} 佔位符）

    Raises:
        FileNotFoundError: 找不到 prompt 檔案
    """
    prompt_path = _PROMPT_DIR / filename
    if not prompt_path.exists():
        raise FileNotFoundError(
            f"找不到 prompt 檔案：{prompt_path}\n"
            f"請確認 {_PROMPT_DIR} 目錄下有 {filename}"
        )

    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read()


# ═══════════════════════════════════════════════════════════
# Dialogue Phase
# ═══════════════════════════════════════════════════════════

class DialoguePhase(Enum):
    """
    對話階段，由後端根據語義距離變化趨勢判定。

    判定邏輯（由 M5 NLP 模組計算，此處僅定義 enum）：
    - engagement:     前 3 輪固定，或語義距離尚未建立基線
    - confrontation:  語義距離穩定或擴大（雙方仍在交鋒）
    - convergence:    最近 3 輪語義距離持續縮小（雙方趨向共識）
    """
    ENGAGEMENT = "engagement"
    CONFRONTATION = "confrontation"
    CONVERGENCE = "convergence"

    @classmethod
    def from_turn_count(cls, turn_count: int) -> "DialoguePhase":
        """
        Fallback：當 M5 尚未接入時，用輪次簡單判定。

        正式版應由 M5 語義距離斜率決定，此方法僅供開發測試。
        """
        if turn_count <= 3:
            return cls.ENGAGEMENT
        elif turn_count <= 8:
            return cls.CONFRONTATION
        else:
            return cls.CONVERGENCE


# ═══════════════════════════════════════════════════════════
# Conversation History
# ═══════════════════════════════════════════════════════════

@dataclass
class DialogueMessage:
    """單則對話訊息。"""
    role: str  # "user" or "agent"
    content: str

    def to_dict(self) -> dict:
        return {"role": self.role, "content": self.content}

    @classmethod
    def from_dict(cls, data: dict) -> "DialogueMessage":
        return cls(role=data["role"], content=data["content"])


@dataclass
class DialogueSession:
    """
    單場對話的狀態容器。

    儲存對話歷史、使用者立場資訊、當前階段等。
    由 M4 Dialogue Room 在 WebSocket 連線時建立，
    每次使用者發言後更新，傳入 DialogueAgent.respond()。

    序列化：
        data = session.to_dict()       # 存入 Django cache / DB
        session = DialogueSession.from_dict(data)  # WebSocket 重連後還原
    """
    topic: str = ""
    topic_description: str = ""
    agent_stance: str = ""
    agent_stance_summary: str = ""
    user_stance_label: str = ""
    user_stance_score: float = 4.0
    user_initial_argument: str = ""
    user_reasoning_mode: str = "unknown"
    focus_signal_count: int = 0
    dialogue_phase: DialoguePhase = DialoguePhase.ENGAGEMENT
    history: list[DialogueMessage] = field(default_factory=list)

    def to_dict(self) -> dict:
        """序列化為 JSON-safe dict，供 Django cache 或資料庫儲存。"""
        return {
            "topic": self.topic,
            "topic_description": self.topic_description,
            "agent_stance": self.agent_stance,
            "agent_stance_summary": self.agent_stance_summary,
            "user_stance_label": self.user_stance_label,
            "user_stance_score": self.user_stance_score,
            "user_initial_argument": self.user_initial_argument,
            "user_reasoning_mode": self.user_reasoning_mode,
            "focus_signal_count": self.focus_signal_count,
            "dialogue_phase": self.dialogue_phase.value,
            "history": [m.to_dict() for m in self.history],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DialogueSession":
        """從 dict 還原 DialogueSession（WebSocket 重連後使用）。"""
        return cls(
            topic=data.get("topic", ""),
            topic_description=data.get("topic_description", ""),
            agent_stance=data.get("agent_stance", ""),
            agent_stance_summary=data.get("agent_stance_summary", ""),
            user_stance_label=data.get("user_stance_label", ""),
            user_stance_score=data.get("user_stance_score", 4.0),
            user_initial_argument=data.get("user_initial_argument", ""),
            user_reasoning_mode=data.get("user_reasoning_mode", "unknown"),
            focus_signal_count=data.get("focus_signal_count", 0),
            dialogue_phase=DialoguePhase(
                data.get("dialogue_phase", DialoguePhase.ENGAGEMENT.value)
            ),
            history=[
                DialogueMessage.from_dict(m) for m in data.get("history", [])
            ],
        )

    @property
    def effective_reasoning_mode(self) -> str:
        """Resolved mode injected into the prompt.
        unknown + focus_signal_count >= 2 → upgrade to collaborative."""
        if self.user_reasoning_mode == "unknown" and self.focus_signal_count >= 2:
            return "collaborative"
        return self.user_reasoning_mode

    @property
    def turn_count(self) -> int:
        """使用者發言的輪次數。"""
        return sum(1 for m in self.history if m.role == "user")

    def add_user_message(self, content: str) -> None:
        self.history.append(DialogueMessage(role="user", content=content))

    def add_agent_message(self, content: str) -> None:
        self.history.append(DialogueMessage(role="agent", content=content))

    def format_history(
        self,
        exclude_last: bool = False,
        max_turns: int | None = None,
    ) -> str:
        """
        將對話歷史格式化為 prompt 可讀的字串。

        Args:
            exclude_last: 若為 True，排除最後一則訊息。
                          respond() 傳入 exclude_last=True 以避免最新使用者訊息
                          同時出現在 {conversation_history}（system）和
                          ("human", "{user_message}") 兩處造成重複。
            max_turns:    保留最近 N 輪（一輪 = 使用者 + 代理人各一則）。
                          None 表示不限制。DialogueAgent 預設傳入 20，
                          避免長對話超過 LLM context window。

        格式：
            使用者：我覺得核能不安全...
            對話者：其實從數據來看...
        """
        messages = self.history[:-1] if exclude_last and self.history else self.history

        if max_turns is not None:
            # 一輪 = 2 則訊息（user + agent），取最後 max_turns 輪
            messages = messages[-(max_turns * 2):]

        if not messages:
            return "（這是對話的第一輪，尚無歷史紀錄。）"

        lines = []
        for msg in messages:
            label = "使用者" if msg.role == "user" else "對話者"
            lines.append(f"{label}：{msg.content}")
        return "\n\n".join(lines)


# ═══════════════════════════════════════════════════════════
# Reasoning Mode Inference
# ═══════════════════════════════════════════════════════════

_COLLABORATIVE_MARKERS = frozenset({
    "雖然", "但", "另一方面", "不確定", "難說", "也許", "可能",
    "複雜", "搞不清楚", "兩難", "理解", "然而", "不過", "折衷",
    "既然", "畢竟", "或許", "感覺", "說實話",
})
_POLARIZED_MARKERS = frozenset({
    "絕對", "一定要", "完全反對", "完全支持", "堅決",
    "絕不", "必須廢核", "必須重啟", "不可能接受",
    "強烈反對", "強烈支持",
})

# ── Focus signal detection (real-time, per-turn) ─────────────────────────────
_FOCUS_CONFIRMATION = frozenset({
    "不是嗎", "對嗎", "是嗎", "對吧", "是吧",
    "認同嗎", "你覺得呢", "你說呢", "對不對",
    "是這樣嗎", "這樣對吧", "是這樣吧", "你認為呢",
    "你認同", "說得對嗎", "沒錯吧", "沒問題吧",
})
_FOCUS_EXPLICIT = frozenset({
    "我想表示", "我要說的是", "就是這個", "我想講的",
    "我的意思是", "說的就是", "我想說的就是",
    "正是這樣", "我想強調的是", "我的重點是",
    "我說的是", "講的就是", "我說的就是",
    "先說清楚", "是前提", "必須先確認",
})
_FOCUS_REJECTION_NEGATION = frozenset({
    "不做", "不是決策者", "我不是", "我沒辦法",
    "不想做", "不接受", "不做此",
})
_FOCUS_REJECTION_HYPOTHETICAL = frozenset({
    "假設", "決策者", "假設你是", "假設我是",
})


def detect_focus_signal(message: str) -> bool:
    """
    Rule-based detector: returns True if the message is a convergence/focus signal.

    Rules (any one triggers True):
    1. 確認尋求 — contains a confirmation-seeking phrase (嗎/吧/呢 in confirmatory context)
    2. 拒絕假設 — negation word + hypothetical-framing word co-occur in same message
    3. 明確指認 — explicit anchoring phrase ("就是這個", "先說清楚" …)
    4. [收窄]    — TODO: scope-narrowing requires structural analysis; skipped for now
    """
    if any(phrase in message for phrase in _FOCUS_EXPLICIT):
        return True
    if any(phrase in message for phrase in _FOCUS_CONFIRMATION):
        return True
    if (
        any(neg in message for neg in _FOCUS_REJECTION_NEGATION)
        and any(hyp in message for hyp in _FOCUS_REJECTION_HYPOTHETICAL)
    ):
        return True
    return False


def infer_reasoning_mode(
    user_stance_score: float,
    user_initial_argument: str,
    opponent_view_text: str = "",
) -> str:
    """
    Heuristic classification of a user's argumentation style.

    collaborative — builds arguments collaboratively, shows nuance, articulates opponent view
    polarized     — entrenched stance, repeats same point, needs perspective-flip prompts
    unknown       — default; AI internally downgrades to collaborative after 2 focus signals
    """
    combined = f"{user_initial_argument} {opponent_view_text}"
    hedge_count = sum(1 for w in _COLLABORATIVE_MARKERS if w in combined)
    certainty_count = sum(1 for w in _POLARIZED_MARKERS if w in combined)
    is_extreme = user_stance_score <= 2.0 or user_stance_score >= 6.0

    if is_extreme and certainty_count >= 1:
        return "polarized"
    if hedge_count >= 2 and not is_extreme:
        return "collaborative"
    if len(opponent_view_text) >= 50 and certainty_count == 0 and not is_extreme:
        return "collaborative"
    return "unknown"


# ═══════════════════════════════════════════════════════════
# DialogueAgent
# ═══════════════════════════════════════════════════════════

class DialogueAgent:
    """
    RAG-based AI dialogue agent for depolarization.

    使用方式（M4 Dialogue Room 呼叫範例）：

        # 建立 session（WebSocket 連線時初始化）
        session = DialogueSession(
            topic="核能政策",
            topic_description="台灣是否應重啟核電廠",
            agent_stance="支持重啟核電",
            agent_stance_summary="核電是兼顧減碳與穩定供電的務實選擇",
            user_stance_label="反對核電",
            user_stance_score=2.0,
        )

        # 建立 agent（可指定不同 prompt 檔案）
        agent = DialogueAgent(collection_name="nuclear_energy_all")

        # 每次使用者發言
        session.add_user_message("核電風險太高了吧？")
        response = agent.respond(session)
        session.add_agent_message(response)
    """

    def __init__(
        self,
        collection_name: str = "general_knowledge",
        chroma_dir: str | None = None,
        retriever_k: int = 5,
        temperature: float = 0.3,
        prompt_file: str = _DEFAULT_PROMPT_FILE,
        max_history_turns: int = 20,
    ):
        self._chroma_dir = ensure_chroma_dir_writable(chroma_dir)
        self._collection_name = collection_name
        self._retriever_k = retriever_k
        self._temperature = temperature
        self._max_history_turns = max_history_turns

        # Load system prompt from file
        system_prompt_text = load_system_prompt(prompt_file)
        self._system_prompt_raw = system_prompt_text
        self._prompt = ChatPromptTemplate.from_messages(
            [
                ("system", system_prompt_text),
                ("human", "{user_message}"),
            ]
        )

        # Initialize retriever
        embeddings = get_embeddings()
        try:
            vectorstore = Chroma(
                persist_directory=self._chroma_dir,
                collection_name=self._collection_name,
                embedding_function=embeddings,
            )
        except Exception as exc:
            raise RuntimeError(
                "無法初始化 Chroma 知識庫。"
                f" collection={self._collection_name}, path={self._chroma_dir}。"
                " 請確認該目錄存在且目前執行帳號有讀寫權限。"
            ) from exc
        self._retriever = vectorstore.as_retriever(
            search_kwargs={"k": self._retriever_k}
        )

        # Initialize LLM
        self._llm = get_llm(temperature=self._temperature)

    def _retrieve_context(self, query: str) -> str:
        """從 ChromaDB 檢索相關知識，格式化為 prompt 可用的字串。"""
        docs = self._retriever.invoke(query)
        if not docs:
            return "（知識庫中未找到與此問題直接相關的參考資訊。）"

        parts = []
        for i, doc in enumerate(docs, 1):
            title = doc.metadata.get("title", "未知")
            source = doc.metadata.get("source", "未知")
            parts.append(
                f"[參考 {i}]（{source} — {title}）\n{doc.page_content}"
            )
        return "\n\n".join(parts)

    def respond(self, session: DialogueSession) -> str:
        """
        根據對話 session 狀態生成回應。

        Args:
            session: 包含完整對話狀態的 DialogueSession

        Returns:
            AI 代理人回應文字
        """
        user_messages = [m for m in session.history if m.role == "user"]
        if not user_messages:
            raise ValueError("Session 中沒有使用者訊息。")
        latest_msg = user_messages[-1].content

        rag_context = self._retrieve_context(latest_msg)

        prompt_vars = {
            "topic": session.topic,
            "topic_description": session.topic_description,
            "agent_stance": session.agent_stance,
            "agent_stance_summary": session.agent_stance_summary,
            "user_stance_label": session.user_stance_label,
            "user_stance_score": str(session.user_stance_score),
            "user_initial_argument": session.user_initial_argument,
            "rag_context": rag_context,
            "conversation_history": session.format_history(
                exclude_last=True, max_turns=self._max_history_turns
            ),
            "turn_count": str(session.turn_count),
            "dialogue_phase": session.dialogue_phase.value,
            "user_reasoning_mode": session.effective_reasoning_mode,
            "user_message": latest_msg,
        }

        chain = self._prompt | self._llm | StrOutputParser()
        try:
            return chain.invoke(prompt_vars)
        except Exception as exc:
            # LLM API 失敗（rate limit、network、invalid key）時，
            # 回傳 graceful 訊息讓 M4 能繼續維持 WebSocket 連線，
            # 同時把原始例外往上拋供 caller 記錄 log。
            raise RuntimeError(
                f"DialogueAgent LLM 呼叫失敗（輪次 {session.turn_count}）：{exc}"
            ) from exc

    async def astream_respond(self, session: DialogueSession):
        """Stream an AI reply for Django Channels while preserving session semantics."""
        import anthropic
        from asgiref.sync import sync_to_async

        user_messages = [m for m in session.history if m.role == "user"]
        if not user_messages:
            raise ValueError("Session 中沒有使用者訊息。")
        latest_msg = user_messages[-1].content

        rag_context = await sync_to_async(self._retrieve_context)(latest_msg)

        prompt_vars = {
            "topic": session.topic,
            "topic_description": session.topic_description,
            "agent_stance": session.agent_stance,
            "agent_stance_summary": session.agent_stance_summary,
            "user_stance_label": session.user_stance_label,
            "user_stance_score": str(session.user_stance_score),
            "user_initial_argument": session.user_initial_argument,
            "rag_context": rag_context,
            "conversation_history": session.format_history(
                exclude_last=True, max_turns=self._max_history_turns
            ),
            "turn_count": str(session.turn_count),
            "dialogue_phase": session.dialogue_phase.value,
            "user_reasoning_mode": session.effective_reasoning_mode,
        }

        system_text = self._system_prompt_raw
        for key, value in prompt_vars.items():
            system_text = system_text.replace("{" + key + "}", value)

        # ponytail: 用 env 開關直接 dump 組好的 system prompt，不要問模型要——
        # 模型背誦會截斷也會編。要在瀏覽器看就把 system_text 當一個 WS frame 送出去。
        if os.getenv("DUMP_SYSTEM_PROMPT"):
            print(f"\n===== SYSTEM PROMPT (turn {session.turn_count}) =====\n{system_text}\n=====\n")

        client = anthropic.AsyncAnthropic()
        async with client.messages.stream(
            model=os.getenv("CLAUDE_CHAT_MODEL", "claude-sonnet-4-6"),
            max_tokens=int(os.getenv("CLAUDE_CHAT_MAX_TOKENS", "1024")),
            temperature=self._temperature,
            system=[
                {
                    "type": "text",
                    "text": system_text,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": latest_msg}],
        ) as stream:
            async for text in stream.text_stream:
                yield text

    def respond_simple(self, user_message: str) -> str:
        """
        簡化版：不需要 session，用於快速測試。

        僅供開發測試，正式對話請用 respond()。
        """
        session = DialogueSession(
            topic="公共議題",
            topic_description="一般性公共議題討論",
            agent_stance="對立立場",
            agent_stance_summary="與使用者持相反觀點",
            user_stance_label="使用者立場",
            user_stance_score=4.0,
        )
        session.add_user_message(user_message)
        return self.respond(session)


# ═══════════════════════════════════════════════════════════
# CLI 測試入口
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    """
    快速測試：
        python -m apps.matching.services.ai_agent

    前置：
        python scripts/build_knowledge_base.py \
            --data-dir data/nuclear_energy --collection nuclear_energy_all
    """
    import os
    from pathlib import Path as _Path

    # 載入專案根目錄 .env
    try:
        from dotenv import load_dotenv as _load_dotenv
        for _p in [_Path(__file__).resolve().parents[3] / ".env"]:
            if _p.exists():
                _load_dotenv(_p)
                break
    except ImportError:
        pass

    # 若 ANTHROPIC_API_KEY 未設定，在執行時詢問
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("═" * 50)
        key = input("請輸入 Anthropic API Key：").strip()
        if not key:
            print("未輸入 API Key，程式結束。")
            raise SystemExit(1)
        os.environ["ANTHROPIC_API_KEY"] = key
        print("═" * 50)

    print("正在載入 embedding 模型（首次執行需下載 ~90MB，請稍候）...")
    print("🚀 初始化 DialogueAgent...")
    agent = DialogueAgent(collection_name="nuclear_energy_all")

    print("\n═" * 25)
    print("使用者推理模式（決定 AI 的對話策略）")
    print("  1  collaborative  — 協作探索型，AI 以共同思考者角色，視角翻轉最多 1 次")
    print("  2  polarized      — 立場鞏固型，AI 採完整三階段策略，正常頻率視角翻轉")
    print("  3  unknown        — 預設值，先以 polarized 為準，偵測 2 次聚焦信號後自動降級")
    mode_input = input("選擇 [1/2/3，直接 Enter = unknown]：").strip()
    reasoning_mode_map = {"1": "collaborative", "2": "polarized", "3": "unknown"}
    chosen_mode = reasoning_mode_map.get(mode_input, "unknown")
    print(f"→ 使用模式：{chosen_mode}\n")

    print("使用者立場（決定 AI 對立的方向）")
    print("  1  反對核電（stance_score=2.0）")
    print("  2  支持核電（stance_score=6.0）")
    print("  3  中立（stance_score=4.0）")
    stance_input = input("選擇 [1/2/3，直接 Enter = 反對核電]：").strip()
    stance_map = {
        "1": ("反對核電", 2.0, "支持重啟核電", "核電是兼顧減碳與穩定供電的務實選擇"),
        "2": ("支持核電", 6.0, "反對重啟核電", "核廢料與地震風險使核電不符台灣國情"),
        "3": ("中立",     4.0, "鼓勵深入辯論", "希望你能從多角度探索這個議題的核心矛盾"),
    }
    user_stance_label, user_stance_score, agent_stance, agent_stance_summary = \
        stance_map.get(stance_input, stance_map["1"])
    print(f"→ 使用者立場：{user_stance_label}，AI 採對立角色：{agent_stance}\n")
    print("═" * 25)

    session = DialogueSession(
        topic="核能政策",
        topic_description="台灣是否應重啟核電廠以應對能源轉型與減碳需求",
        agent_stance=agent_stance,
        agent_stance_summary=agent_stance_summary,
        user_stance_label=user_stance_label,
        user_stance_score=user_stance_score,
        user_reasoning_mode=chosen_mode,
    )

    print(f"[模式：{chosen_mode} | 使用者：{user_stance_label} | AI：{agent_stance}]")
    print("輸入 'exit' 或按 Ctrl+C 結束對話\n")

    while True:
        try:
            user_input = input("👤 你：").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n對話結束。")
            break

        if user_input.lower() == "exit":
            print("對話結束。")
            break
        if not user_input:
            continue

        session.add_user_message(user_input)
        session.dialogue_phase = DialoguePhase.from_turn_count(session.turn_count)

        print("\n🤖 代理人：", end="", flush=True)
        response = agent.respond(session)
        session.add_agent_message(response)
        print(response)
        print(f"\n[第 {session.turn_count} 輪 | 階段：{session.dialogue_phase.value} | 模式：{session.user_reasoning_mode}]\n")
