"""Create tasks, expose progress, and render persisted browser results."""

from django.conf import settings
from django.db.models import Count
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .forms import CreatorAcquisitionTaskForm
from .models import CreatorAcquisitionTask, RelatedCreator


@require_http_methods(["GET", "POST"])
def dashboard(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = CreatorAcquisitionTaskForm(request.POST)
        if form.is_valid():
            task = form.save(commit=False)
            task.model_name = settings.DEEPSEEK_MODEL
            task.save()
            return redirect("tasks:task-detail", task_id=task.id)
    else:
        form = CreatorAcquisitionTaskForm(initial={"product_limit": 10})

    tasks = CreatorAcquisitionTask.objects.annotate(
        product_count=Count("products", distinct=True),
        creator_count=Count("products__related_creators", distinct=True),
    )
    today = timezone.localdate()
    context = {
        "form": form,
        "recent_tasks": tasks[:10],
        "today_count": tasks.filter(created_at__date=today).count(),
        "running_count": tasks.filter(status=CreatorAcquisitionTask.Status.RUNNING).count(),
        "pending_count": tasks.filter(status=CreatorAcquisitionTask.Status.PENDING).count(),
        "success_count": tasks.filter(status=CreatorAcquisitionTask.Status.SUCCESS).count(),
        "task_count": tasks.count(),
        "now": timezone.localtime(),
    }
    return render(request, "tasks/dashboard.html", context)


def task_detail(request: HttpRequest, task_id) -> HttpResponse:
    task = get_object_or_404(
        CreatorAcquisitionTask.objects.prefetch_related("steps", "products__exports"),
        id=task_id,
    )
    products = list(task.products.all())
    creators = (
        RelatedCreator.objects
        .filter(product__task=task)
        .select_related("product")
        .order_by("product_id", "-recent_30_day_revenue")[:200]
    )
    context = {
        "task": task,
        "products": products,
        "creators": creators,
        "product_count": len(products),
        "creator_count": RelatedCreator.objects.filter(product__task=task).count(),
        "export_count": sum(product.exports.count() for product in products),
        "task_count": CreatorAcquisitionTask.objects.count(),
    }
    return render(request, "tasks/task_detail.html", context)


def task_status(request: HttpRequest, task_id) -> JsonResponse:
    task = get_object_or_404(CreatorAcquisitionTask, id=task_id)
    products = task.products.all()
    return JsonResponse(
        {
            "id": str(task.id),
            "status": task.status,
            "statusLabel": task.get_status_display(),
            "progress": task.progress,
            "currentStep": task.current_step,
            "errorCode": task.error_code or None,
            "errorMessage": task.error_message or None,
            "productCount": products.count(),
            "creatorCount": RelatedCreator.objects.filter(product__task=task).count(),
            "exportCount": sum(product.exports.count() for product in products),
            "updatedAt": task.updated_at.isoformat(),
            "detailUrl": reverse("tasks:task-detail", kwargs={"task_id": task.id}),
        }
    )


@require_POST
def retry_task(request: HttpRequest, task_id) -> HttpResponse:
    task = get_object_or_404(CreatorAcquisitionTask, id=task_id)
    if task.status in {
        CreatorAcquisitionTask.Status.WAITING_CONFIRMATION,
        CreatorAcquisitionTask.Status.FAILED,
    }:
        task.status = CreatorAcquisitionTask.Status.PENDING
        task.progress = 0
        task.current_step = "等待 Agent Worker 重试"
        task.error_code = ""
        task.error_message = ""
        task.finished_at = None
        task.save()
    return redirect("tasks:task-detail", task_id=task.id)
