"""Synchronize ongoing directed-collaboration options for one store."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from creator_contact.models import (
    CollaborationSyncJob,
    DirectedCollaborationOption,
)
from shared.logger import JsonlAuditLogger, project_log_root


class CollaborationSyncCapabilityError(RuntimeError):
    """Raised until a store-page collaboration collector is available."""


class CollaborationSyncBrowserBusyError(
    CollaborationSyncCapabilityError
):
    """Raised when another task temporarily owns the store browser."""


@dataclass(frozen=True)
class CollaborationSyncBatch:
    """A collector result with an explicit full-snapshot guarantee."""

    rows: tuple[dict[str, Any], ...]
    complete: bool = True
    explicit_empty: bool = False

    @classmethod
    def from_rows(
        cls,
        rows: Iterable[dict[str, Any]],
        *,
        complete: bool = True,
        explicit_empty: bool | None = None,
    ) -> "CollaborationSyncBatch":
        normalized = tuple(rows)
        return cls(
            rows=normalized,
            complete=complete,
            explicit_empty=(
                not normalized
                if explicit_empty is None
                else explicit_empty
            ),
        )

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self.rows)


class SubprocessCollaborationExecutor:
    """Collect ongoing collaborations through the non-interactive Ziniao CLI."""

    def __init__(
        self,
        *,
        timeout_seconds: int | None = None,
        python_executable: str | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds or settings.TASK_TIMEOUT_SECONDS
        self.python_executable = (
            python_executable or settings.AUTOMATION_PYTHON_EXECUTABLE
        )

    def __call__(self, store_id: str) -> Iterable[dict[str, Any]]:
        required_environment = (
            "ZINIAO_COMPANY",
            "ZINIAO_USERNAME",
            "ZINIAO_PASSWORD",
        )
        missing = [
            name for name in required_environment if not os.getenv(name)
        ]
        if missing:
            raise CollaborationSyncCapabilityError(
                "定向合作同步 Worker 缺少必要环境变量："
                + "、".join(missing)
                + "。"
            )
        environment = os.environ.copy()
        package_src = Path(settings.PROJECT_ROOT) / "ziniao-automation" / "src"
        prior_pythonpath = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = (
            str(package_src)
            if not prior_pythonpath
            else f"{package_src}{os.pathsep}{prior_pythonpath}"
        )
        command = [
            self.python_executable,
            "-m",
            "ziniao_automation.collaboration_sync_runner",
            "--store-id",
            str(store_id),
            "--json",
        ]
        audit_logger = JsonlAuditLogger(
            root=project_log_root(settings.PROJECT_ROOT),
            category="regular",
            component="collaboration-sync",
            task_id=str(store_id),
        )
        started_at = time.perf_counter()
        audit_logger.write(
            "subprocess_started",
            status="STARTED",
            operation="collaboration_sync_runner",
            input_content={
                "command": command,
                "storeId": str(store_id),
                "timeoutSeconds": self.timeout_seconds,
            },
        )
        try:
            completed = subprocess.run(
                command,
                cwd=settings.PROJECT_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            audit_logger.write(
                "subprocess_finished",
                status="TIMEOUT",
                operation="collaboration_sync_runner",
                input_content={
                    "command": command,
                    "storeId": str(store_id),
                },
                output_content={
                    "stdout": error.stdout or "",
                    "stderr": error.stderr or "",
                },
                error={
                    "type": type(error).__name__,
                    "message": str(error),
                },
                duration_ms=(time.perf_counter() - started_at) * 1000,
            )
            raise CollaborationSyncCapabilityError(
                "定向合作同步子进程执行超时。"
            ) from error
        audit_logger.write(
            "subprocess_finished",
            status=(
                "SUCCESS"
                if completed.returncode == 0
                else "FAILED"
            ),
            operation="collaboration_sync_runner",
            input_content={
                "command": command,
                "storeId": str(store_id),
            },
            output_content={
                "returnCode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            },
            duration_ms=(time.perf_counter() - started_at) * 1000,
        )
        try:
            payload = self._last_json_payload(completed.stdout)
        except CollaborationSyncCapabilityError:
            payload = {}
        if completed.returncode != 0:
            message = str(
                payload.get("errorMessage")
                or payload.get("errorCode")
                or completed.stderr
                or completed.stdout
                or ""
            ).strip()
            for name in required_environment:
                sensitive = os.getenv(name, "")
                if sensitive:
                    message = message.replace(sensitive, "[REDACTED]")
            if payload.get("errorCode") == "BROWSER_BUSY":
                raise CollaborationSyncBrowserBusyError(
                    message[-2000:]
                    or "目标店铺浏览器正忙，请稍后重试。"
                )
            raise CollaborationSyncCapabilityError(
                message[-2000:]
                or (
                    "定向合作同步子进程异常退出"
                    f"（{completed.returncode}）。"
                )
            )
        if not payload:
            payload = self._last_json_payload(completed.stdout)
        if payload.get("success") is not True:
            error = payload.get("error") or {}
            if isinstance(error, dict):
                message = error.get("userMessage") or error.get("message")
            else:
                message = error
            normalized_message = str(
                message
                or payload.get("errorMessage")
                or payload.get("errorCode")
                or "定向合作同步未返回成功状态。"
            )
            if payload.get("errorCode") == "BROWSER_BUSY":
                raise CollaborationSyncBrowserBusyError(
                    normalized_message
                )
            raise CollaborationSyncCapabilityError(normalized_message)
        data = payload.get("data")
        if not isinstance(data, dict):
            data = payload
        missing = object()
        rows: object = missing
        for key in (
            "collaborations",
            "options",
            "invitations",
            "rows",
        ):
            if key in data:
                rows = data[key]
                break
        if rows is missing:
            raise CollaborationSyncCapabilityError(
                "定向合作同步成功响应未声明完整结果数组。"
            )
        if not isinstance(rows, list) or not all(
            isinstance(row, dict) for row in rows
        ):
            raise CollaborationSyncCapabilityError(
                "定向合作同步结果中的 collaborations 不是对象数组。"
            )
        complete = data.get("complete", payload.get("complete", True))
        if complete is not True:
            raise CollaborationSyncCapabilityError(
                "定向合作同步结果未声明为完整快照。"
            )
        return CollaborationSyncBatch.from_rows(
            rows,
            complete=True,
            explicit_empty=not rows,
        )

    @staticmethod
    def _last_json_payload(output: str) -> dict[str, Any]:
        stripped = str(output or "").strip()
        try:
            whole = json.loads(stripped)
        except json.JSONDecodeError:
            whole = None
        if isinstance(whole, dict):
            return whole
        for line in reversed(stripped.splitlines()):
            try:
                payload = json.loads(line.strip())
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        raise CollaborationSyncCapabilityError(
            "定向合作同步子进程未输出标准 JSON。"
        )


def _status(value: object) -> str:
    normalized = str(value or "").strip().casefold()
    mapping = {
        "ongoing": DirectedCollaborationOption.Status.ONGOING,
        "in_progress": DirectedCollaborationOption.Status.ONGOING,
        "in progress": DirectedCollaborationOption.Status.ONGOING,
        "进行中": DirectedCollaborationOption.Status.ONGOING,
        "expiring": DirectedCollaborationOption.Status.EXPIRING,
        "即将到期": DirectedCollaborationOption.Status.EXPIRING,
        "cancelled": DirectedCollaborationOption.Status.CANCELLED,
        "取消中": DirectedCollaborationOption.Status.CANCELLED,
        "completed": DirectedCollaborationOption.Status.COMPLETED,
        "已完成": DirectedCollaborationOption.Status.COMPLETED,
    }
    return mapping.get(normalized, DirectedCollaborationOption.Status.UNKNOWN)


def _integer(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


_GROUP_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
)


def _group_id(row: dict[str, Any]) -> str:
    values: list[str] = []
    for key in (
        "invitationGroupId",
        "externalInvitationId",
        "id",
    ):
        if key not in row:
            continue
        raw_value = row[key]
        if raw_value is None or raw_value == "":
            continue
        if isinstance(raw_value, bool) or not isinstance(
            raw_value,
            (str, int),
        ):
            raise CollaborationSyncCapabilityError(
                "定向合作同步包含无效 invitationGroupId。"
            )
        normalized = str(raw_value).strip()
        if not _GROUP_ID_PATTERN.fullmatch(normalized):
            raise CollaborationSyncCapabilityError(
                "定向合作同步包含无效 invitationGroupId。"
            )
        values.append(normalized)
    if not values:
        raise CollaborationSyncCapabilityError(
            "定向合作同步包含缺失 invitationGroupId 的记录。"
        )
    if len(set(values)) != 1:
        raise CollaborationSyncCapabilityError(
            "定向合作同步记录中的 invitationGroupId 字段不一致。"
        )
    return values[0]


class CollaborationSyncRunner:
    def __init__(
        self,
        job: CollaborationSyncJob,
        *,
        executor: Callable[[str], Iterable[dict[str, Any]]] | None = None,
    ) -> None:
        self.job = job
        self.executor = executor or SubprocessCollaborationExecutor()

    def run(self) -> CollaborationSyncJob:
        self.job.status = CollaborationSyncJob.Status.RUNNING
        self.job.started_at = timezone.now()
        self.job.finished_at = None
        self.job.error_code = ""
        self.job.error_message = ""
        self.job.save()
        try:
            execution_result = self.executor(self.job.store_id)
            rows = list(execution_result)
            if isinstance(execution_result, CollaborationSyncBatch):
                complete = execution_result.complete
                explicit_empty = execution_result.explicit_empty
            else:
                complete = True
                explicit_empty = False
            imported_count, deactivated_count = self._persist(
                rows,
                complete=complete,
                explicit_empty=explicit_empty,
            )
        except CollaborationSyncBrowserBusyError as error:
            self.job.status = CollaborationSyncJob.Status.PENDING
            self.job.error_code = "BROWSER_BUSY"
            self.job.error_message = (
                "目标店铺正在执行其他自动化操作，"
                "同步任务已重新排队等待。"
            )
            if str(error):
                self.job.error_message += f" 原因：{error}"
            self.job.finished_at = None
            self.job.save()
            return self.job
        except Exception as error:
            self.job.status = CollaborationSyncJob.Status.FAILED
            self.job.error_code = type(error).__name__
            self.job.error_message = str(error)
            self.job.finished_at = timezone.now()
            self.job.save()
            return self.job

        self.job.status = CollaborationSyncJob.Status.SUCCESS
        self.job.imported_count = imported_count
        self.job.deactivated_count = deactivated_count
        self.job.finished_at = timezone.now()
        self.job.save()
        return self.job

    @transaction.atomic
    def _persist(
        self,
        rows: list[dict[str, Any]],
        *,
        complete: bool = True,
        explicit_empty: bool = False,
    ) -> tuple[int, int]:
        if not complete:
            raise CollaborationSyncCapabilityError(
                "定向合作同步结果不是完整快照，拒绝更新缓存。"
            )
        if not rows and not explicit_empty:
            raise CollaborationSyncCapabilityError(
                "定向合作同步返回了未明确确认的空结果，"
                "拒绝停用现有缓存。"
            )

        validated: list[tuple[str, str, str, dict[str, Any]]] = []
        seen_ids: set[str] = set()
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                raise CollaborationSyncCapabilityError(
                    f"定向合作同步第 {index} 行不是对象。"
                )
            invitation_group_id = _group_id(row)
            if invitation_group_id in seen_ids:
                raise CollaborationSyncCapabilityError(
                    "定向合作同步包含重复 invitationGroupId："
                    f"{invitation_group_id}。"
                )
            name_value = row.get("name") or row.get("invitationName")
            if not isinstance(name_value, str):
                raise CollaborationSyncCapabilityError(
                    f"定向合作同步第 {index} 行缺少有效邀请名称。"
                )
            name = name_value.strip()
            row_status = _status(row.get("status") or "ONGOING")
            if (
                not name
                or len(name) > 255
                or row_status != DirectedCollaborationOption.Status.ONGOING
            ):
                raise CollaborationSyncCapabilityError(
                    f"定向合作同步第 {index} 行不是有效的进行中邀请。"
                )
            seen_ids.add(invitation_group_id)
            validated.append(
                (
                    invitation_group_id,
                    name,
                    row_status,
                    row,
                )
            )

        imported_count = 0
        synced_at = timezone.now()
        for (
            invitation_group_id,
            name,
            row_status,
            row,
        ) in validated:
            DirectedCollaborationOption.objects.update_or_create(
                store_id=self.job.store_id,
                external_invitation_id=invitation_group_id,
                defaults={
                    "name": name,
                    "status": row_status,
                    "product_count": _integer(row.get("productCount")),
                    "invited_creator_count": _integer(
                        row.get("invitedCreatorCount")
                        or row.get("invitedCount")
                    ),
                    "accepted_creator_count": _integer(
                        row.get("acceptedCreatorCount")
                        or row.get("acceptedCount")
                    ),
                    "promoted_creator_count": _integer(
                        row.get("promotedCreatorCount")
                        or row.get("promotedCount")
                    ),
                    "raw_data": row,
                    "is_active": True,
                    "last_synced_at": synced_at,
                },
            )
            imported_count += 1

        active = DirectedCollaborationOption.objects.filter(
            store_id=self.job.store_id,
            is_active=True,
        )
        if seen_ids:
            stale = active.exclude(external_invitation_id__in=seen_ids)
        else:
            stale = active
        deactivated_count = stale.update(
            is_active=False,
            status=DirectedCollaborationOption.Status.UNKNOWN,
            last_synced_at=synced_at,
        )
        return imported_count, deactivated_count
