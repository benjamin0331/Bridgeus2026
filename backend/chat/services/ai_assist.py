"""
AI 輔助介入服務。供三種觸發條件共用：

1. rephrase_message  — 情緒超標時，協助將發言重述為理性語氣
2. suggest_direction — 冷場時，提供新討論角度（觸發條件待定）
3. redirect_to_topic — 離題時，溫和引導回議題

所有 LLM 呼叫失敗時 fallback 到預寫模板。
"""

import logging
import os
import random

from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)

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


def _call_claude(prompt: str) -> str | None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as exc:
        logger.error("Claude API call failed in ai_assist: %s", exc)
        return None


def rephrase_message(original_text: str, topic: str) -> str:
    """
    Rephrase an emotionally charged message into a calmer version
    while preserving the original stance.

    Raises RuntimeError if the LLM is unavailable; callers must handle the fallback.
    """
    prompt = (
        f"用戶在討論「{topic}」時說了以下內容，"
        "請幫他用更理性平和的方式重述同樣的論點，"
        "保留原始立場但移除攻擊性或情緒化語氣。"
        "只回傳重述後的文字，不要加任何說明或前言。\n\n"
        f"原文：{original_text}"
    )
    result = _call_claude(prompt)
    if result:
        return result
    raise RuntimeError("rephrase_message: LLM unavailable or returned empty response")


def suggest_direction(conversation_id: int, topic: str) -> str:
    """
    Suggest a new discussion angle when the conversation stalls.
    """
    from chat.models import Message

    recent = list(
        Message.objects.filter(conversation_id=conversation_id)
        .order_by("-timestamp")[:6]
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


def redirect_to_topic(conversation_id: int, topic: str) -> str:
    """
    Generate a gentle prompt redirecting the conversation back to the topic.
    """
    from chat.models import Message

    recent = list(
        Message.objects.filter(conversation_id=conversation_id)
        .order_by("-timestamp")[:6]
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


arephrase_message = sync_to_async(rephrase_message, thread_sensitive=False)
asuggest_direction = sync_to_async(suggest_direction, thread_sensitive=False)
aredirect_to_topic = sync_to_async(redirect_to_topic, thread_sensitive=False)
