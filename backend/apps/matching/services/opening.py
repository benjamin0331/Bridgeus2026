"""AI 開場（opening brief）。

依「前測開放式問卷」（Q9 自身觀點、Q10 對立觀點理解）產生一段開場白，
外加 2–3 個具體、可以馬上往下談的討論方向。H-AI 與 H-H 共用這一份：

- H-AI：`build_ai_opening()`，只看該位使用者自己的 Q9/Q10，開場由 AI 代理人
  以「對話對手」的身分說出，會被寫進 session history 的第一則 agent 訊息。
- H-H：`build_match_opening()`，同時看兩位參與者的 Q9/Q10，產生**共用**的
  一張開場卡片。這裡刻意不引用任何一方的問卷原文、也不說明誰站哪一邊——
  參與者看到對方的前測自述會污染實驗（那是後測要比對的基線），所以 prompt
  與 fallback 都只輸出「面向」層級的敘述。

LLM 失敗、沒有 API key、或回傳格式不符時一律 fail-open 走 `_fallback_*`：
用 TOPIC_CONFIGS 的錨點（anchor_descriptions）跟問卷文字做關鍵詞比對，
挑出最相關的幾個面向。開場是入場體驗的一部分，不該因為 LLM 掛掉就擋住對話。
"""

from __future__ import annotations

import json
import logging
import os
import re

from asgiref.sync import sync_to_async

from api.dialogue_topics import TOPIC_CONFIGS

logger = logging.getLogger(__name__)

# 產生的方向數量。3 個是刻意的：太少不構成選擇、太多會變成清單而不是開場。
DIRECTION_COUNT = 3

_MAX_SURVEY_CHARS = 600
_LLM_TIMEOUT_SECONDS = 20.0


def _env_bool(name: str, default: bool = True) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def ai_opening_enabled() -> bool:
    """關掉時 `build_*` 直接回 None，前後端都當作「這場沒有開場」。"""
    return _env_bool("AI_OPENING_ENABLED", True)


# ── 題材：議題錨點 ───────────────────────────────────────────────────────


def _topic_meta(topic_id: int) -> dict:
    return TOPIC_CONFIGS.get(topic_id, {}) or {}


def _topic_title(topic_id: int) -> str:
    return _topic_meta(topic_id).get("title") or f"議題 {topic_id}"


def _topic_description(topic_id: int) -> str:
    return _topic_meta(topic_id).get("topic_description") or ""


def _anchor_catalog(topic_id: int) -> list[dict[str, str]]:
    meta = _topic_meta(topic_id)
    descriptions = meta.get("anchor_descriptions") or {}
    catalog = []
    for anchor in meta.get("anchors") or []:
        anchor_id = anchor.get("id", "")
        catalog.append(
            {
                "id": anchor_id,
                "name": anchor.get("name", ""),
                "detail": descriptions.get(anchor_id, ""),
            }
        )
    return catalog


def _bigrams(text: str) -> set[str]:
    """中文沒有詞界，用字元 bigram 當作廉價的相似度基底。

    整詞比對在這裡不夠用：anchor_descriptions 寫「核廢料處置」，受試者寫
    「核廢料沒地方放」，整詞比對會判定零命中。bigram 交集抓得到「核廢」
    「廢料」，對這種近義但不同詞的寫法穩健得多。
    """
    cleaned = re.sub(r"[\s、，,。;；：:！!？?（）()「」『』\-—_/]", "", text or "")
    return {cleaned[i : i + 2] for i in range(len(cleaned) - 1)}


def _rank_anchors(topic_id: int, texts: list[str]) -> list[dict[str, str]]:
    """依與問卷文字的字元 bigram 重疊度，把議題錨點排序（相關的在前）。"""
    corpus_grams = _bigrams("".join(text for text in texts if text))
    catalog = _anchor_catalog(topic_id)
    if not corpus_grams:
        return catalog

    def _score(anchor: dict[str, str]) -> int:
        hits = len(_bigrams(anchor["detail"]) & corpus_grams)
        # 錨點名稱被直接寫出來（「核廢處理」）比描述詞命中更有指示性。
        hits += 2 * len(_bigrams(anchor["name"]) & corpus_grams)
        return hits

    # 命中數相同時維持 TOPIC_CONFIGS 原順序（sorted 是穩定排序），
    # 讓完全沒有命中的作答也有可預期的預設方向。
    return sorted(catalog, key=_score, reverse=True)


# ── LLM ────────────────────────────────────────────────────────────────


def _clip(text: str | None, limit: int = _MAX_SURVEY_CHARS) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "…"


def _call_llm(prompt: str) -> str | None:
    try:
        from core.llm_provider import get_llm

        llm = get_llm(temperature=0.4)
        response = llm.invoke(prompt, timeout=_LLM_TIMEOUT_SECONDS)
    except Exception:
        logger.exception("AI opening LLM call failed; falling back.")
        return None

    content = getattr(response, "content", response)
    if isinstance(content, list):
        # LangChain 的 block 形式回應：[{"type": "text", "text": ...}, ...]
        content = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    text = str(content or "").strip()
    return text or None


