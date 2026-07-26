"""Create, inspect, and monitor persistent creator-contact tasks."""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from tasks.models import CreatorAcquisitionTask, Product

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
        CreatorAcquisitionTask.objects
        .filter(status=CreatorAcquisitionTask.Status.SUCCESS)
        .annotate(
            product_count=Count("products", distinct=True),
            creator_count=Count(
                "products__related_creators",
                distinct=True,
            ),
        )
        .order_by("-created_at")
    )
    products = (
        Product.objects
        .filter(task__status=CreatorAcquisitionTask.Status.SUCCESS)
        .select_related("task")
        .order_by("-task__created_at", "id")
    )
    selected_product = None
    product_id = request.GET.get("product_id") or request.POST.get(
        "source_product"
    )
    if product_id:
        try:
            selected_product = products.get(pk=product_id)
        except (Product.DoesNotExist, TypeError, ValueError):
            selected_product = None

    candidate_preview: list[object] = []
    candidate_preview_payload: list[dict[str, object]] = []
    excluded_count = 0
    if selected_product is not None and store_id:
        try:
            top_n = int(
                request.GET.get("top_n")
                or request.POST.get("top_n")
                or 3
            )
        except (TypeError, ValueError):
            top_n = 3
        selection = select_candidates(
            product=selected_product,
            store_id=store_id,
            top_n=max(1, min(top_n, 100)),
        )
        candidate_preview = list(selection.creators)
        candidate_preview_payload = [
            candidate_payload(creator, rank=rank)
            for rank, creator in enumerate(selection.creators, start=1)
        ]
        excluded_count = selection.excluded_count

    if form is None:
        form = CreatorContactTaskForm(
            store_id=store_id,
            initial={
                "store_id": store_id,
                "top_n": 3,
                "source_product": selected_product,
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
        "products": products,
        "selected_product": selected_product,
        "candidate_preview": candidate_preview,
        "candidate_preview_payload": candidate_preview_payload,
        "excluded_count": excluded_count,
        "latest_contact_tasks": (
            CreatorContactTask.objects
            .select_related("source_product")
            .prefetch_related("targets")
            .order_by("-created_at")[:10]
        ),
        "store_id": store_id,
        "sync_status": (
            CollaborationSyncJob.objects
            .filter(store_id=store_id)
            .order_by("-created_at")
            .first()
        ),
        "task_count": CreatorAcquisitionTask.objects.count(),
    }


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
                task.model_name = settings.DEEPSEEK_MODEL
                task.name = f"联系 {task.top_n} 位商品关联达人"
                task.save()
                freeze_task_targets(task)
        except ValidationError as error:
            form.add_error("source_product", error)
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
    product_id = request.GET.get("product_id")
    if not store_id or not product_id:
        return JsonResponse(
            {
                "success": False,
                "error": "store_id 和 product_id 均为必填项。",
            },
            status=400,
        )
    product = get_object_or_404(
        Product.objects.filter(
            task__status=CreatorAcquisitionTask.Status.SUCCESS
        ),
        pk=product_id,
    )
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
    selection = select_candidates(
        product=product,
        store_id=store_id,
        top_n=top_n,
    )
    return JsonResponse(
        {
            "success": True,
            "productId": product.pk,
            "requestedCount": top_n,
            "availableCount": selection.available_count,
            "excludedCount": selection.excluded_count,
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
            "source_product",
            "greeting_template",
            "collaboration_option",
        ),
        pk=task_id,
    )
    targets = contact_task.targets.select_related(
        "related_creator"
    ).order_by("rank")
    steps = contact_task.steps.select_related("target").order_by("sequence")
    return render(
        request,
        "creator_contact/task_detail.html",
        {
            "contact_task": contact_task,
            "targets": targets,
            "steps": steps,
            "task_count": CreatorAcquisitionTask.objects.count(),
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
            "updatedAt": contact_task.updated_at.isoformat(),
            "detailUrl": reverse(
                "creator_contact:task_detail",
                kwargs={"task_id": contact_task.pk},
            ),
        }
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
