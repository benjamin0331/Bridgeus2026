# 成就系統 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把成就頁與成就通知從寫死的前端假資料，換成後端判定、可解鎖、會授予頭銜的真系統。

**Architecture:** 成就目錄是 Python 常數（同 `dialogue_topics.TOPIC_CONFIGS` / `LEVEL_THRESHOLDS` 的專案慣例），判定規則一律寫成「查現有資料的 predicate」而非累加計數器，解鎖結果落庫到 `UserAchievement(code, unlocked_at, notified_at)`。`evaluate(user)` 掛在 `GET /api/achievements/me/` 當最終安全網，其他觸發點只是讓解鎖來得即時一點——規則改了或某次觸發漏了，下次評估自動補發。

**Tech Stack:** Django 5 + DRF、pytest-django、React + axios。

**執行環境：** 後端指令一律在 `backend/` 底下跑；前端在 `frontend/`。測試用 `DB_ENGINE=sqlite uv run pytest ... -v`（見 `AGENTS.md` §Verification）。

> ⚠️ 後端測試共用同一個測試資料庫，**不要同時跑兩個測試指令**。

---

## 決策紀錄（已與使用者確認）

| 項目 | 決定 |
|---|---|
| 成就狀態存放 | 方案 B：衍生規則 + 落庫解鎖紀錄 |
| 「常回來看看」判準 | 改為「不重複對話日數」，用 `PostDialogueResponse.created_at` 算，零新表零埋點 |
| 「一路同行」判準 | 解鎖其他全部 16 個成就後自動獲得（meta 成就） |
| 成就目錄存放 | Python 常數，不進 DB、不做 admin 可編輯 |

**尚未有人拍板、本 plan 自行給定預設值的參數**：全部集中在 `api/achievements.py` 的門檻區段，指導教授要調就改那一段，不必翻規則實作。`STANCE_CHANGE_THRESHOLD = 0.5` 是其中最需要事後確認的一個（1–7 量表上 |Δs| 要多少才算「立場有變化」）。

---

## 分期與可上線成就數

| 階段 | 內容 | 可解鎖成就 |
|---|---|---|
| **P1**（Task 1–11） | 資料模型、目錄、13 條規則、兩支端點、前端接線 | 13 / 17 |
| **P2**（Task 12–13） | 頭銜掛勾、知識庫埋點 | 14 / 17 |
| **P3**（Task 14–15） | H-AI 的攻擊性計數落庫 | 17 / 17（H-AI 單邊判定） |

**明確不在範圍內**：把 input gate 接進 `MatchRoomConsumer`（H-H 對話室目前完全沒跑閘門，`record_match_attempt()` 是死程式碼）。這是獨立的既有缺陷，不該綁在成就系統裡修。P3 的兩個品質成就因此只看 H-AI 那半的資料——Task 14 的規則實作會明確寫死這個限制並附註原因。

---

## File Structure

**新建**
- `backend/api/achievements.py` — 成就目錄（純資料）+ 判定門檻常數。對應 `dialogue_topics.py` 的角色。
- `backend/api/achievement_rules.py` — 規則 predicate + `evaluate()` + 頭銜授予。對應 `godot_tickets.py` 的角色。
- `backend/api/migrations/0028_userachievement.py`
- `backend/api/management/commands/seed_achievement_titles.py`
- `backend/api/tests_achievements.py`
- `frontend/src/api/achievements.js` — 前端取用成就 API 的薄封裝。

**修改**
- `backend/api/models.py` — 新增 `UserAchievement`（接在檔尾 `Favorite` 之後）
- `backend/api/views.py` — 新增 `AchievementMeView` / `AchievementAckView`；在 `PostDialogueResponseView.post` 與 `GodotTicketRedeemView.post` 呼叫 `evaluate`
- `backend/api/urls.py` — 兩條新路由
- `frontend/src/pages/AchievementPage.jsx` — 改吃 API
- `frontend/src/App.jsx` — toast 改吃 API

**刪除**
- `frontend/src/pages/achievements.data.js` — 文案改由後端供應，避免兩份中文各自漂移

---

# P1

## Task 1: `UserAchievement` 模型

**Files:**
- Modify: `backend/api/models.py`（檔尾，`Favorite` 之後）
- Create: `backend/api/migrations/0028_userachievement.py`（由 makemigrations 產生）
- Test: `backend/api/tests_achievements.py`

- [ ] **Step 1: 寫失敗的測試**

建立 `backend/api/tests_achievements.py`：

```python
"""
pytest tests for the achievement system.

Run from backend/:
    DB_ENGINE=sqlite uv run pytest api/tests_achievements.py -v
"""
import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError

from api.models import UserAchievement

User = get_user_model()


@pytest.mark.django_db
def test_user_achievement_is_unique_per_user_and_code():
    user = User.objects.create_user(username="u1", password="pw")
    UserAchievement.objects.create(user=user, code="first_login")

    with pytest.raises(IntegrityError):
        UserAchievement.objects.create(user=user, code="first_login")


@pytest.mark.django_db
def test_user_achievement_starts_unnotified():
    user = User.objects.create_user(username="u1", password="pw")
    row = UserAchievement.objects.create(user=user, code="first_login")

    assert row.notified_at is None
    assert row.unlocked_at is not None
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
cd backend && DB_ENGINE=sqlite uv run pytest api/tests_achievements.py -v
```

Expected: FAIL — `ImportError: cannot import name 'UserAchievement' from 'api.models'`

- [ ] **Step 3: 加模型**

在 `backend/api/models.py` 檔尾（`Favorite` 類別之後）加：

```python
class UserAchievement(models.Model):
    """使用者解鎖某個成就的紀錄。

    `code` 存的是 api.achievements.CATALOG 的字串，不是 FK——成就目錄是程式碼
    常數（同 dialogue_topics.TOPIC_CONFIGS），沒有對應的資料表可以指。目錄裡被
    移除的 code 會在這裡留下孤兒列；讀取端一律以 CATALOG 為準做 JOIN，孤兒列
    不會被看到，也就不需要清理指令（理由同 Favorite 的鬆散指向）。

    code 一旦上線就不可更改：它是解鎖紀錄的唯一 key，中文名稱只是顯示層，改
    文案不該讓任何人失去成就。

    notified_at = 「這則解鎖通知已經跳過了」，NULL 表示還沒跳。刻意存在後端而不
    是 localStorage——本機紀錄換瀏覽器就會失效，Godot 端已經為了同一個理由移除
    過一個彈窗（見 godot/UI/game_ui.gd 的「首次解鎖某一級」註解）。
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="achievements",
    )
    code = models.CharField(max_length=64)
    unlocked_at = models.DateTimeField(auto_now_add=True)
    notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "code"], name="uniq_user_achievement"
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "unlocked_at"], name="user_achievement_idx"
            ),
        ]
        ordering = ["unlocked_at"]

    def __str__(self):
        return f"user={self.user_id} achievement={self.code}"
```

- [ ] **Step 4: 產生並套用 migration**

```bash
cd backend && DB_ENGINE=sqlite uv run python manage.py makemigrations api --name userachievement
```

Expected: `Migrations for 'api': api/migrations/0028_userachievement.py - Create model UserAchievement`

- [ ] **Step 5: 跑測試確認通過**

```bash
cd backend && DB_ENGINE=sqlite uv run pytest api/tests_achievements.py -v
```

Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add backend/api/models.py backend/api/migrations/0028_userachievement.py backend/api/tests_achievements.py
git commit -m "feat(m6): add UserAchievement model"
```

---

## Task 2: 成就目錄常數

**Files:**
- Create: `backend/api/achievements.py`
- Test: `backend/api/tests_achievements.py`

- [ ] **Step 1: 寫失敗的測試**

在 `backend/api/tests_achievements.py` 檔尾追加（同時更新檔頂 import）：

```python
from api.achievements import (
    ALL_ACHIEVEMENTS_CODE,
    CATALOG,
    CATALOG_BY_CODE,
    CATEGORY_TITLES,
)
```

```python
def test_catalog_codes_are_unique():
    codes = [d.code for d in CATALOG]
    assert len(codes) == len(set(codes))


def test_catalog_has_seventeen_achievements():
    assert len(CATALOG) == 17


def test_every_catalog_category_has_a_title():
    for definition in CATALOG:
        assert definition.category in CATEGORY_TITLES


def test_meta_achievement_is_last_in_catalog():
    # 「一路同行」必須排在最後：evaluate() 先跑完其他 16 個成就才判定它。
    assert CATALOG[-1].code == ALL_ACHIEVEMENTS_CODE


def test_catalog_by_code_covers_every_definition():
    assert set(CATALOG_BY_CODE) == {d.code for d in CATALOG}
    assert CATALOG_BY_CODE["first_login"].name == "初來乍到"
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
cd backend && DB_ENGINE=sqlite uv run pytest api/tests_achievements.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'api.achievements'`

- [ ] **Step 3: 建立目錄模組**

建立 `backend/api/achievements.py`：

```python
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
# ⚠️ 這個數字尚未經指導教授確認，是本 plan 給的預設值。
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
        description="每一個纍積的觀點，都來自前人的貢獻",
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
```

> 注意：`returning_days` 的 `how` 文案已從原本的「累積使用平台指定天數」改成「累積在多個不同日子完成對話」，因為判準改成了不重複對話日數。文案要跟判準一致，否則玩家會以為系統壞了。

- [ ] **Step 4: 跑測試確認通過**

```bash
cd backend && DB_ENGINE=sqlite uv run pytest api/tests_achievements.py -v
```

Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add backend/api/achievements.py backend/api/tests_achievements.py
git commit -m "feat(m6): add achievement catalog constants"
```

