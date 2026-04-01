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

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from langchain_community.vectorstores import Chroma
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from core.llm_provider import get_embeddings, get_llm


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


@dataclass
class DialogueSession:
    """
    單場對話的狀態容器。

    儲存對話歷史、使用者立場資訊、當前階段等。
    由 M4 Dialogue Room 在 WebSocket 連線時建立，
    每次使用者發言後更新，傳入 DialogueAgent.respond()。
    """
    topic: str = ""
    topic_description: str = ""
    agent_stance: str = ""
    agent_stance_summary: str = ""
    user_stance_label: str = ""
    user_stance_score: float = 0.5
    dialogue_phase: DialoguePhase = DialoguePhase.ENGAGEMENT
    history: list[DialogueMessage] = field(default_factory=list)

    @property
    def turn_count(self) -> int:
        """使用者發言的輪次數。"""
        return sum(1 for m in self.history if m.role == "user")

    def add_user_message(self, content: str) -> None:
        self.history.append(DialogueMessage(role="user", content=content))

    def add_agent_message(self, content: str) -> None:
        self.history.append(DialogueMessage(role="agent", content=content))

    def format_history(self) -> str:
        """
        將對話歷史格式化為 prompt 可讀的字串。

        格式：
            使用者：我覺得核能不安全...
            對話者：其實從數據來看...
        """
        if not self.history:
            return "（這是對話的第一輪，尚無歷史紀錄。）"

        lines = []
        for msg in self.history:
            label = "使用者" if msg.role == "user" else "對話者"
            lines.append(f"{label}：{msg.content}")
        return "\n\n".join(lines)


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
            user_stance_score=0.75,
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
    ):
        self._chroma_dir = chroma_dir or os.getenv(
            "CHROMA_PERSIST_DIR", "./chroma_data"
        )
        self._collection_name = collection_name
        self._retriever_k = retriever_k
        self._temperature = temperature

        # Load system prompt from file
        system_prompt_text = load_system_prompt(prompt_file)
        self._prompt = ChatPromptTemplate.from_messages(
            [
                ("system", system_prompt_text),
                ("human", "{user_message}"),
            ]
        )

        # Initialize retriever
        embeddings = get_embeddings()
        vectorstore = Chroma(
            persist_directory=self._chroma_dir,
            collection_name=self._collection_name,
            embedding_function=embeddings,
        )
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
            "rag_context": rag_context,
            "conversation_history": session.format_history(),
            "turn_count": str(session.turn_count),
            "dialogue_phase": session.dialogue_phase.value,
            "user_message": latest_msg,
        }

        chain = self._prompt | self._llm | StrOutputParser()
        return chain.invoke(prompt_vars)

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
            user_stance_score=0.5,
        )
        session.add_user_message(user_message)
        return self.respond(session)


# ═══════════════════════════════════════════════════════════
# CLI 測試入口
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    """
    快速測試：
        LLM_PROVIDER=gemini python -m apps.matching.services.ai_agent

    前置：
        python scripts/build_knowledge_base.py \
            --input data/articles.json data/ptt_processed.json data/laws_processed.json \
            --collection nuclear_energy_all
    """
    print("🚀 初始化 DialogueAgent...")
    agent = DialogueAgent(collection_name="nuclear_energy_all")

    session = DialogueSession(
        topic="核能政策",
        topic_description="台灣是否應重啟核電廠以應對能源轉型與減碳需求",
        agent_stance="支持重啟核電",
        agent_stance_summary=(
            "在確保安全的前提下，核電是兼顧減碳與穩定供電的務實選擇，"
            "不應因恐懼而放棄"
        ),
        user_stance_label="反對核電",
        user_stance_score=0.75,
    )

    test_messages = [
        "核電廠萬一出事就是不可逆的災難，日本福島就是最好的例子，台灣這麼小根本承受不起。",
        "就算技術進步了，核廢料問題到現在還是無解啊，你要放哪裡？",
    ]

    for msg in test_messages:
        print(f"\n{'='*60}")
        print(f"👤 使用者：{msg}")
        session.add_user_message(msg)

        session.dialogue_phase = DialoguePhase.from_turn_count(
            session.turn_count
        )

        response = agent.respond(session)
        session.add_agent_message(response)
        print(f"\n🤖 代理人：{response}")
