"""把「該關而沒關」的 AI 對話 session 標記為 closed。

判定規則見 api/session_cleanup.py（與 migration 0018 共用同一份邏輯）。

0018 已經在 migrate 時清過一次既有資料，這個指令是給日後排查用的：想確認
現在還有沒有殘留、或修好新的漏關路徑後要再清一次時，可以隨時重跑。沒有東西
要關時是 no-op。

用法（在 backend/ 底下）：
    python manage.py close_stale_dialogue_sessions --dry-run   # 只列出，不寫入
    python manage.py close_stale_dialogue_sessions
"""

from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.utils import timezone

from api.models import DialogueSessionRecord, PostDialogueResponse
from api.session_cleanup import (
    find_abandoned_session_ids,
    find_closable_session_ids,
)


class Command(BaseCommand):
    help = "關閉已結束或已被新對話取代、但 status 仍停在 active 的 AI 對話 session。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="只印出會被關閉的 session，不實際寫入資料庫。",
        )

    def handle(self, *args, **options):
        closable = find_closable_session_ids(
            DialogueSessionRecord=DialogueSessionRecord,
            PostDialogueResponse=PostDialogueResponse,
        )
        # 進了房沒開口留下的空 session。跟上面那組分開算，數量與原因不同，
        # 印出來時也分開列，才看得出這次清掉的是哪一類。
        abandoned = [
            session_id
            for session_id in find_abandoned_session_ids(
                DialogueSessionRecord=DialogueSessionRecord,
                now=timezone.now(),
            )
            if session_id not in set(closable)
        ]
        closable = list(closable) + abandoned

        if not closable:
            self.stdout.write(self.style.SUCCESS("沒有需要關閉的 session。"))
            return

        abandoned_set = set(abandoned)
        for session_id in closable:
            record = DialogueSessionRecord.objects.get(session_id=session_id)
            reason = "空對話" if session_id in abandoned_set else "已結束"
            self.stdout.write(
                f"  [{reason}] user={record.user_id} topic={record.topic_id} "
                f"session={session_id[:12]} last_activity={record.last_activity_at}"
            )

        if options["dry_run"]:
            self.stdout.write(
                self.style.WARNING(f"[dry-run] 會關閉 {len(closable)} 筆，未寫入。")
            )
            return

        updated = DialogueSessionRecord.objects.filter(
            session_id__in=closable,
            status=DialogueSessionRecord.Status.ACTIVE,
        ).update(status=DialogueSessionRecord.Status.CLOSED)
        # 快取沒清的話，_restore_dialogue_session_record_for_user 會直接吃到快取
        # 命中而略過 status 檢查，剛關掉的 session 照樣能被恢復。
        cache.delete_many([f"dialogue_session:{s}" for s in closable])

        self.stdout.write(self.style.SUCCESS(f"已關閉 {updated} 筆 session。"))
