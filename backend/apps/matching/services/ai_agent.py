"""
AI Dialogue Agent Service (M3)

RAG-powered 對話代理人，扮演調解者角色，
根據知識庫中的權威資料以理性論述回應使用者。

此模組負責：
- 從 ChromaDB 檢索相關知識
- 組合 prompt 並呼叫 LLM 生成回應
- 提供可被 M4 (Dialogue Room) 呼叫的介面

Owner: 伍晨安 (Backend Developer)
"""

import os

from langchain_community.vectorstores import Chroma
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough

from core.llm_provider import get_embeddings, get_llm


# --- Prompt Template ---
# 調解者人設：溫暖理性、段落式回應、禁止條列
MEDIATOR_PROMPT = ChatPromptTemplate.from_template(
    """你是一位擅長調解公共議題衝突的專家。
請根據【參考資訊】，以溫暖且理性的段落回應使用者的挑戰。
絕對禁止使用 1. 2. 3. 或標題標籤。

【參考資訊】：
{context}

【使用者提問】：
{question}

【調解者回應】：
"""
)


class DialogueAgent:
    """
    RAG-based AI dialogue agent for depolarization.

    使用方式：
        agent = DialogueAgent(collection_name="nuclear_energy_news")
        response = agent.respond("你對核能的看法是什麼？")
    """

    def __init__(
        self,
        collection_name: str = "general_knowledge",
        chroma_dir: str | None = None,
        retriever_k: int = 5,
        temperature: float = 0.3,
    ):
        self._chroma_dir = chroma_dir or os.getenv(
            "CHROMA_PERSIST_DIR", "./chroma_data"
        )
        self._collection_name = collection_name
        self._retriever_k = retriever_k
        self._temperature = temperature

        self._chain = self._build_chain()

    def _build_chain(self):
        """Construct the RAG chain: retriever → prompt → LLM → parse."""
        embeddings = get_embeddings()
        vectorstore = Chroma(
            persist_directory=self._chroma_dir,
            collection_name=self._collection_name,
            embedding_function=embeddings,
        )
        retriever = vectorstore.as_retriever(
            search_kwargs={"k": self._retriever_k}
        )

        llm = get_llm(temperature=self._temperature)

        chain = (
            {"context": retriever, "question": RunnablePassthrough()}
            | MEDIATOR_PROMPT
            | llm
            | StrOutputParser()
        )
        return chain

    def respond(self, user_message: str) -> str:
        """
        Generate a mediator-style response to user input.

        Args:
            user_message: 使用者的對話訊息

        Returns:
            AI 代理人的回應文字
        """
        return self._chain.invoke(user_message)

    def respond_with_stance(
        self, user_message: str, agent_stance: str
    ) -> str:
        """
        Generate a response with explicit opposing stance.

        TODO: 正式版需整合 M2 的 stance vector，
              動態調整 prompt 中的立場方向。

        Args:
            user_message: 使用者的對話訊息
            agent_stance: AI 代理人被賦予的立場描述

        Returns:
            AI 代理人的回應文字
        """
        # Phase 2: 將 stance 注入 prompt
        # 目前先用基本版，後續由伍晨安擴充
        return self.respond(user_message)
