"""Execute validated creator spreadsheet transformation rules."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Iterator
from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from .import_rules import validate_rule
from .import_types import ConvertedRow, RowIssue


EMAIL_PATTERN = re.compile(
    r"(?<![A-Z0-9._%+\-])"
    r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,63}"
    r"(?![A-Z0-9._%+\-])",
    re.IGNORECASE,
)
MONEY_MULTIPLIERS = {
    "k": Decimal("1000"),
    "m": Decimal("1000000"),
    "b": Decimal("1000000000"),
    "千": Decimal("1000"),
    "万": Decimal("10000"),
    "亿": Decimal("100000000"),
}
EMPTY_VALUES = {"", "-", "—", "n/a", "na", "null", "none"}


def extract_email(value: object) -> str:
    match = EMAIL_PATTERN.search(str(value or "").strip())
    if not match:
        return ""
    candidate = match.group(0).casefold()
    try:
        validate_email(candidate)
    except ValidationError:
        return ""
    return candidate


def parse_money(value: object) -> Decimal | None:
    text = str(value or "").strip()
    if text.casefold() in EMPTY_VALUES:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace(",", "").replace("，", "")
    text = re.sub(r"^[¥￥$€£]\s*", "", text)
    text = re.sub(r"\s*(?:usd|cny|rmb|eur|gbp)$", "", text, flags=re.IGNORECASE)
    text = text.strip()
    multiplier = Decimal("1")
    if text:
        suffix = text[-1].casefold()
        if suffix in MONEY_MULTIPLIERS:
            multiplier = MONEY_MULTIPLIERS[suffix]
            text = text[:-1].strip()
    try:
        parsed = Decimal(text) * multiplier
    except InvalidOperation:
        return None
    if negative:
        parsed = -parsed
    return parsed


def _cell(row: tuple[str, ...], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return str(row[index] or "")


def _apply_text_transform(value: str, transform: str) -> str:
    text = str(value or "")
    if transform in {"direct_copy", "trim"}:
        return text.strip()
    if transform == "extract_url_last_segment":
        stripped = text.strip().rstrip("/")
        parsed = urlparse(stripped)
        path = parsed.path if parsed.scheme and parsed.netloc else stripped
        return path.rsplit("/", 1)[-1].strip()
    if transform == "set_null" or transform == "ignore":
        return ""
    return text.strip()


def transform_rows(
    rows: tuple[tuple[str, ...], ...],
    rule: dict[str, object],
    *,
    limit: int | None = None,
) -> Iterator[ConvertedRow]:
    validated = validate_rule(rule, rows)
    header_row = int(validated["header_row"])
    produced = 0
    transforms = validated["column_transforms"]
    creator_column = int(validated["creator_id_column"])
    nickname_column = validated["nickname_column"]
    email_column = validated["email_column"]

    for row_number, row in enumerate(rows[header_row:], start=header_row + 1):
        if not any(str(value).strip() for value in row):
            continue
        issues: list[RowIssue] = []
        creator_transform = transforms.get(str(creator_column), "trim")
        creator_id = _apply_text_transform(
            _cell(row, creator_column),
            creator_transform,
        )
        if not creator_id:
            issues.append(
                RowIssue(
                    code="CREATOR_ID_MISSING",
                    field="creator_id",
                    message="达人 ID 为空或无法提取。",
                    fatal=True,
                )
            )
        elif len(creator_id) > 160:
            issues.append(
                RowIssue(
                    code="CREATOR_ID_TOO_LONG",
                    field="creator_id",
                    message="达人 ID 超过 160 个字符。",
                    fatal=True,
                )
            )
        elif any(character in creator_id for character in "\r\n\t\x00"):
            issues.append(
                RowIssue(
                    code="CREATOR_ID_INVALID",
                    field="creator_id",
                    message="达人 ID 包含不允许的控制字符。",
                    fatal=True,
                )
            )

        nickname = ""
        if nickname_column is not None:
            nickname_index = int(nickname_column)
            nickname = _apply_text_transform(
                _cell(row, nickname_index),
                transforms.get(str(nickname_index), "trim"),
            )[:255]

        email = ""
        if email_column is not None:
            email_index = int(email_column)
            email_source = _cell(row, email_index)
            if email_source.strip():
                email = extract_email(email_source)
                if not email:
                    issues.append(
                        RowIssue(
                            code="EMAIL_INVALID",
                            field="email",
                            message="邮箱为空或格式无效，达人仍可导入。",
                        )
                    )

        sales: dict[int, Decimal] = {}
        for sales_rule in validated["sales_columns"]:
            source_column = int(sales_rule["source_column"])
            source_value = _cell(row, source_column)
            if not source_value.strip():
                continue
            amount = parse_money(source_value)
            if amount is None:
                sales_label = (
                    "总销售额"
                    if int(sales_rule["window_days"]) == 0
                    else f"{sales_rule['window_days']} 天销售额"
                )
                issues.append(
                    RowIssue(
                        code="SALES_AMOUNT_INVALID",
                        field=f"sales_{sales_rule['window_days']}",
                        message=(
                            f"{sales_label}无法解析，"
                            "其他有效字段仍会导入。"
                        ),
                    )
                )
                continue
            sales[int(sales_rule["window_days"])] = amount

        yield ConvertedRow(
            row_number=row_number,
            creator_id=creator_id,
            nickname=nickname,
            email=email,
            sales=sales,
            issues=tuple(issues),
        )
        produced += 1
        if limit is not None and produced >= limit:
            return


def preview_rows(
    rows: tuple[tuple[str, ...], ...],
    rule: dict[str, object],
    *,
    limit: int,
) -> list[dict[str, object]]:
    return [
        converted.as_preview_dict()
        for converted in transform_rows(rows, rule, limit=limit)
    ]
