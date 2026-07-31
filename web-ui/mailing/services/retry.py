"""Calendar-day retry eligibility and reminder queries for email delivery."""

from __future__ import annotations

from datetime import datetime, time, timedelta

from django.conf import settings
from django.db.models import IntegerField, Max, Q, QuerySet, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from mailing.models import EmailDelivery, EmailDeliveryAttempt


def local_attempt_date(value: datetime | None = None):
    return timezone.localtime(value or timezone.now()).date()


def next_local_day_start(value: datetime | None = None) -> datetime:
    local_value = timezone.localtime(value or timezone.now())
    next_date = local_value.date() + timedelta(days=1)
    return timezone.make_aware(
        datetime.combine(next_date, time.min),
        timezone.get_current_timezone(),
    )


def with_today_attempt_count(
    queryset: QuerySet[EmailDelivery],
    *,
    value: datetime | None = None,
) -> QuerySet[EmailDelivery]:
    today = local_attempt_date(value)
    return queryset.annotate(
        today_attempt_count=Coalesce(
            Max(
                "attempts__daily_sequence",
                filter=Q(attempts__attempt_date=today),
            ),
            Value(0),
            output_field=IntegerField(),
        )
    )


def daily_attempt_count(
    delivery: EmailDelivery,
    *,
    value: datetime | None = None,
) -> int:
    annotated = (
        with_today_attempt_count(
            EmailDelivery.objects.filter(pk=delivery.pk),
            value=value,
        )
        .values_list("today_attempt_count", flat=True)
        .first()
    )
    return int(annotated or 0)


def attempts_used_today(*, value: datetime | None = None) -> int:
    today = local_attempt_date(value)
    logged_count = EmailDeliveryAttempt.objects.filter(
        attempt_date=today
    ).count()
    unlogged_delivery_count = (
        EmailDelivery.objects
        .filter(
            Q(last_attempt_at__date=today)
            | Q(sent_at__date=today)
        )
        .exclude(attempts__attempt_date=today)
        .distinct()
        .count()
    )
    return logged_count + unlogged_delivery_count


def failed_today_queryset(
    *,
    value: datetime | None = None,
) -> QuerySet[EmailDelivery]:
    today = local_attempt_date(value)
    return (
        EmailDelivery.objects
        .exclude(status=EmailDelivery.Status.SENT)
        .filter(
            attempts__attempt_date=today,
            attempts__status=EmailDeliveryAttempt.Status.FAILED,
        )
        .distinct()
    )


def retry_available_queryset(
    *,
    value: datetime | None = None,
) -> QuerySet[EmailDelivery]:
    now = value or timezone.now()
    max_attempts = settings.CREATOR_EMAIL_MAX_ATTEMPTS_PER_DAY
    queryset = with_today_attempt_count(
        EmailDelivery.objects.filter(
            retryable=True,
        ).filter(
            Q(
                status=EmailDelivery.Status.RETRY_WAITING,
                next_retry_at__lte=now,
            )
            | Q(status=EmailDelivery.Status.FAILED)
        ),
        value=now,
    )
    return queryset.filter(today_attempt_count__lt=max_attempts)


def requeue_available_retries(
    *,
    value: datetime | None = None,
) -> int:
    now = value or timezone.now()
    delivery_ids = list(
        retry_available_queryset(value=now).values_list("pk", flat=True)
    )
    if not delivery_ids:
        return 0
    return EmailDelivery.objects.filter(pk__in=delivery_ids).update(
        status=EmailDelivery.Status.PENDING,
        next_retry_at=None,
        updated_at=now,
    )


def promote_due_retries(*, value: datetime | None = None) -> int:
    now = value or timezone.now()
    return EmailDelivery.objects.filter(
        status=EmailDelivery.Status.RETRY_WAITING,
        retryable=True,
        next_retry_at__lte=now,
    ).update(
        status=EmailDelivery.Status.PENDING,
        next_retry_at=None,
        updated_at=now,
    )


def defer_exhausted_pending(*, value: datetime | None = None) -> int:
    now = value or timezone.now()
    exhausted_ids = list(
        with_today_attempt_count(
            EmailDelivery.objects.filter(
                status=EmailDelivery.Status.PENDING,
            ),
            value=now,
        )
        .filter(
            today_attempt_count__gte=(
                settings.CREATOR_EMAIL_MAX_ATTEMPTS_PER_DAY
            )
        )
        .values_list("pk", flat=True)
    )
    if not exhausted_ids:
        return 0
    return EmailDelivery.objects.filter(pk__in=exhausted_ids).update(
        status=EmailDelivery.Status.RETRY_WAITING,
        next_retry_at=next_local_day_start(now),
        updated_at=now,
    )


def pending_ready_queryset(
    *,
    value: datetime | None = None,
) -> QuerySet[EmailDelivery]:
    now = value or timezone.now()
    return with_today_attempt_count(
        EmailDelivery.objects.filter(
            status=EmailDelivery.Status.PENDING,
        ).filter(
            Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now)
        ),
        value=now,
    ).filter(
        today_attempt_count__lt=(
            settings.CREATOR_EMAIL_MAX_ATTEMPTS_PER_DAY
        )
    )
