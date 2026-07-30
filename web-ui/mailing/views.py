from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    JsonResponse,
)
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import (
    require_GET,
    require_http_methods,
    require_POST,
)

from tasks.models import ImportTask

from .defaults import DEFAULT_EMAIL_SUBJECT
from .forms import EmailQueueForm, EmailTemplateForm
from .models import EmailDelivery, EmailSendingService, EmailTemplateAsset
from .services.queue import QueueResult, queue_import_creators
from .services.runtime import get_service_state, request_service_stop
from .services.template_content import (
    default_rich_html_for_editor,
    get_active_template_version,
    hydrated_rich_html_for_editor,
    render_template_content,
    rich_html_for_editor,
    save_rich_template_version,
)


@require_http_methods(["GET", "POST"])
def dashboard(request: HttpRequest) -> HttpResponse:
    queue_result: QueueResult | None = None
    template_saved = False
    active_version = get_active_template_version()
    template_form = EmailTemplateForm(
        initial={
            "subject_template": active_version.subject_template,
            "content_html": rich_html_for_editor(active_version),
        }
    )
    form = EmailQueueForm()
    if request.method == "POST":
        action = request.POST.get("action")
        if not action and "import_task" in request.POST:
            action = "queue"
        if action == "save_template":
            template_form = EmailTemplateForm(request.POST, request.FILES)
            if template_form.is_valid():
                try:
                    active_version = save_rich_template_version(
                        subject_template=template_form.cleaned_data[
                            "subject_template"
                        ],
                        content_html=template_form.cleaned_data[
                            "content_html"
                        ],
                        files=request.FILES,
                    )
                except ValidationError as error:
                    template_form.add_error(None, error)
                else:
                    template_saved = True
                    template_form = EmailTemplateForm(
                        initial={
                            "subject_template": (
                                active_version.subject_template
                            ),
                            "content_html": rich_html_for_editor(
                                active_version
                            ),
                        }
                    )
        elif action == "queue":
            form = EmailQueueForm(request.POST)
            if form.is_valid():
                queue_result = queue_import_creators(
                    import_task=form.cleaned_data["import_task"],
                    limit=form.cleaned_data["limit"],
                    retry_failed=form.cleaned_data["retry_failed"],
                    template_version=active_version,
                )
                form = EmailQueueForm()
        else:
            form = EmailQueueForm(request.POST)
            form.add_error(None, "无法识别提交的操作。")

    if template_form.is_bound:
        cleaned_html = template_form.cleaned_data.get("content_html")
        editor_html = (
            hydrated_rich_html_for_editor(cleaned_html)
            if cleaned_html
            else rich_html_for_editor(active_version)
        )
    else:
        editor_html = rich_html_for_editor(active_version)

    today = timezone.localdate()
    deliveries = EmailDelivery.objects.select_related(
        "creator",
        "template_version",
    ).order_by("-updated_at", "-id")
    email_service = get_service_state()
    context = {
        "form": form,
        "template_form": template_form,
        "template_saved": template_saved,
        "active_template_version": active_version,
        "editor_html": editor_html,
        "default_template_html": default_rich_html_for_editor(),
        "default_template_subject": DEFAULT_EMAIL_SUBJECT,
        "queue_result": queue_result,
        "deliveries": deliveries[:50],
        "pending_count": deliveries.filter(
            status=EmailDelivery.Status.PENDING
        ).count(),
        "sending_count": deliveries.filter(
            status=EmailDelivery.Status.SENDING
        ).count(),
        "sent_today_count": deliveries.filter(
            status=EmailDelivery.Status.SENT,
            sent_at__date=today,
        ).count(),
        "sent_count": deliveries.filter(
            status=EmailDelivery.Status.SENT
        ).count(),
        "failed_count": deliveries.filter(
            status=EmailDelivery.Status.FAILED
        ).count(),
        "email_configured": bool(
            settings.EMAIL_HOST_USER
            and settings.EMAIL_HOST_PASSWORD
        ),
        "email_service": email_service,
        "daily_limit": settings.CREATOR_EMAIL_DAILY_LIMIT,
        "max_attempts": settings.CREATOR_EMAIL_MAX_ATTEMPTS,
        "template_image_max_mb": (
            settings.EMAIL_TEMPLATE_IMAGE_MAX_BYTES // (1024 * 1024)
        ),
        "template_image_max_bytes": settings.EMAIL_TEMPLATE_IMAGE_MAX_BYTES,
        "template_total_image_max_mb": (
            settings.EMAIL_TEMPLATE_TOTAL_IMAGE_MAX_BYTES // (1024 * 1024)
        ),
        "template_total_image_max_bytes": (
            settings.EMAIL_TEMPLATE_TOTAL_IMAGE_MAX_BYTES
        ),
        "task_count": ImportTask.objects.count(),
    }
    return render(request, "mailing/dashboard.html", context)


