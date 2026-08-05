"""Supervise the local Django UI and its serial workers."""

from __future__ import annotations

import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from shared.file_lock import SingleInstanceLock
from shared.logger import cleanup_old_logs, project_log_root
from shared.processes import (
    new_process_group_kwargs,
    terminate_process_tree,
)
from shared.runtime_commands import django_command, runtime_cwd
from shared.windows_job import WindowsKillOnCloseJob
from creator_contact.services.worker_runtime import worker_endpoint_ready
from mailing.services.worker_runtime import email_worker_endpoint_ready


@dataclass
class ManagedService:
    name: str
    arguments: tuple[str, ...]
    process: subprocess.Popen | None = None
    started_at: float = 0
    failures: int = 0
    restart_at: float = 0
    health_check: Callable[[], bool] | None = None
    unhealthy_checks: int = 0

    def command(self) -> list[str]:
        return django_command(*self.arguments)


class Command(BaseCommand):
    help = "启动并守护 Django、导入、联系、同步和邮件 Worker。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--address",
            default="127.0.0.1:8000",
            help="Django runserver 地址。",
        )
        parser.add_argument(
            "--skip-browser-prepare",
            action="store_true",
            help="仅用于离线测试，不预启动紫鸟。",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="启动后立即走一遍优雅退出，仅用于测试。",
        )

    def handle(self, *args, **options):
        lock_path = (
            Path(settings.PROJECT_ROOT)
            / "temporary"
            / "runtime-supervisor.lock"
        )
        lock = SingleInstanceLock(lock_path)
        if not lock.acquire():
            raise CommandError("系统已经启动，请不要重复打开。")

        stop_event = threading.Event()
        previous_handlers: dict[int, object] = {}

        def request_stop(_signum=None, _frame=None):
            stop_event.set()

        signal_names = ["SIGINT", "SIGTERM"]
        if hasattr(signal, "SIGBREAK"):
            signal_names.append("SIGBREAK")
        for name in signal_names:
            signum = getattr(signal, name)
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, request_stop)

        job = WindowsKillOnCloseJob()
        services = [
            ManagedService(
                "Django",
                ("runserver", options["address"], "--noreload"),
            ),
            ManagedService("导入 Worker", ("run_import_worker",)),
            ManagedService(
                "达人联系 Worker",
                ("run_creator_contact_worker", "--server-mode"),
                health_check=lambda: worker_endpoint_ready(
                    settings.CREATOR_CONTACT_WORKER_HOST,
                    settings.CREATOR_CONTACT_WORKER_PORT,
                ),
            ),
            ManagedService(
                "定向合作同步 Worker",
                ("run_collaboration_sync_worker",),
            ),
            ManagedService(
                "邮件 Worker",
                ("run_email_worker", "--server-mode"),
                health_check=lambda: email_worker_endpoint_ready(
                    settings.EMAIL_WORKER_HOST,
                    settings.EMAIL_WORKER_PORT,
                ),
            ),
        ]

        try:
            removed = cleanup_old_logs(
                project_log_root(settings.PROJECT_ROOT),
                retention_days=settings.LOG_RETENTION_DAYS,
            )
            if removed:
                self.stdout.write(
                    f"已清理 {len(removed)} 个过期日志目录。"
                )

            self._start_service(services[0], job)
            if not options["skip_browser_prepare"]:
                self.stdout.write(
                    "正在预启动并验收紫鸟店铺浏览器首页。"
                )
                result = subprocess.run(
                    django_command("prepare_ziniao_browser"),
                    cwd=runtime_cwd(),
                    check=False,
                )
                if result.returncode != 0:
                    self.stderr.write(
                        "紫鸟店铺首页暂未就绪；服务将继续启动。"
                    )
            for service in services[1:]:
                self._start_service(service, job)

            self.stdout.write(
                "系统已启动：Django、导入、达人联系、"
                "定向合作同步和邮件 Worker 均受守护。"
            )
            if options["once"]:
                stop_event.set()

            while not stop_event.wait(0.5):
                now = time.monotonic()
                for service in services:
                    process = service.process
                    if process is not None and process.poll() is None:
                        if (
                            service.health_check is not None
                            and now - service.started_at >= 5
                        ):
                            if service.health_check():
                                service.unhealthy_checks = 0
                            else:
                                service.unhealthy_checks += 1
                                if service.unhealthy_checks >= 6:
                                    self.stderr.write(
                                        f"{service.name} 健康检查连续失败，"
                                        "正在重启。"
                                    )
                                    terminate_process_tree(process)
                        continue
                    if process is not None:
                        stable_seconds = now - service.started_at
                        if stable_seconds >= 60:
                            service.failures = 0
                        else:
                            service.failures += 1
                        delay = min(60, 2 ** min(service.failures, 6))
                        service.restart_at = now + delay
                        self.stderr.write(
                            f"{service.name} 已退出（代码 "
                            f"{process.returncode}），{delay} 秒后重启。"
                        )
                        service.process = None
                    if now >= service.restart_at:
                        self._start_service(service, job)
        except KeyboardInterrupt:
            stop_event.set()
        finally:
            self.stdout.write("正在安全停止所有服务。")
            for service in reversed(services):
                if service.process is not None:
                    terminate_process_tree(service.process)
            job.close()
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
            lock.release()
            self.stdout.write("所有受管服务已停止。")

    def _start_service(
        self,
        service: ManagedService,
        job: WindowsKillOnCloseJob,
    ) -> None:
        process = subprocess.Popen(
            service.command(),
            cwd=runtime_cwd(),
            **new_process_group_kwargs(),
        )
        service.process = process
        service.started_at = time.monotonic()
        service.restart_at = 0
        service.unhealthy_checks = 0
        if job.available and not job.assign(process.pid):
            self.stderr.write(
                f"警告：{service.name} 未能加入 Windows 进程作业。"
            )
        self.stdout.write(
            f"{service.name} 已启动（PID {process.pid}）。"
        )
