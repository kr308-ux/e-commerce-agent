"""Create, inspect, and monitor persistent creator-contact tasks."""

from __future__ import annotations

import subprocess
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from tasks.models import ImportTask

from .forms import CreatorContactTaskForm, GreetingTemplateForm
from .models import (
    CollaborationSyncJob,
    CreatorContactTarget,
    CreatorContactTask,
    DirectedCollaborationOption,
    GreetingTemplate,
)
from .services.candidate_selector import (
    candidate_payload,
    freeze_task_targets,
    select_candidates,
)
from .services.browser_status import browser_readiness
from .services.worker_runtime import (
    WORKER_WAITING_STEP,
    wait_for_worker_endpoint,
    worker_endpoint_ready,
)


def _store_id(request: HttpRequest) -> str:
    return str(
        request.POST.get("store_id")
        or request.GET.get("store_id")
        or getattr(settings, "ZINIAO_CONTACT_STORE_ID", "")
        or ""
    ).strip()


def _dashboard_context(
    request: HttpRequest,
    *,
    form: CreatorContactTaskForm | None = None,
    greeting_form: GreetingTemplateForm | None = None,
) -> dict[str, object]:
    store_id = _store_id(request)
    source_tasks = (
        ImportTask.objects
        .filter(
            status__in=[
                ImportTask.Status.SUCCESS,
                ImportTask.Status.PARTIAL_SUCCESS,
            ]
        )
        .annotate(
            creator_count=Count(
                "imported_creators",
                distinct=True,
            ),
        )
        .order_by("-created_at")
    )
    import_batches = source_tasks
    selected_import_task = None
    import_task_id = request.GET.get("import_task_id") or request.POST.get(
        "source_import_task"
    )
    if import_task_id:
        try:
            selected_import_task = import_batches.get(pk=import_task_id)
        except (ImportTask.DoesNotExist, TypeError, ValueError):
            selected_import_task = None
    elif not request.POST:
        selected_import_task = import_batches.first()

    candidate_preview: list[object] = []
    candidate_preview_payload: list[dict[str, object]] = []
    excluded_count = 0
    selection_method = str(
        request.POST.get("selection_method")
        or request.GET.get("selection_method")
        or CreatorContactTask.SelectionMethod.SALES
    )
    try:
        sales_window_days = int(
            request.POST.get("sales_window_days")
            or request.GET.get("sales_window_days")
            or 30
        )
    except (TypeError, ValueError):
        sales_window_days = 30
    manually_selected_ids = list(
        request.POST.getlist("selected_creator_ids")
    )
    if selected_import_task is not None and store_id:
        try:
            top_n = int(
                request.GET.get("top_n")
                or request.POST.get("top_n")
                or 3
            )
        except (TypeError, ValueError):
            top_n = 3
        selection_kwargs: dict[str, object] = {}
        if selection_method == CreatorContactTask.SelectionMethod.SALES:
            selection_kwargs["top_n"] = max(1, min(top_n, 100))
            selection_kwargs["sales_window_days"] = sales_window_days
        elif (
            selection_method
            == CreatorContactTask.SelectionMethod.CREATOR_ID
        ):
            selection_kwargs["top_n"] = 50
        try:
            selection = select_candidates(
                import_task=selected_import_task,
                store_id=store_id,
                selection_method=selection_method,
                **selection_kwargs,
            )
        except ValidationError:
            selection = None
        if selection is not None:
            candidate_preview = list(selection.creators)
            candidate_preview_payload = [
                candidate_payload(creator, rank=rank)
                for rank, creator in enumerate(selection.creators, start=1)
            ]
            excluded_count = selection.excluded_count
            manually_selected_set = set(manually_selected_ids)
            for creator in candidate_preview:
                creator.manually_selected = (
                    str(creator.pk) in manually_selected_set
                )

    if form is None:
        form = CreatorContactTaskForm(
            store_id=store_id,
            initial={
                "store_id": store_id,
                "top_n": 3,
                "source_import_task": selected_import_task,
                "selection_method": selection_method,
                "sales_window_days": sales_window_days,
            },
        )
    if greeting_form is None:
        greeting_form = GreetingTemplateForm()
    return {
        "form": form,
        "greeting_form": greeting_form,
        "greeting_templates": GreetingTemplate.objects.filter(
            is_active=True
        ),
        "collaboration_options": DirectedCollaborationOption.objects.filter(
            store_id=store_id,
            status=DirectedCollaborationOption.Status.ONGOING,
            is_active=True,
        ),
        "source_tasks": source_tasks,
        "import_batches": import_batches,
        "selected_import_task": selected_import_task,
        "candidate_preview": candidate_preview,
        "candidate_preview_payload": candidate_preview_payload,
        "excluded_count": excluded_count,
        "selection_method": selection_method,
        "sales_window_days": sales_window_days,
        "manually_selected_count": len(manually_selected_ids),
        "latest_contact_tasks": (
            CreatorContactTask.objects
            .select_related("source_import_task")
            .prefetch_related("targets")
            .order_by("-created_at")[:10]
        ),
        "store_id": store_id,
        "browser_status": browser_readiness(store_id),
        "sync_status": (
            CollaborationSyncJob.objects
            .filter(store_id=store_id)
            .order_by("-created_at")
            .first()
        ),
        "task_count": ImportTask.objects.count(),
    }


