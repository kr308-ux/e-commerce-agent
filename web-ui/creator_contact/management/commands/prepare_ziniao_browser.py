"""Start or reuse the configured Ziniao store browser before workers run."""

from __future__ import annotations

import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "预启动并验收紫鸟店铺首页，成功后保留浏览器供后续任务复用。"

    def handle(self, *args, **options):
        automation_src = (
            Path(settings.PROJECT_ROOT) / "ziniao-automation" / "src"
        )
        sys.path.insert(0, str(automation_src))

        from ziniao_automation.browser_startup import (  # noqa: PLC0415
            ensure_store_browser_ready,
            error_status,
            waiting_for_login_status,
            write_browser_status,
        )
        from ziniao_automation.browser_session_cache import (  # noqa: PLC0415
            BrowserSessionCache,
        )
        from ziniao_automation.config import ZiniaoSettings  # noqa: PLC0415

        store_id = str(settings.ZINIAO_CONTACT_STORE_ID or "").strip()
        ziniao_settings = None
        try:
            ziniao_settings = ZiniaoSettings.from_env()

            def wait_for_user_login(login_url: str) -> None:
                status_payload = waiting_for_login_status(
                    store_id,
                    login_url,
                )
                cache = BrowserSessionCache(
                    ziniao_settings.browser_session_dir,
                    probe_timeout_seconds=(
                        ziniao_settings.browser_probe_timeout_seconds
                    ),
                )
                cached = cache.resolve_live(store_id)
                if cached is not None:
                    status_payload["debuggingPort"] = cached.debugging_port
                    status_payload["devtoolsBrowserId"] = (
                        cached.devtools_browser_id
                    )
                write_browser_status(
                    ziniao_settings.browser_status_path,
                    status_payload,
                )
                self.stdout.write(
                    self.style.WARNING(
                        "检测到 TikTok Shop 登录或验证码页面。"
                        "请直接在已打开的店铺浏览器中完成验证；"
                        "当前启动流程将保持等待，验证成功后自动继续。"
                    )
                )

            result = ensure_store_browser_ready(
                ziniao_settings,
                store_id,
                on_login_required=wait_for_user_login,
            )
            write_browser_status(ziniao_settings.browser_status_path, result)
        except Exception as error:
            status_path = Path(
                getattr(
                    settings,
                    "ZINIAO_BROWSER_STATUS_PATH",
                    settings.PROJECT_ROOT
                    / "temporary"
                    / "ziniao-browser-status.json",
                )
            )
            status_payload = error_status(store_id, error)
            if ziniao_settings is not None and store_id:
                cache = BrowserSessionCache(
                    ziniao_settings.browser_session_dir,
                    probe_timeout_seconds=(
                        ziniao_settings.browser_probe_timeout_seconds
                    ),
                )
                cached = cache.resolve_live(store_id)
                if cached is not None:
                    status_payload["debuggingPort"] = cached.debugging_port
                    status_payload["devtoolsBrowserId"] = (
                        cached.devtools_browser_id
                    )
                    status_payload["connectionMode"] = "reused"
            write_browser_status(status_path, status_payload)
            raise CommandError(f"紫鸟店铺浏览器预启动失败：{error}") from error

        mode_label = (
            "复用已有浏览器"
            if result.connection_mode == "reused"
            else "启动新浏览器"
        )
        self.stdout.write(
            self.style.SUCCESS(
                "紫鸟店铺首页已就绪："
                f"{mode_label}，DevTools 端口 {result.debugging_port}，"
                f"页面 {result.current_url}"
            )
        )
