"""
LLM Provider

單一切換點：`LLM_PROVIDER` 環境變數決定整個系統用哪家的模型。

    LLM_PROVIDER=claude   # 預設，現行版本
    LLM_PROVIDER=openai   # 備案（Anthropic 出狀況時改這個並重啟）

刻意**不做**自動 failover：一場對話中途從 Claude 換成 GPT，等於實驗操作
變了一半，資料事後無法解讀。切換是人為決定，改 env 重啟即可（幾十秒），
而且每一輪實際用了誰會記進 AIConversation.llm_provider / llm_model。

Embedding 不跟著切——立場向量、CCND 距離、drift 全都建立在同一個
sentence-transformers 模型的向量空間上，換掉會讓新舊資料不可比較。

Usage:
    from core.llm_provider import get_llm, get_embeddings, active_provider

    llm = get_llm()
    embeddings = get_embeddings()
"""

import os
from functools import lru_cache

CLAUDE = "claude"
OPENAI = "openai"

_SUPPORTED_PROVIDERS = (CLAUDE, OPENAI)

DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-6"
DEFAULT_OPENAI_MODEL = "gpt-4o"


def active_provider() -> str:
    """目前生效的 provider。無法辨識的值一律當成 claude。

    刻意不拋例外：演示現場把 env 打錯字時，回到現行版本比整個服務起不來好。
    """
    raw = (os.getenv("LLM_PROVIDER") or CLAUDE).strip().lower()
    return raw if raw in _SUPPORTED_PROVIDERS else CLAUDE


def active_model_name() -> str:
    """目前 provider 實際會用的模型名，供落庫與 log 標註。"""
    if active_provider() == OPENAI:
        return os.getenv("OPENAI_CHAT_MODEL", DEFAULT_OPENAI_MODEL)
    return os.getenv("CLAUDE_CHAT_MODEL", DEFAULT_CLAUDE_MODEL)


def chat_max_tokens() -> int:
    """對話回覆的輸出上限。兩家分開設，因為 token 成本與模型上限不同。"""
    if active_provider() == OPENAI:
        return int(os.getenv("OPENAI_CHAT_MAX_TOKENS", "1536"))
    return int(os.getenv("CLAUDE_CHAT_MAX_TOKENS", "1536"))


def get_llm(temperature: float = 0.3, max_tokens: int | None = None):
    """依 LLM_PROVIDER 回傳對應的 LangChain chat model（已快取）。

    以 lru_cache 快取，避免每次 WebSocket 訊息都重建 HTTP client。
    """
    return _cached_llm(active_provider(), active_model_name(), temperature, max_tokens)


@lru_cache(maxsize=16)
def _cached_llm(provider: str, model: str, temperature: float, max_tokens: int | None):
    if provider == OPENAI:
        from langchain_openai import ChatOpenAI

        kwargs = {"model": model, "temperature": temperature}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        return ChatOpenAI(**kwargs)

    from langchain_anthropic import ChatAnthropic

    kwargs = {"model": model, "temperature": temperature}
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    return ChatAnthropic(**kwargs)


def get_embeddings():
    """
    Return a cached local sentence-transformers Embeddings instance.

    Model 預設 all-MiniLM-L6-v2，可透過 EMBEDDING_MODEL 覆寫。
    首次呼叫載入模型權重，後續呼叫回傳同一實例。

    不受 LLM_PROVIDER 影響——見模組 docstring。
    """
    model = os.getenv(
        "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
    )
    return _cached_embeddings(model)


@lru_cache(maxsize=4)
def _cached_embeddings(model: str):
    from langchain_huggingface import HuggingFaceEmbeddings
    return HuggingFaceEmbeddings(model_name=model)
