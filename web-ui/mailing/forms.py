from django import forms
from django.conf import settings
from django.db.models import Count, Q

from tasks.models import ImportTask

from .services.template_content import (
    validate_subject,
)
from .services.rich_text import sanitize_rich_html


class EmailTemplateForm(forms.Form):
    subject_template = forms.CharField(
        label="邮件主题",
        max_length=255,
        widget=forms.TextInput(
            attrs={
                "placeholder": "请输入邮件主题",
                "autocomplete": "off",
                "data-template-subject": "",
            }
        ),
    )
    content_html = forms.CharField(widget=forms.HiddenInput())

    def clean_subject_template(self) -> str:
        return validate_subject(self.cleaned_data["subject_template"])

    def clean_content_html(self) -> str:
        sanitized, _ = sanitize_rich_html(
            self.cleaned_data["content_html"],
            allow_upload_tokens=True,
        )
        return sanitized


class ImportTaskChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, task: ImportTask) -> str:
        email_count = getattr(task, "email_creator_count", 0)
        name = task.file_name
        if len(name) > 72:
            name = f"{name[:69]}…"
        return f"{name} · {task.sheet_name} · {email_count} 个邮箱"


class EmailQueueForm(forms.Form):
    import_task = ImportTaskChoiceField(
        label="选择导入批次",
        queryset=ImportTask.objects.none(),
        empty_label="请选择包含达人邮箱的导入批次",
    )
    limit = forms.IntegerField(
        label="入队数量",
        min_value=1,
        initial=100,
        widget=forms.NumberInput(
            attrs={
                "inputmode": "numeric",
                "placeholder": "1–100",
            }
        ),
    )
    retry_failed = forms.BooleanField(
        label="允许重新入队未超过重试上限的失败邮件",
        required=False,
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["limit"].max_value = settings.CREATOR_EMAIL_DAILY_LIMIT
        self.fields["limit"].initial = settings.CREATOR_EMAIL_DAILY_LIMIT
        self.fields["import_task"].queryset = (
            ImportTask.objects.filter(
                status__in=[
                    ImportTask.Status.SUCCESS,
                    ImportTask.Status.PARTIAL_SUCCESS,
                ]
            )
            .annotate(
                email_creator_count=Count(
                    "imported_creators",
                    filter=~Q(imported_creators__creator__email=""),
                )
            )
            .filter(email_creator_count__gt=0)
            .order_by("-created_at")
        )
