"""Creator import previews, confirmed task progress, and workbench views."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import Case, IntegerField, Value, When
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from creator_contact.models import ContactedCreator, CreatorContactTask
from mailing.models import EmailDelivery

from .models import (
    Creator,
    ImportRuleVersion,
    ImportTask,
)
from .services.ai_rule_advisor import RuleAdvisorError
from .services.import_runner import cleanup_task_source, save_confirmed_source
from .services.preview_service import (
    create_preview,
    preview_payload,
    preview_store,
    revise_preview,
)
from .services.preview_store import PreviewExpiredError
from .services.spreadsheet_reader import (
    SpreadsheetValidationError,
    list_sheet_names,
    safe_file_name,
    validate_upload,
)

logger = logging.getLogger(__name__)


def _json_error(message: str, *, status: int = 400, code: str = "INVALID_REQUEST"):
    return JsonResponse(
        {"success": False, "error": message, "code": code},
        status=status,
    )


def _read_uploaded_file(request: HttpRequest):
    uploaded = request.FILES.get("file")
    if uploaded is None:
        raise SpreadsheetValidationError("请选择要上传的达人表格。")
    file_name = safe_file_name(uploaded.name)
    file_bytes = uploaded.read()
    validate_upload(
        file_name=file_name,
        content_type=uploaded.content_type or "",
        file_bytes=file_bytes,
        max_size=settings.IMPORT_MAX_FILE_SIZE_BYTES,
    )
    return file_name, file_bytes


def _saved_file_name(request: HttpRequest, original_file_name: str) -> str:
    requested = str(request.POST.get("saved_file_name") or "").strip()
    if not requested:
        return original_file_name

    saved_name = safe_file_name(requested)
    original_extension = Path(original_file_name).suffix.casefold()
    requested_extension = Path(saved_name).suffix.casefold()
    if not requested_extension:
        saved_name = f"{saved_name[: 255 - len(original_extension)]}{original_extension}"
    elif requested_extension != original_extension:
        raise SpreadsheetValidationError(
            f"数据库记录名称必须保留 {original_extension} 扩展名。"
        )
    if not Path(saved_name).stem.strip(" ."):
        raise SpreadsheetValidationError("数据库记录名称不能为空。")
    return saved_name


@require_GET
def dashboard(request: HttpRequest) -> HttpResponse:
    today = timezone.localdate()
    store_id = str(
        getattr(settings, "ZINIAO_CONTACT_STORE_ID", "") or ""
    ).strip()
    contacted_creators = ContactedCreator.objects.all()
    contact_tasks = CreatorContactTask.objects.all()
    if store_id:
        contacted_creators = contacted_creators.filter(store_id=store_id)
        contact_tasks = contact_tasks.filter(store_id=store_id)

    active_status_order = Case(
        When(status=CreatorContactTask.Status.RUNNING, then=Value(0)),
        default=Value(1),
        output_field=IntegerField(),
    )
    active_contact_tasks = (
        contact_tasks.filter(
            status__in=[
                CreatorContactTask.Status.RUNNING,
                CreatorContactTask.Status.PENDING,
            ]
        )
        .select_related("source_import_task")
        .annotate(active_order=active_status_order)
        .order_by("active_order", "-created_at")[:4]
    )
    email_status_order = Case(
        When(status=EmailDelivery.Status.SENDING, then=Value(0)),
        default=Value(1),
        output_field=IntegerField(),
    )
    active_email_deliveries = list(
        EmailDelivery.objects.filter(
            status__in=[
                EmailDelivery.Status.SENDING,
                EmailDelivery.Status.PENDING,
            ]
        )
        .annotate(active_order=email_status_order)
        .order_by("active_order", "created_at", "id")[:4]
    )
    for delivery in active_email_deliveries:
        delivery.ui_progress = (
            55 if delivery.status == EmailDelivery.Status.SENDING else 0
        )

    latest_updates = [
        value
        for value in [
            ImportTask.objects.order_by("-updated_at")
            .values_list("updated_at", flat=True)
            .first(),
            contact_tasks.order_by("-updated_at")
            .values_list("updated_at", flat=True)
            .first(),
            EmailDelivery.objects.order_by("-updated_at")
            .values_list("updated_at", flat=True)
            .first(),
        ]
        if value is not None
    ]
    context = {
        "now": timezone.localtime(),
        "store_id": store_id,
        "creator_total": Creator.objects.count(),
        "creator_today": Creator.objects.filter(created_at__date=today).count(),
        "contact_today": contacted_creators.filter(
            created_at__date=today
        ).count(),
        "contact_total": contacted_creators.count(),
        "email_today": EmailDelivery.objects.filter(
            status=EmailDelivery.Status.SENT,
            sent_at__date=today,
        ).count(),
        "email_pending": EmailDelivery.objects.filter(
            status=EmailDelivery.Status.PENDING
        ).count(),
        "email_total": EmailDelivery.objects.filter(
            status=EmailDelivery.Status.SENT
        ).count(),
        "contact_pending": contact_tasks.filter(
            status=CreatorContactTask.Status.PENDING
        ).count(),
        "email_sending": EmailDelivery.objects.filter(
            status=EmailDelivery.Status.SENDING
        ).count(),
        "active_contact_tasks": active_contact_tasks,
        "active_email_deliveries": active_email_deliveries,
        "task_count": ImportTask.objects.count(),
        "latest_update": (
            timezone.localtime(max(latest_updates))
            if latest_updates
            else None
        ),
    }
    return render(request, "tasks/dashboard.html", context)


@require_GET
def creator_data(request: HttpRequest) -> HttpResponse:
    tasks = ImportTask.objects.all()
    today = timezone.localdate()
    context = {
        "recent_tasks": tasks[:20],
        "today_count": tasks.filter(created_at__date=today).count(),
        "running_count": tasks.filter(
            status__in=[ImportTask.Status.QUEUED, ImportTask.Status.IMPORTING]
        ).count(),
        "success_count": tasks.filter(
            status=ImportTask.Status.SUCCESS
        ).count(),
        "partial_count": tasks.filter(
            status=ImportTask.Status.PARTIAL_SUCCESS
        ).count(),
        "task_count": tasks.count(),
        "now": timezone.localtime(),
        "max_file_size_mb": settings.IMPORT_MAX_FILE_SIZE_BYTES
        // (1024 * 1024),
        "preview_ttl_minutes": settings.IMPORT_PREVIEW_TTL_SECONDS // 60,
        "import_rule_model": settings.IMPORT_RULE_MODEL,
    }
    return render(request, "tasks/creator_data.html", context)


@require_POST
def preview_import(request: HttpRequest) -> JsonResponse:
    try:
        file_name, file_bytes = _read_uploaded_file(request)
        sheet_names = list_sheet_names(file_name, file_bytes)
        requested_sheet = str(request.POST.get("sheet_name") or "").strip()
        if len(sheet_names) > 1 and not requested_sheet:
            return JsonResponse(
                {
                    "success": True,
                    "requiresSheetSelection": True,
                    "sheetNames": list(sheet_names),
                    "fileName": file_name,
                }
            )
        state = create_preview(
            file_name=file_name,
            file_bytes=file_bytes,
            sheet_name=requested_sheet or sheet_names[0],
        )
        return JsonResponse(preview_payload(state))
    except SpreadsheetValidationError as error:
        return _json_error(str(error), code="INVALID_SPREADSHEET")
    except ValueError as error:
        return _json_error(str(error), code="PREVIEW_FAILED")
    except Exception:
        logger.exception("Unexpected creator spreadsheet preview failure")
        return _json_error(
            "预览服务处理文件时发生异常，请检查服务日志后重试。",
            status=500,
            code="PREVIEW_INTERNAL_ERROR",
        )


@require_POST
def revise_import_preview(
    request: HttpRequest,
    preview_id,
) -> JsonResponse:
    try:
        state = revise_preview(
            preview_id=str(preview_id),
            instruction=str(request.POST.get("instruction") or ""),
        )
        return JsonResponse(preview_payload(state))
    except PreviewExpiredError:
        return _json_error(
            "预览已过期，请重新选择文件。",
            status=410,
            code="PREVIEW_EXPIRED",
        )
    except RuleAdvisorError as error:
        return _json_error(str(error), code="RULE_ADVISOR_FAILED")
    except ValueError as error:
        return _json_error(str(error), code="RULE_VALIDATION_FAILED")


@require_POST
def confirm_import(request: HttpRequest, preview_id) -> JsonResponse:
    try:
        state = preview_store.get(str(preview_id))
        file_name, file_bytes = _read_uploaded_file(request)
        saved_file_name = _saved_file_name(request, state.file_name)
    except PreviewExpiredError:
        return _json_error(
            "预览已过期，请重新选择文件。",
            status=410,
            code="PREVIEW_EXPIRED",
        )
    except SpreadsheetValidationError as error:
        return _json_error(str(error), code="INVALID_SPREADSHEET")

    digest = hashlib.sha256(file_bytes).hexdigest()
    if digest != state.file_sha256:
        return _json_error(
            "确认文件与预览文件不一致，请重新生成预览。",
            status=409,
            code="FILE_CHANGED",
        )
    if file_name != state.file_name:
        return _json_error(
            "确认文件名与预览文件不一致，请重新生成预览。",
            status=409,
            code="FILE_CHANGED",
        )

    now = timezone.now()
    try:
        with transaction.atomic():
            task = ImportTask.objects.create(
                file_name=saved_file_name,
                file_sha256=state.file_sha256,
                sheet_name=state.sheet_name,
                status=ImportTask.Status.QUEUED,
                confirmed_rule_version=state.current_version,
                current_step="等待导入 Worker",
                snapshot_date=timezone.localdate(),
                confirmed_at=now,
            )
            ImportRuleVersion.objects.bulk_create(
                [
                    ImportRuleVersion(
                        import_task=task,
                        version=entry.version,
                        rule_json=entry.rule,
                        source=entry.source,
                        user_instruction=entry.user_instruction,
                    )
                    for entry in state.rule_history
                ]
            )
            save_confirmed_source(task, file_bytes)
    except Exception:
        if "task" in locals():
            cleanup_task_source(task.pk)
        return _json_error(
            "创建导入任务失败，请重新生成预览后再试。",
            status=500,
            code="CONFIRM_FAILED",
        )

    preview_store.delete(str(preview_id))
    return JsonResponse(
        {
            "success": True,
            "taskId": str(task.pk),
            "detailUrl": reverse(
                "tasks:import-detail",
                kwargs={"task_id": task.pk},
            ),
        },
        status=202,
    )


@require_GET
def import_detail(request: HttpRequest, task_id) -> HttpResponse:
    task = get_object_or_404(
        ImportTask.objects.prefetch_related(
            "rule_versions",
            "row_errors",
            "logs",
        ),
        pk=task_id,
    )
    memberships = list(
        task.imported_creators.select_related("creator")
        .order_by("first_row_number")[:200]
    )
    sales_by_creator: dict[int, dict[int, object]] = {}
    for metric in task.sales_metrics.order_by(
        "source_row_number",
        "window_days",
    ):
        sales_by_creator.setdefault(metric.creator_id, {})[
            metric.window_days
        ] = metric.sales_amount
    sales_windows = sorted(
        {
            window_days
            for creator_metrics in sales_by_creator.values()
            for window_days in creator_metrics
        }
    )
    creator_rows = [
        {
            "membership": membership,
            "creator": membership.creator,
            "sales_cells": [
                {
                    "window_days": window_days,
                    "amount": sales_by_creator.get(
                        membership.creator_id,
                        {},
                    ).get(window_days),
                }
                for window_days in sales_windows
            ],
        }
        for membership in memberships
    ]
    return render(
        request,
        "tasks/task_detail.html",
        {
            "task": task,
            "creator_rows": creator_rows,
            "sales_windows": sales_windows,
            "errors": task.row_errors.all()[:100],
            "task_count": ImportTask.objects.count(),
        },
    )


@require_GET
def import_status(request: HttpRequest, task_id) -> JsonResponse:
    task = get_object_or_404(ImportTask, pk=task_id)
    progress = (
        min(100, round(task.processed_rows / task.total_rows * 100))
        if task.total_rows
        else (10 if task.status == ImportTask.Status.IMPORTING else 0)
    )
    if task.status in {
        ImportTask.Status.SUCCESS,
        ImportTask.Status.PARTIAL_SUCCESS,
        ImportTask.Status.FAILED,
    }:
        progress = 100
    return JsonResponse(
        {
            "id": str(task.pk),
            "status": task.status,
            "statusLabel": task.get_status_display(),
            "progress": progress,
            "currentStep": task.current_step,
            "totalRows": task.total_rows,
            "processedRows": task.processed_rows,
            "successRows": task.success_rows,
            "partialSuccessRows": task.partial_success_rows,
            "failedRows": task.failed_rows,
            "errorMessage": task.error_message or None,
            "updatedAt": task.updated_at.isoformat(),
            "detailUrl": reverse(
                "tasks:import-detail",
                kwargs={"task_id": task.pk},
            ),
        }
    )


@require_http_methods(["GET"])
def import_errors(request: HttpRequest, task_id) -> JsonResponse:
    task = get_object_or_404(ImportTask, pk=task_id)
    try:
        page = max(1, int(request.GET.get("page") or 1))
        page_size = min(100, max(1, int(request.GET.get("page_size") or 50)))
    except (TypeError, ValueError):
        return _json_error("page 和 page_size 必须是整数。")
    queryset = task.row_errors.all()
    total = queryset.count()
    start = (page - 1) * page_size
    items = queryset[start : start + page_size]
    return JsonResponse(
        {
            "success": True,
            "page": page,
            "pageSize": page_size,
            "total": total,
            "errors": [
                {
                    "rowNumber": item.row_number,
                    "creatorId": item.creator_id,
                    "errorCode": item.error_code,
                    "errorField": item.error_field,
                    "errorMessage": item.error_message,
                    "fatal": item.is_fatal,
                }
                for item in items
            ],
        }
    )


@require_POST
def retry_import(request: HttpRequest, task_id) -> HttpResponse:
    # Confirmed source files are deleted after every terminal run. A retry must
    # therefore go through a new preview/confirmation to guarantee file identity.
    get_object_or_404(ImportTask, pk=task_id)
    return redirect("tasks:creator-data")
