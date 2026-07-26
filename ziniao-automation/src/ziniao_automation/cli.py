"""Local CLI for listing and probing authorized Ziniao stores."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time

from .actions.creator_contact import (
    APPROVED_GREETING_MESSAGE,
    CreatorContactWorkflow,
)
from .actions.visible_demo import run_visible_connection_demo
from .client import ZiniaoClient
from .config import ZiniaoCredentials, ZiniaoSettings
from .errors import (
    ZiniaoError,
    ZiniaoStoreSelectionError,
    ZiniaoWorkflowError,
)
from .models import StoreInfo
from .process import ZiniaoProcessManager
from .session import SeleniumStoreSession


def _credentials(prompt: bool) -> ZiniaoCredentials:
    if not prompt:
        return ZiniaoCredentials.from_env()
    company = os.getenv("ZINIAO_COMPANY", "").strip() or input("紫鸟企业名称：").strip()
    username = (
        os.getenv("ZINIAO_USERNAME", "").strip()
        or input("紫鸟企业用户名：").strip()
    )
    password = os.getenv("ZINIAO_PASSWORD", "") or getpass.getpass("紫鸟企业密码：")
    return ZiniaoCredentials(
        company=company,
        username=username,
        password=password,
    )


def _select_store(
    stores: list[StoreInfo],
    *,
    store_id: str | None,
    store_name: str | None,
) -> StoreInfo:
    candidates = stores
    if store_id:
        candidates = [store for store in candidates if store.browser_id == store_id]
    if store_name:
        candidates = [
            store for store in candidates if store.browser_name == store_name
        ]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ZiniaoStoreSelectionError("未找到符合条件的已授权店铺。")
    public_stores = json.dumps(
        [store.to_public_dict() for store in candidates],
        ensure_ascii=False,
    )
    raise ZiniaoStoreSelectionError(
        f"当前有多个可用店铺，请使用 --store-id 或 --store-name 明确选择："
        f"{public_stores}"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="紫鸟店铺 Selenium 连接工具")
    parser.add_argument(
        "--prompt",
        action="store_true",
        help="缺少环境变量时在本地终端安全提示输入凭据",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("list", "probe", "watch", "contact-creator"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument(
            "--restart-client",
            action="store_true",
            help="正常终止现有紫鸟主进程并以 WebDriver 模式重启",
        )
        if command in {"probe", "watch", "contact-creator"}:
            subparser.add_argument("--store-id")
            subparser.add_argument("--store-name")
        if command == "watch":
            subparser.add_argument(
                "--duration",
                type=int,
                default=30,
                choices=range(1, 301),
                metavar="SECONDS",
                help="可见演示停留秒数，范围 1-300，默认 30",
            )
            subparser.add_argument(
                "--keep-open",
                action="store_true",
                help="持续保持店铺和 Selenium 会话，直到进程被主动停止",
            )
        if command == "contact-creator":
            subparser.add_argument(
                "--creator",
                default="@delaneykreusel",
                help="目标达人用户名",
            )
            subparser.add_argument(
                "--creator-id",
                help="聊天 URL 中必须精确匹配的达人 creator_id",
            )
            subparser.add_argument(
                "--invitation-name",
                default="金色拉链+短裤13",
                help="要精确选择的定向合作邀请名称",
            )
            subparser.add_argument(
                "--greeting-message",
                default=APPROVED_GREETING_MESSAGE,
                help="要原样发送的任务招呼语（最多 2000 字符）",
            )
            subparser.add_argument(
                "--through-step",
                type=int,
                choices=range(1, 13),
                default=5,
                metavar="STEP",
                help="执行并验收到指定步骤；开放第 1-12 步，默认 5",
            )
            subparser.add_argument(
                "--confirm-send-greeting",
                action="store_true",
                help="显式授权第 7 步发送任务快照中的招呼语",
            )
            subparser.add_argument(
                "--confirm-send-invitation",
                action="store_true",
                help="显式授权第 11 步点击最终邀请按钮",
            )
            subparser.add_argument(
                "--confirm-send-card",
                action="store_true",
                help="显式授权第 12 步发送右侧目标合作卡片",
            )
            subparser.add_argument(
                "--keep-open",
                action="store_true",
                help="验收后持续保持可见店铺窗口，按 Ctrl+C 安全关闭",
            )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "contact-creator":
            if (
                arguments.through_step >= 7
                and not arguments.confirm_send_greeting
            ):
                raise ZiniaoWorkflowError(
                    "第 7 步及以后必须显式提供 "
                    "--confirm-send-greeting。"
                )
            if (
                arguments.through_step >= 11
                and not arguments.confirm_send_invitation
            ):
                raise ZiniaoWorkflowError(
                    "第 11 步必须显式提供 "
                    "--confirm-send-invitation。"
                )
            if (
                arguments.through_step >= 12
                and not arguments.confirm_send_card
            ):
                raise ZiniaoWorkflowError(
                    "第 12 步必须显式提供 --confirm-send-card。"
                )
        settings = ZiniaoSettings.from_env(
            credentials=_credentials(arguments.prompt)
        )
        manager = ZiniaoProcessManager(settings)
        manager.ensure_started(restart=arguments.restart_client)
        client = ZiniaoClient(settings)
        client.update_core()
        stores = client.list_stores()
        if arguments.command == "list":
            print(
                json.dumps(
                    {
                        "success": True,
                        "count": len(stores),
                        "stores": [store.to_public_dict() for store in stores],
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        store = _select_store(
            stores,
            store_id=arguments.store_id,
            store_name=arguments.store_name,
        )
        with SeleniumStoreSession(client, settings, store) as selenium_session:
            assert selenium_session.driver is not None
            assert selenium_session.started is not None
            result = {
                "success": True,
                "connected": True,
                "store": store.to_public_dict(),
                "coreVersion": selenium_session.started.core_version,
                "currentUrl": selenium_session.driver.current_url,
                "title": selenium_session.driver.title,
            }
            if arguments.command == "watch":
                duration = None if arguments.keep_open else arguments.duration
                print(
                    json.dumps(
                        {
                            "event": "watching",
                            "message": (
                                "紫鸟店铺窗口已打开，将持续保持到主动停止。"
                                if arguments.keep_open
                                else "紫鸟店铺窗口已打开，正在执行可见只读演示。"
                            ),
                            "durationSeconds": duration,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                run_visible_connection_demo(
                    selenium_session.driver,
                    duration_seconds=duration,
                )
                result["visibleDemo"] = True
            elif arguments.command == "contact-creator":
                workflow = CreatorContactWorkflow(
                    selenium_session.driver,
                    timeout_seconds=60,
                )
                steps = [workflow.open_find_creators().to_dict()]
                if arguments.through_step >= 2:
                    steps.append(
                        workflow.search_creator(arguments.creator).to_dict()
                    )
                if arguments.through_step >= 3:
                    steps.append(
                        workflow.open_creator_detail(
                            arguments.creator
                        ).to_dict()
                    )
                if arguments.through_step >= 4:
                    steps.append(
                        workflow.open_message_panel(
                            arguments.creator
                        ).to_dict()
                    )
                if arguments.through_step >= 5:
                    chat_result = workflow.open_chat_in_new_tab(
                        arguments.creator
                    ).to_dict()
                    steps.append(chat_result)
                    discovered_creator_id = str(
                        chat_result.get("evidence", {}).get(
                            "creatorId"
                        )
                        or ""
                    )
                    if (
                        arguments.creator_id
                        and arguments.creator_id
                        != discovered_creator_id
                    ):
                        raise ZiniaoWorkflowError(
                            "聊天 URL creator_id 与预期值不一致。"
                        )
                    arguments.creator_id = discovered_creator_id
                if arguments.through_step >= 6:
                    steps.append(
                        workflow.verify_chat_recipient(
                            arguments.creator,
                            arguments.creator_id,
                        ).to_dict()
                    )
                if arguments.through_step >= 7:
                    steps.append(
                        workflow.send_greeting(
                            arguments.creator,
                            arguments.creator_id,
                            arguments.greeting_message,
                            confirm_send=(
                                arguments.confirm_send_greeting
                            ),
                        ).to_dict()
                    )
                if arguments.through_step >= 8:
                    steps.append(
                        workflow.open_target_collaboration(
                            arguments.creator,
                            arguments.creator_id,
                        ).to_dict()
                    )
                if arguments.through_step >= 9:
                    steps.append(
                        workflow.open_other_invitation_dialog(
                            arguments.creator,
                            arguments.creator_id,
                        ).to_dict()
                    )
                if arguments.through_step >= 10:
                    steps.append(
                        workflow.select_invitation(
                            arguments.creator,
                            arguments.creator_id,
                            arguments.invitation_name,
                        ).to_dict()
                    )
                if arguments.through_step >= 11:
                    invitation_result = (
                        workflow.send_selected_invitation(
                            arguments.creator,
                            arguments.creator_id,
                            arguments.invitation_name,
                            confirm_send=(
                                arguments.confirm_send_invitation
                            ),
                        ).to_dict()
                    )
                    steps.append(invitation_result)
                if arguments.through_step >= 12:
                    steps.append(
                        workflow.send_collaboration_card(
                            arguments.creator,
                            arguments.creator_id,
                            arguments.invitation_name,
                            str(
                                invitation_result.get(
                                    "evidence", {}
                                ).get("invitationId")
                                or ""
                            ),
                            confirm_send=(
                                arguments.confirm_send_card
                            ),
                        ).to_dict()
                    )
                result.update(
                    {
                        "workflow": "contact_creator",
                        "creator": arguments.creator,
                        "creatorId": arguments.creator_id,
                        "invitationName": arguments.invitation_name,
                        "throughStep": arguments.through_step,
                        "steps": steps,
                    }
                )
                print(json.dumps(result, ensure_ascii=False), flush=True)
                if arguments.keep_open:
                    print(
                        json.dumps(
                            {
                                "event": "holding",
                                "message": (
                                    f"第 {arguments.through_step} 步已验收，"
                                    "窗口将保持打开；"
                                    "按 Ctrl+C 安全关闭。"
                                ),
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    while True:
                        time.sleep(1)
                return 0
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except ZiniaoError as error:
        print(
            json.dumps(
                {"success": False, "error": str(error)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        print(
            json.dumps(
                {
                    "success": True,
                    "message": "已停止可见调试并关闭 Selenium 店铺会话。",
                },
                ensure_ascii=False,
            )
        )
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
