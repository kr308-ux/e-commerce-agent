"""Launch one detached, self-terminating email sender process."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.utils import timezone

from shared.logger import project_log_root
from shared.processes import new_process_group_kwargs

from mailing.models import EmailDelivery, EmailSendingService
from mailing.services.retry import retry_available_queryset
from mailing.services.runtime import get_service_state, resume_service
from mailing.services.worker_runtime import email_worker_endpoint_ready


def email_sender_log_path() -> Path:
    day = timezone.localdate().isoformat()
    return (
        project_log_root(settings.PROJECT_ROOT)
        / day
        / "regular"
        / "email-sender.log"
    )


def launch_email_sender(*, resume_paused: bool = True) -> bool:
    """Start the sender when pending work exists and it is not already active."""
    database_name = str(connection.settings_dict.get("NAME") or "")
    if database_name.startswith("file:memorydb_"):
        # A detached child cannot share Django's in-process test database.
        return False
    if not (
        EmailDelivery.objects.filter(
            status=EmailDelivery.Status.PENDING
        ).exists()
        or retry_available_queryset().exists()
    ):
        return False
    service = resume_service() if resume_paused else get_service_state()
    if service.status == EmailSendingService.Status.PAUSED:
        return False
    if email_worker_endpoint_ready(
        settings.EMAIL_WORKER_HOST,
        settings.EMAIL_WORKER_PORT,
    ):
        # The persistent worker will observe the durable queue.
        return True
    if service.status in {
        EmailSendingService.Status.RUNNING,
        EmailSendingService.Status.STOPPING,
    }:
        return False

    log_path = email_sender_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(settings.BASE_DIR / "manage.py"),
        "send_creator_emails",
    ]
    popen_kwargs: dict[str, object] = {
        "cwd": str(settings.BASE_DIR),
        "stdin": subprocess.DEVNULL,
    }
    popen_kwargs.update(new_process_group_kwargs())

    with log_path.open("ab") as log_file:
        popen_kwargs["stdout"] = log_file
        popen_kwargs["stderr"] = subprocess.STDOUT
        subprocess.Popen(command, **popen_kwargs)
    return True
