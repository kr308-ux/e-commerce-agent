"""Persist one confirmed creator import with the locked preview rule."""

from __future__ import annotations

import shutil
import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from tasks.models import (
    Creator,
    CreatorSalesMetric,
    ImportLog,
    ImportRowError,
    ImportRuleVersion,
    ImportTask,
    ImportTaskCreator,
)

from .import_transformer import transform_rows
from .spreadsheet_reader import read_sheet


def _safe_error_message(error: Exception) -> str:
    message = str(error)
    for sensitive_path in (
        settings.PROJECT_ROOT,
        settings.IMPORT_TEMP_ROOT,
    ):
        message = message.replace(str(sensitive_path), "[受控路径]")
    return message[:2000] or type(error).__name__


def task_source_directory(task_id: object) -> Path:
    return Path(settings.IMPORT_TEMP_ROOT) / str(task_id)


def task_source_path(task: ImportTask) -> Path:
    extension = Path(task.file_name).suffix.casefold()
    return task_source_directory(task.pk) / f"source{extension}"


def save_confirmed_source(task: ImportTask, file_bytes: bytes) -> Path:
    directory = task_source_directory(task.pk)
    directory.mkdir(parents=True, exist_ok=False)
    path = task_source_path(task)
    path.write_bytes(file_bytes)
    return path


def cleanup_task_source(task_id: object) -> None:
    directory = task_source_directory(task_id)
    if directory.exists():
        shutil.rmtree(directory)


def cleanup_stale_sources(*, max_age_hours: int = 24) -> int:
    """Remove abandoned confirmed uploads after crashes or deleted tasks."""
    root = Path(settings.IMPORT_TEMP_ROOT).resolve()
    root.mkdir(parents=True, exist_ok=True)
    cutoff = timezone.now() - timedelta(hours=max_age_hours)
    removed = 0
    for candidate in root.iterdir():
        if not candidate.is_dir():
            continue
        try:
            task_id = uuid.UUID(candidate.name)
        except ValueError:
            continue
        task = ImportTask.objects.filter(pk=task_id).first()
        should_keep = (
            task is not None
            and task.status in {
                ImportTask.Status.QUEUED,
                ImportTask.Status.IMPORTING,
            }
            and task.created_at >= cutoff
        )
        if not should_keep:
            shutil.rmtree(candidate)
            removed += 1
    return removed


def _log(
    task: ImportTask,
    *,
    step: str,
    status: str,
    message: str,
    details: dict[str, object] | None = None,
) -> None:
    ImportLog.objects.create(
        import_task=task,
        step=step,
        status=status,
        message=message,
        details_json=details or {},
    )


