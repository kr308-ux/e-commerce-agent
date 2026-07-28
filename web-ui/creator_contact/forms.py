"""Forms for greeting templates and creator-contact task creation."""

from __future__ import annotations

from django import forms

from tasks.models import CreatorAcquisitionTask, Product

from .models import (
    CreatorContactTask,
    DirectedCollaborationOption,
    GreetingTemplate,
)
from .services.candidate_selector import select_candidates


class GreetingTemplateForm(forms.ModelForm):
    class Meta:
        model = GreetingTemplate
        fields = ["name", "content", "is_default"]
        labels = {
            "name": "模板名称",
            "content": "招呼语内容",
            "is_default": "设为默认招呼语",
        }
        widgets = {
            "content": forms.Textarea(
                attrs={
                    "rows": 7,
                    "maxlength": 2000,
                    "placeholder": "输入要发送给达人的招呼语",
                }
            )
        }

    def clean_content(self) -> str:
        content = str(self.cleaned_data["content"] or "").strip()
        if not content:
            raise forms.ValidationError("招呼语不能为空。")
        if len(content) > 2000:
            raise forms.ValidationError("招呼语不能超过 2000 个字符。")
        return content

    def _get_validation_exclusions(self) -> set[str]:
        exclusions = super()._get_validation_exclusions()
        if self.cleaned_data.get("is_default"):
            # The view atomically clears the previous default before saving.
            # Excluding this field prevents a stale conditional-constraint
            # validation error while preserving the database constraint.
            exclusions.add("is_default")
        return exclusions


class CreatorContactTaskForm(forms.ModelForm):
    store_id = forms.CharField(
        label="紫鸟店铺 ID",
        max_length=80,
        widget=forms.HiddenInput(),
    )
    confirm_send_greeting = forms.BooleanField(
        label="确认发送招呼语",
        required=True,
    )
    confirm_send_invitation = forms.BooleanField(
        label="确认发送定向合作邀请",
        required=True,
    )
    confirm_send_card = forms.BooleanField(
        label="确认批量发送定向合作卡片",
        required=True,
    )

    class Meta:
        model = CreatorContactTask
        fields = [
            "store_id",
            "source_product",
            "top_n",
            "greeting_template",
            "collaboration_option",
            "confirm_send_greeting",
            "confirm_send_invitation",
            "confirm_send_card",
        ]
        labels = {
            "source_product": "关联商品",
            "top_n": "联系人数",
            "greeting_template": "招呼语模板",
            "collaboration_option": "定向合作选项",
        }

    def __init__(self, *args, store_id: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        normalized_store = str(
            store_id
            or self.data.get("store_id")
            or self.initial.get("store_id")
            or ""
        ).strip()
        self.fields["store_id"].initial = normalized_store
        self.fields["source_product"].queryset = (
            Product.objects
            .filter(task__status=CreatorAcquisitionTask.Status.SUCCESS)
            .select_related("task")
            .order_by("-task__created_at", "id")
        )
        greeting_queryset = GreetingTemplate.objects.filter(is_active=True)
        self.fields["greeting_template"].queryset = greeting_queryset
        self.fields["greeting_template"].required = True
        if not self.is_bound and not self.initial.get("greeting_template"):
            default_greeting = greeting_queryset.filter(
                is_default=True
            ).first()
            if default_greeting is not None:
                self.initial["greeting_template"] = default_greeting.pk
        self.fields["collaboration_option"].queryset = (
            DirectedCollaborationOption.objects.filter(
                store_id=normalized_store,
                status=DirectedCollaborationOption.Status.ONGOING,
                is_active=True,
            )
        )
        self.fields["collaboration_option"].required = True

    def clean(self) -> dict[str, object]:
        cleaned = super().clean()
        store_id = str(cleaned.get("store_id") or "").strip()
        product = cleaned.get("source_product")
        invitation = cleaned.get("collaboration_option")
        top_n = cleaned.get("top_n")

        if invitation is not None and invitation.store_id != store_id:
            self.add_error(
                "collaboration_option",
                "定向合作选项不属于当前店铺。",
            )
        if product is not None and top_n:
            selection = select_candidates(
                product=product,
                store_id=store_id,
                top_n=int(top_n),
            )
            if not selection.creators:
                self.add_error(
                    "source_product",
                    "该商品没有尚未联系的达人。",
                )
        return cleaned
