"""Import Chuhaijiang creator XLSX exports into relational records."""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ElementTree
import zipfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator

from django.conf import settings
from django.db import transaction
from django.utils.dateparse import parse_datetime
from tasks.models import CreatorExportArtifact, Product, RelatedCreator


UNIT_MULTIPLIERS = {
    "K": Decimal("1000"),
    "M": Decimal("1000000"),
    "万": Decimal("10000"),
    "亿": Decimal("100000000"),
}


def parse_metric(value: object, *, percent: bool = False) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))

    text = str(value).strip().replace(",", "").replace("$", "")
    if text in {"", "-", "—", "N/A"}:
        return None
    multiplier = Decimal("1")
    for suffix, candidate in UNIT_MULTIPLIERS.items():
        if text.upper().endswith(suffix) if suffix in {"K", "M"} else text.endswith(suffix):
            multiplier = candidate
            text = text[:-1]
            break
    had_percent = text.endswith("%")
    text = text.removesuffix("%").strip()
    try:
        parsed = Decimal(text) * multiplier
    except InvalidOperation:
        return None
    if percent or had_percent:
        return parsed
    return parsed


def parse_count(value: object) -> int | None:
    parsed = parse_metric(value)
    return None if parsed is None else max(0, int(parsed))


def _json_value(value: object) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _text(row: dict[str, object], key: str) -> str:
    value = row.get(key)
    return "" if value is None else str(value).strip()


def _safe_export_path(file_path: str) -> Path:
    resolved = Path(file_path).resolve(strict=True)
    exports_root = Path(settings.EXPORTS_ROOT).resolve()
    try:
        resolved.relative_to(exports_root)
    except ValueError as error:
        raise ValueError("导出文件不在受控 storage/exports 目录中。") from error
    if resolved.suffix.lower() != ".xlsx":
        raise ValueError("关联达人导出文件必须是 XLSX。")
    return resolved


