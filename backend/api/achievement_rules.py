"""成就判定規則與評估入口。

**規則一律寫成「查現有資料的 predicate」，不維護任何計數欄位。** 代價是每次評估
要跑十幾個 query，好處是規則改了、門檻調了、或某次觸發點漏呼叫了，下一次評估
就會自動補發——不會出現「那天服務掛了所以我永遠拿不到那個成就」這種只能手動
補資料的狀況。

這也是 evaluate() 掛在 GET /api/achievements/me/ 上的原因：那條路徑是最終的安全
網（玩家只要打開成就頁就會結算），其他觸發點（送出後測、兌換 Godot 入場券）都
只是讓解鎖來得即時一點的優化，不是正確性的必要條件。

新增規則的作法：在 achievements.CATALOG 加一個 AchievementDef，然後在 RULES 加
一個同 code 的 predicate。兩邊不一致會被 tests_achievements.py 擋下來。
"""

from django.db.models import Count, Q
from django.db.models.functions import TruncDate

from .achievements import (
    ACHIEVEMENT_TIMEZONE,
    ALL_ACHIEVEMENTS_CODE,
    CATALOG,
    COMPLETE_FLOW_COUNT,
    MULTI_CHANGE_COUNT,
    RETURNING_DAYS,
    SAME_DAY_COUNT,
    STANCE_CHANGE_THRESHOLD,
    SURVEY_PAIR_COUNT,
    TALKATIVE_TURNS,
    VETERAN_COUNT,
)
from .models import (
    AIConversation,
    GodotEntryTicket,
    MatchMessage,
    PostDialogueResponse,
    UserAchievement,
)


# ═══════════════════════════════════════════════════════════
# 規則 predicate
#
# 每個都收一個 user、回傳 bool。不得有副作用、不得寫入。
# ═══════════════════════════════════════════════════════════

def _first_login(user) -> bool:
    """能被評估就代表已經登入過了——這個成就沒有額外條件。"""
    return True


def _first_hh_dialogue(user) -> bool:
    return PostDialogueResponse.objects.filter(
        user=user,
        experiment_condition=PostDialogueResponse.ExperimentCondition.HH,
    ).exists()


def _first_ai_dialogue(user) -> bool:
    return PostDialogueResponse.objects.filter(
        user=user,
        experiment_condition=PostDialogueResponse.ExperimentCondition.AI,
    ).exists()


def _changed_stance_count(user) -> int:
    """|Δs| 達門檻的場數。

    delta_s_value 為 NULL 代表「沒量到」（該場沒有前測 UserStanceProfile，見
    PostDialogueResponse.fill_stance_metrics），不是「沒有變化」——所以 NULL 既
    不算「有變化」也不算「維持一致」，兩邊都排除。

    門檻比較推進 SQL（而不是把整批 delta 撈進 Python 再 abs），這樣這個模組裡
    就沒有任何「把資料列載進記憶體再迴圈」的模式。
    """
    return PostDialogueResponse.objects.filter(
        Q(delta_s_value__gte=STANCE_CHANGE_THRESHOLD)
        | Q(delta_s_value__lte=-STANCE_CHANGE_THRESHOLD),
        user=user,
    ).count()


def _held_stance_count(user) -> int:
    """|Δs| 未達門檻的場數。NULL 一樣排除，理由見 _changed_stance_count。"""
    return PostDialogueResponse.objects.filter(
        user=user,
        delta_s_value__isnull=False,
        delta_s_value__gt=-STANCE_CHANGE_THRESHOLD,
        delta_s_value__lt=STANCE_CHANGE_THRESHOLD,
    ).count()


def _stance_changed_once(user) -> bool:
    return _changed_stance_count(user) >= 1


def _stance_held_once(user) -> bool:
    return _held_stance_count(user) >= 1


def _stance_changed_many(user) -> bool:
    return _changed_stance_count(user) >= MULTI_CHANGE_COUNT


def _survey_pairs(user) -> bool:
    """完成前測＋後測的組數是否達門檻。

    s_pre 非 NULL 就代表該場後測成功對上了一份前測快照——這正是「完成前測與
    後測」的定義，不必另外去 JOIN UserStanceProfile。
    """
    return (
        PostDialogueResponse.objects.filter(
            user=user, s_pre__isnull=False
        ).count()
        >= SURVEY_PAIR_COUNT
    )


def _talkative(user) -> bool:
    """單場對話裡自己的發言數達門檻。

    刻意用原始訊息數而不是 DialogueSessionRecord.substantive_turn_count／
    MatchInputGateStat.substantive_turn_count：那兩個欄位要等後測送出才被填，
    在那之前是 NULL，會讓這個成就的解鎖時機變得難以解釋。

    H-AI 與 H-H 分開查再取 or——兩邊的「一場對話」是不同的鍵（session_id vs
    match_id），沒有辦法合成一個 query，也不需要。

    排除空 session_id：那個欄位是 blank=True，空字串會把不相干的輪次收斂成同一
    組。目前寫入端都有給真值，這是防禦性的，順便把假設寫明。
    """
    ai_hit = (
        AIConversation.objects.filter(user=user)
        .exclude(session_id="")
        .values("session_id")
        .annotate(turns=Count("id"))
        .filter(turns__gte=TALKATIVE_TURNS)
        .exists()
    )
    if ai_hit:
        return True
    return (
        MatchMessage.objects.filter(sender=user)
        .values("match_id")
        .annotate(turns=Count("id"))
        .filter(turns__gte=TALKATIVE_TURNS)
        .exists()
    )


