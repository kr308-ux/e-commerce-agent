"""Verify AXTree coverage across the creator-contact browser workflow.

This diagnostic intentionally does not import or call CreatorContactWorkflow.
It attaches to the reusable store browser, performs only the navigation needed
to expose each page shape, and writes a JSON report comparing Chrome AXTree
nodes with the visible interactive DOM.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from selenium.common.exceptions import (
    JavascriptException,
    NoSuchFrameException,
    StaleElementReferenceException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement

from ..browser_connection import connect_reusable_store
from ..config import ZiniaoSettings
from ..keyboard import replace_element_text


Locator = tuple[str, str]
DYNAMIC_TARGET_IDS = frozenset({"T-05", "T-06"})
DEFAULT_PAGE_IDS = (
    "store_home",
    "find_creators",
    "creator_detail",
    "chat_tab",
    "invitation_modal",
)
SEARCH_INPUT_SELECTORS: tuple[Locator, ...] = (
    (By.CSS_SELECTOR, "input.core-input[type='text']"),
    (
        By.CSS_SELECTOR,
        "input[placeholder*='搜索姓名'], input[placeholder*='Search' i]",
    ),
    (
        By.XPATH,
        "//input[@type='text' and "
        "(contains(@placeholder, '搜索') or "
        "contains(translate(@placeholder, 'SEARCH', 'search'), 'search'))]",
    ),
)
AI_SWITCH_SELECTORS: tuple[Locator, ...] = (
    (By.CSS_SELECTOR, "button[role='switch']"),
    (By.CSS_SELECTOR, "[role='switch']"),
)
FIND_CREATORS_ENTRY_SELECTORS: tuple[Locator, ...] = (
    (By.CSS_SELECTOR, 'a[href*="/connection/creator"]'),
    (
        By.XPATH,
        "//*[self::a or self::button or @role='link' "
        "or @role='button' or @role='menuitem']"
        "[contains(normalize-space(.), 'Find creators') "
        "or contains(normalize-space(.), '查找达人') "
        "or contains(normalize-space(.), '寻找达人')]",
    ),
)
AFFILIATE_CENTER_ENTRY_SELECTORS: tuple[Locator, ...] = (
    (
        By.XPATH,
        "//button[contains(normalize-space(.), '前往联盟中心首页') "
        "or contains(normalize-space(.), 'Go to Affiliate Center') "
        "or contains(normalize-space(.), 'Go to affiliate center')]",
    ),
)
CHAT_READY_SELECTORS: tuple[Locator, ...] = (
    (
        By.CSS_SELECTOR,
        "textarea, [contenteditable='true'], "
        "input[placeholder*='发送消息'], "
        "input[placeholder*='message' i]",
    ),
    (
        By.XPATH,
        "//*[@role='tab' and "
        "(contains(normalize-space(.), '定向合作') "
        "or contains(normalize-space(.), 'Target collaboration') "
        "or contains(normalize-space(.), 'Target Collaboration'))]",
    ),
)
MARKABLE_AX_ROLES = frozenset(
    {
        "application",
        "article",
        "button",
        "cell",
        "checkbox",
        "columnheader",
        "combobox",
        "dialog",
        "feed",
        "form",
        "grid",
        "gridcell",
        "group",
        "heading",
        "link",
        "list",
        "listbox",
        "listitem",
        "menu",
        "menubar",
        "menuitem",
        "menuitemcheckbox",
        "menuitemradio",
        "option",
        "presentation",
        "progressbar",
        "radio",
        "radiogroup",
        "region",
        "row",
        "rowgroup",
        "rowheader",
        "scrollbar",
        "searchbox",
        "separator",
        "slider",
        "spinbutton",
        "switch",
        "tab",
        "table",
        "tablist",
        "tabpanel",
        "textbox",
        "toolbar",
        "tree",
        "treegrid",
        "treeitem",
    }
)


@dataclass(frozen=True)
class TargetSpec:
    target_id: str
    description: str
    roles: tuple[str, ...]
    name_keywords: tuple[str, ...] = ()
    tag_hints: tuple[str, ...] = ()
    text_hints: tuple[str, ...] = ()
    dom_selectors: tuple[Locator, ...] = ()
    state_checks: dict[str, Any] | None = None
    dynamic: bool = False


@dataclass(frozen=True)
class PageSpec:
    page_id: str
    description: str
    url_markers: tuple[str, ...]
    exclude_url_markers: tuple[str, ...]
    requires_navigation: bool
    navigation_action: str | None
    known_targets: tuple[TargetSpec, ...]
    min_visible_elements: int


@dataclass(frozen=True)
class AXNode:
    node_id: str
    frame_id: str
    backend_dom_node_id: int | None
    role: str
    name: str
    ignored: bool
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class PageSnapshot:
    phase: str
    url: str
    frame_count: int
    frame_errors: list[dict[str, str]]
    ax_nodes: list[AXNode]
    dom_elements: list[dict[str, Any]]
    dom_targets: dict[str, dict[str, Any] | None]
    marking: dict[str, Any]


def _target(
    target_id: str,
    description: str,
    *,
    roles: Sequence[str],
    name_keywords: Sequence[str] = (),
    tag_hints: Sequence[str] = (),
    text_hints: Sequence[str] = (),
    dom_selectors: Sequence[Locator] = (),
    state_checks: dict[str, Any] | None = None,
    dynamic: bool = False,
) -> TargetSpec:
    return TargetSpec(
        target_id=target_id,
        description=description,
        roles=tuple(roles),
        name_keywords=tuple(name_keywords),
        tag_hints=tuple(tag_hints),
        text_hints=tuple(text_hints),
        dom_selectors=tuple(dom_selectors),
        state_checks=state_checks,
        dynamic=dynamic,
    )


PAGE_SPECS: tuple[PageSpec, ...] = (
    PageSpec(
        page_id="store_home",
        description="店铺首页",
        url_markers=("seller.tiktok", "tiktokshop"),
        exclude_url_markers=(
            "/login",
            "/signin",
            "/affiliate",
            "/connection",
        ),
        requires_navigation=False,
        navigation_action=None,
        known_targets=(
            _target(
                "T-01",
                "联盟入口",
                roles=("link", "menuitem"),
                name_keywords=("affiliate", "联盟"),
                tag_hints=("a",),
                dom_selectors=(
                    (By.CSS_SELECTOR, 'a[href*="/affiliate"]'),
                    (
                        By.XPATH,
                        "//*[@role='menuitem' and "
                        "(contains(normalize-space(.), 'Affiliate') or "
                        "contains(normalize-space(.), '联盟'))]",
                    ),
                ),
            ),
            _target(
                "T-02",
                "查找达人入口",
                roles=("link", "heading", "menuitem"),
                name_keywords=("Find creators", "查找达人", "寻找达人"),
                tag_hints=("a", "h1"),
                dom_selectors=(
                    (
                        By.CSS_SELECTOR,
                        'a[href*="/connection/creator"]',
                    ),
                    (
                        By.XPATH,
                        "//*[self::a or self::h1 or self::h2 "
                        "or @role='menuitem']"
                        "[contains(normalize-space(.), 'Find creators') "
                        "or contains(normalize-space(.), '查找达人') "
                        "or contains(normalize-space(.), '寻找达人')]",
                    ),
                ),
            ),
        ),
        min_visible_elements=5,
    ),
    PageSpec(
        page_id="find_creators",
        description="查找达人页",
        url_markers=("/connection/creator",),
        exclude_url_markers=("/detail",),
        requires_navigation=True,
        navigation_action="activate_existing",
        known_targets=(
            _target(
                "T-03",
                "AI 搜索开关",
                roles=("switch", "button"),
                name_keywords=("AI", "搜索", "Search"),
                tag_hints=("button",),
                dom_selectors=AI_SWITCH_SELECTORS,
                state_checks={"checked": True},
            ),
            _target(
                "T-04",
                "搜索输入框",
                roles=("textbox", "searchbox"),
                name_keywords=("搜索", "Search", "姓名"),
                tag_hints=("input",),
                dom_selectors=SEARCH_INPUT_SELECTORS,
            ),
            _target(
                "T-05",
                "精确候选项",
                roles=("menuitem", "option"),
                tag_hints=("div", "li"),
                dom_selectors=(
                    (By.CSS_SELECTOR, '[role="menuitem"]'),
                    (By.CSS_SELECTOR, '[role="option"]'),
                ),
                dynamic=True,
            ),
            _target(
                "T-06",
                "达人搜索结果卡片",
                roles=("link", "row", "listitem"),
                tag_hints=("tr",),
                dom_selectors=(
                    (By.XPATH, "//tr[.//*[@role='link']]"),
                    (By.CSS_SELECTOR, "tr, [role='row']"),
                ),
                dynamic=True,
            ),
            _target(
                "T-07",
                "页面标题",
                roles=("heading",),
                name_keywords=("Find creators", "查找达人", "寻找达人"),
                tag_hints=("h1", "h2"),
                dom_selectors=((By.CSS_SELECTOR, "h1, h2"),),
            ),
        ),
        min_visible_elements=15,
    ),
    PageSpec(
        page_id="creator_detail",
        description="达人详情页",
        url_markers=("/connection/creator/detail",),
        exclude_url_markers=(),
        requires_navigation=True,
        navigation_action="navigate_via_link",
        known_targets=(
            _target(
                "T-08",
                "私信按钮",
                roles=("button",),
                name_keywords=("Message", "私信", "消息"),
                tag_hints=("button",),
                dom_selectors=(
                    (
                        By.CSS_SELECTOR,
                        "button:has(svg.alliance-icon-Message)",
                    ),
                    (
                        By.XPATH,
                        "//button[.//*[contains(@class, "
                        "'alliance-icon-Message')]]",
                    ),
                ),
            ),
            _target(
                "T-09",
                "外部打开聊天",
                roles=("button",),
                name_keywords=("launch", "打开", "open"),
                tag_hints=("button",),
                dom_selectors=(
                    (
                        By.CSS_SELECTOR,
                        "button:has(svg.arco-icon-launch)",
                    ),
                    (
                        By.XPATH,
                        "//button[.//*[contains(@class, "
                        "'arco-icon-launch')]]",
                    ),
                ),
            ),
            _target(
                "T-10",
                "达人名称",
                roles=("heading", "statictext"),
                tag_hints=("h1", "span"),
                dom_selectors=(
                    (By.XPATH, "//h1 | //*[@role='heading']"),
                ),
            ),
        ),
        min_visible_elements=10,
    ),
    PageSpec(
        page_id="chat_tab",
        description="聊天标签页 (Cooperation Chat)",
        url_markers=("im.tiktok", "/seller/im"),
        exclude_url_markers=(),
        requires_navigation=True,
        navigation_action="activate_existing",
        known_targets=(
            _target(
                "T-11",
                "聊天输入区",
                roles=("textbox",),
                name_keywords=("message", "消息", "发送"),
                tag_hints=("textarea", "div", "input"),
                dom_selectors=(
                    (
                        By.CSS_SELECTOR,
                        "textarea, [contenteditable='true'], "
                        "input[placeholder*='发送消息'], "
                        "input[placeholder*='message' i]",
                    ),
                ),
            ),
            _target(
                "T-12",
                "发送按钮",
                roles=("button",),
                name_keywords=("send", "发送"),
                tag_hints=("button",),
            ),
            _target(
                "T-13",
                "定向合作页签",
                roles=("tab",),
                name_keywords=(
                    "定向合作",
                    "Target collaboration",
                    "Target Collaboration",
                ),
                tag_hints=("div",),
                dom_selectors=(
                    (
                        By.XPATH,
                        "//*[@role='tab' and "
                        "(contains(normalize-space(.), '定向合作') "
                        "or contains(normalize-space(.), "
                        "'Target collaboration') "
                        "or contains(normalize-space(.), "
                        "'Target Collaboration'))]",
                    ),
                ),
            ),
            _target(
                "T-14",
                "发送其他邀请",
                roles=("button",),
                name_keywords=(
                    "发送其他邀请",
                    "Send another invitation",
                ),
                tag_hints=("button",),
                dom_selectors=(
                    (
                        By.XPATH,
                        "//button[contains(normalize-space(.), "
                        "'发送其他邀请') or "
                        "contains(normalize-space(.), "
                        "'Send another invitation')]",
                    ),
                ),
            ),
            _target(
                "T-15",
                "已发送消息气泡",
                roles=("statictext", "article"),
                tag_hints=("div",),
                dom_selectors=(
                    (
                        By.CSS_SELECTOR,
                        ".message-bubble, [class*='message-bubble'], "
                        "[role='article']",
                    ),
                ),
            ),
            _target(
                "T-16",
                "聊天顶部用户名",
                roles=("heading", "statictext"),
                tag_hints=("span", "div"),
            ),
        ),
        min_visible_elements=10,
    ),
    PageSpec(
        page_id="invitation_modal",
        description="邀请弹窗",
        url_markers=(),
        exclude_url_markers=(),
        requires_navigation=False,
        navigation_action="open_modal",
        known_targets=(
            _target(
                "T-17",
                "弹窗标题",
                roles=("heading", "dialog"),
                name_keywords=(
                    "邀请",
                    "合作",
                    "Invite",
                    "collaborat",
                ),
                tag_hints=("div",),
                dom_selectors=(
                    (
                        By.XPATH,
                        "//*[contains(normalize-space(), '邀请 @') "
                        "or (contains(normalize-space(), 'Invite @') "
                        "and contains(normalize-space(), 'collaborat'))]",
                    ),
                ),
            ),
            _target(
                "T-18",
                "进行中页签",
                roles=("tab",),
                name_keywords=("进行中", "In progress"),
                tag_hints=("div",),
                dom_selectors=(
                    (
                        By.XPATH,
                        "//*[@role='tab' and "
                        "(normalize-space()='进行中' or "
                        "normalize-space()='In progress')]",
                    ),
                ),
            ),
            _target(
                "T-19",
                "创建新邀请页签",
                roles=("tab",),
                name_keywords=("创建新邀请", "Create new invitation"),
                tag_hints=("div",),
                dom_selectors=(
                    (
                        By.XPATH,
                        "//*[@role='tab' and "
                        "(normalize-space()='创建新邀请' or "
                        "normalize-space()='Create new invitation')]",
                    ),
                ),
            ),
            _target(
                "T-20",
                "邀请单选框",
                roles=("radio",),
                tag_hints=("input",),
                dom_selectors=(
                    (By.CSS_SELECTOR, "input[type='radio']"),
                    (By.CSS_SELECTOR, "[role='radio']"),
                ),
            ),
            _target(
                "T-21",
                "最终邀请按钮",
                roles=("button",),
                name_keywords=("邀请", "Invite"),
                tag_hints=("button",),
                dom_selectors=(
                    (
                        By.XPATH,
                        "//button[normalize-space()='邀请' "
                        "or normalize-space()='Invite']",
                    ),
                ),
            ),
        ),
        min_visible_elements=5,
    ),
)
PAGE_SPEC_BY_ID = {spec.page_id: spec for spec in PAGE_SPECS}


DOM_COLLECTION_SCRIPT = """
const limit = arguments[0];
const markerToken = arguments[1];
const selector = [
  'a', 'button', 'input', 'textarea', 'select',
  '[role]', '[contenteditable="true"]', '[tabindex]'
].join(',');
const normalize = (value) =>
  String(value || '').replace(/\\s+/g, ' ').trim();
