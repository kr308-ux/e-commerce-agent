"""Select one import batch for the globally deduplicated email queue."""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import DecimalField, F, OuterRef, Subquery

from tasks.models import Creator, CreatorSalesMetric, ImportTask

from mailing.models import (
    EmailDelivery,
    EmailTemplateVersion,
    normalize_email,
    recipient_key_for,
)
from mailing.services.template_content import get_active_template_version


@dataclass
class QueueResult:
    queued: int = 0
    requeued: int = 0
    already_pending: int = 0
    already_sent: int = 0
    sending: int = 0
    failed_not_retried: int = 0
    exhausted: int = 0
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


def queue_import_creators(
    *,
    import_task: ImportTask,
    limit: int,
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
    for creator in _ranked_batch_creators(import_task).iterator():
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
                    and existing.template_version_id is None
                    and template_version is not None
                ):
                    existing.template_version = template_version
                    existing.save(
                        update_fields=["template_version", "updated_at"]
                    )
                if result.selected >= limit:
                    break
                continue
            if existing.status == EmailDelivery.Status.FAILED:
                if (
                    existing.attempt_count
                    >= settings.CREATOR_EMAIL_MAX_ATTEMPTS
                ):
                    result.exhausted += 1
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
                    existing.status = EmailDelivery.Status.PENDING
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
