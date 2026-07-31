from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Count, Q

from tasks.models import ImportTask

from .services.queue import import_email_creators

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
    class BusinessRule:
        CARD_SENT = "CARD_SENT"
        MANUAL = "MANUAL"

        CHOICES = (
            (CARD_SENT, "合作卡片发送成功的达人"),
            (MANUAL, "手动选择达人"),
        )

    business_rule = forms.ChoiceField(
        label="业务规则",
        choices=BusinessRule.CHOICES,
        required=False,
        initial=BusinessRule.CARD_SENT,
        widget=forms.RadioSelect(),
    )
    import_task = ImportTaskChoiceField(
        label="选择导入批次",
        queryset=ImportTask.objects.none(),
        empty_label="请选择包含达人邮箱的导入批次",
        required=False,
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
    selected_creator_ids = forms.MultipleChoiceField(
        required=False,
        choices=(),
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
        raw_task_id = (
            self.data.get("import_task")
            if self.is_bound
            else self.initial.get("import_task")
        )
        if isinstance(raw_task_id, ImportTask):
            import_task = raw_task_id
        else:
            try:
                import_task = (
                    self.fields["import_task"]
                    .queryset.filter(pk=raw_task_id)
                    .first()
                    if raw_task_id
                    else None
                )
            except (ValidationError, ValueError):
                import_task = None
        if import_task is not None:
            self.fields["selected_creator_ids"].choices = [
                (str(creator.pk), creator.nickname or creator.creator_id)
                for creator in import_email_creators(import_task)
            ]

    def clean(self) -> dict[str, object]:
        cleaned = super().clean()
        rule = (
            cleaned.get("business_rule")
            or self.BusinessRule.CARD_SENT
        )
        cleaned["business_rule"] = rule
        import_task = cleaned.get("import_task")
        if import_task is None:
            self.add_error("import_task", "请选择需要关联的导入批次。")
        if rule != self.BusinessRule.MANUAL:
            cleaned["selected_creator_ids"] = []
            return cleaned

        selected = cleaned.get("selected_creator_ids") or []
        if not selected:
            self.add_error(
                "selected_creator_ids",
                "请至少选择一位达人。",
            )
        if len(selected) > settings.CREATOR_EMAIL_DAILY_LIMIT:
            self.add_error(
                "selected_creator_ids",
                f"每次最多选择 {settings.CREATOR_EMAIL_DAILY_LIMIT} 位达人。",
            )
        if selected:
            cleaned["limit"] = len(selected)
        return cleaned