def _completed_dialogue_count(user) -> int:
    """完成的對話場數。

    定義與 views.LEVEL_THRESHOLDS 的「完成」完全一致（送出後測 = 一筆
    PostDialogueResponse），刻意不另立一套——成就頁與等級卡就在同一個畫面上，
    兩個數字對不起來會被當成 bug。
    """
    return PostDialogueResponse.objects.filter(user=user).count()


def _complete_flows(user) -> bool:
    return _completed_dialogue_count(user) >= COMPLETE_FLOW_COUNT


def _veteran_dialogues(user) -> bool:
    return _completed_dialogue_count(user) >= VETERAN_COUNT


def _same_day_dialogues(user) -> bool:
    """同一天完成的場數是否達門檻。

    日界線用 ACHIEVEMENT_TIMEZONE 而不是 settings.TIME_ZONE（UTC）——見那個常數
    的註解。少了 tzinfo，同一個晚上跨午夜的兩場會被判成不同天。
    """
    return (
        PostDialogueResponse.objects.filter(user=user)
        .annotate(day=TruncDate("created_at", tzinfo=ACHIEVEMENT_TIMEZONE))
        .values("day")
        .annotate(n=Count("id"))
        .filter(n__gte=SAME_DAY_COUNT)
        .exists()
    )


def _returning_days(user) -> bool:
    """在幾個不重複的日子完成過對話是否達門檻。

    原文案是「累積使用平台指定天數」，改成這個判準是刻意的：專案沒有每日活躍
    紀錄（User.last_login 是覆寫式的，算不出累積天數），而為了一個成就新增一張
    會持續長大的活躍表並不划算。「有幾天真的來對話過」對這個平台來說也是更有
    意義的指標。

    日界線同 _same_day_dialogues，用 ACHIEVEMENT_TIMEZONE。這一條對時區特別敏感：
    少了 tzinfo 會把同一天跨台北時間 08:00 的兩場算成兩天，也就是**多發**成就。
    """
    return (
        PostDialogueResponse.objects.filter(user=user)
        .annotate(day=TruncDate("created_at", tzinfo=ACHIEVEMENT_TIMEZONE))
        .values("day")
        .distinct()
        .count()
        >= RETURNING_DAYS
    )


def _first_godot_entry(user) -> bool:
    """兌換過入場券 = 真的進過 Godot 世界。

    ⚠️ 本機／桌面測試永遠解不開這一個。入場券只有在帶著 GODOT_SERVICE_TOKEN 的
    headless dedicated server 上才會被兌換（見 godot/CLAUDE.md 的 spawn flow）；
    桌面 host 沒有服務金鑰，peer 是無身份 spawn 的，這裡不會留下任何紀錄。
    這是預期行為，不是 bug——不要為了讓本機測起來方便而改判準。
    """
    return GodotEntryTicket.objects.filter(
        user=user, redeemed_at__isnull=False
    ).exists()


RULES = {
    "first_login": _first_login,
    "first_hh_dialogue": _first_hh_dialogue,
    "first_ai_dialogue": _first_ai_dialogue,
    "stance_changed_once": _stance_changed_once,
    "stance_held_once": _stance_held_once,
    "stance_changed_many": _stance_changed_many,
    "survey_pairs": _survey_pairs,
    "talkative": _talkative,
    "complete_flows": _complete_flows,
    "same_day_dialogues": _same_day_dialogues,
    "returning_days": _returning_days,
    "veteran_dialogues": _veteran_dialogues,
    "first_godot_entry": _first_godot_entry,
}


# ═══════════════════════════════════════════════════════════
# 評估入口
# ═══════════════════════════════════════════════════════════

def evaluate(user) -> list[str]:
    """結算這位使用者的所有成就，回傳這次新解鎖的 code 清單。

    回傳值只供呼叫端記 log 或除錯用。**畫面上的「解鎖通知」不看它**，看的是
    UserAchievement.notified_at（見 views.AchievementMeView）——兩個併發請求
    可能都算出同一個「新解鎖」，但 unique constraint 保證只有一列真的被建立，
    所以以資料庫為準才不會跳兩次通知。
    """
    unlocked = set(
        UserAchievement.objects.filter(user=user).values_list("code", flat=True)
    )
    newly: list[str] = []

    for definition in CATALOG:
        if definition.code == ALL_ACHIEVEMENTS_CODE:
            continue          # meta 成就要等其他全部算完，見下方
        if definition.code in unlocked:
            continue
        # 查不到規則 = 這個成就的判定還沒實作（分期上線，見 tests_achievements.py
        # 的 PENDING_RULES），不是打錯字。守門測試會確保兩邊不會默默漂移。
        rule = RULES.get(definition.code)
        if rule is None or not rule(user):
            continue
        unlocked.add(definition.code)
        newly.append(definition.code)

    # 「一路同行」：其他全部解鎖後自動獲得。放在迴圈之後才判定，這樣「最後一個
    # 成就」與「一路同行」會在同一次評估裡一起解鎖，玩家不必再打開一次成就頁。
    if ALL_ACHIEVEMENTS_CODE not in unlocked:
        others = {d.code for d in CATALOG if d.code != ALL_ACHIEVEMENTS_CODE}
        if others <= unlocked:
            unlocked.add(ALL_ACHIEVEMENTS_CODE)
            newly.append(ALL_ACHIEVEMENTS_CODE)

    if newly:
        _persist(user, newly)
    return newly


def _persist(user, codes: list[str]) -> None:
    """把新解鎖寫進 UserAchievement。

    ignore_conflicts=True：兩個併發請求可能同時算出同一組新解鎖，unique
    constraint 會擋掉後到的那一列，這裡不該因此丟 500。
    """
    UserAchievement.objects.bulk_create(
        [UserAchievement(user=user, code=code) for code in codes],
        ignore_conflicts=True,
    )
