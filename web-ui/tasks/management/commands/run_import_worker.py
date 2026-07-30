"""Poll and execute confirmed creator spreadsheet imports."""

import time

from django.core.management.base import BaseCommand
from django.db import transaction

from tasks.models import ImportTask
from tasks.services.import_runner import cleanup_stale_sources, run_import_task


class Command(BaseCommand):
    help = "轮询并执行已确认的达人表格导入任务。"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="执行一个任务后退出。")
        parser.add_argument(
            "--poll-interval",
            type=float,
            default=2.0,
            help="无任务时的轮询秒数。",
        )

    def handle(self, *args, **options):
        removed = cleanup_stale_sources()
        if removed:
            self.stdout.write(f"已清理 {removed} 个过期导入临时目录。")
        while True:
            with transaction.atomic():
                task = (
                    ImportTask.objects.select_for_update()
                    .filter(status=ImportTask.Status.QUEUED)
                    .order_by("created_at")
                    .first()
                )
                if task is not None:
                    task.status = ImportTask.Status.IMPORTING
                    task.current_step = "导入 Worker 已领取任务"
                    task.save(
                        update_fields=["status", "current_step", "updated_at"]
                    )
            if task is None:
                if options["once"]:
                    self.stdout.write("没有待执行的达人导入任务。")
                    return
                time.sleep(options["poll_interval"])
                continue

            self.stdout.write(f"开始导入任务 {task.pk}（{task.file_name}）")
            result = run_import_task(task)
            self.stdout.write(
                f"任务 {result.pk} 完成，状态：{result.status}，"
                f"成功 {result.success_rows}，部分成功 "
                f"{result.partial_success_rows}，失败 {result.failed_rows}"
            )
            if options["once"]:
                return
