"""Lazy, server-side workbench queries for creator outreach records."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Mapping
from urllib.parse import urlencode
from uuid import UUID

from django.core.paginator import Paginator
from django.db.models import CharField, F, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone

from creator_contact.models import (
    ContactedCreator,
    CreatorContactTarget,
    CreatorContactTaskStep,
)
from mailing.models import EmailDelivery
from tasks.models import ImportTask


PAGE_SIZE = 20
RECORD_CONTACT = "contact"
RECORD_EMAIL = "email"
RESULT_SUCCESS = "success"
RESULT_FAILED = "failed"


def _text(params: Mapping[str, object], key: str) -> str:
    value = params.get(key, "")
    return str(value or "").strip()


def _parse_date(value: str) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def _aware_day_start(value: date) -> datetime:
    return timezone.make_aware(datetime.combine(value, time.min))


def _valid_import_task_id(value: str) -> str:
    if not value:
        return ""
    return str(UUID(value))


def _dashboard_url(values: dict[str, str | int]) -> str:
    query = urlencode(
        {
            key: value
            for key, value in values.items()
            if value not in {"", None}
        }
    )
    base = reverse("tasks:dashboard")
    return f"{base}?{query}#outreach-records" if query else f"{base}#outreach-records"


def _email_state(record: ContactedCreator) -> tuple[str, str]:
    creator = record.creator
    if creator is None or not creator.email:
        return "无邮箱", "no-email"
    status = getattr(record, "email_delivery_status", "") or ""
    if not status:
        return "未发送", "not-sent"
    return (
        dict(EmailDelivery.Status.choices).get(status, status),
        status.casefold(),
    )


def build_outreach_records_context(
    params: Mapping[str, object],
    *,
    store_id: str = "",
) -> dict[str, object]:
    """Return one filtered page only; inactive tabs never hit record tables."""
    record_type = _text(params, "record_type")
    if record_type not in {RECORD_CONTACT, RECORD_EMAIL}:
        record_type = RECORD_CONTACT

    contact_result = _text(params, "contact_result")
    if contact_result not in {RESULT_SUCCESS, RESULT_FAILED}:
        contact_result = RESULT_SUCCESS

    creator_search = _text(params, "creator_id").lstrip("@").casefold()
    import_task_id = _text(params, "import_task")
    date_from_text = _text(params, "date_from")
    date_to_text = _text(params, "date_to")
    email_status = _text(params, "email_status").upper()
    valid_email_statuses = {value for value, _label in EmailDelivery.Status.choices}
    if email_status not in valid_email_statuses:
        email_status = ""

    filter_errors: list[str] = []
    try:
        import_task_id = _valid_import_task_id(import_task_id)
    except ValueError:
        import_task_id = ""
        filter_errors.append("导入批次参数无效，已忽略该条件。")

    try:
        date_from = _parse_date(date_from_text)
        date_to = _parse_date(date_to_text)
    except ValueError:
        date_from = None
        date_to = None
        filter_errors.append("日期格式无效，请使用正确的开始和结束日期。")
    if date_from and date_to and date_from > date_to:
        date_from = None
        date_to = None
        filter_errors.append("开始日期不能晚于结束日期。")

    start_at = _aware_day_start(date_from) if date_from else None
    end_at = (
        _aware_day_start(date_to + timedelta(days=1))
        if date_to
        else None
    )

    if record_type == RECORD_EMAIL:
        records = EmailDelivery.objects.select_related(
            "creator",
            "source_import_task",
        ).annotate(
            activity_at=Coalesce("sent_at", "last_attempt_at"),
        )
        if creator_search:
            records = records.filter(
                Q(creator_id_snapshot__icontains=creator_search)
                | Q(creator_name_snapshot__icontains=creator_search)
                | Q(recipient_email__icontains=creator_search)
            )
        if import_task_id:
            records = records.filter(source_import_task_id=import_task_id)
        if email_status:
            records = records.filter(status=email_status)
        if start_at:
            records = records.filter(activity_at__gte=start_at)
        if end_at:
            records = records.filter(activity_at__lt=end_at)
        records = records.order_by(
            F("activity_at").desc(nulls_last=True),
            "-updated_at",
            "-id",
        )
    elif contact_result == RESULT_FAILED:
        latest_failed_step = (
            CreatorContactTaskStep.objects
            .filter(
                target_id=OuterRef("pk"),
                status=CreatorContactTaskStep.Status.FAILED,
            )
            .order_by("-sequence", "-id")
        )
        records = (
            CreatorContactTarget.objects
            .filter(status=CreatorContactTarget.Status.FAILED)
            .select_related(
                "creator",
                "task",
                "task__source_import_task",
            )
            .annotate(
                recorded_at=Coalesce("finished_at", "updated_at"),
                failed_step_label=Subquery(
                    latest_failed_step.values("label")[:1],
                    output_field=CharField(),
                ),
            )
        )
        if store_id:
            records = records.filter(task__store_id=store_id)
        if creator_search:
            records = records.filter(
                Q(normalized_handle__icontains=creator_search)
                | Q(nickname_snapshot__icontains=creator_search)
            )
        if import_task_id:
            records = records.filter(
                task__source_import_task_id=import_task_id
            )
        if start_at:
            records = records.filter(recorded_at__gte=start_at)
        if end_at:
            records = records.filter(recorded_at__lt=end_at)
        records = records.order_by("-recorded_at", "-id")
    else:
        latest_email_status = (
            EmailDelivery.objects
            .filter(creator_id=OuterRef("creator_id"))
            .order_by("-updated_at", "-id")
        )
        records = (
            ContactedCreator.objects
            .select_related(
                "creator",
                "contact_task",
                "contact_task__source_import_task",
            )
            .annotate(
                email_delivery_status=Subquery(
                    latest_email_status.values("status")[:1],
                    output_field=CharField(),
                ),
            )
        )
        if store_id:
            records = records.filter(store_id=store_id)
        if creator_search:
            records = records.filter(
                Q(normalized_handle__icontains=creator_search)
                | Q(creator__nickname__icontains=creator_search)
                | Q(creator__email__icontains=creator_search)
            )
        if import_task_id:
            records = records.filter(
                contact_task__source_import_task_id=import_task_id
            )
        if start_at:
            records = records.filter(contacted_at__gte=start_at)
        if end_at:
            records = records.filter(contacted_at__lt=end_at)
        records = records.order_by("-contacted_at", "-id")

    paginator = Paginator(records, PAGE_SIZE)
    page_obj = paginator.get_page(_text(params, "page") or "1")
    if record_type == RECORD_CONTACT and contact_result == RESULT_SUCCESS:
        for record in page_obj.object_list:
            record.email_state_label, record.email_state_code = _email_state(
                record
            )

    common_values: dict[str, str | int] = {
        "creator_id": creator_search,
        "import_task": import_task_id,
        "date_from": date_from_text,
        "date_to": date_to_text,
    }

    def link_values(
        destination: str,
        *,
        result: str = "",
        page: int | None = None,
        clear: bool = False,
    ) -> dict[str, str | int]:
        values = {} if clear else dict(common_values)
        values["record_type"] = destination
        if destination == RECORD_CONTACT:
            values["contact_result"] = result or contact_result
        elif email_status and not clear:
            values["email_status"] = email_status
        if page is not None:
            values["page"] = page
        return values

    previous_url = (
        _dashboard_url(
            link_values(
                record_type,
                result=contact_result,
                page=page_obj.previous_page_number(),
            )
        )
        if page_obj.has_previous()
        else ""
    )
    next_url = (
        _dashboard_url(
            link_values(
                record_type,
                result=contact_result,
                page=page_obj.next_page_number(),
            )
        )
        if page_obj.has_next()
        else ""
    )

    import_batches = (
        ImportTask.objects
        .filter(
            status__in=[
                ImportTask.Status.SUCCESS,
                ImportTask.Status.PARTIAL_SUCCESS,
            ]
        )
        .values("id", "file_name", "created_at")
        .order_by("-created_at")
    )
    return {
        "record_type": record_type,
        "contact_result": contact_result,
        "creator_search": creator_search,
        "selected_import_task_id": import_task_id,
        "date_from": date_from_text,
        "date_to": date_to_text,
        "email_status": email_status,
        "email_status_choices": EmailDelivery.Status.choices,
        "filter_errors": filter_errors,
        "import_batches": import_batches,
        "page_obj": page_obj,
        "page_size": PAGE_SIZE,
        "contact_success_url": _dashboard_url(
            link_values(RECORD_CONTACT, result=RESULT_SUCCESS)
        ),
        "contact_failed_url": _dashboard_url(
            link_values(RECORD_CONTACT, result=RESULT_FAILED)
        ),
        "email_records_url": _dashboard_url(
            link_values(RECORD_EMAIL)
        ),
        "clear_filters_url": _dashboard_url(
            link_values(
                record_type,
                result=contact_result,
                clear=True,
            )
        ),
        "previous_page_url": previous_url,
        "next_page_url": next_url,
    }
