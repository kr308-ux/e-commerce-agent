"""Select and freeze creators from one confirmed import batch."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import DecimalField, F, OuterRef, QuerySet, Subquery

from tasks.models import Creator, CreatorSalesMetric, ImportTask

from creator_contact.models import (
    ContactedCreator,
    CreatorContactTarget,
    CreatorContactTask,
    normalize_creator_handle,
)


@dataclass(frozen=True)
class CandidateSelection:
    creators: tuple[Creator, ...]
    excluded_count: int
    available_count: int
    unmatched_identifiers: tuple[str, ...] = ()


def import_creators_with_metrics(
    import_task: ImportTask,
) -> QuerySet[Creator]:
    """Return batch creators with the latest 7/30-day and total snapshots."""

    def metric_subquery(window_days: int):
        return (
            CreatorSalesMetric.objects.filter(
                import_task=import_task,
                creator_id=OuterRef("pk"),
                window_days=window_days,
            )
            .order_by("-source_row_number", "-id")
            .values("sales_amount")[:1]
        )

    return (
        Creator.objects.filter(import_memberships__import_task=import_task)
        .annotate(
            recent_30_day_revenue=Subquery(
                metric_subquery(30),
                output_field=DecimalField(max_digits=24, decimal_places=4),
            ),
            recent_7_day_revenue=Subquery(
                metric_subquery(7),
                output_field=DecimalField(max_digits=24, decimal_places=4),
            ),
            total_revenue=Subquery(
                metric_subquery(0),
                output_field=DecimalField(max_digits=24, decimal_places=4),
            ),
            import_row=F("import_memberships__first_row_number"),
        )
    )


def ranked_import_creators(
    import_task: ImportTask,
    *,
    sales_window_days: int = 30,
) -> QuerySet[Creator]:
    """Return batch creators ordered by the requested sales window."""
    field_by_window = {
        0: "total_revenue",
        7: "recent_7_day_revenue",
        30: "recent_30_day_revenue",
    }
    primary_field = field_by_window.get(sales_window_days)
    if primary_field is None:
        raise ValidationError(
            "销售额周期仅支持近 7 天、近 30 天或总销售额。"
        )
    return import_creators_with_metrics(import_task).order_by(
        F(primary_field).desc(nulls_last=True),
        "import_row",
        "creator_id",
    )


def _stable_import_creators(
    import_task: ImportTask,
) -> QuerySet[Creator]:
    return (
        import_creators_with_metrics(import_task)
        .order_by(
            "import_row",
            "creator_id",
        )
    )


def select_candidates(
    *,
    import_task: ImportTask,
    store_id: str,
    top_n: int | None = None,
    selection_method: str = CreatorContactTask.SelectionMethod.SALES,
    sales_window_days: int = 30,
    selected_creator_pks: list[str] | tuple[str, ...] | None = None,
) -> CandidateSelection:
    normalized_store = str(store_id or "").strip()
    if not normalized_store:
        raise ValidationError("必须指定紫鸟店铺。")
    if top_n is not None and top_n < 1:
        raise ValidationError("联系达人数必须大于 0。")
    valid_methods = {
        value for value, _label in CreatorContactTask.SelectionMethod.choices
    }
    if selection_method not in valid_methods:
        raise ValidationError("不支持的达人选择方式。")

    contacted_handles = set(
        ContactedCreator.objects.filter(store_id=normalized_store).values_list(
            "normalized_handle",
            flat=True,
        )
    )
    selected: list[Creator] = []
    seen: set[str] = set()
    excluded_count = 0
    unmatched_identifiers: list[str] = []

    if selection_method == CreatorContactTask.SelectionMethod.SALES:
        source_creators = list(
            ranked_import_creators(
                import_task,
                sales_window_days=sales_window_days,
            )
        )
        revenue_field = {
            0: "total_revenue",
            7: "recent_7_day_revenue",
            30: "recent_30_day_revenue",
        }[sales_window_days]
        if source_creators and all(
            getattr(creator, revenue_field, None) is None
            for creator in source_creators
        ):
            sales_label = {
                0: "总销售额",
                7: "近 7 天销售额",
                30: "近 30 天销售额",
            }[sales_window_days]
            raise ValidationError(
                f"所选导入批次不包含{sales_label}数据。"
            )
    else:
        stable_creators = list(_stable_import_creators(import_task))
        if selection_method == CreatorContactTask.SelectionMethod.CREATOR_ID:
            source_creators = stable_creators
        elif selected_creator_pks is None:
            source_creators = stable_creators
        else:
            creator_map = {
                str(creator.pk): creator for creator in stable_creators
            }
            source_creators = []
            requested_pk_seen: set[str] = set()
            for raw_pk in selected_creator_pks:
                creator_pk = str(raw_pk).strip()
                if not creator_pk or creator_pk in requested_pk_seen:
                    continue
                requested_pk_seen.add(creator_pk)
                creator = creator_map.get(creator_pk)
                if creator is None:
                    unmatched_identifiers.append(creator_pk)
                else:
                    source_creators.append(creator)

    for creator in source_creators:
        normalized = normalize_creator_handle(creator.creator_id)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        if normalized in contacted_handles:
            excluded_count += 1
            continue
        selected.append(creator)

    available_count = len(selected)
    if selection_method == CreatorContactTask.SelectionMethod.CREATOR_ID:
        top_n = min(top_n or 50, 50)
    if top_n is not None:
        selected = selected[:top_n]
    return CandidateSelection(
        creators=tuple(selected),
        excluded_count=excluded_count,
        available_count=available_count,
        unmatched_identifiers=tuple(unmatched_identifiers),
    )


@transaction.atomic
def freeze_task_targets(
    task: CreatorContactTask,
    *,
    selection: CandidateSelection | None = None,
) -> CandidateSelection:
    locked_task = (
        CreatorContactTask.objects.select_for_update()
        .select_related("source_import_task")
        .get(pk=task.pk)
    )
    if locked_task.targets.exists():
        existing = tuple(
            target.creator
            for target in locked_task.targets.select_related("creator")
            if target.creator is not None
        )
        return CandidateSelection(
            creators=existing,
            excluded_count=0,
            available_count=len(existing),
        )

    if selection is None:
        if (
            locked_task.selection_method
            != CreatorContactTask.SelectionMethod.SALES
        ):
            raise ValidationError(
                "按达人 ID 或手动勾选创建的任务必须同时冻结所选达人。"
            )
        selection = select_candidates(
            import_task=locked_task.source_import_task,
            store_id=locked_task.store_id,
            top_n=locked_task.top_n,
            selection_method=locked_task.selection_method,
            sales_window_days=locked_task.sales_window_days,
        )
    if not selection.creators:
        raise ValidationError("所选导入批次没有可联系且尚未联系的达人。")

    targets = []
    for rank, creator in enumerate(selection.creators, start=1):
        normalized = normalize_creator_handle(creator.creator_id)
        targets.append(
            CreatorContactTarget(
                task=locked_task,
                creator=creator,
                rank=rank,
                creator_handle_snapshot=creator.creator_id,
                normalized_handle=normalized,
                nickname_snapshot=creator.nickname,
                recent_7_day_revenue_snapshot=getattr(
                    creator,
                    "recent_7_day_revenue",
                    None,
                ),
                recent_30_day_revenue_snapshot=getattr(
                    creator,
                    "recent_30_day_revenue",
                    None,
                ),
                total_revenue_snapshot=getattr(
                    creator,
                    "total_revenue",
                    None,
                ),
            )
        )
    CreatorContactTarget.objects.bulk_create(targets)
    return selection


def candidate_payload(
    creator: Creator,
    *,
    rank: int,
) -> dict[str, object]:
    revenue: Decimal | None = getattr(
        creator,
        "recent_30_day_revenue",
        None,
    )
    revenue_7_day: Decimal | None = getattr(
        creator,
        "recent_7_day_revenue",
        None,
    )
    total_revenue: Decimal | None = getattr(
        creator,
        "total_revenue",
        None,
    )
    creator_id = normalize_creator_handle(creator.creator_id)
    return {
        "id": creator.pk,
        "rank": rank,
        "handle": f"@{creator_id}",
        "creatorHandle": creator_id,
        "nickname": creator.nickname,
        "recent7DayRevenue": (
            None if revenue_7_day is None else str(revenue_7_day)
        ),
        "recent30DayRevenue": (
            None if revenue is None else str(revenue)
        ),
        "totalRevenue": (
            None if total_revenue is None else str(total_revenue)
        ),
        "relatedVideoCount": None,
        "tiktokUrl": "",
    }
