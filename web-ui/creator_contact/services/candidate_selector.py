"""Select and freeze the highest-revenue uncontacted creators for a task."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, QuerySet

from tasks.models import Product, RelatedCreator

from creator_contact.models import (
    ContactedCreator,
    CreatorContactTarget,
    CreatorContactTask,
    normalize_creator_handle,
)


@dataclass(frozen=True)
class CandidateSelection:
    creators: tuple[RelatedCreator, ...]
    excluded_count: int
    available_count: int


def ranked_product_creators(product: Product) -> QuerySet[RelatedCreator]:
    """Return product creators ordered by 30-day then 7-day revenue."""
    return (
        RelatedCreator.objects
        .filter(product=product)
        .exclude(creator_handle="")
        .order_by(
            F("recent_30_day_revenue").desc(nulls_last=True),
            F("recent_7_day_revenue").desc(nulls_last=True),
            "creator_handle",
            "id",
        )
    )


def select_candidates(
    *,
    product: Product,
    store_id: str,
    top_n: int | None = None,
) -> CandidateSelection:
    """Exclude store-global contact history and return deterministic candidates."""
    normalized_store = str(store_id or "").strip()
    if not normalized_store:
        raise ValidationError("必须指定紫鸟店铺。")
    if top_n is not None and top_n < 1:
        raise ValidationError("联系达人数必须大于 0。")

    contacted_handles = set(
        ContactedCreator.objects
        .filter(store_id=normalized_store)
        .values_list("normalized_handle", flat=True)
    )
    selected: list[RelatedCreator] = []
    seen: set[str] = set()
    excluded_count = 0

    for creator in ranked_product_creators(product):
        normalized = normalize_creator_handle(creator.creator_handle)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        if normalized in contacted_handles:
            excluded_count += 1
            continue
        selected.append(creator)

    available_count = len(selected)
    if top_n is not None:
        selected = selected[:top_n]
    return CandidateSelection(
        creators=tuple(selected),
        excluded_count=excluded_count,
        available_count=available_count,
    )


@transaction.atomic
def freeze_task_targets(
    task: CreatorContactTask,
) -> CandidateSelection:
    """Persist the exact creator ranking that the worker will execute."""
    locked_task = (
        CreatorContactTask.objects
        .select_for_update()
        .select_related("source_product")
        .get(pk=task.pk)
    )
    if locked_task.targets.exists():
        existing = tuple(
            target.related_creator
            for target in locked_task.targets.select_related("related_creator")
            if target.related_creator is not None
        )
        return CandidateSelection(
            creators=existing,
            excluded_count=0,
            available_count=len(existing),
        )

    selection = select_candidates(
        product=locked_task.source_product,
        store_id=locked_task.store_id,
        top_n=locked_task.top_n,
    )
    if not selection.creators:
        raise ValidationError("所选商品没有可联系且尚未联系的达人。")

    targets = []
    for rank, creator in enumerate(selection.creators, start=1):
        normalized = normalize_creator_handle(creator.creator_handle)
        targets.append(
            CreatorContactTarget(
                task=locked_task,
                related_creator=creator,
                rank=rank,
                creator_handle_snapshot=creator.creator_handle,
                normalized_handle=normalized,
                nickname_snapshot=creator.nickname,
                recent_7_day_revenue_snapshot=creator.recent_7_day_revenue,
                recent_30_day_revenue_snapshot=creator.recent_30_day_revenue,
            )
        )
    CreatorContactTarget.objects.bulk_create(targets)
    return selection


def candidate_payload(
    creator: RelatedCreator,
    *,
    rank: int,
) -> dict[str, object]:
    revenue: Decimal | None = creator.recent_30_day_revenue
    revenue_7_day: Decimal | None = creator.recent_7_day_revenue
    return {
        "id": creator.pk,
        "rank": rank,
        "handle": f"@{normalize_creator_handle(creator.creator_handle)}",
        "creatorHandle": normalize_creator_handle(creator.creator_handle),
        "nickname": creator.nickname,
        "recent7DayRevenue": (
            None if revenue_7_day is None else str(revenue_7_day)
        ),
        "recent30DayRevenue": None if revenue is None else str(revenue),
        "relatedVideoCount": creator.related_video_count,
        "tiktokUrl": creator.tiktok_url,
    }
