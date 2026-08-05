"""Conservative recovery for contact work interrupted with remote side effects."""

from __future__ import annotations

from django.utils import timezone

from creator_contact.models import CreatorContactTarget, CreatorContactTask
from creator_contact.services.worker_runtime import (
    WORKER_CLAIMED_STEP,
    WORKER_WAITING_STEP,
)
from shared.processes import terminate_process_id_tree


def recover_interrupted_contact_tasks() -> tuple[int, int]:
    """Return ``(requeued, review_required)`` for orphaned running tasks."""

    requeued = 0
    review_required = 0
    orphaned = CreatorContactTask.objects.filter(
        status=CreatorContactTask.Status.RUNNING,
    ).exclude(current_step=WORKER_WAITING_STEP)
    for task in orphaned.order_by("created_at"):
        if task.active_process_id:
            terminate_process_id_tree(task.active_process_id)
            task.active_process_id = None
            task.save(
                update_fields=["active_process_id", "updated_at"]
            )
        running_targets = task.targets.filter(
            status=CreatorContactTarget.Status.RUNNING
        )
        if not running_targets.exists():
            task.current_step = WORKER_WAITING_STEP
            task.save(update_fields=["current_step", "updated_at"])
            requeued += 1
            continue

        now = timezone.now()
        affected = running_targets.update(
            status=CreatorContactTarget.Status.REVIEW_REQUIRED,
            current_step="Worker 中断，远端结果需人工复核",
            error_code="CONTACT_WORKER_INTERRUPTED",
            error_message=(
                "进程在达人操作期间中断；为避免重复私信或邀请，"
                "该目标不会自动重试。"
            ),
            finished_at=now,
            updated_at=now,
        )
        has_prior_result = task.targets.exclude(
            status__in={
                CreatorContactTarget.Status.PENDING,
                CreatorContactTarget.Status.FAILED,
                CreatorContactTarget.Status.REVIEW_REQUIRED,
            }
        ).exists()
        task.status = (
            CreatorContactTask.Status.PARTIAL_SUCCESS
            if has_prior_result
            else CreatorContactTask.Status.FAILED
        )
        task.progress = 100
        task.current_step = "Worker 中断，任务已停止并等待人工复核"
        task.error_code = "CONTACT_WORKER_INTERRUPTED"
        task.error_message = (
            "当前达人可能已发生远端写入，系统未自动重试。"
        )
        task.finished_at = now
        task.email_dispatch_status = (
            CreatorContactTask.EmailDispatchStatus.PENDING
        )
        task.save()
        review_required += affected
    return requeued, review_required
