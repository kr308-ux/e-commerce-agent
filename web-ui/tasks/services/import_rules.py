"""Deterministic creator-column recognition and rule validation."""

from __future__ import annotations

import copy
import re
from typing import Any

from jsonschema import Draft202012Validator


CREATOR_ID_ALIASES = {
    "达人id",
    "creatorid",
    "uid",
    "达人uid",
    "账号id",
    "达人编号",
}
NICKNAME_ALIASES = {
    "达人昵称",
    "达人名称",
    "creatorname",
    "nickname",
    "username",
    "用户名",
    "账号名称",
}
EMAIL_ALIASES = {
    "邮箱",
    "联系邮箱",
    "商务邮箱",
    "email",
    "emailaddress",
    "联系方式",
    "contact",
}
SALES_TERMS = ("gmv", "revenue", "销售额", "成交额", "成交金额")
NON_SALES_TERMS = (
    "销量",
    "订单",
    "unitsold",
    "unitssold",
    "ordercount",
)
SALES_BREAKDOWN_TERMS = ("视频", "直播", "video", "live", "livestream")
TOTAL_SALES_TERMS = (
    "总销售额",
    "累计销售额",
    "全部销售额",
    "总成交额",
    "累计成交额",
    "lifetimegmv",
    "lifetimerevenue",
    "totalsales",
    "totalrevenue",
)
ALLOWED_TRANSFORMS = {
    "direct_copy",
    "trim",
    "parse_money",
    "extract_email",
    "extract_url_last_segment",
    "set_null",
    "ignore",
}
HEADER_SCAN_LIMIT = 50

IMPORT_RULE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "header_row",
        "creator_id_column",
        "nickname_column",
        "email_column",
        "sales_columns",
        "ignored_columns",
        "column_transforms",
    ],
    "properties": {
        "header_row": {"type": "integer", "minimum": 1},
        "creator_id_column": {"type": "integer", "minimum": 0},
        "nickname_column": {
            "anyOf": [{"type": "integer", "minimum": 0}, {"type": "null"}]
        },
        "email_column": {
            "anyOf": [{"type": "integer", "minimum": 0}, {"type": "null"}]
        },
        "sales_columns": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source_column", "window_days", "transform_type"],
                "properties": {
                    "source_column": {"type": "integer", "minimum": 0},
                    "window_days": {"type": "integer", "minimum": 0, "maximum": 3650},
                    "transform_type": {"enum": sorted(ALLOWED_TRANSFORMS)},
                },
            },
        },
        "ignored_columns": {
            "type": "array",
            "items": {"type": "integer", "minimum": 0},
            "uniqueItems": True,
        },
        "column_transforms": {
            "type": "object",
            "additionalProperties": {"enum": sorted(ALLOWED_TRANSFORMS)},
        },
    },
}


class RuleValidationError(ValueError):
    """Raised when a transformation rule cannot be executed safely."""


def normalize_header(value: object) -> str:
    return re.sub(r"[\s_\-:/（）()]+", "", str(value or "")).casefold()


def _row_score(row: tuple[str, ...]) -> tuple[int, int]:
    normalized = [normalize_header(value) for value in row]
    recognized = 0
    has_creator = False
    for value in normalized:
        if value in CREATOR_ID_ALIASES:
            recognized += 8
            has_creator = True
        elif value in NICKNAME_ALIASES or value in EMAIL_ALIASES:
            recognized += 3
        elif is_sales_header(value):
            recognized += 4
    nonempty = sum(bool(value) for value in normalized)
    if has_creator:
        recognized += 10
    return recognized, min(nonempty, 10)


def detect_header_row(rows: tuple[tuple[str, ...], ...]) -> int:
    if not rows:
        raise RuleValidationError("表格中没有数据。")
    candidates = list(enumerate(rows[:HEADER_SCAN_LIMIT], start=1))
    best_row, best_score = max(candidates, key=lambda item: _row_score(item[1]))
    if _row_score(best_score)[0] == 0:
        # Fall back to the densest early row so the user can correct the rule.
        best_row, _ = max(
            candidates,
            key=lambda item: sum(bool(str(value).strip()) for value in item[1]),
        )
    return best_row


def is_sales_header(value: object) -> bool:
    normalized = normalize_header(value)
    if any(term in normalized for term in NON_SALES_TERMS):
        return False
    return any(term in normalized for term in SALES_TERMS)


def extract_window_days(value: object) -> int | None:
    normalized = normalize_header(value)
    patterns = (
        r"(?:近)?(\d{1,4})(?:天|日|d|day|days)",
        r"(\d{1,4})(?:天|日|d|day|days)(?:内|累计)?",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized, re.IGNORECASE)
        if match:
            days = int(match.group(1))
            if 1 <= days <= 3650:
                return days
    return None


