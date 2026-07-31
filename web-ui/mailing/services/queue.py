"""Select creators for the globally deduplicated email queue."""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import (
    DecimalField,
    Exists,
    F,
    Max,
    OuterRef,
    Q,
    Subquery,
)
from django.utils import timezone

from creator_contact.models import CreatorContactTarget
from tasks.models import Creator, CreatorSalesMetric, ImportTask

from mailing.models import (
    EmailDelivery,
    EmailTemplateVersion,
    normalize_email,
    recipient_key_for,
)
from mailing.services.template_content import get_active_template_version
from mailing.services.retry import (
    daily_attempt_count,
    next_local_day_start,
)


@dataclass
class QueueResult:
    queued: int = 0
    requeued: int = 0
    already_pending: int = 0
    already_sent: int = 0
    sending: int = 0
    failed_not_retried: int = 0
    exhausted: int = 0
    deferred_today: int = 0
    invalid_email: int = 0
    duplicate_candidate: int = 0

    @property
    def selected(self) -> int:
        return self.queued + self.requeued + self.already_pending


def _ranked_batch_creators(import_task: ImportTask):
    def metric_subquery(window_days: int):
        return (
            CreatorSalesMetric.objects.filter(
                import_task=import_task,
                creator_id=OuterRef("pk"),
                window_days=window_days,
            )
            .order_by("-source_row_number", "-id")
            .values("sales_amount")[:1]
        )

    return (
        Creator.objects.filter(import_memberships__import_task=import_task)
        .annotate(
            _sort_30=Subquery(
                metric_subquery(30),
                output_field=DecimalField(max_digits=24, decimal_places=4),
            ),
            _sort_7=Subquery(
                metric_subquery(7),
                output_field=DecimalField(max_digits=24, decimal_places=4),
            ),
            _import_row=F("import_memberships__first_row_number"),
        )
        .order_by(
            F("_sort_30").desc(nulls_last=True),
            F("_sort_7").desc(nulls_last=True),
            "_import_row",
            "creator_id",
        )
    )


def card_sent_creators(import_task: ImportTask):
    """Return one batch's creators with a fully verified card delivery."""
    already_sent = EmailDelivery.objects.filter(
        status=EmailDelivery.Status.SENT,
    ).filter(
        Q(creator_id=OuterRef("pk"))
        | Q(creator_id_snapshot=OuterRef("creator_id"))
    )
    return (
        Creator.objects.filter(
            contact_targets__task__source_import_task=import_task,
            contact_targets__status=CreatorContactTarget.Status.SUCCESS,
            contact_targets__card_sent=True,
            contact_targets__final_send_verified=True,
        )
        .exclude(email="")
        .annotate(
            latest_card_sent_at=Max("contact_targets__finished_at"),
            email_already_sent=Exists(already_sent),
        )
        .filter(email_already_sent=False)
        .order_by(
            F("latest_card_sent_at").desc(nulls_last=True),
            "creator_id",
        )
    )


def import_email_creators(import_task: ImportTask):
    """Return one batch's email-capable creators in stable import order."""
    already_sent = EmailDelivery.objects.filter(
        status=EmailDelivery.Status.SENT,
    ).filter(
        Q(creator_id=OuterRef("pk"))
        | Q(creator_id_snapshot=OuterRef("creator_id"))
    )
    return (
        Creator.objects.filter(import_memberships__import_task=import_task)
        .exclude(email="")
        .annotate(
            import_row=F("import_memberships__first_row_number"),
            email_already_sent=Exists(already_sent),
        )
        .filter(email_already_sent=False)
        .order_by("import_row", "creator_id")
    )


def selected_import_creators(
    *,
    import_task: ImportTask,
    creator_pks: list[str] | tuple[str, ...],
) -> tuple[Creator, ...]:
    requested: list[str] = []
    seen: set[str] = set()
    for raw_pk in creator_pks:
        creator_pk = str(raw_pk).strip()
        if not creator_pk or creator_pk in seen:
            continue
        requested.append(creator_pk)
        seen.add(creator_pk)

    available = {
        str(creator.pk): creator
        for creator in import_email_creators(import_task).filter(
            pk__in=requested
        )
    }
    if len(available) != len(requested):
        raise ValueError("所选达人不属于当前导入批次或没有可用邮箱。")
    return tuple(available[creator_pk] for creator_pk in requested)


