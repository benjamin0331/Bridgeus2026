"""成就目錄：17 個成就的定義，與判定用的門檻常數。

放程式碼常數而不是資料庫，理由與 dialogue_topics.TOPIC_CONFIGS、
views.LEVEL_THRESHOLDS 一致：這是研究設計的一部分，不是使用者產生的內容，沒有
「線上新增一個成就」的需求，也就沒有理由付出 fixture 與 data migration 同步的
代價。UserAchievement 存 code 字串而非 FK，所以改中文文案不影響任何解鎖紀錄。

判定邏輯不在這裡，在 achievement_rules.py——這個檔案刻意只有資料，讓非工程背景
的人也能安全地改文案與門檻。
"""

from dataclasses import dataclass
from zoneinfo import ZoneInfo


# ═══════════════════════════════════════════════════════════
# 判定門檻
#
# 全部集中在這裡。指導教授要調數字時只改這一段，不必翻規則實作。
# ═══════════════════════════════════════════════════════════

# 「一天」以受試者所在時區為準。settings.TIME_ZONE 是 UTC，直接用 TruncDate
# 會把日界線切在台北時間早上 8 點——同一個晚上的兩場對話會被算成兩天
# （「常回來看看」多發），跨 08:00 的兩場又不算同一天（「今晚聊個夠」少發）。
# 受試者是台灣的大學生，日界線就該是他們的午夜。
ACHIEVEMENT_TIMEZONE = ZoneInfo("Asia/Taipei")

# |Δs| 達到多少算「立場有變化」。單位是 1–7 立場量表上的分數差
# （PostDialogueResponse.delta_s_value）。0.5 = 八題平均往同方向挪半格。
# ⚠️ 這個數字尚未經指導教授確認，是實作時給的預設值。
STANCE_CHANGE_THRESHOLD = 0.5

MULTI_CHANGE_COUNT = 3      # 換個角度：幾場有立場變化
SURVEY_PAIR_COUNT = 3       # 思辨旅程：幾組完整的前測＋後測
TALKATIVE_TURNS = 15        # 話匣子：單場對話裡自己的發言數
COMPLETE_FLOW_COUNT = 3     # 留到最後：幾場走完完整流程
SAME_DAY_COUNT = 2          # 今晚聊個夠：同一天完成幾場
VETERAN_COUNT = 10          # 百戰交流：累積幾場
RETURNING_DAYS = 5          # 常回來看看：幾個不重複的對話日
CLEAN_DIALOGUE_COUNT = 5    # 有話好說：幾場零攻擊性內容的對話


CATEGORY_TITLES = {
    "experience": "使用體驗",
    "stance": "立場變動",
    "engagement": "對話投入度",
    "quality": "對話品質",
    "longterm": "長期參與",
}

# 「一路同行」是 meta 成就：其他 16 個全滿才給。evaluate() 對它特別處理，
# 所以它的 code 要有個名字可以引用。
ALL_ACHIEVEMENTS_CODE = "all_achievements"


@dataclass(frozen=True)
class AchievementDef:
    """一個成就的顯示資料。

    title_name 對應 api.models.Title.name；解鎖時一併授予該頭銜（見
    achievement_rules._grant_titles）。None = 這個成就不給頭銜。
    """

    code: str
    category: str
    name: str
    how: str
    description: str
    title_name: str | None = None


