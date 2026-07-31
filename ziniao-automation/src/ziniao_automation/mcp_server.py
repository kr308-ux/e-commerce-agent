"""Minimal stdio MCP server for the validated Ziniao contact workflow."""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from shared.logger import JsonlAuditLogger, project_log_root

from .actions.creator_contact import CreatorContactWorkflow
from .browser_connection import (
    ReusableStoreConnection,
    connect_reusable_store,
)
from .config import ZiniaoSettings
from .dom_fallback import DeepSeekDomFallback, is_dom_failure_message
from .errors import (
    ZiniaoError,
    ZiniaoWorkflowError,
)
from .session import SeleniumStoreSession


SERVER_NAME = "ziniao-contact-mcp"
SERVER_VERSION = "0.1.0"
TOOL_ORDER = (
    "ziniao_connect",
    "ziniao_open_find_creators",
    "ziniao_search_creator",
    "ziniao_open_creator_detail",
    "ziniao_open_message_panel",
    "ziniao_open_chat_new_tab",
    "ziniao_verify_chat_recipient",
    "ziniao_send_greeting",
    "ziniao_open_target_collaboration",
    "ziniao_open_other_invitation_dialog",
    "ziniao_select_invitation",
    "ziniao_send_selected_invitation",
    "ziniao_disconnect",
)
RECOVERABLE_STEP_TOOLS = {
    "ziniao_open_find_creators",
    "ziniao_search_creator",
    "ziniao_open_creator_detail",
    "ziniao_open_message_panel",
    "ziniao_open_chat_new_tab",
    "ziniao_verify_chat_recipient",
    "ziniao_open_target_collaboration",
    "ziniao_open_other_invitation_dialog",
    "ziniao_select_invitation",
}
WRITE_STEP_TOOLS = {
    "ziniao_send_greeting",
    "ziniao_send_selected_invitation",
}
NON_RETRYABLE_MESSAGE_MARKERS = (
    "与目标达人不一致",
    "不得更换",
    "与任务固定店铺不一致",
    "哈希不一致",
    "必须显式确认",
    "invitationGroupId 与任务目标不一致",
)


def _operation_schema(
    *,
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
) -> dict[str, Any]:
    all_properties: dict[str, Any] = {
        "taskId": {"type": "string", "minLength": 1},
        "stepId": {"type": "string", "minLength": 1},
        **(properties or {}),
    }
    return {
        "type": "object",
        "properties": all_properties,
        "required": ["taskId", "stepId", *(required or [])],
        "additionalProperties": False,
    }


