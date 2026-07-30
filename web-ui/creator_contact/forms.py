"""Forms for greeting templates and creator-contact task creation."""

from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError

from tasks.models import Creator, ImportTask

from .models import (
    CreatorContactTask,
    DirectedCollaborationOption,
    GreetingTemplate,
)
from .services.candidate_selector import select_candidates


def collaboration_option_label(
    option: DirectedCollaborationOption,
) -> str:
    return f"{option.name} · ID {option.external_invitation_id}"


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
    selection_method = forms.ChoiceField(
        label="达人筛选规则",
        choices=CreatorContactTask.SelectionMethod.choices,
        initial=CreatorContactTask.SelectionMethod.SALES,
        required=False,
        widget=forms.RadioSelect(),
    )
    sales_window_days = forms.TypedChoiceField(
        label="销售额周期",
        choices=((7, "近 7 天"), (30, "近 30 天"), (0, "总销售额")),
        coerce=int,
        initial=30,
        required=False,
    )
    selected_creator_ids = forms.MultipleChoiceField(
        label="手动选择达人",
        required=False,
    )

    class Meta:
        model = CreatorContactTask
        fields = [
            "store_id",
            "source_import_task",
            "selection_method",
            "sales_window_days",
            "selected_creator_ids",
            "top_n",
            "greeting_template",
            "collaboration_option",
        ]
        labels = {
            "source_import_task": "达人导入批次",
            "selection_method": "达人筛选规则",
            "sales_window_days": "销售额周期",
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
        self.fields["source_import_task"].queryset = (
            ImportTask.objects.filter(
                status__in=[
                    ImportTask.Status.SUCCESS,
                    ImportTask.Status.PARTIAL_SUCCESS,
                ],
                imported_creators__isnull=False,
            )
            .distinct()
            .order_by("-created_at")
        )
        self.fields["top_n"].required = False
        source_import_task_id = (
            self.data.get("source_import_task")
            or getattr(self.instance, "source_import_task_id", None)
            or getattr(self.initial.get("source_import_task"), "pk", None)
            or self.initial.get("source_import_task")
        )
        selected_creator_choices: list[tuple[str, str]] = []
        if source_import_task_id:
            try:
                selected_creator_choices = [
                    (str(creator_id), str(creator_id))
                    for creator_id in Creator.objects.filter(
                        import_memberships__import_task_id=(
                            source_import_task_id
                        ),
                    )
                    .distinct()
                    .values_list("pk", flat=True)
                ]
            except (ValidationError, ValueError):
                selected_creator_choices = []
        self.fields["selected_creator_ids"].choices = (
            selected_creator_choices
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
        self.fields[
            "collaboration_option"
        ].label_from_instance = collaboration_option_label
        self.fields["collaboration_option"].required = True

    def clean(self) -> dict[str, object]:
        cleaned = super().clean()
        store_id = str(cleaned.get("store_id") or "").strip()
        import_task = cleaned.get("source_import_task")
        invitation = cleaned.get("collaboration_option")
        top_n = cleaned.get("top_n")
        selection_method = str(
            cleaned.get("selection_method")
            or CreatorContactTask.SelectionMethod.SALES
        )
        sales_window_days = cleaned.get("sales_window_days")
        if sales_window_days in (None, ""):
            sales_window_days = 30
        sales_window_days = int(sales_window_days)
        cleaned["selection_method"] = selection_method
        cleaned["sales_window_days"] = sales_window_days

        if invitation is not None and invitation.store_id != store_id:
            self.add_error(
                "collaboration_option",
                "定向合作选项不属于当前店铺。",
            )
        selection = None
        selection_field = "source_import_task"
        if selection_method == CreatorContactTask.SelectionMethod.SALES:
            if not top_n:
                self.add_error("top_n", "请输入需要联系的达人数。")
                return cleaned
            selection_kwargs = {
                "top_n": int(top_n),
                "sales_window_days": sales_window_days,
            }
        elif selection_method == CreatorContactTask.SelectionMethod.CREATOR_ID:
            selection_kwargs = {"top_n": 50}
        else:
            selected_creator_ids = list(
                cleaned.get("selected_creator_ids") or ()
            )
            if not selected_creator_ids:
                self.add_error(
                    "selected_creator_ids",
                    "请至少勾选一位达人。",
                )
                return cleaned
            selection_field = "selected_creator_ids"
            selection_kwargs = {
                "selected_creator_pks": selected_creator_ids,
            }

        if import_task is not None and store_id:
            try:
                selection = select_candidates(
                    import_task=import_task,
                    store_id=store_id,
                    selection_method=selection_method,
                    **selection_kwargs,
                )
            except ValidationError as error:
                self.add_error(
                    (
                        "sales_window_days"
                        if selection_method
                        == CreatorContactTask.SelectionMethod.SALES
                        else selection_field
                    ),
                    error,
                )
                self.candidate_selection = None
                return cleaned
            if selection.unmatched_identifiers:
                self.add_error(
                    selection_field,
                    "以下达人不在所选导入批次中："
                    + "、".join(selection.unmatched_identifiers),
                )
            if not selection.creators:
                self.add_error(
                    selection_field,
                    "没有可联系的达人，请检查选择或已联系记录。",
                )
            elif len(selection.creators) > 100:
                self.add_error(
                    selection_field,
                    "单个联系任务最多可选择 100 位达人。",
                )
            else:
                cleaned["top_n"] = len(selection.creators)
                self.instance.top_n = len(selection.creators)
        self.candidate_selection = selection
        return cleaned
