"""
Emotion intensity detection for H-H dialogue monitoring.

Model  : lxyuan/distilbert-base-multilingual-cased-sentiments-student
         multilingual DistilBERT fine-tuned on sentiment (~500 MB, supports zh)
Outputs: positive / neutral / negative class probabilities.

Intensity score = negative-class probability.
In a depolarisation debate, hostile / angry messages are the primary concern.
Score >= EMOTION_THRESHOLD triggers a calming system prompt toward the sender.
"""

import threading

from asgiref.sync import sync_to_async

EMOTION_THRESHOLD: float = 0.65

_MODEL_NAME = "lxyuan/distilbert-base-multilingual-cased-sentiments-student"
_pipeline = None
_lock = threading.Lock()


def _get_pipeline():
    global _pipeline
    if _pipeline is None:
        with _lock:
            if _pipeline is None:
                from transformers import pipeline as hf_pipeline

                _pipeline = hf_pipeline(
                    "text-classification",
                    model=_MODEL_NAME,
                    top_k=None,
                    truncation=True,
                    max_length=512,
                )
    return _pipeline


def analyze_emotion(text: str) -> dict:
    """Analyse emotional intensity of a single text string.

    Returns a dict with three keys:
        score             float  0-1; negative-sentiment probability
        label             str    dominant class: positive / neutral / negative
        is_over_threshold bool   True when score >= EMOTION_THRESHOLD
    """
    if not text or not text.strip():
        return {"score": 0.0, "label": "neutral", "is_over_threshold": False}

    # pipe(str) returns [[{label, score}, ...]] — index [0] to unwrap batch dim
    raw: list[dict] = _get_pipeline()(text.strip())[0]
    class_scores = {r["label"].lower(): r["score"] for r in raw}

    negative_score = class_scores.get("negative", 0.0)
    dominant = max(class_scores, key=class_scores.get)

    return {
        "score": round(float(negative_score), 4),
        "label": dominant,
        "is_over_threshold": negative_score >= EMOTION_THRESHOLD,
    }


# Async wrapper — thread_sensitive=False: inference runs in the thread pool,
# not the main event loop thread
aget_analyze_emotion = sync_to_async(analyze_emotion, thread_sensitive=False)
