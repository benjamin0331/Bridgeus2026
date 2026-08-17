"""建立成就目錄裡用到的所有頭銜，並回填既有解鎖缺的頭銜。

頭銜定義（名稱）跟著成就目錄走，但 Title 是資料表——需要一支指令把兩邊對齊。
get_or_create 所以可以重複跑；已存在的頭銜不會被改動（顏色可能已被研究者在
admin 調過，不該被 seed 覆寫）。

**回填是必要的而不是順手做的**：achievement_rules.evaluate() 只會對「從未解鎖
過」的成就呼叫 _grant_titles，所以在這支指令執行前就解鎖成就的使用者，頭銜不會
自己補回來。回填放在這裡而不是 evaluate 裡，是因為這支指令本來就是「把目錄與
資料表對齊」的地方，而且維持冪等。

部署後執行一次；之後每次改動成就目錄的頭銜對應也要再跑一次：
    uv run python manage.py seed_achievement_titles
"""

from django.core.management.base import BaseCommand

from api.achievements import CATALOG, CATALOG_BY_CODE
from api.models import Title, UserAchievement, UserTitle


class Command(BaseCommand):
    help = "建立成就目錄裡用到的所有 Title，並回填既有解鎖缺的 UserTitle（可重複執行）"

    def handle(self, *args, **options):
        names = sorted({d.title_name for d in CATALOG if d.title_name})
        created = 0
        for name in names:
            _, was_created = Title.objects.get_or_create(name=name)
            if was_created:
                created += 1

        backfilled = self._backfill_user_titles()

        self.stdout.write(
            self.style.SUCCESS(
                f"頭銜共 {len(names)} 個，新建 {created} 個；回填 {backfilled} 筆使用者頭銜。"
            )
        )

    def _backfill_user_titles(self) -> int:
        """把既有 UserAchievement 對應但還沒授予的頭銜補上。

        刻意不設 is_selected（同 achievement_rules._grant_titles 的理由：
        UserTitle 有「一位使用者最多一個 is_selected」的 partial unique index，
        而且掛哪一個頭銜該由玩家自己決定）。

        ignore_conflicts=True 讓重複執行安全：已經擁有的 (user, title) 會被
        user_title_unique 擋掉而不是丟例外。代價是回傳的「回填數」是嘗試數而非
        實際新建數，所以下面先扣掉已擁有的組合再建立。
        """
        titles = {t.name: t.id for t in Title.objects.all()}
        owned = set(UserTitle.objects.values_list("user_id", "title_id"))

        wanted: set[tuple[int, int]] = set()
        for user_id, code in UserAchievement.objects.values_list("user_id", "code"):
            definition = CATALOG_BY_CODE.get(code)
            if definition is None or not definition.title_name:
                continue          # 目錄裡已移除的舊 code，或本來就沒有頭銜的成就
            title_id = titles.get(definition.title_name)
            if title_id is None:
                continue          # 理論上不會發生：上面剛把目錄裡的頭銜都建好了
            if (user_id, title_id) in owned:
                continue
            wanted.add((user_id, title_id))

        if not wanted:
            return 0

        UserTitle.objects.bulk_create(
            [
                UserTitle(user_id=user_id, title_id=title_id)
                for user_id, title_id in sorted(wanted)
            ],
            ignore_conflicts=True,
        )
        return len(wanted)