const cssPath = (element) => {
  const parts = [];
  let current = element;
  for (let depth = 0; current && depth < 7; depth += 1) {
    if (current.id) {
      parts.unshift(`#${CSS.escape(current.id)}`);
      break;
    }
    const tag = String(current.tagName || '').toLowerCase();
    if (!tag) break;
    const parent = current.parentElement;
    if (!parent) {
      parts.unshift(tag);
      break;
    }
    const siblings = Array.from(parent.children)
      .filter((item) => item.tagName === current.tagName);
    const suffix = siblings.length > 1
      ? `:nth-of-type(${siblings.indexOf(current) + 1})`
      : '';
    parts.unshift(`${tag}${suffix}`);
    current = parent;
  }
  return parts.join(' > ');
};
const nodes = [];
for (const element of document.querySelectorAll(selector)) {
  const style = getComputedStyle(element);
  const rect = element.getBoundingClientRect();
  const visible = (
    style.display !== 'none' &&
    style.visibility !== 'hidden' &&
    Number(style.opacity || 1) > 0 &&
    rect.width > 0 &&
    rect.height > 0
  );
  if (!visible) continue;
  const parent = element.parentElement;
  nodes.push({
    index: nodes.length,
    tag: String(element.tagName || '').toLowerCase(),
    role: element.getAttribute('role') || '',
    type: element.getAttribute('type') || '',
    id: element.id || '',
    name: element.getAttribute('name') || '',
    placeholder: element.getAttribute('placeholder') || '',
    ariaLabel: element.getAttribute('aria-label') || '',
    ariaHidden: element.getAttribute('aria-hidden') || '',
    title: element.getAttribute('title') || '',
    dataE2e: element.getAttribute('data-e2e') || '',
    href: element.getAttribute('href') || '',
    text: normalize(
      element.innerText || element.value || element.textContent
    ).slice(0, 240),
    className: normalize(element.className).slice(0, 300),
    enabled: !element.disabled &&
      element.getAttribute('aria-disabled') !== 'true',
    checked: (
      element.checked === true ||
      element.getAttribute('aria-checked') === 'true'
    ),
    cssPath: cssPath(element),
    parentText: normalize(
      parent?.innerText || parent?.textContent
    ).slice(0, 300),
    inDialog: Boolean(element.closest('[role="dialog"]')),
    inAxTree: (
      element.getAttribute('data-ziniao-ax-tree') === markerToken
    ),
    visible: true,
  });
  if (nodes.length >= limit) break;
}
return {
  url: location.href,
  title: document.title,
  readyState: document.readyState,
  elements: nodes,
};
"""


def _value(payload: Any) -> Any:
    if isinstance(payload, dict) and "value" in payload:
        return payload.get("value")
    return payload


def _normalize_role(value: object) -> str:
    return str(value or "").replace(" ", "").lower()


def _parse_ax_node(raw: dict[str, Any], frame_id: str) -> AXNode:
    properties: dict[str, Any] = {}
    for item in raw.get("properties") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if name:
            properties[name] = _value(item.get("value"))
    return AXNode(
        node_id=str(raw.get("nodeId") or ""),
        frame_id=frame_id,
        backend_dom_node_id=(
            int(raw["backendDOMNodeId"])
            if raw.get("backendDOMNodeId") is not None
            else None
        ),
        role=str(_value(raw.get("role")) or ""),
        name=str(_value(raw.get("name")) or ""),
        ignored=bool(raw.get("ignored")),
        properties=properties,
    )


def _flatten_frame_tree(frame_tree: dict[str, Any]) -> list[str]:
    frame_ids: list[str] = []

    def visit(entry: dict[str, Any]) -> None:
        frame = entry.get("frame")
        if isinstance(frame, dict) and frame.get("id"):
            frame_ids.append(str(frame["id"]))
        for child in entry.get("childFrames") or []:
            if isinstance(child, dict):
                visit(child)

    if isinstance(frame_tree, dict):
        visit(frame_tree)
    return frame_ids


def collect_ax_tree_for_page(
    driver: WebDriver,
) -> tuple[list[AXNode], int, list[dict[str, str]]]:
    """Collect non-mutating AXTree snapshots for the main and child frames."""
    payload = driver.execute_cdp_cmd("Page.getFrameTree", {})
    frame_tree = payload.get("frameTree") if isinstance(payload, dict) else {}
    frame_ids = _flatten_frame_tree(
        frame_tree if isinstance(frame_tree, dict) else {}
    )
    if not frame_ids:
        frame_ids = [""]
    nodes: list[AXNode] = []
    errors: list[dict[str, str]] = []
    for frame_id in frame_ids:
        params = {"frameId": frame_id} if frame_id else {}
        try:
            result = driver.execute_cdp_cmd(
                "Accessibility.getFullAXTree",
                params,
            )
            raw_nodes = (
                result.get("nodes")
                if isinstance(result, dict)
                and isinstance(result.get("nodes"), list)
                else []
            )
            nodes.extend(
                _parse_ax_node(raw, frame_id)
                for raw in raw_nodes
                if isinstance(raw, dict)
            )
        except Exception as error:
            errors.append(
                {
                    "frame_id": frame_id,
                    "error": f"{type(error).__name__}: {error}",
                }
            )
    return nodes, len(frame_ids), errors


def bridge_mark_ax_in_dom(
    driver: WebDriver,
    ax_nodes: Sequence[AXNode],
    *,
    interactive_only: bool = False,
) -> dict[str, Any]:
    """Mark exact DOM nodes represented by non-ignored AXTree nodes."""
    token = uuid.uuid4().hex
    backend_ids = sorted(
        {
            node.backend_dom_node_id
            for node in ax_nodes
            if not node.ignored
            and node.backend_dom_node_id is not None
            and node.backend_dom_node_id > 0
            and (
                not interactive_only
                or (
                    _normalize_role(node.role) in MARKABLE_AX_ROLES
                    or (
                        _normalize_role(node.role) == "generic"
                        and bool(node.properties.get("focusable"))
                    )
                )
            )
        }
    )
    marked = 0
    skipped_non_element = 0
    failures: list[dict[str, Any]] = []
    object_group = f"ziniao-ax-coverage-{token}"
    for backend_id in backend_ids:
        try:
            resolved = driver.execute_cdp_cmd(
                "DOM.resolveNode",
                {
                    "backendNodeId": backend_id,
                    "objectGroup": object_group,
                },
            )
            remote_object = (
                resolved.get("object")
                if isinstance(resolved, dict)
                and isinstance(resolved.get("object"), dict)
                else {}
            )
            object_id = str(remote_object.get("objectId") or "")
            if not object_id:
                raise RuntimeError("DOM.resolveNode did not return objectId")
            result = driver.execute_cdp_cmd(
                "Runtime.callFunctionOn",
                {
                    "objectId": object_id,
                    "functionDeclaration": (
                        "function(token) {"
                        " if (!this || !this.setAttribute) return false;"
                        " this.setAttribute('data-ziniao-ax-tree', token);"
                        " return true;"
                        "}"
                    ),
                    "arguments": [{"value": token}],
                    "returnByValue": True,
                    "silent": True,
                },
            )
            value = (
                result.get("result", {}).get("value")
                if isinstance(result, dict)
                else False
            )
            if value is not True:
                skipped_non_element += 1
                continue
            marked += 1
        except Exception as error:
            if len(failures) < 25:
                failures.append(
                    {
                        "backend_dom_node_id": backend_id,
                        "error": f"{type(error).__name__}: {error}",
                    }
                )
    try:
        driver.execute_cdp_cmd(
            "Runtime.releaseObjectGroup",
            {"objectGroup": object_group},
        )
    except Exception:
        pass
    failed = len(backend_ids) - marked - skipped_non_element
    attempted = marked + failed
    return {
        "token": token,
        "backend_nodes_considered": len(backend_ids),
        "attempted": attempted,
        "marked": marked,
        "failed": failed,
        "skipped_non_element": skipped_non_element,
        "success_rate": round(marked / max(attempted, 1), 4),
        "failure_examples": failures,
    }


FrameCallback = Callable[[tuple[int, ...]], bool | None]


def _walk_frame_documents(
    driver: WebDriver,
    callback: FrameCallback,
    *,
    max_depth: int = 8,
) -> None:
    """Visit every Selenium-accessible frame, restoring the main document."""
    driver.switch_to.default_content()
    stopped = False

    def visit(path: tuple[int, ...], depth: int) -> None:
        nonlocal stopped
        if stopped:
            return
        if callback(path):
            stopped = True
            return
        if depth >= max_depth:
            return
        try:
            frame_count = len(
                driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
            )
        except Exception:
            return
        for index in range(frame_count):
            if stopped:
                break
            try:
                frames = driver.find_elements(
                    By.CSS_SELECTOR,
                    "iframe, frame",
                )
                if index >= len(frames):
                    break
                driver.switch_to.frame(frames[index])
                visit((*path, index), depth + 1)
            except (
                JavascriptException,
                NoSuchFrameException,
                StaleElementReferenceException,
            ):
                pass
            finally:
                try:
                    driver.switch_to.parent_frame()
                except Exception:
                    driver.switch_to.default_content()
                    break

    try:
        visit((), 0)
    finally:
        driver.switch_to.default_content()


def collect_dom_for_page(
    driver: WebDriver,
    *,
    marker_token: str,
    max_elements: int,
) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []

    def collect(path: tuple[int, ...]) -> None:
        remaining = max_elements - len(elements)
        if remaining <= 0:
            return
        try:
            snapshot = driver.execute_script(
                DOM_COLLECTION_SCRIPT,
                remaining,
                marker_token,
            )
        except Exception:
            return
        if not isinstance(snapshot, dict):
            return
        frame_url = str(snapshot.get("url") or "")
        for raw in snapshot.get("elements") or []:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            item["frame_path"] = list(path)
            item["frame_url"] = frame_url
            elements.append(item)

    _walk_frame_documents(driver, collect)
    return elements[:max_elements]


def _xpath_literal(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ", \"'\", ".join(f"'{part}'" for part in parts) + ")"


def _visible(element: WebElement) -> bool:
    try:
        return element.is_displayed()
    except StaleElementReferenceException:
        return False


def _element_snapshot(
    element: WebElement,
    *,
    by: str,
    selector: str,
    frame_path: tuple[int, ...],
) -> dict[str, Any]:
    def attribute(name: str) -> str:
        try:
            return str(element.get_attribute(name) or "")
        except StaleElementReferenceException:
            return ""

    try:
        text = " ".join(str(element.text or "").split())
    except StaleElementReferenceException:
        text = ""
    try:
        tag = str(element.tag_name or "").lower()
    except StaleElementReferenceException:
        tag = ""
    return {
        "tag": tag,
        "text": text[:300],
        "role": attribute("role"),
        "aria_label": attribute("aria-label"),
        "placeholder": attribute("placeholder"),
        "title": attribute("title"),
        "checked": (
            attribute("checked") in {"true", "checked"}
            or attribute("aria-checked") == "true"
        ),
        "enabled": (
            attribute("disabled") not in {"true", "disabled"}
            and attribute("aria-disabled") != "true"
        ),
        "frame_path": list(frame_path),
        "locator": {"by": by, "value": selector},
    }


def _context_keywords(
    target: TargetSpec,
    *,
    creator: str,
    message: str,
) -> tuple[str, ...]:
    values = list(target.name_keywords)
    bare_creator = creator.strip().lstrip("@")
    if bare_creator and target.target_id in {
        "T-05",
        "T-06",
        "T-10",
        "T-16",
        "T-17",
    }:
        values.extend((bare_creator, f"@{bare_creator}"))
    if message and target.target_id == "T-15":
        values.append(message)
    return tuple(value for value in values if value)


def _runtime_dom_selectors(
    target: TargetSpec,
    *,
    creator: str,
    message: str,
) -> tuple[Locator, ...]:
    selectors = list(target.dom_selectors)
    bare_creator = creator.strip().lstrip("@")
    if bare_creator:
        literal = _xpath_literal(bare_creator)
        at_literal = _xpath_literal(f"@{bare_creator}")
        if target.target_id == "T-05":
            selectors.insert(
                0,
                (
                    By.XPATH,
                    "//*[@role='menuitem' or @role='option']"
                    f"[.//*[normalize-space()={literal} or "
                    f"normalize-space()={at_literal}] or "
                    f"normalize-space()={literal} or "
                    f"normalize-space()={at_literal}]",
                ),
            )
        elif target.target_id == "T-06":
            selectors.insert(
                0,
                (
                    By.XPATH,
                    "//tr[.//*[normalize-space()="
                    f"{literal} or normalize-space()={at_literal}]] "
                    "| //*[@role='row'][.//*[normalize-space()="
                    f"{literal} or normalize-space()={at_literal}]]",
                ),
            )
        elif target.target_id in {"T-10", "T-16"}:
            selectors.insert(
                0,
                (
                    By.XPATH,
                    f"//*[normalize-space()={literal} or "
                    f"normalize-space()={at_literal}]",
                ),
            )
    if target.target_id == "T-12":
        selectors.extend(
            (
                (
                    By.XPATH,
                    "//button[normalize-space()='发送' "
                    "or normalize-space()='Send' "
                    "or contains(@aria-label, '发送') "
                    "or contains(translate(@aria-label, "
                    "'SEND', 'send'), 'send')]",
                ),
            )
        )
    if target.target_id == "T-15" and message:
        selectors.insert(
            0,
            (
                By.XPATH,
                f"//*[normalize-space()={_xpath_literal(message)}]",
            ),
        )
    return tuple(selectors)


TEXT_FILTER_TARGET_IDS = frozenset(
    {"T-05", "T-06", "T-07", "T-10", "T-15", "T-16"}
)


def _find_dom_target(
    driver: WebDriver,
    target: TargetSpec,
    *,
    creator: str,
    message: str,
) -> dict[str, Any] | None:
    selectors = _runtime_dom_selectors(
        target,
        creator=creator,
        message=message,
    )
    if not selectors:
        return None
    keywords = _context_keywords(
        target,
        creator=creator,
        message=message,
    )
    require_text = target.target_id in TEXT_FILTER_TARGET_IDS
    match: dict[str, Any] | None = None

    def find(path: tuple[int, ...]) -> bool:
        nonlocal match
        for by, selector in selectors:
            try:
                candidates = driver.find_elements(by, selector)
            except Exception:
                continue
            for candidate in candidates:
                if not _visible(candidate):
                    if target.target_id != "T-20":
                        continue
                    try:
                        visible_row = candidate.find_element(
                            By.XPATH,
                            "ancestor::*[@role='listitem' "
                            "or @role='row'][1]",
                        )
                        if not _visible(visible_row):
                            continue
                    except Exception:
                        continue
                snapshot = _element_snapshot(
                    candidate,
                    by=by,
                    selector=selector,
                    frame_path=path,
                )
                searchable = " ".join(
                    str(snapshot.get(key) or "")
                    for key in (
                        "text",
                        "aria_label",
                        "placeholder",
                        "title",
                    )
                ).casefold()
                if (
                    require_text
                    and keywords
                    and not any(
                        keyword.casefold() in searchable
                        for keyword in keywords
                    )
                ):
                    continue
                if (
                    require_text
                    and target.target_id in {"T-10", "T-16"}
                    and not creator
                ):
                    continue
                if (
                    require_text
                    and target.target_id == "T-15"
                    and not message
                ):
                    continue
                match = snapshot
                return True
        return False

    _walk_frame_documents(driver, find)
    return match


def _state_matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, bool):
        if isinstance(actual, str):
            actual = actual.strip().lower() in {
                "1",
                "true",
                "yes",
                "checked",
            }
        return actual is expected
    return actual == expected


def find_in_ax(
    ax_nodes: Sequence[AXNode],
    target: TargetSpec,
    *,
    creator: str = "",
    message: str = "",
) -> AXNode | None:
    roles = {_normalize_role(role) for role in target.roles}
    keywords = _context_keywords(
        target,
        creator=creator,
        message=message,
    )
    candidates: list[tuple[int, AXNode]] = []
    for node in ax_nodes:
        if node.ignored or _normalize_role(node.role) not in roles:
            continue
        normalized_name = node.name.casefold()
        if (
            target.target_id == "T-21"
            and normalized_name.strip() not in {"邀请", "invite"}
        ):
            continue
        if (
            target.target_id == "T-12"
            and normalized_name.strip() not in {"发送", "send"}
        ):
            continue
        keyword_hits = sum(
            keyword.casefold() in normalized_name
            for keyword in keywords
        )
        if keywords and keyword_hits == 0:
            continue
        if target.state_checks and any(
            not _state_matches(node.properties.get(name), expected)
            for name, expected in target.state_checks.items()
        ):
            continue
        candidates.append((keyword_hits, node))
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            item[0],
            bool(item[1].backend_dom_node_id),
            len(item[1].name),
        ),
        reverse=True,
    )
    return candidates[0][1]


def _capture_page(
    spec: PageSpec,
    driver: WebDriver,
    *,
    phase: str,
    creator: str,
    message: str,
    max_elements: int,
) -> PageSnapshot:
    ax_nodes, frame_count, frame_errors = collect_ax_tree_for_page(driver)
    marking = bridge_mark_ax_in_dom(
        driver,
        ax_nodes,
        interactive_only=(spec.page_id == "invitation_modal"),
    )
    dom_elements = collect_dom_for_page(
        driver,
        marker_token=str(marking["token"]),
        max_elements=max_elements,
    )
    if spec.page_id == "invitation_modal":
        dom_elements = [
            element
            for element in dom_elements
            if element.get("inDialog")
        ]
    dom_targets = {
        target.target_id: _find_dom_target(
            driver,
            target,
            creator=creator,
            message=message,
        )
        for target in spec.known_targets
    }
    return PageSnapshot(
        phase=phase,
        url=str(driver.current_url or ""),
        frame_count=frame_count,
        frame_errors=frame_errors,
        ax_nodes=ax_nodes,
        dom_elements=dom_elements,
        dom_targets=dom_targets,
        marking=marking,
    )


def _wait_until(
    predicate: Callable[[], Any],
    *,
    timeout_seconds: float,
    interval_seconds: float = 0.25,
) -> Any:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except Exception as error:
            last_error = error
        time.sleep(interval_seconds)
    if last_error is not None:
        raise TimeoutError(str(last_error)) from last_error
    raise TimeoutError("condition was not met before timeout")


def _url_matches(spec: PageSpec, url: str) -> bool:
    normalized = str(url or "").lower()
    if spec.page_id == "store_home":
        hostname = str(urlparse(normalized).hostname or "")
        marker_matches = hostname.startswith(("seller.", "shop."))
    else:
        marker_matches = (
            not spec.url_markers
            or any(
                marker.lower() in normalized
                for marker in spec.url_markers
            )
        )
    return (
        marker_matches
        and not any(
            marker.lower() in normalized
            for marker in spec.exclude_url_markers
        )
    )


def _activate_window(
    driver: WebDriver,
    matcher: Callable[[str], bool],
) -> bool:
    original = driver.current_window_handle
    handles = list(driver.window_handles)
    ordered = [original, *reversed([h for h in handles if h != original])]
    for handle in ordered:
        try:
            driver.switch_to.window(handle)
            if matcher(str(driver.current_url or "")):
                return True
        except Exception:
            continue
    if original in driver.window_handles:
        driver.switch_to.window(original)
    return False


def _click_first_in_frames(
    driver: WebDriver,
    selectors: Iterable[Locator],
    *,
    validator: Callable[[WebElement], bool] | None = None,
) -> bool:
    clicked = False

    def click(_path: tuple[int, ...]) -> bool:
        nonlocal clicked
        for by, selector in selectors:
            try:
                candidates = driver.find_elements(by, selector)
            except Exception:
                continue
            for candidate in candidates:
                try:
                    if (
                        not candidate.is_displayed()
                        or not candidate.is_enabled()
                        or (
                            validator is not None
                            and not validator(candidate)
                        )
                    ):
                        continue
                    driver.execute_script(
                        "arguments[0].scrollIntoView("
                        "{block: 'center', inline: 'center'});",
                        candidate,
                    )
                    try:
                        candidate.click()
                    except Exception:
                        driver.execute_script(
                            "arguments[0].click();",
                            candidate,
                        )
                    clicked = True
                    return True
                except StaleElementReferenceException:
                    continue
        return False

    _walk_frame_documents(driver, click)
    return clicked


def _visible_in_frames(
    driver: WebDriver,
    selectors: Iterable[Locator],
) -> bool:
    found = False

    def inspect(_path: tuple[int, ...]) -> bool:
        nonlocal found
        for by, selector in selectors:
            try:
                if any(
                    _visible(element)
                    for element in driver.find_elements(by, selector)
                ):
                    found = True
                    return True
            except Exception:
                continue
        return False

    _walk_frame_documents(driver, inspect)
    return found


def _act_on_first_in_frames(
    driver: WebDriver,
    selectors: Iterable[Locator],
    action: Callable[[WebElement], Any],
) -> Any:
    result: Any = None

    def apply(_path: tuple[int, ...]) -> bool:
        nonlocal result
        for by, selector in selectors:
            try:
                candidates = driver.find_elements(by, selector)
            except Exception:
                continue
            for candidate in candidates:
                try:
                    if not (
                        candidate.is_displayed() and candidate.is_enabled()
                    ):
                        continue
                    result = action(candidate)
                    return True
                except StaleElementReferenceException:
                    continue
        return False

    _walk_frame_documents(driver, apply)
    return result


def _find_exact_creator_element(
    driver: WebDriver,
    creator: str,
    *,
    container_roles: Sequence[str],
) -> bool:
    bare = creator.strip().lstrip("@")
    if not bare:
        return False
    literal = _xpath_literal(bare)
    at_literal = _xpath_literal(f"@{bare}")
    role_expression = " or ".join(
        f"@role={_xpath_literal(role)}" for role in container_roles
    )
    selector = (
        f"//*[{role_expression}]"
        f"[.//*[normalize-space()={literal} or "
        f"normalize-space()={at_literal}] or "
        f"normalize-space()={literal} or "
        f"normalize-space()={at_literal}]"
    )
    found = False

    def inspect(_path: tuple[int, ...]) -> bool:
        nonlocal found
        try:
            found = any(
                _visible(element)
                for element in driver.find_elements(By.XPATH, selector)
            )
        except Exception:
            found = False
        return found

    _walk_frame_documents(driver, inspect)
    return found


def _type_creator(driver: WebDriver, creator: str) -> bool:
    bare = creator.strip().lstrip("@")

    def enter(element: WebElement) -> bool:
        driver.execute_script(
            "arguments[0].scrollIntoView("
            "{block: 'center', inline: 'center'});",
            element,
        )
        try:
            element.click()
        except Exception:
            driver.execute_script("arguments[0].focus();", element)
        replace_element_text(element, bare)
        return True

    return bool(_act_on_first_in_frames(driver, SEARCH_INPUT_SELECTORS, enter))


def _close_ai_search_if_needed(driver: WebDriver) -> bool:
    def is_checked(element: WebElement) -> bool:
        return str(element.get_attribute("aria-checked") or "").lower() != "false"

    return _click_first_in_frames(
        driver,
        AI_SWITCH_SELECTORS,
        validator=is_checked,
    )


def _perform_dynamic_search(
    spec: PageSpec,
    driver: WebDriver,
    *,
    creator: str,
    message: str,
    max_elements: int,
    timeout_seconds: float,
) -> list[PageSnapshot]:
    snapshots: list[PageSnapshot] = []
    if not creator:
        return snapshots
    _close_ai_search_if_needed(driver)
    typed = _wait_until(
        lambda: _type_creator(driver, creator),
        timeout_seconds=timeout_seconds,
    )
    if not typed:
        raise RuntimeError("未找到可用达人搜索框")
    _wait_until(
        lambda: _find_exact_creator_element(
            driver,
            creator,
            container_roles=("menuitem", "option"),
        ),
        timeout_seconds=timeout_seconds,
    )
    snapshots.append(
        _capture_page(
            spec,
            driver,
            phase="dynamic_candidate",
            creator=creator,
            message=message,
            max_elements=max_elements,
        )
    )
    bare = creator.strip().lstrip("@")

    def exact_candidate(element: WebElement) -> bool:
        tokens = {
            token.strip().lstrip("@").casefold()
            for token in str(element.get_attribute("innerText") or "").splitlines()
            if token.strip()
        }
        return bare.casefold() in tokens

    if not _click_first_in_frames(
        driver,
        (
            (By.CSS_SELECTOR, '[role="menuitem"]'),
            (By.CSS_SELECTOR, '[role="option"]'),
        ),
        validator=exact_candidate,
    ):
        raise RuntimeError(f"未能点击精确候选项 @{bare}")
    _wait_until(
        lambda: _find_exact_creator_element(
            driver,
            creator,
            container_roles=("row",),
        )
        or _creator_result_row_visible(driver, creator),
        timeout_seconds=timeout_seconds,
    )
    snapshots.append(
        _capture_page(
            spec,
            driver,
            phase="dynamic_result",
            creator=creator,
            message=message,
            max_elements=max_elements,
        )
    )
    return snapshots


def _creator_result_row_visible(driver: WebDriver, creator: str) -> bool:
    bare = creator.strip().lstrip("@")
    if not bare:
        return False
    literal = _xpath_literal(bare)
    at_literal = _xpath_literal(f"@{bare}")
    found = False

    def inspect(_path: tuple[int, ...]) -> bool:
        nonlocal found
        selector = (
            "//tr[.//*[normalize-space()="
            f"{literal} or normalize-space()={at_literal}]]"
        )
        found = any(
            _visible(element)
            for element in driver.find_elements(By.XPATH, selector)
        )
        return found

    _walk_frame_documents(driver, inspect)
    return found


def _navigate_find_creators(
    driver: WebDriver,
    *,
    timeout_seconds: float,
) -> None:
    spec = PAGE_SPEC_BY_ID["find_creators"]
    if _activate_window(driver, lambda url: _url_matches(spec, url)):
        return
    store_spec = PAGE_SPEC_BY_ID["store_home"]
    if not _activate_window(
        driver,
        lambda url: _url_matches(store_spec, url)
        or "/affiliate" in url.lower(),
    ):
        raise RuntimeError("未找到可用的 TikTok Shop 店铺或联盟标签页")
    if "/affiliate" not in str(driver.current_url).lower():
        if not _click_first_in_frames(
            driver,
            (
                (By.CSS_SELECTOR, 'a[href*="/affiliate"]'),
                (
                    By.XPATH,
                    "//*[self::a or self::button or "
                    "@role='menuitem' or @role='button']"
                    "[contains(normalize-space(.), 'Affiliate') "
                    "or contains(normalize-space(.), '联盟')]",
                ),
            ),
        ):
            raise RuntimeError("店铺首页未找到 Affiliate/联盟入口")
        _wait_until(
            lambda: (
                "/affiliate" in str(driver.current_url).lower()
                or _activate_window(
                    driver,
                    lambda url: "/affiliate" in url.lower(),
                )
            ),
            timeout_seconds=timeout_seconds,
        )
    _enter_affiliate_center_if_needed(
        driver,
        timeout_seconds=timeout_seconds,
    )
    if not _click_first_in_frames(driver, FIND_CREATORS_ENTRY_SELECTORS):
        raise RuntimeError("联盟页未找到 Find creators/查找达人入口")
    _wait_until(
        lambda: (
            _url_matches(spec, str(driver.current_url))
            or _activate_window(driver, lambda url: _url_matches(spec, url))
        ),
        timeout_seconds=timeout_seconds,
    )


def _enter_affiliate_center_if_needed(
    driver: WebDriver,
    *,
    timeout_seconds: float,
) -> None:
    ready_state = _wait_until(
        lambda: (
            "find_creators"
            if _visible_in_frames(
                driver,
                FIND_CREATORS_ENTRY_SELECTORS,
            )
            else "affiliate_entry"
            if _visible_in_frames(
                driver,
                AFFILIATE_CENTER_ENTRY_SELECTORS,
            )
            else False
        ),
        timeout_seconds=timeout_seconds,
    )
    if ready_state == "find_creators":
        return
    if not _click_first_in_frames(
        driver,
        AFFILIATE_CENTER_ENTRY_SELECTORS,
    ):
        return
    _wait_until(
        lambda: (
            _visible_in_frames(driver, FIND_CREATORS_ENTRY_SELECTORS)
            or _activate_window(
                driver,
                lambda url: (
                    "affiliate.tiktokshop" in url.lower()
                    and "/affiliate/landing" not in url.lower()
                ),
            )
            and _visible_in_frames(
                driver,
                FIND_CREATORS_ENTRY_SELECTORS,
            )
        ),
        timeout_seconds=timeout_seconds,
    )


def _navigate_creator_detail(
    driver: WebDriver,
    *,
    creator: str,
    timeout_seconds: float,
) -> None:
    spec = PAGE_SPEC_BY_ID["creator_detail"]
    if _activate_window(driver, lambda url: _url_matches(spec, url)):
        return
    if not creator:
        raise RuntimeError("自动进入达人详情页需要 --creator")
    _navigate_find_creators(driver, timeout_seconds=timeout_seconds)
    if not _creator_result_row_visible(driver, creator):
        _close_ai_search_if_needed(driver)
        _wait_until(
            lambda: _type_creator(driver, creator),
            timeout_seconds=timeout_seconds,
        )
        _wait_until(
            lambda: _find_exact_creator_element(
                driver,
                creator,
                container_roles=("menuitem", "option"),
            ),
            timeout_seconds=timeout_seconds,
        )
        bare = creator.strip().lstrip("@").casefold()
        if not _click_first_in_frames(
            driver,
            (
                (By.CSS_SELECTOR, '[role="menuitem"]'),
                (By.CSS_SELECTOR, '[role="option"]'),
            ),
            validator=lambda element: bare
            in {
                token.strip().lstrip("@").casefold()
                for token in str(
                    element.get_attribute("innerText") or ""
                ).splitlines()
                if token.strip()
            },
        ):
            raise RuntimeError("未能选择精确达人候选项")
        _wait_until(
            lambda: _creator_result_row_visible(driver, creator),
            timeout_seconds=timeout_seconds,
        )
    bare = creator.strip().lstrip("@")
    literal = _xpath_literal(bare)
    at_literal = _xpath_literal(f"@{bare}")
    if not _click_first_in_frames(
        driver,
        (
            (
                By.XPATH,
                "//tr[.//*[normalize-space()="
                f"{literal} or normalize-space()={at_literal}]]"
                "//*[normalize-space()="
                f"{literal} or normalize-space()={at_literal}][1]",
            ),
        ),
    ):
        raise RuntimeError(f"未找到 @{bare} 的可点击搜索结果")
    _wait_until(
        lambda: (
            _url_matches(spec, str(driver.current_url))
            or _activate_window(driver, lambda url: _url_matches(spec, url))
        ),
        timeout_seconds=timeout_seconds,
    )


def _navigate_chat_tab(
    driver: WebDriver,
    *,
    creator: str,
    timeout_seconds: float,
) -> None:
    spec = PAGE_SPEC_BY_ID["chat_tab"]
    if _activate_window(driver, lambda url: _url_matches(spec, url)):
        _close_modal_if_present(
            driver,
            timeout_seconds=timeout_seconds,
        )
        _wait_until(
            lambda: _visible_in_frames(driver, CHAT_READY_SELECTORS),
            timeout_seconds=timeout_seconds,
        )
        return
    _navigate_creator_detail(
        driver,
        creator=creator,
        timeout_seconds=timeout_seconds,
    )
    launch_selectors = (
        (
            By.CSS_SELECTOR,
            "button:has(svg.arco-icon-launch)",
        ),
        (
            By.XPATH,
            "//button[.//*[contains(@class, 'arco-icon-launch')]]",
        ),
    )
    if not _visible_in_frames(driver, launch_selectors):
        if not _click_first_in_frames(
            driver,
            (
                (
                    By.CSS_SELECTOR,
                    "button:has(svg.alliance-icon-Message)",
                ),
                (
                    By.XPATH,
                    "//button[.//*[contains(@class, "
                    "'alliance-icon-Message')]]",
                ),
            ),
        ):
            raise RuntimeError("达人详情页未找到私信按钮")
    _wait_until(
        lambda: _click_first_in_frames(driver, launch_selectors),
        timeout_seconds=timeout_seconds,
    )
    _wait_until(
        lambda: (
            _url_matches(spec, str(driver.current_url))
            or _activate_window(driver, lambda url: _url_matches(spec, url))
        ),
        timeout_seconds=timeout_seconds,
    )
    _close_modal_if_present(
        driver,
        timeout_seconds=timeout_seconds,
    )
    _wait_until(
        lambda: _visible_in_frames(driver, CHAT_READY_SELECTORS),
        timeout_seconds=timeout_seconds,
    )


def _modal_visible(driver: WebDriver) -> bool:
    found = False

    def inspect(_path: tuple[int, ...]) -> bool:
        nonlocal found
        selectors = (
            (By.CSS_SELECTOR, "[role='dialog']"),
            (
                By.XPATH,
                "//*[contains(normalize-space(), '邀请 @') "
                "or contains(normalize-space(), 'Invite @')]",
            ),
        )
        for by, selector in selectors:
            if any(
                _visible(element)
                for element in driver.find_elements(by, selector)
            ):
                found = True
                return True
        return False

    _walk_frame_documents(driver, inspect)
    return found


def _close_modal_if_present(
    driver: WebDriver,
    *,
    timeout_seconds: float,
) -> None:
    if not _modal_visible(driver):
        return
    closed = _click_first_in_frames(
        driver,
        (
            (
                By.XPATH,
                "//*[@role='dialog']"
                "//*[self::button or @role='button']"
                "[normalize-space()='取消' "
                "or normalize-space()='Cancel' "
                "or normalize-space()='Close' "
                "or @aria-label='Close']",
            ),
        ),
    )
    if not closed:
        raise RuntimeError("邀请弹窗已打开，但未找到取消或关闭控件")
    _wait_until(
        lambda: not _modal_visible(driver),
        timeout_seconds=timeout_seconds,
    )


def _open_invitation_modal(
    driver: WebDriver,
    *,
    creator: str,
    timeout_seconds: float,
) -> None:
    if _modal_visible(driver):
        _wait_for_invitation_rows(
            driver,
            timeout_seconds=timeout_seconds,
        )
        return
    _navigate_chat_tab(
        driver,
        creator=creator,
        timeout_seconds=timeout_seconds,
    )
    if not _click_first_in_frames(
        driver,
        (
            (
                By.XPATH,
                "//*[@role='tab' and "
                "(contains(normalize-space(.), '定向合作') "
                "or contains(normalize-space(.), "
                "'Target collaboration') "
                "or contains(normalize-space(.), "
                "'Target Collaboration'))]",
            ),
        ),
    ):
        raise RuntimeError("聊天页未找到定向合作页签")
    other_invitation = (
        (
            By.XPATH,
            "//button[contains(normalize-space(.), "
            "'发送其他邀请开展合作') or "
            "contains(normalize-space(.), "
            "'Send another invitation')]",
        ),
    )
    _wait_until(
        lambda: _click_first_in_frames(driver, other_invitation),
        timeout_seconds=timeout_seconds,
    )
    _wait_until(
        lambda: _modal_visible(driver),
        timeout_seconds=timeout_seconds,
    )
    _wait_for_invitation_rows(
        driver,
        timeout_seconds=timeout_seconds,
    )


def _wait_for_invitation_rows(
    driver: WebDriver,
    *,
    timeout_seconds: float,
) -> None:
    _wait_until(
        lambda: _visible_in_frames(
            driver,
            (
                (
                    By.XPATH,
                    "//*[@role='dialog']"
                    "//*[@role='listitem']"
                    "[.//input[@type='radio']]",
                ),
                (
                    By.XPATH,
                    "//*[@role='dialog']"
                    "//*[contains(normalize-space(.), '暂无邀请') "
                    "or contains(normalize-space(.), "
                    "'No invitations')]",
                ),
            ),
        ),
        timeout_seconds=timeout_seconds,
    )


def _navigate_to_page(
    driver: WebDriver,
    spec: PageSpec,
    *,
    creator: str,
    timeout_seconds: float,
) -> None:
    if spec.page_id == "store_home":
        if not _activate_window(driver, lambda url: _url_matches(spec, url)):
            activated_seller = _activate_window(
                driver,
                lambda url: str(
                    urlparse(url).hostname or ""
                ).startswith(("seller.", "shop.")),
            )
            if not activated_seller or not _click_first_in_frames(
                driver,
                (
                    (By.CSS_SELECTOR, 'a[href="/homepage"]'),
                    (
                        By.XPATH,
                        "//*[self::a or @role='menuitem']"
                        "[normalize-space()='首页' "
                        "or normalize-space()='Home']",
                    ),
                ),
            ):
                raise RuntimeError("当前浏览器没有可复用的店铺首页标签页")
            _wait_until(
                lambda: _url_matches(spec, _safe_current_url(driver)),
                timeout_seconds=timeout_seconds,
            )
    elif spec.page_id == "find_creators":
        _navigate_find_creators(driver, timeout_seconds=timeout_seconds)
    elif spec.page_id == "creator_detail":
        _navigate_creator_detail(
            driver,
            creator=creator,
            timeout_seconds=timeout_seconds,
        )
    elif spec.page_id == "chat_tab":
        _navigate_chat_tab(
            driver,
            creator=creator,
            timeout_seconds=timeout_seconds,
        )
    elif spec.page_id == "invitation_modal":
        _open_invitation_modal(
            driver,
            creator=creator,
            timeout_seconds=timeout_seconds,
        )


def _capture_store_navigation_state(
    spec: PageSpec,
    driver: WebDriver,
    *,
    creator: str,
    message: str,
    max_elements: int,
    timeout_seconds: float,
) -> PageSnapshot | None:
    find_target = next(
        target
        for target in spec.known_targets
        if target.target_id == "T-02"
    )
    if _find_dom_target(
        driver,
        find_target,
        creator=creator,
        message=message,
    ):
        return None
    clicked = _click_first_in_frames(
        driver,
        (
            (By.CSS_SELECTOR, 'a[href*="/affiliate"]'),
            (
                By.XPATH,
                "//*[self::a or self::button or "
                "@role='menuitem' or @role='button']"
                "[contains(normalize-space(.), 'Affiliate') "
                "or contains(normalize-space(.), '联盟')]",
            ),
        ),
    )
    if not clicked:
        return None
    _wait_until(
        lambda: (
            "/affiliate" in _safe_current_url(driver).lower()
            or _activate_window(
                driver,
                lambda url: "/affiliate" in url.lower(),
            )
            or _visible_in_frames(
                driver,
                (
                    (
                        By.CSS_SELECTOR,
                        'a[href*="/connection/creator"]',
                    ),
                    (
                        By.XPATH,
                        "//*[contains(normalize-space(.), "
                        "'Find creators') or "
                        "contains(normalize-space(.), '查找达人') or "
                        "contains(normalize-space(.), '寻找达人')]",
                    ),
                ),
            )
        ),
        timeout_seconds=timeout_seconds,
    )
    _enter_affiliate_center_if_needed(
        driver,
        timeout_seconds=timeout_seconds,
    )
    return _capture_page(
        spec,
        driver,
        phase="affiliate_navigation",
        creator=creator,
        message=message,
        max_elements=max_elements,
    )


def _capture_find_creators_ready_state(
    spec: PageSpec,
    driver: WebDriver,
    *,
    creator: str,
    message: str,
    max_elements: int,
    timeout_seconds: float,
) -> PageSnapshot | None:
    if not _close_ai_search_if_needed(driver):
        return None
    _wait_until(
        lambda: _visible_in_frames(driver, SEARCH_INPUT_SELECTORS),
        timeout_seconds=timeout_seconds,
    )
    return _capture_page(
        spec,
        driver,
        phase="post_ai_close",
        creator=creator,
        message=message,
        max_elements=max_elements,
    )


def _capture_creator_drawer_state(
    spec: PageSpec,
    driver: WebDriver,
    *,
    creator: str,
    message: str,
    max_elements: int,
    timeout_seconds: float,
) -> PageSnapshot | None:
    launch_selectors = (
        (
            By.CSS_SELECTOR,
            "button:has(svg.arco-icon-launch)",
        ),
        (
            By.XPATH,
            "//button[.//*[contains(@class, 'arco-icon-launch')]]",
        ),
    )
    if not _visible_in_frames(driver, launch_selectors):
        if not _click_first_in_frames(
            driver,
            (
                (
                    By.CSS_SELECTOR,
                    "button:has(svg.alliance-icon-Message)",
                ),
                (
                    By.XPATH,
                    "//button[.//*[contains(@class, "
                    "'alliance-icon-Message')]]",
                ),
            ),
        ):
            return None
        _wait_until(
            lambda: _visible_in_frames(driver, launch_selectors),
            timeout_seconds=timeout_seconds,
        )
    return _capture_page(
        spec,
        driver,
        phase="message_drawer",
        creator=creator,
        message=message,
        max_elements=max_elements,
    )


def _capture_target_collaboration_state(
    spec: PageSpec,
    driver: WebDriver,
    *,
    creator: str,
    message: str,
    max_elements: int,
    timeout_seconds: float,
) -> PageSnapshot | None:
    other_invitation = (
        (
            By.XPATH,
            "//button[contains(normalize-space(.), "
            "'发送其他邀请开展合作') or "
            "contains(normalize-space(.), "
            "'Send another invitation')]",
        ),
    )
    if not _visible_in_frames(driver, other_invitation):
        if not _click_first_in_frames(
            driver,
            (
                (
                    By.XPATH,
                    "//*[@role='tab' and "
                    "(contains(normalize-space(.), '定向合作') "
                    "or contains(normalize-space(.), "
                    "'Target collaboration') "
                    "or contains(normalize-space(.), "
                    "'Target Collaboration'))]",
                ),
            ),
        ):
            return None
        _wait_until(
            lambda: _visible_in_frames(driver, other_invitation),
            timeout_seconds=timeout_seconds,
        )
    return _capture_page(
        spec,
        driver,
        phase="target_collaboration",
        creator=creator,
        message=message,
        max_elements=max_elements,
    )


def _miss_reason(element: dict[str, Any]) -> str:
    if str(element.get("ariaHidden") or "").lower() == "true":
        return "DOM 元素 aria-hidden=true，预期不进入 AXTree"
    if not element.get("enabled", True):
        return "DOM 元素当前不可用，可能被 AXTree 忽略"
    if not element.get("role") and element.get("tag") == "div":
        return "可交互 div 缺少显式语义，未映射到非 ignored AX 节点"
    return "可见交互 DOM 节点未映射到同一非 ignored AX 节点"


def _snapshot_report(snapshot: PageSnapshot) -> dict[str, Any]:
    visible = [
        element
        for element in snapshot.dom_elements
        if element.get("visible", True)
    ]
    covered = [element for element in visible if element.get("inAxTree")]
    return {
        "phase": snapshot.phase,
        "url": snapshot.url,
        "frame_count": snapshot.frame_count,
        "frame_errors": snapshot.frame_errors,
        "ax_nodes": len(snapshot.ax_nodes),
        "ax_nodes_non_ignored": sum(
            not node.ignored for node in snapshot.ax_nodes
        ),
        "dom_visible": len(visible),
        "covered": len(covered),
        "coverage_rate": round(
            len(covered) / max(len(visible), 1),
            4,
        ),
        "marking": {
            key: value
            for key, value in snapshot.marking.items()
            if key != "token"
        },
    }


def _target_result(
    target: TargetSpec,
    snapshots: Sequence[PageSnapshot],
    *,
    creator: str,
    message: str,
) -> dict[str, Any]:
    if target.dynamic and not creator:
        return {
            "description": target.description,
            "dynamic": True,
            "status": "not_testable",
            "in_ax": False,
            "in_dom": False,
            "note": "需要 --creator 才能触发动态渲染",
        }
    if target.target_id in {"T-10", "T-16"} and not creator:
        return {
            "description": target.description,
            "dynamic": target.dynamic,
            "status": "not_testable",
            "in_ax": False,
            "in_dom": False,
            "note": "需要 --creator 才能精确匹配达人名称",
        }
    if target.target_id == "T-15" and not message:
        return {
            "description": target.description,
            "dynamic": target.dynamic,
            "status": "not_testable",
            "in_ax": False,
            "in_dom": False,
            "note": "需要 --message 才能精确匹配已发送消息气泡",
        }
    ordered = list(snapshots)
    if target.dynamic:
        ordered = [
            snapshot
            for snapshot in snapshots
            if snapshot.phase.startswith("dynamic_")
        ]
    ax_match: AXNode | None = None
    ax_phase: str | None = None
    dom_match: dict[str, Any] | None = None
    dom_phase: str | None = None
    for snapshot in ordered:
        if ax_match is None:
            candidate = find_in_ax(
                snapshot.ax_nodes,
                target,
                creator=creator,
                message=message,
            )
            if candidate is not None:
                ax_match = candidate
                ax_phase = snapshot.phase
        if dom_match is None:
            candidate_dom = snapshot.dom_targets.get(target.target_id)
            if candidate_dom is not None:
                dom_match = candidate_dom
                dom_phase = snapshot.phase
    in_ax = ax_match is not None
    in_dom = dom_match is not None
    result: dict[str, Any] = {
        "description": target.description,
        "dynamic": target.dynamic,
        "status": "hit" if in_ax else "miss",
        "in_ax": in_ax,
        "ax_role": ax_match.role if ax_match else None,
        "ax_name": ax_match.name if ax_match else None,
        "ax_state": ax_match.properties if ax_match else None,
        "ax_phase": ax_phase,
        "in_dom": in_dom,
        "dom_tag": dom_match.get("tag") if dom_match else None,
        "dom_role": dom_match.get("role") if dom_match else None,
        "dom_phase": dom_phase,
    }
    if target.state_checks:
        result["expected_state"] = target.state_checks
    if in_dom and not in_ax:
        result["note"] = "DOM 可见但未在 AXTree 中命中"
    elif in_ax and not in_dom:
        result["note"] = "AXTree 命中，但 DOM 定位器未命中"
    return result


def _page_verdict(
    *,
    coverage_rate: float,
    static_misses: int,
    visible_count: int,
    minimum_visible: int,
) -> str:
    if coverage_rate < 0.6 or static_misses >= 3:
        return "fail"
    if (
        coverage_rate < 0.8
        or static_misses > 0
        or visible_count < minimum_visible
    ):
        return "warn"
    return "pass"


def verify_page(
    spec: PageSpec,
    driver: WebDriver,
    *,
    creator: str = "",
    message: str = "",
    skip_navigation: bool = False,
    max_elements: int = 500,
    timeout_seconds: float = 15,
) -> dict[str, Any]:
    navigation: dict[str, Any] = {
        "skipped": skip_navigation,
        "action": spec.navigation_action,
    }
    try:
        if not skip_navigation:
            _navigate_to_page(
                driver,
                spec,
                creator=creator,
                timeout_seconds=timeout_seconds,
            )
        navigation["url_after"] = str(driver.current_url or "")
        if spec.url_markers and not _url_matches(
            spec,
            str(driver.current_url or ""),
        ):
            navigation["url_mismatch"] = True
        snapshots = [
            _capture_page(
                spec,
                driver,
                phase="initial",
                creator=creator,
                message=message,
                max_elements=max_elements,
            )
        ]
        phase_error: str | None = None
        phase_capture: PageSnapshot | None = None
        try:
            if spec.page_id == "store_home" and not skip_navigation:
                phase_capture = _capture_store_navigation_state(
                    spec,
                    driver,
                    creator=creator,
                    message=message,
                    max_elements=max_elements,
                    timeout_seconds=timeout_seconds,
                )
            elif spec.page_id == "find_creators":
                phase_capture = _capture_find_creators_ready_state(
                    spec,
                    driver,
                    creator=creator,
                    message=message,
                    max_elements=max_elements,
                    timeout_seconds=timeout_seconds,
                )
            elif spec.page_id == "creator_detail":
                phase_capture = _capture_creator_drawer_state(
                    spec,
                    driver,
                    creator=creator,
                    message=message,
                    max_elements=max_elements,
                    timeout_seconds=timeout_seconds,
                )
            elif spec.page_id == "chat_tab":
                phase_capture = _capture_target_collaboration_state(
                    spec,
                    driver,
                    creator=creator,
                    message=message,
                    max_elements=max_elements,
                    timeout_seconds=timeout_seconds,
                )
            if phase_capture is not None:
                snapshots.append(phase_capture)
        except Exception as error:
            phase_error = f"{type(error).__name__}: {error}"
        dynamic_error: str | None = None
        if spec.page_id == "find_creators" and creator:
            try:
                snapshots.extend(
                    _perform_dynamic_search(
                        spec,
                        driver,
                        creator=creator,
                        message=message,
                        max_elements=max_elements,
                        timeout_seconds=timeout_seconds,
                    )
                )
            except Exception as error:
                dynamic_error = f"{type(error).__name__}: {error}"
        target_results = {
            target.target_id: _target_result(
                target,
                snapshots,
                creator=creator,
                message=message,
            )
            for target in spec.known_targets
        }
        primary = (
            snapshots[0]
            if spec.page_id == "store_home"
            else snapshots[-1]
        )
        primary_report = _snapshot_report(primary)
        visible = [
            element
            for element in primary.dom_elements
            if element.get("visible", True)
        ]
        missed = [
            {
                "tag": element.get("tag"),
                "role": element.get("role"),
                "text": str(element.get("text") or "")[:160],
                "css_path": element.get("cssPath"),
                "frame_path": element.get("frame_path"),
                "reason": _miss_reason(element),
            }
            for element in visible
            if not element.get("inAxTree")
        ][:20]
        static_misses = sum(
            result.get("status") == "miss"
            for target_id, result in target_results.items()
            if target_id not in DYNAMIC_TARGET_IDS
        )
        verdict = _page_verdict(
            coverage_rate=float(primary_report["coverage_rate"]),
            static_misses=static_misses,
            visible_count=int(primary_report["dom_visible"]),
            minimum_visible=spec.min_visible_elements,
        )
        if navigation.get("url_mismatch") or dynamic_error or phase_error:
            verdict = "warn" if verdict == "pass" else verdict
        return {
            "page_id": spec.page_id,
            "description": spec.description,
            "status": "tested",
            "url": primary.url,
            "navigation": navigation,
            "ax_nodes": primary_report["ax_nodes"],
            "dom_visible": primary_report["dom_visible"],
            "covered": primary_report["covered"],
            "coverage_rate": primary_report["coverage_rate"],
            "frame_count": primary.frame_count,
            "targets": target_results,
            "missed_examples": missed,
            "phases": [_snapshot_report(snapshot) for snapshot in snapshots],
            "phase_error": phase_error,
            "dynamic_error": dynamic_error,
            "verdict": verdict,
        }
    except Exception as error:
        return {
            "page_id": spec.page_id,
            "description": spec.description,
            "status": "error",
            "url": _safe_current_url(driver),
            "navigation": navigation,
            "ax_nodes": 0,
            "dom_visible": 0,
            "covered": 0,
            "coverage_rate": 0.0,
            "frame_count": 0,
            "targets": {},
            "missed_examples": [],
            "phases": [],
            "error": f"{type(error).__name__}: {error}",
            "verdict": "fail",
        }


def _safe_current_url(driver: WebDriver) -> str:
    try:
        return str(driver.current_url or "")
    except Exception:
        return ""


def build_summary_reports(
    page_reports: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    target_results = [
        result
        for page in page_reports
        for result in page.get("targets", {}).values()
    ]
    evaluated = [
        result
        for result in target_results
        if result.get("status") != "not_testable"
    ]
    ax_hits = sum(bool(result.get("in_ax")) for result in evaluated)
    ax_misses = len(evaluated) - ax_hits
    visible = sum(int(page.get("dom_visible") or 0) for page in page_reports)
    covered = sum(int(page.get("covered") or 0) for page in page_reports)
    verdict_counts = {
        verdict: sum(page.get("verdict") == verdict for page in page_reports)
        for verdict in ("pass", "warn", "fail")
    }
    overall_verdict = (
        "fail"
        if verdict_counts["fail"]
        else "warn"
        if verdict_counts["warn"]
        else "pass"
    )
    missed_ids = [
        target_id
        for page in page_reports
        for target_id, result in page.get("targets", {}).items()
        if result.get("status") == "miss"
    ]
    risks: list[str] = []
    for page in page_reports:
        if page.get("status") == "error":
            risks.append(
                f"{page.get('description')}未完成采集：{page.get('error')}"
            )
        elif float(page.get("coverage_rate") or 0) < 0.8:
            risks.append(
                f"{page.get('description')}覆盖率 "
                f"{float(page.get('coverage_rate') or 0):.1%}，低于 80%"
            )
    if "T-12" in missed_ids:
        risks.append("发送按钮 T-12 需要 DOM 兜底采集")
    not_testable = [
        result
        for result in target_results
        if result.get("status") == "not_testable"
    ]
    if not_testable:
        risks.append(
            f"{len(not_testable)} 个目标缺少运行参数，未精确验证"
        )
    if overall_verdict == "pass":
        recommendation = (
            "所有已测页面达到 pass：AXTree 可作为主采集来源；"
            "仍建议保留小范围 DOM 兜底处理页面重绘和未命名控件。"
        )
    elif overall_verdict == "warn":
        recommendation = (
            "采用 AXTree + DOM 混合方案；对 warn 页面和 DOM 可见但 "
            "AXTree 未命中的目标保留确定性 DOM 定位。"
        )
    else:
        recommendation = (
            "至少一个页面未达到 AXTree 主力方案门槛；"
            "先修复采集或导航失败，并对 fail 页面保留 DOM 主路径。"
        )
    return {
        "total_targets": len(target_results),
        "evaluated_targets": len(evaluated),
        "not_testable_targets": len(not_testable),
        "ax_hits": ax_hits,
        "ax_misses": ax_misses,
        "ax_hit_rate": round(ax_hits / max(len(evaluated), 1), 4),
        "pages_pass": verdict_counts["pass"],
        "pages_warn": verdict_counts["warn"],
        "pages_fail": verdict_counts["fail"],
        "overall_coverage": round(covered / max(visible, 1), 4),
        "overall_verdict": overall_verdict,
        "recommendation": recommendation,
        "risks": risks,
    }


def _aggregate_marking(
    page_reports: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    markings = [
        phase.get("marking", {})
        for page in page_reports
        for phase in page.get("phases", [])
    ]
    attempted = sum(int(item.get("attempted") or 0) for item in markings)
    marked = sum(int(item.get("marked") or 0) for item in markings)
    failed = sum(int(item.get("failed") or 0) for item in markings)
    return {
        "attempted": attempted,
        "total_marked": marked,
        "failed": failed,
        "success_rate": round(marked / max(attempted, 1), 4),
    }


def _parse_pages(value: str) -> list[str]:
    page_ids = [item.strip() for item in value.split(",") if item.strip()]
    unknown = [page_id for page_id in page_ids if page_id not in PAGE_SPEC_BY_ID]
    if unknown:
        raise argparse.ArgumentTypeError(
            "未知页面："
            + ", ".join(unknown)
            + "；可选值："
            + ", ".join(DEFAULT_PAGE_IDS)
        )
    if not page_ids:
        raise argparse.ArgumentTypeError("--pages 不能为空")
    if len(set(page_ids)) != len(page_ids):
        raise argparse.ArgumentTypeError("--pages 不能包含重复页面")
    return page_ids


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="逐页验证联系达人工作流的 AXTree 覆盖率。",
    )
    parser.add_argument("--store-id", required=True, help="紫鸟店铺 ID")
    parser.add_argument(
        "--pages",
        type=_parse_pages,
        default=list(DEFAULT_PAGE_IDS),
        help="逗号分隔页面，默认验证全部 5 个页面形态",
    )
    parser.add_argument(
        "--creator",
        default="",
        help="达人用户名；动态搜索和自动详情/聊天导航需要",
    )
    parser.add_argument(
        "--message",
        default="",
        help="可选：用于精确验证已发送消息气泡 T-15",
    )
    parser.add_argument(
        "--skip-navigation",
        action="store_true",
        help="不执行自动导航，直接采集当前页面状态",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="JSON 报告输出路径",
    )
    parser.add_argument(
        "--max-elements",
        type=int,
        default=500,
        help="每个快照最多采集的可见交互 DOM 元素数",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=15,
        help="单个导航或动态渲染等待超时",
    )
    return parser


def _normalize_creator(value: str) -> str:
    bare = str(value or "").strip().lstrip("@").strip()
    return f"@{bare}" if bare else ""


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.max_elements <= 0:
        raise ValueError("--max-elements 必须大于 0")
    if args.timeout_seconds <= 0:
        raise ValueError("--timeout-seconds 必须大于 0")
    creator = _normalize_creator(args.creator)
    settings = ZiniaoSettings.from_env()
    page_reports: list[dict[str, Any]] = []
    connection_mode = ""
    with connect_reusable_store(settings, args.store_id) as connection:
        driver = connection.session.driver
        if driver is None:
            raise RuntimeError("紫鸟浏览器连接未返回 Selenium driver")
        connection_mode = connection.connection_mode
        for page_id in args.pages:
            page_reports.append(
                verify_page(
                    PAGE_SPEC_BY_ID[page_id],
                    driver,
                    creator=creator,
                    message=str(args.message or ""),
                    skip_navigation=bool(args.skip_navigation),
                    max_elements=int(args.max_elements),
                    timeout_seconds=float(args.timeout_seconds),
                )
            )
    report = {
        "meta": {
            "timestamp": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "store_id": str(args.store_id),
            "creator": creator,
            "pages_requested": len(args.pages),
            "pages_tested": sum(
                page.get("status") == "tested" for page in page_reports
            ),
            "connection_mode": connection_mode,
            "skip_navigation": bool(args.skip_navigation),
            "cdp_marking": _aggregate_marking(page_reports),
        },
        "per_page": page_reports,
        "summary": build_summary_reports(page_reports),
    }
    output = Path(args.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        report = run(args)
    except Exception as error:
        parser.exit(2, f"AXTree 验证失败：{type(error).__name__}: {error}\n")
    summary = report["summary"]
    print(
        json.dumps(
            {
                "output": str(Path(args.output).expanduser()),
                "overall_verdict": summary["overall_verdict"],
                "overall_coverage": summary["overall_coverage"],
                "ax_hit_rate": summary["ax_hit_rate"],
            },
            ensure_ascii=False,
        )
    )
    return 1 if summary["overall_verdict"] == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())