@require_GET
def template_preview(request: HttpRequest) -> HttpResponse:
    version = get_active_template_version()

    def image_url_for(block: dict[str, object]) -> str:
        if block.get("legacy_asset"):
            return reverse("mailing:legacy_template_asset")
        return reverse("mailing:template_asset", args=[block["asset_id"]])

    rendered = render_template_content(
        version,
        creator_name="Creator One",
        image_url_for=image_url_for,
    )
    return HttpResponse(
        rendered.html_body,
        content_type="text/html; charset=utf-8",
        headers={
            "Content-Security-Policy": (
                "default-src 'none'; img-src 'self'; "
                "style-src 'unsafe-inline'; base-uri 'none'"
            )
        },
    )


@require_GET
def template_asset(
    request: HttpRequest,
    asset_id: int,
) -> FileResponse:
    asset = EmailTemplateAsset.objects.filter(pk=asset_id).first()
    if asset is None:
        raise Http404("邮件图片不存在。")
    try:
        file_handle = asset.file.open("rb")
    except (FileNotFoundError, OSError) as error:
        raise Http404("邮件图片文件不存在。") from error
    return FileResponse(
        file_handle,
        content_type=asset.content_type,
        as_attachment=False,
        filename=asset.original_name,
        headers={"X-Content-Type-Options": "nosniff"},
    )


@require_GET
def legacy_template_asset(request: HttpRequest) -> FileResponse:
    image_path = Path(settings.CREATOR_EMAIL_IMAGE_PATH)
    if not image_path.is_file():
        raise Http404("默认邮件图片不存在。")
    suffix = image_path.suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg"}:
        raise Http404("默认邮件图片格式无效。")
    content_type = "image/png" if suffix == ".png" else "image/jpeg"
    return FileResponse(
        image_path.open("rb"),
        content_type=content_type,
        as_attachment=False,
        filename=image_path.name,
        headers={"X-Content-Type-Options": "nosniff"},
    )


@require_GET
def service_status(request: HttpRequest) -> JsonResponse:
    service = get_service_state()
    return JsonResponse(
        {
            "status": service.status,
            "statusLabel": service.get_status_display(),
            "stopRequested": service.stop_requested,
            "currentDeliveryId": service.current_delivery_id,
            "sentCount": service.sent_count,
            "failedCount": service.failed_count,
            "startedAt": (
                service.started_at.isoformat()
                if service.started_at
                else None
            ),
            "heartbeatAt": (
                service.heartbeat_at.isoformat()
                if service.heartbeat_at
                else None
            ),
        }
    )


@require_POST
def stop_service(request: HttpRequest) -> HttpResponse:
    service = request_service_stop()
    if service.status == EmailSendingService.Status.STOPPED:
        payload = {
            "success": False,
            "error": "邮件发送服务当前未运行。",
            "status": service.status,
        }
        if "application/json" in request.headers.get("Accept", ""):
            return JsonResponse(payload, status=409)
        return redirect("mailing:dashboard")
    payload = {
        "success": True,
        "status": service.status,
        "message": "停止请求已提交；当前邮件完成后不再发送下一封。",
        "statusUrl": reverse("mailing:service_status"),
    }
    if "application/json" in request.headers.get("Accept", ""):
        return JsonResponse(payload, status=202)
    return redirect("mailing:dashboard")
