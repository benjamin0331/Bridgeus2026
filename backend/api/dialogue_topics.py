TOPIC_CONFIGS = {
    102: {
        "title": "台灣核能議題討論",
        "topic_description": "台灣是否應重啟核電廠以應對能源轉型與減碳需求",
        "collection_name": "nuclear_energy_all",
        "date": "2026/04/27",
        "display_order": 1,
    },
}

SURVEY_CONFIGS = {
    102: {
        "topic_id": 102,
        "title": "立場檢測問卷",
        "subtitle": "第一部分會計算核能立場分數；第二部分先保留開放式觀點接口。",
        "scale": {
            "min": 1,
            "max": 7,
            "min_label": "非常不同意",
            "max_label": "非常同意",
        },
        "stance_rules": {
            "reverse_question_ids": [2, 4, 5, 6],
            "support_threshold": 4.5,
            "oppose_threshold": 3.5,
            "neutral_min": 3.5,
            "neutral_max": 4.5,
        },
        "questions": [
            {
                "id": 1,
                "code": "Q1",
                "tag": "安全",
                "question_type": "likert",
                "dimension": "安全",
                "direction": "positive",
                "reverse_scored": False,
                "min_value": 1,
                "max_value": 7,
                "text": "我認為台灣現有的核電技術與管理能力，足以確保核電廠的安全運轉。",
            },
            {
                "id": 2,
                "code": "Q2",
                "tag": "環境",
                "question_type": "likert",
                "dimension": "環境",
                "direction": "reverse",
                "reverse_scored": True,
                "min_value": 1,
                "max_value": 7,
                "text": "核廢料的長期處置風險，使核電不應被視為環保的能源選項。",
            },
            {
                "id": 3,
                "code": "Q3",
                "tag": "經濟",
                "question_type": "likert",
                "dimension": "經濟",
                "direction": "positive",
                "reverse_scored": False,
                "min_value": 1,
                "max_value": 7,
                "text": "與其他能源相比，核電在發電成本與供電穩定性上具有明顯優勢。",
            },
            {
                "id": 4,
                "code": "Q4",
                "tag": "替代方案",
                "question_type": "likert",
                "dimension": "替代方案",
                "direction": "reverse",
                "reverse_scored": True,
                "min_value": 1,
                "max_value": 7,
                "text": "台灣應優先發展再生能源，而非依賴核電來達成淨零碳排目標。",
            },
            {
                "id": 5,
                "code": "Q5",
                "tag": "安全",
                "question_type": "likert",
                "dimension": "安全",
                "direction": "reverse",
                "reverse_scored": True,
                "min_value": 1,
                "max_value": 7,
                "text": "考量台灣的天然災害，核電廠的存在對周邊居民構成不可接受的威脅。",
            },
            {
                "id": 6,
                "code": "Q6",
                "tag": "經濟",
                "question_type": "likert",
                "dimension": "經濟",
                "direction": "reverse",
                "reverse_scored": True,
                "min_value": 1,
                "max_value": 7,
                "text": "核電廠的建設、維護與除役成本被嚴重低估，實際上並不划算。",
            },
            {
                "id": 7,
                "code": "Q7",
                "tag": "環境",
                "question_type": "likert",
                "dimension": "環境",
                "direction": "positive",
                "reverse_scored": False,
                "min_value": 1,
                "max_value": 7,
                "text": "核電是目前能大規模穩定供電的低碳能源中，最務實可行的選項。",
            },
            {
                "id": 8,
                "code": "Q8",
                "tag": "替代方案",
                "question_type": "likert",
                "dimension": "替代方案",
                "direction": "positive",
                "reverse_scored": False,
                "min_value": 1,
                "max_value": 7,
                "text": "在再生能源尚無法滿足基載電力需求的過渡期，核電是必要的橋接方案。",
            },
        ],
        "open_questions": [
            {
                "id": 9,
                "code": "Q9",
                "tag": "核心觀點",
                "question_type": "open_text",
                "text": "請用 3–5 句話說明你對「台灣是否應該使用核電」的看法，以及你最主要的理由。",
                "placeholder": "請用 3–5 句話描述你的觀點與理由",
                "min_sentences": 3,
                "max_sentences": 5,
            },
            {
                "id": 10,
                "code": "Q10",
                "tag": "對立觀點理解",
                "question_type": "open_text",
                "text": "你認為反對（或支持）核電的人，他們最有力的論點是什麼？請試著用他們的角度來陳述。",
                "placeholder": "試著站在對立方角度描述他們最強的論點",
                "min_sentences": 2,
                "max_sentences": 5,
            },
        ],
        "semantic_vector_interface": {
            "enabled": False,
            "status": "pending",
            "target_question_code": "Q9",
            "model_name": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        },
    },
}


def get_dialogue_topics() -> list[dict[str, str | int]]:
    topics = []

    for topic_id, config in sorted(
        TOPIC_CONFIGS.items(),
        key=lambda item: item[1].get("display_order", item[0]),
    ):
        topics.append(
            {
                "id": topic_id,
                "title": config["title"],
                "description": config["topic_description"],
                "date": config["date"],
            }
        )

    return topics


def get_dialogue_survey(topic_id: int) -> dict | None:
    return SURVEY_CONFIGS.get(topic_id)
