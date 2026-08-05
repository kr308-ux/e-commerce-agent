"""Run accepted-creator collaboration-card delivery in WebDriver mode."""

from __future__ import annotations

import argparse
import json

from .actions.accepted_collaboration import AcceptedCollaborationWorkflow
from .browser_connection import connect_reusable_store
from .config import ZiniaoSettings
from .errors import ZiniaoError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store-id", required=True)
    creators = parser.add_mutually_exclusive_group(required=True)
    creators.add_argument("--creator")
    creators.add_argument(
        "--creators-json",
        help="JSON 数组；整批达人在同一个项目详情页和 WebDriver 会话中处理。",
    )
    parser.add_argument("--invitation-name", required=True)
    parser.add_argument("--invitation-group-id", required=True)
    parser.add_argument("--confirm-send-card", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    return parser


def _creators_from_arguments(arguments: argparse.Namespace) -> list[str]:
    raw_creators: object
    if arguments.creators_json:
        try:
            raw_creators = json.loads(arguments.creators_json)
        except json.JSONDecodeError as error:
            raise ZiniaoError("--creators-json 必须是合法 JSON 数组。") from error
        if not isinstance(raw_creators, list):
            raise ZiniaoError("--creators-json 必须是 JSON 数组。")
    else:
        raw_creators = [arguments.creator]
    creators: list[str] = []
    seen: set[str] = set()
    for raw_creator in raw_creators:
        creator = str(raw_creator or "").strip()
        normalized = creator.lstrip("@").strip().casefold()
        if not normalized:
            raise ZiniaoError("达人用户名不能为空。")
        if normalized in seen:
            continue
        seen.add(normalized)
        creators.append(f"@{creator.lstrip('@').strip()}")
    if not creators:
        raise ZiniaoError("至少需要一个达人用户名。")
    return creators


def _creator_error(creator: str, error: Exception) -> dict[str, object]:
    return {
        "success": False,
        "creator": creator,
        "errorCode": (
            type(error).__name__
            if isinstance(error, ZiniaoError)
            else "UNEXPECTED_ERROR"
        ),
        "errorMessage": str(error),
        "steps": [],
    }


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if not arguments.verify_only and not arguments.confirm_send_card:
        print(
            json.dumps(
                {
                    "success": False,
                    "errorCode": "CARD_SEND_NOT_CONFIRMED",
                    "errorMessage": "必须显式确认发送定向合作卡片。",
                },
                ensure_ascii=False,
            )
        )
        return 2
    try:
        creators = _creators_from_arguments(arguments)
        settings = ZiniaoSettings.from_env()
        with connect_reusable_store(
            settings,
            arguments.store_id,
        ) as connection:
            session = connection.session
            assert session.driver is not None
            workflow = AcceptedCollaborationWorkflow(session.driver)
            opened = workflow.open_project_accepted_creators(
                arguments.invitation_name,
                arguments.invitation_group_id,
            )
            results: list[dict[str, object]] = []
            for creator in creators:
                try:
                    if arguments.verify_only:
                        membership = workflow.verify_creator_membership(creator)
                        results.append(
                            {
                                "success": True,
                                "creator": creator,
                                "creatorId": "",
                                "invitationId": "",
                                "invitationGroupId": (
                                    arguments.invitation_group_id
                                ),
                                "invitationCompleted": True,
                                "acceptedCreatorsPageVisible": True,
                                "projectMembershipVerified": True,
                                "invitationVerificationSource": (
                                    "accepted_creator_list"
                                ),
                                "finalInviteButtonClicked": False,
                                "cardSent": False,
                                "finalSendVerified": False,
                                "steps": [membership.to_dict()],
                            }
                        )
                        continue
                    membership = workflow.verify_creator_membership(
                        creator
                    )
                    chat = workflow.open_creator_chat(creator)
                    sent = workflow.send_collaboration_card(
                        creator,
                        arguments.invitation_name,
                        arguments.invitation_group_id,
                        confirm_send=True,
                    )
                    if sent.evidence.get("cardSkipped") is True:
                        results.append(
                            {
                                "success": False,
                                "creator": creator,
                                "creatorId": "",
                                "invitationId": "",
                                "invitationGroupId": (
                                    arguments.invitation_group_id
                                ),
                                "cardSent": False,
                                "targetPlanMessageVerified": False,
                                "finalSendVerified": False,
                                "reviewRequired": True,
                                "errorCode": "CARD_NOT_FOUND_AFTER_RETRY",
                                "errorMessage": (
                                    "多次关闭重开聊天后仍未识别到定向合作卡片，"
                                    "需人工复核。"
                                ),
                                "acceptedCreatorsPageVisible": True,
                                "projectMembershipVerified": True,
                                "recipientVerified": True,
                                "cardRetryCount": sent.evidence.get(
                                    "cardRetryCount"
                                ),
                                "steps": [
                                    membership.to_dict(),
                                    chat.to_dict(),
                                    sent.to_dict(),
                                ],
                            }
                        )
                        continue
                    closed = workflow.close_chat_drawer()
                    evidence = sent.evidence
                    results.append(
                        {
                            "success": True,
                            "creator": creator,
                            "creatorId": "",
                            "invitationId": evidence["invitationId"],
                            "invitationGroupId": evidence[
                                "invitationGroupId"
                            ],
                            "cardSent": evidence["cardSent"],
                            "targetPlanMessageVerified": evidence[
                                "targetPlanMessageVerified"
                            ],
                            "planCardServerIds": evidence[
                                "planCardServerIds"
                            ],
                            "targetPlanFlightStatus": evidence[
                                "targetPlanFlightStatus"
                            ],
                            "finalSendVerified": evidence[
                                "finalSendVerified"
                            ],
                            "acceptedCreatorsPageVisible": True,
                            "projectMembershipVerified": True,
                            "recipientVerified": True,
                            "chatDrawerClosed": closed.evidence[
                                "chatDrawerClosed"
                            ],
                            "cdpClickRecoveryCount": (
                                workflow._cdp_click_recovery_count
                            ),
                            "steps": [
                                membership.to_dict(),
                                chat.to_dict(),
                                sent.to_dict(),
                                closed.to_dict(),
                            ],
                        }
                    )
                except Exception as error:
                    results.append(_creator_error(creator, error))

            closed_project_tabs = workflow.close_project_accepted_creators_tab(
                arguments.invitation_group_id,
            )

            shared = {
                "success": all(
                    result.get("success") is True for result in results
                ),
                "invitationGroupId": arguments.invitation_group_id,
                "acceptedCreatorsPageVisible": True,
                "projectOpenedOnce": True,
                "projectOpenCount": 1,
                "connectionMode": connection.connection_mode,
                "browserSessionCachePersisted": (
                    connection.cache_persisted
                ),
                "steps": [opened.to_dict()],
                "results": results,
                **closed_project_tabs,
            }
            if len(creators) == 1 and not arguments.creators_json:
                single = {
                    **results[0],
                    "projectOpenedOnce": True,
                    "projectOpenCount": 1,
                    "connectionMode": connection.connection_mode,
                    "browserSessionCachePersisted": (
                        connection.cache_persisted
                    ),
                    "steps": [
                        opened.to_dict(),
                        *(results[0].get("steps") or []),
                    ],
                }
                print(json.dumps(single, ensure_ascii=False))
                return 0 if single.get("success") is True else 1
            print(json.dumps(shared, ensure_ascii=False))
            return 0 if shared["success"] else 1
    except ZiniaoError as error:
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
        return 1
    except Exception as error:
        print(
            json.dumps(
                {
                    "success": False,
                    "errorCode": "UNEXPECTED_ERROR",
                    "errorMessage": str(error),
                },
                ensure_ascii=False,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