# 順序 = 成就頁的顯示順序。ALL_ACHIEVEMENTS_CODE 必須排在最後。
CATALOG = (
    # ── 使用體驗 ──────────────────────────────────────────
    AchievementDef(
        code="first_login",
        category="experience",
        name="初來乍到",
        how="首次註冊並登入 TakeABridge",
        description="歡迎來到 TakeABridge，準備好來一場觀點與觀點之間的碰撞了嗎？",
        title_name="築橋新手",
    ),
    AchievementDef(
        code="first_hh_dialogue",
        category="experience",
        name="第一聲問候",
        how="首次完成真人聊天室對話",
        description="每一段交流，都從友善的一句「你好」開始",
        title_name="上橋新人",
    ),
    AchievementDef(
        code="first_ai_dialogue",
        category="experience",
        name="AI 初體驗",
        how="首次與 AI 完成完整對話",
        description="有時候，與 AI 對話也能帶來新的想法！",
        title_name="智橋行者",
    ),
    AchievementDef(
        code="first_godot_entry",
        category="experience",
        name="初入異次元",
        how="首次進入 Godot 世界",
        description="歡迎來到 Godot 世界，瞭解更多的觀點",
    ),
    AchievementDef(
        code="first_knowledge_base",
        category="experience",
        name="求知若渴",
        how="首次開啟觀點知識庫",
        description="每一個累積的觀點，都來自前人的貢獻",
    ),
    # ── 立場變動 ──────────────────────────────────────────
    AchievementDef(
        code="stance_changed_once",
        category="stance",
        name="一念之間",
        how="首次完成一場有立場變化的交流",
        description="改變與被説服不是妥協，而是願意重新思考自己的想法",
    ),
    AchievementDef(
        code="stance_held_once",
        category="stance",
        name="保持初心",
        how="首次完成一場立場維持一致的交流",
        description="經過思考後依然堅持，或許是對自己的觀點足夠堅定",
    ),
    AchievementDef(
        code="stance_changed_many",
        category="stance",
        name="換個角度",
        how="多次完成有立場變化的交流",
        description="一件事總有各種不同的看法，換個角度或許能看見更多",
    ),
    AchievementDef(
        code="survey_pairs",
        category="stance",
        name="思辨旅程",
        how="多次完成前測與後測",
        description="每一個回答，都在留下自己思考的痕跡",
    ),
    # ── 對話投入度 ────────────────────────────────────────
    AchievementDef(
        code="talkative",
        category="engagement",
        name="話匣子",
        how="首次完成高輪數對話",
        description="真正的交流，從來不是一句話就結束",
    ),
    AchievementDef(
        code="complete_flows",
        category="engagement",
        name="留到最後",
        how="多次完成完整聊天流程",
        description="願意陪伴一場場對話走到最後",
        title_name="長橋旅人",
    ),
    AchievementDef(
        code="same_day_dialogues",
        category="engagement",
        name="今晚聊個夠",
        how="一天完成多場交流",
        description="今晚，渴望瞭解更多觀點的心情根本停不下來",
        title_name="夜橋旅人",
    ),
    # ── 對話品質 ──────────────────────────────────────────
    AchievementDef(
        code="clean_dialogue_once",
        category="quality",
        name="理性交流",
        how="首次完成未偵測到攻擊性內容的交流",
        description="尊重，是展開良好對話的基本要素",
    ),
    AchievementDef(
        code="clean_dialogue_many",
        category="quality",
        name="有話好說",
        how="累積多場友善交流",
        description="不同立場，也能好好說話",
        title_name="溝通達人",
    ),
    # ── 長期參與 ──────────────────────────────────────────
    AchievementDef(
        code="returning_days",
        category="longterm",
        name="常回來看看",
        how="累積在多個不同日子完成對話",
        description="熟悉的身影，再次出現在 TakeABridge",
        title_name="橋上常客",
    ),
    AchievementDef(
        code="veteran_dialogues",
        category="longterm",
        name="百戰交流",
        how="累積完成指定場數對話",
        description="一句一句，誕生了無數想法",
        title_name="千橋旅人",
    ),
    AchievementDef(
        code=ALL_ACHIEVEMENTS_CODE,
        category="longterm",
        name="一路同行",
        how="解鎖其他全部成就",
        description="謝謝你，這麽支持我們的畢業專題 ;)",
    ),
)


# code → 定義。code 不是 FK，資料庫層沒有任何防呆，打錯字只會安靜寫進一列永遠
# 對不到目錄的孤兒紀錄；下游一律用這張表查，順便當成 code 拼寫的唯一真相。
CATALOG_BY_CODE = {d.code: d for d in CATALOG}
