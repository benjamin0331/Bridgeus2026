"""顯示設定的唯一讀取入口（覆寫層）。

TOPIC_CONFIGS / SURVEY_CONFIGS 是議題內容的真實來源；TopicDisplayOverride 與
PlatformDisplaySetting 只存 Supervisor 實際改過的值。所有「這個議題看不看得
到」「門檻是多少」「入口是混合還是分開」的判斷都要走這裡，不要直接讀
TOPIC_CONFIGS / SURVEY_CONFIGS——否則覆寫會有讀不到的死角。

見 docs/superpowers/specs/2026-07-27-supervisor-display-settings-and-mixed-entry-design.md §6
"""

from .dialogue_topics import TOPIC_CONFIGS, get_dialogue_survey, get_dialogue_topics
from .models import PlatformDisplaySetting, TopicDisplayOverride

# SURVEY_CONFIGS 沒有設 stance_rules 時的最終保底值，與 views._get_survey_scoring_config
# 原本寫死的 fallback 相同。
FALLBACK_SUPPORT_THRESHOLD = 4.5
FALLBACK_OPPOSE_THRESHOLD = 3.5


def default_stance_thresholds(*, topic_id: int) -> tuple[float, float]:
    """程式碼裡的預設門檻，不看任何覆寫。設定頁要顯示「預設是多少」時用。"""
    survey_config = get_dialogue_survey(topic_id) or {}
    stance_rules = survey_config.get("stance_rules", {})
    return (
        float(stance_rules.get("support_threshold", FALLBACK_SUPPORT_THRESHOLD)),
        float(stance_rules.get("oppose_threshold", FALLBACK_OPPOSE_THRESHOLD)),
    )


def get_stance_thresholds(*, topic_id: int) -> tuple[float, float]:
    """實際生效的門檻，回傳 (support, oppose)。"""
    support, oppose = default_stance_thresholds(topic_id=topic_id)

    override = TopicDisplayOverride.objects.filter(topic_id=topic_id).first()
    if override is not None:
        if override.support_threshold is not None:
            support = float(override.support_threshold)
        if override.oppose_threshold is not None:
            oppose = float(override.oppose_threshold)

    return support, oppose


def is_topic_visible(*, topic_id: int, is_researcher: bool) -> bool:
    if topic_id not in TOPIC_CONFIGS:
        return False

    override = TopicDisplayOverride.objects.filter(topic_id=topic_id).first()
    if override is None:
        return True

    return (
        override.visible_to_researcher if is_researcher
        else override.visible_to_participant
    )


def visible_topics(*, is_researcher: bool) -> list[dict]:
    """這個角色看得到的議題清單，維持 get_dialogue_topics() 的排序與欄位。"""
    overrides = {
        override.topic_id: override
        for override in TopicDisplayOverride.objects.all()
    }

    topics = []
    for topic in get_dialogue_topics():
        override = overrides.get(topic["id"])
        if override is not None:
            visible = (
                override.visible_to_researcher if is_researcher
                else override.visible_to_participant
            )
            if not visible:
                continue
        topics.append(topic)

    return topics


def get_entry_mode(*, is_researcher: bool) -> str:
    setting = PlatformDisplaySetting.load()
    return (
        setting.researcher_entry_mode if is_researcher
        else setting.participant_entry_mode
    )


def get_match_fallback_timeout_seconds() -> int:
    return PlatformDisplaySetting.load().match_fallback_timeout_minutes * 60