def _parse_llm_payload(raw: str | None) -> dict | None:
    """LLM 被要求輸出 JSON，但實務上常包在 ``` 或前後說明裡，這裡容錯地抓。"""
    if not raw:
        return None

    candidate = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", candidate, re.DOTALL)
    if fenced:
        candidate = fenced.group(1).strip()
    else:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start != -1 and end > start:
            candidate = candidate[start : end + 1]

    try:
        data = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        logger.warning("AI opening: LLM payload was not valid JSON; falling back.")
        return None
    if not isinstance(data, dict):
        return None

    greeting = str(data.get("greeting") or "").strip()
    directions = []
    for item in data.get("directions") or []:
        if isinstance(item, dict):
            title = str(item.get("title") or "").strip()
            detail = str(item.get("detail") or "").strip()
        else:
            title = str(item).strip()
            detail = ""
        if title:
            directions.append({"title": title, "detail": detail})

    if not greeting or not directions:
        return None
    return {"greeting": greeting, "directions": directions[:DIRECTION_COUNT]}


def _compose(greeting: str, directions: list[dict[str, str]], source: str) -> dict:
    lines = [greeting.strip(), ""]
    for index, direction in enumerate(directions, 1):
        detail = direction.get("detail", "").strip()
        suffix = f"　{detail}" if detail else ""
        lines.append(f"{index}. {direction['title']}{suffix}")
    lines.append("")
    lines.append("想從哪一個開始都可以，也可以完全換一個你更在意的方向。")
    return {
        "text": "\n".join(lines).strip(),
        "directions": directions,
        "source": source,
    }


# ── H-AI ───────────────────────────────────────────────────────────────


_AI_PROMPT = """你是一個異質觀點對話平台的 AI 對話對手，即將和一位使用者討論公共議題。
請根據他在「對話前問卷」寫下的內容，寫一段開場。

議題：{topic_title}
議題說明：{topic_description}
使用者的立場傾向：{stance_label}
使用者對這個議題的自述（Q9）：
{q9}
使用者眼中「對立方最有力的論點」（Q10）：
{q10}
這個議題常見的討論面向（僅供參考，可不使用）：
{anchors}

要求：
1. greeting：2–3 句。用「你」稱呼對方，扣住他自述裡真正在意的點開場，語氣平和、不評價對錯，不要恭維也不要說教。不要重複貼上他的原文。
2. directions：{count} 個具體的討論方向。每個方向要能直接回答或深化他寫的內容，其中至少一個要碰到他自己提到的對立方論點。title 6–14 字；detail 一句話（30 字內），用問句或邀請句，讓他知道從這裡可以談什麼。
3. 不要在開場就提出你自己的主張或反駁，這只是開場。
4. 全程使用繁體中文。

只輸出 JSON，不要任何其他文字：
{{"greeting": "...", "directions": [{{"title": "...", "detail": "..."}}]}}"""


def _fallback_ai_opening(topic_id: int, q9: str, q10: str) -> dict:
    anchors = _rank_anchors(topic_id, [q9, q10])[:DIRECTION_COUNT]
    directions = [
        {
            "title": anchor["name"],
            "detail": f"例如：{anchor['detail'].rstrip('。')}",
        }
        for anchor in anchors
        if anchor["name"]
    ]
    if not directions:
        directions = [
            {"title": "你最主要的理由", "detail": "先把你問卷裡最在意的那一點展開來說"},
            {"title": "對立方的說法", "detail": "你認為對方最有力的論點，站得住腳嗎"},
            {"title": "什麼會讓你改變想法", "detail": "要有什麼樣的證據你才會重新考慮"},
        ]
    greeting = (
        f"我看過你在問卷裡對「{_topic_title(topic_id)}」的說明了。"
        "我們先不急著爭論誰對誰錯，從你在意的地方開始談。"
    )
    return _compose(greeting, directions[:DIRECTION_COUNT], "fallback")


def build_ai_opening(
    *,
    topic_id: int,
    q9: str,
    q10: str,
    stance_label: str = "",
) -> dict | None:
    """H-AI 開場。回傳 {text, directions, source}；功能關閉時回 None。"""
    if not ai_opening_enabled():
        return None

    q9_text = _clip(q9)
    q10_text = _clip(q10)
    if not q9_text and not q10_text:
        # 沒有任何開放式作答就沒有「依問卷提供方向」可言，退回議題預設面向。
        return _fallback_ai_opening(topic_id, "", "")

    anchors = _anchor_catalog(topic_id)
    prompt = _AI_PROMPT.format(
        topic_title=_topic_title(topic_id),
        topic_description=_topic_description(topic_id),
        stance_label=stance_label or "未標示",
        q9=q9_text or "（未填寫）",
        q10=q10_text or "（未填寫）",
        anchors="\n".join(f"- {a['name']}：{a['detail']}" for a in anchors) or "（無）",
        count=DIRECTION_COUNT,
    )
    parsed = _parse_llm_payload(_call_llm(prompt))
    if not parsed:
        return _fallback_ai_opening(topic_id, q9_text, q10_text)
    return _compose(parsed["greeting"], parsed["directions"], "llm")


