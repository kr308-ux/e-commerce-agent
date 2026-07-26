from io import StringIO
from unittest.mock import patch

from django.core.management import call_command

from creator_contact.models import CreatorContactTask

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
