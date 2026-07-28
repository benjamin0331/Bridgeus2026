"""議題問卷計分／立場分類設定與判定邏輯。

從 api/views.py 搬出來獨立成一個模組：這裡的函式原本定義在 HTTP view 層，卻被
apps/summary/pipeline（M6 觀點知識庫 pipeline）直接 import 使用，造成 pipeline
層反過來依賴 view 層的反向依賴、層次錯亂。這個模組不依賴 DRF/HTTP，純粹是
「議題設定 → 立場分類」的計算邏輯，view 層和 pipeline 層都可以安全地 import。
"""

from .dialogue_topics import get_dialogue_survey


def get_survey_scoring_config(topic_id: int) -> dict:
    survey_config = get_dialogue_survey(topic_id) or {}
    scale_config = survey_config.get("scale", {})
    stance_rules = survey_config.get("stance_rules", {})
    likert_questions = survey_config.get("questions", [])

    return {
        "scale_min": int(scale_config.get("min", 1)),
        "scale_max": int(scale_config.get("max", 7)),
        "reverse_question_ids": {
            str(question_id)
            for question_id in stance_rules.get("reverse_question_ids", [])
        },
        "support_threshold": float(stance_rules.get("support_threshold", 4.5)),
        "oppose_threshold": float(stance_rules.get("oppose_threshold", 3.5)),
        "neutral_score": float(
            (
                float(scale_config.get("min", 1))
                + float(scale_config.get("max", 7))
            )
            / 2
        ),
        "likert_question_ids": {
            str(question["id"]) for question in likert_questions
        },
        "open_question_mappings": [
            {
                "id": question["id"],
                "code": question["code"],
            }
            for question in survey_config.get("open_questions", [])
        ],
    }


def resolve_stance_category(*, topic_id: int, user_stance_score: float) -> str:
    scoring_config = get_survey_scoring_config(topic_id)

    if user_stance_score > scoring_config["support_threshold"]:
        return "support"
    if user_stance_score < scoring_config["oppose_threshold"]:
        return "oppose"
    return "neutral"
