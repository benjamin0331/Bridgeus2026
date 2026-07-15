"""AI assistance helpers for human-to-human matching rooms.

They operate on the current matching-room models (``api.DialogueMatch`` /
``api.MatchMessage``) via callers in ``api.consumers``.
"""

from __future__ import annotations

import logging
import os
import random

from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)

REPHRASE_FALLBACK = "你的發言可能帶有較強烈的情緒，建議修改後再發送。"

_DIRECTION_FALLBACKS = [
    "可以試著從對方的角度思考：他們最核心的顧慮是什麼？",
    "換個切入點：這個議題對不同世代的人來說意義是否相同？",
    "試著問對方：如果你的擔憂都被解決了，你還有其他考量嗎？",
]

_REDIRECT_FALLBACKS = [
    "回到主題聊聊吧，對方在等你對議題的看法。",
    "目前的討論似乎偏離了主題，可以試著回到核心議題的討論。",
    "這個方向有點遠離議題了，試著把焦點拉回來？",
]


def _env_bool(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def hh_ai_assist_enabled() -> bool:
    return _env_bool("H_H_AI_ASSIST_ENABLED", False)


def _call_claude(prompt: str, *, max_tokens: int = 256) -> str | None:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=os.getenv("CLAUDE_CHAT_MODEL", "claude-sonnet-4-6"),
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception:
        logger.exception("Claude API call failed in H-H AI assist.")
        return None


def rephrase_match_message(original_text: str, topic: str) -> tuple[str, bool]:
    prompt = (
        f"用戶在討論「{topic}」時說了以下內容，"
        "請幫他用更理性平和的方式重述同樣的論點，"
        "保留原始立場但移除攻擊性或情緒化語氣。"
        "只回傳重述後的文字，不要加任何說明或前言。\n\n"
        f"原文：{original_text}"
    )
    result = _call_claude(prompt)
    if result:
        return result, True
    return REPHRASE_FALLBACK, False


def suggest_match_direction(match_id: int, topic: str) -> str:
    from api.models import MatchMessage

    recent = list(
        MatchMessage.objects.filter(match_id=match_id).order_by("-created_at")[:6]
    )
    transcript = (
        "\n".join(f"User {m.sender_id}: {m.content}" for m in reversed(recent))
        if recent
        else "(尚無對話紀錄)"
    )
    prompt = (
        f"以下是關於「{topic}」的對話紀錄，目前對話停滯，"
        "請提供一個新的討論角度或問題，引導雙方深入交流。"
        "只回傳建議文字，不要加任何說明。\n\n"
        f"{transcript}"
    )
    result = _call_claude(prompt)
    return result if result else random.choice(_DIRECTION_FALLBACKS)


def redirect_match_to_topic(match_id: int, topic: str) -> str:
    from api.models import MatchMessage

    recent = list(
        MatchMessage.objects.filter(match_id=match_id).order_by("-created_at")[:6]
    )
    transcript = (
        "\n".join(f"User {m.sender_id}: {m.content}" for m in reversed(recent))
        if recent
        else "(尚無對話紀錄)"
    )
    prompt = (
        f"以下是關於「{topic}」的對話紀錄，對話似乎偏離主題，"
        "請生成一則提示，溫和地引導討論回到議題。"
        "只回傳提示文字，不要加任何說明。\n\n"
        f"{transcript}"
    )
    result = _call_claude(prompt)
    return result if result else random.choice(_REDIRECT_FALLBACKS)


arephrase_match_message = sync_to_async(rephrase_match_message, thread_sensitive=False)
asuggest_match_direction = sync_to_async(suggest_match_direction, thread_sensitive=False)
aredirect_match_to_topic = sync_to_async(redirect_match_to_topic, thread_sensitive=False)