def run_import_task(task: ImportTask) -> ImportTask:
    task.status = ImportTask.Status.IMPORTING
    task.started_at = timezone.now()
    task.current_step = "读取已确认文件"
    task.error_message = ""
    task.save()
    _log(
        task,
        step="IMPORT_STARTED",
        status=ImportLog.Status.INFO,
        message="完整表格转换开始。",
    )

    source_path = task_source_path(task)
    try:
        file_bytes = source_path.read_bytes()
        sheet = read_sheet(
            file_name=task.file_name,
            file_bytes=file_bytes,
            sheet_name=task.sheet_name,
            max_rows=None,
        )
        rule_version = ImportRuleVersion.objects.get(
            import_task=task,
            version=task.confirmed_rule_version,
        )
        converted_rows = list(
            transform_rows(sheet.rows, rule_version.rule_json)
        )
        task.total_rows = len(converted_rows)
        task.current_step = "写入达人和销售额数据"
        task.save(update_fields=["total_rows", "current_step", "updated_at"])

        with transaction.atomic():
            task = ImportTask.objects.select_for_update().get(pk=task.pk)
            task.imported_creators.all().delete()
            task.sales_metrics.all().delete()
            task.row_errors.all().delete()

            success_rows = 0
            partial_rows = 0
            failed_rows = 0
            new_creator_ids: set[int] = set()
            updated_creator_ids: set[int] = set()
            sales_metric_count = 0

            for converted in converted_rows:
                for issue in converted.issues:
                    ImportRowError.objects.create(
                        import_task=task,
                        row_number=converted.row_number,
                        creator_id=converted.creator_id,
                        error_code=issue.code,
                        error_field=issue.field,
                        error_message=issue.message,
                        is_fatal=issue.fatal,
                    )

                if converted.status == "FAILED":
                    failed_rows += 1
                    continue

                creator, created = Creator.objects.get_or_create(
                    creator_id=converted.creator_id,
                    defaults={
                        "nickname": converted.nickname,
                        "email": converted.email,
                    },
                )
                changed_fields: list[str] = []
                if not created:
                    if converted.nickname and converted.nickname != creator.nickname:
                        creator.nickname = converted.nickname
                        changed_fields.append("nickname")
                    if converted.email and converted.email != creator.email:
                        creator.email = converted.email
                        changed_fields.append("email")
                    if changed_fields:
                        creator.save(update_fields=[*changed_fields, "updated_at"])
                        updated_creator_ids.add(creator.pk)
                else:
                    new_creator_ids.add(creator.pk)

                membership, membership_created = ImportTaskCreator.objects.get_or_create(
                    import_task=task,
                    creator=creator,
                    defaults={
                        "first_row_number": converted.row_number,
                        "row_status": converted.status,
                    },
                )
                if (
                    not membership_created
                    and converted.status == "PARTIAL_SUCCESS"
                    and membership.row_status != "PARTIAL_SUCCESS"
                ):
                    membership.row_status = "PARTIAL_SUCCESS"
                    membership.save(update_fields=["row_status"])

                CreatorSalesMetric.objects.bulk_create(
                    [
                        CreatorSalesMetric(
                            creator=creator,
                            import_task=task,
                            window_days=window_days,
                            sales_amount=amount,
                            snapshot_date=task.snapshot_date,
                            source_row_number=converted.row_number,
                        )
                        for window_days, amount in converted.sales.items()
                    ]
                )
                sales_metric_count += len(converted.sales)
                if converted.status == "PARTIAL_SUCCESS":
                    partial_rows += 1
                else:
                    success_rows += 1

            task.processed_rows = len(converted_rows)
            task.success_rows = success_rows
            task.partial_success_rows = partial_rows
            task.failed_rows = failed_rows
            task.new_creator_count = len(new_creator_ids)
            task.updated_creator_count = len(updated_creator_ids - new_creator_ids)
            task.sales_metric_count = sales_metric_count
            task.status = (
                ImportTask.Status.PARTIAL_SUCCESS
                if failed_rows or partial_rows
                else ImportTask.Status.SUCCESS
            )
            task.current_step = "导入完成"
            task.finished_at = timezone.now()
            task.error_message = ""
            task.save()
            _log(
                task,
                step="IMPORT_COMPLETED",
                status=(
                    ImportLog.Status.WARNING
                    if task.status == ImportTask.Status.PARTIAL_SUCCESS
                    else ImportLog.Status.SUCCESS
                ),
                message="达人表格导入完成。",
                details={
                    "totalRows": task.total_rows,
                    "successRows": success_rows,
                    "partialSuccessRows": partial_rows,
                    "failedRows": failed_rows,
                    "newCreators": task.new_creator_count,
                    "updatedCreators": task.updated_creator_count,
                    "salesMetrics": sales_metric_count,
                },
            )
        return ImportTask.objects.get(pk=task.pk)
    except Exception as error:
        task = ImportTask.objects.get(pk=task.pk)
        task.status = ImportTask.Status.FAILED
        task.current_step = "导入失败"
        task.error_message = _safe_error_message(error)
        task.finished_at = timezone.now()
        task.save()
        _log(
            task,
            step="IMPORT_FAILED",
            status=ImportLog.Status.FAILED,
            message="达人表格导入失败。",
            details={"errorType": type(error).__name__},
        )
        return task
    finally:
        cleanup_task_source(task.pk)
