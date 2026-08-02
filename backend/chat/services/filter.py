"""
H-H 對話內容過濾（兩階段）。

Stage 1（同步，< 50ms）
    關鍵字黑名單比對 — 架構中唯一的同步阻擋機制。
    命中 → 不轉發，要求發言者重新表達。

Stage 2（非同步，TODO）
    輕量分類器，捕捉未列入黑名單的攻擊性內容。
    候選模型：thu-coai/roberta-base-cold（中文冒犯性語言，COLD dataset）
    暫不實作，原因：額外 ~500 MB 模型對 2-4 GB VPS 負擔過重，
    且情緒偵測（emotion.py）已提供第二道語意防線；
    待真實對話數據累積後再決定是否引入。
"""

import re

from asgiref.sync import sync_to_async

from chat.services._blacklist import BLACKLIST, STANDALONE_PROFANITY

# 判斷「整則訊息只有粗口」時要先剝掉的雜訊：空白與標點。
# 「幹」「幹幹幹」「幹！！！」「幹 幹 幹」「幹。」都必須收斂成同一件事。
_NOISE_RE = re.compile(r"[\s　!-/:-@\[-`{-~。．，、！？；：（）「」『』《》〈〉…～·]+")


def find_standalone_profanity(text: str) -> str | None:
    """整則訊息只由單字粗口（含重複）構成時，回傳命中的第一個字。

    這是 BLACKLIST 子字串比對做不到的判斷。「幹」不能進 BLACKLIST，
    否則「幹嘛」「樹幹」全部誤判；但整則只有「幹」或「幹幹幹」時，
    不存在良性解讀。判準是上下文，不是字本身。

    有其他實質內容時一律不命中——「幹，核電根本是騙局」是帶情緒的
    論述，該由 BLACKLIST 與情緒偵測處理，不歸這裡管。
    """
    if not text:
        return None

    stripped = _NOISE_RE.sub("", text)
    if not stripped:
        return None
    if not all(char in STANDALONE_PROFANITY for char in stripped):
        return None
    return stripped[0]


def check_content_sync(text: str) -> dict:
    """Stage 1 only — keyword blacklist + 單字粗口。

    Designed for the consumer's synchronous intercept path (must be < 50ms).

    Returns:
        is_blocked    bool
        reason        "blacklist" | "standalone_profanity" | None
        matched_word  str | None   — first blacklisted word found in text
    """
    if not text or not text.strip():
        return {"is_blocked": False, "reason": None, "matched_word": None}

    for word in BLACKLIST:
        if word in text:
            return {"is_blocked": True, "reason": "blacklist", "matched_word": word}

    matched = find_standalone_profanity(text)
    if matched is not None:
        return {
            "is_blocked": True,
            "reason": "standalone_profanity",
            "matched_word": matched,
        }

    return {"is_blocked": False, "reason": None, "matched_word": None}


def check_content(text: str) -> dict:
    """Stage 1 + Stage 2.  For non-real-time or background analysis.

    Currently identical to check_content_sync; Stage 2 stub is below.
    """
    result = check_content_sync(text)
    if result["is_blocked"]:
        return result

    # TODO Stage 2 — 輕量中文攻擊性語言分類器
    #
    # 實作建議：
    #   model = pipeline("text-classification",
    #                     model="thu-coai/roberta-base-cold", ...)
    #   pred  = model(text)[0]  # {"label": "offensive"/"non-offensive", "score": ...}
    #   if pred["label"] == "offensive" and pred["score"] >= 0.85:
    #       return {"is_blocked": True, "reason": "classifier", "matched_word": None}
    #
    # 失敗時：降級為 Stage 1 結果（不阻擋），並 log warning。
    # 不應讓分類器失敗中斷訊息轉發。

    return result


# Async wrapper — Stage 2 will be I/O-bound; keep thread_sensitive=False
acheck_content = sync_to_async(check_content, thread_sensitive=False)