# ── H-H ────────────────────────────────────────────────────────────────


_MATCH_PROMPT = """你是一個異質觀點對話平台的中立引導者。兩位立場不同的參與者即將就同一個議題對話。
以下是他們各自在「對話前問卷」寫的內容（他們彼此看不到，你也絕對不能透露或轉述任何一方寫的內容）。

議題：{topic_title}
議題說明：{topic_description}

參與者甲（立場傾向：{stance_a}）
自述：{q9_a}
他眼中對立方最有力的論點：{q10_a}

參與者乙（立場傾向：{stance_b}）
自述：{q9_b}
他眼中對立方最有力的論點：{q10_b}

這個議題常見的討論面向（僅供參考，可不使用）：
{anchors}

要求：
1. greeting：2–3 句，對「兩位」說話。說明這是一場立場不同的對話、目的是把彼此的理由講清楚而不是分輸贏。語氣中立，不偏袒任何一方。
2. directions：{count} 個雙方都寫到、或雙方明顯分歧的討論方向。每個 title 6–14 字；detail 一句話（30 字內），寫成兩人都能各自回答的開放式問題。
3. 嚴格禁止：引用或轉述任一方問卷的字句、指出誰支持誰反對、暗示哪一方比較有道理、稱呼「甲」「乙」。方向只描述議題面向本身。
4. 全程使用繁體中文。

只輸出 JSON，不要任何其他文字：
{{"greeting": "...", "directions": [{{"title": "...", "detail": "..."}}]}}"""


def _fallback_match_opening(topic_id: int, texts: list[str]) -> dict:
    anchors = _rank_anchors(topic_id, texts)[:DIRECTION_COUNT]
    directions = [
        {
            "title": anchor["name"],
            "detail": f"兩位對「{anchor['name']}」的判斷差在哪裡？",
        }
        for anchor in anchors
        if anchor["name"]
    ]
    if not directions:
        directions = [
            {"title": "各自最主要的理由", "detail": "先各自說明立場背後最關鍵的一點"},
            {"title": "最擔心的後果", "detail": "如果照對方的主張走，你最擔心什麼？"},
            {"title": "可能的共識", "detail": "有沒有哪一點其實你們都同意？"},
        ]
    greeting = (
        f"兩位好，這是一場關於「{_topic_title(topic_id)}」的異質觀點對話——"
        "你們的立場並不相同，這正是這場對話的用意。"
        "目標不是說服對方，而是把各自的理由講清楚、也聽懂對方的理由。"
    )
    return _compose(greeting, directions[:DIRECTION_COUNT], "fallback")


def build_match_opening(
    *,
    topic_id: int,
    participant_a: dict,
    participant_b: dict,
) -> dict | None:
    """H-H 開場。participant_* 需含 q9 / q10 / stance_label。

    回傳共用的 {text, directions, source}；功能關閉時回 None。
    """
    if not ai_opening_enabled():
        return None

    q9_a = _clip(participant_a.get("q9"))
    q10_a = _clip(participant_a.get("q10"))
    q9_b = _clip(participant_b.get("q9"))
    q10_b = _clip(participant_b.get("q10"))
    texts = [q9_a, q10_a, q9_b, q10_b]

    if not any(texts):
        return _fallback_match_opening(topic_id, [])

    anchors = _anchor_catalog(topic_id)
    prompt = _MATCH_PROMPT.format(
        topic_title=_topic_title(topic_id),
        topic_description=_topic_description(topic_id),
        stance_a=participant_a.get("stance_label") or "未標示",
        stance_b=participant_b.get("stance_label") or "未標示",
        q9_a=q9_a or "（未填寫）",
        q10_a=q10_a or "（未填寫）",
        q9_b=q9_b or "（未填寫）",
        q10_b=q10_b or "（未填寫）",
        anchors="\n".join(f"- {a['name']}：{a['detail']}" for a in anchors) or "（無）",
        count=DIRECTION_COUNT,
    )
    parsed = _parse_llm_payload(_call_llm(prompt))
    if not parsed:
        return _fallback_match_opening(topic_id, texts)
    return _compose(parsed["greeting"], parsed["directions"], "llm")


abuild_ai_opening = sync_to_async(build_ai_opening, thread_sensitive=False)
abuild_match_opening = sync_to_async(build_match_opening, thread_sensitive=False)