@require_GET
def browser_status(request: HttpRequest) -> JsonResponse:
    return JsonResponse(browser_readiness(_store_id(request)))


@require_http_methods(["GET", "POST"])
def dashboard(request: HttpRequest) -> HttpResponse:
    if request.method == "GET":
        return render(
            request,
            "creator_contact/dashboard.html",
            _dashboard_context(request),
        )

    action = str(request.POST.get("action") or "create_task")
    if action == "save_greeting":
        existing_template = GreetingTemplate.objects.filter(
            name=str(request.POST.get("name") or "").strip()
        ).first()
        greeting_form = GreetingTemplateForm(
            request.POST,
            instance=existing_template,
        )
        if greeting_form.is_valid():
            with transaction.atomic():
                if greeting_form.cleaned_data.get("is_default"):
                    GreetingTemplate.objects.filter(
                        is_default=True
                    ).update(is_default=False)
                greeting_form.save()
            return redirect("creator_contact:dashboard")
        context = _dashboard_context(
            request,
            greeting_form=greeting_form,
        )
        return render(
            request,
            "creator_contact/dashboard.html",
            context,
            status=400,
        )

    if action != "create_task":
        return JsonResponse(
            {"success": False, "error": "不支持的操作。"},
            status=400,
        )

    store_id = _store_id(request)
    form = CreatorContactTaskForm(
        request.POST,
        store_id=store_id,
    )
    if form.is_valid():
        try:
            with transaction.atomic():
                task = form.save(commit=False)
                greeting = form.cleaned_data["greeting_template"]
                invitation = form.cleaned_data["collaboration_option"]
                task.greeting_snapshot = greeting.content
                task.invitation_name_snapshot = invitation.name
                task.invitation_id_snapshot = (
                    invitation.external_invitation_id
                )
                task.model_name = settings.DOM_FALLBACK_MODEL
                task.confirm_send_greeting = True
                task.confirm_send_invitation = True
                task.confirm_send_card = True
                selection_label = task.get_selection_method_display()
                task.name = f"{selection_label}联系 {task.top_n} 位达人"
                task.save()
                freeze_task_targets(
                    task,
                    selection=form.candidate_selection,
                )
        except ValidationError as error:
            form.add_error("source_import_task", error)
        else:
            return redirect(
                "creator_contact:task_detail",
                task_id=task.pk,
            )
    context = _dashboard_context(request, form=form)
    return render(
        request,
        "creator_contact/dashboard.html",
        context,
        status=400,
    )


@require_GET
def candidates(request: HttpRequest) -> JsonResponse:
    store_id = _store_id(request)
    import_task_id = request.GET.get("import_task_id")
    if not store_id or not import_task_id:
        return JsonResponse(
            {
                "success": False,
                "error": "store_id 和 import_task_id 均为必填项。",
            },
            status=400,
        )
    import_task = get_object_or_404(
        ImportTask.objects.filter(
            status__in=[
                ImportTask.Status.SUCCESS,
                ImportTask.Status.PARTIAL_SUCCESS,
            ]
        ),
        pk=import_task_id,
    )
    try:
        sales_window_days = int(
            request.GET.get("sales_window_days") or 30
        )
    except (TypeError, ValueError):
        return JsonResponse(
            {"success": False, "error": "销售额周期无效。"},
            status=400,
        )
    selection_method = str(
        request.GET.get("selection_method")
        or CreatorContactTask.SelectionMethod.SALES
    )
    selection_kwargs: dict[str, object] = {}
    if selection_method == CreatorContactTask.SelectionMethod.SALES:
        try:
            top_n = max(
                1,
                min(int(request.GET.get("top_n") or 3), 100),
            )
        except (TypeError, ValueError):
            return JsonResponse(
                {"success": False, "error": "top_n 必须是整数。"},
                status=400,
            )
        selection_kwargs.update(
            top_n=top_n,
            sales_window_days=sales_window_days,
        )
    elif selection_method == CreatorContactTask.SelectionMethod.CREATOR_ID:
        top_n = 50
        selection_kwargs["top_n"] = top_n
    else:
        top_n = None
    try:
        selection = select_candidates(
            import_task=import_task,
            store_id=store_id,
            selection_method=selection_method,
            **selection_kwargs,
        )
    except ValidationError as error:
        return JsonResponse(
            {"success": False, "error": "; ".join(error.messages)},
            status=400,
        )
    return JsonResponse(
        {
            "success": True,
            "importTaskId": str(import_task.pk),
            "requestedCount": top_n,
            "selectionMethod": selection_method,
            "salesWindowDays": sales_window_days,
            "availableCount": selection.available_count,
            "excludedCount": selection.excluded_count,
            "unmatchedIdentifiers": selection.unmatched_identifiers,
            "creators": [
                candidate_payload(creator, rank=rank)
                for rank, creator in enumerate(
                    selection.creators,
                    start=1,
                )
            ],
        }
    )


