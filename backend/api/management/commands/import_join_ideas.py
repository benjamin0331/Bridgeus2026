"""把公共政策網路參與平臺（join.gov.tw）的爬蟲 JSON 匯入 PolicyIdea。

用法（在 backend/ 底下）：
    uv run python manage.py import_join_ideas --dry-run
    uv run python manage.py import_join_ideas
    uv run python manage.py import_join_ideas data/join_ideas/join_ideas_XXXX.json

不給路徑時取 `data/join_ideas/` 裡檔名最新的那一份（爬蟲輸出檔名帶時間戳，
所以字典序就是時間序）。

**每個 section 是整批換掉，不是累加**：JSON 裡沒出現的舊資料會被刪掉。
爬蟲重跑之後「熱門前五名」必須真的是新的五名，留著上一輪的殘骸會讓讀取端
看到六筆、七筆，還得自己判斷哪些是舊的——那個判斷沒有依據可用。
所以刪除是這支指令的功能，不是副作用。

指令是冪等的：同一份 JSON 跑兩次結果一樣。
"""
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from api.models import PolicyIdea

DEFAULT_DIR = Path("data/join_ideas")

# JSON 的區塊名稱（中文）→ 模型的 section 值。爬蟲輸出用中文 key，資料表用英文
# 值，對照只放這一份。JSON 裡出現對照表以外的 key 會被忽略並在輸出裡點名，
# 不是靜默跳過——爬蟲多抓了一個區塊而沒人發現，是最容易漏掉的那種變更。
SECTION_MAP = {
    "熱門": PolicyIdea.Section.HOT,
    "最新": PolicyIdea.Section.LATEST,
}

# JSON 裡的時間都是沒有時區的字串（"2026-07-20 17:50:05"），而它們是 join.gov.tw
# 這個台灣網站上的台灣時間。專案的 TIME_ZONE 是 UTC，所以不能用
# timezone.make_aware() 的預設值——那會把台北時間當成 UTC，整批資料的時間全部
# 早八小時。要用來源自己的時區補。
SOURCE_TIMEZONE = ZoneInfo("Asia/Taipei")


class Command(BaseCommand):
    help = "匯入 join.gov.tw 提案快照（每個 section 整批換掉，可重複執行）"

    def add_arguments(self, parser):
        parser.add_argument(
            "path",
            nargs="?",
            default=None,
            help=f"爬蟲 JSON 路徑；省略時取 {DEFAULT_DIR}/ 裡最新的一份。",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="只印出會匯入什麼，不寫入資料庫。",
        )

    def handle(self, *args, **options):
        path = self._resolve_path(options["path"])
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError(f"讀不到或解不開 {path}：{exc}") from exc

        fetched_at = self._parse_fetched_at(payload.get("fetchedAt"))
        self.stdout.write(f"來源：{path}（fetchedAt={fetched_at.isoformat()}）")

        unknown = [k for k in payload if isinstance(payload[k], dict) and k not in SECTION_MAP]
        if unknown:
            self.stdout.write(
                self.style.WARNING(f"忽略未知區塊：{'、'.join(unknown)}（對照表在 SECTION_MAP）")
            )

        plan = {}
        for raw_section, section in SECTION_MAP.items():
            block = payload.get(raw_section)
            if not isinstance(block, dict):
                continue
            ideas = block.get("ideas")
            if not isinstance(ideas, list):
                continue
            plan[section] = [
                self._to_row(item, section=section, rank=rank, fetched_at=fetched_at)
                for rank, item in enumerate(ideas)
                if isinstance(item, dict) and item.get("id")
            ]

        if not plan:
            raise CommandError("JSON 裡沒有任何可匯入的區塊（預期 熱門／最新）。")

        for section, rows in plan.items():
            self.stdout.write(f"  {section}：{len(rows)} 筆")
            for row in rows:
                self.stdout.write(f"    #{row['rank']} {row['title'][:40]}")

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("[dry-run] 未寫入。"))
            return

        with transaction.atomic():
            for section, rows in plan.items():
                keep = [row["external_id"] for row in rows]
                # 先刪掉這次沒出現的，再 upsert 留下的。順序反過來也可以，但這樣
                # 中間狀態不會出現「同一個 rank 有兩筆」。
                PolicyIdea.objects.filter(section=section).exclude(
                    external_id__in=keep
                ).delete()
                for row in rows:
                    # 複製再 pop：plan 裡那份 row 上面已經印過了，就地改掉它會讓
                    # 這支指令變成「只能跑一次」的那種函式。
                    defaults = dict(row)
                    external_id = defaults.pop("external_id")
                    PolicyIdea.objects.update_or_create(
                        section=section,
                        external_id=external_id,
                        defaults=defaults,
                    )

        total = sum(len(rows) for rows in plan.values())
        self.stdout.write(self.style.SUCCESS(f"已匯入 {total} 筆。"))

    def _resolve_path(self, given) -> Path:
        if given:
            path = Path(given)
            if not path.is_file():
                raise CommandError(f"找不到檔案：{path}")
            return path
        candidates = sorted(DEFAULT_DIR.glob("*.json"))
        if not candidates:
            raise CommandError(
                f"{DEFAULT_DIR}/ 裡沒有 JSON。把爬蟲輸出放進去，或直接把路徑當參數傳。"
            )
        return candidates[-1]

    def _parse_fetched_at(self, raw):
        """fetchedAt 是 'YYYY-MM-DD HH:MM:SS'（無時區）。缺或壞就退回現在——
        寧可記一個偏晚的時間，也不要因為這個欄位讓整批匯入失敗。"""
        parsed = parse_datetime(raw) if isinstance(raw, str) else None
        if parsed is None:
            return timezone.now()
        if timezone.is_naive(parsed):
            return timezone.make_aware(parsed, SOURCE_TIMEZONE)
        return parsed

    def _to_row(self, item, *, section, rank, fetched_at) -> dict:
        publish_date = item.get("publishDate")
        parsed_publish = (
            parse_datetime(publish_date) if isinstance(publish_date, str) else None
        )
        if parsed_publish is not None and timezone.is_naive(parsed_publish):
            parsed_publish = timezone.make_aware(parsed_publish, SOURCE_TIMEZONE)
        return {
            "external_id": str(item["id"]),
            "rank": rank,
            # 平臺上的標題偶爾夾著換行與尾空白（實測過），進資料表前先正規化，
            # 免得每個消費者各自 strip 一次。
            "title": " ".join(str(item.get("title", "")).split())[:300],
            "outline": str(item.get("outline", "") or ""),
            "url": str(item.get("url", "") or "")[:500],
            "endorse_count": max(0, int(item.get("endorseCount") or 0)),
            "endorse_goal": max(0, int(item.get("endorseGoal") or 0)),
            "categories": item.get("categories") or [],
            "organizations": item.get("organizations") or [],
            "publish_date": parsed_publish,
            "fetched_at": fetched_at,
        }
