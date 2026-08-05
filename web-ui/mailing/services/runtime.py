"""Persistent cooperative control for the serial email sender."""

from __future__ import annotations

import os
import time
import uuid

from django.db import transaction
from django.utils import timezone

from mailing.models import (
    EmailDelivery,
    EmailDeliveryAttempt,
    EmailSendingService,
)


SERVICE_PK = 1
STOP_POLL_INTERVAL_SECONDS = 0.25


class EmailServiceAlreadyRunning(RuntimeError):
    """Raised when a second sender tries to claim the singleton service."""


def get_service_state() -> EmailSendingService:
    state, _ = EmailSendingService.objects.get_or_create(pk=SERVICE_PK)
    return state


def process_is_running(process_id: int | None) -> bool:
    if not process_id:
        return False
    try:
        os.kill(int(process_id), 0)
    except (OSError, ValueError):
        return False
    return True


def recover_interrupted_email_state(
    *,
    current_process_id: int | None = None,
) -> tuple[bool, int]:
    """Reconcile a dead sender without ever auto-retrying uncertain SMTP."""

    with transaction.atomic():
        state, _ = EmailSendingService.objects.get_or_create(pk=SERVICE_PK)
        active = (
            state.status
            in {
                EmailSendingService.Status.RUNNING,
                EmailSendingService.Status.STOPPING,
            }
            and state.process_id != current_process_id
            and process_is_running(state.process_id)
        )
        if active:
            return True, 0
        if state.status == EmailSendingService.Status.PAUSED:
            return False, 0

        now = timezone.now()
        uncertain_ids = list(
            EmailDelivery.objects.filter(
                status=EmailDelivery.Status.SENDING
            ).values_list("pk", flat=True)
        )
        if uncertain_ids:
            EmailDeliveryAttempt.objects.filter(
                delivery_id__in=uncertain_ids,
                status=EmailDeliveryAttempt.Status.STARTED,
            ).update(
                status=EmailDeliveryAttempt.Status.UNCERTAIN,
                error_code="SENDER_INTERRUPTED",
                error_message=(
                    "邮件进程在确认 SMTP 结果前中断，需人工核对。"
                ),
                finished_at=now,
            )
            EmailDelivery.objects.filter(pk__in=uncertain_ids).update(
                status=EmailDelivery.Status.UNCERTAIN,
                retryable=False,
                error_code="SENDER_INTERRUPTED",
                error_message=(
                    "送达状态不确定；确认 Gmail 已发送记录前不得重试。"
                ),
                next_retry_at=None,
                updated_at=now,
            )
        if (
            state.status == EmailSendingService.Status.STOPPED
            and not uncertain_ids
        ):
            return False, 0
        state.status = EmailSendingService.Status.STOPPED
        state.stop_requested = False
        state.run_id = None
        state.process_id = None
        state.current_delivery = None
        state.heartbeat_at = now
        state.stopped_at = now
        state.save()
        return False, len(uncertain_ids)


def begin_service_run() -> uuid.UUID:
    with transaction.atomic():
        EmailSendingService.objects.get_or_create(
            pk=SERVICE_PK
        )
        run_id = uuid.uuid4()
        now = timezone.now()
        claimed = EmailSendingService.objects.filter(
            pk=SERVICE_PK,
            status=EmailSendingService.Status.STOPPED,
        ).update(
            status=EmailSendingService.Status.RUNNING,
            stop_requested=False,
            run_id=run_id,
            process_id=os.getpid(),
            current_delivery=None,
            sent_count=0,
            failed_count=0,
            started_at=now,
            heartbeat_at=now,
            stopped_at=None,
            updated_at=now,
        )
        if claimed != 1:
            raise EmailServiceAlreadyRunning("邮件发送服务已经在运行。")
        return run_id


def request_service_stop() -> EmailSendingService:
    with transaction.atomic():
        state, _ = EmailSendingService.objects.select_for_update().get_or_create(
            pk=SERVICE_PK
        )
        if state.status not in {
            EmailSendingService.Status.RUNNING,
            EmailSendingService.Status.STOPPING,
        }:
            return state
        state.stop_requested = True
        state.status = EmailSendingService.Status.STOPPING
        state.heartbeat_at = timezone.now()
        state.save(
            update_fields=[
                "stop_requested",
                "status",
                "heartbeat_at",
                "updated_at",
            ]
        )
        return state


def service_stop_requested(run_id: uuid.UUID) -> bool:
    return EmailSendingService.objects.filter(
        pk=SERVICE_PK,
        run_id=run_id,
        stop_requested=True,
    ).exists()


def update_service_progress(
    run_id: uuid.UUID,
    *,
    delivery: EmailDelivery | None = None,
    sent_count: int | None = None,
    failed_count: int | None = None,
) -> None:
    values: dict[str, object] = {
        "heartbeat_at": timezone.now(),
        "updated_at": timezone.now(),
    }
    if delivery is not None:
        values["current_delivery"] = delivery
    if sent_count is not None:
        values["sent_count"] = sent_count
    if failed_count is not None:
        values["failed_count"] = failed_count
    EmailSendingService.objects.filter(
        pk=SERVICE_PK,
        run_id=run_id,
    ).update(**values)


def wait_for_interval_or_stop(run_id: uuid.UUID, seconds: float) -> bool:
    """Return True when a stop was requested during the interval."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if service_stop_requested(run_id):
            return True
        remaining = deadline - time.monotonic()
        time.sleep(min(STOP_POLL_INTERVAL_SECONDS, max(remaining, 0)))
    return service_stop_requested(run_id)


def finish_service_run(run_id: uuid.UUID) -> None:
    now = timezone.now()
    common_values = {
        "stop_requested": False,
        "process_id": None,
        "current_delivery": None,
        "heartbeat_at": now,
        "stopped_at": now,
        "updated_at": now,
    }
    for _attempt in range(2):
        paused = EmailSendingService.objects.filter(
            pk=SERVICE_PK,
            run_id=run_id,
            stop_requested=True,
        ).update(
            status=EmailSendingService.Status.PAUSED,
            **common_values,
        )
        if paused:
            return
        stopped = EmailSendingService.objects.filter(
            pk=SERVICE_PK,
            run_id=run_id,
            stop_requested=False,
        ).update(
            status=EmailSendingService.Status.STOPPED,
            **common_values,
        )
        if stopped:
            return


def resume_service() -> EmailSendingService:
    EmailSendingService.objects.filter(
        pk=SERVICE_PK,
        status=EmailSendingService.Status.PAUSED,
    ).update(
        status=EmailSendingService.Status.STOPPED,
        stop_requested=False,
        updated_at=timezone.now(),
    )
    return get_service_state()