@require_GET
def task_detail(
    request: HttpRequest,
    task_id,
) -> HttpResponse:
    contact_task = get_object_or_404(
        CreatorContactTask.objects.select_related(
            "source_import_task",
            "greeting_template",
            "collaboration_option",
        ),
        pk=task_id,
    )
    targets = contact_task.targets.select_related(
        "creator"
    ).order_by("rank")
    steps = contact_task.steps.select_related("target").order_by("sequence")
    return render(
        request,
        "creator_contact/task_detail.html",
        {
            "contact_task": contact_task,
            "targets": targets,
            "steps": steps,
            "invitation_completed_count": targets.filter(
                invitation_created=True
            ).count(),
            "task_count": ImportTask.objects.count(),
        },
    )


@require_GET
def task_status(
    request: HttpRequest,
    task_id,
) -> JsonResponse:
    contact_task = get_object_or_404(CreatorContactTask, pk=task_id)
    counts = {
        status: contact_task.targets.filter(status=status).count()
        for status in CreatorContactTarget.Status.values
    }
    return JsonResponse(
        {
            "id": str(contact_task.pk),
            "status": contact_task.status,
            "statusLabel": contact_task.get_status_display(),
            "progress": contact_task.progress,
            "currentStep": contact_task.current_step,
            "errorCode": contact_task.error_code or None,
            "errorMessage": contact_task.error_message or None,
            "targetCount": contact_task.targets.count(),
            "successCount": counts[CreatorContactTarget.Status.SUCCESS],
            "failedCount": counts[CreatorContactTarget.Status.FAILED],
            "skippedCount": counts[CreatorContactTarget.Status.SKIPPED],
            "invitationCompletedCount": contact_task.targets.filter(
                invitation_created=True
            ).count(),
            "cardSentCount": contact_task.targets.filter(
                card_sent=True,
                final_send_verified=True,
            ).count(),
            "cardPendingCount": counts[
                CreatorContactTarget.Status.INVITATION_COMPLETED
            ],
            "updatedAt": contact_task.updated_at.isoformat(),
            "detailUrl": reverse(
                "creator_contact:task_detail",
                kwargs={"task_id": contact_task.pk},
            ),
        }
    )


