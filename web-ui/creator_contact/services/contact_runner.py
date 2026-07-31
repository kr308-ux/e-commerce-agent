"""Execute frozen creator-contact targets and persist verified outcomes."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Protocol

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from creator_contact.models import (
    ContactedCreator,
    CreatorContactTarget,
    CreatorContactTask,
    CreatorContactTaskStep,
)
from creator_contact.services.subprocess_control import (
    TaskCancellationRequested,
    run_task_subprocess,
    task_cancellation_requested,
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


class CardExecutor(Protocol):
    def __call__(
        self,
        task: CreatorContactTask,
        target: CreatorContactTarget,
    ) -> dict[str, Any]: ...


class BatchCardExecutor(CardExecutor, Protocol):
    def run_many(
        self,
        task: CreatorContactTask,
        targets: list[CreatorContactTarget],
    ) -> dict[int, dict[str, Any]]: ...


class MembershipExecutor(Protocol):
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


def _exact_creator_unavailable(result: dict[str, Any]) -> bool:
    if result.get("messageSent") is True:
        return False
    message = str(result.get("errorMessage") or "")
    return any(
        marker in message
        for marker in (
            "候选项不再是精确目标",
            "未出现匹配候选项",
            "未出现精确同名候选项",
            "未出现目标达人搜索结果卡片",
            "未找到目标达人搜索结果卡片",
            "聊天页顶部未显示目标达人用户名",
        )
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
    accepted_list_context_verified = (
        result.get("deliverySource") == "accepted_creator_list"
        and result.get("acceptedCreatorsPageVisible") is True
        and result.get("projectMembershipVerified") is True
        and result.get("recipientVerified") is True
    )
    if not accepted_list_context_verified:
        return (
            "CARD_DELIVERY_CONTEXT_NOT_VERIFIED",
            "未确认目标达人存在于精确项目列表并已打开其聊天。",
        )
    if not (
        result.get("cardSent") is True
        and result.get("targetPlanMessageVerified") is True
        and result.get("finalSendVerified") is True
    ):
        return (
            "CARD_DELIVERY_NOT_VERIFIED",
            "合作卡片未取得已发送和 React 消息终态证据。",
        )
    return None


def _invitation_evidence_error(
    task: CreatorContactTask,
    result: dict[str, Any],
    *,
    invitation_group_id: str,
) -> tuple[str, str] | None:
    if (
        not task.invitation_id_snapshot
        or invitation_group_id != task.invitation_id_snapshot
        or result.get("invitationGroupId") != task.invitation_id_snapshot
    ):
        return (
            "INVITATION_GROUP_MISMATCH",
            "邀请完成结果的 invitationGroupId 与任务快照不一致。",
        )
    completion_verified = (
        result.get("invitationCompleted") is True
        and (
            result.get("invitationButtonClicked") is True
            or result.get("alreadySent") is True
        )
    )
    cleanup_verified = (
        result.get("creatorDetailTargetGone") is True
        and result.get("creatorTabsClosed") is True
        and result.get("searchTabKept") is True
        and result.get("returnedToFindCreators") is True
        and result.get("findCreatorsSearchReady") is True
    )
    if not (completion_verified and cleanup_verified):
        return (
            "INVITATION_COMPLETION_NOT_VERIFIED",
            "未取得邀请按钮点击/幂等完成及可复用搜索页证据。",
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
    if not isinstance(parsed, dict):
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


def _final_json_payload(text: str) -> dict[str, Any]:
    """Accept plain, fenced, or prose-prefixed JSON and return the last object."""
    normalized = str(text or "").strip()
    if not normalized:
        return {}
    candidates = [normalized]
    candidates.extend(
        match.strip()
        for match in re.findall(
            r"```(?:json)?\s*([\s\S]*?)```",
            normalized,
            flags=re.IGNORECASE,
        )
    )
    decoded: list[dict[str, Any]] = []
    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            decoded.append(payload)
        for index, character in enumerate(candidate):
            if character != "{":
                continue
            try:
                nested, _end = decoder.raw_decode(candidate[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(nested, dict):
                decoded.append(nested)
    return decoded[-1] if decoded else {}


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
        "invitationCompleted",
        "invitationButtonClicked",
        "invitationSubmissionAttempted",
        "invitationSubmissionConfirmed",
        "invitationCompletionSource",
        "invitationConfirmationSource",
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
        "creatorDetailTabClosed",
        "creatorDetailReturnedToSearch",
        "creatorDetailTargetGone",
        "creatorChatTabClosed",
        "searchTabKept",
        "returnedToFindCreators",
        "findCreatorsUrl",
        "findCreatorsSearchReady",
        "connectionMode",
        "browserSessionCachePersisted",
        "debuggingPort",
        "reusedExistingFindCreatorsTab",
        "pageNavigationSkipped",
        "pageRefreshSkipped",
        "existingPageReloadedForRecovery",
        "projectPageReused",
        "projectOpenedOnce",
        "projectOpenCount",
        "skipCreator",
        "skipReason",
        "invitationSyncPending",
        "refreshAttempts",
        "randomWaitSeconds",
        "actionWaitSeconds",
        "refreshWaitSeconds",
        "acceptedCreatorCount",
        "acceptedCreatorsPageVisible",
        "creatorDetailsExpanded",
        "projectMembershipVerified",
        "invitationVerificationSource",
        "chatDrawerVisible",
        "deliverySource",
        "disconnected",
        "storeBrowserPreserved",
        "webdriverEndpointPreserved",
        "cdpClickRecoveryCount",
        "importedCreatorId",
        "findCreatorsObstructionPresent",
        "findCreatorsObstructionClosed",
        "findCreatorsObstructionSelector",
        "domFallbackUsed",
        "domFallbackEvents",
        "stateMachineStages",
        "modelFallbackStageUsed",
        "adaptiveLocatorUsed",
        "adaptiveLocatorEvents",
        "targetCollaborationRefreshAttempted",
        "targetCollaborationRefreshWaitSeconds",
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
    """Run the deterministic Ziniao state machine and normalize its JSONL."""

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

    def __call__(
        self,
        task: CreatorContactTask,
        target: CreatorContactTarget,
    ) -> dict[str, Any]:
        if not (
            task.confirm_send_greeting
            and task.confirm_send_invitation
        ):
            raise ContactExecutionError(
                "任务缺少招呼语或定向合作邀请发送授权。"
            )
        required_environment = (
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
            "ziniao_automation.contact_task_runner",
            "--task-id",
            f"{task.pk}:{target.pk}",
            "--store-id",
            task.store_id,
            "--creator",
            target.imported_creator_id,
            "--invitation-name",
            task.invitation_name_snapshot,
            "--invitation-group-id",
            task.invitation_id_snapshot,
            "--greeting-message",
            task.greeting_snapshot,
            "--through-step",
            "11",
            "--confirm-send-greeting",
            "--confirm-send-invitation",
            "--model",
            task.model_name,
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
            completed = run_task_subprocess(
                task.pk,
                command,
                cwd=settings.PROJECT_ROOT,
                env=environment,
                timeout=self.timeout_seconds,
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
        if _exact_creator_unavailable(normalized):
            normalized.update(
                {
                    "skipCreator": True,
                    "skipReason": (
                        "联盟中心未提供任务快照中的精确达人账号；"
                        "已阻止相似账号并跳过该目标。"
                    ),
                    "errorCode": "CREATOR_EXACT_MATCH_UNAVAILABLE",
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
            "invitationCompleted": False,
            "invitationButtonClicked": False,
            "invitationSubmissionAttempted": False,
            "invitationSubmissionConfirmed": False,
            "invitationCompletionSource": "",
            "invitationConfirmationSource": "",
            "alreadySent": False,
            "invitationSent": False,
            "invitationGroupId": "",
            "creatorTabsClosed": False,
            "closedCreatorTabCount": 0,
            "creatorDetailTabClosed": False,
            "creatorDetailReturnedToSearch": False,
            "creatorDetailTargetGone": False,
            "creatorChatTabClosed": False,
            "searchTabKept": False,
            "returnedToFindCreators": False,
            "findCreatorsUrl": "",
            "findCreatorsSearchReady": False,
            "skipCreator": False,
            "skipReason": "",
            "reviewRequired": False,
            "taskFatal": False,
            "failureStage": "",
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
            tool_input = state.get("input") or {}
            operation = _tool_name(str(part.get("tool") or ""))
            if operation not in TOOL_LABELS:
                continue
            response_source = (
                state.get("output")
                if state.get("status") == "completed"
                else state.get("error")
            )
            response = _response_payload(response_source)
            error = response.get("error") or {}
            success = response.get("success") is True
            data = response.get("data") or {}
            evidence = _evidence(data)
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
                    "domFallback": _json_safe(
                        {
                            **(
                                error.get("domFallback")
                                if isinstance(
                                    error.get("domFallback"),
                                    dict,
                                )
                                else {}
                            ),
                            **(
                                {
                                    "events": error.get(
                                        "domFallbackEvents"
                                    )
                                }
                                if error.get("domFallbackEvents")
                                else {}
                            ),
                        }
                    ),
                    "adaptiveLocator": _json_safe(
                        {
                            "order": error.get(
                                "adaptiveLocatorOrder"
                            )
                            or [],
                            "events": error.get(
                                "adaptiveLocatorEvents"
                            )
                            or [],
                            "diagnosis": error.get(
                                "modelDiagnosis"
                            )
                            or {},
                            "stateMachineStages": error.get(
                                "stateMachineStages"
                            )
                            or [],
                        }
                    ),
                }
            )
            if success:
                for source_key, destination_key in (
                    ("creatorId", "creatorId"),
                    ("messageSent", "messageSent"),
                    ("invitationCreated", "invitationCreated"),
                    ("invitationCompleted", "invitationCompleted"),
                    ("alreadySent", "alreadySent"),
                    (
                        "invitationButtonClicked",
                        "invitationButtonClicked",
                    ),
                    (
                        "invitationSubmissionAttempted",
                        "invitationSubmissionAttempted",
                    ),
                    (
                        "invitationSubmissionConfirmed",
                        "invitationSubmissionConfirmed",
                    ),
                    (
                        "invitationCompletionSource",
                        "invitationCompletionSource",
                    ),
                    (
                        "invitationConfirmationSource",
                        "invitationConfirmationSource",
                    ),
                    ("invitationSent", "invitationSent"),
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
                    (
                        "creatorDetailTabClosed",
                        "creatorDetailTabClosed",
                    ),
                    (
                        "creatorDetailReturnedToSearch",
                        "creatorDetailReturnedToSearch",
                    ),
                    (
                        "creatorDetailTargetGone",
                        "creatorDetailTargetGone",
                    ),
                    (
                        "creatorChatTabClosed",
                        "creatorChatTabClosed",
                    ),
                    ("searchTabKept", "searchTabKept"),
                    (
                        "returnedToFindCreators",
                        "returnedToFindCreators",
                    ),
                    ("findCreatorsUrl", "findCreatorsUrl"),
                    (
                        "findCreatorsSearchReady",
                        "findCreatorsSearchReady",
                    ),
                ):
                    if source_key in evidence:
                        result[destination_key] = evidence[source_key]
            if not success:
                result["errorCode"] = str(
                    error.get("code") or "CONTACT_STEP_FAILED"
                )
                result["errorMessage"] = _redact(
                    error.get("userMessage") or "联系达人步骤执行失败。"
                )

        final_text = "".join(final_text_parts).strip()
        if final_text:
            final_payload = _final_json_payload(final_text)
            for key in (
                "success",
                "errorCode",
                "errorMessage",
                "skipCreator",
                "skipReason",
                "reviewRequired",
                "taskFatal",
                "failureStage",
                "invitationCompleted",
                "invitationButtonClicked",
                "invitationSubmissionConfirmed",
                "creatorTabsClosed",
                "searchTabKept",
                "returnedToFindCreators",
                "findCreatorsSearchReady",
            ):
                if key in final_payload:
                    result[key] = final_payload[key]

        invitation_tool_succeeded = any(
            step.get("operation")
            == "ziniao_send_selected_invitation"
            and step.get("status") == "SUCCESS"
            for step in result["steps"]
        )
        structured_completion_verified = (
            invitation_tool_succeeded
            and result.get("invitationCompleted") is True
            and (
                result.get("invitationButtonClicked") is True
                or result.get("alreadySent") is True
            )
            and result.get("creatorDetailTargetGone") is True
            and result.get("creatorTabsClosed") is True
            and result.get("searchTabKept") is True
            and result.get("returnedToFindCreators") is True
            and result.get("findCreatorsSearchReady") is True
            and not any(
                step.get("status") == "FAILED"
                for step in result["steps"]
            )
        )
        if structured_completion_verified:
            result["success"] = True
            result["errorCode"] = ""
            result["errorMessage"] = ""
        return result


class SubprocessCardExecutor:
    """Send accepted creators' cards in one shared WebDriver project page."""

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

    def __call__(
        self,
        task: CreatorContactTask,
        target: CreatorContactTarget,
    ) -> dict[str, Any]:
        if not task.confirm_send_card:
            raise ContactExecutionError("任务缺少定向合作卡片发送授权。")
        required_environment = (
            "ZINIAO_COMPANY",
            "ZINIAO_USERNAME",
            "ZINIAO_PASSWORD",
        )
        missing = [name for name in required_environment if not os.getenv(name)]
        if missing:
            raise ContactExecutionError(
                "发送合作卡片 Worker 缺少必要环境变量："
                + "、".join(missing)
                + "。"
            )
        command = [
            self.python_executable,
            "-m",
            "ziniao_automation.accepted_card_runner",
            "--store-id",
            task.store_id,
            "--creator",
            f"@{target.normalized_handle}",
            "--invitation-name",
            task.invitation_name_snapshot,
            "--invitation-group-id",
            task.invitation_id_snapshot,
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
            completed = run_task_subprocess(
                task.pk,
                command,
                cwd=settings.PROJECT_ROOT,
                env=environment,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise ContactExecutionError(
                "发送定向合作卡片子进程执行超时。"
            ) from error

        payload: dict[str, Any] = {}
        for line in reversed(completed.stdout.splitlines()):
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                payload = candidate
                break
        if completed.returncode != 0:
            payload.update(
                {
                    "success": False,
                    "errorCode": payload.get("errorCode")
                    or "CARD_SUBPROCESS_FAILED",
                    "errorMessage": _redact(
                        payload.get("errorMessage")
                        or completed.stderr
                        or completed.stdout[-2000:]
                    ),
                }
            )
        return self._normalize_payload(payload)

    @staticmethod
    def _normalize_payload(
        payload: dict[str, Any],
        *,
        shared_steps: list[object] | None = None,
    ) -> dict[str, Any]:
        normalized = dict(payload)
        normalized["deliverySource"] = "accepted_creator_list"
        normalized_steps: list[dict[str, Any]] = []
        raw_steps = [
            *(shared_steps or []),
            *(normalized.get("steps") or []),
        ]
        for index, raw_step in enumerate(raw_steps, start=1):
            if not isinstance(raw_step, dict):
                continue
            evidence = raw_step.get("evidence")
            if not isinstance(evidence, dict):
                evidence = {}
            operation = str(raw_step.get("action") or f"card-step-{index}")
            normalized_steps.append(
                {
                    "stepId": f"accepted-card-{index}",
                    "operation": operation,
                    "label": {
                        "open_project_accepted_creators": (
                            "进入项目已接受达人列表"
                        ),
                        "verify_project_creator_membership": (
                            "核验项目精确达人"
                        ),
                        "open_accepted_creator_chat": "打开已接受达人聊天",
                        "send_accepted_creator_collaboration_card": (
                            "发送定向合作卡片"
                        ),
                    }.get(operation, operation),
                    "status": (
                        "SUCCESS"
                        if raw_step.get("success") is True
                        else "FAILED"
                    ),
                    "outputSummary": _step_output_summary(evidence),
                }
            )
        normalized["steps"] = normalized_steps
        return normalized

    def run_many(
        self,
        task: CreatorContactTask,
        targets: list[CreatorContactTarget],
    ) -> dict[int, dict[str, Any]]:
        """Open the project once and deliver every target from its detail page."""
        if not targets:
            return {}
        if not task.confirm_send_card:
            raise ContactExecutionError("任务缺少定向合作卡片发送授权。")
        required_environment = (
            "ZINIAO_COMPANY",
            "ZINIAO_USERNAME",
            "ZINIAO_PASSWORD",
        )
        missing = [name for name in required_environment if not os.getenv(name)]
        if missing:
            raise ContactExecutionError(
                "发送合作卡片 Worker 缺少必要环境变量："
                + "、".join(missing)
                + "。"
            )
        creators = [f"@{target.normalized_handle}" for target in targets]
        command = [
            self.python_executable,
            "-m",
            "ziniao_automation.accepted_card_runner",
            "--store-id",
            task.store_id,
            "--creators-json",
            json.dumps(creators, ensure_ascii=False),
            "--invitation-name",
            task.invitation_name_snapshot,
            "--invitation-group-id",
            task.invitation_id_snapshot,
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
            completed = run_task_subprocess(
                task.pk,
                command,
                cwd=settings.PROJECT_ROOT,
                env=environment,
                timeout=self.timeout_seconds * max(1, len(targets)),
            )
        except subprocess.TimeoutExpired as error:
            raise ContactExecutionError(
                "批量发送定向合作卡片子进程执行超时。"
            ) from error

        payload: dict[str, Any] = {}
        for line in reversed(completed.stdout.splitlines()):
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                payload = candidate
                break
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            message = _redact(
                payload.get("errorMessage")
                or completed.stderr
                or completed.stdout[-2000:]
                or "批量合作卡片子进程未返回逐达人结果。"
            )
            return {
                target.pk: self._normalize_payload(
                    {
                        "success": False,
                        "errorCode": payload.get("errorCode")
                        or "CARD_BATCH_SUBPROCESS_FAILED",
                        "errorMessage": message,
                        "steps": [],
                    }
                )
                for target in targets
            }

        by_handle: dict[str, dict[str, Any]] = {}
        for raw_result in raw_results:
            if not isinstance(raw_result, dict):
                continue
            normalized_handle = str(
                raw_result.get("creator") or ""
            ).strip().lstrip("@").casefold()
            if normalized_handle:
                by_handle[normalized_handle] = raw_result
        shared_steps = (
            payload.get("steps")
            if isinstance(payload.get("steps"), list)
            else []
        )
        results: dict[int, dict[str, Any]] = {}
        for index, target in enumerate(targets):
            raw_result = by_handle.get(target.normalized_handle)
            if raw_result is None:
                raw_result = {
                    "success": False,
                    "errorCode": "CARD_BATCH_RESULT_MISSING",
                    "errorMessage": (
                        f"批量结果缺少达人 @{target.normalized_handle}。"
                    ),
                    "steps": [],
                }
            raw_result = {
                **raw_result,
                "projectOpenedOnce": payload.get("projectOpenedOnce") is True,
                "projectOpenCount": payload.get("projectOpenCount"),
            }
            results[target.pk] = self._normalize_payload(
                raw_result,
                shared_steps=shared_steps if index == 0 else [],
            )
        return results


class SubprocessMembershipExecutor:
    """Reconcile creators in one shared exact-project detail page."""

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

    def __call__(
        self,
        task: CreatorContactTask,
        target: CreatorContactTarget,
    ) -> dict[str, Any]:
        required_environment = (
            "ZINIAO_COMPANY",
            "ZINIAO_USERNAME",
            "ZINIAO_PASSWORD",
        )
        missing = [name for name in required_environment if not os.getenv(name)]
        if missing:
            raise ContactExecutionError(
                "核验项目达人 Worker 缺少必要环境变量："
                + "、".join(missing)
                + "。"
            )
        command = [
            self.python_executable,
            "-m",
            "ziniao_automation.accepted_card_runner",
            "--store-id",
            task.store_id,
            "--creator",
            f"@{target.normalized_handle}",
            "--invitation-name",
            task.invitation_name_snapshot,
            "--invitation-group-id",
            task.invitation_id_snapshot,
            "--verify-only",
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
            completed = run_task_subprocess(
                task.pk,
                command,
                cwd=settings.PROJECT_ROOT,
                env=environment,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise ContactExecutionError(
                "核验项目达人子进程执行超时。"
            ) from error
        payload: dict[str, Any] = {}
        for line in reversed(completed.stdout.splitlines()):
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                payload = candidate
                break
        if completed.returncode != 0:
            payload.update(
                {
                    "success": False,
                    "errorCode": payload.get("errorCode")
                    or "MEMBERSHIP_SUBPROCESS_FAILED",
                    "errorMessage": _redact(
                        payload.get("errorMessage")
                        or completed.stderr
                        or completed.stdout[-2000:]
                    ),
                }
            )
        return self._normalize_payload(payload)

    @staticmethod
    def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        normalized["deliverySource"] = (
            "project_creator_details_reconciliation"
        )
        normalized_steps: list[dict[str, Any]] = []
        for index, raw_step in enumerate(
            normalized.get("steps") or [],
            start=1,
        ):
            if not isinstance(raw_step, dict):
                continue
            evidence = raw_step.get("evidence")
            if not isinstance(evidence, dict):
                evidence = {}
            operation = str(raw_step.get("action") or f"verify-step-{index}")
            normalized_steps.append(
                {
                    "stepId": f"membership-{index}",
                    "operation": operation,
                    "label": {
                        "open_project_accepted_creators": (
                            "进入项目达人详情列表"
                        ),
                        "verify_project_creator_membership": (
                            "核验达人已加入项目"
                        ),
                    }.get(operation, operation),
                    "status": (
                        "SUCCESS"
                        if raw_step.get("success") is True
                        else "FAILED"
                    ),
                    "outputSummary": _step_output_summary(evidence),
                }
            )
        normalized["steps"] = normalized_steps
        return normalized

    def run_many(
        self,
        task: CreatorContactTask,
        targets: list[CreatorContactTarget],
    ) -> dict[int, dict[str, Any]]:
        if not targets:
            return {}
        required_environment = (
            "ZINIAO_COMPANY",
            "ZINIAO_USERNAME",
            "ZINIAO_PASSWORD",
        )
        missing = [name for name in required_environment if not os.getenv(name)]
        if missing:
            raise ContactExecutionError(
                "核验项目达人 Worker 缺少必要环境变量："
                + "、".join(missing)
                + "。"
            )
        creators = [f"@{target.normalized_handle}" for target in targets]
        command = [
            self.python_executable,
            "-m",
            "ziniao_automation.accepted_card_runner",
            "--store-id",
            task.store_id,
            "--creators-json",
            json.dumps(creators, ensure_ascii=False),
            "--invitation-name",
            task.invitation_name_snapshot,
            "--invitation-group-id",
            task.invitation_id_snapshot,
            "--verify-only",
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
            completed = run_task_subprocess(
                task.pk,
                command,
                cwd=settings.PROJECT_ROOT,
                env=environment,
                timeout=self.timeout_seconds * max(1, len(targets)),
            )
        except subprocess.TimeoutExpired as error:
            raise ContactExecutionError(
                "批量核验项目达人子进程执行超时。"
            ) from error
        payload: dict[str, Any] = {}
        for line in reversed(completed.stdout.splitlines()):
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                payload = candidate
                break
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            message = _redact(
                payload.get("errorMessage")
                or completed.stderr
                or completed.stdout[-2000:]
                or "批量项目达人核验未返回逐达人结果。"
            )
            return {
                target.pk: self._normalize_payload(
                    {
                        "success": False,
                        "errorCode": payload.get("errorCode")
                        or "MEMBERSHIP_BATCH_SUBPROCESS_FAILED",
                        "errorMessage": message,
                        "steps": [],
                    }
                )
                for target in targets
            }
        by_handle = {
            str(result.get("creator") or "")
            .strip()
            .lstrip("@")
            .casefold(): result
            for result in raw_results
            if isinstance(result, dict) and result.get("creator")
        }
        results: dict[int, dict[str, Any]] = {}
        for target in targets:
            raw_result = by_handle.get(target.normalized_handle) or {
                "success": False,
                "errorCode": "MEMBERSHIP_BATCH_RESULT_MISSING",
                "errorMessage": (
                    f"批量核验结果缺少达人 @{target.normalized_handle}。"
                ),
                "steps": [],
            }
            raw_result = {
                **raw_result,
                "projectOpenedOnce": payload.get("projectOpenedOnce") is True,
                "projectOpenCount": payload.get("projectOpenCount"),
            }
            results[target.pk] = self._normalize_payload(raw_result)
        return results


@transaction.atomic
def record_invited_creator(
    target: CreatorContactTarget,
) -> ContactedCreator:
    """Register store-global contact history after the invitation click."""
    locked = (
        CreatorContactTarget.objects
        .select_for_update()
        .select_related("task", "creator")
        .get(pk=target.pk)
    )
    stored_result = (
        locked.result if isinstance(locked.result, dict) else {}
    )
    button_receipt = (
        stored_result.get("invitationButtonClicked") is True
        or stored_result.get("alreadySent") is True
    )
    group_matches = (
        bool(locked.task.invitation_id_snapshot)
        and locked.invitation_group_id
        == locked.task.invitation_id_snapshot
        and stored_result.get("invitationGroupId")
        == locked.task.invitation_id_snapshot
    )
    if not (button_receipt and group_matches):
        raise ValidationError(
            "尚未取得指定定向邀请按钮点击/幂等证据，"
            "不能标记达人已邀请。"
        )

    task = locked.task
    existing = ContactedCreator.objects.filter(
        store_id=task.store_id,
        normalized_handle=locked.normalized_handle,
    ).first()
    prior_evidence = (
        existing.evidence
        if existing is not None and isinstance(existing.evidence, dict)
        else {}
    )
    record, _created = ContactedCreator.objects.update_or_create(
        store_id=task.store_id,
        normalized_handle=locked.normalized_handle,
        defaults={
            "creator_handle": locked.creator_handle_snapshot,
            "chat_creator_id": (
                locked.chat_creator_id
                or (existing.chat_creator_id if existing else "")
            ),
            "creator": locked.creator,
            "contact_task": task,
            "greeting_sha256": task.greeting_sha256,
            "invitation_id": (
                locked.actual_invitation_id
                or (existing.invitation_id if existing else "")
            ),
            "invitation_name": task.invitation_name_snapshot,
            "evidence": {
                **prior_evidence,
                "contactScope": "store_creator",
                "contactStage": "INVITATION_COMPLETED",
                "targetId": locked.pk,
                "messageSent": locked.message_sent,
                "creatorId": locked.chat_creator_id,
                "invitationGroupId": locked.invitation_group_id,
                "invitationCompleted": (
                    stored_result.get("invitationCompleted") is True
                ),
                "invitationButtonClicked": (
                    stored_result.get("invitationButtonClicked") is True
                ),
                "alreadySent": stored_result.get("alreadySent") is True,
                "creatorTabsClosed": (
                    stored_result.get("creatorTabsClosed") is True
                ),
                "returnedToFindCreators": (
                    stored_result.get("returnedToFindCreators") is True
                ),
            },
        },
    )
    return record


@transaction.atomic
def record_successful_contact(
    target: CreatorContactTarget,
) -> ContactedCreator:
    """Enrich store-global contact history after verified card delivery."""
    locked = (
        CreatorContactTarget.objects
        .select_for_update()
        .select_related("task", "creator")
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
        and locked.invitation_created
        and locked.card_sent
        and locked.target_plan_message_verified
        and locked.final_send_verified
        and evidence_error is None
    ):
        raise ValidationError(
            "尚未通过精确项目达人核验和合作卡片终态验收，"
            "不能标记合作卡片已送达。"
        )
    task = locked.task
    record, _created = ContactedCreator.objects.update_or_create(
        store_id=task.store_id,
        normalized_handle=locked.normalized_handle,
        defaults={
            "creator_handle": locked.creator_handle_snapshot,
            "chat_creator_id": locked.chat_creator_id,
            "creator": locked.creator,
            "contact_task": task,
            "greeting_sha256": task.greeting_sha256,
            "invitation_id": locked.actual_invitation_id,
            "invitation_name": task.invitation_name_snapshot,
            "evidence": {
                "contactScope": "store_creator",
                "contactStage": "CARD_DELIVERED",
                "targetId": locked.pk,
                "messageSent": True,
                "invitationCreated": locked.invitation_created,
                "invitationGroupId": locked.invitation_group_id,
                "invitationCompleted": True,
                "invitationButtonClicked": locked.result.get(
                    "invitationButtonClicked",
                    False,
                ),
                "creatorTabsClosed": locked.result.get(
                    "creatorTabsClosed",
                    False,
                ),
                "searchTabKept": locked.result.get(
                    "searchTabKept",
                    False,
                ),
                "returnedToFindCreators": locked.result.get(
                    "returnedToFindCreators",
                    False,
                ),
                "findCreatorsSearchReady": locked.result.get(
                    "findCreatorsSearchReady",
                    False,
                ),
                "acceptedCreatorsPageVisible": True,
                "projectMembershipVerified": True,
                "recipientVerified": True,
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
        card_executor: CardExecutor | None = None,
        membership_executor: MembershipExecutor | None = None,
    ) -> None:
        self.task = task
        self.executor = executor or SubprocessContactExecutor()
        self.card_executor = card_executor or SubprocessCardExecutor()
        self.membership_executor = (
            membership_executor or SubprocessMembershipExecutor()
        )

    def run(self) -> CreatorContactTask:
        self.task.refresh_from_db()
        if self.task.status == CreatorContactTask.Status.CANCELLED:
            return self.task
        self.task.status = CreatorContactTask.Status.RUNNING
        self.task.progress = 0
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
            if self._cancellation_requested():
                return self._cancel_task()
            if target.status in {
                CreatorContactTarget.Status.SUCCESS,
                CreatorContactTarget.Status.INVITATION_COMPLETED,
                CreatorContactTarget.Status.SKIPPED,
                CreatorContactTarget.Status.REVIEW_REQUIRED,
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
            except TaskCancellationRequested:
                return self._cancel_task()
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
            if result.get("taskFatal") is True:
                return self._fail_task(
                    str(
                        result.get("errorCode")
                        or "CONTACT_TASK_FATAL"
                    ),
                    str(
                        result.get("errorMessage")
                        or "店铺浏览器连接阶段发生任务级错误。"
                    ),
                )
            if self._cancellation_requested():
                return self._cancel_task()

        if self._cancellation_requested():
            return self._cancel_task()
        try:
            self._run_card_phase()
        except TaskCancellationRequested:
            return self._cancel_task()
        if self._cancellation_requested():
            return self._cancel_task()
        return self._finish_task()

    def _run_card_phase(self) -> None:
        targets = list(
            self.task.targets.filter(
                status=CreatorContactTarget.Status.INVITATION_COMPLETED,
            ).order_by("rank")
        )
        if not targets:
            return
        self.task.current_step = (
            f"批量发送 {len(targets)} 位达人的定向合作卡片"
        )
        self.task.save(update_fields=["current_step", "updated_at"])
        batch_executor = getattr(self.card_executor, "run_many", None)
        results: dict[int, dict[str, Any]] = {}
        if callable(batch_executor):
            try:
                results = batch_executor(self.task, targets)
            except TaskCancellationRequested:
                raise
            except Exception as error:
                results = {
                    target.pk: {
                        "success": False,
                        "errorCode": type(error).__name__,
                        "errorMessage": _redact(error),
                        "steps": [],
                    }
                    for target in targets
                }
        else:
            for target in targets:
                try:
                    results[target.pk] = self.card_executor(
                        self.task,
                        target,
                    )
                except TaskCancellationRequested:
                    raise
                except Exception as error:
                    results[target.pk] = {
                        "success": False,
                        "errorCode": type(error).__name__,
                        "errorMessage": _redact(error),
                        "steps": [],
                    }
        for index, target in enumerate(targets, start=1):
            result = results.get(target.pk) or {
                "success": False,
                "errorCode": "CARD_BATCH_RESULT_MISSING",
                "errorMessage": (
                    f"批量合作卡片结果缺少达人 "
                    f"@{target.normalized_handle}。"
                ),
                "steps": [],
            }
            self._persist_steps(target, result.get("steps") or [])
            self._persist_card_result(target, result)
            self.task.progress = min(
                99,
                70 + int(index / max(len(targets), 1) * 29),
            )
            self.task.current_step = target.current_step
            self.task.save(
                update_fields=[
                    "progress",
                    "current_step",
                    "updated_at",
                ]
            )

    def _reconcile_project_memberships(self) -> None:
        recoverable_targets = list(
            self.task.targets.filter(
                status=CreatorContactTarget.Status.FAILED,
            )
            .filter(
                Q(message_sent=True)
                | Q(
                    steps__operation="ziniao_send_greeting",
                    steps__status=CreatorContactTaskStep.Status.SUCCESS,
                )
            )
            .distinct()
            .order_by("rank")
        )
        batch_memberships: dict[int, dict[str, Any]] = {}
        batch_membership_executor = getattr(
            self.membership_executor,
            "run_many",
            None,
        )
        if recoverable_targets and callable(batch_membership_executor):
            try:
                batch_memberships = batch_membership_executor(
                    self.task,
                    recoverable_targets,
                )
            except TaskCancellationRequested:
                raise
            except Exception as error:
                batch_memberships = {
                    target.pk: {
                        "success": False,
                        "errorCode": type(error).__name__,
                        "errorMessage": _redact(error),
                        "steps": [],
                        "deliverySource": (
                            "project_creator_details_reconciliation"
                        ),
                    }
                    for target in recoverable_targets
                }
        for target in recoverable_targets:
            membership = batch_memberships.get(target.pk)
            if membership is None:
                try:
                    membership = self.membership_executor(self.task, target)
                except TaskCancellationRequested:
                    raise
                except Exception as error:
                    membership = {
                        "success": False,
                        "errorCode": type(error).__name__,
                        "errorMessage": _redact(error),
                        "steps": [],
                        "deliverySource": (
                            "project_creator_details_reconciliation"
                        ),
                    }
            self._persist_steps(target, membership.get("steps") or [])
            if not (
                membership.get("success") is True
                and membership.get("projectMembershipVerified") is True
            ):
                continue
            prior_result = (
                target.result if isinstance(target.result, dict) else {}
            )
            reconciled = {
                **prior_result,
                **membership,
                "success": True,
                "creatorId": (
                    prior_result.get("creatorId")
                    or target.chat_creator_id
                ),
                "messageSent": True,
                "invitationCreated": True,
                "invitationCompleted": True,
                "invitationButtonClicked": (
                    prior_result.get("invitationButtonClicked") is True
                ),
                "invitationSent": True,
                "invitationGroupId": self.task.invitation_id_snapshot,
                "invitationVerificationSource": (
                    "accepted_creator_list"
                ),
                "cardSent": False,
                "targetPlanMessageVerified": False,
                "finalSendVerified": False,
            }
            self._persist_target_result(target, reconciled)

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
                        {
                            **(
                                step.get("outputSummary")
                                if isinstance(
                                    step.get("outputSummary"),
                                    dict,
                                )
                                else {}
                            ),
                            **(
                                {
                                    "domFallback": step.get(
                                        "domFallback"
                                    )
                                }
                                if step.get("domFallback")
                                else {}
                            ),
                            **(
                                {
                                    "adaptiveLocator": step.get(
                                        "adaptiveLocator"
                                    )
                                }
                                if step.get("adaptiveLocator")
                                else {}
                            ),
                        }
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
            locked.invitation_group_id = str(
                result.get("invitationGroupId") or ""
            )[:128]
            locked.message_sent = result.get("messageSent") is True
            locked.invitation_created = (
                result.get("invitationCreated") is True
                or result.get("invitationSent") is True
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
            review_required = result.get("reviewRequired") is True
            invitation_evidence_error = _invitation_evidence_error(
                self.task,
                result,
                invitation_group_id=locked.invitation_group_id,
            )
            invitation_success = (
                result.get("success") is True
                and locked.message_sent
                and locked.invitation_created
                and invitation_evidence_error is None
            )
            if invitation_success:
                locked.status = (
                    CreatorContactTarget.Status.INVITATION_COMPLETED
                )
                locked.current_step = "邀请按钮已点击，等待批量发送卡片"
                locked.error_code = ""
                locked.error_message = ""
            elif review_required:
                locked.status = (
                    CreatorContactTarget.Status.REVIEW_REQUIRED
                )
                locked.current_step = "存在写入证据，需人工复核"
                locked.error_code = str(
                    result.get("errorCode")
                    or "CONTACT_REVIEW_REQUIRED"
                )[:100]
                locked.error_message = _redact(
                    result.get("errorMessage")
                    or "自动恢复与模型兜底均失败，且已存在写入证据。"
                )
            elif skip_requested:
                locked.status = CreatorContactTarget.Status.SKIPPED
                exact_creator_unavailable = (
                    result.get("errorCode")
                    == "CREATOR_EXACT_MATCH_UNAVAILABLE"
                )
                locked.current_step = (
                    "精确达人账号不可用，已跳过"
                    if exact_creator_unavailable
                    else "定向合作邀请未完成，已跳过"
                )
                locked.error_code = str(
                    result.get("errorCode")
                    or "INVITATION_NOT_VERIFIED"
                )[:100]
                locked.error_message = _redact(
                    result.get("skipReason")
                    or "未取得定向合作邀请成功证据；已按任务规则跳过。"
                )
            else:
                locked.status = CreatorContactTarget.Status.FAILED
                locked.current_step = "联系达人失败"
                locked.error_code = str(
                    result.get("errorCode")
                    or (invitation_evidence_error or ("", ""))[0]
                    or "INVITATION_NOT_VERIFIED"
                )[:100]
                locked.error_message = _redact(
                    result.get("errorMessage")
                    or (invitation_evidence_error or ("", ""))[1]
                    or "未取得定向合作邀请提交成功证据。"
                )
            locked.finished_at = (
                None
                if invitation_success
                else timezone.now()
            )
            locked.save()
        target.refresh_from_db()
        stored_target_result = (
            target.result if isinstance(target.result, dict) else {}
        )
        if (
            (
                stored_target_result.get("invitationButtonClicked") is True
                or stored_target_result.get("alreadySent") is True
            )
            and target.invitation_group_id
            == self.task.invitation_id_snapshot
            and stored_target_result.get("invitationGroupId")
            == self.task.invitation_id_snapshot
        ):
            record_invited_creator(target)

    def _persist_card_result(
        self,
        target: CreatorContactTarget,
        result: dict[str, Any],
    ) -> None:
        with transaction.atomic():
            locked = CreatorContactTarget.objects.select_for_update().get(
                pk=target.pk
            )
            prior_result = (
                locked.result if isinstance(locked.result, dict) else {}
            )
            actual_invitation_id = str(
                result.get("invitationId") or ""
            )[:128]
            if actual_invitation_id:
                locked.actual_invitation_id = actual_invitation_id
            result_group_id = str(
                result.get("invitationGroupId") or ""
            )[:128]
            if result_group_id:
                locked.invitation_group_id = result_group_id
            locked.card_sent = result.get("cardSent") is True
            locked.target_plan_message_verified = (
                result.get("targetPlanMessageVerified") is True
            )
            locked.final_send_verified = (
                result.get("finalSendVerified") is True
            )
            locked.result = _json_safe(
                {
                    **prior_result,
                    **{
                        key: value
                        for key, value in result.items()
                        if key not in {"steps", "greetingMessage"}
                    },
                }
            )
            evidence_error = _terminal_evidence_error(
                self.task,
                locked.result,
                actual_invitation_id=locked.actual_invitation_id,
                invitation_group_id=locked.invitation_group_id,
            )
            card_success = (
                result.get("success") is True
                and locked.message_sent
                and locked.invitation_created
                and locked.card_sent
                and locked.target_plan_message_verified
                and locked.final_send_verified
                and evidence_error is None
            )
            if card_success:
                locked.status = CreatorContactTarget.Status.SUCCESS
                locked.current_step = "定向合作卡片发送成功"
                locked.error_code = ""
                locked.error_message = ""
            else:
                locked.status = (
                    CreatorContactTarget.Status.INVITATION_COMPLETED
                )
                locked.current_step = "邀请已完成，合作卡片尚未发送"
                locked.error_code = str(
                    result.get("errorCode")
                    or (evidence_error or ("", ""))[0]
                    or "CARD_NOT_VERIFIED"
                )[:100]
                locked.error_message = _redact(
                    result.get("errorMessage")
                    or (evidence_error or ("", ""))[1]
                    or "未在精确项目达人列表中完成合作卡片发送。"
                )
            locked.finished_at = timezone.now()
            locked.save()
            if card_success:
                record_successful_contact(locked)
        target.refresh_from_db()

    def _update_progress(
        self,
        processed: int,
        total: int,
        target: CreatorContactTarget,
    ) -> None:
        self.task.progress = min(
            70,
            int(processed / max(total, 1) * 70),
        )
        self.task.current_step = target.current_step
        self.task.save(
            update_fields=["progress", "current_step", "updated_at"]
        )

    def _finish_task(self) -> CreatorContactTask:
        if self._cancellation_requested():
            return self._cancel_task()
        counts = {
            status: self.task.targets.filter(status=status).count()
            for status in CreatorContactTarget.Status.values
        }
        failed = counts[CreatorContactTarget.Status.FAILED]
        succeeded = counts[CreatorContactTarget.Status.SUCCESS]
        skipped = counts[CreatorContactTarget.Status.SKIPPED]
        review_required = counts[
            CreatorContactTarget.Status.REVIEW_REQUIRED
        ]
        card_pending = counts[
            CreatorContactTarget.Status.INVITATION_COMPLETED
        ]
        invitations = self.task.targets.filter(
            invitation_created=True
        ).count()
        cards_sent = self.task.targets.filter(
            card_sent=True,
            final_send_verified=True,
        ).count()
        if (
            failed == 0
            and card_pending == 0
            and review_required == 0
        ):
            status = CreatorContactTask.Status.SUCCESS
        elif succeeded or skipped or card_pending or review_required:
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
            "reviewRequiredCount": review_required,
            "invitationCompletedCount": invitations,
            "cardSentCount": cards_sent,
            "cardPendingCount": card_pending,
        }
        first_failure = self.task.targets.filter(
            status__in=(
                CreatorContactTarget.Status.FAILED,
                CreatorContactTarget.Status.REVIEW_REQUIRED,
                CreatorContactTarget.Status.INVITATION_COMPLETED,
            )
        ).order_by("rank").first()
        if first_failure is not None:
            self.task.error_code = first_failure.error_code
            self.task.error_message = first_failure.error_message
        else:
            self.task.error_code = ""
            self.task.error_message = ""
        self.task.save()
        return self.task

    def _cancellation_requested(self) -> bool:
        return task_cancellation_requested(self.task.pk)

    def _cancel_task(self) -> CreatorContactTask:
        now = timezone.now()
        CreatorContactTarget.objects.filter(
            task=self.task,
            status__in={
                CreatorContactTarget.Status.PENDING,
                CreatorContactTarget.Status.RUNNING,
            },
        ).update(
            status=CreatorContactTarget.Status.SKIPPED,
            current_step="任务已终止，未继续执行",
            error_code="TASK_CANCELLED",
            error_message="达人联系任务已由用户终止。",
            finished_at=now,
            updated_at=now,
        )
        self.task.refresh_from_db()
        self.task.status = CreatorContactTask.Status.CANCELLED
        self.task.current_step = "达人联系任务已终止"
        self.task.error_code = "TASK_CANCELLED"
        self.task.error_message = "达人联系任务已由用户终止。"
        self.task.finished_at = self.task.finished_at or now
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
