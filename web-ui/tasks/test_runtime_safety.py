from __future__ import annotations

from datetime import date
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
from unittest.mock import Mock, patch

from django.db import close_old_connections, transaction
from django.core.management import call_command
from django.test import (
    SimpleTestCase,
    TransactionTestCase,
    override_settings,
)
from django.utils import timezone

from mailing.models import EmailSendingService
from shared.file_lock import SingleInstanceLock
from shared.db import retry_locked_database_operation
from shared.logger import cleanup_old_logs
from shared.processes import new_process_group_kwargs
from shared import runtime_commands, runtime_paths
from tasks.models import ImportTask


class LogCleanupTests(SimpleTestCase):
    def test_retains_exactly_fourteen_calendar_days(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in (
                "2026-07-17",
                "2026-07-18",
                "2026-07-31",
                "not-a-log-day",
            ):
                (root / name).mkdir()

            removed = cleanup_old_logs(
                root,
                retention_days=14,
                today=date(2026, 7, 31),
            )

            self.assertEqual(
                [path.name for path in removed],
                ["2026-07-17"],
            )
            self.assertTrue((root / "2026-07-18").is_dir())
            self.assertTrue((root / "not-a-log-day").is_dir())

    def test_dry_run_and_symlink_are_safe(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "2020-01-01"
            old.mkdir()
            target = root / "target"
            target.mkdir()
            link = root / "2019-01-01"
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError:
                link = None

            removed = cleanup_old_logs(
                root,
                retention_days=14,
                today=date(2026, 7, 31),
                dry_run=True,
            )

            self.assertEqual([path.name for path in removed], ["2020-01-01"])
            self.assertTrue(old.exists())
            if link is not None:
                self.assertTrue(link.exists())

    def test_rejects_invalid_retention(self) -> None:
        with self.assertRaisesRegex(ValueError, "必须大于 0"):
            cleanup_old_logs(".", retention_days=0)


class RuntimePrimitiveTests(SimpleTestCase):
    def test_frozen_paths_split_writable_home_and_resources(self) -> None:
        with patch.object(
            runtime_paths.sys,
            "frozen",
            True,
            create=True,
        ), patch.object(
            runtime_paths.sys,
            "executable",
            "/release/EcommerceAgent/EcommerceAgent.exe",
        ), patch.object(
            runtime_paths.sys,
            "_MEIPASS",
            "/release/EcommerceAgent/_internal",
            create=True,
        ):
            self.assertEqual(
                runtime_paths.app_home(),
                Path("/release/EcommerceAgent"),
            )
            self.assertEqual(
                runtime_paths.resource_root(),
                Path("/release/EcommerceAgent/_internal"),
            )

    def test_frozen_commands_dispatch_through_allowlisted_flags(self) -> None:
        with patch(
            "shared.runtime_commands.is_frozen",
            return_value=True,
        ), patch.object(
            runtime_commands.sys,
            "executable",
            r"C:\EcommerceAgent\EcommerceAgent.exe",
        ):
            self.assertEqual(
                runtime_commands.django_command("check"),
                [
                    r"C:\EcommerceAgent\EcommerceAgent.exe",
                    "--internal-manage",
                    "check",
                ],
            )
            self.assertEqual(
                runtime_commands.automation_command(
                    "ziniao_automation.contact_task_runner",
                    "--task-id",
                    "safe-id",
                ),
                [
                    r"C:\EcommerceAgent\EcommerceAgent.exe",
                    "--internal-contact-task",
                    "--task-id",
                    "safe-id",
                ],
            )
            with self.assertRaisesRegex(ValueError, "不支持"):
                runtime_commands.automation_command(
                    "ziniao_automation.not_allowlisted"
                )

    def test_single_instance_lock_rejects_second_owner(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.lock"
            first = SingleInstanceLock(path)
            second = SingleInstanceLock(path)
            self.assertTrue(first.acquire())
            try:
                self.assertFalse(second.acquire())
            finally:
                first.release()
            self.assertTrue(second.acquire())
            second.release()

    def test_process_group_configuration_matches_platform(self) -> None:
        options = new_process_group_kwargs()
        self.assertEqual(len(options), 1)
        self.assertTrue(
            "start_new_session" in options
            or "creationflags" in options
        )

    def test_supervisor_manages_all_five_services(self) -> None:
        processes = []

        def make_process(*_args, **_kwargs):
            process = Mock()
            process.pid = 1000 + len(processes)
            process.poll.return_value = None
            processes.append(process)
            return process

        with TemporaryDirectory() as directory, override_settings(
            PROJECT_ROOT=Path(directory),
            LOG_RETENTION_DAYS=14,
        ), patch(
            "tasks.management.commands.run_runtime_supervisor."
            "subprocess.Popen",
            side_effect=make_process,
        ) as popen, patch(
            "tasks.management.commands.run_runtime_supervisor."
            "terminate_process_tree",
        ) as terminate:
            call_command(
                "run_runtime_supervisor",
                skip_browser_prepare=True,
                once=True,
                stdout=StringIO(),
                stderr=StringIO(),
            )

        self.assertEqual(popen.call_count, 5)
        commands = [call.args[0] for call in popen.call_args_list]
        self.assertTrue(any("run_email_worker" in command for command in commands))
        self.assertTrue(any("--noreload" in command for command in commands))
        self.assertEqual(terminate.call_count, 5)


class SQLiteConcurrencyTests(TransactionTestCase):
    def test_short_cross_table_write_waits_for_existing_writer(self) -> None:
        import_task = ImportTask.objects.create(
            file_name="concurrency.xlsx",
            file_sha256="c" * 64,
            snapshot_date=timezone.localdate(),
            confirmed_at=timezone.now(),
        )
        service = EmailSendingService.objects.create(id=1)
        writer_started = threading.Event()
        errors: list[Exception] = []

        def hold_contact_write() -> None:
            close_old_connections()
            try:
                with transaction.atomic():
                    ImportTask.objects.filter(pk=import_task.pk).update(
                        current_step="持有短写事务"
                    )
                    writer_started.set()
                    time.sleep(0.15)
            except Exception as error:
                errors.append(error)
            finally:
                close_old_connections()

        def write_email_state() -> None:
            close_old_connections()
            writer_started.wait(timeout=2)
            try:
                retry_locked_database_operation(
                    lambda: EmailSendingService.objects.filter(
                        pk=service.pk
                    ).update(sent_count=1),
                    attempts=8,
                    initial_delay=0.02,
                )
            except Exception as error:
                errors.append(error)
            finally:
                close_old_connections()

        contact_thread = threading.Thread(target=hold_contact_write)
        email_thread = threading.Thread(target=write_email_state)
        contact_thread.start()
        email_thread.start()
        contact_thread.join(timeout=3)
        email_thread.join(timeout=3)

        self.assertEqual(errors, [])
        service.refresh_from_db()
        self.assertEqual(service.sent_count, 1)
