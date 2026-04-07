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
from functools import lru_cache


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
    Return a cached LangChain-compatible Chat LLM instance.

    Switches provider based on LLM_PROVIDER env var.
    Instances are cached per (provider, model, temperature) so repeated calls
    — e.g. on every WebSocket message — do not rebuild the HTTP client.
    """
    provider = _get_provider()

    if provider == LLMProvider.GEMINI:
        model = os.getenv("GEMINI_CHAT_MODEL", "gemini-1.5-flash")
        return _cached_llm(provider.value, model, temperature)

    elif provider == LLMProvider.CLAUDE:
        model = os.getenv("CLAUDE_CHAT_MODEL", "claude-3-5-sonnet-20241022")
        return _cached_llm(provider.value, model, temperature)

    elif provider == LLMProvider.OPENAI:
        model = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o")
        return _cached_llm(provider.value, model, temperature)

    raise ValueError(f"Unhandled provider: {provider}")


@lru_cache(maxsize=16)
def _cached_llm(provider: str, model: str, temperature: float):
    """Inner cached factory — keyed by (provider, model, temperature)."""
    p = LLMProvider(provider)

    if p == LLMProvider.GEMINI:
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=model, temperature=temperature)

    if p == LLMProvider.CLAUDE:
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model, temperature=temperature)

    if p == LLMProvider.OPENAI:
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model, temperature=temperature)

    raise ValueError(f"Unhandled provider: {p}")


def get_embeddings():
    """
    Return a cached LangChain-compatible Embeddings instance.

    由 EMBEDDING_PROVIDER 環境變數獨立控制，與 LLM_PROVIDER 解耦。
    支援：local（sentence-transformers）、gemini、openai
    預設：local

    HuggingFaceEmbeddings 在首次呼叫時載入模型權重，後續呼叫直接回傳同一實例，
    避免每次 DialogueAgent.__init__() 都重新載入。
    """
    embedding_provider = os.getenv("EMBEDDING_PROVIDER", "local").lower()

    if embedding_provider == "local":
        model = os.getenv(
            "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
        )
        return _cached_embeddings(embedding_provider, model)

    elif embedding_provider == "gemini":
        model = os.getenv("GEMINI_EMBEDDING_MODEL", "models/gemini-embedding-001")
        return _cached_embeddings(embedding_provider, model)

    elif embedding_provider == "openai":
        model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
        return _cached_embeddings(embedding_provider, model)

    raise ValueError(
        f"Unsupported EMBEDDING_PROVIDER: '{embedding_provider}'. "
        f"Choose from: local, gemini, openai"
    )


@lru_cache(maxsize=8)
def _cached_embeddings(provider: str, model: str):
    """Inner cached factory — keyed by (provider, model)."""
    if provider == "local":
        from langchain_huggingface import HuggingFaceEmbeddings
        return HuggingFaceEmbeddings(model_name=model)

    if provider == "gemini":
        from langchain_google_genai import GoogleGenerativeAIEmbeddings
        return GoogleGenerativeAIEmbeddings(model=model)

    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings
        return OpenAIEmbeddings(model=model)

    raise ValueError(f"Unsupported EMBEDDING_PROVIDER: '{provider}'")
