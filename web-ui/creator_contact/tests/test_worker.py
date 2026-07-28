from io import StringIO
from unittest.mock import patch

from django.core.management import call_command

from creator_contact.models import CreatorContactTask
from creator_contact.services.worker_runtime import (
    WORKER_CLAIMED_STEP,
    WORKER_WAITING_STEP,
)

from .base import CreatorContactTestCase


class CreatorContactWorkerTests(CreatorContactTestCase):
    def test_worker_claims_pending_task_and_uses_runner(self):
        task = self.create_contact_task(top_n=1)
        output = StringIO()

        with patch(
            "creator_contact.management.commands."
            "run_creator_contact_worker.CreatorContactRunner"
        ) as runner_class:
            runner_class.return_value.run.return_value = task
            call_command(
                "run_creator_contact_worker",
                once=True,
                stdout=output,
            )

        task.refresh_from_db()
        self.assertEqual(task.status, CreatorContactTask.Status.RUNNING)
        runner_class.assert_called_once()
        self.assertIn(str(task.pk), output.getvalue())

    def test_worker_runs_exact_task_claimed_by_django_server(self):
        task = self.create_contact_task(top_n=1)
        task.status = CreatorContactTask.Status.RUNNING
        task.current_step = WORKER_WAITING_STEP
        task.save()
        output = StringIO()

        with patch(
            "creator_contact.management.commands."
            "run_creator_contact_worker.CreatorContactRunner"
        ) as runner_class:
            runner_class.return_value.run.return_value = task
            call_command(
                "run_creator_contact_worker",
                once=True,
                task_id=str(task.pk),
                claimed_by_server=True,
                stdout=output,
            )

        runner_class.assert_called_once()
        task.refresh_from_db()
        self.assertEqual(
            task.current_step,
            WORKER_CLAIMED_STEP,
        )

    def test_server_mode_only_claims_tasks_started_from_django(self):
        waiting = self.create_contact_task(top_n=1)
        waiting.status = CreatorContactTask.Status.RUNNING
        waiting.current_step = WORKER_WAITING_STEP
        waiting.save()
        untouched = self.create_contact_task(top_n=1)
        output = StringIO()

        with patch(
            "creator_contact.management.commands."
            "run_creator_contact_worker.WorkerHealthServer"
        ) as health_server, patch(
            "creator_contact.management.commands."
            "run_creator_contact_worker.worker_endpoint_ready",
            return_value=False,
        ), patch(
            "creator_contact.management.commands."
            "run_creator_contact_worker.CreatorContactRunner"
        ) as runner_class:
            runner_class.return_value.run.return_value = waiting
            call_command(
                "run_creator_contact_worker",
                once=True,
                server_mode=True,
                control_port=16899,
                stdout=output,
            )

        waiting.refresh_from_db()
        untouched.refresh_from_db()
        self.assertEqual(waiting.current_step, WORKER_CLAIMED_STEP)
        self.assertEqual(untouched.status, CreatorContactTask.Status.PENDING)
        health_server.return_value.start.assert_called_once()
        health_server.return_value.close.assert_called_once()
