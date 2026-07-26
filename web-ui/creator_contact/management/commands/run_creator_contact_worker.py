import time

from django.core.management.base import BaseCommand
from django.db import transaction

from creator_contact.models import CreatorContactTask
from creator_contact.services.contact_runner import CreatorContactRunner


class Command(BaseCommand):
    help = "轮询并串行执行达人联系任务。"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll-interval", type=float, default=2.0)

    def handle(self, *args, **options):
        while True:
            with transaction.atomic():
                task = (
                    CreatorContactTask.objects
                    .select_for_update()
                    .filter(status=CreatorContactTask.Status.PENDING)
                    .order_by("created_at")
                    .first()
                )
                if task is not None:
                    task.status = CreatorContactTask.Status.RUNNING
                    task.current_step = "达人联系 Worker 已领取任务"
                    task.save(
                        update_fields=[
                            "status",
                            "current_step",
                            "updated_at",
                        ]
                    )
            if task is None:
                if options["once"]:
                    self.stdout.write("没有待执行的达人联系任务。")
                    return
                time.sleep(options["poll_interval"])
                continue

            self.stdout.write(f"开始达人联系任务 {task.pk}")
            result = CreatorContactRunner(task).run()
            self.stdout.write(
                f"任务 {result.pk} 完成，状态：{result.status}"
            )
            if options["once"]:
                return
