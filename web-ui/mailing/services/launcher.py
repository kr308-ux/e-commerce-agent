"""Launch one detached, self-terminating email sender process."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.utils import timezone

from shared.logger import project_log_root

from mailing.models import EmailDelivery, EmailSendingService
from mailing.services.retry import retry_available_queryset
from mailing.services.runtime import get_service_state


def email_sender_log_path() -> Path:
    day = timezone.localdate().isoformat()
    return (
        project_log_root(settings.PROJECT_ROOT)
        / day
        / "regular"
        / "email-sender.log"
    )


def launch_email_sender() -> bool:
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
    service = get_service_state()
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
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    elif hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    with log_path.open("ab") as log_file:
        popen_kwargs["stdout"] = log_file
        popen_kwargs["stderr"] = subprocess.STDOUT
        subprocess.Popen(command, **popen_kwargs)
    return True
