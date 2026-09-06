"""把「改動上線前就已完成」的人對 AI（H-AI）對話補跑一次 M6 觀點知識庫 pipeline。

M6 的 H-AI 觸發點是「對話後問卷送出 → session 轉 CLOSED」
（api.views._close_dialogue_session_record 的 transaction.on_commit）。這個掛勾
是這次改動才加的，所以在那之前就完成的 H-AI 對話從來沒觸發過 pipeline，
研究者的審核佇列裡看不到任何 H-AI 觀點。這個指令對「已完成」的 H-AI session
逐一補跑一次。

「已完成」= 該 session 有一筆未被取代的後測問卷
（PostDialogueResponse, experiment_condition='ai', is_superseded=False），跟正式
觸發點用的是同一個訊號。

安全性：run_pipeline_for_session() 自己會擋掉「這個 session 已經有
DialogueSummary」的情況並回傳 0，所以這個指令可以重複跑，不會產生重複資料。

用法（在 backend/ 底下）：
    python manage.py backfill_hai_viewpoints --dry-run   # 只列出會處理哪些 session
    python manage.py backfill_hai_viewpoints
    python manage.py backfill_hai_viewpoints --topic-id 103
"""

from django.core.management.base import BaseCommand

from api.models import DialogueSessionRecord, PostDialogueResponse
from apps.summary.models import DialogueSummary
from apps.summary.pipeline.assemble import run_pipeline_for_session


class Command(BaseCommand):
    help = "對改動上線前已完成的 H-AI 對話補跑 M6 觀點知識庫 pipeline。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="只印出會處理哪些 session，不實際寫入。",
        )
        parser.add_argument(
            "--topic-id",
            type=int,
            default=None,
            help="只處理指定議題的 session（預設全部）。",
        )

    def handle(self, *args, **options):
        completed_qs = PostDialogueResponse.objects.filter(
            experiment_condition=PostDialogueResponse.ExperimentCondition.AI,
        ).exclude(session_id__isnull=True).exclude(session_id="")
        completed_session_ids = sorted(
            set(completed_qs.values_list("session_id", flat=True))
        )

        records = DialogueSessionRecord.objects.filter(
            session_id__in=completed_session_ids
        )
        if options["topic_id"] is not None:
            records = records.filter(topic_id=options["topic_id"])
        records = list(records.order_by("topic_id", "id"))

        if not records:
            self.stdout.write(self.style.SUCCESS("沒有符合條件的已完成 H-AI session。"))
            return

        already_done = set(
            DialogueSummary.objects.filter(
                dialogue_id__in=[r.session_id for r in records]
            ).values_list("dialogue_id", flat=True)
        )

        pending = [r for r in records if r.session_id not in already_done]
        self.stdout.write(
            f"已完成 H-AI session：{len(records)} 筆；"
            f"已有 DialogueSummary（將略過）：{len(already_done)} 筆；"
            f"待補跑：{len(pending)} 筆。"
        )
        for r in pending:
            self.stdout.write(
                f"  user={r.user_id} topic={r.topic_id} "
                f"session={r.session_id[:12]} status={r.status}"
            )

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("[dry-run] 未寫入。"))
            return

        total_sessions_with_output = 0
        total_nodes = 0
        for r in pending:
            written = run_pipeline_for_session(r.session_id)
            total_nodes += written
            if written:
                total_sessions_with_output += 1
            self.stdout.write(
                f"  session={r.session_id[:12]} topic={r.topic_id} -> {written} 筆觀點"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"補跑完成：{len(pending)} 個 session，其中 "
                f"{total_sessions_with_output} 個產生觀點，共寫入 {total_nodes} 筆 "
                f"ViewpointNode（review_status=pending，等待人工終審）。"
            )
        )
