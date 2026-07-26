import time

from django.core.management.base import BaseCommand
from django.db import transaction

from tasks.models import CreatorAcquisitionTask
from tasks.services.opencode_runner import OpenCodeTaskRunner


class Command(BaseCommand):
    help = "轮询并执行“获取达人数据”任务。"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="执行一个任务后退出。")
        parser.add_argument(
            "--poll-interval",
            type=float,
            default=2.0,
            help="无任务时的轮询秒数。",
        )

    def handle(self, *args, **options):
        while True:
            with transaction.atomic():
                task = (
                    CreatorAcquisitionTask.objects
                    .select_for_update()
                    .filter(status=CreatorAcquisitionTask.Status.PENDING)
                    .order_by("created_at")
                    .first()
                )
                if task is not None:
                    task.status = CreatorAcquisitionTask.Status.RUNNING
                    task.current_step = "Agent Worker 已领取任务"
                    task.save(update_fields=["status", "current_step", "updated_at"])
            if task is None:
                if options["once"]:
                    self.stdout.write("没有待执行的获取达人数据任务。")
                    return
                time.sleep(options["poll_interval"])
                continue

            self.stdout.write(f"开始任务 {task.id}（{task.product_limit} 个商品）")
            result = OpenCodeTaskRunner(task).run()
            self.stdout.write(
                f"任务 {result.id} 完成，状态：{result.status}，进度：{result.progress}%"
            )
            if options["once"]:
                return