def build_initial_rule(
    rows: tuple[tuple[str, ...], ...],
) -> tuple[dict[str, Any], list[str]]:
    header_row = detect_header_row(rows)
    headers = rows[header_row - 1]
    normalized = [normalize_header(value) for value in headers]
    warnings: list[str] = []

    creator_id_column = next(
        (index for index, value in enumerate(normalized) if value in CREATOR_ID_ALIASES),
        None,
    )
    if creator_id_column is None:
        creator_id_column = 0
        warnings.append("未能可靠识别达人 ID 列，请检查预览并使用自然语言修正。")

    nickname_column = next(
        (index for index, value in enumerate(normalized) if value in NICKNAME_ALIASES),
        None,
    )
    email_column = next(
        (index for index, value in enumerate(normalized) if value in EMAIL_ALIASES),
        None,
    )
    sales_candidates: dict[int, tuple[tuple[int, int, int], dict[str, Any], str]] = {}
    for index, header in enumerate(headers):
        if not is_sales_header(header):
            continue
        normalized_header = normalize_header(header)
        days = extract_window_days(header)
        if (
            days is None
            and any(term in normalized_header for term in TOTAL_SALES_TERMS)
        ):
            days = 0
        if days is None:
            warnings.append(f"销售额列“{header}”没有明确统计周期，已暂时忽略。")
            continue
        priority = (
            int(any(term in normalized_header for term in SALES_BREAKDOWN_TERMS)),
            len(normalized_header),
            index,
        )
        candidate = {
            "source_column": index,
            "window_days": days,
            "transform_type": "parse_money",
        }
        existing = sales_candidates.get(days)
        if existing is None or priority < existing[0]:
            sales_candidates[days] = (priority, candidate, str(header))

    sales_columns = sorted(
        (entry[1] for entry in sales_candidates.values()),
        key=lambda item: item["source_column"],
    )

    selected = {
        creator_id_column,
        nickname_column,
        email_column,
        *(item["source_column"] for item in sales_columns),
    }
    ignored = [index for index in range(len(headers)) if index not in selected]
    transforms = {str(creator_id_column): "trim"}
    if nickname_column is not None:
        transforms[str(nickname_column)] = "trim"
    if email_column is not None:
        transforms[str(email_column)] = "extract_email"
    for item in sales_columns:
        transforms[str(item["source_column"])] = "parse_money"

    rule = {
        "header_row": header_row,
        "creator_id_column": creator_id_column,
        "nickname_column": nickname_column,
        "email_column": email_column,
        "sales_columns": sales_columns,
        "ignored_columns": ignored,
        "column_transforms": transforms,
    }
    return validate_rule(rule, rows), warnings


def validate_rule(
    rule: dict[str, Any],
    rows: tuple[tuple[str, ...], ...],
) -> dict[str, Any]:
    errors = sorted(
        Draft202012Validator(IMPORT_RULE_SCHEMA).iter_errors(rule),
        key=lambda error: list(error.path),
    )
    if errors:
        raise RuleValidationError(
            "转换规则格式无效：" + "；".join(error.message for error in errors[:3])
        )
    header_row = int(rule["header_row"])
    if header_row > len(rows):
        raise RuleValidationError("规则指定的表头行超出表格范围。")
    headers = rows[header_row - 1]
    column_count = len(headers)
    referenced: list[int] = [int(rule["creator_id_column"])]
    for optional_key in ("nickname_column", "email_column"):
        if rule[optional_key] is not None:
            referenced.append(int(rule[optional_key]))
    referenced.extend(int(item["source_column"]) for item in rule["sales_columns"])
    referenced.extend(int(index) for index in rule["ignored_columns"])
    try:
        transform_indexes = [
            int(index)
            for index in rule["column_transforms"].keys()
        ]
    except (TypeError, ValueError) as error:
        raise RuleValidationError("转换操作的列索引必须是整数。") from error
    referenced.extend(transform_indexes)
    if any(index >= column_count for index in referenced):
        raise RuleValidationError("转换规则引用了不存在的列。")
    if not str(headers[int(rule["creator_id_column"])]).strip():
        raise RuleValidationError("达人 ID 映射到空表头列。")

    sales_indexes: set[int] = set()
    sales_windows: set[int] = set()
    for item in rule["sales_columns"]:
        index = int(item["source_column"])
        days = int(item["window_days"])
        if index in sales_indexes:
            raise RuleValidationError("同一列不能重复映射为销售额。")
        if days in sales_windows:
            raise RuleValidationError("同一统计周期只能映射一个销售额列。")
        sales_indexes.add(index)
        sales_windows.add(days)
        if item["transform_type"] != "parse_money":
            raise RuleValidationError("销售额列只能使用 parse_money 转换。")
    if int(rule["creator_id_column"]) in sales_indexes:
        raise RuleValidationError("达人 ID 列不能同时作为销售额列。")
    creator_transform = rule["column_transforms"].get(
        str(rule["creator_id_column"]),
        "trim",
    )
    if creator_transform not in {
        "direct_copy",
        "trim",
        "extract_url_last_segment",
    }:
        raise RuleValidationError("达人 ID 列使用了不支持的转换操作。")
    if rule["nickname_column"] is not None:
        nickname_transform = rule["column_transforms"].get(
            str(rule["nickname_column"]),
            "trim",
        )
        if nickname_transform not in {
            "direct_copy",
            "trim",
            "set_null",
            "ignore",
        }:
            raise RuleValidationError("达人昵称列使用了不支持的转换操作。")
    if rule["email_column"] is not None:
        email_transform = rule["column_transforms"].get(
            str(rule["email_column"]),
            "extract_email",
        )
        if email_transform != "extract_email":
            raise RuleValidationError("邮箱列必须使用 extract_email 转换。")
    return copy.deepcopy(rule)


def field_mapping_labels(
    rule: dict[str, Any],
    rows: tuple[tuple[str, ...], ...],
) -> list[str]:
    headers = rows[int(rule["header_row"]) - 1]
    labels = [
        f"表头位置：第 {rule['header_row']} 行",
        f"达人 ID 来源：{headers[int(rule['creator_id_column'])]}",
    ]
    if rule["nickname_column"] is not None:
        labels.append(f"达人昵称来源：{headers[int(rule['nickname_column'])]}")
    if rule["email_column"] is not None:
        labels.append(f"邮箱来源：{headers[int(rule['email_column'])]}")
    for sales_rule in sorted(
        rule["sales_columns"],
        key=lambda item: int(item["window_days"]),
    ):
        labels.append(
            f"{sales_rule['window_days']} 天销售额来源："
            f"{headers[int(sales_rule['source_column'])]}"
        )
    return labels
