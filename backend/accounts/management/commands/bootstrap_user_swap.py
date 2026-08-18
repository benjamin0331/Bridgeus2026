"""prod 專用：在切換 AUTH_USER_MODEL 之後、執行 migrate 之前跑一次。

為什麼需要這支指令：api/migrations/0001_initial.py 透過 swappable_dependency
宣告依賴，切換設定後會解析成 accounts.0001_initial。prod 上前者已套用、
後者未套用，check_consistent_history 會拋 InconsistentMigrationHistory。

為什麼不能用 migrate --fake：check_consistent_history 在
django/core/management/commands/migrate.py:118 是無條件執行的，早於 --fake
的任何處理，所以 --fake 自己也跑不起來。這是 chicken-and-egg，只能繞過
migrate 直接寫紀錄列。

全新資料庫（dev / CI / 測試）不需要這一步——accounts.0001_initial 會照正常
順序真正執行。本指令偵測到紀錄已存在時不做任何事，可安全重複執行。
"""

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder

APP_LABEL = "accounts"
MIGRATION_NAME = "0001_initial"


class Command(BaseCommand):
    help = (
        "把 accounts.0001_initial 標記為已套用，讓 migrate 能在既有資料庫上啟動。"
        "只在既有資料庫需要；全新資料庫上為 no-op。"
    )

    def handle(self, *args, **options):
        recorder = MigrationRecorder(connection)
        recorder.ensure_schema()

        exists = recorder.migration_qs.filter(
            app=APP_LABEL, name=MIGRATION_NAME
        ).exists()

        if exists:
            self.stdout.write(
                f"{APP_LABEL}.{MIGRATION_NAME} 的紀錄已存在，不做任何事。"
            )
            return

        recorder.record_applied(APP_LABEL, MIGRATION_NAME)
        self.stdout.write(
            self.style.SUCCESS(
                f"已寫入 {APP_LABEL}.{MIGRATION_NAME} 的套用紀錄，現在可以執行 migrate。"
            )
        )
