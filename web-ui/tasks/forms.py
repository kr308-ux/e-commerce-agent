from django import forms

from .models import CreatorAcquisitionTask, PRODUCT_LIMIT_CHOICES


class CreatorAcquisitionTaskForm(forms.ModelForm):
    product_limit = forms.TypedChoiceField(
        label="获取商品数量",
        choices=PRODUCT_LIMIT_CHOICES,
        coerce=int,
        empty_value=None,
        help_text="每个商品最多导入当前导出的 100 位关联达人。",
    )

    class Meta:
        model = CreatorAcquisitionTask
        fields = ["product_limit"]

    def clean_product_limit(self) -> int:
        product_limit = self.cleaned_data["product_limit"]
        if product_limit % 10 != 0:
            raise forms.ValidationError("商品数量必须是 10 的倍数。")
        return product_limit
