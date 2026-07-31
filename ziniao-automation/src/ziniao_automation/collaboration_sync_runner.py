"""Non-interactive CLI for read-only target-collaboration synchronization."""

from __future__ import annotations

import argparse
import json
from typing import Any

from .actions.collaboration_sync import TargetCollaborationSync
from .browser_connection import connect_reusable_store
from .config import ZiniaoSettings
from .errors import (
    ZiniaoApiError,
    ZiniaoConfigurationError,
    ZiniaoConnectionError,
    ZiniaoDriverError,
    ZiniaoStoreSelectionError,
    ZiniaoWorkflowError,
)


def _result(
    *,
    success: bool,
    store_id: str,
    options: list[dict[str, Any]] | None = None,
    error_code: str = "",
    error_message: str = "",
) -> dict[str, Any]:
    return {
        "success": success,
        "storeId": store_id,
        "options": options or [],
        "errorCode": error_code,
        "errorMessage": error_message,
    }


def sync_store(store_id: str) -> dict[str, Any]:
    """Connect one exact store and synchronize without prompting for secrets."""
    normalized_store_id = str(store_id or "").strip()
    if not normalized_store_id:
        return _result(
            success=False,
            store_id="",
            error_code="STORE_ID_REQUIRED",
            error_message="必须提供非空 storeId。",
        )

    try:
        settings = ZiniaoSettings.from_env()
        with connect_reusable_store(
            settings,
            normalized_store_id,
            allow_start=False,
        ) as connection:
            session = connection.session
            if session.driver is None:
                raise ZiniaoConnectionError(
                    "已打开的店铺浏览器存在，但 Selenium 驱动未连接。"
                )
            options = TargetCollaborationSync(session.driver).sync()
        return _result(
            success=True,
            store_id=normalized_store_id,
            options=options,
        )
    except ZiniaoConfigurationError as error:
        return _result(
            success=False,
            store_id=normalized_store_id,
            error_code="CONFIGURATION_ERROR",
            error_message=str(error),
        )
    except ZiniaoStoreSelectionError as error:
        return _result(
            success=False,
            store_id=normalized_store_id,
            error_code="STORE_SELECTION_ERROR",
            error_message=str(error),
        )
    except ZiniaoConnectionError as error:
        message = str(error)
        browser_busy = (
            "目标店铺正由另一个自动化进程操作" in message
            or "等待浏览器控制权超时" in message
        )
        return _result(
            success=False,
            store_id=normalized_store_id,
            error_code=(
                "BROWSER_BUSY"
                if browser_busy
                else "CONNECTION_ERROR"
            ),
            error_message=message,
        )
    except (
        ZiniaoApiError,
        ZiniaoDriverError,
    ) as error:
        return _result(
            success=False,
            store_id=normalized_store_id,
            error_code="CONNECTION_ERROR",
            error_message=str(error),
        )
    except ZiniaoWorkflowError as error:
        return _result(
            success=False,
            store_id=normalized_store_id,
            error_code="SYNC_ERROR",
            error_message=str(error),
        )
    except Exception as error:
        return _result(
            success=False,
            store_id=normalized_store_id,
            error_code="UNEXPECTED_ERROR",
            error_message=(
                "同步发生未预期错误："
                f"{type(error).__name__}"
            ),
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="只读同步 TikTok Shop 进行中的定向合作",
    )
    parser.add_argument("--store-id", required=True)
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出标准 JSON；为兼容服务端，当前始终输出 JSON",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    result = sync_store(arguments.store_id)
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        flush=True,
    )
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
