"""Delete expired date-partitioned runtime logs."""

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from shared.logger import cleanup_old_logs, project_log_root


class Command(BaseCommand):
    help = "按 LOG_RETENTION_DAYS 清理过期运行日志。"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--retention-days", type=int)

    def handle(self, *args, **options):
        retention_days = (
            options["retention_days"]
            if options["retention_days"] is not None
            else settings.LOG_RETENTION_DAYS
        )
        removed = cleanup_old_logs(
            project_log_root(settings.PROJECT_ROOT),
            retention_days=retention_days,
            today=timezone.localdate(),
            dry_run=options["dry_run"],
        )
        verb = "将清理" if options["dry_run"] else "已清理"
        self.stdout.write(f"{verb} {len(removed)} 个过期日志目录。")
        for path in removed:
            self.stdout.write(f"  {path.name}")
