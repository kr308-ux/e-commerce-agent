from __future__ import annotations

import smtplib
from datetime import timedelta
from email.utils import make_msgid

from django.conf import settings
from django.core.mail import get_connection
from django.core.management.base import BaseCommand, CommandError
from django.db.models import F, Q
from django.utils import timezone

from mailing.models import EmailDelivery
from mailing.services.email_sender import (
    CreatorEmailError,
    EmailConfigurationError,
    send_creator_email,
    validate_email_configuration,
)
from mailing.services.runtime import (
    EmailServiceAlreadyRunning,
    begin_service_run,
    finish_service_run,
    service_stop_requested,
    update_service_progress,
    wait_for_interval_or_stop,
)


class Command(BaseCommand):
    help = "串行发送待处理的达人合作邮件。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=settings.CREATOR_EMAIL_DAILY_LIMIT,
        )
        parser.add_argument(
            "--interval",
            type=float,
            default=settings.CREATOR_EMAIL_SEND_INTERVAL_SECONDS,
            help="每封成功邮件之间的等待秒数。",
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        limit = options["limit"]
        interval = options["interval"]
        if limit < 1:
            raise CommandError("limit 必须大于 0。")
        if interval < 0:
            raise CommandError("interval 不能小于 0。")

        cutoff = timezone.now() - timedelta(hours=24)
        attempted_in_window = (
            EmailDelivery.objects
            .filter(
                Q(last_attempt_at__gte=cutoff)
                | Q(
                    status=EmailDelivery.Status.SENT,
                    sent_at__gte=cutoff,
                )
            )
            .count()
        )
        remaining = max(
            0,
            settings.CREATOR_EMAIL_DAILY_LIMIT - attempted_in_window,
        )
        batch_limit = min(limit, remaining)
        delivery_ids = list(
            EmailDelivery.objects
            .filter(status=EmailDelivery.Status.PENDING)
            .order_by("created_at", "id")
            .values_list("pk", flat=True)[:batch_limit]
        )

        if options["dry_run"]:
            sending_count = EmailDelivery.objects.filter(
                status=EmailDelivery.Status.SENDING
            ).count()
            failed_count = EmailDelivery.objects.filter(
                status=EmailDelivery.Status.FAILED
            ).count()
            self.stdout.write(
                self.style.SUCCESS(
                    f"预览：过去 24 小时已尝试 "
                    f"{attempted_in_window}，"
                    f"剩余额度 {remaining}，本次将发送 "
                    f"{len(delivery_ids)} 封；当前发送中 "
                    f"{sending_count}，失败 {failed_count}。"
                )
            )
            return
        if not remaining:
            self.stdout.write(
                self.style.WARNING(
                    "过去 24 小时已达到发送上限，队列保持不变。"
                )
            )
            return
        if not delivery_ids:
            self.stdout.write("没有待发送邮件。")
            return

        try:
            run_id = begin_service_run()
        except EmailServiceAlreadyRunning as error:
            raise CommandError(str(error)) from error

        try:
            validate_email_configuration()
        except EmailConfigurationError as error:
            finish_service_run(run_id)
            raise CommandError(str(error)) from error

        connection = get_connection(fail_silently=False)
        try:
            connection.open()
        except smtplib.SMTPAuthenticationError as error:
            finish_service_run(run_id)
            raise CommandError("Gmail SMTP 认证失败，未领取队列。") from error
        except (smtplib.SMTPException, OSError) as error:
            finish_service_run(run_id)
            raise CommandError(
                f"Gmail SMTP 连接失败，未领取队列：{error}"
            ) from error

        sent_count = 0
        failed_count = 0
        try:
            for index, delivery_id in enumerate(delivery_ids):
                if service_stop_requested(run_id):
                    self.stdout.write(
                        self.style.WARNING(
                            "收到停止请求，未再领取后续邮件。"
                        )
                    )
                    break
                now = timezone.now()
                message_id = make_msgid(domain="vaelos.email")
                claimed = (
                    EmailDelivery.objects
                    .filter(
                        pk=delivery_id,
                        status=EmailDelivery.Status.PENDING,
                    )
                    .update(
                        status=EmailDelivery.Status.SENDING,
                        attempt_count=F("attempt_count") + 1,
                        last_attempt_at=now,
                        message_id=message_id,
                        error_code="",
                        error_message="",
                        updated_at=now,
                    )
                )
                if claimed != 1:
                    continue
                delivery = EmailDelivery.objects.get(pk=delivery_id)
                update_service_progress(
                    run_id,
                    delivery=delivery,
                    sent_count=sent_count,
                    failed_count=failed_count,
                )
                try:
                    message_id = send_creator_email(
                        delivery,
                        connection=connection,
                    )
                except smtplib.SMTPAuthenticationError as error:
                    self._mark_failed(
                        delivery,
                        code="SMTP_AUTHENTICATION_FAILED",
                        error=error,
                    )
                    raise CommandError(
                        "Gmail SMTP 认证失效，已停止本批发送。"
                    ) from error
                except (
                    smtplib.SMTPException,
                    OSError,
                    CreatorEmailError,
                ) as error:
                    self._mark_failed(
                        delivery,
                        code=type(error).__name__.upper()[:80],
                        error=error,
                    )
                    failed_count += 1
                    update_service_progress(
                        run_id,
                        sent_count=sent_count,
                        failed_count=failed_count,
                    )
                    if service_stop_requested(run_id):
                        self.stdout.write(
                            self.style.WARNING(
                                "当前邮件已记录失败，"
                                "邮件发送服务按请求停止。"
                            )
                        )
                        break
                    if isinstance(
                        error,
                        (smtplib.SMTPException, OSError),
                    ) and index < len(delivery_ids) - 1:
                        connection.close()
                        connection = get_connection(
                            fail_silently=False
                        )
                        try:
                            connection.open()
                        except smtplib.SMTPAuthenticationError as reconnect:
                            raise CommandError(
                                "Gmail SMTP 重新认证失败，"
                                "已停止本批发送。"
                            ) from reconnect
                        except (
                            smtplib.SMTPException,
                            OSError,
                        ) as reconnect:
                            raise CommandError(
                                "Gmail SMTP 连接中断且无法恢复，"
                                "已停止本批发送。"
                            ) from reconnect
                    continue

                delivery.status = EmailDelivery.Status.SENT
                delivery.message_id = message_id
                delivery.sent_at = timezone.now()
                delivery.error_code = ""
                delivery.error_message = ""
                delivery.save(
                    update_fields=[
                        "status",
                        "message_id",
                        "sent_at",
                        "error_code",
                        "error_message",
                        "updated_at",
                    ]
                )
                sent_count += 1
                update_service_progress(
                    run_id,
                    sent_count=sent_count,
                    failed_count=failed_count,
                )
                if service_stop_requested(run_id):
                    self.stdout.write(
                        self.style.WARNING(
                            "当前邮件已完成，邮件发送服务按请求停止。"
                        )
                    )
                    break
                if interval and index < len(delivery_ids) - 1:
                    if wait_for_interval_or_stop(run_id, interval):
                        self.stdout.write(
                            self.style.WARNING(
                                "等待期间收到停止请求，"
                                "未再领取后续邮件。"
                            )
                        )
                        break
        finally:
            connection.close()
            finish_service_run(run_id)

        self.stdout.write(
            self.style.SUCCESS(
                f"发送结束：成功 {sent_count}，失败 {failed_count}，"
                f"队列剩余 "
                f"{EmailDelivery.objects.filter(status=EmailDelivery.Status.PENDING).count()}。"
            )
        )

    @staticmethod
    def _mark_failed(
        delivery: EmailDelivery,
        *,
        code: str,
        error: Exception,
    ) -> None:
        delivery.status = EmailDelivery.Status.FAILED
        delivery.error_code = code
        delivery.error_message = str(error)[:2000]
        delivery.save(
            update_fields=[
                "status",
                "error_code",
                "error_message",
                "updated_at",
            ]
        )