TOOLS: list[dict[str, Any]] = [
    {
        "name": "ziniao_connect",
        "title": "连接紫鸟店铺",
        "description": (
            "优先复用已验证的紫鸟店铺浏览器；仅在缓存失效时启动浏览器，"
            "随后附加可见 Selenium。"
        ),
        "inputSchema": _operation_schema(
            properties={
                "storeId": {"type": "string", "minLength": 1},
            },
            required=["storeId"],
        ),
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "ziniao_open_find_creators",
        "title": "进入寻找达人",
        "description": (
            "优先通过 CDP 激活已打开的查找达人页；不存在时才进入联盟和"
            "寻找达人，并验收搜索列表页。"
        ),
        "inputSchema": _operation_schema(),
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "ziniao_search_creator",
        "title": "搜索达人",
        "description": (
            "输入达人用户名，点击下拉候选项，下滑并验收搜索结果卡片。"
        ),
        "inputSchema": _operation_schema(
            properties={
                "creator": {"type": "string", "minLength": 1},
            },
            required=["creator"],
        ),
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "ziniao_open_creator_detail",
        "title": "打开达人卡片",
        "description": "点击搜索结果中的目标达人卡片，并验收详情新标签页。",
        "inputSchema": _operation_schema(
            properties={
                "creator": {"type": "string", "minLength": 1},
            },
            required=["creator"],
        ),
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "ziniao_open_message_panel",
        "title": "打开私信抽屉",
        "description": "点击达人详情私信图标并验收聊天抽屉；不输入消息。",
        "inputSchema": _operation_schema(
            properties={
                "creator": {"type": "string", "minLength": 1},
            },
            required=["creator"],
        ),
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "ziniao_open_chat_new_tab",
        "title": "新标签页打开聊天",
        "description": (
            "点击聊天抽屉的外部打开按钮，验收新聊天标签页；不发送消息。"
        ),
        "inputSchema": _operation_schema(
            properties={
                "creator": {"type": "string", "minLength": 1},
            },
            required=["creator"],
        ),
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "ziniao_verify_chat_recipient",
        "title": "确认聊天对象",
        "description": (
            "同时核验聊天页顶部用户名与 URL creator_id；不输入消息。"
        ),
        "inputSchema": _operation_schema(
            properties={
                "creator": {"type": "string", "minLength": 1},
                "creatorId": {
                    "type": "string",
                    "pattern": "^[0-9]+$",
                },
            },
            required=["creator"],
        ),
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "ziniao_send_greeting",
        "title": "发送任务招呼语",
        "description": (
            "再次核验聊天对象，只发送任务快照中的精确招呼语，并验收消息气泡与哈希。"
        ),
        "inputSchema": _operation_schema(
            properties={
                "creator": {"type": "string", "minLength": 1},
                "creatorId": {
                    "type": "string",
                    "pattern": "^[0-9]+$",
                },
                "greetingMessage": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 2000,
                },
                "greetingSha256": {
                    "type": "string",
                    "pattern": "^[0-9a-fA-F]{64}$",
                },
                "confirmSendGreeting": {
                    "type": "boolean",
                    "const": True,
                },
            },
            required=[
                "creator",
                "greetingMessage",
                "greetingSha256",
                "confirmSendGreeting",
            ],
        ),
        "annotations": {"readOnlyHint": False, "destructiveHint": True},
    },
    {
        "name": "ziniao_open_target_collaboration",
        "title": "打开定向合作",
        "description": "点击并验收聊天页右侧的定向合作页签。",
        "inputSchema": _operation_schema(
            properties={
                "creator": {"type": "string", "minLength": 1},
                "creatorId": {
                    "type": "string",
                    "pattern": "^[0-9]+$",
                },
            },
            required=["creator"],
        ),
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "ziniao_open_other_invitation_dialog",
        "title": "打开其他合作邀请",
        "description": (
            "点击“发送其他邀请开展合作”，并核验弹窗中的目标达人。"
        ),
        "inputSchema": _operation_schema(
            properties={
                "creator": {"type": "string", "minLength": 1},
                "creatorId": {
                    "type": "string",
                    "pattern": "^[0-9]+$",
                },
            },
            required=["creator"],
        ),
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "ziniao_select_invitation",
        "title": "选择定向合作邀请",
        "description": (
            "精确选择指定邀请并验收单选状态；不会点击最终邀请按钮。"
        ),
        "inputSchema": _operation_schema(
            properties={
                "creator": {"type": "string", "minLength": 1},
                "creatorId": {
                    "type": "string",
                    "pattern": "^[0-9]+$",
                },
                "invitationName": {
                    "type": "string",
                    "minLength": 1,
                },
                "invitationGroupId": {
                    "type": "string",
                    "pattern": "^[0-9]+$",
                },
            },
            required=[
                "creator",
                "invitationName",
                "invitationGroupId",
            ],
        ),
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "ziniao_send_selected_invitation",
        "title": "发送已选择邀请",
        "description": (
            "仅在全部前置验收通过后点击一次最终邀请按钮，并验证成功结果。"
        ),
        "inputSchema": _operation_schema(
            properties={
                "creator": {"type": "string", "minLength": 1},
                "creatorId": {
                    "type": "string",
                    "pattern": "^[0-9]+$",
                },
                "invitationName": {
                    "type": "string",
                    "minLength": 1,
                },
                "invitationGroupId": {
                    "type": "string",
                    "pattern": "^[0-9]+$",
                },
                "confirmSendInvitation": {
                    "type": "boolean",
                    "const": True,
                },
            },
            required=[
                "creator",
                "invitationName",
                "invitationGroupId",
                "confirmSendInvitation",
            ],
        ),
        "annotations": {"readOnlyHint": False, "destructiveHint": True},
    },
    {
        "name": "ziniao_disconnect",
        "title": "断开紫鸟会话",
        "description": (
            "仅断开本次 Selenium/ChromeDriver 控制连接；"
            "保留紫鸟店铺浏览器和已验收的查找达人标签页。"
        ),
        "inputSchema": _operation_schema(),
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
]