@require_POST
def start_task(
    request: HttpRequest,
    task_id,
) -> HttpResponse:
    """Claim one pending task and launch its worker from the Django UI."""
    with transaction.atomic():
        contact_task = get_object_or_404(
            CreatorContactTask.objects.select_for_update(),
            pk=task_id,
        )
        if contact_task.status != CreatorContactTask.Status.PENDING:
            payload = {
                "success": False,
                "error": "只有等待中的联系达人任务可以启动。",
                "status": contact_task.status,
            }
            if "application/json" in request.headers.get("Accept", ""):
                return JsonResponse(payload, status=409)
            return redirect(
                "creator_contact:task_detail",
                task_id=contact_task.pk,
            )
        contact_task.status = CreatorContactTask.Status.RUNNING
        contact_task.current_step = WORKER_WAITING_STEP
        contact_task.started_at = timezone.now()
        contact_task.finished_at = None
        contact_task.error_code = ""
        contact_task.error_message = ""
        contact_task.save()

    worker_host = settings.CREATOR_CONTACT_WORKER_HOST
    worker_port = settings.CREATOR_CONTACT_WORKER_PORT
    worker_reused = worker_endpoint_ready(worker_host, worker_port)
    command = None
    try:
        if not worker_reused:
            manage_py = Path(settings.BASE_DIR) / "manage.py"
            command = [
                settings.AUTOMATION_PYTHON_EXECUTABLE,
                str(manage_py),
                "run_creator_contact_worker",
                "--server-mode",
                "--control-host",
                worker_host,
                "--control-port",
                str(worker_port),
            ]
            subprocess.Popen(
                command,
                cwd=settings.PROJECT_ROOT,
                close_fds=True,
                start_new_session=True,
            )
            if not wait_for_worker_endpoint(
                worker_host,
                worker_port,
                timeout_seconds=(
                    settings.CREATOR_CONTACT_WORKER_START_TIMEOUT_SECONDS
                ),
            ):
                raise OSError(
                    "常驻达人联系 Worker 未在限定时间内监听固定端口。"
                )
    except OSError as error:
        contact_task.status = CreatorContactTask.Status.FAILED
        contact_task.current_step = "Django 服务端启动自动化失败"
        contact_task.error_code = "CONTACT_WORKER_START_FAILED"
        contact_task.error_message = str(error)
        contact_task.finished_at = timezone.now()
        contact_task.save()
        if "application/json" in request.headers.get("Accept", ""):
            return JsonResponse(
                {
                    "success": False,
                    "error": contact_task.error_message,
                    "status": contact_task.status,
                },
                status=500,
            )
    if "application/json" in request.headers.get("Accept", ""):
        return JsonResponse(
            {
                "success": True,
                "taskId": str(contact_task.pk),
                "status": contact_task.status,
                "workerReused": worker_reused,
                "workerEndpoint": f"http://{worker_host}:{worker_port}/health",
                "statusUrl": reverse(
                    "creator_contact:task_status",
                    kwargs={"task_id": contact_task.pk},
                ),
            },
            status=202,
        )
    return redirect(
        "creator_contact:task_detail",
        task_id=contact_task.pk,
    )


@require_POST
def cancel_task(
    request: HttpRequest,
    task_id,
) -> HttpResponse:
    """Cancel a queued task or stop its active automation subprocess."""
    with transaction.atomic():
        contact_task = get_object_or_404(
            CreatorContactTask.objects.select_for_update(),
            pk=task_id,
        )
        if contact_task.status not in {
            CreatorContactTask.Status.PENDING,
            CreatorContactTask.Status.RUNNING,
        }:
            payload = {
                "success": False,
                "error": "只有等待中或执行中的联系达人任务可以终止。",
                "status": contact_task.status,
            }
            if "application/json" in request.headers.get("Accept", ""):
                return JsonResponse(payload, status=409)
            return redirect(
                "creator_contact:task_detail",
                task_id=contact_task.pk,
            )
        now = timezone.now()
        contact_task.status = CreatorContactTask.Status.CANCELLED
        contact_task.current_step = "达人联系任务已终止"
        contact_task.error_code = "TASK_CANCELLED"
        contact_task.error_message = "达人联系任务已由用户终止。"
        contact_task.finished_at = now
        contact_task.save()
        contact_task.targets.filter(
            status__in={
                CreatorContactTarget.Status.PENDING,
                CreatorContactTarget.Status.RUNNING,
            },
        ).update(
            status=CreatorContactTarget.Status.SKIPPED,
            current_step="任务已终止，未继续执行",
            error_code="TASK_CANCELLED",
            error_message="达人联系任务已由用户终止。",
            finished_at=now,
            updated_at=now,
        )

    payload = {
        "success": True,
        "taskId": str(contact_task.pk),
        "status": contact_task.status,
        "message": "任务已取消，当前自动化进程正在退出。",
        "statusUrl": reverse(
            "creator_contact:task_status",
            kwargs={"task_id": contact_task.pk},
        ),
    }
    if "application/json" in request.headers.get("Accept", ""):
        return JsonResponse(payload, status=202)
    return redirect(
        "creator_contact:task_detail",
        task_id=contact_task.pk,
    )


@require_POST
def sync_collaborations(request: HttpRequest) -> HttpResponse:
    store_id = _store_id(request)
    if not store_id:
        return JsonResponse(
            {"success": False, "error": "必须指定紫鸟店铺。"},
            status=400,
        )
    existing = (
        CollaborationSyncJob.objects
        .filter(
            store_id=store_id,
            status__in=[
                CollaborationSyncJob.Status.PENDING,
                CollaborationSyncJob.Status.RUNNING,
            ],
        )
        .order_by("created_at")
        .first()
    )
    job = existing or CollaborationSyncJob.objects.create(
        store_id=store_id
    )
    if "application/json" in request.headers.get("Accept", ""):
        return JsonResponse(
            {
                "success": True,
                "jobId": str(job.pk),
                "status": job.status,
            },
            status=202,
        )
    return redirect("creator_contact:dashboard")
