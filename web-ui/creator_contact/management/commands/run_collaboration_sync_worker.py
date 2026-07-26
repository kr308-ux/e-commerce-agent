import time

from django.core.management.base import BaseCommand
from django.db import transaction

from creator_contact.models import CollaborationSyncJob
from creator_contact.services.collaboration_sync import (
    CollaborationSyncRunner,
)


class Command(BaseCommand):
    help = "轮询并同步紫鸟店铺的进行中定向合作。"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll-interval", type=float, default=2.0)

    def handle(self, *args, **options):
        while True:
            with transaction.atomic():
                job = (
                    CollaborationSyncJob.objects
                    .select_for_update()
                    .filter(status=CollaborationSyncJob.Status.PENDING)
                    .order_by("created_at")
                    .first()
                )
                if job is not None:
                    job.status = CollaborationSyncJob.Status.RUNNING
                    job.save(update_fields=["status", "updated_at"])
            if job is None:
                if options["once"]:
                    self.stdout.write("没有待执行的定向合作同步任务。")
                    return
                time.sleep(options["poll_interval"])
                continue

            result = CollaborationSyncRunner(job).run()
            self.stdout.write(
                f"同步任务 {result.pk} 完成，状态：{result.status}"
            )
            if options["once"]:
                return