def _success(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "success": True,
        "status": "SUCCESS",
        "data": data,
        "error": None,
    }


def _failure(code: str, message: str) -> dict[str, Any]:
    return {
        "success": False,
        "status": "FAILED",
        "data": None,
        "error": {
            "code": code,
            "userMessage": message,
            "retryable": False,
        },
    }


class ContactAutomationState:
    """Own one ordered Selenium session for one MCP client."""

    def __init__(self) -> None:
        self.session: SeleniumStoreSession | None = None
        self.browser_connection: ReusableStoreConnection | None = None
        self.workflow: CreatorContactWorkflow | None = None
        self.expected_store_id = os.getenv(
            "ZINIAO_EXPECTED_STORE_ID", ""
        ).strip()
        configured_creator = os.getenv(
            "ZINIAO_EXPECTED_CREATOR", ""
        ).strip()
        if configured_creator:
            configured_creator, _bare = (
                CreatorContactWorkflow.normalize_creator_handle(
                    configured_creator
                )
            )
        self.creator: str | None = configured_creator or None
        self.creator_id: str | None = None
        self.invitation_name: str | None = (
            os.getenv("ZINIAO_EXPECTED_INVITATION_NAME", "").strip()
            or None
        )
        encoded_greeting = os.getenv(
            "ZINIAO_EXPECTED_GREETING_B64", ""
        ).strip()
        self.expected_greeting = ""
        if encoded_greeting:
            try:
                decoded_greeting = base64.b64decode(
                    encoded_greeting, validate=True
                ).decode("utf-8")
                self.expected_greeting = (
                    CreatorContactWorkflow.validate_greeting_message(
                        decoded_greeting
                    )
                )
            except (ValueError, UnicodeDecodeError) as error:
                raise ZiniaoWorkflowError(
                    "ZINIAO_EXPECTED_GREETING_B64 不是有效 UTF-8 快照。"
                ) from error
        configured_greeting_sha256 = os.getenv(
            "ZINIAO_EXPECTED_GREETING_SHA256", ""
        ).strip()
        self.expected_greeting_sha256 = (
            CreatorContactWorkflow.greeting_sha256(
                self.expected_greeting
            )
            if self.expected_greeting
            else configured_greeting_sha256
        )
        configured_group_id = os.getenv(
            "ZINIAO_INVITATION_GROUP_ID", ""
        ).strip()
        if configured_group_id and not configured_group_id.isdigit():
            raise ZiniaoWorkflowError(
                "ZINIAO_INVITATION_GROUP_ID 必须为有效数字 ID。"
            )
        self.invitation_group_id: str | None = (
            configured_group_id or None
        )
        self.completed: dict[str, dict[str, Any]] = {}
        self.terminal_failure: dict[str, Any] | None = None
        self.failed_tool_name: str | None = None
        self.should_exit = False
        self.skip_creator = False
        self.review_required = False
        self.task_fatal = False
        self.failure_stage = ""
        self.task_id = ""
        self.session_id = (
            os.getenv("ZINIAO_RUN_SESSION_ID", "").strip()
            or f"mcp_{uuid.uuid4().hex}"
        )
        project_root = Path(__file__).resolve().parents[3]
        self.audit_logger = JsonlAuditLogger(
            root=project_log_root(project_root),
            category="regular",
            component="creator-contact",
            session_id=self.session_id,
        )

    def _bind_audit_context(self, arguments: dict[str, Any]) -> None:
        task_id = str(arguments.get("taskId") or "").strip()
        if task_id:
            self.task_id = task_id
            self.audit_logger.bind(task_id=task_id)

    def _browser_context(self) -> dict[str, Any]:
        if self.workflow is None:
            return {}
        try:
            return {
                "currentUrl": self.workflow.driver.current_url,
                "title": self.workflow.driver.title,
                "windowCount": len(self.workflow.driver.window_handles),
            }
        except Exception as error:
            return {"contextError": type(error).__name__}

    def _logged_result(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        started_at: float,
        source: str = "executed",
    ) -> dict[str, Any]:
        success = result.get("success") is True
        self.audit_logger.write(
            "operation_finished",
            status="SUCCESS" if success else "FAILED",
            task_id=self.task_id,
            session_id=self.session_id,
            operation=tool_name,
            input_content=arguments,
            output_content=result,
            duration_ms=(time.perf_counter() - started_at) * 1000,
            metadata={
                "source": source,
                "browser": self._browser_context(),
            },
        )
        return result

    def _safe_message(self, error: Exception) -> str:
        message = str(error)
        if self.session is not None:
            credentials = self.session.settings.credentials
            for sensitive in (
                credentials.company,
                credentials.username,
                credentials.password,
            ):
                if sensitive:
                    message = message.replace(sensitive, "[REDACTED]")
        return message

    @staticmethod
    def _step_error_recoverable(
        tool_name: str,
        error: Exception,
    ) -> bool:
        if tool_name not in RECOVERABLE_STEP_TOOLS:
            return False
        message = str(error)
        if any(marker in message for marker in NON_RETRYABLE_MESSAGE_MARKERS):
            return False
        return (
            isinstance(error, ZiniaoError)
            and is_dom_failure_message(message)
        ) or type(error).__name__ in {
            "StaleElementReferenceException",
            "TimeoutException",
            "ElementClickInterceptedException",
            "NoSuchWindowException",
        }

    def _log_attempt(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        stage: str,
        status: str,
        error: Exception | None = None,
        recovery: dict[str, Any] | None = None,
    ) -> None:
        self.audit_logger.write(
            "operation_attempt",
            status=status,
            task_id=self.task_id,
            session_id=self.session_id,
            operation=tool_name,
            input_content=arguments,
            output_content={
                "stage": stage,
                "recovery": recovery or {},
            },
            error=(
                {
                    "type": type(error).__name__,
                    "message": self._safe_message(error),
                }
                if error is not None
                else None
            ),
            metadata={
                "stateMachineStage": stage,
                "browser": self._browser_context(),
            },
        )

    def _select_store(
        self,
        stores: list[StoreInfo],
        store_id: str,
    ) -> StoreInfo:
        matches = [
            store
            for store in stores
            if store.browser_id == store_id and not store.is_expired
        ]
        if len(matches) != 1:
            raise ZiniaoStoreSelectionError(
                "未找到唯一且未过期的已授权目标店铺。"
            )
        return matches[0]

    def _require_workflow(self) -> CreatorContactWorkflow:
        if self.workflow is None:
            raise ZiniaoWorkflowError("尚未连接紫鸟店铺。")
        return self.workflow

    def _check_order(self, tool_name: str) -> None:
        if tool_name in self.completed:
            return
        if tool_name == "ziniao_disconnect" and self.session is not None:
            return
        expected = TOOL_ORDER[len(self.completed)]
        if tool_name != expected:
            raise ZiniaoWorkflowError(
                f"工具调用顺序错误：当前应调用 {expected}。"
            )

    def _remember_creator(self, raw_creator: object) -> str:
        creator = str(raw_creator or "")
        normalized, _bare = CreatorContactWorkflow.normalize_creator_handle(
            creator
        )
        if self.creator is not None and self.creator != normalized:
            raise ZiniaoWorkflowError("同一任务中不得更换目标达人。")
        self.creator = normalized
        return normalized

    def _remember_creator_id(self, raw_creator_id: object) -> str:
        creator_id = CreatorContactWorkflow.normalize_creator_id(
            str(raw_creator_id or "")
        )
        if self.creator_id is not None and self.creator_id != creator_id:
            raise ZiniaoWorkflowError("同一任务中不得更换达人 creator_id。")
        self.creator_id = creator_id
        return creator_id

    def _remember_invitation_name(self, raw_name: object) -> str:
        invitation_name = str(raw_name or "").strip()
        if not invitation_name:
            raise ZiniaoWorkflowError("邀请名称不能为空。")
        if (
            self.invitation_name is not None
            and self.invitation_name != invitation_name
        ):
            raise ZiniaoWorkflowError("同一任务中不得更换定向合作邀请。")
        self.invitation_name = invitation_name
        return invitation_name

    def _remember_invitation_group_id(self, raw_group_id: object) -> str:
        invitation_group_id = str(raw_group_id or "").strip()
        if not invitation_group_id.isdigit():
            raise ZiniaoWorkflowError(
                "invitationGroupId 必须为有效数字 ID。"
            )
        if (
            self.invitation_group_id is not None
            and self.invitation_group_id != invitation_group_id
        ):
            raise ZiniaoWorkflowError(
                "同一任务中不得更换 invitationGroupId。"
            )
        self.invitation_group_id = invitation_group_id
        return invitation_group_id

    @staticmethod
    def _result_invitation_group_id(
        result: dict[str, Any],
    ) -> str:
        return str(
            result.get("evidence", {}).get("invitationGroupId") or ""
        ).strip()

    def _verify_result_invitation_group_id(
        self,
        result: dict[str, Any],
        expected_group_id: str,
        *,
        stage: str,
    ) -> None:
        actual_group_id = self._result_invitation_group_id(result)
        if actual_group_id != expected_group_id:
            raise ZiniaoWorkflowError(
                f"{stage}返回的 invitationGroupId 与任务目标不一致。"
            )

    def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        started_at = time.perf_counter()
        self._bind_audit_context(arguments)
        self.audit_logger.write(
            "operation_started",
            status="STARTED",
            task_id=self.task_id,
            session_id=self.session_id,
            operation=tool_name,
            input_content=arguments,
            metadata={"browser": self._browser_context()},
        )
        if tool_name not in TOOL_ORDER:
            return self._logged_result(
                tool_name=tool_name,
                arguments=arguments,
                result=_failure("UNKNOWN_TOOL", f"未知工具：{tool_name}"),
                started_at=started_at,
                source="rejected",
            )
        if self.terminal_failure is not None:
            if tool_name == "ziniao_disconnect":
                return self._logged_result(
                    tool_name=tool_name,
                    arguments=arguments,
                    result=_success({"disconnected": True}),
                    started_at=started_at,
                    source="terminal_disconnect",
                )
            return self._logged_result(
                tool_name=tool_name,
                arguments=arguments,
                result=_failure(
                    "TASK_TERMINATED_AFTER_FAILURE",
                    (
                        "任务已因工具 "
                        f"{self.failed_tool_name or 'unknown'} "
                        "发生不可重试错误而终止，不得重复发送或继续后续步骤。"
                    ),
                ),
                started_at=started_at,
                source="terminal_rejected",
            )
        if tool_name in self.completed:
            return self._logged_result(
                tool_name=tool_name,
                arguments=arguments,
                result=self.completed[tool_name],
                started_at=started_at,
                source="cached",
            )
        try:
            self._check_order(tool_name)
            handlers: dict[str, Callable[[], dict[str, Any]]] = {
                "ziniao_connect": lambda: self._connect(arguments),
                "ziniao_open_find_creators": self._open_find_creators,
                "ziniao_search_creator": lambda: self._search_creator(arguments),
                "ziniao_open_creator_detail": lambda: self._open_creator_detail(
                    arguments
                ),
                "ziniao_open_message_panel": lambda: self._open_message_panel(
                    arguments
                ),
                "ziniao_open_chat_new_tab": lambda: self._open_chat_new_tab(
                    arguments
                ),
                "ziniao_verify_chat_recipient": lambda: (
                    self._verify_chat_recipient(arguments)
                ),
                "ziniao_send_greeting": lambda: (
                    self._send_greeting(arguments)
                ),
                "ziniao_open_target_collaboration": lambda: (
                    self._open_target_collaboration(arguments)
                ),
                "ziniao_open_other_invitation_dialog": lambda: (
                    self._open_other_invitation_dialog(arguments)
                ),
                "ziniao_select_invitation": lambda: (
                    self._select_invitation(arguments)
                ),
                "ziniao_send_selected_invitation": lambda: (
                    self._send_selected_invitation(arguments)
                ),
                "ziniao_disconnect": self._disconnect,
            }
            handler = handlers[tool_name]
            stages = (
                ("deterministic-1", False),
                ("deterministic-2", False),
                ("model-fallback", True),
            )
            last_error: Exception | None = None
            used_stages: list[str] = []
            for stage_index, (stage, model_enabled) in enumerate(stages):
                if stage_index > 0:
                    if (
                        last_error is None
                        or not self._step_error_recoverable(
                            tool_name,
                            last_error,
                        )
                        or self.workflow is None
                    ):
                        break
                    try:
                        recovery = self.workflow.recover_for_retry(
                            tool_name
                        )
                        self._log_attempt(
                            tool_name=tool_name,
                            arguments=arguments,
                            stage=f"recovery-before-{stage}",
                            status="SUCCESS",
                            recovery=recovery,
                        )
                    except Exception as recovery_error:
                        last_error = recovery_error
                        self._log_attempt(
                            tool_name=tool_name,
                            arguments=arguments,
                            stage=f"recovery-before-{stage}",
                            status="FAILED",
                            error=recovery_error,
                        )
                        break
                if self.workflow is not None:
                    self.workflow.set_model_fallback_enabled(
                        model_enabled
                    )
                used_stages.append(stage)
                try:
                    data = handler()
                except Exception as attempt_error:
                    last_error = attempt_error
                    self._log_attempt(
                        tool_name=tool_name,
                        arguments=arguments,
                        stage=stage,
                        status="FAILED",
                        error=attempt_error,
                    )
                    continue
                self._log_attempt(
                    tool_name=tool_name,
                    arguments=arguments,
                    stage=stage,
                    status="SUCCESS",
                )
                if self.workflow is not None:
                    fallback_events = (
                        self.workflow.consume_dom_fallback_events()
                    )
                    evidence = (
                        data.get("evidence")
                        if isinstance(data, dict)
                        else None
                    )
                    if isinstance(evidence, dict):
                        evidence["stateMachineStages"] = used_stages
                        evidence["modelFallbackStageUsed"] = (
                            model_enabled
                        )
                        if fallback_events:
                            evidence["adaptiveLocatorUsed"] = True
                            evidence["adaptiveLocatorEvents"] = (
                                fallback_events
                            )
                result = _success(data)
                self.completed[tool_name] = result
                return self._logged_result(
                    tool_name=tool_name,
                    arguments=arguments,
                    result=result,
                    started_at=started_at,
                    source=stage,
                )

            assert last_error is not None
            diagnosis = (
                self.workflow.diagnose_dom_failure(
                    step_name=tool_name,
                    error_message=self._safe_message(last_error),
                )
                if self.workflow is not None
                else {}
            )
            failure = _failure(
                (
                    type(last_error).__name__
                    if isinstance(last_error, ZiniaoError)
                    else "UNEXPECTED_ERROR"
                ),
                self._safe_message(last_error),
            )
            failure["error"]["stateMachineStages"] = used_stages
            failure["error"]["adaptiveLocatorOrder"] = [
                "persisted_recipe",
                "accessibility_tree",
                "dom_candidates",
            ]
            if diagnosis:
                failure["error"]["modelDiagnosis"] = diagnosis
            if self.workflow is not None:
                fallback_events = (
                    self.workflow.consume_dom_fallback_events()
                )
                if fallback_events:
                    failure["error"]["adaptiveLocatorEvents"] = (
                        fallback_events
                    )
            return self._logged_result(
                tool_name=tool_name,
                arguments=arguments,
                result=self._terminate_after_failure(
                    tool_name,
                    failure,
                ),
                started_at=started_at,
                source=(
                    used_stages[-1]
                    if used_stages
                    else "rejected"
                ),
            )
        except Exception as error:
            return self._logged_result(
                tool_name=tool_name,
                arguments=arguments,
                result=self._terminate_after_failure(
                    tool_name,
                    _failure(
                        (
                            type(error).__name__
                            if isinstance(error, ZiniaoError)
                            else "UNEXPECTED_ERROR"
                        ),
                        self._safe_message(error),
                    ),
                ),
                started_at=started_at,
            )

    def _terminate_after_failure(
        self,
        tool_name: str,
        failure: dict[str, Any],
    ) -> dict[str, Any]:
        self.terminal_failure = failure
        self.failed_tool_name = tool_name
        self.should_exit = True
        self.review_required = (
            "ziniao_send_greeting" in self.completed
            or tool_name in WRITE_STEP_TOOLS
        )
        self.task_fatal = tool_name == "ziniao_connect"
        self.skip_creator = (
            not self.review_required and not self.task_fatal
        )
        self.failure_stage = (
            "TASK_FATAL"
            if self.task_fatal
            else (
                "REVIEW_REQUIRED"
                if self.review_required
                else "SKIPPED"
            )
        )
        failure["reviewRequired"] = self.review_required
        failure["skipCreator"] = self.skip_creator
        failure["taskFatal"] = self.task_fatal
        failure["failureStage"] = self.failure_stage
        try:
            self.close()
        except Exception:
            pass
        return failure

    def _connect(self, arguments: dict[str, Any]) -> dict[str, Any]:
        requested_store_id = str(arguments.get("storeId") or "").strip()
        if (
            self.expected_store_id
            and requested_store_id != self.expected_store_id
        ):
            raise ZiniaoWorkflowError(
                "模型提交的 storeId 与任务固定店铺不一致。"
            )
        settings = ZiniaoSettings.from_env()
        connection = connect_reusable_store(
            settings,
            requested_store_id,
        )
        session = connection.session
        store = connection.store
        self.browser_connection = connection
        self.session = session
        assert session.driver is not None
        self.workflow = CreatorContactWorkflow(
            session.driver,
            timeout_seconds=60,
            dom_fallback=DeepSeekDomFallback.from_env(
                task_id=self.task_id,
                session_id=self.session_id,
            ),
        )
        return {
            "connected": True,
            "store": store.to_public_dict(),
            "coreVersion": session.started.core_version if session.started else "",
            "debuggingPort": (
                session.started.debugging_port if session.started else 0
            ),
            "connectionMode": connection.connection_mode,
            "browserSessionCachePersisted": (
                connection.cache_persisted
            ),
            "currentUrl": session.driver.current_url,
            "title": session.driver.title,
        }

    def _open_find_creators(self) -> dict[str, Any]:
        return self._require_workflow().open_find_creators().to_dict()

    def _search_creator(self, arguments: dict[str, Any]) -> dict[str, Any]:
        creator = self._remember_creator(arguments.get("creator"))
        return self._require_workflow().search_creator(creator).to_dict()

    def _open_creator_detail(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        creator = self._remember_creator(arguments.get("creator"))
        return self._require_workflow().open_creator_detail(creator).to_dict()

    def _open_message_panel(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        creator = self._remember_creator(arguments.get("creator"))
        return self._require_workflow().open_message_panel(creator).to_dict()

    def _open_chat_new_tab(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        creator = self._remember_creator(arguments.get("creator"))
        result = self._require_workflow().open_chat_in_new_tab(
            creator
        ).to_dict()
        discovered = str(
            result.get("evidence", {}).get("creatorId") or ""
        )
        self._remember_creator_id(discovered)
        return result

    def _recipient_arguments(
        self,
        arguments: dict[str, Any],
    ) -> tuple[str, str]:
        raw_creator_id = arguments.get("creatorId")
        if raw_creator_id is not None and str(raw_creator_id).strip():
            creator_id = self._remember_creator_id(raw_creator_id)
        elif self.creator_id is not None:
            creator_id = self.creator_id
        else:
            raise ZiniaoWorkflowError(
                "尚未从 Cooperation Chat URL 获取 creator_id。"
            )
        return (
            self._remember_creator(arguments.get("creator")),
            creator_id,
        )

    def _verify_chat_recipient(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        creator, creator_id = self._recipient_arguments(arguments)
        return self._require_workflow().verify_chat_recipient(
            creator,
            creator_id,
        ).to_dict()

    def _send_greeting(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        creator, creator_id = self._recipient_arguments(arguments)
        greeting = str(arguments.get("greetingMessage") or "")
        expected_sha256 = str(
            arguments.get("greetingSha256") or ""
        )
        if self.expected_greeting:
            if expected_sha256 != self.expected_greeting_sha256:
                raise ZiniaoWorkflowError(
                    "模型提交的招呼语哈希与任务固定快照不一致。"
                )
            greeting = self.expected_greeting
        return self._require_workflow().send_greeting(
            creator,
            creator_id,
            greeting,
            confirm_send=arguments.get("confirmSendGreeting") is True,
            expected_sha256=expected_sha256,
        ).to_dict()

    def _open_target_collaboration(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        creator, creator_id = self._recipient_arguments(arguments)
        return self._require_workflow().open_target_collaboration(
            creator,
            creator_id,
        ).to_dict()

    def _open_other_invitation_dialog(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        creator, creator_id = self._recipient_arguments(arguments)
        return self._require_workflow().open_other_invitation_dialog(
            creator,
            creator_id,
        ).to_dict()

    def _select_invitation(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        creator, creator_id = self._recipient_arguments(arguments)
        invitation_name = self._remember_invitation_name(
            arguments.get("invitationName")
        )
        invitation_group_id = self._remember_invitation_group_id(
            arguments.get("invitationGroupId")
        )
        result = self._require_workflow().select_invitation(
            creator,
            creator_id,
            invitation_name,
            invitation_group_id=invitation_group_id,
        ).to_dict()
        evidence = result.setdefault("evidence", {})
        evidence["invitationGroupId"] = invitation_group_id
        evidence["invitationGroupIdBound"] = True
        return result

    def _send_selected_invitation(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        creator, creator_id = self._recipient_arguments(arguments)
        invitation_name = self._remember_invitation_name(
            arguments.get("invitationName")
        )
        invitation_group_id = self._remember_invitation_group_id(
            arguments.get("invitationGroupId")
        )
        result = self._require_workflow().send_selected_invitation(
            creator,
            creator_id,
            invitation_name,
            invitation_group_id=invitation_group_id,
            confirm_send=arguments.get("confirmSendInvitation") is True,
        ).to_dict()
        self._verify_result_invitation_group_id(
            result,
            invitation_group_id,
            stage="邀请发送结果",
        )
        evidence = result.get("evidence", {})
        cleanup_verified = (
            evidence.get("creatorDetailTargetGone") is True
            and evidence.get("creatorTabsClosed") is True
            and evidence.get("searchTabKept") is True
            and evidence.get("returnedToFindCreators") is True
            and evidence.get("findCreatorsSearchReady") is True
        )
        completion_verified = (
            evidence.get("invitationCompleted") is True
            and (
                evidence.get("invitationButtonClicked") is True
                or evidence.get("alreadySent") is True
            )
        )
        if not (cleanup_verified and completion_verified):
            raise ZiniaoWorkflowError(
                "邀请阶段未取得按钮点击/幂等完成、标签清理或"
                "下一位达人可复用搜索页的证据。"
            )
        return result

    def _disconnect(self) -> dict[str, Any]:
        self.close()
        return {
            "disconnected": True,
            "storeBrowserPreserved": True,
            "webdriverEndpointPreserved": True,
        }

    def close(self) -> None:
        connection = self.browser_connection
        session = self.session
        self.browser_connection = None
        self.session = None
        self.workflow = None
        if connection is not None:
            connection.close()
        elif session is not None:
            session.close()


class ZiniaoContactMcpServer:
    def __init__(self) -> None:
        self.state = ContactAutomationState()

    @staticmethod
    def _tool_result(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(payload, ensure_ascii=False),
                }
            ],
            "structuredContent": payload,
            **({} if payload["success"] else {"isError": True}),
        }

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        request_id = message.get("id")
        method = message.get("method")
        if method == "initialize":
            params = message.get("params") or {}
            result = {
                "protocolVersion": params.get(
                    "protocolVersion",
                    "2025-03-26",
                ),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION,
                },
            }
        elif method in {
            "notifications/initialized",
            "notifications/cancelled",
        }:
            return None
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = message.get("params") or {}
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {}
            payload = self.state.call(str(params.get("name") or ""), arguments)
            result = self._tool_result(payload)
        else:
            if request_id is None:
                return None
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}",
                },
            }
        if request_id is None:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def run(self) -> None:
        try:
            for raw_line in sys.stdin.buffer:
                try:
                    message = json.loads(raw_line.decode("utf-8"))
                    response = self.handle(message)
                except Exception as error:
                    response = {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {
                            "code": -32603,
                            "message": f"Internal error: {type(error).__name__}",
                        },
                    }
                if response is not None:
                    print(
                        json.dumps(response, ensure_ascii=False),
                        flush=True,
                    )
                if self.state.should_exit:
                    break
        finally:
            try:
                self.state.close()
            except Exception:
                pass


def main() -> int:
    print(f"{SERVER_NAME} running on stdio", file=sys.stderr, flush=True)
    ZiniaoContactMcpServer().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
