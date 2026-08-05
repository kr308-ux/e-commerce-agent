from __future__ import annotations

from django.test import TestCase
from django.utils import timezone
from unittest.mock import patch

from creator_contact.models import CreatorContactTarget, CreatorContactTask
from creator_contact.services.worker_recovery import (
    recover_interrupted_contact_tasks,
)
from creator_contact.services.worker_runtime import (
    WORKER_CLAIMED_STEP,
    WORKER_WAITING_STEP,
)
from tasks.models import Creator, ImportTask


class ContactWorkerRecoveryTests(TestCase):
    def setUp(self) -> None:
        self.import_task = ImportTask.objects.create(
            file_name="recovery.xlsx",
            file_sha256="r" * 64,
            status=ImportTask.Status.SUCCESS,
            snapshot_date=timezone.localdate(),
            confirmed_at=timezone.now(),
        )

    def make_task(self, *, target_status: str) -> CreatorContactTask:
        creator = Creator.objects.create(
            creator_id=f"creator-{Creator.objects.count()}",
        )
        task = CreatorContactTask.objects.create(
            store_id="recovery-store",
            source_import_task=self.import_task,
            selection_method=CreatorContactTask.SelectionMethod.MANUAL,
            top_n=1,
            greeting_snapshot="Hello",
            invitation_name_snapshot="Plan",
            invitation_id_snapshot="plan-id",
            status=CreatorContactTask.Status.RUNNING,
            current_step=WORKER_CLAIMED_STEP,
        )
        CreatorContactTarget.objects.create(
            task=task,
            creator=creator,
            rank=1,
            creator_handle_snapshot=creator.creator_id,
            status=target_status,
        )
        return task

    def test_claimed_task_without_active_target_is_requeued(self) -> None:
        task = self.make_task(
            target_status=CreatorContactTarget.Status.PENDING
        )

        requeued, reviews = recover_interrupted_contact_tasks()

        self.assertEqual((requeued, reviews), (1, 0))
        task.refresh_from_db()
        self.assertEqual(task.current_step, WORKER_WAITING_STEP)

    def test_active_target_requires_review_and_is_not_retried(self) -> None:
        task = self.make_task(
            target_status=CreatorContactTarget.Status.RUNNING
        )
        task.active_process_id = 43210
        task.save(update_fields=["active_process_id", "updated_at"])

        with patch(
            "creator_contact.services.worker_recovery."
            "terminate_process_id_tree"
        ) as terminate:
            requeued, reviews = recover_interrupted_contact_tasks()

        self.assertEqual((requeued, reviews), (0, 1))
        terminate.assert_called_once_with(43210)
        task.refresh_from_db()
        target = task.targets.get()
        self.assertEqual(task.status, CreatorContactTask.Status.FAILED)
        self.assertEqual(
            target.status,
            CreatorContactTarget.Status.REVIEW_REQUIRED,
        )
        self.assertEqual(
            task.email_dispatch_status,
            CreatorContactTask.EmailDispatchStatus.PENDING,
        )
        self.assertIsNone(task.active_process_id)