def _sha256(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as exported_file:
        for chunk in iter(lambda: exported_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _column_index(cell_reference: str) -> int:
    letters = re.match(r"[A-Z]+", cell_reference)
    if letters is None:
        return 0
    index = 0
    for character in letters.group(0):
        index = index * 26 + ord(character) - ord("A") + 1
    return index - 1


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        shared_xml = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ElementTree.fromstring(shared_xml)
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    return [
        "".join(text.text or "" for text in item.iter(f"{namespace}t"))
        for item in root.findall(f"{namespace}si")
    ]


def _xlsx_rows(file_path: Path) -> Iterator[list[object]]:
    """Read cell values without loading styles from a third-party XLSX export."""
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(file_path) as archive:
        shared_strings = _shared_strings(archive)
        with archive.open("xl/worksheets/sheet1.xml") as worksheet:
            for _, element in ElementTree.iterparse(worksheet, events=("end",)):
                if element.tag != f"{namespace}row":
                    continue
                values: list[object] = []
                for cell in element.findall(f"{namespace}c"):
                    cell_index = _column_index(cell.attrib.get("r", "A1"))
                    while len(values) <= cell_index:
                        values.append(None)
                    cell_type = cell.attrib.get("t")
                    value_element = cell.find(f"{namespace}v")
                    if cell_type == "inlineStr":
                        inline = cell.find(f"{namespace}is")
                        value: object = "" if inline is None else "".join(
                            text.text or "" for text in inline.iter(f"{namespace}t")
                        )
                    elif value_element is None:
                        value = None
                    elif cell_type == "s":
                        shared_index = int(value_element.text or "0")
                        value = (
                            shared_strings[shared_index]
                            if shared_index < len(shared_strings)
                            else ""
                        )
                    else:
                        value = value_element.text
                    values[cell_index] = value
                yield values
                element.clear()


@transaction.atomic
def import_creator_export(
    product: Product,
    export_data: dict[str, object],
) -> CreatorExportArtifact:
    file_path = _safe_export_path(str(export_data["filePath"]))
    rows = _xlsx_rows(file_path)
    next(rows, None)  # Export attribution row.
    header_values = next(rows, None)
    if header_values is None:
        raise ValueError("达人 XLSX 中没有表头。")
    headers = [str(value).strip() if value is not None else "" for value in header_values]
    imported_count = 0

    for row_number, values in enumerate(rows, start=3):
        row = {
            header: _json_value(value)
            for header, value in zip(headers, values, strict=False)
            if header
        }
        creator_handle = _text(row, "达人 ID")
        if not creator_handle:
            creator_handle = _text(row, "TikTok Creator Url").rstrip("/").rsplit("/", 1)[-1]
        if not creator_handle:
            creator_handle = f"export-row-{row_number}"
        if not any(value not in (None, "") for value in values):
            continue

        defaults = {
            "nickname": _text(row, "达人昵称"),
            "tiktok_url": _text(row, "TikTok Creator Url"),
            "creator_detail_url": _text(row, "uid"),
            "category": _text(row, "达人分类"),
            "country_code": _text(row, "country_code"),
            "recent_7_day_revenue": parse_metric(row.get("近 7 天销售额")),
            "recent_7_day_video_revenue": parse_metric(row.get("近 7 天视频销售额")),
            "recent_7_day_live_revenue": parse_metric(row.get("近 7 天直播销售额")),
            "recent_30_day_revenue": parse_metric(row.get("近 30 天销售额")),
            "recent_30_day_video_revenue": parse_metric(row.get("近 30 天视频销售额")),
            "recent_30_day_live_revenue": parse_metric(
                row.get("近 30 天直播销售额") or row.get("近 30 天直播销售额 ")
            ),
            "related_video_count": parse_count(row.get("关联视频")),
            "related_live_count": parse_count(row.get("关联直播")),
            "follower_count": parse_metric(row.get("粉丝数")),
            "average_views": parse_metric(row.get("平均播放量")),
            "total_views": parse_metric(row.get("总播放量")),
            "average_likes": parse_metric(row.get("平均点赞数")),
            "total_likes": parse_metric(row.get("总点赞数")),
            "engagement_rate": parse_metric(row.get("互动率"), percent=True),
            "like_follower_ratio": parse_metric(row.get("赞粉比")),
            "email": _text(row, "email"),
            "x_url": _text(row, "x_url"),
            "instagram_url": _text(row, "instagram_url"),
            "youtube_url": _text(row, "youtube_url"),
            "whatsapp_url": _text(row, "whatsapp_url"),
            "linkedin_url": _text(row, "linkedin_url"),
            "telegram_url": _text(row, "telegram_url"),
            "facebook_url": _text(row, "facebook_url"),
            "raw_data": row,
        }
        RelatedCreator.objects.update_or_create(
            product=product,
            creator_handle=creator_handle,
            defaults=defaults,
        )
        imported_count += 1

    downloaded_at = parse_datetime(str(export_data.get("downloadedAt", "")))
    try:
        stored_path = str(file_path.relative_to(settings.PROJECT_ROOT))
    except ValueError:
        stored_path = str(file_path)
    artifact, _ = CreatorExportArtifact.objects.update_or_create(
        product=product,
        sha256=_sha256(file_path),
        defaults={
            "file_name": str(export_data["fileName"]),
            "file_path": stored_path,
            "requested_row_count": int(export_data.get("requestedRowCount", 100)),
            "exported_row_count": int(export_data.get("exportedRowCount", 0)),
            "imported_row_count": imported_count,
            "file_size_bytes": int(export_data.get("fileSizeBytes", file_path.stat().st_size)),
            "downloaded_at": downloaded_at,
        },
    )
    return artifact


def product_id_from_url(product_url: str) -> str:
    match = re.search(r"/products/(\d+)", product_url)
    if match is None:
        raise ValueError(f"无法从商品链接解析商品 ID：{product_url}")
    return match.group(1)
