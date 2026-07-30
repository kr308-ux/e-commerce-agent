"""Build and revise unconfirmed creator-import previews in memory."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from django.conf import settings

from .ai_rule_advisor import ImportRuleAdvisor
from .import_rules import (
    build_initial_rule,
    field_mapping_labels,
)
from .import_transformer import preview_rows
from .import_types import PreviewState, RuleHistoryEntry
from .preview_store import InMemoryPreviewStore
from .spreadsheet_reader import read_sheet


preview_store = InMemoryPreviewStore(
    ttl_seconds=settings.IMPORT_PREVIEW_TTL_SECONDS,
    max_entries=settings.IMPORT_PREVIEW_MAX_ENTRIES,
)


def create_preview(
    *,
    file_name: str,
    file_bytes: bytes,
    sheet_name: str,
) -> PreviewState:
    sheet = read_sheet(
        file_name=file_name,
        file_bytes=file_bytes,
        sheet_name=sheet_name,
        max_rows=settings.IMPORT_PREVIEW_SCAN_ROWS,
    )
    rule, warnings = build_initial_rule(sheet.rows)
    state = PreviewState(
        preview_id=str(uuid.uuid4()),
        file_name=file_name,
        file_sha256=hashlib.sha256(file_bytes).hexdigest(),
        file_bytes=file_bytes,
        sheet_name=sheet.selected_sheet,
        sheet_names=sheet.sheet_names,
        rows=sheet.rows,
        rule_history=[
            RuleHistoryEntry(
                version=1,
                rule=rule,
                source="SYSTEM",
            )
        ],
        original_sample=[
            list(row)
            for row in sheet.rows[: settings.IMPORT_PREVIEW_ROWS]
        ],
        converted_sample=preview_rows(
            sheet.rows,
            rule,
            limit=settings.IMPORT_PREVIEW_ROWS,
        ),
        field_mappings=field_mapping_labels(rule, sheet.rows),
        warnings=warnings,
    )
    preview_store.put(state)
    return state


def revise_preview(
    *,
    preview_id: str,
    instruction: str,
    advisor: ImportRuleAdvisor | None = None,
) -> PreviewState:
    state = preview_store.get(preview_id)
    proposed = (advisor or ImportRuleAdvisor()).revise_rule(
        instruction=instruction,
        rows=state.rows,
        current_rule=state.current_rule,
        task_id=preview_id,
    )
    state.rule_history.append(
        RuleHistoryEntry(
            version=state.current_version + 1,
            rule=proposed,
            source="AI",
            user_instruction=str(instruction).strip(),
        )
    )
    state.converted_sample = preview_rows(
        state.rows,
        proposed,
        limit=settings.IMPORT_PREVIEW_ROWS,
    )
    state.field_mappings = field_mapping_labels(proposed, state.rows)
    state.warnings = []
    preview_store.put(state)
    return state


def preview_payload(state: PreviewState) -> dict[str, Any]:
    sales_windows = sorted(
        {
            int(window)
            for row in state.converted_sample
            for window in row["sales"].keys()
        }
        | {
            int(item["window_days"])
            for item in state.current_rule["sales_columns"]
        }
    )
    return {
        "success": True,
        "previewId": state.preview_id,
        "fileName": state.file_name,
        "sheetName": state.sheet_name,
        "sheetNames": list(state.sheet_names),
        "fileSha256": state.file_sha256,
        "ruleVersion": state.current_version,
        "originalSample": state.original_sample,
        "convertedSample": state.converted_sample,
        "fieldMappings": state.field_mappings,
        "warnings": state.warnings,
        "salesWindows": sales_windows,
        "ruleHistory": [
            {
                "version": entry.version,
                "source": entry.source,
                "sourceLabel": "系统识别" if entry.source == "SYSTEM" else "大模型修正",
                "userInstruction": entry.user_instruction,
            }
            for entry in state.rule_history
        ],
        "expiresInSeconds": settings.IMPORT_PREVIEW_TTL_SECONDS,
    }
