"""Execute frozen creator-contact targets and persist verified outcomes."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Protocol

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from creator_contact.models import (
    ContactedCreator,
    CreatorContactTarget,
    CreatorContactTask,
    CreatorContactTaskStep,
)


TOOL_LABELS = {
    "ziniao_connect": "连接紫鸟店铺",
    "ziniao_open_find_creators": "进入寻找达人",
    "ziniao_search_creator": "搜索并确认达人卡片",
    "ziniao_open_creator_detail": "打开达人详情",
    "ziniao_open_message_panel": "打开私信抽屉",
    "ziniao_open_chat_new_tab": "打开 Cooperation Chat",
    "ziniao_verify_chat_recipient": "核验聊天对象",
    "ziniao_send_approved_greeting": "发送招呼语",
    "ziniao_send_greeting": "发送招呼语",
    "ziniao_open_target_collaboration": "打开定向合作",
    "ziniao_open_other_invitation_dialog": "打开其他邀请",
    "ziniao_select_invitation": "选择定向合作邀请",
    "ziniao_send_selected_invitation": "创建定向合作邀请",
    "ziniao_send_collaboration_card": "发送定向合作卡片",
    "ziniao_disconnect": "断开紫鸟会话",
}
SENSITIVE_PATTERN = re.compile(
    r"(sk-[a-zA-Z0-9_-]{8,}|bearer\s+[a-zA-Z0-9._-]{8,})",
    re.IGNORECASE,
)
ASCII_DIGITS_PATTERN = re.compile(r"^[0-9]+$")
VERIFIED_TARGET_PLAN_FLIGHT_STATUSES = {3, 4}


class ContactExecutor(Protocol):
    def __call__(
        self,
        task: CreatorContactTask,
        target: CreatorContactTarget,
    ) -> dict[str, Any]: ...


class ContactExecutionError(RuntimeError):
    """Raised when a real contact subprocess cannot produce a result."""


def _redact(value: object) -> str:
    return SENSITIVE_PATTERN.sub("[REDACTED]", str(value or ""))


def _numeric_identifier(value: object) -> str:
    if isinstance(value, bool):
        return ""
    text = str(value or "")
    return text if ASCII_DIGITS_PATTERN.fullmatch(text) else ""


def _has_plan_card_server_ids(value: object) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(str(item or "").strip() for item in value)
    )


def _terminal_evidence_error(
    task: CreatorContactTask,
    result: dict[str, Any],
    *,
    actual_invitation_id: str,
    invitation_group_id: str,
) -> tuple[str, str] | None:
    expected_group_id = task.invitation_id_snapshot
    result_group_id = result.get("invitationGroupId")
    if (
        not expected_group_id
        or result_group_id != expected_group_id
        or invitation_group_id != expected_group_id
    ):
        return (
            "INVITATION_GROUP_MISMATCH",
            "返回的定向合作 invitationGroupId 与任务快照不一致。",
        )

    result_invitation_id = _numeric_identifier(result.get("invitationId"))
    if (
        not result_invitation_id
        or actual_invitation_id != result_invitation_id
    ):
        return (
            "INVALID_INVITATION_ID",
            "未取得数字格式的达人实际 invitationId。",
        )

    if not _has_plan_card_server_ids(result.get("planCardServerIds")):
        return (
            "MISSING_PLAN_CARD_SERVER_IDS",
            "未取得已发送定向合作卡片的服务端消息 ID。",
        )

    flight_status = result.get("targetPlanFlightStatus")
    if (
        type(flight_status) is not int
        or flight_status not in VERIFIED_TARGET_PLAN_FLIGHT_STATUSES
    ):
        return (
            "INVALID_TARGET_PLAN_FLIGHT_STATUS",
            "定向合作卡片服务端状态不是已送达状态。",
        )
    if not (
        result.get("creatorTabsClosed") is True
        and result.get("searchTabKept") is True
        and result.get("returnedToFindCreators") is True
    ):
        return (
            "CREATOR_TABS_NOT_CLEANED",
            "达人联系成功后未确认关闭详情/聊天标签并返回查找达人页。",
        )
    return None


def _tool_name(raw_name: str) -> str:
    for prefix in ("ziniao-contact_", "ziniao_contact_"):
        if raw_name.startswith(prefix):
            return raw_name[len(prefix):]
    return raw_name


def _json_safe(value: object) -> object:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _response_payload(raw_output: object) -> dict[str, Any]:
    if isinstance(raw_output, dict):
        parsed = raw_output
    else:
        try:
            parsed = json.loads(str(raw_output or "{}"))
        except json.JSONDecodeError:
            return {}
    structured = parsed.get("structuredContent")
    return structured if isinstance(structured, dict) else parsed


def _evidence(data: object) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    nested = data.get("evidence")
    if isinstance(nested, dict):
        return {**data, **nested}
    return data


def _step_output_summary(data: object) -> dict[str, Any]:
    evidence = _evidence(data)
    allowed = {
        "action",
        "step",
        "currentUrl",
        "title",
        "creatorHandle",
        "creatorId",
        "recipientVerified",
        "messageSent",
        "messageBubbleVisible",
        "alreadySent",
        "invitationName",
        "invitationId",
        "invitationGroupId",
        "invitationCreated",
        "invitationSent",
        "cardSent",
        "finalSendVerified",
        "targetPlanMessageVerified",
        "planCardServerIds",
        "targetPlanFlightStatus",
        "rightPanelInvitationVisible",
        "verifiedAfterRefresh",
        "reusedExistingWindow",
        "urlChangedAfterLaunch",
        "creatorTabsClosed",
        "closedCreatorTabCount",
        "searchTabKept",
        "returnedToFindCreators",
        "findCreatorsUrl",
        "skipCreator",
        "skipReason",
        "invitationSyncPending",
        "invitationSubmitAcknowledged",
        "refreshAttempts",
        "randomWaitSeconds",
        "disconnected",
    }
    return {
        key: _json_safe(value)
        for key, value in evidence.items()
        if key in allowed
    }


def _safe_input_summary(tool_input: object) -> dict[str, Any]:
    if not isinstance(tool_input, dict):
        return {}
    summary: dict[str, Any] = {}
    for key, value in tool_input.items():
        if key in {"taskId", "stepId"}:
            continue
        if key in {"greetingMessage", "message"}:
            text = str(value or "")
            summary[f"{key}Length"] = len(text)
            continue
        summary[key] = _json_safe(value)
    return summary


class SubprocessContactExecutor:
    """Run the validated Ziniao agent CLI and normalize its JSONL output."""

    def __init__(
        self,
        *,
        timeout_seconds: int | None = None,
        python_executable: str | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds or settings.TASK_TIMEOUT_SECONDS
        self.python_executable = python_executable or sys.executable

    def __call__(
        self,
        task: CreatorContactTask,
        target: CreatorContactTarget,
    ) -> dict[str, Any]:
        if not (
            task.confirm_send_greeting
            and task.confirm_send_invitation
            and task.confirm_send_card
        ):
            raise ContactExecutionError(
                "任务缺少招呼语、邀请或邀请卡片发送授权。"
            )
        required_environment = (
            "DEEPSEEK_API_KEY",
            "ZINIAO_COMPANY",
            "ZINIAO_USERNAME",
            "ZINIAO_PASSWORD",
        )
        missing_environment = [
            name for name in required_environment if not os.getenv(name)
        ]
        if missing_environment:
            raise ContactExecutionError(
                "联系达人 Worker 缺少必要环境变量："
                + "、".join(missing_environment)
                + "。为避免后台进程进入交互式凭据提示，任务已停止。"
            )

        command = [
            self.python_executable,
            "-m",
            "ziniao_automation.agent_runner",
            "--task-id",
            f"{task.pk}:{target.pk}",
            "--store-id",
            task.store_id,
            "--creator",
            f"@{target.normalized_handle}",
            "--invitation-name",
            task.invitation_name_snapshot,
            "--invitation-group-id",
            task.invitation_id_snapshot,
            "--greeting-message",
            task.greeting_snapshot,
            "--through-step",
            "12",
            "--confirm-send-greeting",
            "--confirm-send-invitation",
            "--confirm-send-card",
        ]
        environment = os.environ.copy()
        package_src = Path(settings.PROJECT_ROOT) / "ziniao-automation" / "src"
        prior_pythonpath = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = (
            str(package_src)
            if not prior_pythonpath
            else f"{package_src}{os.pathsep}{prior_pythonpath}"
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
            raise ContactExecutionError("联系达人子进程执行超时。") from error
        output = "\n".join(
            part for part in (completed.stdout, completed.stderr) if part
        )
        normalized = self._parse_output(output)
        if completed.returncode != 0:
            message = normalized.get("errorMessage") or output[-2000:]
            normalized.update(
                {
                    "success": False,
                    "errorCode": normalized.get("errorCode")
                    or "CONTACT_SUBPROCESS_FAILED",
                    "errorMessage": _redact(message)
                    or f"联系达人子进程异常退出（{completed.returncode}）。",
                }
            )
        return normalized

    @staticmethod
    def _parse_output(output: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "success": False,
            "creatorId": "",
            "messageSent": False,
            "invitationCreated": False,
            "invitationSent": False,
            "invitationId": "",
            "invitationGroupId": "",
            "cardSent": False,
            "finalSendVerified": False,
            "targetPlanMessageVerified": False,
            "planCardServerIds": [],
            "targetPlanFlightStatus": None,
            "creatorTabsClosed": False,
            "closedCreatorTabCount": 0,
            "searchTabKept": False,
            "returnedToFindCreators": False,
            "findCreatorsUrl": "",
            "skipCreator": False,
            "skipReason": "",
            "invitationSyncPending": False,
            "refreshAttempts": 0,
            "randomWaitSeconds": [],
            "sessionID": "",
            "errorCode": "",
            "errorMessage": "",
            "steps": [],
        }
        final_text_parts: list[str] = []
        for raw_line in output.splitlines():
            try:
                event = json.loads(raw_line.strip())
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(event, dict):
                continue
            session_id = event.get("sessionID")
            if session_id and not result["sessionID"]:
                result["sessionID"] = str(session_id)
            event_type = event.get("type")
            if event_type == "text":
                final_text_parts.append(
                    str((event.get("part") or {}).get("text") or "")
                )
                continue
            if event_type == "error":
                error = event.get("error") or {}
                data = error.get("data", {}) if isinstance(error, dict) else {}
                result["errorCode"] = "AGENT_EVENT_ERROR"
                result["errorMessage"] = _redact(
                    data.get("message") if isinstance(data, dict) else error
                )
                continue
            if event_type != "tool_use":
                continue
            part = event.get("part") or {}
            state = part.get("state") or {}
            if state.get("status") != "completed":
                continue
            tool_input = state.get("input") or {}
            response = _response_payload(state.get("output"))
            error = response.get("error") or {}
            success = response.get("success") is True
            data = response.get("data") or {}
            evidence = _evidence(data)
            operation = _tool_name(str(part.get("tool") or ""))
            result["steps"].append(
                {
                    "stepId": str(
                        tool_input.get("stepId")
                        or f"step-{len(result['steps']) + 1}"
                    ),
                    "operation": operation,
                    "label": TOOL_LABELS.get(operation, operation),
                    "status": "SUCCESS" if success else "FAILED",
                    "inputSummary": _safe_input_summary(tool_input),
                    "outputSummary": _step_output_summary(data),
                    "errorCode": str(error.get("code") or ""),
                    "errorMessage": _redact(error.get("userMessage")),
                }
            )
            if success:
                for source_key, destination_key in (
                    ("creatorId", "creatorId"),
                    ("messageSent", "messageSent"),
                    ("invitationCreated", "invitationCreated"),
                    ("invitationSent", "invitationSent"),
                    ("invitationId", "invitationId"),
                    ("invitationGroupId", "invitationGroupId"),
                    ("skipCreator", "skipCreator"),
                    ("skipReason", "skipReason"),
                    (
                        "invitationSyncPending",
                        "invitationSyncPending",
                    ),
                    ("refreshAttempts", "refreshAttempts"),
                    ("randomWaitSeconds", "randomWaitSeconds"),
                    ("creatorTabsClosed", "creatorTabsClosed"),
                    (
                        "closedCreatorTabCount",
                        "closedCreatorTabCount",
                    ),
                    ("searchTabKept", "searchTabKept"),
                    (
                        "returnedToFindCreators",
                        "returnedToFindCreators",
                    ),
                    ("findCreatorsUrl", "findCreatorsUrl"),
                ):
                    if source_key in evidence:
                        result[destination_key] = evidence[source_key]
                if operation == "ziniao_send_collaboration_card":
                    for source_key in (
                        "cardSent",
                        "finalSendVerified",
                        "targetPlanMessageVerified",
                        "planCardServerIds",
                        "targetPlanFlightStatus",
                        "creatorTabsClosed",
                        "searchTabKept",
                        "returnedToFindCreators",
                    ):
                        if source_key in evidence:
                            result[source_key] = evidence[source_key]
            if not success:
                result["errorCode"] = str(
                    error.get("code") or "CONTACT_STEP_FAILED"
                )
                result["errorMessage"] = _redact(
                    error.get("userMessage") or "联系达人步骤执行失败。"
                )

        final_text = "".join(final_text_parts).strip()
        if final_text:
            try:
                final_payload = json.loads(final_text)
            except json.JSONDecodeError:
                final_payload = {}
            if isinstance(final_payload, dict):
                for key in (
                    "success",
                    "errorCode",
                    "errorMessage",
                    "skipCreator",
                    "skipReason",
                ):
                    if key in final_payload:
                        result[key] = final_payload[key]
        return result


@transaction.atomic
def record_successful_contact(
    target: CreatorContactTarget,
) -> ContactedCreator:
    """Write store-global dedupe history only after the terminal send proof."""
    locked = (
        CreatorContactTarget.objects
        .select_for_update()
        .select_related("task", "related_creator")
        .get(pk=target.pk)
    )
    stored_result = (
        locked.result if isinstance(locked.result, dict) else {}
    )
    evidence_error = _terminal_evidence_error(
        locked.task,
        stored_result,
        actual_invitation_id=locked.actual_invitation_id,
        invitation_group_id=locked.invitation_group_id,
    )
    if not (
        stored_result.get("success") is True
        and locked.message_sent
        and locked.card_sent
        and locked.target_plan_message_verified
        and locked.final_send_verified
        and evidence_error is None
    ):
        raise ValidationError(
            "尚未验收邀请卡片最终发送，不能标记达人已联系。"
        )
    task = locked.task
    record, _created = ContactedCreator.objects.update_or_create(
        store_id=task.store_id,
        normalized_handle=locked.normalized_handle,
        defaults={
            "creator_handle": locked.creator_handle_snapshot,
            "chat_creator_id": locked.chat_creator_id,
            "related_creator": locked.related_creator,
            "contact_task": task,
            "greeting_sha256": task.greeting_sha256,
            "invitation_id": locked.actual_invitation_id,
            "invitation_name": task.invitation_name_snapshot,
            "evidence": {
                "targetId": locked.pk,
                "messageSent": True,
                "invitationCreated": locked.invitation_created,
                "invitationId": locked.actual_invitation_id,
                "invitationGroupId": locked.invitation_group_id,
                "cardSent": True,
                "targetPlanMessageVerified": True,
                "planCardServerIds": locked.result.get(
                    "planCardServerIds",
                    [],
                ),
                "targetPlanFlightStatus": locked.result.get(
                    "targetPlanFlightStatus"
                ),
                "finalSendVerified": True,
                "creatorTabsClosed": True,
                "searchTabKept": True,
                "returnedToFindCreators": True,
            },
        },
    )
    return record


class CreatorContactRunner:
    """Run one contact task sequentially, one frozen creator at a time."""

    def __init__(
        self,
        task: CreatorContactTask,
        *,
        executor: ContactExecutor | None = None,
    ) -> None:
        self.task = task
        self.executor = executor or SubprocessContactExecutor()

    def run(self) -> CreatorContactTask:
        self.task.refresh_from_db()
        if self.task.status == CreatorContactTask.Status.CANCELLED:
            return self.task
        self.task.status = CreatorContactTask.Status.RUNNING
        self.task.started_at = self.task.started_at or timezone.now()
        self.task.finished_at = None
        self.task.error_code = ""
        self.task.error_message = ""
        self.task.current_step = "开始联系达人"
        self.task.save()

        targets = list(self.task.targets.order_by("rank"))
        if not targets:
            return self._fail_task(
                "NO_CONTACT_TARGETS",
                "任务没有冻结的达人目标。",
            )

        for index, target in enumerate(targets, start=1):
            if target.status in {
                CreatorContactTarget.Status.SUCCESS,
                CreatorContactTarget.Status.SKIPPED,
            }:
                self._update_progress(index, len(targets), target)
                continue
            if ContactedCreator.objects.filter(
                store_id=self.task.store_id,
                normalized_handle=target.normalized_handle,
            ).exists():
                target.status = CreatorContactTarget.Status.SKIPPED
                target.current_step = "已由其他任务成功联系"
                target.finished_at = timezone.now()
                target.save()
                self._update_progress(index, len(targets), target)
                continue

            target.status = CreatorContactTarget.Status.RUNNING
            target.started_at = timezone.now()
            target.finished_at = None
            target.error_code = ""
            target.error_message = ""
            target.current_step = "启动紫鸟联系流程"
            target.save()
            self.task.current_step = (
                f"联系第 {target.rank} 位达人 "
                f"@{target.normalized_handle}"
            )
            self.task.save(update_fields=["current_step", "updated_at"])

            try:
                result = self.executor(self.task, target)
            except Exception as error:
                result = {
                    "success": False,
                    "errorCode": type(error).__name__,
                    "errorMessage": _redact(error),
                    "steps": [],
                }
            self._persist_steps(target, result.get("steps") or [])
            self._persist_target_result(target, result)
            self._update_progress(index, len(targets), target)

        return self._finish_task()

    def _persist_steps(
        self,
        target: CreatorContactTarget,
        steps: object,
    ) -> None:
        if not isinstance(steps, list):
            return
        for step in steps:
            if not isinstance(step, dict):
                continue
            raw_step_id = str(
                step.get("stepId") or f"step-{target.steps.count() + 1}"
            )
            step_id = f"target-{target.pk}-{raw_step_id}"
            existing = CreatorContactTaskStep.objects.filter(
                task=self.task,
                step_id=step_id,
            ).first()
            if existing is None:
                maximum = (
                    self.task.steps.aggregate(value=Max("sequence"))["value"]
                    or 0
                )
                sequence = maximum + 1
            else:
                sequence = existing.sequence
            status = str(step.get("status") or "").upper()
            if status not in CreatorContactTaskStep.Status.values:
                status = CreatorContactTaskStep.Status.FAILED
            CreatorContactTaskStep.objects.update_or_create(
                task=self.task,
                step_id=step_id,
                defaults={
                    "target": target,
                    "sequence": sequence,
                    "operation": str(step.get("operation") or raw_step_id)[:160],
                    "label": str(
                        step.get("label")
                        or step.get("operation")
                        or raw_step_id
                    )[:200],
                    "status": status,
                    "input_summary": _json_safe(
                        step.get("inputSummary") or {}
                    ),
                    "output_summary": _json_safe(
                        step.get("outputSummary") or {}
                    ),
                    "error_code": str(step.get("errorCode") or "")[:100],
                    "error_message": _redact(step.get("errorMessage")),
                    "started_at": timezone.now(),
                    "finished_at": timezone.now(),
                },
            )

    def _persist_target_result(
        self,
        target: CreatorContactTarget,
        result: dict[str, Any],
    ) -> None:
        with transaction.atomic():
            locked = CreatorContactTarget.objects.select_for_update().get(
                pk=target.pk
            )
            locked.chat_creator_id = str(result.get("creatorId") or "")[:40]
            locked.actual_invitation_id = str(
                result.get("invitationId") or ""
            )[:128]
            locked.invitation_group_id = str(
                result.get("invitationGroupId") or ""
            )[:128]
            locked.message_sent = result.get("messageSent") is True
            locked.invitation_created = (
                result.get("invitationCreated") is True
                or result.get("invitationSent") is True
            )
            locked.card_sent = result.get("cardSent") is True
            locked.target_plan_message_verified = (
                result.get("targetPlanMessageVerified") is True
            )
            locked.final_send_verified = (
                result.get("finalSendVerified") is True
            )
            locked.opencode_session_id = str(
                result.get("sessionID") or ""
            )[:120]
            if locked.opencode_session_id and not self.task.opencode_session_id:
                self.task.opencode_session_id = locked.opencode_session_id
                self.task.save(
                    update_fields=["opencode_session_id", "updated_at"]
                )
            locked.result = _json_safe(
                {
                    key: value
                    for key, value in result.items()
                    if key not in {"steps", "greetingMessage"}
                }
            )
            skip_requested = result.get("skipCreator") is True
            evidence_error = _terminal_evidence_error(
                self.task,
                result,
                actual_invitation_id=locked.actual_invitation_id,
                invitation_group_id=locked.invitation_group_id,
            )
            terminal_success = (
                result.get("success") is True
                and locked.message_sent
                and locked.card_sent
                and locked.target_plan_message_verified
                and locked.final_send_verified
                and evidence_error is None
            )
            if terminal_success:
                locked.status = CreatorContactTarget.Status.SUCCESS
                locked.current_step = "邀请卡片发送成功"
                locked.error_code = ""
                locked.error_message = ""
            elif skip_requested:
                locked.status = CreatorContactTarget.Status.SKIPPED
                locked.current_step = "邀请已提示成功但卡片未同步，已跳过"
                locked.error_code = "INVITATION_SYNC_NOT_VISIBLE"
                locked.error_message = _redact(
                    result.get("skipReason")
                    or (
                        "页面提示邀请添加成功，但连续三次刷新后仍未显示"
                        "定向合作卡片；已按任务规则跳过。"
                    )
                )
            else:
                locked.status = CreatorContactTarget.Status.FAILED
                locked.current_step = "联系达人失败"
                locked.error_code = str(
                    result.get("errorCode")
                    or (evidence_error or ("", ""))[0]
                    or "FINAL_SEND_NOT_VERIFIED"
                )[:100]
                locked.error_message = _redact(
                    result.get("errorMessage")
                    or (evidence_error or ("", ""))[1]
                    or "未取得邀请卡片最终发送成功证据。"
                )
            locked.finished_at = timezone.now()
            locked.save()
            if terminal_success:
                record_successful_contact(locked)
        target.refresh_from_db()

    def _update_progress(
        self,
        processed: int,
        total: int,
        target: CreatorContactTarget,
    ) -> None:
        self.task.progress = min(99, int(processed / max(total, 1) * 100))
        self.task.current_step = target.current_step
        self.task.save(
            update_fields=["progress", "current_step", "updated_at"]
        )

    def _finish_task(self) -> CreatorContactTask:
        counts = {
            status: self.task.targets.filter(status=status).count()
            for status in CreatorContactTarget.Status.values
        }
        failed = counts[CreatorContactTarget.Status.FAILED]
        succeeded = counts[CreatorContactTarget.Status.SUCCESS]
        skipped = counts[CreatorContactTarget.Status.SKIPPED]
        if failed == 0:
            status = CreatorContactTask.Status.SUCCESS
        elif succeeded or skipped:
            status = CreatorContactTask.Status.PARTIAL_SUCCESS
        else:
            status = CreatorContactTask.Status.FAILED

        self.task.status = status
        self.task.progress = 100
        self.task.current_step = "联系达人任务已结束"
        self.task.finished_at = timezone.now()
        self.task.final_summary = {
            "targetCount": self.task.targets.count(),
            "successCount": succeeded,
            "failedCount": failed,
            "skippedCount": skipped,
        }
        first_failure = self.task.targets.filter(
            status=CreatorContactTarget.Status.FAILED
        ).order_by("rank").first()
        if first_failure is not None:
            self.task.error_code = first_failure.error_code
            self.task.error_message = first_failure.error_message
        else:
            self.task.error_code = ""
            self.task.error_message = ""
        self.task.save()
        return self.task

    def _fail_task(
        self,
        code: str,
        message: str,
    ) -> CreatorContactTask:
        self.task.status = CreatorContactTask.Status.FAILED
        self.task.error_code = code
        self.task.error_message = _redact(message)
        self.task.current_step = "联系达人任务失败"
        self.task.finished_at = timezone.now()
        self.task.save()
        return self.task
