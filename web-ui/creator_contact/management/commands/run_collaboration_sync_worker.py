import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from creator_contact.models import CollaborationSyncJob
from creator_contact.services.collaboration_sync import (
    CollaborationSyncRunner,
)
from shared.db import retry_locked_database_operation


class Command(BaseCommand):
    help = "轮询并同步紫鸟店铺的进行中定向合作。"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll-interval", type=float, default=2.0)

    def handle(self, *args, **options):
        while True:
            def claim_job():
                candidate = (
                    CollaborationSyncJob.objects
                    .filter(status=CollaborationSyncJob.Status.PENDING)
                    .order_by("created_at")
                    .first()
                )
                if candidate is None:
                    return None
                claimed = CollaborationSyncJob.objects.filter(
                    pk=candidate.pk,
                    status=CollaborationSyncJob.Status.PENDING,
                ).update(
                    status=CollaborationSyncJob.Status.RUNNING,
                    updated_at=timezone.now(),
                )
                if claimed != 1:
                    return None
                return CollaborationSyncJob.objects.get(pk=candidate.pk)

            job = retry_locked_database_operation(claim_job)
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
            if result.status == CollaborationSyncJob.Status.PENDING:
                time.sleep(options["poll_interval"])
