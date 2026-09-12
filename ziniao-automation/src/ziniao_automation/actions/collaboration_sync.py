"""Read-only synchronization of in-progress target collaborations.

The synchronizer only performs navigation, tab selection, page refreshes, and
pagination.  It never opens an edit menu or clicks invitation/send controls.
"""

from __future__ import annotations

import json
import random
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    StaleElementReferenceException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support.ui import WebDriverWait

from ..errors import ZiniaoWorkflowError


IN_PROGRESS = "IN_PROGRESS"
TARGET_INVITATION_PATH = "/affiliate/collaboration/target-invitation"
_GROUP_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


_TABLE_SNAPSHOT_SCRIPT = r"""
const root = arguments[0];

const clone = value => {
  try {
    return JSON.parse(JSON.stringify(value));
  } catch (_) {
    return null;
  }
};

const isRecord = value => (
  value
  && typeof value === 'object'
  && !Array.isArray(value)
  && String(value.name || '').trim()
  && ('id' in value || 'invitation_group_id' in value)
  && (
    'group_status' in value
    || 'creator_cnt' in value
    || 'product_cnt' in value
  )
);

const fiberProps = node => {
  const key = Object.keys(node || {}).find(
    item => item.startsWith('__reactFiber$')
  );
  const props = [];
  let fiber = key ? node[key] : null;
  const seen = new Set();
  for (
    let depth = 0;
    fiber && depth < 40 && !seen.has(fiber);
    depth += 1, fiber = fiber.return
  ) {
    seen.add(fiber);
    for (const value of [fiber.memoizedProps, fiber.pendingProps]) {
      if (value && typeof value === 'object') {
        props.push(value);
      }
    }
  }
  return props;
};

let best = null;
const nodes = [
  root,
  root.querySelector('table'),
  ...root.querySelectorAll('tbody tr'),
].filter(Boolean);

for (const node of nodes) {
  for (const props of fiberProps(node)) {
    if (!Array.isArray(props.data)) {
      continue;
    }
    const records = props.data.filter(isRecord);
    const pagination = (
      props.pagination && typeof props.pagination === 'object'
        ? props.pagination
        : null
    );
    const qualifies = (
      records.length > 0
      || (
        pagination
        && Number(pagination.total || 0) === 0
      )
    );
    if (!qualifies) {
      continue;
    }
    const score = records.length + (pagination ? 10000 : 0);
    if (!best || score > best.score) {
      best = {
        score,
        records,
        pagination,
        loading: Boolean(props.loading),
        source: 'react-table',
      };
    }
  }
}

if (!best) {
  const records = [];
  for (const row of root.querySelectorAll('tbody tr')) {
    for (const props of fiberProps(row)) {
      if (isRecord(props.record)) {
        records.push(props.record);
        break;
      }
    }
  }
  if (records.length) {
    best = {
      score: records.length,
      records,
      pagination: null,
      loading: false,
      source: 'react-row',
    };
  }
}

const paginationNode = root.querySelector('.core-pagination');
const domPagination = () => {
  if (!paginationNode) {
    return null;
  }
  const active = paginationNode.querySelector(
    '.core-pagination-item-active'
  );
  const totalNode = paginationNode.querySelector(
    '.core-pagination-total-text'
  );
  const option = paginationNode.querySelector('.core-pagination-option');
  const totalMatch = String(totalNode?.innerText || '').match(/(\d+)/);
  const sizeMatch = String(option?.innerText || '').match(/(\d+)/);
  return {
    current: Number(String(active?.innerText || '').trim() || 1),
    pageSize: Number(sizeMatch?.[1] || 0),
    total: Number(totalMatch?.[1] || 0),
  };
};

const textNumber = (text, pattern) => {
  const match = String(text || '').match(pattern);
  return Number(match?.[1] || 0);
};

if (!best) {
  const records = [];
  for (const row of root.querySelectorAll('tbody tr')) {
    const cells = [...row.querySelectorAll('td')];
    if (!cells.length) {
      continue;
    }
    const cellTexts = cells.map(
      cell => String(cell.innerText || '').trim()
    );
    const first = cellTexts[0] || '';
    const lines = first
      .split(/\n+/)
      .map(value => value.trim())
      .filter(value => value && value !== '.');
    const name = lines[0] || '';
    if (!name) {
      continue;
    }
    const modifiedMatch = first.match(
      /(?:上次修改时间|Last modified(?: time)?)\s*[：:]\s*([^\n]+)/
    );
    records.push({
      name,
      invitationGroupId: '',
      productCount: textNumber(
        first,
        /(\d+)\s*(?:件商品|products?)/i
      ),
      invitedCreatorCount: textNumber(
        first,
        /(?:已邀请|Invited)\s*(\d+)/i
      ),
      acceptedCreatorCount: textNumber(
        cellTexts[1],
        /(\d+)/
      ),
      promotedCreatorCount: textNumber(
        cellTexts[2],
        /(\d+)/
      ),
      modifiedAt: String(modifiedMatch?.[1] || '').trim(),
      rawData: {
        source: 'dom',
        rowText: String(row.innerText || '').trim(),
        cellTexts,
      },
    });
  }
  best = {
    records,
    pagination: domPagination(),
    loading: Boolean(root.querySelector('[aria-busy="true"]')),
    source: 'dom',
  };
}

const emptyState = Boolean(
  root.querySelector(
    '[data-e2e="e40b343e-b0b8-cac3"], '
    + '[data-e2e="aea7f929-6aad-cb39"]'
  )
);

return {
  records: clone(best.records) || [],
  pagination: clone(best.pagination || domPagination()),
  loading: Boolean(best.loading),
  emptyState,
  source: best.source,
};
"""


