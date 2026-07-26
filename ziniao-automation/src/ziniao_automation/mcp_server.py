"""Minimal stdio MCP server for the validated Ziniao contact workflow."""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Callable

from .actions.creator_contact import CreatorContactWorkflow
from .client import ZiniaoClient
from .config import ZiniaoSettings
from .errors import ZiniaoError, ZiniaoStoreSelectionError, ZiniaoWorkflowError
from .models import StoreInfo
from .process import ZiniaoProcessManager
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
    "ziniao_send_collaboration_card",
    "ziniao_disconnect",
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
        "description": "连接指定的已授权紫鸟店铺并附加可见 Selenium。",
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
        "description": "点击联盟，再点击寻找达人，并验收达人搜索列表页。",
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
        "name": "ziniao_send_collaboration_card",
        "title": "发送定向合作卡片",
        "description": (
            "刷新复核定向合作数量与邀请 ID，点击右侧目标卡片一次并验证计划卡片发送成功。"
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
                "invitationId": {
                    "type": "string",
                    "pattern": "^[0-9]+$",
                },
                "invitationGroupId": {
                    "type": "string",
                    "pattern": "^[0-9]+$",
                },
                "confirmSendCard": {
                    "type": "boolean",
                    "const": True,
                },
            },
            required=[
                "creator",
                "invitationName",
                "invitationGroupId",
                "confirmSendCard",
            ],
        ),
        "annotations": {"readOnlyHint": False, "destructiveHint": True},
    },
    {
        "name": "ziniao_disconnect",
        "title": "断开紫鸟会话",
        "description": "安全关闭 Selenium 会话并调用紫鸟 stopBrowser。",
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
        self.workflow: CreatorContactWorkflow | None = None
        self.creator: str | None = None
        self.creator_id: str | None = None
        self.invitation_name: str | None = None
        self.invitation_id: str | None = None
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
        if tool_name not in TOOL_ORDER:
            return _failure("UNKNOWN_TOOL", f"未知工具：{tool_name}")
        if tool_name in self.completed:
            return self.completed[tool_name]
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
                "ziniao_send_collaboration_card": lambda: (
                    self._send_collaboration_card(arguments)
                ),
                "ziniao_disconnect": self._disconnect,
            }
            result = _success(handlers[tool_name]())
            self.completed[tool_name] = result
            return result
        except ZiniaoError as error:
            return _failure(type(error).__name__, self._safe_message(error))
        except Exception as error:
            return _failure("UNEXPECTED_ERROR", self._safe_message(error))

    def _connect(self, arguments: dict[str, Any]) -> dict[str, Any]:
        settings = ZiniaoSettings.from_env()
        manager = ZiniaoProcessManager(settings)
        manager.ensure_started(restart=False)
        client = ZiniaoClient(settings)
        client.update_core()
        store = self._select_store(
            client.list_stores(),
            str(arguments.get("storeId") or ""),
        )
        session = SeleniumStoreSession(client, settings, store)
        try:
            session.connect()
        except Exception:
            session.close()
            raise
        self.session = session
        assert session.driver is not None
        self.workflow = CreatorContactWorkflow(
            session.driver,
            timeout_seconds=60,
        )
        return {
            "connected": True,
            "store": store.to_public_dict(),
            "coreVersion": session.started.core_version if session.started else "",
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
        return self._require_workflow().send_greeting(
            creator,
            creator_id,
            str(arguments.get("greetingMessage") or ""),
            confirm_send=arguments.get("confirmSendGreeting") is True,
            expected_sha256=str(
                arguments.get("greetingSha256") or ""
            ),
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
        if evidence.get("skipCreator") is True:
            self.invitation_id = None
            return result
        invitation_id = str(
            evidence.get("invitationId") or ""
        )
        if not invitation_id.isdigit():
            raise ZiniaoWorkflowError(
                "邀请创建结果未返回有效 invitation_id。"
            )
        self.invitation_id = invitation_id
        return result

    def _send_collaboration_card(
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
        requested_id = str(arguments.get("invitationId") or "").strip()
        if (
            requested_id
            and self.invitation_id
            and requested_id != self.invitation_id
        ):
            raise ZiniaoWorkflowError(
                "合作卡片 invitationId 与已创建邀请不一致。"
            )
        invitation_id = requested_id or str(self.invitation_id or "")
        if not invitation_id.isdigit():
            raise ZiniaoWorkflowError(
                "发送合作卡片前缺少有效 invitationId。"
            )
        result = self._require_workflow().send_collaboration_card(
            creator,
            creator_id,
            invitation_name,
            invitation_id or None,
            invitation_group_id=invitation_group_id,
            confirm_send=arguments.get("confirmSendCard") is True,
        ).to_dict()
        self._verify_result_invitation_group_id(
            result,
            invitation_group_id,
            stage="合作卡片发送结果",
        )
        return result

    def _disconnect(self) -> dict[str, Any]:
        self.close()
        return {"disconnected": True}

    def close(self) -> None:
        session = self.session
        self.session = None
        self.workflow = None
        if session is not None:
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
