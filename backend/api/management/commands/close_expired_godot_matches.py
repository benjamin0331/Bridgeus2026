"""收掉逾期而且沒人在照顧的 Godot 綁定配對房。

為什麼需要這支：問卷階段的裁決（resolve_godot_survey_gate）是輪詢驅動的，兩位
參與者都關掉網頁時沒有人觸發，房間會一直掛在 ACTIVE。另外還有一種孤兒房——
Godot 端建房成功但座位驗證失敗（有人中途取消配對），那種房從來沒有人進來過。

用法（在 backend/ 底下）：
    python manage.py close_expired_godot_matches --dry-run
    python manage.py close_expired_godot_matches

建議每分鐘跑一次。指令是冪等的，沒有東西要收時是 no-op。
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from api.godot_binding import godot_binding_info, match_pretest_state, survey_deadline_of
from api.models import DialogueMatch
from api.views import GODOT_SURVEY_WINDOW_SECONDS
from apps.matching.services.matcher import resolve_godot_survey_gate


class Command(BaseCommand):
    help = "收掉逾期而且沒人輪詢的 Godot 綁定配對房（問卷階段裁決的輪詢死角兜底）。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="只印出會被收掉的房，不實際呼叫裁決。",
        )

    def handle(self, *args, **options):
        now = timezone.now()
        candidates = []  # list of (match, deadline, used_fallback)

        for match in DialogueMatch.objects.filter(status=DialogueMatch.Status.ACTIVE):
            binding = godot_binding_info(match)
            if binding is None:
                continue  # 一般配對房，不歸這支指令管。

            pretest = match_pretest_state(match)
            if pretest["both_done"]:
                # 都填完了就進聊天室，交給既有的 close_match_if_idle 處理。
                continue

            deadline = survey_deadline_of(match)
            used_fallback = False
            if deadline is None:
                # 理論上不該發生（binding 一律會記 survey_deadline），但用備援
                # 判斷兜底，避免這種孤兒房永遠留在 ACTIVE。
                deadline = match.created_at + timedelta(
                    seconds=GODOT_SURVEY_WINDOW_SECONDS
                )
                used_fallback = True

            if now > deadline:
                candidates.append((match, deadline, used_fallback))

        if not candidates:
            self.stdout.write(self.style.SUCCESS("沒有需要收掉的 Godot 房。"))
            return

        for match, deadline, used_fallback in candidates:
            fallback_note = "（備援判斷：無 survey_deadline，改用建房時間推算）" if used_fallback else ""
            self.stdout.write(
                f"  room={match.room_id} topic={match.topic_id} "
                f"deadline={deadline.isoformat()}{fallback_note}"
            )

        if options["dry_run"]:
            self.stdout.write(
                self.style.WARNING(f"[dry-run] 會收掉 {len(candidates)} 間房，未寫入。")
            )
            return

        closed = 0
        for match, _deadline, _used_fallback in candidates:
            resolved = resolve_godot_survey_gate(match=match, now=now)
            if resolved.status == DialogueMatch.Status.CANCELLED:
                closed += 1

        self.stdout.write(self.style.SUCCESS(f"已收掉 {closed} 間房。"))
