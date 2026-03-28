"""
LLM Provider Abstraction Layer

透過環境變數 LLM_PROVIDER 切換不同 LLM 後端，
目前支援 gemini (測試階段免費)，未來擴充 claude / openai。

Usage:
    from core.llm_provider import get_llm, get_embeddings

    llm = get_llm()
    embeddings = get_embeddings()
"""

import os
from enum import Enum


class LLMProvider(Enum):
    GEMINI = "gemini"
    CLAUDE = "claude"
    OPENAI = "openai"


def _get_provider() -> LLMProvider:
    """Read LLM_PROVIDER from env, default to gemini for dev/testing."""
    raw = os.getenv("LLM_PROVIDER", "gemini").lower()
    try:
        return LLMProvider(raw)
    except ValueError:
        raise ValueError(
            f"Unsupported LLM_PROVIDER: '{raw}'. "
            f"Choose from: {[p.value for p in LLMProvider]}"
        )


def get_llm(temperature: float = 0.3):
    """
    Return a LangChain-compatible Chat LLM instance.

    Switches provider based on LLM_PROVIDER env var.
    """
    provider = _get_provider()

    if provider == LLMProvider.GEMINI:
        from langchain_google_genai import ChatGoogleGenerativeAI

        model = os.getenv("GEMINI_CHAT_MODEL", "gemini-1.5-flash")
        return ChatGoogleGenerativeAI(model=model, temperature=temperature)

    elif provider == LLMProvider.CLAUDE:
        # TODO: 正式開發階段切換至 Claude 3.5 Sonnet
        from langchain_anthropic import ChatAnthropic

        model = os.getenv("CLAUDE_CHAT_MODEL", "claude-3-5-sonnet-20241022")
        return ChatAnthropic(model=model, temperature=temperature)

    elif provider == LLMProvider.OPENAI:
        from langchain_openai import ChatOpenAI

        model = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o")
        return ChatOpenAI(model=model, temperature=temperature)

    raise ValueError(f"Unhandled provider: {provider}")


def get_embeddings():
    """
    Return a LangChain-compatible Embeddings instance.

    Note: Embedding model 不一定要跟 chat model 同 provider。
    目前統一跟隨 LLM_PROVIDER，未來可獨立設定。
    """
    provider = _get_provider()

    if provider == LLMProvider.GEMINI:
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        model = os.getenv("GEMINI_EMBEDDING_MODEL", "models/gemini-embedding-001")
        return GoogleGenerativeAIEmbeddings(model=model)

    elif provider == LLMProvider.CLAUDE:
        # Anthropic 沒有自己的 embedding model，用 OpenAI 或 HuggingFace
        # 正式版改用 Sentence-Transformers (local, 免費, 無 API 依賴)
        from langchain_huggingface import HuggingFaceEmbeddings

        model = os.getenv(
            "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
        )
        return HuggingFaceEmbeddings(model_name=model)

    elif provider == LLMProvider.OPENAI:
        from langchain_openai import OpenAIEmbeddings

        model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
        return OpenAIEmbeddings(model=model)

    raise ValueError(f"Unhandled provider: {provider}")
