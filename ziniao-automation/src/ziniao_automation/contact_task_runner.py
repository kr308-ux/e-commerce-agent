"""Run the fixed creator-contact state machine without an LLM orchestrator."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from shared.logger import JsonlAuditLogger, project_log_root

from .actions.creator_contact import APPROVED_GREETING_MESSAGE
from .errors import ZiniaoWorkflowError
from .mcp_server import ContactAutomationState


DEFAULT_INVITATION_NAME = "金色拉链+短裤13"
DEFAULT_FALLBACK_MODEL = "deepseek/deepseek-v4-flash"


def normalize_greeting_message(value: object) -> str:
    return (
        str(value or "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .strip()
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="以确定性状态机执行紫鸟联系达人流程"
    )
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--creator", required=True)
    parser.add_argument("--creator-id")
    parser.add_argument(
        "--invitation-name",
        default=DEFAULT_INVITATION_NAME,
    )
    parser.add_argument("--invitation-group-id")
    parser.add_argument(
        "--greeting-message",
        default=APPROVED_GREETING_MESSAGE,
    )
    parser.add_argument(
        "--through-step",
        type=int,
        choices=range(1, 12),
        default=5,
        metavar="STEP",
    )
    parser.add_argument("--confirm-send-greeting", action="store_true")
    parser.add_argument("--confirm-send-invitation", action="store_true")
    parser.add_argument("--task-id", default="ziniao-contact-debug")
    parser.add_argument(
        "--model",
        default=os.getenv(
            "DOM_FALLBACK_MODEL",
            DEFAULT_FALLBACK_MODEL,
        ),
        help="仅在 DOM/定位失败时使用的 DeepSeek 模型",
    )
    return parser


def _validate(arguments: argparse.Namespace) -> None:
    if arguments.through_step >= 7 and not arguments.confirm_send_greeting:
        raise ZiniaoWorkflowError(
            "第 7 步及以后必须提供 --confirm-send-greeting。"
        )
    if arguments.through_step >= 11 and not arguments.confirm_send_invitation:
        raise ZiniaoWorkflowError(
            "第 11 步必须提供 --confirm-send-invitation。"
        )
    if (
        arguments.through_step >= 10
        and not str(arguments.invitation_group_id or "").strip().isdigit()
    ):
        raise ZiniaoWorkflowError(
            "第 10 步及以后必须提供有效 --invitation-group-id。"
        )
    greeting = normalize_greeting_message(arguments.greeting_message)
    if not greeting or len(greeting) > 2000:
        raise ZiniaoWorkflowError("招呼语必须为 1-2000 个字符。")


def _tool_event(
    *,
    session_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "type": "tool_use",
        "sessionID": session_id,
        "part": {
            "tool": f"ziniao-contact_{tool_name}",
            "state": {
                # The state-machine call completed even when its business
                # payload reports FAILED. This keeps the root error parseable.
                "status": "completed",
                "input": tool_input,
                "output": json.dumps(payload, ensure_ascii=False),
            },
        },
    }


def _calls(arguments: argparse.Namespace) -> list[tuple[str, dict[str, Any]]]:
    creator = str(arguments.creator or "").strip().lstrip("@")
    common = {
        "taskId": arguments.task_id,
        "creator": creator,
    }
    calls: list[tuple[str, dict[str, Any]]] = [
        (
            "ziniao_connect",
            {
                "taskId": arguments.task_id,
                "stepId": "connect",
                "storeId": arguments.store_id,
            },
        ),
        (
            "ziniao_open_find_creators",
            {
                "taskId": arguments.task_id,
                "stepId": "open-find-creators",
            },
        ),
    ]
    workflow_calls: list[tuple[str, dict[str, Any]]] = [
        (
            "ziniao_search_creator",
            {**common, "stepId": "search-creator"},
        ),
        (
            "ziniao_open_creator_detail",
            {**common, "stepId": "open-creator-detail"},
        ),
        (
            "ziniao_open_message_panel",
            {**common, "stepId": "open-message-panel"},
        ),
        (
            "ziniao_open_chat_new_tab",
            {**common, "stepId": "open-chat-new-tab"},
        ),
        (
            "ziniao_verify_chat_recipient",
            {**common, "stepId": "verify-chat-recipient"},
        ),
        (
            "ziniao_send_greeting",
            {
                **common,
                "stepId": "send-greeting",
                "greetingMessage": normalize_greeting_message(
                    arguments.greeting_message
                ),
                "greetingSha256": hashlib.sha256(
                    normalize_greeting_message(
                        arguments.greeting_message
                    ).encode("utf-8")
                ).hexdigest(),
                "confirmSendGreeting": True,
            },
        ),
        (
            "ziniao_open_target_collaboration",
            {**common, "stepId": "open-target-collaboration"},
        ),
        (
            "ziniao_open_other_invitation_dialog",
            {**common, "stepId": "open-other-invitation-dialog"},
        ),
        (
            "ziniao_select_invitation",
            {
                **common,
                "stepId": "select-invitation",
                "invitationName": arguments.invitation_name,
                "invitationGroupId": arguments.invitation_group_id,
            },
        ),
        (
            "ziniao_send_selected_invitation",
            {
                **common,
                "stepId": "send-selected-invitation",
                "invitationName": arguments.invitation_name,
                "invitationGroupId": arguments.invitation_group_id,
                "confirmSendInvitation": True,
            },
        ),
    ]
    calls.extend(workflow_calls[: max(0, arguments.through_step - 1)])
    if arguments.creator_id:
        for _tool_name, tool_input in calls:
            if "creator" in tool_input:
                tool_input["creatorId"] = arguments.creator_id
    calls.append(
        (
            "ziniao_disconnect",
            {
                "taskId": arguments.task_id,
                "stepId": "disconnect",
            },
        )
    )
    return calls


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    session_id = f"direct_{uuid.uuid4().hex}"
    project_root = Path(__file__).resolve().parents[3]
    audit_logger = JsonlAuditLogger(
        root=project_log_root(project_root),
        category="regular",
        component="creator-contact",
        task_id=arguments.task_id,
        session_id=session_id,
    )
    run_started_at = time.perf_counter()
    audit_logger.write(
        "task_started",
        status="STARTED",
        operation="contact_task_runner",
        input_content=vars(arguments),
        metadata={"runner": "deterministic", "pid": os.getpid()},
    )
    try:
        _validate(arguments)
    except ZiniaoWorkflowError as error:
        audit_logger.write(
            "task_finished",
            status="FAILED",
            operation="contact_task_runner",
            input_content=vars(arguments),
            error={
                "type": type(error).__name__,
                "message": str(error),
            },
            duration_ms=(time.perf_counter() - run_started_at) * 1000,
        )
        print(
            json.dumps(
                {
                    "success": False,
                    "errorCode": type(error).__name__,
                    "errorMessage": str(error),
                },
                ensure_ascii=False,
            )
        )
        return 2

    greeting = normalize_greeting_message(arguments.greeting_message)
    os.environ.update(
        {
            "ZINIAO_EXPECTED_STORE_ID": arguments.store_id,
            "ZINIAO_EXPECTED_CREATOR": (
                str(arguments.creator or "").strip().lstrip("@")
            ),
            "ZINIAO_EXPECTED_INVITATION_NAME": arguments.invitation_name,
            "ZINIAO_EXPECTED_GREETING_B64": base64.b64encode(
                greeting.encode("utf-8")
            ).decode("ascii"),
            "ZINIAO_EXPECTED_GREETING_SHA256": hashlib.sha256(
                greeting.encode("utf-8")
            ).hexdigest(),
            "DOM_FALLBACK_MODEL": arguments.model,
            "ZINIAO_TASK_ID": arguments.task_id,
            "ZINIAO_RUN_SESSION_ID": session_id,
        }
    )
    if arguments.invitation_group_id:
        os.environ["ZINIAO_INVITATION_GROUP_ID"] = (
            arguments.invitation_group_id
        )

    state = ContactAutomationState()
    success = True
    error_code = ""
    error_message = ""
    try:
        for tool_name, tool_input in _calls(arguments):
            payload = state.call(tool_name, tool_input)
            print(
                json.dumps(
                    _tool_event(
                        session_id=session_id,
                        tool_name=tool_name,
                        tool_input=tool_input,
                        payload=payload,
                    ),
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if payload.get("success") is not True:
                success = False
                error = payload.get("error") or {}
                error_code = str(error.get("code") or "")
                error_message = str(error.get("userMessage") or "")
                break
    finally:
        state.close()

    final_payload = {
        "success": success,
        "status": "SUCCESS" if success else "FAILED",
        "creator": str(arguments.creator or "").strip().lstrip("@"),
        "invitationName": arguments.invitation_name,
        "sessionID": session_id,
        "errorCode": error_code,
        "errorMessage": error_message,
        "skipCreator": state.skip_creator,
        "reviewRequired": state.review_required,
        "taskFatal": state.task_fatal,
        "failureStage": state.failure_stage,
    }
    print(
        json.dumps(
            {
                "type": "text",
                "sessionID": session_id,
                "part": {
                    "text": json.dumps(
                        final_payload,
                        ensure_ascii=False,
                    )
                },
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    audit_logger.write(
        "task_finished",
        status="SUCCESS" if success else "FAILED",
        operation="contact_task_runner",
        input_content=vars(arguments),
        output_content=final_payload,
        duration_ms=(time.perf_counter() - run_started_at) * 1000,
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