@dataclass(frozen=True)
class CollaborationOption:
    """One target-collaboration option safe for service synchronization."""

    name: str
    invitationGroupId: str
    status: str
    productCount: int
    invitedCreatorCount: int
    acceptedCreatorCount: int
    promotedCreatorCount: int
    modifiedAt: str
    rawData: dict[str, Any] = field(repr=False)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _PageSnapshot:
    records: tuple[dict[str, Any], ...]
    current_page: int
    page_size: int
    total: int
    source: str
    loading: bool = False
    empty_state: bool = False

    @classmethod
    def from_mapping(cls, value: object) -> "_PageSnapshot":
        mapping = value if isinstance(value, dict) else {}
        raw_records = mapping.get("records")
        records = tuple(
            record
            for record in (
                raw_records if isinstance(raw_records, list) else []
            )
            if isinstance(record, dict)
        )
        pagination = mapping.get("pagination")
        pagination = pagination if isinstance(pagination, dict) else {}
        return cls(
            records=records,
            current_page=_as_int(pagination.get("current"), default=1),
            page_size=_as_int(pagination.get("pageSize"), default=0),
            total=_as_int(pagination.get("total"), default=-1),
            source=str(mapping.get("source") or "unknown"),
            loading=bool(mapping.get("loading", False)),
            empty_state=bool(mapping.get("emptyState", False)),
        )

    @property
    def ready(self) -> bool:
        return (
            not self.loading
            and (
                bool(self.records)
                or self.total == 0
                or self.empty_state
            )
        )

    @property
    def fingerprint(self) -> str:
        keys = []
        for record in self.records:
            identifier = str(
                record.get("id")
                or record.get("invitation_group_id")
                or record.get("invitationGroupId")
                or ""
            )
            keys.append(
                identifier
                or "|".join(
                    (
                        str(record.get("name") or ""),
                        str(
                            record.get("update_time")
                            or record.get("modifiedAt")
                            or ""
                        ),
                    )
                )
            )
        return json.dumps(
            [self.current_page, keys],
            ensure_ascii=False,
            separators=(",", ":"),
        )


