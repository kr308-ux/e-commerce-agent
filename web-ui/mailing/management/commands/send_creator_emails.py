from __future__ import annotations

import random
import smtplib
from datetime import timedelta
from email.utils import make_msgid

from django.conf import settings
from django.core.mail import get_connection
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from mailing.models import EmailDelivery, EmailDeliveryAttempt
from mailing.services.email_sender import (
    CreatorEmailError,
    EmailConfigurationError,
    send_creator_email,
    validate_email_configuration,
)
from mailing.services.retry import (
    attempts_used_today,
    defer_exhausted_pending,
    local_attempt_date,
    next_local_day_start,
    pending_ready_queryset,
    promote_due_retries,
    requeue_available_retries,
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
            default=None,
            help="固定发送间隔；仅用于测试或临时覆盖随机区间。",
        )
        parser.add_argument(
            "--interval-min",
            type=float,
            default=settings.CREATOR_EMAIL_SEND_INTERVAL_MIN_SECONDS,
        )
        parser.add_argument(
            "--interval-max",
            type=float,
            default=settings.CREATOR_EMAIL_SEND_INTERVAL_MAX_SECONDS,
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        limit = options["limit"]
        fixed_interval = options["interval"]
        interval_min = options["interval_min"]
        interval_max = options["interval_max"]
        if limit < 1:
            raise CommandError("limit 必须大于 0。")
        if fixed_interval is not None and fixed_interval < 0:
            raise CommandError("interval 不能小于 0。")
        if interval_min < 0 or interval_max < interval_min:
            raise CommandError("随机发送间隔必须满足 0 ≤ 最小值 ≤ 最大值。")

        now = timezone.now()
        attempted_today = attempts_used_today(value=now)
        remaining = max(
            0,
            settings.CREATOR_EMAIL_DAILY_LIMIT - attempted_today,
        )
        batch_limit = min(limit, remaining)

        if options["dry_run"]:
            preview_count = min(
                pending_ready_queryset(value=now).count(),
                batch_limit,
            )
            sending_count = EmailDelivery.objects.filter(
                status=EmailDelivery.Status.SENDING
            ).count()
            failed_count = EmailDelivery.objects.filter(
                status__in=[
                    EmailDelivery.Status.FAILED,
                    EmailDelivery.Status.RETRY_WAITING,
                ]
            ).count()
            self.stdout.write(
                self.style.SUCCESS(
                    f"预览：今日已尝试 {attempted_today} 次，"
                    f"剩余额度 {remaining}，本次最多尝试 "
                    f"{batch_limit} 次，当前可立即发送 "
                    f"{preview_count} 封；发送中 {sending_count}，"
                    f"失败或等待明日重试 {failed_count}。"
                )
            )
            return
        if not remaining:
            self.stdout.write(
                self.style.WARNING(
                    "今日已达到发送上限，队列保持不变。"
                )
            )
            return

        promote_due_retries(value=now)
        requeue_available_retries(value=now)
        defer_exhausted_pending(value=now)
        if not pending_ready_queryset(value=now).exists():
            self.stdout.write("没有当前可发送的邮件。")
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
        processed_count = 0
        try:
            while processed_count < batch_limit:
                if service_stop_requested(run_id):
                    self.stdout.write(
                        self.style.WARNING(
                            "收到停止请求，未再领取后续邮件。"
                        )
                    )
                    break
                now = timezone.now()
                delivery_id = (
                    pending_ready_queryset(value=now)
                    .order_by("created_at", "id")
                    .values_list("pk", flat=True)
                    .first()
                )
                if delivery_id is None:
                    break

                interval = self._next_interval(
                    fixed=fixed_interval,
                    minimum=interval_min,
                    maximum=interval_max,
                )
                claimed = self._claim_attempt(delivery_id, value=now)
                if claimed is None:
                    continue
                delivery, attempt = claimed
                processed_count += 1
                update_service_progress(
                    run_id,
                    delivery=delivery,
                    sent_count=sent_count,
                    failed_count=failed_count,
                )

                should_reconnect = False
                try:
                    delivered_message_id = send_creator_email(
                        delivery,
                        connection=connection,
                    )
                except smtplib.SMTPAuthenticationError as error:
                    self._mark_failed(
                        delivery,
                        attempt,
                        code="SMTP_AUTHENTICATION_FAILED",
                        error=error,
                        retryable=True,
                        automatic_retry=False,
                        retry_delay=interval,
                    )
                    failed_count += 1
                    update_service_progress(
                        run_id,
                        sent_count=sent_count,
                        failed_count=failed_count,
                    )
                    raise CommandError(
                        "Gmail SMTP 认证失效，已停止本批发送。"
                    ) from error
                except (smtplib.SMTPException, OSError) as error:
                    self._mark_failed(
                        delivery,
                        attempt,
                        code=type(error).__name__.upper()[:80],
                        error=error,
                        retryable=True,
                        automatic_retry=True,
                        retry_delay=interval,
                    )
                    failed_count += 1
                    should_reconnect = True
                except CreatorEmailError as error:
                    self._mark_failed(
                        delivery,
                        attempt,
                        code=type(error).__name__.upper()[:80],
                        error=error,
                        retryable=False,
                        automatic_retry=False,
                        retry_delay=interval,
                    )
                    failed_count += 1
                else:
                    self._mark_sent(
                        delivery,
                        attempt,
                        message_id=delivered_message_id,
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
                            "当前邮件已记录结果，邮件发送服务按请求停止。"
                        )
                    )
                    break

                if should_reconnect and EmailDelivery.objects.filter(
                    status=EmailDelivery.Status.PENDING
                ).exists():
                    connection = self._reconnect(connection)

                if (
                    interval > 0
                    and processed_count < batch_limit
                    and EmailDelivery.objects.filter(
                        status=EmailDelivery.Status.PENDING
                    ).exists()
                ):
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

        pending_count = EmailDelivery.objects.filter(
            status=EmailDelivery.Status.PENDING
        ).count()
        waiting_count = EmailDelivery.objects.filter(
            status=EmailDelivery.Status.RETRY_WAITING
        ).count()
        self.stdout.write(
            self.style.SUCCESS(
                f"发送结束：成功 {sent_count}，失败尝试 {failed_count}，"
                f"队列剩余 {pending_count}，"
                f"等待明日重试 {waiting_count}。"
            )
        )

    @staticmethod
    def _next_interval(
        *,
        fixed: float | None,
        minimum: float,
        maximum: float,
    ) -> float:
        if fixed is not None:
            return fixed
        return random.uniform(minimum, maximum)

    @staticmethod
    def _claim_attempt(
        delivery_id: int,
        *,
        value,
    ) -> tuple[EmailDelivery, EmailDeliveryAttempt] | None:
        with transaction.atomic():
            delivery = (
                EmailDelivery.objects
                .select_for_update()
                .filter(
                    pk=delivery_id,
                    status=EmailDelivery.Status.PENDING,
                )
                .first()
            )
            if delivery is None:
                return None
            today = local_attempt_date(value)
            daily_sequence = (
                EmailDeliveryAttempt.objects
                .filter(
                    delivery=delivery,
                    attempt_date=today,
                )
                .aggregate(maximum=Max("daily_sequence"))["maximum"]
                or 0
            ) + 1
            if daily_sequence > settings.CREATOR_EMAIL_MAX_ATTEMPTS_PER_DAY:
                delivery.status = EmailDelivery.Status.RETRY_WAITING
                delivery.next_retry_at = next_local_day_start(value)
                delivery.save(
                    update_fields=[
                        "status",
                        "next_retry_at",
                        "updated_at",
                    ]
                )
                return None

            message_id = make_msgid(domain="vaelos.email")
            delivery.status = EmailDelivery.Status.SENDING
            delivery.attempt_count += 1
            delivery.last_attempt_at = value
            delivery.next_retry_at = None
            delivery.message_id = message_id
            delivery.error_code = ""
            delivery.error_message = ""
            delivery.save(
                update_fields=[
                    "status",
                    "attempt_count",
                    "last_attempt_at",
                    "next_retry_at",
                    "message_id",
                    "error_code",
                    "error_message",
                    "updated_at",
                ]
            )
            attempt = EmailDeliveryAttempt.objects.create(
                delivery=delivery,
                sequence=delivery.attempt_count,
                attempt_date=today,
                daily_sequence=daily_sequence,
                status=EmailDeliveryAttempt.Status.STARTED,
                message_id=message_id,
                started_at=value,
            )
            return delivery, attempt

    @staticmethod
    def _mark_failed(
        delivery: EmailDelivery,
        attempt: EmailDeliveryAttempt,
        *,
        code: str,
        error: Exception,
        retryable: bool,
        automatic_retry: bool,
        retry_delay: float,
    ) -> None:
        now = timezone.now()
        message = str(error)[:2000]
        attempt.status = EmailDeliveryAttempt.Status.FAILED
        attempt.error_code = code
        attempt.error_message = message
        attempt.finished_at = now
        attempt.save(
            update_fields=[
                "status",
                "error_code",
                "error_message",
                "finished_at",
            ]
        )

        delivery.retryable = retryable
        delivery.last_failure_at = now
        delivery.error_code = code
        delivery.error_message = message
        if (
            retryable
            and attempt.daily_sequence
            >= settings.CREATOR_EMAIL_MAX_ATTEMPTS_PER_DAY
        ):
            delivery.status = EmailDelivery.Status.RETRY_WAITING
            delivery.next_retry_at = next_local_day_start(now)
        elif retryable and automatic_retry:
            delivery.status = EmailDelivery.Status.PENDING
            delivery.next_retry_at = now + timedelta(seconds=retry_delay)
        else:
            delivery.status = EmailDelivery.Status.FAILED
            delivery.next_retry_at = None
        delivery.save(
            update_fields=[
                "status",
                "retryable",
                "last_failure_at",
                "next_retry_at",
                "error_code",
                "error_message",
                "updated_at",
            ]
        )

    @staticmethod
    def _mark_sent(
        delivery: EmailDelivery,
        attempt: EmailDeliveryAttempt,
        *,
        message_id: str,
    ) -> None:
        now = timezone.now()
        attempt.status = EmailDeliveryAttempt.Status.SENT
        attempt.message_id = message_id
        attempt.finished_at = now
        attempt.save(
            update_fields=[
                "status",
                "message_id",
                "finished_at",
            ]
        )
        delivery.status = EmailDelivery.Status.SENT
        delivery.message_id = message_id
        delivery.sent_at = now
        delivery.next_retry_at = None
        delivery.error_code = ""
        delivery.error_message = ""
        delivery.save(
            update_fields=[
                "status",
                "message_id",
                "sent_at",
                "next_retry_at",
                "error_code",
                "error_message",
                "updated_at",
            ]
        )

    @staticmethod
    def _reconnect(connection):
        connection.close()
        replacement = get_connection(fail_silently=False)
        try:
            replacement.open()
        except smtplib.SMTPAuthenticationError as error:
            raise CommandError(
                "Gmail SMTP 重新认证失败，已停止本批发送。"
            ) from error
        except (smtplib.SMTPException, OSError) as error:
            raise CommandError(
                "Gmail SMTP 连接中断且无法恢复，已停止本批发送。"
            ) from error
        return replacement