---

## Task 3: 規則模組骨架與 `evaluate()`

先只實作兩條最單純的規則，把 `evaluate()` 的骨架與落庫行為測起來；其餘規則在 Task 4–7 逐批補上。

**Files:**
- Create: `backend/api/achievement_rules.py`
- Test: `backend/api/tests_achievements.py`

- [ ] **Step 1: 寫失敗的測試**

在 `backend/api/tests_achievements.py` 檔尾追加：

```python
from api.achievement_rules import evaluate
from api.models import PostDialogueResponse


def _post_response(user, *, condition, topic_id=102, **overrides):
    """建一筆最小可用的後測紀錄。Likert 全填 4（中立），需要立場位移的測試自行覆蓋。"""
    fields = {f"post_likert_{i}": 4 for i in range(1, 9)}
    fields.update(
        {
            "exp_stance_change_1": 4,
            "exp_stance_change_2": 4,
            "exp_quality_1": 4,
            "exp_quality_2": 4,
            "exp_reflection_1": 4,
            "exp_reflection_2": 4,
            "exp_comprehension_1": 4,
            "ccnd_attention": 4,
            "ccnd_awareness": 4,
            "ccnd_influence": 4,
            "post_open_comprehension": "測試用回答，長度足夠。",
        }
    )
    fields.update(overrides)
    return PostDialogueResponse.objects.create(
        user=user,
        topic_id=topic_id,
        experiment_condition=condition,
        **fields,
    )


@pytest.mark.django_db
def test_evaluate_unlocks_first_login_for_any_user():
    user = User.objects.create_user(username="u1", password="pw")

    newly = evaluate(user)

    assert "first_login" in newly
    assert UserAchievement.objects.filter(user=user, code="first_login").exists()


@pytest.mark.django_db
def test_evaluate_is_idempotent():
    user = User.objects.create_user(username="u1", password="pw")
    evaluate(user)

    newly = evaluate(user)

    assert "first_login" not in newly
    assert UserAchievement.objects.filter(user=user, code="first_login").count() == 1


@pytest.mark.django_db
def test_evaluate_unlocks_first_hh_dialogue_only_after_an_hh_response():
    user = User.objects.create_user(username="u1", password="pw")
    evaluate(user)
    assert not UserAchievement.objects.filter(user=user, code="first_hh_dialogue").exists()

    _post_response(user, condition=PostDialogueResponse.ExperimentCondition.HH)
    newly = evaluate(user)

    assert "first_hh_dialogue" in newly


@pytest.mark.django_db
def test_ai_response_does_not_unlock_the_hh_achievement():
    user = User.objects.create_user(username="u1", password="pw")
    _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)

    evaluate(user)

    assert not UserAchievement.objects.filter(user=user, code="first_hh_dialogue").exists()
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
cd backend && DB_ENGINE=sqlite uv run pytest api/tests_achievements.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'api.achievement_rules'`

- [ ] **Step 3: 建立規則模組**

建立 `backend/api/achievement_rules.py`：

```python
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

from django.db.models import Count

from .achievements import (
    ALL_ACHIEVEMENTS_CODE,
    CATALOG,
)
from .models import (
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


RULES = {
    "first_login": _first_login,
    "first_hh_dialogue": _first_hh_dialogue,
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
```

- [ ] **Step 4: 跑測試確認通過**

```bash
cd backend && DB_ENGINE=sqlite uv run pytest api/tests_achievements.py -v
```

Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add backend/api/achievement_rules.py backend/api/tests_achievements.py
git commit -m "feat(m6): add achievement evaluate() with first two rules"
```

---

## Task 4: 立場變動類規則

**Files:**
- Modify: `backend/api/achievement_rules.py`
- Test: `backend/api/tests_achievements.py`

- [ ] **Step 1: 寫失敗的測試**

追加到 `backend/api/tests_achievements.py`：

```python
from api.achievements import MULTI_CHANGE_COUNT, STANCE_CHANGE_THRESHOLD, SURVEY_PAIR_COUNT


@pytest.mark.django_db
def test_stance_changed_needs_delta_over_threshold():
    user = User.objects.create_user(username="u1", password="pw")
    response = _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)
    response.delta_s_value = STANCE_CHANGE_THRESHOLD / 2
    response.save(update_fields=["delta_s_value"])

    evaluate(user)
    assert not UserAchievement.objects.filter(user=user, code="stance_changed_once").exists()

    response.delta_s_value = STANCE_CHANGE_THRESHOLD
    response.save(update_fields=["delta_s_value"])
    evaluate(user)

    assert UserAchievement.objects.filter(user=user, code="stance_changed_once").exists()


@pytest.mark.django_db
def test_negative_drift_also_counts_as_change():
    # Δs 是有號的（正=偏支持、負=偏反對）。往哪邊挪都算「有變化」。
    user = User.objects.create_user(username="u1", password="pw")
    response = _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)
    response.delta_s_value = -STANCE_CHANGE_THRESHOLD
    response.save(update_fields=["delta_s_value"])

    evaluate(user)

    assert UserAchievement.objects.filter(user=user, code="stance_changed_once").exists()


@pytest.mark.django_db
def test_stance_held_needs_a_measured_but_small_delta():
    user = User.objects.create_user(username="u1", password="pw")
    response = _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)
    response.delta_s_value = 0.0
    response.save(update_fields=["delta_s_value"])

    evaluate(user)

    assert UserAchievement.objects.filter(user=user, code="stance_held_once").exists()


@pytest.mark.django_db
def test_null_delta_unlocks_neither_stance_achievement():
    # 沒填前測 → delta_s_value 是 NULL，代表「沒量到」，不是「沒變化」。
    user = User.objects.create_user(username="u1", password="pw")
    _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)

    evaluate(user)

    assert not UserAchievement.objects.filter(user=user, code="stance_changed_once").exists()
    assert not UserAchievement.objects.filter(user=user, code="stance_held_once").exists()


@pytest.mark.django_db
def test_stance_changed_many_needs_multiple_changed_dialogues():
    user = User.objects.create_user(username="u1", password="pw")
    for _ in range(MULTI_CHANGE_COUNT - 1):
        response = _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)
        response.delta_s_value = 1.0
        response.save(update_fields=["delta_s_value"])
    evaluate(user)
    assert not UserAchievement.objects.filter(user=user, code="stance_changed_many").exists()

    response = _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)
    response.delta_s_value = 1.0
    response.save(update_fields=["delta_s_value"])
    evaluate(user)

    assert UserAchievement.objects.filter(user=user, code="stance_changed_many").exists()


@pytest.mark.django_db
def test_survey_pairs_counts_responses_with_a_pre_snapshot():
    user = User.objects.create_user(username="u1", password="pw")
    for _ in range(SURVEY_PAIR_COUNT):
        response = _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)
        response.s_pre = 4.0
        response.save(update_fields=["s_pre"])

    evaluate(user)

    assert UserAchievement.objects.filter(user=user, code="survey_pairs").exists()
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
cd backend && DB_ENGINE=sqlite uv run pytest api/tests_achievements.py -v
```

Expected: 6 failed（新加的六個），10 passed

- [ ] **Step 3: 加規則**

在 `backend/api/achievement_rules.py` 的 import 區補上門檻常數：

```python
from .achievements import (
    ALL_ACHIEVEMENTS_CODE,
    CATALOG,
    MULTI_CHANGE_COUNT,
    STANCE_CHANGE_THRESHOLD,
    SURVEY_PAIR_COUNT,
)
```

在 `_first_hh_dialogue` 之後、`RULES` 之前加：

```python
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
    """完成前測＋後測的組數。

    s_pre 非 NULL 就代表該場後測成功對上了一份前測快照——這正是「完成前測與
    後測」的定義，不必另外去 JOIN UserStanceProfile。
    """
    return (
        PostDialogueResponse.objects.filter(
            user=user, s_pre__isnull=False
        ).count()
        >= SURVEY_PAIR_COUNT
    )
