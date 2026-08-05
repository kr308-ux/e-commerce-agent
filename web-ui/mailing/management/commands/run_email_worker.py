"""Persistently dispatch contact-task mail and run the serial sender."""

from __future__ import annotations

import os
import time

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand

from mailing.models import EmailDelivery, EmailSendingService
from mailing.services.contact_dispatch import dispatch_pending_contact_tasks
from mailing.services.retry import retry_available_queryset
from mailing.services.retry import attempts_used_today
from mailing.services.runtime import (
    get_service_state,
    recover_interrupted_email_state,
)
from mailing.services.worker_runtime import (
    EmailWorkerHealthServer,
    email_worker_endpoint_ready,
)


class Command(BaseCommand):
    help = "常驻轮询联系任务邮件派发和邮件发送队列。"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--server-mode", action="store_true")
        parser.add_argument(
            "--poll-interval",
            type=float,
            default=settings.EMAIL_WORKER_POLL_INTERVAL_SECONDS,
        )
        parser.add_argument(
            "--control-host",
            default=settings.EMAIL_WORKER_HOST,
        )
        parser.add_argument(
            "--control-port",
            type=int,
            default=settings.EMAIL_WORKER_PORT,
        )

    def handle(self, *args, **options):
        health_server = None
        if options["server_mode"]:
            host = options["control_host"]
            port = options["control_port"]
            if email_worker_endpoint_ready(host, port):
                self.stdout.write("常驻邮件 Worker 已在运行。")
                return
            health_server = EmailWorkerHealthServer(host, port)
            health_server.start()
            self.stdout.write(
                f"常驻邮件 Worker 已监听 {host}:{port}"
            )

        try:
            while True:
                active_sender, uncertain = recover_interrupted_email_state(
                    current_process_id=os.getpid()
                )
                if uncertain:
                    self.stderr.write(
                        f"{uncertain} 封中断邮件已转为人工复核。"
                    )
                if not active_sender:
                    dispatch_pending_contact_tasks()
                    if (
                        get_service_state().status
                        == EmailSendingService.Status.PAUSED
                    ):
                        if options["once"]:
                            return
                        time.sleep(max(0.1, options["poll_interval"]))
                        continue
                    has_work = (
                        EmailDelivery.objects.filter(
                            status=EmailDelivery.Status.PENDING
                        ).exists()
                        or retry_available_queryset().exists()
                    )
                    has_daily_capacity = (
                        attempts_used_today()
                        < settings.CREATOR_EMAIL_DAILY_LIMIT
                    )
                    if has_work and has_daily_capacity:
                        try:
                            call_command(
                                "send_creator_emails",
                                stdout=self.stdout,
                                stderr=self.stderr,
                            )
                        except Exception as error:
                            self.stderr.write(
                                f"邮件发送批次异常："
                                f"{type(error).__name__}: {error}"
                            )
                if options["once"]:
                    return
                time.sleep(max(0.1, options["poll_interval"]))
        finally:
            if health_server is not None:
                health_server.close()
