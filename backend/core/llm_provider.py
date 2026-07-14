"""
LLM Provider

固定使用 Claude (Anthropic) 作為 LLM，sentence-transformers 作為 embedding。

Usage:
    from core.llm_provider import get_llm, get_embeddings

    llm = get_llm()
    embeddings = get_embeddings()
"""

import os
from functools import lru_cache


def get_llm(temperature: float = 0.3):
    """
    Return a cached Claude LLM instance.

    Model 預設 claude-3-5-sonnet-20241022，可透過 CLAUDE_CHAT_MODEL 覆寫。
    以 lru_cache 快取，避免每次 WebSocket 訊息都重建 HTTP client。
    """
    model = os.getenv("CLAUDE_CHAT_MODEL", "claude-sonnet-4-6")
    return _cached_llm(model, temperature)


@lru_cache(maxsize=8)
def _cached_llm(model: str, temperature: float):
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(model=model, temperature=temperature)


def get_embeddings():
    """
    Return a cached local sentence-transformers Embeddings instance.

    Model 預設 all-MiniLM-L6-v2，可透過 EMBEDDING_MODEL 覆寫。
    首次呼叫載入模型權重，後續呼叫回傳同一實例。
    """
    model = os.getenv(
        "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
    )
    return _cached_embeddings(model)


@lru_cache(maxsize=4)
def _cached_embeddings(model: str):
    from langchain_huggingface import HuggingFaceEmbeddings
    return HuggingFaceEmbeddings(model_name=model)
