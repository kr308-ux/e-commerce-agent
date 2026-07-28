import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from creator_contact.models import CreatorContactTask
from creator_contact.services.contact_runner import CreatorContactRunner
from creator_contact.services.worker_runtime import (
    WORKER_CLAIMED_STEP,
    WORKER_WAITING_STEP,
    WorkerHealthServer,
    worker_endpoint_ready,
)


class Command(BaseCommand):
    help = "轮询并串行执行达人联系任务。"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll-interval", type=float, default=2.0)
        parser.add_argument("--task-id")
        parser.add_argument("--claimed-by-server", action="store_true")
        parser.add_argument("--server-mode", action="store_true")
        parser.add_argument(
            "--control-host",
            default=settings.CREATOR_CONTACT_WORKER_HOST,
        )
        parser.add_argument(
            "--control-port",
            type=int,
            default=settings.CREATOR_CONTACT_WORKER_PORT,
        )

    def handle(self, *args, **options):
        health_server = None
        if options["server_mode"]:
            host = options["control_host"]
            port = options["control_port"]
            if worker_endpoint_ready(host, port):
                self.stdout.write(
                    f"常驻达人联系 Worker 已在 {host}:{port} 运行。"
                )
                return
            try:
                health_server = WorkerHealthServer(host, port)
            except OSError as error:
                raise RuntimeError(
                    f"无法监听达人联系 Worker 固定端口 {host}:{port}。"
                ) from error
            health_server.start()
            self.stdout.write(
                f"常驻达人联系 Worker 已监听 {host}:{port}"
            )

        try:
            while True:
                with transaction.atomic():
                    tasks = CreatorContactTask.objects.select_for_update()
                    if options["task_id"]:
                        tasks = tasks.filter(pk=options["task_id"])
                        if options["claimed_by_server"]:
                            tasks = tasks.filter(
                                status=CreatorContactTask.Status.RUNNING,
                                current_step=WORKER_WAITING_STEP,
                            )
                        else:
                            tasks = tasks.filter(
                                status=CreatorContactTask.Status.PENDING
                            )
                        task = tasks.first()
                    elif options["server_mode"]:
                        task = (
                            tasks
                            .filter(
                                status=CreatorContactTask.Status.RUNNING,
                                current_step=WORKER_WAITING_STEP,
                            )
                            .order_by("created_at")
                            .first()
                        )
                    else:
                        task = (
                            tasks
                            .filter(status=CreatorContactTask.Status.PENDING)
                            .order_by("created_at")
                            .first()
                        )
                    if task is not None:
                        task.status = CreatorContactTask.Status.RUNNING
                        task.current_step = (
                            WORKER_CLAIMED_STEP
                            if (
                                options["claimed_by_server"]
                                or options["server_mode"]
                            )
                            else "达人联系 Worker 已领取任务"
                        )
                        task.save(
                            update_fields=[
                                "status",
                                "current_step",
                                "updated_at",
                            ]
                        )
                if task is None:
                    if options["once"]:
                        self.stdout.write(
                            "没有符合条件的待执行达人联系任务。"
                        )
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
        finally:
            if health_server is not None:
                health_server.close()