def queue_creators(
    *,
    creators,
    limit: int,
    source_import_task: ImportTask | None = None,
    retry_failed: bool = False,
    dry_run: bool = False,
    template_version: EmailTemplateVersion | None = None,
) -> QueueResult:
    if limit < 1 or limit > settings.CREATOR_EMAIL_DAILY_LIMIT:
        raise ValueError(
            "limit 必须介于 1 和 CREATOR_EMAIL_DAILY_LIMIT 之间。"
        )

    result = QueueResult()
    if template_version is None and not dry_run:
        template_version = get_active_template_version()
    seen_keys: set[str] = set()
    creator_iterator = (
        creators.iterator() if hasattr(creators, "iterator") else iter(creators)
    )
    for creator in creator_iterator:
        email = normalize_email(creator.email)
        try:
            validate_email(email)
        except ValidationError:
            result.invalid_email += 1
            continue

        key = recipient_key_for(
            creator_id=creator.creator_id,
            email=email,
        )
        if key in seen_keys:
            result.duplicate_candidate += 1
            continue
        seen_keys.add(key)

        existing = EmailDelivery.objects.filter(recipient_key=key).first()
        if existing is not None:
            if existing.status == EmailDelivery.Status.SENT:
                result.already_sent += 1
                continue
            if existing.status == EmailDelivery.Status.SENDING:
                result.sending += 1
                continue
            if existing.status == EmailDelivery.Status.PENDING:
                result.already_pending += 1
                if (
                    not dry_run
                    and (
                        (
                            existing.template_version_id is None
                            and template_version is not None
                        )
                        or (
                            existing.source_import_task_id is None
                            and source_import_task is not None
                        )
                    )
                ):
                    update_fields = ["updated_at"]
                    if (
                        existing.template_version_id is None
                        and template_version is not None
                    ):
                        existing.template_version = template_version
                        update_fields.append("template_version")
                    if (
                        existing.source_import_task_id is None
                        and source_import_task is not None
                    ):
                        existing.source_import_task = source_import_task
                        update_fields.append("source_import_task")
                    existing.save(update_fields=update_fields)
                if result.selected >= limit:
                    break
                continue
            if existing.status == EmailDelivery.Status.RETRY_WAITING:
                if (
                    existing.next_retry_at
                    and existing.next_retry_at <= timezone.now()
                    and retry_failed
                ):
                    result.requeued += 1
                    if not dry_run:
                        existing.status = EmailDelivery.Status.PENDING
                        existing.next_retry_at = None
                        existing.save(
                            update_fields=[
                                "status",
                                "next_retry_at",
                                "updated_at",
                            ]
                        )
                else:
                    result.deferred_today += 1
                if result.selected >= limit:
                    break
                continue
            if existing.status == EmailDelivery.Status.FAILED:
                if not existing.retryable:
                    result.failed_not_retried += 1
                    continue
                if daily_attempt_count(existing) >= (
                    settings.CREATOR_EMAIL_MAX_ATTEMPTS_PER_DAY
                ):
                    result.deferred_today += 1
                    if not dry_run:
                        existing.status = EmailDelivery.Status.RETRY_WAITING
                        existing.next_retry_at = next_local_day_start()
                        existing.save(
                            update_fields=[
                                "status",
                                "next_retry_at",
                                "updated_at",
                            ]
                        )
                    continue
                if not retry_failed:
                    result.failed_not_retried += 1
                    continue
                result.requeued += 1
                if not dry_run:
                    existing.creator = creator
                    existing.creator_id_snapshot = creator.creator_id
                    existing.creator_name_snapshot = (
                        creator.nickname or creator.creator_id or "Creator"
                    )
                    existing.recipient_email = email
                    existing.source_import_task = source_import_task
                    existing.status = EmailDelivery.Status.PENDING
                    existing.next_retry_at = None
                    existing.error_code = ""
                    existing.error_message = ""
                    existing.save()
                if result.selected >= limit:
                    break
                continue
            result.failed_not_retried += 1
            continue

        result.queued += 1
        if not dry_run:
            EmailDelivery.objects.create(
                creator=creator,
                source_import_task=source_import_task,
                recipient_key=key,
                creator_id_snapshot=creator.creator_id,
                creator_name_snapshot=(
                    creator.nickname or creator.creator_id or "Creator"
                ),
                recipient_email=email,
                template_version=template_version,
            )
        if result.selected >= limit:
            break

    return result


def queue_import_creators(
    *,
    import_task: ImportTask,
    limit: int,
    retry_failed: bool = False,
    dry_run: bool = False,
    template_version: EmailTemplateVersion | None = None,
) -> QueueResult:
    """Keep the management command's legacy sales-ranked batch behavior."""
    return queue_creators(
        creators=_ranked_batch_creators(import_task),
        limit=limit,
        source_import_task=import_task,
        retry_failed=retry_failed,
        dry_run=dry_run,
        template_version=template_version,
    )
