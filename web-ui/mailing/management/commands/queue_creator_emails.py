from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Q

from tasks.models import ImportTask

from mailing.services.queue import queue_import_creators


class Command(BaseCommand):
    help = "从一个达人导入批次创建全局防重邮件队列。"

    def add_arguments(self, parser):
        parser.add_argument("--import-task-id")
        parser.add_argument(
            "--limit",
            type=int,
            default=settings.CREATOR_EMAIL_DAILY_LIMIT,
        )
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--retry-failed",
            action="store_true",
            help="重新入队未超过最大尝试次数的失败记录。",
        )

    def handle(self, *args, **options):
        if options["import_task_id"] is None:
            tasks = (
                ImportTask.objects.filter(
                    status__in=[
                        ImportTask.Status.SUCCESS,
                        ImportTask.Status.PARTIAL_SUCCESS,
                    ]
                )
                .annotate(
                    email_creator_count=Count(
                        "imported_creators",
                        filter=~Q(imported_creators__creator__email=""),
                    )
                )
                .filter(email_creator_count__gt=0)
                .order_by("-created_at")[:20]
            )
            if not tasks:
                self.stdout.write("当前没有包含达人邮箱的导入批次。")
                return
            self.stdout.write("包含达人邮箱的最近导入批次：")
            for task in tasks:
                self.stdout.write(
                    f"  ID {task.pk} · "
                    f"{task.email_creator_count} 个邮箱 · "
                    f"{task.file_name} / {task.sheet_name}"
                )
            self.stdout.write(
                "使用 --import-task-id <UUID> 选择一个导入批次。"
            )
            return
        try:
            import_task = ImportTask.objects.get(
                pk=options["import_task_id"]
            )
        except (ImportTask.DoesNotExist, ValueError) as error:
            raise CommandError("指定导入批次不存在。") from error
        try:
            result = queue_import_creators(
                import_task=import_task,
                limit=options["limit"],
                retry_failed=options["retry_failed"],
                dry_run=options["dry_run"],
            )
        except ValueError as error:
            raise CommandError(str(error)) from error

        prefix = "预览" if options["dry_run"] else "入队"
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}完成：待新增 {result.queued}，"
                f"重新入队 {result.requeued}，"
                f"已在队列 {result.already_pending}，"
                f"已成功防重 {result.already_sent}，"
                f"发送中 {result.sending}，"
                f"失败未重试 {result.failed_not_retried}，"
                f"已耗尽尝试 {result.exhausted}，"
                f"今日已达上限 {result.deferred_today}，"
                f"无效/空邮箱 {result.invalid_email}。"
            )
        )