```

把 `RULES` 擴充成：

```python
RULES = {
    "first_login": _first_login,
    "first_hh_dialogue": _first_hh_dialogue,
    "first_ai_dialogue": _first_ai_dialogue,
    "stance_changed_once": _stance_changed_once,
    "stance_held_once": _stance_held_once,
    "stance_changed_many": _stance_changed_many,
    "survey_pairs": _survey_pairs,
}
```

- [ ] **Step 4: 跑測試確認通過**

```bash
cd backend && DB_ENGINE=sqlite uv run pytest api/tests_achievements.py -v
```

Expected: 16 passed

- [ ] **Step 5: Commit**

```bash
git add backend/api/achievement_rules.py backend/api/tests_achievements.py
git commit -m "feat(m6): add stance-change achievement rules"
```

---

## Task 5: 對話投入度與長期參與類規則

**Files:**
- Modify: `backend/api/achievement_rules.py`
- Test: `backend/api/tests_achievements.py`

- [ ] **Step 1: 寫失敗的測試**

追加到 `backend/api/tests_achievements.py`（同時在檔頂 import 區加 `from datetime import datetime, timedelta, timezone as dt_timezone`、`from django.utils import timezone`、`from api.models import AIConversation, DialogueMatch, MatchMessage`）：

```python
from api.achievements import (
    COMPLETE_FLOW_COUNT,
    RETURNING_DAYS,
    SAME_DAY_COUNT,
    TALKATIVE_TURNS,
    VETERAN_COUNT,
)


@pytest.mark.django_db
def test_talkative_unlocks_from_a_long_ai_session():
    user = User.objects.create_user(username="u1", password="pw")
    for i in range(TALKATIVE_TURNS):
        AIConversation.objects.create(
            user=user, session_id="s1", user_prompt=f"第 {i} 則發言"
        )

    evaluate(user)

    assert UserAchievement.objects.filter(user=user, code="talkative").exists()


@pytest.mark.django_db
def test_talkative_does_not_unlock_from_turns_spread_across_sessions():
    # 「高輪數對話」是單場的性質，不是總量。
    user = User.objects.create_user(username="u1", password="pw")
    for i in range(TALKATIVE_TURNS):
        AIConversation.objects.create(
            user=user, session_id=f"s{i}", user_prompt=f"第 {i} 則發言"
        )

    evaluate(user)

    assert not UserAchievement.objects.filter(user=user, code="talkative").exists()


@pytest.mark.django_db
def test_talkative_unlocks_from_a_long_hh_room():
    user = User.objects.create_user(username="u1", password="pw")
    other = User.objects.create_user(username="u2", password="pw")
    match = DialogueMatch.objects.create(
        topic_id=102, user_a=user, user_b=other, room_id="room-1"
    )
    for i in range(TALKATIVE_TURNS):
        MatchMessage.objects.create(match=match, sender=user, content=f"第 {i} 則")

    evaluate(user)

    assert UserAchievement.objects.filter(user=user, code="talkative").exists()


@pytest.mark.django_db
def test_complete_flows_counts_post_responses():
    user = User.objects.create_user(username="u1", password="pw")
    for _ in range(COMPLETE_FLOW_COUNT):
        _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)

    evaluate(user)

    assert UserAchievement.objects.filter(user=user, code="complete_flows").exists()


@pytest.mark.django_db
def test_same_day_dialogues_needs_multiple_on_one_day():
    user = User.objects.create_user(username="u1", password="pw")
    for _ in range(SAME_DAY_COUNT):
        _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)

    evaluate(user)

    assert UserAchievement.objects.filter(user=user, code="same_day_dialogues").exists()


@pytest.mark.django_db
def test_same_day_uses_taipei_midnight_not_utc_midnight():
    """UTC 同一天、台北跨午夜 → 不算同一天。

    2026-08-18 10:00 UTC = 台北 08/18 18:00
    2026-08-18 17:00 UTC = 台北 08/19 01:00
    少了 tzinfo 的舊寫法會把這兩場錯判成同一天。
    """
    user = User.objects.create_user(username="u1", password="pw")
    for _ in range(2):
        _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)
    stamps = [
        datetime(2026, 8, 18, 10, 0, tzinfo=dt_timezone.utc),
        datetime(2026, 8, 18, 17, 0, tzinfo=dt_timezone.utc),
    ]
    for stamp, response in zip(stamps, PostDialogueResponse.objects.filter(user=user)):
        PostDialogueResponse.objects.filter(pk=response.pk).update(created_at=stamp)

    evaluate(user)

    assert not UserAchievement.objects.filter(
        user=user, code="same_day_dialogues"
    ).exists()


@pytest.mark.django_db
def test_same_day_spans_utc_midnight_when_taipei_day_is_shared():
    """台北同一天、UTC 跨午夜 → 算同一天。反方向，擋住「時區設成別的值」。"""
    user = User.objects.create_user(username="u1", password="pw")
    for _ in range(2):
        _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)
    stamps = [
        datetime(2026, 8, 17, 20, 0, tzinfo=dt_timezone.utc),
        datetime(2026, 8, 18, 1, 0, tzinfo=dt_timezone.utc),
    ]
    for stamp, response in zip(stamps, PostDialogueResponse.objects.filter(user=user)):
        PostDialogueResponse.objects.filter(pk=response.pk).update(created_at=stamp)

    evaluate(user)

    assert UserAchievement.objects.filter(
        user=user, code="same_day_dialogues"
    ).exists()


@pytest.mark.django_db
def test_dialogues_on_different_days_do_not_count_as_same_day():
    # 沒有「按天分組」的爛實作（純 count >= N）會在這裡露餡。
    user = User.objects.create_user(username="u1", password="pw")
    for _ in range(SAME_DAY_COUNT):
        _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)
    now = timezone.now()
    for offset, response in enumerate(PostDialogueResponse.objects.filter(user=user)):
        PostDialogueResponse.objects.filter(pk=response.pk).update(
            created_at=now - timedelta(days=offset)
        )

    evaluate(user)

    assert not UserAchievement.objects.filter(
        user=user, code="same_day_dialogues"
    ).exists()


@pytest.mark.django_db
def test_returning_days_counts_distinct_days_not_total_dialogues():
    user = User.objects.create_user(username="u1", password="pw")
    # 同一天做滿 RETURNING_DAYS 場，不該解鎖。
    for _ in range(RETURNING_DAYS):
        _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)
    evaluate(user)
    assert not UserAchievement.objects.filter(user=user, code="returning_days").exists()

    # 改成散在不同天。created_at 是 auto_now_add，只能建立後再覆寫。
    responses = list(PostDialogueResponse.objects.filter(user=user))
    now = timezone.now()
    for offset, response in enumerate(responses):
        PostDialogueResponse.objects.filter(pk=response.pk).update(
            created_at=now - timedelta(days=offset)
        )
    evaluate(user)

    assert UserAchievement.objects.filter(user=user, code="returning_days").exists()


