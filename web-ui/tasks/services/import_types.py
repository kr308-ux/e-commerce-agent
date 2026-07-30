"""Typed values shared by creator spreadsheet import services."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class SheetData:
    sheet_names: tuple[str, ...]
    selected_sheet: str
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class RowIssue:
    code: str
    field: str
    message: str
    fatal: bool = False


@dataclass(frozen=True)
class ConvertedRow:
    row_number: int
    creator_id: str
    nickname: str
    email: str
    sales: dict[int, Decimal]
    issues: tuple[RowIssue, ...] = ()

    @property
    def status(self) -> str:
        if any(issue.fatal for issue in self.issues):
            return "FAILED"
        if self.issues:
            return "PARTIAL_SUCCESS"
        return "SUCCESS"

    def as_preview_dict(self) -> dict[str, Any]:
        return {
            "rowNumber": self.row_number,
            "creatorId": self.creator_id,
            "nickname": self.nickname,
            "email": self.email,
            "sales": {
                str(days): format(amount, "f")
                for days, amount in sorted(self.sales.items())
            },
            "status": self.status,
            "warnings": [issue.message for issue in self.issues],
        }


@dataclass
class RuleHistoryEntry:
    version: int
    rule: dict[str, Any]
    source: str
    user_instruction: str = ""


@dataclass
class PreviewState:
    preview_id: str
    file_name: str
    file_sha256: str
    file_bytes: bytes
    sheet_name: str
    sheet_names: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    rule_history: list[RuleHistoryEntry]
    original_sample: list[list[str]]
    converted_sample: list[dict[str, Any]]
    field_mappings: list[str]
    warnings: list[str] = field(default_factory=list)

    @property
    def current_rule(self) -> dict[str, Any]:
        return self.rule_history[-1].rule

    @property
    def current_version(self) -> int:
        return self.rule_history[-1].version