def _as_int(value: object, *, default: int = 0) -> int:
    try:
        return max(0, int(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        match = re.search(r"-?\d+", str(value or ""))
        return max(0, int(match.group(0))) if match else default


def _group_id(value: object) -> str:
    if isinstance(value, bool):
        normalized = ""
    elif isinstance(value, (str, int)):
        normalized = str(value).strip()
    else:
        normalized = ""
    if not _GROUP_ID_PATTERN.fullmatch(normalized):
        raise ZiniaoWorkflowError(
            "邀请列表存在缺失或无效的 invitationGroupId，"
            "已拒绝返回不完整同步结果。"
        )
    return normalized


class TargetCollaborationSync:
    """Synchronize all in-progress invitations from one attached store."""

    def __init__(
        self,
        driver: WebDriver,
        *,
        timeout_seconds: int = 45,
        max_pages: int = 200,
    ):
        self.driver = driver
        self.timeout_seconds = timeout_seconds
        self.max_pages = max_pages

    def _wait(
        self,
        condition: Any,
        *,
        message: str,
        timeout_seconds: int | None = None,
    ) -> Any:
        try:
            return WebDriverWait(
                self.driver,
                timeout_seconds or self.timeout_seconds,
                poll_frequency=0.25,
                ignored_exceptions=(StaleElementReferenceException,),
            ).until(condition)
        except Exception as error:
            raise ZiniaoWorkflowError(message) from error

    def _wait_for_document(self) -> None:
        self._wait(
            lambda driver: driver.execute_script(
                "return document.readyState"
            )
            in {"interactive", "complete"},
            message="定向合作页面在限定时间内未完成加载。",
        )

    @staticmethod
    def _visible(element: WebElement) -> bool:
        try:
            rect = element.rect
            return (
                element.is_displayed()
                and rect.get("width", 0) > 0
                and rect.get("height", 0) > 0
            )
        except StaleElementReferenceException:
            return False

    def _click_read_only(self, element: WebElement) -> None:
        """Click a navigation/tab/pagination control, never a write control."""
        time.sleep(round(random.uniform(1.0, 2.0), 3))
        self.driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'});",
            element,
        )
        try:
            element.click()
        except ElementClickInterceptedException:
            self.driver.execute_script("arguments[0].click();", element)

    def _click_navigation_in_current_tab(self, element: WebElement) -> None:
        """Click a read-only link while reusing the current browser tab."""
        time.sleep(round(random.uniform(1.0, 2.0), 3))
        self.driver.execute_script(
            """
            const target = arguments[0];
            target.scrollIntoView({block: 'center'});
            const originalOpen = window.open;
            window.open = function(url) {
              if (url) {
                window.location.assign(String(url));
                return window;
              }
              return originalOpen.apply(window, arguments);
            };
            window.setTimeout(() => {
              if (window.open !== originalOpen) {
                window.open = originalOpen;
              }
            }, 10000);
            target.click();
            """,
            element,
        )

    @staticmethod
    def _is_target_url(url: str) -> bool:
        parsed = urlparse(str(url or ""))
        host = (parsed.hostname or "").lower()
        return (
            parsed.scheme in {"http", "https"}
            and TargetCollaborationSync._is_trusted_shop_host(host)
            and host.startswith("affiliate.")
            and parsed.path.rstrip("/") == TARGET_INVITATION_PATH
        )

    @staticmethod
    def _is_trusted_shop_host(host: str) -> bool:
        normalized = str(host or "").lower()
        return (
            normalized.endswith(".tiktokshopglobalselling.com")
            or normalized.endswith(".tiktokglobalshop.com")
            or normalized.endswith(".tiktokshop.com")
            or normalized.endswith(".tiktok.com")
        )

    @classmethod
    def _affiliate_url_rank(cls, url: str) -> int:
        parsed = urlparse(str(url or ""))
        host = (parsed.hostname or "").lower()
        path = parsed.path.rstrip("/").lower()
        if (
            parsed.scheme not in {"http", "https"}
            or not cls._is_trusted_shop_host(host)
        ):
            return 0
        if host.startswith("affiliate."):
            if path == TARGET_INVITATION_PATH:
                return 500
            query = parse_qs(parsed.query)
            if str((query.get("shop_id") or [""])[0]).strip():
                return 450
            return 400
        return 0

    def _window_urls(self) -> dict[str, str]:
        handles = set(self.driver.window_handles)
        urls: dict[str, str] = {}
        try:
            payload = self.driver.execute_cdp_cmd("Target.getTargets", {})
        except Exception:
            payload = {}
        infos = (
            payload.get("targetInfos")
            if isinstance(payload, dict)
            and isinstance(payload.get("targetInfos"), list)
            else []
        )
        for info in infos:
            if not isinstance(info, dict) or info.get("type") != "page":
                continue
            target_id = str(info.get("targetId") or "")
            handle = next(
                (
                    candidate
                    for candidate in (target_id, f"CDwindow-{target_id}")
                    if candidate in handles
                ),
                "",
            )
            if handle:
                urls[handle] = str(info.get("url") or "")
        try:
            urls.setdefault(
                self.driver.current_window_handle,
                self.driver.current_url,
            )
        except Exception:
            pass
        return urls

    def _activate_target_window(self) -> str | bool:
        try:
            if self._is_target_url(self.driver.current_url):
                return self.driver.current_url
        except Exception:
            pass
        handles = list(self.driver.window_handles)
        urls = self._window_urls()
        prioritized = [
            handle
            for handle in reversed(handles)
            if self._is_target_url(urls.get(handle, ""))
        ]
        prioritized.extend(
            handle
            for handle in reversed(handles)
            if handle not in prioritized and handle not in urls
        )
        for handle in prioritized:
            try:
                self.driver.switch_to.window(handle)
                if self._is_target_url(self.driver.current_url):
                    return self.driver.current_url
            except Exception:
                continue
        return False

    def _affiliate_window(self) -> str | None:
        handles = list(self.driver.window_handles)
        urls = self._window_urls()
        ranked: list[tuple[int, int, str]] = []
        for index, handle in enumerate(handles):
            try:
                url = urls.get(handle, "")
                if not url:
                    self.driver.switch_to.window(handle)
                    url = self.driver.current_url
                rank = self._affiliate_url_rank(url)
                if rank:
                    ranked.append((rank, index, handle))
            except Exception:
                continue
        if not ranked:
            return None
        _rank, _index, selected = max(ranked)
        self.driver.switch_to.window(selected)
        return selected

    @staticmethod
    def _is_seller_url(url: str) -> bool:
        parsed = urlparse(str(url or ""))
        host = (parsed.hostname or "").lower()
        path = parsed.path.lower()
        return (
            parsed.scheme in {"http", "https"}
            and (
                host.startswith("seller.")
                or host.startswith("shop.")
            )
            and TargetCollaborationSync._is_trusted_shop_host(host)
            and not path.startswith(("/login", "/signin"))
        )

    @staticmethod
    def _is_seller_affiliate_landing_url(url: str) -> bool:
        parsed = urlparse(str(url or ""))
        host = (parsed.hostname or "").lower()
        return (
            TargetCollaborationSync._is_trusted_shop_host(host)
            and host.startswith(("seller.", "shop."))
            and parsed.path.rstrip("/").lower() == "/affiliate/landing"
        )

    def _seller_window(self) -> tuple[str, str] | None:
        handles = list(self.driver.window_handles)
        urls = self._window_urls()
        for handle in reversed(handles):
            try:
                url = urls.get(handle, "")
                if not url:
                    self.driver.switch_to.window(handle)
                    url = self.driver.current_url
                if self._is_seller_url(url):
                    self.driver.switch_to.window(handle)
                    return handle, url
            except Exception:
                continue
        return None

    def _normalize_affiliate_action(
        self,
        element: WebElement,
    ) -> WebElement:
        try:
            if (element.tag_name or "").lower() == "a":
                return element
        except StaleElementReferenceException:
            return element
        try:
            descendants = [
                candidate
                for candidate in element.find_elements(
                    By.CSS_SELECTOR,
                    'a[href*="/affiliate"], '
                    'a[href*="affiliate.tiktokshop"]',
                )
                if self._visible(candidate)
            ]
        except StaleElementReferenceException:
            descendants = []
        if len(descendants) == 1:
            return descendants[0]
        return element

    @classmethod
    def _affiliate_action_identity(
        cls,
        element: WebElement,
    ) -> tuple[str, int]:
        try:
            href = str(element.get_attribute("href") or "").strip()
            text = " ".join(str(element.text or "").split()).casefold()
            tag = str(element.tag_name or "").lower()
            role = str(element.get_attribute("role") or "").lower()
        except StaleElementReferenceException:
            return "", 0

        if href:
            parsed = urlparse(href)
            host = (parsed.hostname or "").lower()
            path = parsed.path.rstrip("/").lower()
            if (
                parsed.scheme in {"http", "https"}
                and cls._is_trusted_shop_host(host)
                and host.startswith("affiliate.")
            ):
                return "affiliate-center", 950
            if (
                cls._is_trusted_shop_host(host)
                and host.startswith(("seller.", "shop."))
                and path.startswith("/affiliate")
            ):
                return "affiliate-landing", (
                    1000 if path == "/affiliate/landing" else 900
                )

        exact_labels = {"联盟", "affiliate"}
        center_labels = {
            "前往联盟中心首页",
            "go to affiliate center homepage",
            "go to affiliate center",
        }
        if text in center_labels:
            return "affiliate-landing", 800
        if text in exact_labels:
            if tag == "a":
                return "affiliate-landing", 750
            if role in {"menuitem", "button"} or tag == "button":
                return "affiliate-landing", 650
        if "联盟" in text or "affiliate" in text:
            return f"element:{element.id}", 300
        return "", 0

    def _affiliate_entry(self) -> WebElement | None:
        raw_candidates: dict[str, WebElement] = {}
        selectors = (
            (
                By.CSS_SELECTOR,
                'a[href*="/affiliate"], a[href*="affiliate.tiktokshop"]',
            ),
            (
                By.XPATH,
                "//*[self::a or self::button or @role='menuitem' "
                "or @role='button'][contains(normalize-space(.), '联盟') "
                "or contains(normalize-space(.), 'Affiliate')]",
            ),
        )
        for by, selector in selectors:
            for element in self.driver.find_elements(by, selector):
                if self._visible(element):
                    raw_candidates[element.id] = element

        semantic_candidates: dict[
            str,
            tuple[int, WebElement],
        ] = {}
        for raw_element in raw_candidates.values():
            element = self._normalize_affiliate_action(raw_element)
            identity, score = self._affiliate_action_identity(element)
            if not identity or not score or not self._visible(element):
                continue
            previous = semantic_candidates.get(identity)
            if previous is None or score > previous[0]:
                semantic_candidates[identity] = (score, element)

        if not semantic_candidates:
            return None
        highest_score = max(
            score for score, _element in semantic_candidates.values()
        )
        winners = [
            element
            for score, element in semantic_candidates.values()
            if score == highest_score
        ]
        if len(winners) == 1:
            return winners[0]
        raise ZiniaoWorkflowError(
            "店铺首页存在多个语义不同且同优先级的"
            "“联盟/Affiliate”入口，已停止只读同步。"
        )

    def _open_affiliate_from_store(self) -> str | None:
        """Reuse a seller tab and navigate it into the Affiliate Center."""
        seller = self._seller_window()
        if seller is None:
            return None
        self.driver.switch_to.window(seller[0])

        # Reuse the seller tab.  Its /affiliate/landing bridge already contains
        # the target-collaboration card, so it is a valid navigation surface but
        # not an affiliate.* window from which a direct URL can be synthesized.
        for _hop in range(3):
            affiliate = self._affiliate_window()
            if affiliate is not None:
                return affiliate

            seller = self._seller_window()
            if seller is None:
                break
            if self._is_seller_affiliate_landing_url(seller[1]):
                return seller[0]
            self._wait_for_document()
            destination = self._wait(
                lambda _driver: (
                    ("affiliate", affiliate_handle)
                    if (
                        affiliate_handle := self._affiliate_window()
                    )
                    else (
                        ("entry", entry)
                        if (entry := self._affiliate_entry()) is not None
                        else False
                    )
                ),
                message=(
                    "已复用店铺浏览器，但店铺页未找到可确认的"
                    "“联盟/Affiliate”入口。"
                ),
            )
            if destination[0] == "affiliate":
                return str(destination[1])

            entry = destination[1]
            href = str(entry.get_attribute("href") or "").strip()
            parsed = urlparse(href)
            host = (parsed.hostname or "").lower()
            path = parsed.path.rstrip("/").lower()
            navigate_in_place = (
                parsed.scheme in {"http", "https"}
                and self._is_trusted_shop_host(host)
                and (
                    host.startswith("affiliate.")
                    or (
                        host.startswith(("seller.", "shop."))
                        and path.startswith("/affiliate")
                    )
                )
            )
            if navigate_in_place:
                self.driver.get(href)
                self._wait_for_document()
            else:
                self._click_read_only(entry)

        return self._wait(
            lambda _driver: self._affiliate_window() or False,
            message=(
                "从店铺首页进入联盟页面失败：导航未到达"
                " affiliate.* 联盟中心。"
            ),
        )

    @staticmethod
    def _direct_target_url(current_url: str) -> str:
        parsed = urlparse(current_url)
        host = (parsed.hostname or "").lower()
        if (
            not host.startswith("affiliate.")
            or not TargetCollaborationSync._is_trusted_shop_host(host)
        ):
            return ""
        query = parse_qs(parsed.query)
        shop_id = str((query.get("shop_id") or [""])[0]).strip()
        if not shop_id:
            return ""
        flat_query = {
            key: str(query[key][-1])
            for key in ("shop_id", "shop_region", "region", "lang", "locale")
            if query.get(key)
        }
        flat_query["tab"] = "1"
        return urlunparse(
            (
                parsed.scheme or "https",
                parsed.netloc,
                TARGET_INVITATION_PATH,
                "",
                urlencode(flat_query),
                "",
            )
        )

    def _landing_target_card(self) -> WebElement | None:
        raw_candidates: dict[str, WebElement] = {}
        for element in self.driver.find_elements(
            By.CSS_SELECTOR,
            f'a[href*="{TARGET_INVITATION_PATH}"]',
        ):
            if self._visible(element):
                raw_candidates[element.id] = element
        labels = (
            "定向合作设置",
            "Target collaboration settings",
            "Target Collaboration Settings",
        )
        for label in labels:
            literal = _xpath_literal(label)
            for element in self.driver.find_elements(
                By.XPATH,
                "//*[normalize-space()="
                f"{literal}]"
                "/ancestor-or-self::*[self::a or self::button "
                "or @role='link' or @role='button' "
                "or contains(@class, 'cursor-pointer')][1]",
            ):
                if self._visible(element):
                    raw_candidates[element.id] = element

        if not raw_candidates:
            for label in labels:
                literal = _xpath_literal(label)
                for text_element in self.driver.find_elements(
                    By.XPATH,
                    f"//*[normalize-space()={literal}]",
                ):
                    try:
                        element = self.driver.execute_script(
                            """
                            for (
                              let node = arguments[0], depth = 0;
                              node && depth < 5;
                              node = node.parentElement, depth += 1
                            ) {
                              if (typeof node.onclick === 'function') {
                                return node;
                              }
                            }
                            return null;
                            """,
                            text_element,
                        )
                    except StaleElementReferenceException:
                        continue
                    if element is not None and self._visible(element):
                        raw_candidates[element.id] = element

        # A card wrapper and its nested anchor are often both returned by the
        # selectors above.  Collapse them to one semantic destination so a DOM
        # wrapper change does not look like two different write-sensitive
        # actions.
        candidates: dict[str, WebElement] = {}
        for raw_element in raw_candidates.values():
            element = self._normalize_affiliate_action(raw_element)
            try:
                href = str(element.get_attribute("href") or "").strip()
                parsed = urlparse(href)
                host = (parsed.hostname or "").lower()
                path = parsed.path.rstrip("/").lower()
            except StaleElementReferenceException:
                continue
            if (
                parsed.scheme in {"http", "https"}
                and self._is_trusted_shop_host(host)
                and host.startswith("affiliate.")
                and path == TARGET_INVITATION_PATH
            ):
                shop_id = str(
                    (parse_qs(parsed.query).get("shop_id") or [""])[0]
                ).strip()
                identity = f"target:{host}:{shop_id}"
            else:
                identity = f"element:{element.id}"
            candidates[identity] = element
        if len(candidates) == 1:
            return next(iter(candidates.values()))
        if len(candidates) > 1:
            raise ZiniaoWorkflowError(
                "联盟首页出现多个“定向合作设置”入口，已停止只读同步。"
            )
        return None

    def open_target_page(self) -> str:
        """Open or refresh the target-collaboration page without writes."""
        if self._activate_target_window():
            time.sleep(round(random.uniform(5.0, 8.0), 3))
            self.driver.refresh()
            self._wait_for_document()
            return self.driver.current_url

        if (
            self._affiliate_window() is None
            and self._open_affiliate_from_store() is None
        ):
            raise ZiniaoWorkflowError(
                "当前复用浏览器没有已登录的 TikTok Shop "
                "店铺页或联盟页面。"
            )

        direct_url = self._direct_target_url(self.driver.current_url)
        if direct_url:
            self.driver.get(direct_url)
        else:
            card = self._wait(
                lambda _driver: self._landing_target_card() or False,
                message="联盟首页未找到唯一“定向合作设置”入口。",
            )
            self._click_navigation_in_current_tab(card)
            self._wait(
                lambda _driver: self._activate_target_window(),
                message="点击“定向合作设置”后未进入定向合作列表。",
            )

        self._wait_for_document()
        if not self._is_target_url(self.driver.current_url):
            raise ZiniaoWorkflowError(
                "当前页面不是定向合作列表，已停止同步。"
            )
        return self.driver.current_url

    def _in_progress_tab(self) -> WebElement | None:
        candidates: dict[str, WebElement] = {}
        for tab in self.driver.find_elements(By.CSS_SELECTOR, "[role='tab']"):
            try:
                text = " ".join(tab.text.split()).casefold()
                if (
                    self._visible(tab)
                    and (
                        text.startswith("进行中")
                        or text.startswith("in progress")
                    )
                ):
                    candidates[tab.id] = tab
            except StaleElementReferenceException:
                continue
        if len(candidates) == 1:
            return next(iter(candidates.values()))
        if len(candidates) > 1:
            raise ZiniaoWorkflowError(
                "定向合作页出现多个“进行中”页签，已停止同步。"
            )
        return None

    @staticmethod
    def _tab_active(tab: WebElement) -> bool:
        return (
            tab.get_attribute("aria-selected") == "true"
            or "active" in (tab.get_attribute("class") or "").lower()
        )

    def activate_in_progress(self) -> None:
        tab = self._wait(
            lambda _driver: self._in_progress_tab() or False,
            message="定向合作页未找到“进行中”页签。",
        )
        if not self._tab_active(tab):
            self._click_read_only(tab)
        self._wait(
            lambda _driver: (
                current
                if (
                    (current := self._in_progress_tab()) is not None
                    and self._tab_active(current)
                )
                else False
            ),
            message="“进行中”页签未成功激活。",
        )

    def _table_root(self) -> WebElement | None:
        candidates: dict[str, WebElement] = {}
        selectors = (
            ".core-tabs-content-item-active div.core-table",
            "div.core-tabs-pane div.core-table",
        )
        for selector in selectors:
            for element in self.driver.find_elements(
                By.CSS_SELECTOR,
                selector,
            ):
                try:
                    if (
                        self._visible(element)
                        and element.find_elements(By.CSS_SELECTOR, "table")
                        and element.find_elements(
                            By.CSS_SELECTOR,
                            ".core-pagination",
                        )
                    ):
                        candidates[element.id] = element
                except StaleElementReferenceException:
                    continue
        if len(candidates) == 1:
            return next(iter(candidates.values()))
        if len(candidates) > 1:
            raise ZiniaoWorkflowError(
                "“进行中”页签内出现多个邀请表格，已停止同步。"
            )
        return None

    def _read_snapshot(self) -> _PageSnapshot:
        root = self._table_root()
        if root is None:
            raise ZiniaoWorkflowError("“进行中”页签未显示邀请表格。")
        value = self.driver.execute_script(_TABLE_SNAPSHOT_SCRIPT, root)
        return _PageSnapshot.from_mapping(value)

    def _wait_for_ready_snapshot(self) -> _PageSnapshot:
        def ready_snapshot(_driver: WebDriver) -> _PageSnapshot | bool:
            try:
                snapshot = self._read_snapshot()
            except ZiniaoWorkflowError:
                return False
            return snapshot if snapshot.ready else False

        return self._wait(
            ready_snapshot,
            message="“进行中”邀请列表未完成加载。",
        )

    def _next_page_button(self) -> WebElement | None:
        root = self._table_root()
        if root is None:
            return None
        buttons = root.find_elements(
            By.CSS_SELECTOR,
            ".core-pagination-item-next",
        )
        visible = [button for button in buttons if self._visible(button)]
        if len(visible) != 1:
            if visible:
                raise ZiniaoWorkflowError(
                    "邀请列表出现多个下一页按钮，已停止同步。"
                )
            return None
        button = visible[0]
        classes = (button.get_attribute("class") or "").lower()
        if (
            "disabled" in classes
            or button.get_attribute("aria-disabled") == "true"
        ):
            return None
        return button

    @staticmethod
    def _option_key(option: CollaborationOption) -> str:
        return f"id:{option.invitationGroupId}"

    @staticmethod
    def _modified_at(value: object) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            timestamp = int(text)
        except ValueError:
            timestamp = 0
        if timestamp:
            seconds = timestamp / 1000 if timestamp > 10_000_000_000 else timestamp
            try:
                return (
                    datetime.fromtimestamp(seconds, tz=timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z")
                )
            except (OverflowError, OSError, ValueError):
                pass
        date_match = re.fullmatch(
            r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})",
            text,
        )
        if date_match:
            year, month, day = map(int, date_match.groups())
            try:
                return datetime(year, month, day).date().isoformat()
            except ValueError:
                return text
        return text

    @classmethod
    def option_from_record(
        cls,
        record: dict[str, Any],
    ) -> CollaborationOption:
        name = str(record.get("name") or "").strip()
        if not name:
            raise ZiniaoWorkflowError("邀请列表中存在缺少名称的记录。")
        raw_data = record.get("rawData")
        if not isinstance(raw_data, dict):
            raw_data = dict(record)
        invitation_group_id = _group_id(
            record.get("id")
            or record.get("invitation_group_id")
            or record.get("invitationGroupId")
        )
        return CollaborationOption(
            name=name,
            invitationGroupId=invitation_group_id,
            status=IN_PROGRESS,
            productCount=_as_int(
                record.get("product_cnt", record.get("productCount"))
            ),
            invitedCreatorCount=_as_int(
                record.get(
                    "creator_cnt",
                    record.get("invitedCreatorCount"),
                )
            ),
            acceptedCreatorCount=_as_int(
                record.get(
                    "creator_added_cnt",
                    record.get("acceptedCreatorCount"),
                )
            ),
            promotedCreatorCount=_as_int(
                record.get(
                    "creator_posted_cnt",
                    record.get("promotedCreatorCount"),
                )
            ),
            modifiedAt=cls._modified_at(
                record.get("update_time", record.get("modifiedAt"))
            ),
            rawData=raw_data,
        )

    def sync_in_progress(self) -> list[dict[str, Any]]:
        """Return every in-progress collaboration across all pages."""
        self.open_target_page()
        self.activate_in_progress()
        snapshot = self._wait_for_ready_snapshot()
        options: dict[str, CollaborationOption] = {}
        fingerprints: set[str] = set()
        expected_total = snapshot.total

        for _page_index in range(self.max_pages):
            if snapshot.fingerprint in fingerprints:
                raise ZiniaoWorkflowError(
                    "邀请分页没有前进，已停止以避免无限循环。"
                )
            fingerprints.add(snapshot.fingerprint)

            for record in snapshot.records:
                option = self.option_from_record(record)
                option_key = self._option_key(option)
                if option_key in options:
                    raise ZiniaoWorkflowError(
                        "邀请列表出现重复 invitationGroupId，"
                        "已拒绝返回不完整同步结果。"
                    )
                options[option_key] = option

            expected_total = max(expected_total, snapshot.total)
            if (
                expected_total == 0
                or (
                    expected_total > 0
                    and len(options) >= expected_total
                )
            ):
                break

            next_button = self._next_page_button()
            if next_button is None:
                if expected_total > 0:
                    raise ZiniaoWorkflowError(
                        "邀请列表分页提前结束，"
                        "未抓取到全部进行中邀请。"
                    )
                break
            previous_fingerprint = snapshot.fingerprint
            self._click_read_only(next_button)
            snapshot = self._wait(
                lambda _driver: (
                    candidate
                    if (
                        (candidate := self._read_snapshot()).ready
                        and candidate.fingerprint != previous_fingerprint
                    )
                    else False
                ),
                message="点击下一页后邀请列表内容未更新。",
            )
        else:
            raise ZiniaoWorkflowError(
                f"邀请分页超过安全上限 {self.max_pages} 页。"
            )

        if expected_total > 0 and len(options) != expected_total:
            raise ZiniaoWorkflowError(
                "进行中邀请数量与页面总数不一致，拒绝返回部分结果。"
            )
        return [option.to_dict() for option in options.values()]

    def sync(self) -> list[dict[str, Any]]:
        """Run read-only synchronization and restore the caller's tab."""
        try:
            original_handle = self.driver.current_window_handle
        except Exception:
            original_handle = ""
        try:
            original_handles = set(self.driver.window_handles)
        except Exception:
            original_handles = set()
        try:
            return self.sync_in_progress()
        finally:
            try:
                for handle in list(self.driver.window_handles):
                    if handle in original_handles:
                        continue
                    try:
                        self.driver.switch_to.window(handle)
                        if self._is_target_url(self.driver.current_url):
                            time.sleep(
                                round(random.uniform(1.0, 2.0), 3)
                            )
                        self.driver.close()
                    except Exception:
                        continue
                remaining = set(self.driver.window_handles)
                if original_handle and original_handle in remaining:
                    self.driver.switch_to.window(original_handle)
            except Exception:
                pass


def _xpath_literal(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ', "\'", '.join(
        f"'{part}'" for part in parts
    ) + ")"
