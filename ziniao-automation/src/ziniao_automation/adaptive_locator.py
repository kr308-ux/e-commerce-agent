"""Candidate-based locator healing with bounded persistent recipes."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence
from urllib.parse import urlparse

from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement


MAX_PERSISTED_RECIPES = 3
MAX_MODEL_CANDIDATES = 12
MIN_MODEL_CONFIDENCE = 0.65


@dataclass(frozen=True)
class LocatorRecipe:
    locator_id: str
    page_key: str
    target_key: str
    strategy: str
    value: str
    kind: str
    source: str
    confidence: float
    status: str
    created_at: str
    last_success_at: str
    success_count: int = 0
    structural_failure_count: int = 0
    consecutive_structural_failures: int = 0

    @property
    def by(self) -> str:
        return (
            By.CSS_SELECTOR
            if self.strategy == "css"
            else By.XPATH
        )


@dataclass(frozen=True)
class ElementCandidate:
    candidate_id: str
    source: str
    role: str
    name: str
    tag: str = ""
    states: dict[str, Any] = field(default_factory=dict)
    context: str = ""
    frame_id: str = ""
    backend_dom_node_id: int | None = None
    immediate_strategy: str = ""
    immediate_value: str = ""
    fingerprint: dict[str, Any] = field(default_factory=dict)
    local_score: float = 0

    def to_model_dict(self) -> dict[str, Any]:
        return {
            "candidateId": self.candidate_id,
            "source": self.source,
            "role": self.role,
            "name": self.name,
            "tag": self.tag,
            "states": self.states,
            "context": self.context,
        }


@dataclass(frozen=True)
class CandidateSelection:
    decision: str
    candidate_id: str = ""
    confidence: float = 0
    reason: str = ""


@dataclass
class AdaptiveLocateResult:
    element: WebElement | None
    event: dict[str, Any]
    pending_recipes: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PersistedLocatorAttempt:
    purpose: str
    page_key: str
    target_key: str
    attempted_selector_count: int
    recipes: tuple[LocatorRecipe, ...]
    attempted_locator_ids: list[str] = field(default_factory=list)
    completed: bool = False


CandidateSelector = Callable[
    [
        str,
        str,
        str,
        Sequence[ElementCandidate],
    ],
    CandidateSelection | None,
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def page_key(url: object) -> str:
    parsed = urlparse(str(url or ""))
    hostname = str(parsed.hostname or "").lower()
    path = re.sub(r"/+", "/", str(parsed.path or "/")).rstrip("/") or "/"
    return f"{hostname}{path}"


def target_key(purpose: object) -> str:
    normalized = re.sub(r"\s+", " ", str(purpose or "")).strip().casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


class AdaptiveLocatorStore:
    """SQLite-backed recipe cache with scoring and a three-entry bound."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_recipes: int = MAX_PERSISTED_RECIPES,
    ) -> None:
        self.path = Path(path)
        self.max_recipes = max(1, int(max_recipes))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS locator_recipes (
                    locator_id TEXT PRIMARY KEY,
                    page_key TEXT NOT NULL,
                    target_key TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    value TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_success_at TEXT NOT NULL,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    structural_failure_count INTEGER NOT NULL DEFAULT 0,
                    consecutive_structural_failures INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(page_key, target_key, strategy, value)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS locator_recipe_lookup
                ON locator_recipes(page_key, target_key, status)
                """
            )

    @staticmethod
    def _recipe(row: sqlite3.Row) -> LocatorRecipe:
        return LocatorRecipe(**dict(row))

    @staticmethod
    def _score(recipe: LocatorRecipe) -> float:
        attempts = (
            recipe.success_count
            + recipe.structural_failure_count
        )
        success_rate = (
            recipe.success_count / attempts
            if attempts
            else 0.5
        )
        try:
            last_success = datetime.fromisoformat(
                recipe.last_success_at
            )
            age_days = max(
                (
                    datetime.now(timezone.utc) - last_success
                ).total_seconds()
                / 86400,
                0,
            )
        except (TypeError, ValueError):
            age_days = 365
        recency = max(0, 1 - age_days / 30)
        stability = {
            "stable_attribute": 1.0,
            "semantic": 0.9,
            "icon": 0.85,
            "contextual": 0.7,
        }.get(recipe.kind, 0.5)
        status_bonus = 0.08 if recipe.status == "active" else 0
        return (
            success_rate * 0.45
            + recency * 0.25
            + stability * 0.20
            + recipe.confidence * 0.10
            + status_bonus
            - recipe.consecutive_structural_failures * 0.15
        )

    def ranked(
        self,
        page: str,
        target: str,
    ) -> list[LocatorRecipe]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM locator_recipes
                WHERE page_key = ? AND target_key = ?
                  AND status != 'disabled'
                """,
                (page, target),
            ).fetchall()
        recipes = [self._recipe(row) for row in rows]
        recipes.sort(
            key=lambda recipe: (
                self._score(recipe),
                recipe.last_success_at,
            ),
            reverse=True,
        )
        return recipes[: self.max_recipes]

    def learn(
        self,
        *,
        page: str,
        target: str,
        strategy: str,
        value: str,
        kind: str,
        source: str,
        confidence: float,
    ) -> LocatorRecipe | None:
        now = _now_iso()
        locator_id = uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT locator_id FROM locator_recipes
                WHERE page_key = ? AND target_key = ?
                  AND strategy = ? AND value = ?
                """,
                (page, target, strategy, value),
            ).fetchone()
            if existing is not None:
                locator_id = str(existing["locator_id"])
                connection.execute(
                    """
                    UPDATE locator_recipes
                    SET last_success_at = ?,
                        success_count = success_count + 1,
                        consecutive_structural_failures = 0,
                        confidence = MAX(confidence, ?),
                        status = CASE
                            WHEN success_count + 1 >= 2
                            THEN 'active'
                            ELSE status
                        END
                    WHERE locator_id = ?
                    """,
                    (now, min(max(confidence, 0), 1), locator_id),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO locator_recipes (
                        locator_id, page_key, target_key,
                        strategy, value, kind, source,
                        confidence, status, created_at,
                        last_success_at, success_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        locator_id,
                        page,
                        target,
                        strategy,
                        value,
                        kind,
                        source,
                        min(max(confidence, 0), 1),
                        "probation",
                        now,
                        now,
                    ),
                )
            rows = connection.execute(
                """
                SELECT * FROM locator_recipes
                WHERE page_key = ? AND target_key = ?
                """,
                (page, target),
            ).fetchall()
            recipes = [self._recipe(row) for row in rows]
            recipes.sort(
                key=self._score,
                reverse=True,
            )
            keep_ids = {
                recipe.locator_id
                for recipe in recipes[: self.max_recipes]
            }
            for recipe in recipes[self.max_recipes :]:
                connection.execute(
                    "DELETE FROM locator_recipes WHERE locator_id = ?",
                    (recipe.locator_id,),
                )
            row = connection.execute(
                "SELECT * FROM locator_recipes WHERE locator_id = ?",
                (locator_id,),
            ).fetchone()
        if row is None or locator_id not in keep_ids:
            return None
        return self._recipe(row)

    def record_success(self, locator_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE locator_recipes
                SET last_success_at = ?,
                    success_count = success_count + 1,
                    consecutive_structural_failures = 0,
                    status = CASE
                        WHEN success_count + 1 >= 2
                        THEN 'active'
                        ELSE status
                    END
                WHERE locator_id = ?
                """,
                (_now_iso(), locator_id),
            )

    def record_structural_failure(self, locator_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE locator_recipes
                SET structural_failure_count =
                        structural_failure_count + 1,
                    consecutive_structural_failures =
                        consecutive_structural_failures + 1,
                    status = CASE
                        WHEN consecutive_structural_failures + 1 >= 3
                        THEN 'disabled'
                        ELSE status
                    END
                WHERE locator_id = ?
                """,
                (locator_id,),
            )

    def export(
        self,
        page: str,
        target: str,
    ) -> list[dict[str, Any]]:
        return [asdict(recipe) for recipe in self.ranked(page, target)]


def _value(payload: Any) -> Any:
    if isinstance(payload, dict) and "value" in payload:
        return payload.get("value")
    return payload


def _normalize(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _infer_roles(purpose: str) -> set[str]:
    normalized = purpose.casefold()
    roles: set[str] = set()
    mapping = (
        (("搜索框", "输入框", "输入区", "composer"), {"textbox", "searchbox"}),
        (("开关", "switch"), {"switch", "button"}),
        (("标签页", "页签", "tab"), {"tab"}),
        (("候选项", "option"), {"menuitem", "option"}),
        (("结果卡", "卡片", "结果行"), {"row", "listitem", "link"}),
        (("单选", "radio"), {"radio"}),
        (("弹窗", "对话框", "dialog"), {"dialog"}),
        (
            ("按钮", "私信", "发送", "邀请", "关闭", "打开", "入口"),
            {"button", "link", "menuitem"},
        ),
    )
    for keywords, inferred in mapping:
        if any(keyword.casefold() in normalized for keyword in keywords):
            roles.update(inferred)
    return roles


PURPOSE_KEYWORDS = (
    ("affiliate", ("affiliate", "联盟")),
    ("查找达人", ("查找达人", "寻找达人", "find creators")),
    ("ai 搜索", ("ai 搜索", "ai search")),
    ("搜索框", ("搜索", "search", "姓名")),
    ("私信", ("私信", "message", "消息")),
    ("打开聊天", ("打开", "open", "launch")),
    ("发送其他邀请", ("发送其他邀请", "send another invitation")),
    ("定向合作", ("定向合作", "target collaboration")),
    ("发送按钮", ("发送", "send")),
    ("进行中", ("进行中", "in progress")),
    ("创建新邀请", ("创建新邀请", "create new invitation")),
    ("最终", ("邀请", "invite")),
)


def _infer_keywords(purpose: str) -> tuple[str, ...]:
    normalized = purpose.casefold()
    output: list[str] = []
    for marker, alternatives in PURPOSE_KEYWORDS:
        if marker.casefold() in normalized:
            output.extend(alternatives)
    return tuple(dict.fromkeys(output))


def _candidate_score(
    *,
    role: str,
    name: str,
    purpose: str,
    enabled: bool,
) -> float:
    inferred_roles = _infer_roles(purpose)
    keywords = _infer_keywords(purpose)
    normalized_role = role.replace(" ", "").casefold()
    normalized_name = name.casefold()
    score = 0.0
    if inferred_roles:
        if normalized_role in inferred_roles:
            score += 4
        else:
            score -= 2
    if keywords:
        hits = sum(keyword.casefold() in normalized_name for keyword in keywords)
        score += hits * 3
        if hits == 0 and inferred_roles:
            score -= 1
    if enabled:
        score += 1
    if name:
        score += 0.5
    return score


def collect_ax_candidates(
    driver: WebDriver,
    *,
    purpose: str,
    limit: int = MAX_MODEL_CANDIDATES,
) -> tuple[str, list[ElementCandidate]]:
    try:
        frame_payload = driver.execute_cdp_cmd("Page.getFrameTree", {})
    except Exception:
        frame_payload = {}
    frame_tree = (
        frame_payload.get("frameTree")
        if isinstance(frame_payload, dict)
        else {}
    )
    frame_ids: list[str] = []

    def frames(entry: object) -> None:
        if not isinstance(entry, dict):
            return
        frame = entry.get("frame")
        if isinstance(frame, dict) and frame.get("id"):
            frame_ids.append(str(frame["id"]))
        for child in entry.get("childFrames") or []:
            frames(child)

    frames(frame_tree)
    if not frame_ids:
        frame_ids = [""]
    raw_nodes: list[tuple[str, dict[str, Any]]] = []
    for frame_id in frame_ids:
        params = {"frameId": frame_id} if frame_id else {}
        try:
            result = driver.execute_cdp_cmd(
                "Accessibility.getFullAXTree",
                params,
            )
        except Exception:
            continue
        for node in result.get("nodes") or []:
            if isinstance(node, dict):
                raw_nodes.append((frame_id, node))
    parent_context = {
        str(node.get("nodeId") or ""): _normalize(_value(node.get("name")))
        for _frame_id, node in raw_nodes
    }
    ranked: list[ElementCandidate] = []
    for frame_id, node in raw_nodes:
        if node.get("ignored") or not node.get("backendDOMNodeId"):
            continue
        role = _normalize(_value(node.get("role")))
        name = _normalize(_value(node.get("name")))
        properties = {
            str(item.get("name") or ""): _value(item.get("value"))
            for item in node.get("properties") or []
            if isinstance(item, dict) and item.get("name")
        }
        enabled = properties.get("disabled") is not True
        score = _candidate_score(
            role=role,
            name=name,
            purpose=purpose,
            enabled=enabled,
        )
        if score <= 0:
            continue
        context = parent_context.get(str(node.get("parentId") or ""), "")
        ranked.append(
            ElementCandidate(
                candidate_id="",
                source="ax",
                role=role,
                name=name,
                states={
                    key: properties[key]
                    for key in (
                        "checked",
                        "disabled",
                        "expanded",
                        "selected",
                    )
                    if key in properties
                },
                context=context[:240],
                frame_id=frame_id,
                backend_dom_node_id=int(node["backendDOMNodeId"]),
                local_score=score,
            )
        )
    ranked.sort(
        key=lambda candidate: (
            candidate.local_score,
            bool(candidate.name),
        ),
        reverse=True,
    )
    selected = [
        ElementCandidate(
            **{
                **asdict(candidate),
                "candidate_id": f"ax-{index:03d}",
            }
        )
        for index, candidate in enumerate(ranked[:limit], start=1)
    ]
    digest = hashlib.sha256(
        json.dumps(
            [candidate.to_model_dict() for candidate in selected],
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:24]
    return f"ax-{digest}", selected


def collect_dom_candidates(
    snapshot: dict[str, Any],
    *,
    purpose: str,
    limit: int = MAX_MODEL_CANDIDATES,
) -> tuple[str, list[ElementCandidate]]:
    ranked: list[ElementCandidate] = []
    for raw in snapshot.get("elements") or []:
        if not isinstance(raw, dict):
            continue
        tag = _normalize(raw.get("tag")).lower()
        role = _normalize(raw.get("role")).lower()
        semantic_role = role
        if not semantic_role:
            semantic_role = {
                "a": "link",
                "button": "button",
                "input": (
                    "radio"
                    if raw.get("type") == "radio"
                    else "textbox"
                ),
                "textarea": "textbox",
                "select": "combobox",
            }.get(tag, "")
        name = _normalize(
            raw.get("ariaLabel")
            or raw.get("text")
            or raw.get("placeholder")
            or raw.get("title")
        )
        score = _candidate_score(
            role=semantic_role,
            name=name,
            purpose=purpose,
            enabled=bool(raw.get("enabled", True)),
        )
        if score <= 0:
            continue
        ranked.append(
            ElementCandidate(
                candidate_id="",
                source="dom",
                role=semantic_role,
                name=name,
                tag=tag,
                states={"enabled": bool(raw.get("enabled", True))},
                context=_normalize(raw.get("parentText"))[:240],
                immediate_strategy="css",
                immediate_value=str(raw.get("cssPath") or ""),
                fingerprint={
                    key: raw.get(key)
                    for key in (
                        "tag",
                        "role",
                        "type",
                        "id",
                        "name",
                        "placeholder",
                        "ariaLabel",
                        "title",
                        "dataE2e",
                        "href",
                        "text",
                    )
                },
                local_score=score,
            )
        )
    ranked.sort(
        key=lambda candidate: (
            candidate.local_score,
            bool(candidate.name),
        ),
        reverse=True,
    )
    selected = [
        ElementCandidate(
            **{
                **asdict(candidate),
                "candidate_id": f"dom-{index:03d}",
            }
        )
        for index, candidate in enumerate(ranked[:limit], start=1)
    ]
    digest = hashlib.sha256(
        json.dumps(
            [candidate.to_model_dict() for candidate in selected],
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:24]
    return f"dom-{digest}", selected


def _unique_visible(
    driver: WebDriver,
    *,
    by: str,
    value: str,
) -> WebElement | None:
    matches: dict[str, WebElement] = {}
    try:
        elements = driver.find_elements(by, value)
    except Exception:
        return None
    for element in elements:
        try:
            if element.is_displayed() and element.is_enabled():
                matches[element.id] = element
        except StaleElementReferenceException:
            continue
    if len(matches) != 1:
        return None
    return next(iter(matches.values()))


def resolve_candidate(
    driver: WebDriver,
    candidate: ElementCandidate,
) -> WebElement | None:
    if candidate.source == "dom":
        if not candidate.immediate_value:
            return None
        return _unique_visible(
            driver,
            by=By.CSS_SELECTOR,
            value=candidate.immediate_value,
        )
    if candidate.backend_dom_node_id is None:
        return None
    token = uuid.uuid4().hex
    try:
        resolved = driver.execute_cdp_cmd(
            "DOM.resolveNode",
            {"backendNodeId": candidate.backend_dom_node_id},
        )
        object_id = str(
            (resolved.get("object") or {}).get("objectId") or ""
        )
        if not object_id:
            return None
        result = driver.execute_cdp_cmd(
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "functionDeclaration": (
                    "function(token) {"
                    " if (!this || !this.setAttribute) return false;"
                    " this.setAttribute("
                    "'data-ziniao-adaptive-candidate', token);"
                    " return true;"
                    "}"
                ),
                "arguments": [{"value": token}],
                "returnByValue": True,
                "silent": True,
            },
        )
        if result.get("result", {}).get("value") is not True:
            return None
        element = _unique_visible(
            driver,
            by=By.CSS_SELECTOR,
            value=f'[data-ziniao-adaptive-candidate="{token}"]',
        )
        if element is not None:
            driver.execute_script(
                "arguments[0].removeAttribute("
                "'data-ziniao-adaptive-candidate');",
                element,
            )
        return element
    except Exception:
        return None


def _css_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _xpath_literal(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ", \"'\", ".join(f"'{part}'" for part in parts) + ")"


def _persistent_text(value: str) -> bool:
    normalized = _normalize(value)
    return (
        0 < len(normalized) <= 80
        and "@" not in normalized
        and not re.search(r"\d{5,}", normalized)
    )


def synthesize_recipes(
    driver: WebDriver,
    element: WebElement,
    *,
    page: str,
    target: str,
    source: str,
    confidence: float,
) -> list[dict[str, Any]]:
    try:
        fingerprint = driver.execute_script(
            """
            const e = arguments[0];
            const icon = e.querySelector('svg[class], [class*="icon"]');
            return {
              tag: String(e.tagName || '').toLowerCase(),
              id: e.id || '',
              dataE2e: e.getAttribute('data-e2e') || '',
              href: e.getAttribute('href') || '',
              ariaLabel: e.getAttribute('aria-label') || '',
              name: e.getAttribute('name') || '',
              type: e.getAttribute('type') || '',
              placeholder: e.getAttribute('placeholder') || '',
              role: e.getAttribute('role') || '',
              text: String(e.innerText || e.textContent || '')
                .replace(/\\s+/g, ' ').trim(),
              iconClasses: icon
                ? String(icon.getAttribute('class') || '')
                    .replace(/\\s+/g, ' ').trim()
                : ''
            };
            """,
            element,
        )
    except Exception:
        return []
    if not isinstance(fingerprint, dict):
        return []
    tag = str(fingerprint.get("tag") or "*")
    proposals: list[tuple[str, str, str]] = []
    for attribute, key in (
        ("id", "id"),
        ("data-e2e", "dataE2e"),
        ("href", "href"),
        ("aria-label", "ariaLabel"),
        ("name", "name"),
        ("placeholder", "placeholder"),
    ):
        value = _normalize(fingerprint.get(key))
        if value:
            proposals.append(
                (
                    "css",
                    f"{tag}[{attribute}={_css_string(value)}]",
                    "stable_attribute",
                )
            )
    icon_classes = [
        item
        for item in _normalize(
            fingerprint.get("iconClasses")
        ).split(" ")
        if item
        and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", item)
        and not re.search(r"[a-f0-9]{8,}", item, re.I)
    ]
    if icon_classes and tag == "button":
        icon_selector = "." + ".".join(icon_classes[:3])
        proposals.append(
            ("css", f"button:has({icon_selector})", "icon")
        )
    text = _normalize(fingerprint.get("text"))
    if _persistent_text(text):
        proposals.append(
            (
                "xpath",
                f"//{tag}[normalize-space()={_xpath_literal(text)}]",
                "semantic",
            )
        )
    role = _normalize(fingerprint.get("role"))
    aria_label = _normalize(fingerprint.get("ariaLabel"))
    if role and aria_label:
        proposals.append(
            (
                "xpath",
                f"//*[@role={_xpath_literal(role)} and "
                f"@aria-label={_xpath_literal(aria_label)}]",
                "semantic",
            )
        )
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for strategy, value, kind in proposals:
        identity = (strategy, value)
        if identity in seen:
            continue
        seen.add(identity)
        match = _unique_visible(
            driver,
            by=(
                By.CSS_SELECTOR
                if strategy == "css"
                else By.XPATH
            ),
            value=value,
        )
        if match is None or match.id != element.id:
            continue
        output.append(
            {
                "page": page,
                "target": target,
                "strategy": strategy,
                "value": value,
                "kind": kind,
                "source": source,
                "confidence": confidence,
            }
        )
    output.sort(
        key=lambda recipe: {
            "stable_attribute": 0,
            "semantic": 1,
            "icon": 2,
            "contextual": 3,
        }.get(str(recipe["kind"]), 9)
    )
    return output[:MAX_PERSISTED_RECIPES]


class AdaptiveLocatorEngine:
    """Try learned recipes, then AX candidates, then scoped DOM candidates."""

    def __init__(
        self,
        store: AdaptiveLocatorStore,
        *,
        max_candidates: int = MAX_MODEL_CANDIDATES,
    ) -> None:
        self.store = store
        self.max_candidates = max(1, int(max_candidates))

    def begin_persisted_attempt(
        self,
        driver: WebDriver,
        *,
        purpose: str,
        attempted_selector_count: int = 0,
    ) -> PersistedLocatorAttempt:
        current_page = page_key(driver.current_url)
        current_target = target_key(purpose)
        return PersistedLocatorAttempt(
            purpose=purpose,
            page_key=current_page,
            target_key=current_target,
            attempted_selector_count=attempted_selector_count,
            recipes=tuple(
                self.store.ranked(current_page, current_target)
            ),
        )

    def probe_persisted(
        self,
        driver: WebDriver,
        attempt: PersistedLocatorAttempt,
    ) -> AdaptiveLocateResult:
        event: dict[str, Any] = {
            "kind": "adaptive_locator",
            "purpose": attempt.purpose,
            "pageKey": attempt.page_key,
            "targetKey": attempt.target_key,
            "attemptedSelectorCount": attempt.attempted_selector_count,
            "modelUsed": False,
        }
        if attempt.completed:
            event.update(
                {
                    "status": "persisted_attempt_completed",
                    "attemptedLocatorIds": list(
                        attempt.attempted_locator_ids
                    ),
                }
            )
            return AdaptiveLocateResult(element=None, event=event)
        for recipe in attempt.recipes:
            if recipe.locator_id not in attempt.attempted_locator_ids:
                attempt.attempted_locator_ids.append(recipe.locator_id)
            element = _unique_visible(
                driver,
                by=recipe.by,
                value=recipe.value,
            )
            if element is None:
                continue
            self.store.record_success(recipe.locator_id)
            attempt.completed = True
            event.update(
                {
                    "status": "persisted_recipe_validated",
                    "locatorId": recipe.locator_id,
                    "locatorKind": recipe.kind,
                    "attemptedLocatorIds": list(
                        attempt.attempted_locator_ids
                    ),
                }
            )
            return AdaptiveLocateResult(element=element, event=event)
        event.update(
            {
                "status": "no_persisted_recipe_match",
                "attemptedLocatorIds": list(
                    attempt.attempted_locator_ids
                ),
            }
        )
        return AdaptiveLocateResult(element=None, event=event)

    def finalize_persisted_timeout(
        self,
        attempt: PersistedLocatorAttempt,
    ) -> dict[str, Any]:
        """Record each recipe miss once after the complete wait expires."""
        recorded: list[str] = []
        if not attempt.completed:
            for locator_id in attempt.attempted_locator_ids:
                self.store.record_structural_failure(locator_id)
                recorded.append(locator_id)
            attempt.completed = True
        return {
            "kind": "adaptive_locator",
            "purpose": attempt.purpose,
            "pageKey": attempt.page_key,
            "targetKey": attempt.target_key,
            "status": "persisted_timeout_finalized",
            "structuralFailureLocatorIds": recorded,
            "structuralFailureCount": len(recorded),
            "modelUsed": False,
        }

    def locate_persisted(
        self,
        driver: WebDriver,
        *,
        purpose: str,
        attempted_selector_count: int = 0,
    ) -> AdaptiveLocateResult:
        """One non-penalizing probe for callers without a wait session."""
        attempt = self.begin_persisted_attempt(
            driver,
            purpose=purpose,
            attempted_selector_count=attempted_selector_count,
        )
        return self.probe_persisted(driver, attempt)

    def locate(
        self,
        driver: WebDriver,
        *,
        purpose: str,
        attempted_selectors: Iterable[tuple[str, str]],
        dom_snapshot: Callable[[], dict[str, Any]],
        select_candidate: CandidateSelector,
        include_persisted: bool = True,
    ) -> AdaptiveLocateResult:
        selector_list = tuple(attempted_selectors)
        current_page = page_key(driver.current_url)
        current_target = target_key(purpose)
        event: dict[str, Any] = {
            "kind": "adaptive_locator",
            "purpose": purpose,
            "pageKey": current_page,
            "targetKey": current_target,
            "attemptedSelectorCount": len(selector_list),
            "modelUsed": False,
        }
        if include_persisted:
            persisted = self.locate_persisted(
                driver,
                purpose=purpose,
                attempted_selector_count=len(selector_list),
            )
            if persisted.element is not None:
                return persisted
            event = dict(persisted.event)
        attempts: list[dict[str, Any]] = []
        saw_candidates = False
        last_status = "no_local_candidates"
        for source in ("ax", "dom"):
            if source == "ax":
                snapshot_id, candidates = collect_ax_candidates(
                    driver,
                    purpose=purpose,
                    limit=self.max_candidates,
                )
            else:
                snapshot_id, candidates = collect_dom_candidates(
                    dom_snapshot(),
                    purpose=purpose,
                    limit=self.max_candidates,
                )
            attempt: dict[str, Any] = {
                "snapshotId": snapshot_id,
                "candidateSource": source,
                "candidateCount": len(candidates),
                "candidates": [
                    candidate.to_model_dict()
                    for candidate in candidates
                ],
            }
            attempts.append(attempt)
            if not candidates:
                attempt["status"] = "no_local_candidates"
                continue
            saw_candidates = True
            selection = select_candidate(
                snapshot_id,
                purpose,
                current_page,
                candidates,
            )
            if selection is None or selection.decision != "select":
                last_status = "model_stopped"
                attempt.update(
                    {
                        "status": last_status,
                        "decision": (
                            selection.decision if selection else "stop"
                        ),
                        "reason": (
                            selection.reason if selection else ""
                        ),
                    }
                )
                continue
            if selection.confidence < MIN_MODEL_CONFIDENCE:
                last_status = "low_confidence"
                attempt.update(
                    {
                        "status": last_status,
                        "decision": selection.decision,
                        "reason": selection.reason,
                        "confidence": selection.confidence,
                    }
                )
                continue
            by_id = {
                candidate.candidate_id: candidate
                for candidate in candidates
            }
            selected = by_id.get(selection.candidate_id)
            if selected is None:
                last_status = "invalid_candidate_id"
                attempt.update(
                    {
                        "status": last_status,
                        "selectedCandidateId": selection.candidate_id,
                    }
                )
                continue
            element = resolve_candidate(driver, selected)
            if element is None:
                last_status = "selected_candidate_stale"
                attempt.update(
                    {
                        "status": last_status,
                        "selectedCandidateId": selected.candidate_id,
                    }
                )
                continue
            pending = synthesize_recipes(
                driver,
                element,
                page=current_page,
                target=current_target,
                source=f"{source}_candidate",
                confidence=selection.confidence,
            )
            attempt.update(
                {
                    "status": "candidate_validated",
                    "decision": selection.decision,
                    "reason": selection.reason,
                    "confidence": selection.confidence,
                    "selectedCandidateId": selected.candidate_id,
                    "pendingRecipeCount": len(pending),
                }
            )
            event.update(
                {
                    "status": "candidate_validated",
                    "snapshotId": snapshot_id,
                    "candidateSource": source,
                    "candidateCount": len(candidates),
                    "selectedCandidateId": selected.candidate_id,
                    "confidence": selection.confidence,
                    "modelUsed": True,
                    "attempts": attempts,
                }
            )
            return AdaptiveLocateResult(
                element=element,
                event=event,
                pending_recipes=pending,
            )
        event.update(
            {
                "status": (
                    last_status
                    if saw_candidates
                    else "no_local_candidates"
                ),
                "modelUsed": saw_candidates,
                "attempts": attempts,
            }
        )
        return AdaptiveLocateResult(element=None, event=event)