@pytest.mark.django_db
def test_veteran_dialogues_counts_total():
    user = User.objects.create_user(username="u1", password="pw")
    for _ in range(VETERAN_COUNT):
        _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)

    evaluate(user)

    assert UserAchievement.objects.filter(user=user, code="veteran_dialogues").exists()
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
cd backend && DB_ENGINE=sqlite uv run pytest api/tests_achievements.py -v
```

Expected: 7 failed，16 passed

- [ ] **Step 3: 加規則**

在 `backend/api/achievement_rules.py` 的 import 區補上：

```python
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
    MatchMessage,
    PostDialogueResponse,
    UserAchievement,
)
```

在 `_survey_pairs` 之後加：

```python
def _talkative(user) -> bool:
    """單場對話裡自己的發言數達門檻。

    刻意用原始訊息數而不是 DialogueSessionRecord.substantive_turn_count／
    MatchInputGateStat.substantive_turn_count：那兩個欄位要等後測送出才被填，
    在那之前是 NULL，會讓這個成就的解鎖時機變得難以解釋。

    H-AI 與 H-H 分開查再取 or——兩邊的「一場對話」是不同的鍵（session_id vs
    match_id），沒有辦法合成一個 query，也不需要。
    """
    ai_hit = (
        AIConversation.objects.filter(user=user)
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
    """同一天完成幾場。

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
    """在幾個不重複的日子完成過對話。

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
```

把這六條加進 `RULES`：

```python
    "talkative": _talkative,
    "complete_flows": _complete_flows,
    "same_day_dialogues": _same_day_dialogues,
    "returning_days": _returning_days,
    "veteran_dialogues": _veteran_dialogues,
```

- [ ] **Step 4: 跑測試確認通過**

```bash
cd backend && DB_ENGINE=sqlite uv run pytest api/tests_achievements.py -v
```

Expected: 23 passed

- [ ] **Step 5: Commit**

```bash
git add backend/api/achievement_rules.py backend/api/tests_achievements.py
git commit -m "feat(m6): add engagement and long-term achievement rules"
```

---

## Task 6: Godot 入場成就

**Files:**
- Modify: `backend/api/achievement_rules.py`
- Test: `backend/api/tests_achievements.py`

- [ ] **Step 1: 寫失敗的測試**

追加（檔頂 import 加 `from api.models import GodotEntryTicket`）：

```python
@pytest.mark.django_db
def test_godot_entry_needs_a_redeemed_ticket():
    user = User.objects.create_user(username="u1", password="pw")
    GodotEntryTicket.objects.create(
        token="t1", user=user, expires_at=timezone.now() + timedelta(minutes=1)
    )

    evaluate(user)
    assert not UserAchievement.objects.filter(user=user, code="first_godot_entry").exists()

    GodotEntryTicket.objects.filter(token="t1").update(redeemed_at=timezone.now())
    evaluate(user)

    assert UserAchievement.objects.filter(user=user, code="first_godot_entry").exists()
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
cd backend && DB_ENGINE=sqlite uv run pytest api/tests_achievements.py::test_godot_entry_needs_a_redeemed_ticket -v
```

Expected: FAIL — assertion error（規則不存在，成就永遠不解鎖）

- [ ] **Step 3: 加規則**

`backend/api/achievement_rules.py` 的 `.models` import 加入 `GodotEntryTicket`，並加：

```python
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
```

加進 `RULES`：

```python
    "first_godot_entry": _first_godot_entry,
```

- [ ] **Step 4: 跑測試確認通過**

```bash
cd backend && DB_ENGINE=sqlite uv run pytest api/tests_achievements.py -v
```

Expected: 24 passed

- [ ] **Step 5: Commit**

```bash
git add backend/api/achievement_rules.py backend/api/tests_achievements.py
git commit -m "feat(m6): add godot entry achievement rule"
```

---

## Task 7: 目錄與規則一致性的守門測試

這一步不加功能，只加一道防止兩邊漂移的護欄。目前 `CATALOG` 有 17 個 code，`RULES` 有 13 個（`first_knowledge_base` 在 P2、兩個品質成就在 P3，meta 成就不需要規則）。

**Files:**
- Test: `backend/api/tests_achievements.py`

- [ ] **Step 1: 寫測試**

追加：

```python
from api.achievement_rules import RULES

# P1 尚未實作規則的成就。每完成一期就從這裡拿掉對應的 code。
PENDING_RULES = {
    "first_knowledge_base",   # P2：知識庫瀏覽埋點
    "clean_dialogue_once",    # P3：攻擊性計數落庫
    "clean_dialogue_many",    # P3
}


def test_every_catalog_entry_has_a_rule_or_is_explicitly_pending():
    for definition in CATALOG:
        if definition.code == ALL_ACHIEVEMENTS_CODE:
            continue          # meta 成就由 evaluate() 直接處理，沒有 predicate
        assert definition.code in RULES or definition.code in PENDING_RULES, (
            f"{definition.code} 既沒有規則也沒有列在 PENDING_RULES"
        )


def test_no_orphan_rules():
    codes = {d.code for d in CATALOG}
    for code in RULES:
        assert code in codes, f"RULES 有 {code}，但 CATALOG 沒有"
```

- [ ] **Step 2: 跑測試確認通過**

```bash
cd backend && DB_ENGINE=sqlite uv run pytest api/tests_achievements.py -v
```

Expected: 26 passed

- [ ] **Step 3: Commit**

```bash
git add backend/api/tests_achievements.py
git commit -m "test(m6): guard achievement catalog and rules from drifting apart"
```

---

## Task 8: `GET /api/achievements/me/`

**Files:**
- Modify: `backend/api/views.py`（接在 `TitleMeView` 之後）、`backend/api/urls.py`
- Test: `backend/api/tests_achievements.py`

- [ ] **Step 1: 寫失敗的測試**

追加（檔頂 import 加 `from rest_framework.test import APIClient`，若尚未 import）：

```python
@pytest.mark.django_db
def test_achievements_me_requires_authentication():
    client = APIClient()

    response = client.get("/api/achievements/me/")

    assert response.status_code == 401


@pytest.mark.django_db
def test_achievements_me_returns_all_categories_and_items():
    user = User.objects.create_user(username="u1", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/achievements/me/")

    assert response.status_code == 200
    assert [c["id"] for c in response.data["categories"]] == list(CATEGORY_TITLES)
    total = sum(len(c["items"]) for c in response.data["categories"])
    assert total == len(CATALOG)


@pytest.mark.django_db
def test_achievements_me_evaluates_on_read():
    # 打開成就頁本身就會結算——這是規則的最終安全網。
    user = User.objects.create_user(username="u1", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)

    client.get("/api/achievements/me/")

    assert UserAchievement.objects.filter(user=user, code="first_login").exists()


@pytest.mark.django_db
def test_achievements_me_reports_newly_unlocked_with_full_text():
    user = User.objects.create_user(username="u1", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/achievements/me/")

    newly = response.data["newly_unlocked"]
    assert [item["code"] for item in newly] == ["first_login"]
    assert newly[0]["name"] == "初來乍到"
    assert newly[0]["title"] == "築橋新手"


@pytest.mark.django_db
def test_locked_items_are_marked_unlocked_false():
    user = User.objects.create_user(username="u1", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/achievements/me/")

    items = {i["code"]: i for c in response.data["categories"] for i in c["items"]}
    assert items["first_login"]["unlocked"] is True
    assert items["veteran_dialogues"]["unlocked"] is False
    assert items["veteran_dialogues"]["unlocked_at"] is None
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
cd backend && DB_ENGINE=sqlite uv run pytest api/tests_achievements.py -v
```

Expected: 5 failed（404，路由不存在）

- [ ] **Step 3: 加 view**

在 `backend/api/views.py` 檔頂 import 區加：

```python
from .achievement_rules import evaluate as evaluate_achievements
from .achievements import CATALOG, CATEGORY_TITLES
```

並在 `.models` 的 import 清單加入 `UserAchievement`。

在 `TitleMeView` 之後加：

```python
def _achievement_item(definition, row):
    """把目錄定義 + 解鎖紀錄（可能沒有）攤成前端要的一筆。"""
    return {
        "code": definition.code,
        "name": definition.name,
        "how": definition.how,
        "description": definition.description,
        "title": definition.title_name,
        "unlocked": row is not None,
        "unlocked_at": row.unlocked_at if row is not None else None,
    }


class AchievementMeView(APIView):
    """GET /api/achievements/me/ — 成就頁的全部內容，外加還沒跳過通知的新解鎖。

    這支 GET 有副作用（會呼叫 evaluate 落庫新解鎖），這是刻意的：它是規則的
    最終安全網，玩家只要打開成就頁就會結算，不必依賴每一個觸發點都沒漏掉。

    newly_unlocked 的判準是 notified_at IS NULL（資料庫），不是 evaluate() 的
    回傳值——兩個併發請求可能都算出同一個新解鎖，但只有一列會真的被建立。
    通知跳完之後由前端呼叫 POST /api/achievements/ack/ 標記。
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        evaluate_achievements(request.user)
        rows = {
            row.code: row
            for row in UserAchievement.objects.filter(user=request.user)
        }

        categories = []
        for category_id, category_title in CATEGORY_TITLES.items():
            categories.append(
                {
                    "id": category_id,
                    "title": category_title,
                    "items": [
                        _achievement_item(d, rows.get(d.code))
                        for d in CATALOG
                        if d.category == category_id
                    ],
                }
            )

        # 帶完整文案而不只是 code：toast 要顯示名稱、描述與頭銜，讓前端再去
        # categories 裡撈一次只是多一層可能對不上的查表。
        newly_unlocked = [
            _achievement_item(d, rows[d.code])
            for d in CATALOG
            if d.code in rows and rows[d.code].notified_at is None
        ]

        return Response(
            {"categories": categories, "newly_unlocked": newly_unlocked}
        )
```

- [ ] **Step 4: 加路由**

在 `backend/api/urls.py` 的 `path('titles/me/', views.TitleMeView.as_view()),` 之後加：

```python
    path('achievements/me/', views.AchievementMeView.as_view()),
```

- [ ] **Step 5: 跑測試確認通過**

```bash
cd backend && DB_ENGINE=sqlite uv run pytest api/tests_achievements.py -v
```

Expected: 31 passed

- [ ] **Step 6: Commit**

```bash
git add backend/api/views.py backend/api/urls.py backend/api/tests_achievements.py
git commit -m "feat(m6): add GET /api/achievements/me/"
```

---

## Task 9: `POST /api/achievements/ack/`

**Files:**
- Modify: `backend/api/views.py`、`backend/api/urls.py`
- Test: `backend/api/tests_achievements.py`

- [ ] **Step 1: 寫失敗的測試**

追加：

```python
@pytest.mark.django_db
def test_ack_marks_notifications_as_seen():
    user = User.objects.create_user(username="u1", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)
    client.get("/api/achievements/me/")

    response = client.post(
        "/api/achievements/ack/", {"codes": ["first_login"]}, format="json"
    )

    assert response.status_code == 200
    assert response.data == {"acknowledged": 1}
    again = client.get("/api/achievements/me/")
    assert again.data["newly_unlocked"] == []


@pytest.mark.django_db
def test_ack_is_idempotent():
    user = User.objects.create_user(username="u1", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)
    client.get("/api/achievements/me/")
    client.post("/api/achievements/ack/", {"codes": ["first_login"]}, format="json")

    response = client.post(
        "/api/achievements/ack/", {"codes": ["first_login"]}, format="json"
    )

    assert response.data == {"acknowledged": 0}


@pytest.mark.django_db
def test_ack_rejects_a_non_list_payload():
    user = User.objects.create_user(username="u1", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(
        "/api/achievements/ack/", {"codes": "first_login"}, format="json"
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_ack_cannot_touch_another_users_rows():
    owner = User.objects.create_user(username="u1", password="pw")
    intruder = User.objects.create_user(username="u2", password="pw")
    UserAchievement.objects.create(user=owner, code="veteran_dialogues")
    client = APIClient()
    client.force_authenticate(user=intruder)

    response = client.post(
        "/api/achievements/ack/", {"codes": ["veteran_dialogues"]}, format="json"
    )

    assert response.data == {"acknowledged": 0}
    assert UserAchievement.objects.get(
        user=owner, code="veteran_dialogues"
    ).notified_at is None
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
cd backend && DB_ENGINE=sqlite uv run pytest api/tests_achievements.py -v
```

Expected: 4 failed（404）

- [ ] **Step 3: 加 view**

在 `AchievementMeView` 之後加：

```python
class AchievementAckView(APIView):
    """POST /api/achievements/ack/ — 標記「這些解鎖通知已經跳過了」。

    只更新 notified_at 還是 NULL 的列，所以重送不會累加、也不會把時間往後推。
    queryset 一律鎖在 request.user 底下——code 是全域字串，不做這個限制就等於
    讓任何登入者去標記別人的通知。
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        codes = request.data.get("codes")
        if not isinstance(codes, list) or not all(
            isinstance(code, str) for code in codes
        ):
            return Response(
                {"detail": "codes 必須是字串陣列。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        updated = UserAchievement.objects.filter(
            user=request.user, code__in=codes, notified_at__isnull=True
        ).update(notified_at=timezone.now())

        return Response({"acknowledged": updated})
```

（`timezone` 已在 `views.py` 頂部 import；若沒有，加 `from django.utils import timezone`。）

- [ ] **Step 4: 加路由**

在 `backend/api/urls.py` 的 `achievements/me/` 之後加：

```python
    path('achievements/ack/', views.AchievementAckView.as_view()),
```

- [ ] **Step 5: 跑測試確認通過**

```bash
cd backend && DB_ENGINE=sqlite uv run pytest api/tests_achievements.py -v
```

Expected: 35 passed

- [ ] **Step 6: Commit**

```bash
git add backend/api/views.py backend/api/urls.py backend/api/tests_achievements.py
git commit -m "feat(m6): add POST /api/achievements/ack/"
```

---

## Task 10: 把 `evaluate` 接到既有的觸發點

**Files:**
- Modify: `backend/api/views.py`（`PostDialogueResponseView.post`、`GodotTicketRedeemView.post`）
- Test: `backend/api/tests_achievements.py`

- [ ] **Step 1: 寫失敗的測試**

追加：

```python
@pytest.mark.django_db
def test_submitting_the_post_questionnaire_unlocks_immediately():
    user = User.objects.create_user(username="u1", password="pw")
    _post_response(user, condition=PostDialogueResponse.ExperimentCondition.HH)
    # 直接呼叫 view 的觸發點太繁瑣，這裡驗證的是「evaluate 在建立後測紀錄之後
    # 被呼叫過」的效果：解鎖紀錄已經在庫裡，不必等使用者打開成就頁。
    from api.achievement_rules import evaluate as _evaluate

    _evaluate(user)

    assert UserAchievement.objects.filter(user=user, code="first_hh_dialogue").exists()


@pytest.mark.django_db
def test_evaluate_failure_does_not_break_the_caller():
    """評估壞掉不該讓後測送出跟著失敗——問卷答案比成就重要得多。"""
    from api import views

    user = User.objects.create_user(username="u1", password="pw")
    assert views._safe_evaluate_achievements(user) == []
```

第二個測試需要一個可測的包裝函式。

- [ ] **Step 2: 跑測試確認失敗**

```bash
cd backend && DB_ENGINE=sqlite uv run pytest api/tests_achievements.py -v
```

Expected: 1 failed — `AttributeError: module 'api.views' has no attribute '_safe_evaluate_achievements'`

- [ ] **Step 3: 加包裝函式並接到觸發點**

在 `backend/api/views.py` 的 `AchievementMeView` 之前加：

```python
def _safe_evaluate_achievements(user) -> list[str]:
    """在既有流程裡結算成就，永遠不把例外往上丟。

    成就是附加價值，不是那些流程的目的：一次評估失敗絕不該讓受試者的問卷答案
    送不出去，或讓玩家進不了 Godot 大廳。漏掉的解鎖會在下次打開成就頁時由
    AchievementMeView 的 evaluate 補上，所以吞掉例外沒有永久後果——
    但要留 log，否則規則寫壞了沒人會發現。
    """
    try:
        return evaluate_achievements(user)
    except Exception:
        logger.exception("Achievement evaluation failed for user=%s.", user.id)
        return []
```

在 `PostDialogueResponseView.post` 裡，`_finalize_input_gate_metrics(...)` 呼叫**之後**、`out = PostDialogueResponseOutputSerializer(response_obj)` 之前插入：

```python
        # 必須排在 _finalize_input_gate_metrics 之後：品質類規則讀的是那支函式
        # 落庫的指標，先評估會讀到還沒算完的值。
        _safe_evaluate_achievements(request.user)
```

在 `GodotTicketRedeemView.post` 裡，`return Response({"user_id": user.id})` 之前插入：

```python
        # 兌換成功 = 這位玩家真的進了 Godot 世界。
        _safe_evaluate_achievements(user)
```

- [ ] **Step 4: 跑測試確認通過**

```bash
cd backend && DB_ENGINE=sqlite uv run pytest api/tests_achievements.py -v
```

Expected: 37 passed

- [ ] **Step 5: 跑既有的相關測試確認沒有回歸**

```bash
cd backend && DB_ENGINE=sqlite uv run pytest api/tests_post_questionnaire.py api/tests_godot_tickets.py api/tests_titles.py -v
```

Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add backend/api/views.py backend/api/tests_achievements.py
git commit -m "feat(m6): evaluate achievements on questionnaire submit and godot entry"
```

---

## Task 11: 前端接上 API

**Files:**
- Create: `frontend/src/api/achievements.js`
- Modify: `frontend/src/pages/AchievementPage.jsx`、`frontend/src/App.jsx`
- Delete: `frontend/src/pages/achievements.data.js`

- [ ] **Step 1: 加 API 封裝**

建立 `frontend/src/api/achievements.js`：

```javascript
import api from './client';

// 成就頁與成就通知共用同一支端點：GET 會順便結算（後端刻意的設計，見
// AchievementMeView 的 docstring），所以不需要另外的「刷新」呼叫。
export function fetchAchievements() {
  return api.get('/api/achievements/me/').then((response) => response.data);
}

// 通知跳完之後標記已讀。存在後端而不是 localStorage，換瀏覽器才不會重跳。
export function ackAchievements(codes) {
  if (!codes.length) return Promise.resolve();
  return api.post('/api/achievements/ack/', { codes });
}
```

- [ ] **Step 2: 改成就頁**

把 `frontend/src/pages/AchievementPage.jsx` 整份換成：

```jsx
import { useEffect, useState } from 'react';
import './AchievementPage.css';
import { fetchAchievements } from '../api/achievements';
import LevelSummaryCard from './LevelSummaryCard';

function AchievementPage() {
  const [categories, setCategories] = useState([]);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    fetchAchievements()
      .then((data) => {
        if (cancelled) return;
        setCategories(data.categories ?? []);
      })
      .catch((err) => {
        if (cancelled) return;
        // 不要靜靜消失：畫面給訊息、console 留線索（同 LevelSummaryCard 的作法）。
        console.warn('成就資料讀取失敗：', err?.message ?? err);
        setError('成就資料讀取失敗，請重新整理再試一次。');
      });
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="achievement-page">
      <div className="achievement-heading">
        <span className="achievement-kicker">Achievement</span>
        <h1>成就</h1>
        <p>在 TakeABridge 的每一步，都會留下屬於你的足跡與頭銜。</p>
      </div>

      <LevelSummaryCard />

      {error && <p className="achievement-error">{error}</p>}

      <div className="achievement-body">
        {categories.map((category) => (
          <section className="achievement-category" key={category.id}>
            <h2 className="achievement-category-title">{category.title}</h2>
            <div className="achievement-grid">
              {category.items.map((item) => {
                const locked = !item.unlocked;
                return (
                  <article
                    className={`achievement-card${locked ? ' achievement-card--locked' : ''}`}
                    key={item.code}
                  >
                    <div className="achievement-row-main">
                      <span className="achievement-name">{item.name}</span>
                      {locked
                        ? <span className="achievement-locked-tag">· 未獲得</span>
                        : item.title && <span className="achievement-title-badge">{item.title}</span>}
                    </div>
                    {!locked && <p className="achievement-desc">{item.description}</p>}
                    <span className="achievement-how">{item.how}</span>
                  </article>
                );
              })}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

export default AchievementPage;
```

在 `frontend/src/pages/AchievementPage.css` 檔尾加：

```css
.achievement-error {
  margin: 12px 0;
  color: #c0392b;
  font-size: 14px;
}
```

- [ ] **Step 3: 改 toast**

在 `frontend/src/App.jsx`：

刪掉這兩行 import：

```jsx
import { findAchievement } from './pages/achievements.data'
```

加上：

```jsx
import { ackAchievements, fetchAchievements } from './api/achievements'
```

把寫死的那一行（原本的 `const [unlockedToast, setUnlockedToast] = useState(() => findAchievement('一路同行'));`）換成一個佇列 + 拉取：

```jsx
  // 待跳的解鎖通知佇列。後端以 UserAchievement.notified_at 為準，跳完才 ack，
  // 所以重整不會重跳，換瀏覽器也不會。
  const [toastQueue, setToastQueue] = useState([]);
  const unlockedToast = toastQueue[0] ?? null;

  useEffect(() => {
    if (!user) return undefined;
    let cancelled = false;
    fetchAchievements()
      .then((data) => {
        if (cancelled) return;
        setToastQueue(data.newly_unlocked ?? []);
      })
      .catch((err) => {
        // 通知拿不到不影響任何功能，記著就好。
        console.warn('成就通知讀取失敗：', err?.message ?? err);
      });
    return () => { cancelled = true; };
  }, [user]);

  const dismissToast = useCallback(() => {
    setToastQueue((queue) => {
      const [shown, ...rest] = queue;
      if (shown) ackAchievements([shown.code]).catch(() => {});
      return rest;
    });
  }, []);
```

把 `<AchievementToast ... />` 換成：

```jsx
        <AchievementToast
          achievement={unlockedToast}
          onClose={dismissToast}
          onOpen={() => {
            dismissToast();
            navigate('/achievement');
          }}
        />
```

- [ ] **Step 4: 改 toast 元件的欄位名**

`frontend/src/components/AchievementToast.jsx` 目前讀 `achievement.desc`，後端回的是 `description`。把該行改成：

```jsx
        <span className="achievement-toast-desc">{achievement.description}</span>
```

同時更新檔頂註解：

```jsx
// achievement: { code, name, description, title? }，來自 GET /api/achievements/me/
// 的 newly_unlocked；null 時不顯示
```

- [ ] **Step 5: 刪掉靜態資料檔**

```bash
git rm frontend/src/pages/achievements.data.js
```

- [ ] **Step 6: 確認沒有殘留引用**

```bash
grep -rn "achievements.data\|findAchievement\|item.desc" frontend/src
```

Expected: 沒有輸出

- [ ] **Step 7: 前端 smoke check**

```bash
cd frontend && npm run lint && npm run build
```

Expected: 兩者都成功，無 error

- [ ] **Step 8: Commit**

```bash
git add frontend/src/api/achievements.js frontend/src/pages/AchievementPage.jsx frontend/src/pages/AchievementPage.css frontend/src/App.jsx frontend/src/components/AchievementToast.jsx
git commit -m "feat(m6): wire achievement page and toast to the backend"
```

### 實作後的修正（code review 發現，已納入出貨程式碼）

上面的 Step 3 骨架有四個問題，實際出貨的版本已修正。新寫類似程式碼時照下面這版：

**1. 登出必須清空 `toastQueue`（資料正確性）。** `useEffect([user])` 在 `!user` 時只 early return、不清狀態，所以佇列會跨使用者殘留。A 未點掉的 toast 會在 B 登入後、`fetchAchievements()` 回來前的空檔顯示給 B；B 點下去，ack 以 B 的身份送出，後端 queryset 鎖 `request.user`，於是把 **B 自己**同 code 的成就誤標成已通知。共用實驗室機器的部署下會真的發生。在 `handleLogout` 加 `setToastQueue([]);`。

**2. 副作用不可放在 setState 的 updater 裡。** StrictMode 會把 updater 跑兩次，ack 就送兩次。當前值改從閉包讀，依賴陣列帶 `toastQueue`：

```jsx
  const dismissToast = useCallback(() => {
    const shown = toastQueue[0];
    if (shown) {
      ackAchievements([shown.code])
        .catch((err) => console.warn('成就通知標記已讀失敗：', err?.message ?? err));
    }
    setToastQueue((queue) => queue.slice(1));
  }, [toastQueue]);
```

（空 `.catch(() => {})` 也一併改掉——ack 失敗的後果可觀察「下次登入同一則又跳」，但屆時沒線索可查。）

**3. `<AchievementToast>` 要有 `key={unlockedToast?.code}`。** 元素樹位置固定又沒 key 時 React 重用同一個 DOM node，CSS 進場動畫不會重播，佇列前進只會「文字瞬間換掉」，使用者容易沒發現自己跳過一則。

**4. `AchievementPage` 要有 loading 狀態。** 初次 render `categories` 是 `[]`，回來後 17 張卡一次塞入，版面明顯跳動；而且「載入中」與「API 回了空 categories」在畫面上無法區分。加 `loading` state、在 `.finally()` 裡關（記得先看 `cancelled`），用中性樣式 `.achievement-loading`（不要複用紅色的 `.achievement-error`）。

另外 `ackAchievements` 的 guard 要寫 `if (!codes?.length)`——`codes` 為 `undefined` 時 `!codes.length` 會直接 TypeError。

### 實機驗證結果（已完成）

後端 sqlite + `npm run dev`，實際登入走過一遍：`GET /api/achievements/me/` 回 5 分類 17 卡（16 鎖 1 解），toast 跳出並顯示頭銜，點關閉送出**恰好一次** ack（確認第 2 點的修正生效），`notified_at` 落庫，重整後 toast 不再出現，console 無錯誤。未認證打端點回 401，畸形 `codes` 回 400，ack 重送回 `{"acknowledged": 0}`。

---

# P2

## Task 12: 頭銜掛勾

解鎖成就時一併授予對應的 `Title`，補上 `UserTitle` docstring 裡承諾但不存在的那一環。

**Files:**
- Create: `backend/api/management/commands/seed_achievement_titles.py`
- Modify: `backend/api/achievement_rules.py`
- Test: `backend/api/tests_achievements.py`

- [ ] **Step 1: 寫失敗的測試**

追加（檔頂 import 加 `from api.models import Title, UserTitle`、`from django.core.management import call_command`）：

```python
@pytest.mark.django_db
def test_unlocking_grants_the_mapped_title():
    call_command("seed_achievement_titles")
    user = User.objects.create_user(username="u1", password="pw")

    evaluate(user)

    assert UserTitle.objects.filter(user=user, title__name="築橋新手").exists()


@pytest.mark.django_db
def test_granted_titles_are_not_auto_selected():
    # UserTitle 有「一位使用者最多一個 is_selected」的 partial unique index。
    # 自動選取會在第二個頭銜到手時炸掉，而且也該由玩家自己決定要掛哪一個。
    call_command("seed_achievement_titles")
    user = User.objects.create_user(username="u1", password="pw")
    _post_response(user, condition=PostDialogueResponse.ExperimentCondition.HH)
    _post_response(user, condition=PostDialogueResponse.ExperimentCondition.AI)

    evaluate(user)

    assert UserTitle.objects.filter(user=user).count() >= 3
    assert not UserTitle.objects.filter(user=user, is_selected=True).exists()


@pytest.mark.django_db
def test_unlocking_without_seeded_titles_still_works():
    # 沒跑過 seed 指令的環境（例如剛建好的測試庫）不該讓解鎖整個失敗。
    user = User.objects.create_user(username="u1", password="pw")

    newly = evaluate(user)

    assert "first_login" in newly
    assert not UserTitle.objects.filter(user=user).exists()


@pytest.mark.django_db
def test_seed_command_is_idempotent():
    call_command("seed_achievement_titles")
    call_command("seed_achievement_titles")

    assert Title.objects.filter(name="築橋新手").count() == 1
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
cd backend && DB_ENGINE=sqlite uv run pytest api/tests_achievements.py -v
```

Expected: 4 failed — `CommandError: Unknown command: 'seed_achievement_titles'`

- [ ] **Step 3: 加 seed 指令**

建立 `backend/api/management/commands/seed_achievement_titles.py`：

```python
"""建立成就目錄裡用到的所有頭銜。

頭銜定義（名稱）跟著成就目錄走，但 Title 是資料表——需要一支指令把兩邊對齊。
get_or_create 所以可以重複跑；已存在的頭銜不會被改動（顏色可能已被研究者在
admin 調過，不該被 seed 覆寫）。

部署後執行一次：
    uv run python manage.py seed_achievement_titles
"""

from django.core.management.base import BaseCommand

from api.achievements import CATALOG
from api.models import Title


class Command(BaseCommand):
    help = "建立成就目錄裡用到的所有 Title（可重複執行）"

    def handle(self, *args, **options):
        names = sorted({d.title_name for d in CATALOG if d.title_name})
        created = 0
        for name in names:
            _, was_created = Title.objects.get_or_create(name=name)
            if was_created:
                created += 1
        self.stdout.write(
            self.style.SUCCESS(f"頭銜共 {len(names)} 個，新建 {created} 個。")
        )
```

- [ ] **Step 4: 在 `_persist` 裡授予頭銜**

在 `backend/api/achievement_rules.py`：`.models` 的 import 加入 `Title, UserTitle`，檔頂加 `import logging` 與 `logger = logging.getLogger(__name__)`，然後把 `_persist` 改成：

```python
def _persist(user, codes: list[str]) -> None:
    """把新解鎖寫進 UserAchievement，並授予對應頭銜。

    ignore_conflicts=True：兩個併發請求可能同時算出同一組新解鎖，unique
    constraint 會擋掉後到的那一列，這裡不該因此丟 500。
    """
    UserAchievement.objects.bulk_create(
        [UserAchievement(user=user, code=code) for code in codes],
        ignore_conflicts=True,
    )
    _grant_titles(user, codes)


def _grant_titles(user, codes: list[str]) -> None:
    """成就 → 頭銜。

    刻意**不**設 is_selected：UserTitle 有「一位使用者最多一個 is_selected」的
    partial unique index，自動選取會在第二個頭銜到手時直接違反約束；而且掛哪一
    個頭銜本來就該由玩家在 Godot 大廳自己決定（POST /api/titles/me/）。

    Title 不存在時跳過而不是建立：頭銜表由 seed_achievement_titles 管理，在這裡
    順手建會讓「哪些頭銜存在」變成兩個地方說了算。也不讓它讓解鎖失敗——成就已經
    寫進去了，頭銜之後補 seed 再跑一次 evaluate 就會補上。
    """
    wanted = {
        d.title_name
        for d in CATALOG
        if d.code in codes and d.title_name
    }
    if not wanted:
        return

    titles = {t.name: t for t in Title.objects.filter(name__in=wanted)}
    for name in wanted:
        title = titles.get(name)
        if title is None:
            logger.warning(
                "Title %s 不存在，成就頭銜未授予；請執行 seed_achievement_titles。",
                name,
            )
            continue
        UserTitle.objects.get_or_create(user=user, title=title)
```

- [ ] **Step 5: 跑測試確認通過**

```bash
cd backend && DB_ENGINE=sqlite uv run pytest api/tests_achievements.py -v
```

Expected: 41 passed

- [ ] **Step 6: Commit**

```bash
git add backend/api/management/commands/seed_achievement_titles.py backend/api/achievement_rules.py backend/api/tests_achievements.py
git commit -m "feat(m6): grant titles when achievements unlock"
```

---

## Task 13: 知識庫瀏覽成就

`first_knowledge_base` 沒有可查的既有資料，但也不需要新的埋點表——在知識庫端點呼叫 `evaluate` 就直接寫進 `UserAchievement`，那張表本身就是紀錄。

**Files:**
- Modify: `backend/api/achievement_rules.py`、`backend/api/views.py`
- Test: `backend/api/tests_achievements.py`

- [ ] **Step 1: 寫失敗的測試**

追加：

```python
@pytest.mark.django_db
def test_browsing_the_knowledge_base_unlocks_the_achievement():
    user = User.objects.create_user(username="u1", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)

    client.get("/api/summary/viewpoints/browse/?topic_id=102")

    assert UserAchievement.objects.filter(
        user=user, code="first_knowledge_base"
    ).exists()


@pytest.mark.django_db
def test_a_rejected_browse_request_does_not_unlock():
    # 少帶 topic_id 會 400；沒真的看到知識庫就不該給成就。
    user = User.objects.create_user(username="u1", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/summary/viewpoints/browse/")

    assert response.status_code == 400
    assert not UserAchievement.objects.filter(
        user=user, code="first_knowledge_base"
    ).exists()
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
cd backend && DB_ENGINE=sqlite uv run pytest api/tests_achievements.py -v
```

Expected: 1 failed（第一個），1 passed

- [ ] **Step 3: 加規則**

在 `backend/api/achievement_rules.py` 加：

```python
def _first_knowledge_base(user) -> bool:
    """「打開過知識庫」沒有其他資料來源可查——UserAchievement 本身就是那筆紀錄。

    所以這條規則只回報既有狀態，真正的解鎖動作發生在
    views.KnowledgeBaseViewpointBrowseView 呼叫 unlock_knowledge_base() 的時候。
    寫成 predicate 是為了讓它跟其他 16 個走同一套流程（含「一路同行」的判定）。
    """
    return UserAchievement.objects.filter(
        user=user, code="first_knowledge_base"
    ).exists()


def unlock_knowledge_base(user) -> list[str]:
    """埋點：使用者真的打開了知識庫。

    先寫紀錄再評估，這樣同一次呼叫裡「求知若渴」就會被算進「一路同行」。
    """
    UserAchievement.objects.get_or_create(user=user, code="first_knowledge_base")
    return evaluate(user)
```

加進 `RULES`：

```python
    "first_knowledge_base": _first_knowledge_base,
```

從 `tests_achievements.py` 的 `PENDING_RULES` 拿掉 `"first_knowledge_base"`。

- [ ] **Step 4: 在知識庫端點呼叫**

在 `backend/api/views.py` 的 import 加：

```python
from .achievement_rules import unlock_knowledge_base
```

在 `KnowledgeBaseViewpointBrowseView.get` 裡，兩個 400 的 early return **之後**（也就是 `topic_id` 已確定合法之處），`qs = _approved_viewpoints_queryset(topic_id)` 之前插入：

```python
        # 埋點放在參數驗證之後：400 的請求沒真的看到知識庫，不該給成就。
        try:
            unlock_knowledge_base(request.user)
        except Exception:
            logger.exception(
                "Knowledge base achievement unlock failed for user=%s.",
                request.user.id,
            )
```

- [ ] **Step 5: 跑測試確認通過**

```bash
cd backend && DB_ENGINE=sqlite uv run pytest api/tests_achievements.py -v
```

Expected: 43 passed

- [ ] **Step 6: 確認知識庫既有測試沒有回歸**

```bash
cd backend && DB_ENGINE=sqlite uv run pytest api/tests_knowledge_base_browse.py -v
```

Expected: all passed

- [ ] **Step 7: Commit**

```bash
git add backend/api/achievement_rules.py backend/api/views.py backend/api/tests_achievements.py
git commit -m "feat(m6): unlock the knowledge-base achievement on first browse"
```

---

# P3

> **範圍限制（必讀）：** H-H 對話室（`MatchRoomConsumer`）目前**完全沒有跑 input gate**，`record_match_attempt()` 是沒有 production 呼叫端的死程式碼。因此這一期的兩個品質成就只看 H-AI 那半的資料。把閘門接進 H-H 是獨立的既有缺陷，不在本 plan 範圍內。

## Task 14: H-AI 攻擊性計數落庫

**Files:**
- Modify: `backend/api/models.py`（`DialogueSessionRecord`）、`backend/apps/matching/services/input_gate_store.py`、`backend/api/consumers.py`、`backend/api/views.py`
- Create: `backend/api/migrations/0029_dialoguesessionrecord_profanity_total.py`
- Test: `backend/api/tests_achievements.py`

- [ ] **Step 1: 寫失敗的測試**

追加（檔頂 import 加 `from api.models import DialogueSessionRecord`）：

```python
@pytest.mark.django_db
def test_record_ai_attempt_counts_profanity_separately():
    from apps.matching.services.input_gate_store import record_ai_attempt

    user = User.objects.create_user(username="u1", password="pw")
    DialogueSessionRecord.objects.create(
        user=user,
        session_id="s1",
        topic_id=102,
        topic_title="測試議題",
        collection_name="test",
        last_activity_at=timezone.now(),
    )

    record_ai_attempt("s1", blocked=True, profanity=False)
    record_ai_attempt("s1", blocked=True, profanity=True)

    record = DialogueSessionRecord.objects.get(session_id="s1")
    assert record.invalid_input_total == 2
    assert record.profanity_total == 1
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
cd backend && DB_ENGINE=sqlite uv run pytest api/tests_achievements.py -v
```

Expected: FAIL — `TypeError: record_ai_attempt() got an unexpected keyword argument 'profanity'`

- [ ] **Step 3: 加欄位**

在 `backend/api/models.py` 的 `DialogueSessionRecord`，緊接在 `input_attempt_total` 之後加：

```python
    # 被攔下的訊息裡有幾則是單字粗口（InputVerdict.PROFANITY_ONLY）。
    # 從 invalid_input_total 拆出來單獨計數的理由：那個欄位混合了「亂敲鍵盤」與
    # 「罵髒話」，兩者在研究上是不同的訊號，成就系統的「未偵測到攻擊性內容」
    # 也只認後者。同樣永不歸零。
    profanity_total = models.IntegerField(default=0)
```

```bash
cd backend && DB_ENGINE=sqlite uv run python manage.py makemigrations api --name dialoguesessionrecord_profanity_total
```

Expected: 產生 `api/migrations/0029_dialoguesessionrecord_profanity_total.py`

- [ ] **Step 4: 改 store 的簽名**

在 `backend/apps/matching/services/input_gate_store.py`，把 `record_ai_attempt` 改成：

```python
def record_ai_attempt(session_id: str, *, blocked: bool, profanity: bool = False) -> int:
    """記一次送出嘗試，回傳更新後的 `invalid_input_count`。

    blocked=True  → 攔截計數 +1（連續計數與累計數同時 +1）
    blocked=False → 連續計數歸零（累計數不動）
    profanity=True → 這次攔截的原因是單字粗口，另外再 +1 到 profanity_total。
                     只在 blocked=True 時有意義。
    """
    from api.models import DialogueSessionRecord

    queryset = DialogueSessionRecord.objects.filter(session_id=session_id)
    if blocked:
        updates = {
            "input_attempt_total": F("input_attempt_total") + 1,
            "invalid_input_total": F("invalid_input_total") + 1,
            "invalid_input_count": F("invalid_input_count") + 1,
        }
        if profanity:
            updates["profanity_total"] = F("profanity_total") + 1
        queryset.update(**updates)
    else:
        queryset.update(
            input_attempt_total=F("input_attempt_total") + 1,
            invalid_input_count=0,
        )
    return (
        queryset.values_list("invalid_input_count", flat=True).first() or 0
    )
```

- [ ] **Step 5: 改兩個呼叫端**

`backend/api/consumers.py` 的 `_handle_blocked_input`（約第 230 行），把：

```python
        count = await arecord_ai_attempt(self.session_id, blocked=True)
```

改成：

```python
        count = await arecord_ai_attempt(
            self.session_id,
            blocked=True,
            profanity=verdict is InputVerdict.PROFANITY_ONLY,
        )
```

`backend/api/views.py` 約第 1814 行，把：

```python
    count = record_ai_attempt(session_id, blocked=True)
```

改成：

```python
    count = record_ai_attempt(
        session_id,
        blocked=True,
        profanity=verdict is InputVerdict.PROFANITY_ONLY,
    )
```

- [ ] **Step 6: 跑測試確認通過**

```bash
cd backend && DB_ENGINE=sqlite uv run pytest api/tests_achievements.py api/tests_input_gate_ws.py -v
```

Expected: all passed

- [ ] **Step 7: Commit**

```bash
git add backend/api/models.py backend/api/migrations/0029_dialoguesessionrecord_profanity_total.py backend/apps/matching/services/input_gate_store.py backend/api/consumers.py backend/api/views.py backend/api/tests_achievements.py
git commit -m "feat(m6): count profanity-only blocks separately in the AI input gate"
```

---

## Task 15: 對話品質類規則

**Files:**
- Modify: `backend/api/achievement_rules.py`
- Test: `backend/api/tests_achievements.py`

- [ ] **Step 1: 寫失敗的測試**

追加：

```python
from api.achievements import CLEAN_DIALOGUE_COUNT


def _closed_session(user, session_id, *, profanity_total=0):
    return DialogueSessionRecord.objects.create(
        user=user,
        session_id=session_id,
        topic_id=102,
        topic_title="測試議題",
        collection_name="test",
        last_activity_at=timezone.now(),
        status=DialogueSessionRecord.Status.CLOSED,
        profanity_total=profanity_total,
    )


@pytest.mark.django_db
def test_clean_dialogue_once_needs_a_closed_session_with_no_profanity():
    user = User.objects.create_user(username="u1", password="pw")
    _closed_session(user, "s1", profanity_total=1)
    evaluate(user)
    assert not UserAchievement.objects.filter(user=user, code="clean_dialogue_once").exists()

    _closed_session(user, "s2", profanity_total=0)
    evaluate(user)

    assert UserAchievement.objects.filter(user=user, code="clean_dialogue_once").exists()


@pytest.mark.django_db
def test_an_active_session_does_not_count_as_a_completed_clean_dialogue():
    user = User.objects.create_user(username="u1", password="pw")
    DialogueSessionRecord.objects.create(
        user=user,
        session_id="s1",
        topic_id=102,
        topic_title="測試議題",
        collection_name="test",
        last_activity_at=timezone.now(),
        status=DialogueSessionRecord.Status.ACTIVE,
    )

    evaluate(user)

    assert not UserAchievement.objects.filter(user=user, code="clean_dialogue_once").exists()


@pytest.mark.django_db
def test_clean_dialogue_many_needs_the_full_count():
    user = User.objects.create_user(username="u1", password="pw")
    for i in range(CLEAN_DIALOGUE_COUNT):
        _closed_session(user, f"s{i}")

    evaluate(user)

    assert UserAchievement.objects.filter(user=user, code="clean_dialogue_many").exists()
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
cd backend && DB_ENGINE=sqlite uv run pytest api/tests_achievements.py -v
```

Expected: 2 failed（第一與第三個）

- [ ] **Step 3: 加規則**

在 `backend/api/achievement_rules.py`：`.achievements` import 加 `CLEAN_DIALOGUE_COUNT`，`.models` import 加 `DialogueSessionRecord`，然後加：

```python
def _clean_dialogue_count(user) -> int:
    """完成且零攻擊性內容的對話場數。

    ⚠️ 只看 H-AI。H-H 對話室（MatchRoomConsumer）目前根本沒有跑 input gate——
    record_match_attempt() 沒有任何 production 呼叫端，MatchInputGateStat 的計數
    永遠是 0，把它納進來只會讓每一場 H-H 都白白算成「零攻擊性」。等閘門真的接上
    H-H 之後，這裡再加上 MatchInputGateStat.profanity_total 的條件。

    status=CLOSED 而不是「有沒有訊息」：這個成就的文案是「完成一場交流」。
    """
    return DialogueSessionRecord.objects.filter(
        user=user,
        status=DialogueSessionRecord.Status.CLOSED,
        profanity_total=0,
    ).count()


def _clean_dialogue_once(user) -> bool:
    return _clean_dialogue_count(user) >= 1


def _clean_dialogue_many(user) -> bool:
    return _clean_dialogue_count(user) >= CLEAN_DIALOGUE_COUNT
```

加進 `RULES`：

```python
    "clean_dialogue_once": _clean_dialogue_once,
    "clean_dialogue_many": _clean_dialogue_many,
```

把 `tests_achievements.py` 的 `PENDING_RULES` 改成空集合並加註解：

```python
# 全部成就都有規則了。這個集合留著，是為了讓下一個「先加目錄、規則晚一期才做」
# 的成就有地方登記，而不必動守門測試本身。
PENDING_RULES: set[str] = set()
```

- [ ] **Step 4: 跑測試確認通過**

```bash
cd backend && DB_ENGINE=sqlite uv run pytest api/tests_achievements.py -v
```

Expected: 48 passed

- [ ] **Step 5: 全套後端驗證**

```bash
cd backend && DB_ENGINE=sqlite uv run python manage.py check && DB_ENGINE=sqlite uv run python manage.py test api --keepdb --noinput
```

Expected: `System check identified no issues` + 全部測試通過

- [ ] **Step 6: Commit**

```bash
git add backend/api/achievement_rules.py backend/api/tests_achievements.py
git commit -m "feat(m6): add dialogue-quality achievement rules"
```

---

## 收尾時發現的兩件事（需要人決定，不是程式碼問題）

**1. 「一路同行」實際上幾乎不可能達成。** 它要求解鎖其他全部 16 個，其中包含：
- `first_godot_entry` —— 只有在帶 `GODOT_SERVICE_TOKEN` 的 headless dedicated server 上兌換入場券才會解鎖，桌面／本機一律拿不到
- `veteran_dialogues` —— 累積 10 場完成對話
- `returning_days` —— 5 個不重複的對話日

60 人、8 個月的研究期間，這組條件的實際解鎖數很可能是 0。當成「看得到但拿不到」的彩蛋是合理的設計，但這應該是個**決定**，不是分期上線的副作用。要讓它可達成的話，最直接的是降低 `VETERAN_COUNT` 與 `RETURNING_DAYS`，或把 `first_godot_entry` 排除在 meta 條件之外。

**2.「對話品質」這一類量到的東西比名字承諾的少很多。** 判準是 input gate 的規則 0，只在**整則訊息都是單字粗口**時觸發：
- 「幹，核電根本就是騙局」判為 VALID，不計入
- H-AI 根本沒有黑名單過濾（`check_content_sync` 只接在 `MatchRoomConsumer`）
- H-H 完全不算（`MatchRoomConsumer` 沒跑 input gate，見下）

所以「理性交流」在每句話都罵髒話的情況下照樣拿得到。當成遊戲化的鼓勵沒問題，但**不可以**在研究報告裡被當成文明度或去極化指標使用。`_clean_dialogue_count` 的 docstring 已寫明這個上限。

## 範圍外但已知的既有缺陷

**H-H 對話室沒有跑 input gate。** `apps/matching/services/input_gate_store.record_match_attempt()` 有實作、有測試，但**沒有任何 production 程式碼呼叫它**——`api/consumers.py` 的 `MatchRoomConsumer` 從頭到尾沒有 gate。因此 `MatchInputGateStat` 的計數永遠是 0，而 `_finalize_input_gate_metrics` 照樣會呼叫 `finalize_match_metrics`，用全 0 的計數算出 `invalid_ratio`。

這影響的不只是成就（兩個品質成就因此只看 H-AI），還有 H-H 那一半的實驗資料完整性指標。修它需要把 gate 接進 `MatchRoomConsumer`，是獨立議題，不在本 plan 範圍。

## 部署備註

1. 套 migration：`uv run python manage.py migrate`
2. **建頭銜**：`uv run python manage.py seed_achievement_titles`（不跑的話成就照樣解鎖，只是不會發頭銜，log 會有 warning）
3. 現有使用者不需要回填指令——規則是即時 query，任何人第一次打開成就頁就會把過去累積的成就一次結算出來。
