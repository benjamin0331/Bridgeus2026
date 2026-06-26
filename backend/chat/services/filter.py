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

from asgiref.sync import sync_to_async

from chat.services._blacklist import BLACKLIST


def check_content_sync(text: str) -> dict:
    """Stage 1 only — keyword blacklist.

    Designed for the consumer's synchronous intercept path (must be < 50ms).

    Returns:
        is_blocked    bool
        reason        "blacklist" | None
        matched_word  str | None   — first blacklisted word found in text
    """
    if not text or not text.strip():
        return {"is_blocked": False, "reason": None, "matched_word": None}

    for word in BLACKLIST:
        if word in text:
            return {"is_blocked": True, "reason": "blacklist", "matched_word": word}

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
