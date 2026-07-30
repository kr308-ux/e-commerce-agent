"""Validated TikTok Shop creator-contact actions.

Each public method performs one visible action and verifies the resulting page
state before returning structured evidence. Remote mutations are isolated in
dedicated methods that require explicit confirmation and re-check the recipient.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, urlparse

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    StaleElementReferenceException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support.ui import WebDriverWait

from ..dom_fallback import DeepSeekDomFallback, is_dom_failure_message
from ..errors import ZiniaoWorkflowError


APPROVED_GREETING_MESSAGE = (
    "Hi We’re obsessed with your content 🥰\n"
    "Your aesthetic goes so well with VAELOS activewear ✨\n"
    "We’re a reliable brand selling soft, stretchy yoga & gym wear 🩳🧘‍♀️\n"
    "We sent you an official collab invite: free samples + high commission 🎁💰\n"
    "Accept it in your TikTok dashboard to get your products ASAP 🚀\n"
    "Let’s partner long term and make great content together! 🤩"
)

FIND_CREATORS_RENDER_WAIT_SECONDS = 3.0

FIND_CREATORS_OBSTRUCTION_CLOSE_SELECTORS = (
    (
        By.CSS_SELECTOR,
        (
            "#garfish_app_for_connection_x3s3dld3 > div > "
            "div:nth-child(2) > div.mb-16.rounded-8 > div > div > div > "
            "div.mb-16 > div > "
            "div.transition-all.duration-300.ease-out.opacity-100 > div > "
            "div > div > div > div.mt-10.flex.items-center."
            "justify-between.gap-12 > "
            "div.flex.flex-none.items-center.gap-20.pl-8 > "
            "div > div > button"
        ),
    ),
    (
        By.CSS_SELECTOR,
        (
            '[id^="garfish_app_for_connection_"] > div > '
            "div:nth-child(2) > div.mb-16.rounded-8 > div > div > div > "
            "div.mb-16 > div > "
            "div.transition-all.duration-300.ease-out.opacity-100 > div > "
            "div > div > div > div.mt-10.flex.items-center."
            "justify-between.gap-12 > "
            "div.flex.flex-none.items-center.gap-20.pl-8 > "
            "div > div > button"
        ),
    ),
    (
        By.CSS_SELECTOR,
        (
            '[id^="garfish_app_for_connection_"] '
            "div.transition-all.duration-300.ease-out.opacity-100 > "
            "div.flex.h-32.flex-none.items-center.gap-8 "
            'button[role="switch"]'
        ),
    ),
    (
        By.CSS_SELECTOR,
        (
            "#garfish_app_for_connection_0lgo6vyn > div > "
            "div:nth-child(2) > div.mb-16.rounded-8 > div > div > div > "
            "div.mb-16 > div > "
            "div.transition-all.duration-300.ease-out.opacity-100 > div > "
            "div.flex.h-32.flex-none.items-center.gap-8 > div > button"
        ),
    ),
    (
        By.CSS_SELECTOR,
        (
            '[id^="garfish_app_for_connection_"] > div > '
            "div:nth-child(2) > div.mb-16.rounded-8 > div > div > div > "
            "div.mb-16 > div > "
            "div.transition-all.duration-300.ease-out.opacity-100 > div > "
            "div.flex.h-32.flex-none.items-center.gap-8 > div > button"
        ),
    ),
)

FIND_CREATORS_SEARCH_SELECTORS = (
    (By.CSS_SELECTOR, "input.core-input[type='text']"),
    (By.CSS_SELECTOR, "input[placeholder*='搜索姓名']"),
    (By.CSS_SELECTOR, "input[placeholder*='Search']"),
    (
        By.XPATH,
        "//input[@type='text' and "
        "(contains(@placeholder, '姓名') "
        "or contains(@placeholder, '达人') "
        "or contains(@placeholder, 'Search') "
        "or contains(@placeholder, 'search'))]",
    ),
)


@dataclass(frozen=True)
class WorkflowStepResult:
    step: int
    action: str
    success: bool
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CreatorContactWorkflow:
    """Operate the TikTok Shop affiliate UI one verified step at a time."""

    def __init__(
        self,
        driver: WebDriver,
        *,
        timeout_seconds: int = 30,
        dom_fallback: DeepSeekDomFallback | None = None,
    ):
        self.driver = driver
        self.timeout_seconds = timeout_seconds
        self.dom_fallback = dom_fallback
        self._action_wait_seconds: list[float] = []
        self._refresh_wait_seconds: list[float] = []
        self._verified_recipient: tuple[str, str] | None = None
        self._greeting_delivery_verified = False
        self._target_collaboration_verified = False
        self._invitation_dialog_verified = False
        self._selected_invitation: str | None = None
        self._selected_invitation_group_id: str | None = None
        self._created_invitation: dict[str, str] | None = None
        self._collaboration_card_delivery_verified = False
        self._find_creators_handle: str | None = None
        self._find_creators_url: str | None = None
        self._creator_detail_handle: str | None = None
        self._creator_detail_url: str | None = None
        self._chat_handle: str | None = None
        self._cdp_click_recovery_count = 0

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
            message="页面在限定时间内未完成加载。",
        )

    def _random_pause(
        self,
        minimum: float,
        maximum: float,
        *,
        refresh: bool = False,
    ) -> float:
        wait_seconds = round(random.uniform(minimum, maximum), 3)
        if refresh:
            self._refresh_wait_seconds.append(wait_seconds)
        else:
            self._action_wait_seconds.append(wait_seconds)
        time.sleep(wait_seconds)
        return wait_seconds

    def _click_fingerprint(self, element: WebElement) -> dict[str, Any]:
        try:
            fingerprint: dict[str, Any] = {
                "tag": str(element.tag_name or "").lower(),
                "text": " ".join(str(element.text or "").split()),
                "id": str(element.get_attribute("id") or ""),
                "dataE2e": str(element.get_attribute("data-e2e") or ""),
                "href": str(element.get_attribute("href") or ""),
                "ariaLabel": str(
                    element.get_attribute("aria-label") or ""
                ),
                "title": str(element.get_attribute("title") or ""),
                "name": str(element.get_attribute("name") or ""),
                "type": str(element.get_attribute("type") or ""),
                "placeholder": str(
                    element.get_attribute("placeholder") or ""
                ),
                "role": str(element.get_attribute("role") or ""),
                "className": " ".join(
                    str(element.get_attribute("class") or "").split()
                ),
            }
        except StaleElementReferenceException:
            return {}
        try:
            context = self.driver.execute_script(
                """
                const element = arguments[0];
                if (!element || !element.isConnected) return null;
                const root = element.closest(
                    'tr, [role="row"], [role="dialog"]'
                );
                if (!root) return null;
                const path = [];
                let current = element;
                while (current && current !== root) {
                    const parent = current.parentElement;
                    if (!parent) return null;
                    path.unshift(
                        Array.prototype.indexOf.call(
                            parent.children,
                            current
                        )
                    );
                    current = parent;
                }
                return {
                    contextTag: (root.tagName || '').toLowerCase(),
                    contextRole: root.getAttribute('role') || '',
                    contextText: (root.innerText || root.textContent || '')
                        .replace(/\\s+/g, ' ')
                        .trim(),
                    contextPath: path,
                };
                """,
                element,
            )
            if isinstance(context, dict):
                fingerprint.update(context)
        except StaleElementReferenceException:
            pass
        return fingerprint

    def _refind_click_target_with_cdp(
        self,
        fingerprint: dict[str, Any],
    ) -> WebElement | None:
        """Use CDP in the active tab to mark one uniquely matching redraw."""
        token = uuid.uuid4().hex
        payload = json.dumps(
            fingerprint,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        expression = f"""
            (() => {{
                const fp = {payload};
                const token = {json.dumps(token)};
                const marker = 'data-ziniao-cdp-click-target';
                const normalize = (value) =>
                    String(value || '').replace(/\\s+/g, ' ').trim();
                const attributePairs = [
                    ['id', 'id'],
                    ['data-e2e', 'dataE2e'],
                    ['href', 'href'],
                    ['aria-label', 'ariaLabel'],
                    ['title', 'title'],
                    ['name', 'name'],
                    ['type', 'type'],
                    ['placeholder', 'placeholder'],
                    ['role', 'role'],
                ];
                const hasStableIdentity =
                    Boolean(fp.text) ||
                    attributePairs.some(([, key]) => Boolean(fp[key]));
                const matches = (element) => {{
                    if (!element || !element.isConnected) return false;
                    if (
                        fp.tag &&
                        String(element.tagName || '').toLowerCase() !== fp.tag
                    ) return false;
                    if (
                        element.disabled ||
                        element.getAttribute('aria-disabled') === 'true'
                    ) return false;
                    const style = getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    if (
                        style.display === 'none' ||
                        style.visibility === 'hidden' ||
                        rect.width <= 0 ||
                        rect.height <= 0
                    ) return false;
                    if (
                        fp.text &&
                        normalize(element.innerText || element.textContent) !==
                            fp.text
                    ) return false;
                    for (const [attribute, key] of attributePairs) {{
                        if (
                            fp[key] &&
                            String(element.getAttribute(attribute) || '') !==
                                fp[key]
                        ) return false;
                    }}
                    if (
                        !hasStableIdentity &&
                        fp.className &&
                        normalize(element.getAttribute('class')) !==
                            fp.className
                    ) return false;
                    return true;
                }};
                let candidates = Array.from(
                    document.querySelectorAll(fp.tag || '*')
                ).filter(matches);
                if (
                    candidates.length !== 1 &&
                    fp.contextText &&
                    Array.isArray(fp.contextPath)
                ) {{
                    const contexts = Array.from(
                        document.querySelectorAll(
                            'tr, [role="row"], [role="dialog"]'
                        )
                    ).filter((context) => {{
                        if (
                            fp.contextTag &&
                            String(context.tagName || '').toLowerCase() !==
                                fp.contextTag
                        ) return false;
                        if (
                            fp.contextRole &&
                            String(context.getAttribute('role') || '') !==
                                fp.contextRole
                        ) return false;
                        return normalize(
                            context.innerText || context.textContent
                        ) === fp.contextText;
                    }});
                    const contextual = [];
                    for (const context of contexts) {{
                        let current = context;
                        for (const index of fp.contextPath) {{
                            current = current?.children?.[index];
                            if (!current) break;
                        }}
                        if (matches(current)) contextual.push(current);
                    }}
                    candidates = contextual;
                }}
                document.querySelectorAll(`[${{marker}}]`).forEach(
                    (element) => element.removeAttribute(marker)
                );
                if (candidates.length !== 1) {{
                    return {{matchCount: candidates.length}};
                }}
                candidates[0].setAttribute(marker, token);
                return {{matchCount: 1}};
            }})()
        """
        try:
            response = self.driver.execute_cdp_cmd(
                "Runtime.evaluate",
                {
                    "expression": expression,
                    "returnByValue": True,
                    "awaitPromise": False,
                },
            )
            result = (
                response.get("result", {}).get("value", {})
                if isinstance(response, dict)
                else {}
            )
            if result.get("matchCount") != 1:
                return None
            marked = self.driver.find_elements(
                By.CSS_SELECTOR,
                f'[data-ziniao-cdp-click-target="{token}"]',
            )
            visible = [
                candidate
                for candidate in marked
                if candidate.is_displayed() and candidate.is_enabled()
            ]
            if len(visible) != 1:
                return None
            self._cdp_click_recovery_count += 1
            return visible[0]
        except Exception:
            return None

    def _refind_click_target(
        self,
        fingerprint: dict[str, Any],
    ) -> WebElement:
        tag = fingerprint.get("tag") or "*"
        selectors: list[tuple[str, str]] = []
        for attribute, key in (
            ("id", "id"),
            ("data-e2e", "dataE2e"),
            ("href", "href"),
            ("aria-label", "ariaLabel"),
            ("title", "title"),
            ("name", "name"),
            ("type", "type"),
            ("placeholder", "placeholder"),
            ("role", "role"),
        ):
            value = fingerprint.get(key) or ""
            if value:
                selectors.append(
                    (
                        By.XPATH,
                        f"//{tag}[@{attribute}={self._xpath_literal(value)}]",
                    )
                )
        text = fingerprint.get("text") or ""
        if text:
            selectors.append(
                (
                    By.XPATH,
                    f"//{tag}[normalize-space()="
                    f"{self._xpath_literal(text)}]",
                )
            )
        for by, value in selectors:
            matches: dict[str, WebElement] = {}
            for candidate in self.driver.find_elements(by, value):
                try:
                    if candidate.is_displayed() and candidate.is_enabled():
                        matches[candidate.id] = candidate
                except StaleElementReferenceException:
                    continue
            if len(matches) == 1:
                return next(iter(matches.values()))
        cdp_match = self._refind_click_target_with_cdp(fingerprint)
        if cdp_match is not None:
            return cdp_match
        raise ZiniaoWorkflowError(
            "等待后点击目标已重绘，且无法重新定位唯一的同一元素。"
        )

    def _click(
        self,
        element: WebElement,
        *,
        validator: Callable[[WebElement], bool] | None = None,
        validation_message: str = "等待后点击目标已不再满足任务约束。",
        fixed_wait_seconds: float | None = None,
    ) -> None:
        fingerprint = self._click_fingerprint(element)
        if fixed_wait_seconds is None:
            self._random_pause(1.0, 2.0)
        else:
            wait_seconds = round(float(fixed_wait_seconds), 3)
            self._action_wait_seconds.append(wait_seconds)
            time.sleep(wait_seconds)
        target: WebElement | None = element
        last_stale: Exception | None = None
        for attempt in range(3):
            if target is None:
                try:
                    target = self._refind_click_target(fingerprint)
                except ZiniaoWorkflowError as error:
                    last_stale = error
                    if attempt < 2:
                        time.sleep(0.2)
                        continue
                    raise
            try:
                target.is_enabled()
                if validator is not None and not validator(target):
                    raise ZiniaoWorkflowError(validation_message)
                self.driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center', "
                    "inline: 'center'});",
                    target,
                )
                if validator is not None and not validator(target):
                    raise ZiniaoWorkflowError(validation_message)
                try:
                    target.click()
                except ElementClickInterceptedException:
                    self.driver.execute_script(
                        "arguments[0].click();",
                        target,
                    )
                return
            except StaleElementReferenceException as error:
                last_stale = error
                target = None
                if attempt < 2:
                    time.sleep(0.2)
                    continue
                break
        raise ZiniaoWorkflowError(
            "点击前目标连续重绘，CDP 有限重试后仍无法安全定位。"
        ) from last_stale

    def _refresh_page(self) -> float:
        """Refresh once, always respecting the 5–8 second page interval."""
        wait_seconds = self._random_pause(5.0, 8.0, refresh=True)
        self.driver.refresh()
        self._wait_for_document()
        return wait_seconds

    def _first_clickable(
        self,
        selectors: Iterable[tuple[str, str]],
        *,
        missing_message: str,
        timeout_seconds: int | None = None,
    ) -> WebElement:
        selector_list = tuple(selectors)

        def find(driver: WebDriver) -> WebElement | bool:
            for by, value in selector_list:
                for element in driver.find_elements(by, value):
                    try:
                        if element.is_displayed() and element.is_enabled():
                            return element
                    except StaleElementReferenceException:
                        continue
            return False

        try:
            return self._wait(
                find,
                message=missing_message,
                timeout_seconds=timeout_seconds,
            )
        except ZiniaoWorkflowError:
            recovered: WebElement | None = None
            if self.dom_fallback is not None:
                recovered = self.dom_fallback.locate(
                    self.driver,
                    purpose=missing_message,
                    attempted_selectors=selector_list,
                )
            late_local_match = find(self.driver)
            if late_local_match is not False:
                return late_local_match
            if recovered is not None:
                return recovered
            raise

    def consume_dom_fallback_events(self) -> list[dict[str, Any]]:
        if self.dom_fallback is None:
            return []
        return self.dom_fallback.consume_events()

    def diagnose_dom_failure(
        self,
        *,
        step_name: str,
        error_message: str,
    ) -> dict[str, Any]:
        if (
            self.dom_fallback is None
            or not is_dom_failure_message(error_message)
        ):
            return {}
        return self.dom_fallback.diagnose_failure(
            self.driver,
            step_name=step_name,
            error_message=error_message,
        )

    def _failure_evidence(self, step: int) -> dict[str, Any]:
        try:
            frame_count = len(self.driver.find_elements(By.CSS_SELECTOR, "iframe"))
        except Exception:
            frame_count = -1
        return {
            "currentUrl": self.driver.current_url,
            "title": self.driver.title,
            "iframeCount": frame_count,
            "domOnlyDiagnostics": True,
        }

    def _window_targets(self) -> dict[str, dict[str, str]]:
        """Read tab URLs through CDP without visibly cycling every tab."""
        handles = set(self.driver.window_handles)
        targets: dict[str, dict[str, str]] = {}
        try:
            payload = self.driver.execute_cdp_cmd("Target.getTargets", {})
        except Exception:
            payload = {}
        target_infos = (
            payload.get("targetInfos")
            if isinstance(payload, dict)
            and isinstance(payload.get("targetInfos"), list)
            else []
        )
        for info in target_infos:
            if not isinstance(info, dict) or info.get("type") != "page":
                continue
            target_id = str(info.get("targetId") or "")
            candidates = (target_id, f"CDwindow-{target_id}")
            handle = next(
                (candidate for candidate in candidates if candidate in handles),
                "",
            )
            if handle:
                targets[handle] = {
                    "url": str(info.get("url") or ""),
                    "targetId": target_id,
                }
        try:
            current_handle = self.driver.current_window_handle
            targets.setdefault(
                current_handle,
                {
                    "url": self.driver.current_url,
                    "targetId": "",
                },
            )
        except Exception:
            pass
        return targets

    def _activate_window_matching(
        self,
        url_markers: Iterable[str],
        *,
        preferred_handles: Iterable[str] = (),
    ) -> str | bool:
        markers = tuple(marker.lower() for marker in url_markers)
        original_handle = self.driver.current_window_handle
        handles = list(self.driver.window_handles)
        try:
            current_url = self.driver.current_url
            if any(marker in current_url.lower() for marker in markers):
                return current_url
        except Exception:
            pass
        target_map = self._window_targets()
        ordered = [
            handle
            for handle in preferred_handles
            if handle in handles
        ]
        ordered.extend(
            handle
            for handle in reversed(handles)
            if handle not in ordered
        )
        inspected: set[str] = set()
        for handle in ordered:
            known_url = str(
                (target_map.get(handle) or {}).get("url") or ""
            )
            if not known_url or not any(
                marker in known_url.lower() for marker in markers
            ):
                continue
            try:
                self.driver.switch_to.window(handle)
                inspected.add(handle)
                current_url = self.driver.current_url
                if any(marker in current_url.lower() for marker in markers):
                    return self.driver.current_url
            except Exception:
                continue
        for handle in ordered:
            if handle in inspected or handle in target_map:
                continue
            try:
                self.driver.switch_to.window(handle)
                current_url = self.driver.current_url.lower()
                if any(marker in current_url for marker in markers):
                    return self.driver.current_url
            except Exception:
                continue
        if original_handle in self.driver.window_handles:
            self.driver.switch_to.window(original_handle)
        return False

    def _click_and_wait_for_url(
        self,
        element: WebElement,
        *,
        url_markers: Iterable[str],
        failure_message: str,
    ) -> str:
        handles_before = set(self.driver.window_handles)
        source_handle = self.driver.current_window_handle
        urls_before = {
            handle: str(target.get("url") or "")
            for handle, target in self._window_targets().items()
        }
        self._click(element)

        def activate_clicked_destination(
            _driver: WebDriver,
        ) -> str | bool:
            handles_after = list(self.driver.window_handles)
            new_handles = [
                handle
                for handle in handles_after
                if handle not in handles_before
            ]
            changed_handles = [
                handle
                for handle, target in self._window_targets().items()
                if (
                    handle in handles_before
                    and str(target.get("url") or "")
                    != urls_before.get(handle, "")
                )
            ]
            return self._activate_window_matching(
                url_markers,
                preferred_handles=(
                    *new_handles,
                    *changed_handles,
                    source_handle,
                ),
            )

        url = self._wait(
            activate_clicked_destination,
            message=failure_message,
        )
        self._wait_for_document()
        return str(url)

    @staticmethod
    def _is_find_creators_list_url(url: str) -> bool:
        return (
            urlparse(str(url or "")).path.rstrip("/")
            == "/connection/creator"
        )

    @staticmethod
    def _store_page_priority(url: object) -> int:
        parsed = urlparse(str(url or "").strip())
        host = (parsed.hostname or "").lower().rstrip(".")
        path = parsed.path.lower().rstrip("/")
        if parsed.scheme not in {"http", "https"}:
            return 0
        is_tiktok_shop = (
            host == "tiktokshopglobalselling.com"
            or host.endswith(".tiktokshopglobalselling.com")
            or host == "tiktokglobalshop.com"
            or host.endswith(".tiktokglobalshop.com")
            or host == "tiktokshop.com"
            or host.endswith(".tiktokshop.com")
        )
        if not is_tiktok_shop or path.startswith(
            ("/login", "/signin", "/account/login")
        ):
            return 0
        if path == "/connection/creator":
            return 3
        if host.startswith("affiliate.") or "/affiliate" in path:
            return 2
        if host.startswith(("seller.", "shop.")):
            return 1
        return 0

    def _activate_available_store_page(self) -> dict[str, str] | None:
        targets = self._window_targets()
        candidates = sorted(
            (
                (
                    self._store_page_priority(target.get("url")),
                    handle,
                    str(target.get("url") or ""),
                )
                for handle, target in targets.items()
            ),
            reverse=True,
        )
        for priority, handle, _target_url in candidates:
            if priority <= 0:
                continue
            try:
                self.driver.switch_to.window(handle)
                current_url = str(self.driver.current_url or "")
                if self._store_page_priority(current_url) <= 0:
                    continue
                self._wait_for_document()
                return {"handle": handle, "url": current_url}
            except Exception:
                continue
        try:
            handles = list(self.driver.window_handles)
        except Exception:
            handles = []
        for handle in handles:
            if handle in targets:
                continue
            try:
                self.driver.switch_to.window(handle)
                current_url = str(self.driver.current_url or "")
                if self._store_page_priority(current_url) <= 0:
                    continue
                self._wait_for_document()
                return {"handle": handle, "url": current_url}
            except Exception:
                continue
        return None

    def _activate_existing_find_creators(self) -> dict[str, Any] | None:
        targets = self._window_targets()
        candidates = [
            (handle, target)
            for handle, target in targets.items()
            if self._is_find_creators_list_url(
                str(target.get("url") or "")
            )
        ]
        if not candidates:
            return None
        current_handle = self.driver.current_window_handle
        selected_handle, selected_target = next(
            (
                candidate
                for candidate in candidates
                if candidate[0] == current_handle
            ),
            candidates[-1],
        )
        self.driver.switch_to.window(selected_handle)
        selected_url = self.driver.current_url
        selected_shop_id = str(
            (parse_qs(urlparse(selected_url).query).get("shop_id") or [""])[0]
        )
        duplicate_count = 0
        for handle, target in candidates:
            if handle == selected_handle:
                continue
            duplicate_url = str(target.get("url") or "")
            duplicate_shop_id = str(
                (
                    parse_qs(urlparse(duplicate_url).query).get("shop_id")
                    or [""]
                )[0]
            )
            if (
                selected_shop_id
                and duplicate_shop_id != selected_shop_id
            ):
                continue
            target_id = str(target.get("targetId") or "")
            if not target_id:
                continue
            try:
                closed = self.driver.execute_cdp_cmd(
                    "Target.closeTarget",
                    {"targetId": target_id},
                )
                if (
                    not isinstance(closed, dict)
                    or closed.get("success") is not False
                ):
                    duplicate_count += 1
            except Exception:
                continue
        self.driver.switch_to.window(selected_handle)
        return {
            "handle": selected_handle,
            "url": selected_url,
            "duplicateFindCreatorsTabsClosed": duplicate_count,
        }

    def _visible_find_creators_search_inputs(self) -> list[WebElement]:
        visible: list[WebElement] = []
        seen: set[str] = set()
        for by, selector in FIND_CREATORS_SEARCH_SELECTORS:
            for element in self.driver.find_elements(by, selector):
                try:
                    identity = str(getattr(element, "id", "") or id(element))
                    if (
                        identity not in seen
                        and element.is_displayed()
                        and element.is_enabled()
                    ):
                        seen.add(identity)
                        visible.append(element)
                except StaleElementReferenceException:
                    continue
        return visible

    def open_find_creators(self) -> WorkflowStepResult:
        """Step 1: open Affiliate and then the Find Creators page."""
        self.driver.maximize_window()
        self._wait_for_document()
        reused_find_creators = self._activate_existing_find_creators()
        existing_page_reloaded_for_recovery = False
        obstruction_evidence: dict[str, Any] | None = None
        if reused_find_creators is not None:
            try:
                self._wait_for_document()
                time.sleep(FIND_CREATORS_RENDER_WAIT_SECONDS)
                obstruction_evidence = (
                    self._close_find_creators_obstruction()
                )
                obstruction_evidence[
                    "findCreatorsRenderWaitSeconds"
                ] = FIND_CREATORS_RENDER_WAIT_SECONDS
                self._wait(
                    lambda _driver: (
                        self._visible_find_creators_search_inputs() or False
                    ),
                    message=(
                        "复用的“查找达人”标签页未显示可用搜索框。"
                    ),
                    timeout_seconds=min(10, self.timeout_seconds),
                )
            except ZiniaoWorkflowError:
                recovery_url = str(
                    reused_find_creators.get("url") or ""
                )
                if recovery_url:
                    self._random_pause(5.0, 8.0, refresh=True)
                    self.driver.get(recovery_url)
                    self._wait_for_document()
                    existing_page_reloaded_for_recovery = True
                    obstruction_evidence = None
        if reused_find_creators is None:
            self._wait(
                lambda _driver: (
                    self._activate_available_store_page() or False
                ),
                message=(
                    "第 1 步失败：紫鸟浏览器未出现可用的 "
                    "TikTok Shop 店铺或联盟页面。"
                ),
            )
        started_url = self.driver.current_url
        affiliate_url = started_url

        if not (
            self._is_find_creators_list_url(started_url)
            or "/affiliate" in started_url.lower()
            or "affiliate.tiktokshop" in started_url.lower()
        ):
            affiliate = self._first_clickable(
                (
                    (By.CSS_SELECTOR, 'a[href*="/affiliate"]'),
                    (
                        By.XPATH,
                        "//*[self::a or self::button or @role='menuitem' "
                        "or @role='button'][contains(normalize-space(.), '联盟') "
                        "or contains(normalize-space(.), 'Affiliate')]",
                    ),
                    (
                        By.XPATH,
                        "//*[normalize-space()='联盟' "
                        "or normalize-space()='Affiliate']"
                        "/ancestor-or-self::*[self::a or self::button "
                        "or @role='menuitem' or @role='button'][1]",
                    ),
                ),
                missing_message="第 1 步失败：当前店铺页未找到“联盟/Affiliate”入口。",
            )
            affiliate_url = self._click_and_wait_for_url(
                affiliate,
                url_markers=("/affiliate", "affiliate.tiktokshop"),
                failure_message="第 1 步失败：点击“联盟”后未进入联盟页面。",
            )

        current_url = self.driver.current_url
        if "/connection/creator" not in current_url.lower():
            try:
                find_creators = self._first_clickable(
                    (
                        (By.CSS_SELECTOR, 'a[href*="/connection/creator"]'),
                        (By.CSS_SELECTOR, 'a[href*="creator"]'),
                        (
                            By.XPATH,
                            "//*[self::a or self::button or @role='link' "
                            "or @role='button'][contains(normalize-space(.), '寻找达人') "
                            "or contains(normalize-space(.), '查找达人') "
                            "or contains(normalize-space(.), 'Find creators') "
                            "or contains(normalize-space(.), 'Find Creators')]",
                        ),
                        (
                            By.XPATH,
                            "//*[string-length(normalize-space(.)) < 80 "
                            "and (contains(normalize-space(.), '寻找达人') "
                            "or contains(normalize-space(.), '查找达人') "
                            "or contains(normalize-space(.), 'Find creators') "
                            "or contains(normalize-space(.), 'Find Creators'))]",
                        ),
                    ),
                    missing_message=(
                        "第 1 步失败：联盟页面未找到"
                        "“寻找达人/Find creators”。"
                    ),
                )
            except ZiniaoWorkflowError as error:
                evidence = self._failure_evidence(1)
                raise ZiniaoWorkflowError(
                    f"{error} 诊断：{evidence}"
                ) from error
            self._click_and_wait_for_url(
                find_creators,
                url_markers=("/connection/creator",),
                failure_message="第 1 步失败：点击“寻找达人”后未进入达人搜索页。",
            )

        final_url = self.driver.current_url
        if (
            "/connection/creator" not in final_url.lower()
            or "/connection/creator/detail" in final_url.lower()
        ):
            raise ZiniaoWorkflowError(
                "第 1 步验收失败：当前页面不是“查找达人”列表页。"
            )
        self._find_creators_handle = self.driver.current_window_handle
        self._find_creators_url = final_url
        if obstruction_evidence is None:
            time.sleep(FIND_CREATORS_RENDER_WAIT_SECONDS)
            obstruction_evidence = (
                self._close_find_creators_obstruction()
            )
            obstruction_evidence[
                "findCreatorsRenderWaitSeconds"
            ] = FIND_CREATORS_RENDER_WAIT_SECONDS
        search_input = self._first_clickable(
            FIND_CREATORS_SEARCH_SELECTORS,
            missing_message=(
                "第 1 步验收失败：“查找达人”列表页未显示可用搜索框。"
            ),
        )

        headings = [
            element.text.strip()
            for element in self.driver.find_elements(By.CSS_SELECTOR, "h1, h2")
            if element.is_displayed() and element.text.strip()
        ]
        return WorkflowStepResult(
            step=1,
            action="open_find_creators",
            success=True,
            evidence={
                "startedUrl": started_url,
                "affiliateUrl": affiliate_url,
                "currentUrl": final_url,
                "title": self.driver.title,
                "headings": headings[:5],
                "reusedExistingFindCreatorsTab": (
                    reused_find_creators is not None
                ),
                "pageNavigationSkipped": (
                    reused_find_creators is not None
                    and not existing_page_reloaded_for_recovery
                ),
                "pageRefreshSkipped": (
                    not existing_page_reloaded_for_recovery
                ),
                "existingPageReloadedForRecovery": (
                    existing_page_reloaded_for_recovery
                ),
                **obstruction_evidence,
                "findCreatorsSearchReady": search_input is not None,
                "duplicateFindCreatorsTabsClosed": (
                    reused_find_creators.get(
                        "duplicateFindCreatorsTabsClosed",
                        0,
                    )
                    if reused_find_creators is not None
                    else 0
                ),
            },
        )

    @staticmethod
    def normalize_creator_handle(creator: str) -> tuple[str, str]:
        bare_handle = creator.strip().lstrip("@").strip()
        if not bare_handle:
            raise ZiniaoWorkflowError("达人用户名不能为空。")
        return f"@{bare_handle}", bare_handle

    def _close_find_creators_obstruction(self) -> dict[str, Any]:
        def local_match() -> tuple[WebElement | None, str]:
            for by, selector in FIND_CREATORS_OBSTRUCTION_CLOSE_SELECTORS:
                for candidate in self.driver.find_elements(by, selector):
                    try:
                        if candidate.is_displayed() and candidate.is_enabled():
                            return candidate, selector
                    except StaleElementReferenceException:
                        continue
            return None, ""

        element, selected_selector = local_match()
        locator_source = "local"
        if (
            element is None
            and self._visible_find_creators_search_inputs()
        ):
            return {
                "findCreatorsObstructionPresent": False,
                "findCreatorsObstructionClosed": True,
                "findCreatorsObstructionAlreadyClosed": True,
                "findCreatorsObstructionSelector": "",
                "findCreatorsObstructionLocatorSource": (
                    "search_input_already_visible"
                ),
            }
        if element is None and self.dom_fallback is not None:
            recovered = self.dom_fallback.locate(
                self.driver,
                purpose=(
                    "关闭查找达人页面已开启的 AI 搜索开关，"
                    "恢复普通达人用户名搜索框"
                ),
                attempted_selectors=(
                    FIND_CREATORS_OBSTRUCTION_CLOSE_SELECTORS
                ),
            )
            late_local_match, late_selector = local_match()
            if late_local_match is not None:
                element = late_local_match
                selected_selector = late_selector
                locator_source = "local_after_dom_fallback"
            elif recovered is not None:
                element = recovered
                selected_selector = "DOM_FALLBACK"
                locator_source = "dom_fallback"

        if element is not None and locator_source == "dom_fallback":
            try:
                if not (element.is_displayed() and element.is_enabled()):
                    element = None
            except StaleElementReferenceException:
                element = None

        if element is not None:
            try:
                role = str(element.get_attribute("role") or "").lower()
                aria_checked = str(
                    element.get_attribute("aria-checked") or ""
                ).lower()
                is_switch = role == "switch" or aria_checked in {
                    "true",
                    "false",
                }
                if is_switch and aria_checked == "false":
                    return {
                        "findCreatorsObstructionPresent": True,
                        "findCreatorsObstructionClosed": True,
                        "findCreatorsObstructionAlreadyClosed": True,
                        "findCreatorsObstructionSelector": selected_selector,
                        "findCreatorsObstructionLocatorSource": locator_source,
                    }
                self._click(element)

                def obstruction_closed(_driver: WebDriver) -> bool:
                    try:
                        if is_switch:
                            checked = str(
                                element.get_attribute("aria-checked") or ""
                            ).lower()
                            if checked == "false" or not element.is_displayed():
                                return True
                            current_switch, _selector = local_match()
                            if current_switch is not None:
                                current_checked = str(
                                    current_switch.get_attribute(
                                        "aria-checked"
                                    )
                                    or ""
                                ).lower()
                                if current_checked == "false":
                                    return True
                            return bool(
                                current_switch is None
                                and self._visible_find_creators_search_inputs()
                            )
                        return not element.is_displayed()
                    except StaleElementReferenceException:
                        return True

                self._wait(
                    obstruction_closed,
                    message=(
                        "查找达人页面 AI 搜索开关点击后仍未关闭。"
                        if is_switch
                        else "查找达人页面遮挡关闭按钮点击后仍未消失。"
                    ),
                    timeout_seconds=min(12, self.timeout_seconds),
                )
                return {
                    "findCreatorsObstructionPresent": True,
                    "findCreatorsObstructionClosed": True,
                    "findCreatorsObstructionAlreadyClosed": False,
                    "findCreatorsObstructionSelector": selected_selector,
                    "findCreatorsObstructionLocatorSource": locator_source,
                }
            except StaleElementReferenceException:
                return {
                    "findCreatorsObstructionPresent": True,
                    "findCreatorsObstructionClosed": True,
                    "findCreatorsObstructionAlreadyClosed": False,
                    "findCreatorsObstructionSelector": selected_selector,
                    "findCreatorsObstructionLocatorSource": locator_source,
                }
        return {
            "findCreatorsObstructionPresent": False,
            "findCreatorsObstructionClosed": False,
            "findCreatorsObstructionAlreadyClosed": False,
            "findCreatorsObstructionSelector": "",
            "findCreatorsObstructionLocatorSource": "none",
        }

    def search_creator(self, creator: str) -> WorkflowStepResult:
        """Step 2: close the obstruction, search the imported ID, and verify."""
        requested_handle, bare_handle = self.normalize_creator_handle(creator)
        current_url = self.driver.current_url
        if (
            "/connection/creator" not in current_url.lower()
            or "/connection/creator/detail" in current_url.lower()
        ):
            raise ZiniaoWorkflowError(
                "第 2 步前置验收失败：当前页面不是“查找达人”列表页。"
            )

        obstruction_evidence = self._close_find_creators_obstruction()
        search_input = self._first_clickable(
            FIND_CREATORS_SEARCH_SELECTORS,
            missing_message="第 2 步失败：未找到达人搜索框。",
        )
        self._click(search_input)
        search_input.send_keys(Keys.COMMAND, "a")
        search_input.send_keys(Keys.BACKSPACE)
        search_input.send_keys(bare_handle)

        self._wait(
            lambda _driver: search_input.get_attribute("value")
            == bare_handle,
            message="第 2 步失败：导入达人 ID 未完整写入搜索框。",
        )

        exact_handle = self._xpath_literal(bare_handle)
        requested_handle_literal = self._xpath_literal(requested_handle)
        suggestion = self._first_clickable(
            (
                (
                    By.XPATH,
                    "//*[@role='menuitem'][.//*"
                    f"[normalize-space()={exact_handle} or "
                    f"normalize-space()={requested_handle_literal}]]",
                ),
                (
                    By.XPATH,
                    "//*[(normalize-space()="
                    f"{exact_handle} or normalize-space()="
                    f"{requested_handle_literal})]"
                    "/ancestor::*[@role='menuitem'][1]",
                ),
            ),
            missing_message=(
                f"第 2 步跳过：输入 {requested_handle} 后未出现"
                "精确同名候选项。"
            ),
            timeout_seconds=min(8, self.timeout_seconds),
        )
        suggestion_text = suggestion.text.strip()

        def exact_suggestion_still_bound(target: WebElement) -> bool:
            try:
                tokens = {
                    token.strip().lstrip("@").casefold()
                    for token in str(
                        target.get_attribute("innerText") or ""
                    ).splitlines()
                    if token.strip()
                }
                return (
                    bare_handle.casefold() in tokens
                    and search_input.get_attribute("value") == bare_handle
                )
            except StaleElementReferenceException:
                return False

        self._click(
            suggestion,
            validator=exact_suggestion_still_bound,
            validation_message=(
                "第 2 步被安全门阻止：等待后候选项不再是精确目标"
                f" {requested_handle}。"
            ),
        )

        def matching_result_row(driver: WebDriver) -> WebElement | bool:
            for row in driver.find_elements(
                By.XPATH,
                "//tr[.//*"
                f"[normalize-space()={exact_handle}]]",
            ):
                try:
                    if row.is_displayed():
                        return row
                except StaleElementReferenceException:
                    continue
            return False

        try:
            result_row = self._wait(
                matching_result_row,
                message=(
                    "第 2 步失败：点击候选项后未出现目标达人搜索结果卡片。"
                ),
            )
        except ZiniaoWorkflowError as error:
            evidence = self._failure_evidence(2)
            raise ZiniaoWorkflowError(f"{error} 诊断：{evidence}") from error
        self.driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'});",
            result_row,
        )
        try:
            final_input_value = search_input.get_attribute("value")
        except StaleElementReferenceException:
            final_input_value = ""

        return WorkflowStepResult(
            step=2,
            action="search_creator",
            success=True,
            evidence={
                "currentUrl": self.driver.current_url,
                "inputValue": final_input_value,
                "importedCreatorId": bare_handle,
                "matchedHandle": bare_handle,
                "dropdownSelected": True,
                "selectedSuggestion": suggestion_text,
                "resultCardVisible": result_row.is_displayed(),
                "resultCardTag": result_row.tag_name,
                **obstruction_evidence,
            },
        )

    def open_creator_detail(self, creator: str) -> WorkflowStepResult:
        """Step 3: click the exact creator result card and verify its detail."""
        requested_handle, bare_handle = self.normalize_creator_handle(creator)
        current_url = self.driver.current_url
        if (
            "/connection/creator" not in current_url.lower()
            or "/connection/creator/detail" in current_url.lower()
        ):
            raise ZiniaoWorkflowError(
                "第 3 步前置验收失败：当前页面不是达人搜索结果页。"
            )

        exact_handle = self._xpath_literal(bare_handle)
        result_handle = self._first_clickable(
            (
                (
                    By.XPATH,
                    "//tr[.//*"
                    f"[normalize-space()={exact_handle}]]"
                    "//*[normalize-space()="
                    f"{exact_handle} and not(.//*"
                    f"[normalize-space()={exact_handle}])][1]",
                ),
                (
                    By.XPATH,
                    "//*[normalize-space()="
                    f"{exact_handle} and ancestor::tr][1]",
                ),
            ),
            missing_message=(
                f"第 3 步失败：未找到 {requested_handle} 的搜索结果卡片。"
            ),
        )
        handles_before = set(self.driver.window_handles)
        try:
            self._click_and_wait_for_url(
                result_handle,
                url_markers=("/connection/creator/detail",),
                failure_message="第 3 步失败：点击达人卡片后未进入详情页。",
            )
        except ZiniaoWorkflowError as error:
            evidence = self._failure_evidence(3)
            try:
                evidence["windowUrls"] = []
                active_handle = self.driver.current_window_handle
                for handle in self.driver.window_handles:
                    self.driver.switch_to.window(handle)
                    evidence["windowUrls"].append(self.driver.current_url)
                self.driver.switch_to.window(active_handle)
            except Exception:
                pass
            raise ZiniaoWorkflowError(f"{error} 诊断：{evidence}") from error

        def exact_visible_handle(driver: WebDriver) -> list[WebElement] | bool:
            matches = []
            for element in driver.find_elements(
                By.XPATH,
                f"//*[normalize-space()={exact_handle}]",
            ):
                try:
                    if element.is_displayed():
                        matches.append(element)
                except StaleElementReferenceException:
                    continue
            return matches or False

        visible_handles = self._wait(
            exact_visible_handle,
            message=(
                "第 3 步验收失败：达人详情页未显示目标达人用户名。"
            ),
        )
        final_url = self.driver.current_url
        if "/connection/creator/detail" not in final_url.lower():
            raise ZiniaoWorkflowError(
                "第 3 步验收失败：当前 URL 不是达人详情页。"
            )
        self._creator_detail_handle = self.driver.current_window_handle
        self._creator_detail_url = final_url

        return WorkflowStepResult(
            step=3,
            action="open_creator_detail",
            success=True,
            evidence={
                "currentUrl": final_url,
                "title": self.driver.title,
                "creatorHandle": bare_handle,
                "visibleHandleCount": len(visible_handles),
                "openedNewWindow": bool(
                    set(self.driver.window_handles) - handles_before
                ),
                "windowCount": len(self.driver.window_handles),
            },
        )

    def _visible_message_composers(self) -> list[WebElement]:
        composers: list[WebElement] = []
        for element in self.driver.find_elements(
            By.CSS_SELECTOR,
            (
                "textarea, [contenteditable='true'], "
                "input[placeholder*='发送消息'], "
                "input[placeholder*='message' i]"
            ),
        ):
            try:
                rect = element.rect
                if (
                    element.is_displayed()
                    and rect.get("width", 0) >= 160
                    and rect.get("height", 0) >= 20
                ):
                    composers.append(element)
            except StaleElementReferenceException:
                continue
        return composers

    def open_message_panel(self, creator: str) -> WorkflowStepResult:
        """Step 4: open the creator chat panel without entering a message."""
        _requested_handle, bare_handle = self.normalize_creator_handle(creator)
        if "/connection/creator/detail" not in self.driver.current_url.lower():
            raise ZiniaoWorkflowError(
                "第 4 步前置验收失败：当前页面不是达人详情页。"
            )

        message_button = self._first_clickable(
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
            missing_message="第 4 步失败：达人详情页未找到私信按钮。",
        )
        self._click(message_button)

        try:
            self._first_clickable(
                (
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
                missing_message="第 4 步失败：点击私信后聊天抽屉未打开。",
            )
            viewport_width = int(
                self.driver.execute_script("return window.innerWidth || 0;")
            )
            exact_handle = self._xpath_literal(bare_handle)

            def right_side_target(
                driver: WebDriver,
            ) -> WebElement | bool:
                for element in driver.find_elements(
                    By.XPATH,
                    f"//*[normalize-space()={exact_handle}]",
                ):
                    try:
                        if (
                            element.is_displayed()
                            and element.rect.get("x", 0)
                            >= viewport_width * 0.5
                        ):
                            return element
                    except StaleElementReferenceException:
                        continue
                return False

            target_in_panel = self._wait(
                right_side_target,
                message=(
                    "第 4 步失败：聊天抽屉未显示目标达人联系人。"
                ),
            )
            contact_selected = False
            composers = self._visible_message_composers()
            if not composers:
                self._click(target_in_panel)
                contact_selected = True
            composers = self._wait(
                lambda _driver: self._visible_message_composers() or False,
                message="第 4 步失败：点击私信后未出现聊天输入区域。",
            )
        except ZiniaoWorkflowError as error:
            evidence = self._failure_evidence(4)
            raise ZiniaoWorkflowError(f"{error} 诊断：{evidence}") from error

        right_side_handle_count = 0
        for element in self.driver.find_elements(
            By.XPATH,
            f"//*[normalize-space()={exact_handle}]",
        ):
            try:
                rect = element.rect
                if (
                    element.is_displayed()
                    and rect.get("x", 0) >= viewport_width * 0.5
                ):
                    right_side_handle_count += 1
            except StaleElementReferenceException:
                continue

        composer_evidence = []
        for element in composers:
            composer_evidence.append(
                {
                    "tag": element.tag_name,
                    "placeholder": element.get_attribute("placeholder") or "",
                    "contentEditable": (
                        element.get_attribute("contenteditable") or ""
                    ),
                }
            )

        return WorkflowStepResult(
            step=4,
            action="open_message_panel",
            success=True,
            evidence={
                "currentUrl": self.driver.current_url,
                "creatorHandle": bare_handle,
                "composerCount": len(composers),
                "composers": composer_evidence[:3],
                "targetHandleVisibleInPanel": right_side_handle_count > 0,
                "contactSelected": contact_selected,
            },
        )

    def _chat_target_handle_visible(self, bare_handle: str) -> bool:
        for element in self.driver.find_elements(
            By.XPATH,
            "//*[contains(normalize-space(.), "
            f"{self._xpath_literal(bare_handle)})]",
        ):
            try:
                text = element.text.strip()
                if (
                    element.is_displayed()
                    and bare_handle.lower() in text.lower()
                    and 0 < len(text) <= 120
                ):
                    return True
            except StaleElementReferenceException:
                continue
        return False

    def _activate_chat_window_after_launch(
        self,
        *,
        handles_before: set[str],
        urls_before: dict[str, str],
        bare_handle: str,
    ) -> dict[str, Any] | bool:
        handles = list(self.driver.window_handles)
        target_map = self._window_targets()
        prioritized = [
            handle for handle in handles if handle not in handles_before
        ]
        prioritized.extend(
            handle
            for handle in handles
            if handle in handles_before
            and handle not in prioritized
        )
        known_chat_handles = [
            handle
            for handle in prioritized
            if "/seller/im" in str(
                (target_map.get(handle) or {}).get("url") or ""
            ).lower()
        ]
        unknown_handles = [
            handle
            for handle in prioritized
            if handle not in target_map
        ]
        prioritized = [
            *known_chat_handles,
            *unknown_handles,
        ]
        for handle in prioritized:
            try:
                self.driver.switch_to.window(handle)
                current_url = self.driver.current_url
                parsed = urlparse(current_url)
                if (
                    "/seller/im" not in parsed.path.lower()
                    or not self.creator_id_from_url(current_url).isdigit()
                    or not self._chat_target_handle_visible(bare_handle)
                ):
                    continue
                opened_new_window = handle not in handles_before
                return {
                    "handle": handle,
                    "url": current_url,
                    "openedNewWindow": opened_new_window,
                    "reusedExistingWindow": not opened_new_window,
                    "urlChangedAfterLaunch": (
                        current_url != urls_before.get(handle, "")
                    ),
                }
            except Exception:
                continue
        return False

    def open_chat_in_new_tab(self, creator: str) -> WorkflowStepResult:
        """Step 5: open the selected conversation in a verified chat tab."""
        _requested_handle, bare_handle = self.normalize_creator_handle(creator)
        if not self._visible_message_composers():
            raise ZiniaoWorkflowError(
                "第 5 步前置验收失败：聊天抽屉尚未打开。"
            )

        launch_button = self._first_clickable(
            (
                (
                    By.CSS_SELECTOR,
                    "button:has(svg.arco-icon-launch)",
                ),
                (
                    By.XPATH,
                    "//button[.//*[contains(@class, 'arco-icon-launch')]]",
                ),
            ),
            missing_message=(
                "第 5 步失败：聊天抽屉未找到“在新标签页打开聊天”按钮。"
            ),
        )

        tooltip_visible = False
        try:
            ActionChains(self.driver).move_to_element(launch_button).perform()
            tooltips = self.driver.find_elements(
                By.XPATH,
                "//*[contains(normalize-space(.), '在新标签页打开聊天') "
                "or contains(normalize-space(.), "
                "'Open chat in a new tab')]",
            )
            tooltip_visible = any(
                element.is_displayed() for element in tooltips
            )
        except Exception:
            pass

        handles_before = set(self.driver.window_handles)
        urls_before = {
            handle: str(target.get("url") or "")
            for handle, target in self._window_targets().items()
        }
        source_handle = self.driver.current_window_handle
        source_url = self.driver.current_url
        self._click(launch_button)

        try:
            launched_chat = self._wait(
                lambda _driver: self._activate_chat_window_after_launch(
                    handles_before=handles_before,
                    urls_before=urls_before,
                    bare_handle=bare_handle,
                ),
                message=(
                    "第 5 步失败：点击“打开聊天”后，未检测到"
                    "新建或安全复用的目标聊天标签页。"
                ),
            )
            self.driver.switch_to.window(str(launched_chat["handle"]))
            self._wait(
                lambda driver: (
                    driver.current_url
                    and driver.current_url != "about:blank"
                    and driver.current_url != source_url
                ),
                message="第 5 步失败：新聊天标签页未完成导航。",
            )
            self._wait_for_document()
            composers = self._wait(
                lambda _driver: self._visible_message_composers() or False,
                message="第 5 步验收失败：新标签页未显示聊天输入区域。",
            )
        except ZiniaoWorkflowError as error:
            evidence = self._failure_evidence(5)
            try:
                evidence["windowUrls"] = []
                for handle in self.driver.window_handles:
                    self.driver.switch_to.window(handle)
                    evidence["windowUrls"].append(self.driver.current_url)
            except Exception:
                pass
            raise ZiniaoWorkflowError(f"{error} 诊断：{evidence}") from error

        self._chat_handle = str(launched_chat["handle"])
        target_visible = self._chat_target_handle_visible(bare_handle)
        if not target_visible:
            raise ZiniaoWorkflowError(
                "第 5 步验收失败：新聊天标签页未显示目标达人。"
            )
        discovered_creator_id = self.creator_id_from_url(
            self.driver.current_url
        )
        if not discovered_creator_id.isdigit():
            raise ZiniaoWorkflowError(
                "第 5 步验收失败：聊天 URL 未提供有效 creator_id。"
            )

        return WorkflowStepResult(
            step=5,
            action="open_chat_in_new_tab",
            success=True,
            evidence={
                "sourceUrl": source_url,
                "currentUrl": self.driver.current_url,
                "title": self.driver.title,
                "creatorHandle": bare_handle,
                "openedNewWindow": launched_chat["openedNewWindow"],
                "reusedExistingWindow": (
                    launched_chat["reusedExistingWindow"]
                ),
                "urlChangedAfterLaunch": (
                    launched_chat["urlChangedAfterLaunch"]
                ),
                "windowCount": len(self.driver.window_handles),
                "composerCount": len(composers),
                "targetHandleVisible": target_visible,
                "creatorId": discovered_creator_id,
                "creatorIdDiscoveredFromUrl": True,
                "tooltipVisibleBeforeClick": tooltip_visible,
                "messageEntered": False,
                "messageSent": False,
            },
        )

    def close_creator_tabs_keep_search(self) -> dict[str, Any]:
        """Close this creator's detail/chat tabs and return to Find Creators."""
        search_handle = self._find_creators_handle
        search_url = str(self._find_creators_url or "")
        detail_handle = self._creator_detail_handle
        detail_url = str(self._creator_detail_url or "")
        chat_handle = self._chat_handle
        if (
            not search_handle
            or search_handle not in self.driver.window_handles
            or "/connection/creator" not in search_url.lower()
            or "/connection/creator/detail" in search_url.lower()
        ):
            raise ZiniaoWorkflowError(
                "达人标签清理失败：未找到已验收的“查找达人”标签页。"
            )

        closed_count = 0
        for handle in dict.fromkeys((detail_handle, chat_handle)):
            if (
                not handle
                or handle == search_handle
                or handle not in self.driver.window_handles
            ):
                continue
            try:
                self.driver.switch_to.window(handle)
                self.driver.close()
                closed_count += 1
            except Exception as error:
                raise ZiniaoWorkflowError(
                    "达人标签清理失败：详情页或聊天页未能关闭。"
                ) from error

        self.driver.switch_to.window(search_handle)
        current_url = self.driver.current_url
        if (
            "/connection/creator" not in current_url.lower()
            or "/connection/creator/detail" in current_url.lower()
        ):
            self._random_pause(5.0, 8.0, refresh=True)
            self.driver.get(search_url)
            self._wait_for_document()
            current_url = self.driver.current_url
        if (
            "/connection/creator" not in current_url.lower()
            or "/connection/creator/detail" in current_url.lower()
        ):
            raise ZiniaoWorkflowError(
                "达人标签清理失败：未能返回“查找达人”列表页。"
            )
        remaining_handles = set(self.driver.window_handles)
        detail_target_gone = (
            not detail_handle
            or (
                detail_handle == search_handle
                and self._is_find_creators_list_url(current_url)
            )
            or detail_handle not in remaining_handles
        )
        chat_target_gone = (
            not chat_handle
            or chat_handle == search_handle
            or chat_handle not in remaining_handles
        )
        if not detail_target_gone or not chat_target_gone:
            raise ZiniaoWorkflowError(
                "达人标签清理失败：当前达人的详情页或聊天页仍然存在。"
            )
        search_inputs = self._wait(
            lambda _driver: (
                self._visible_find_creators_search_inputs() or False
            ),
            message="达人标签清理失败：“查找达人”搜索框不可用。",
        )

        self._creator_detail_handle = None
        self._creator_detail_url = None
        self._chat_handle = None
        return {
            "creatorTabsClosed": True,
            "closedCreatorTabCount": closed_count,
            "creatorDetailUrl": detail_url,
            "creatorDetailTabClosed": bool(
                detail_handle
                and detail_handle != search_handle
                and detail_handle not in remaining_handles
            ),
            "creatorDetailReturnedToSearch": bool(
                detail_handle == search_handle
                and self._is_find_creators_list_url(current_url)
            ),
            "creatorDetailTargetGone": detail_target_gone,
            "creatorChatTabClosed": chat_target_gone,
            "searchTabKept": True,
            "returnedToFindCreators": True,
            "findCreatorsUrl": current_url,
            "findCreatorsSearchReady": bool(search_inputs),
        }

    @staticmethod
    def normalize_creator_id(creator_id: str) -> str:
        normalized = str(creator_id or "").strip()
        if not normalized or not normalized.isdigit():
            raise ZiniaoWorkflowError("达人 creator_id 必须是非空数字。")
        return normalized

    @staticmethod
    def creator_id_from_url(url: str) -> str:
        values = parse_qs(urlparse(url).query).get("creator_id", [])
        return str(values[0]).strip() if values else ""

    @staticmethod
    def greeting_sha256(message: str) -> str:
        return hashlib.sha256(message.encode("utf-8")).hexdigest()

    @classmethod
    def approved_greeting_sha256(cls) -> str:
        return cls.greeting_sha256(APPROVED_GREETING_MESSAGE)

    @staticmethod
    def validate_greeting_message(message: str) -> str:
        normalized = (
            str(message or "")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .strip()
        )
        if not normalized.strip():
            raise ZiniaoWorkflowError("招呼语不能为空。")
        if len(normalized) > 2000:
            raise ZiniaoWorkflowError("招呼语不能超过 2000 个字符。")
        return normalized

    @staticmethod
    def _is_visible(element: WebElement) -> bool:
        try:
            rect = element.rect
            return (
                element.is_displayed()
                and rect.get("width", 0) > 0
                and rect.get("height", 0) > 0
            )
        except StaleElementReferenceException:
            return False

    def _visible_exact_text_elements(
        self,
        text: str,
        *,
        root: WebElement | None = None,
    ) -> list[WebElement]:
        literal = self._xpath_literal(text)
        selector = f".//*[normalize-space()={literal}]"
        if root is None:
            selector = f"//*[normalize-space()={literal}]"
            candidates = self.driver.find_elements(By.XPATH, selector)
        else:
            candidates = root.find_elements(By.XPATH, selector)
        return [
            element
            for element in candidates
            if self._is_visible(element)
        ]

    def _recipient_evidence(
        self,
        creator: str,
        creator_id: str | None = None,
    ) -> dict[str, Any]:
        _requested_handle, bare_handle = self.normalize_creator_handle(creator)
        current_url = self.driver.current_url
        parsed_creator_id = self.creator_id_from_url(current_url)
        if "/seller/im" not in urlparse(current_url).path.lower():
            raise ZiniaoWorkflowError(
                "聊天对象验收失败：当前页面不是 Cooperation Chat。"
            )
        if not parsed_creator_id.isdigit():
            raise ZiniaoWorkflowError(
                "聊天对象验收失败：URL 中缺少有效 creator_id。"
            )
        expected_creator_id = (
            self.normalize_creator_id(creator_id)
            if creator_id is not None and str(creator_id).strip()
            else parsed_creator_id
        )
        if parsed_creator_id != expected_creator_id:
            raise ZiniaoWorkflowError(
                "聊天对象验收失败：URL 中的 creator_id 与目标达人不一致。"
            )

        exact_handles = self._visible_exact_text_elements(bare_handle)
        viewport_width = int(
            self.driver.execute_script("return window.innerWidth || 0;")
        )
        header_matches: list[WebElement] = []
        for element in exact_handles:
            try:
                rect = element.rect
                in_chat_header = (
                    rect.get("x", 0) >= viewport_width * 0.25
                    and rect.get("x", 0) <= viewport_width * 0.8
                    and rect.get("y", 0) <= 180
                )
                inside_top_bar = bool(
                    element.find_elements(
                        By.XPATH,
                        "ancestor::*[contains(@class, 'chatRoomTopBar')]",
                    )
                )
                if in_chat_header or inside_top_bar:
                    header_matches.append(element)
            except StaleElementReferenceException:
                continue
        if not header_matches:
            raise ZiniaoWorkflowError(
                "聊天对象验收失败：聊天页顶部未显示目标达人用户名。"
            )
        if not self._visible_message_composers():
            raise ZiniaoWorkflowError(
                "聊天对象验收失败：目标聊天页未显示消息输入区。"
            )

        normalized_recipient = (bare_handle.lower(), expected_creator_id)
        if (
            self._verified_recipient is not None
            and self._verified_recipient != normalized_recipient
        ):
            raise ZiniaoWorkflowError("同一工作流中不得更换聊天对象。")
        self._verified_recipient = normalized_recipient
        return {
            "currentUrl": current_url,
            "title": self.driver.title,
            "creatorHandle": bare_handle,
            "creatorId": expected_creator_id,
            "urlCreatorIdMatched": True,
            "headerHandleMatched": True,
            "headerMatchCount": len(header_matches),
            "composerVisible": True,
        }

    def verify_chat_recipient(
        self,
        creator: str,
        creator_id: str | None = None,
    ) -> WorkflowStepResult:
        """Step 6: verify the handle and creator_id before any mutation."""
        try:
            evidence = self._recipient_evidence(creator, creator_id)
        except ZiniaoWorkflowError as error:
            failure = self._failure_evidence(6)
            raise ZiniaoWorkflowError(f"{error} 诊断：{failure}") from error
        evidence.update(
            {
                "recipientVerified": True,
                "messageEntered": False,
                "messageSent": False,
            }
        )
        return WorkflowStepResult(
            step=6,
            action="verify_chat_recipient",
            success=True,
            evidence=evidence,
        )

    def _require_verified_recipient(
        self,
        creator: str,
        creator_id: str | None = None,
    ) -> dict[str, Any]:
        evidence = self._recipient_evidence(creator, creator_id)
        expected = (
            self.normalize_creator_handle(creator)[1].lower(),
            str(evidence["creatorId"]),
        )
        if self._verified_recipient != expected:
            raise ZiniaoWorkflowError("目标聊天对象尚未完成验收。")
        return evidence

    def _chat_message_elements(self, message: str) -> list[WebElement]:
        composers = self._visible_message_composers()
        if not composers:
            return []
        composer_rect = composers[0].rect
        left = composer_rect.get("x", 0) - 40
        right = (
            composer_rect.get("x", 0)
            + composer_rect.get("width", 0)
            + 40
        )
        top_limit = composer_rect.get("y", 0)
        matches: list[WebElement] = []
        prefix = message.splitlines()[0]
        candidates = self.driver.find_elements(
            By.XPATH,
            "//*[contains(normalize-space(.), "
            f"{self._xpath_literal(prefix)})]",
        )
        for element in candidates:
            try:
                rect = element.rect
                inner_text = str(
                    element.get_attribute("innerText") or ""
                ).strip().replace("\r\n", "\n")
                if (
                    self._is_visible(element)
                    and element.tag_name.lower() not in {"input", "textarea"}
                    and inner_text == message
                    and rect.get("x", 0) >= left
                    and rect.get("x", 0) <= right
                    and rect.get("y", 0) < top_limit
                ):
                    matches.append(element)
            except StaleElementReferenceException:
                continue
        return matches

    @staticmethod
    def _composer_value(composer: WebElement) -> str:
        if composer.tag_name.lower() in {"input", "textarea"}:
            return str(composer.get_attribute("value") or "")
        return str(composer.get_attribute("textContent") or "")

    def _set_composer_value(
        self,
        composer: WebElement,
        message: str,
    ) -> None:
        self._click(composer)
        self.driver.execute_script(
            """
            const element = arguments[0];
            const value = arguments[1];
            const tag = element.tagName.toLowerCase();
            if (tag === 'textarea' || tag === 'input') {
              const prototype = tag === 'textarea'
                ? HTMLTextAreaElement.prototype
                : HTMLInputElement.prototype;
              const setter = Object.getOwnPropertyDescriptor(
                prototype, 'value'
              ).set;
              setter.call(element, value);
            } else {
              element.textContent = value;
            }
            element.dispatchEvent(new InputEvent('input', {
              bubbles: true,
              inputType: 'insertText',
              data: value
            }));
            element.dispatchEvent(new Event('change', {bubbles: true}));
            """,
            composer,
            message,
        )

    def _message_send_button(self, composer: WebElement) -> WebElement:
        composer_rect = composer.rect

        def find(_driver: WebDriver) -> WebElement | bool:
            candidates: list[tuple[float, WebElement]] = []
            for button in self.driver.find_elements(
                By.XPATH,
                "//button[normalize-space()='发送' "
                "or normalize-space()='Send']",
            ):
                try:
                    if not button.is_displayed() or not button.is_enabled():
                        continue
                    rect = button.rect
                    if not (
                        rect.get("x", 0) >= composer_rect.get("x", 0) - 20
                        and rect.get("x", 0)
                        <= (
                            composer_rect.get("x", 0)
                            + composer_rect.get("width", 0)
                            + 30
                        )
                        and rect.get("y", 0)
                        >= composer_rect.get("y", 0) - 30
                        and rect.get("y", 0)
                        <= (
                            composer_rect.get("y", 0)
                            + composer_rect.get("height", 0)
                            + 80
                        )
                    ):
                        continue
                    distance = abs(
                        rect.get("y", 0)
                        - (
                            composer_rect.get("y", 0)
                            + composer_rect.get("height", 0)
                        )
                    ) + abs(
                        rect.get("x", 0)
                        + rect.get("width", 0)
                        - (
                            composer_rect.get("x", 0)
                            + composer_rect.get("width", 0)
                        )
                    )
                    candidates.append((distance, button))
                except StaleElementReferenceException:
                    continue
            if not candidates:
                return False
            candidates.sort(key=lambda item: item[0])
            return candidates[0][1]

        return self._wait(
            find,
            message="第 7 步失败：聊天输入区附近未找到发送按钮。",
        )

    def send_greeting(
        self,
        creator: str,
        creator_id: str | None,
        message: str,
        *,
        confirm_send: bool = False,
        expected_sha256: str | None = None,
    ) -> WorkflowStepResult:
        """Step 7: send the task-snapshotted greeting exactly once."""
        recipient = self._require_verified_recipient(creator, creator_id)
        greeting = self.validate_greeting_message(message)
        message_sha256 = self.greeting_sha256(greeting)
        if (
            expected_sha256 is not None
            and str(expected_sha256).strip().lower() != message_sha256
        ):
            raise ZiniaoWorkflowError(
                "第 7 步被安全门阻止：招呼语内容与任务快照哈希不一致。"
            )
        existing = self._chat_message_elements(greeting)
        already_sent = bool(existing)
        if not already_sent:
            if confirm_send is not True:
                raise ZiniaoWorkflowError(
                    "第 7 步被安全门阻止：必须显式确认发送招呼语。"
                )
            composers = self._visible_message_composers()
            if not composers:
                raise ZiniaoWorkflowError(
                    "第 7 步前置验收失败：未找到聊天输入区。"
                )
            composer = composers[0]
            self._set_composer_value(composer, greeting)
            self._wait(
                lambda _driver: (
                    self._composer_value(composer)
                    == greeting
                ),
                message="第 7 步失败：招呼语未完整写入聊天输入区。",
            )
            send_button = self._message_send_button(composer)
            self._click(send_button)
            try:
                existing = self._wait(
                    lambda _driver: (
                        self._chat_message_elements(
                            greeting
                        )
                        or False
                    ),
                    message=(
                        "第 7 步验收失败：点击发送后未出现完整招呼语气泡。"
                    ),
                )
            except ZiniaoWorkflowError as error:
                failure = self._failure_evidence(7)
                raise ZiniaoWorkflowError(
                    f"{error} 诊断：{failure}"
                ) from error

        self._greeting_delivery_verified = True
        return WorkflowStepResult(
            step=7,
            action="send_greeting",
            success=True,
            evidence={
                **recipient,
                "messageLength": len(greeting),
                "messageSha256": message_sha256,
                "messageBubbleCount": len(existing),
                "messageBubbleVisible": True,
                "alreadySent": already_sent,
                "messageEntered": not already_sent,
                "messageSent": True,
            },
        )

    def send_approved_greeting(
        self,
        creator: str,
        creator_id: str | None,
        *,
        confirm_send: bool = False,
    ) -> WorkflowStepResult:
        """Compatibility wrapper for the original approved greeting."""
        return self.send_greeting(
            creator,
            creator_id,
            APPROVED_GREETING_MESSAGE,
            confirm_send=confirm_send,
            expected_sha256=self.approved_greeting_sha256(),
        )

    def _target_collaboration_tab(self) -> WebElement:
        return self._first_clickable(
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
            missing_message="第 8 步失败：聊天页未找到“定向合作”页签。",
        )

    def _other_invitation_button(self) -> WebElement:
        return self._first_clickable(
            (
                (
                    By.XPATH,
                    "//button[contains(normalize-space(.), "
                    "'发送其他邀请开展合作') "
                    "or contains(normalize-space(.), "
                    "'Send another invitation')]",
                ),
            ),
            missing_message=(
                "第 8 步失败：定向合作页未找到"
                "“发送其他邀请开展合作”。"
            ),
        )

    def open_target_collaboration(
        self,
        creator: str,
        creator_id: str | None,
    ) -> WorkflowStepResult:
        """Step 8: open and verify the target-collaboration side panel."""
        recipient = self._require_verified_recipient(creator, creator_id)
        if not self._greeting_delivery_verified:
            raise ZiniaoWorkflowError(
                "第 8 步前置验收失败：招呼语发送结果尚未验收。"
            )
        tab = self._target_collaboration_tab()
        selected_before = (
            tab.get_attribute("aria-selected") == "true"
            or "active" in (tab.get_attribute("class") or "").lower()
        )
        if not selected_before:
            self._click(tab)
        active_tab = self._wait(
            lambda _driver: (
                self._target_collaboration_tab()
                if (
                    self._target_collaboration_tab().get_attribute(
                        "aria-selected"
                    )
                    == "true"
                    or "active"
                    in (
                        self._target_collaboration_tab().get_attribute("class")
                        or ""
                    ).lower()
                )
                else False
            ),
            message="第 8 步验收失败：“定向合作”页签未激活。",
        )
        other_button = self._other_invitation_button()
        self._target_collaboration_verified = True
        return WorkflowStepResult(
            step=8,
            action="open_target_collaboration",
            success=True,
            evidence={
                **recipient,
                "tabText": active_tab.text.strip(),
                "tabAlreadySelected": selected_before,
                "targetCollaborationActive": True,
                "otherInvitationButtonVisible": self._is_visible(
                    other_button
                ),
                "invitationSent": False,
            },
        )

    def _visible_invitation_modal(
        self,
        creator: str,
    ) -> WebElement | bool:
        _requested_handle, bare_handle = self.normalize_creator_handle(creator)
        expected_title = f"邀请 @{bare_handle} 合作"
        for title in self._visible_exact_text_elements(expected_title):
            current = title
            for _index in range(10):
                try:
                    invite_buttons = current.find_elements(
                        By.XPATH,
                        ".//button[normalize-space()='邀请' "
                        "or normalize-space()='Invite']",
                    )
                    cancel_buttons = current.find_elements(
                        By.XPATH,
                        ".//button[normalize-space()='取消' "
                        "or normalize-space()='Cancel']",
                    )
                    if invite_buttons and cancel_buttons:
                        return current
                    current = current.find_element(By.XPATH, "..")
                except Exception:
                    break
        return False

    def open_other_invitation_dialog(
        self,
        creator: str,
        creator_id: str | None,
    ) -> WorkflowStepResult:
        """Step 9: open the existing-invitation chooser for this recipient."""
        recipient = self._require_verified_recipient(creator, creator_id)
        if not self._target_collaboration_verified:
            raise ZiniaoWorkflowError(
                "第 9 步前置验收失败：定向合作页尚未验收。"
            )
        self._click(self._other_invitation_button())
        try:
            modal = self._wait(
                lambda _driver: self._visible_invitation_modal(creator),
                message="第 9 步失败：邀请选择弹窗未打开。",
            )
            current_tab = self._first_clickable(
                (
                    (
                        By.XPATH,
                        ".//*[@role='tab' and "
                        "(normalize-space()='进行中' "
                        "or normalize-space()='In progress')]",
                    ),
                ),
                missing_message="第 9 步失败：弹窗未显示“进行中”页签。",
            )
            create_tabs = modal.find_elements(
                By.XPATH,
                ".//*[@role='tab' and "
                "(normalize-space()='创建新邀请' "
                "or normalize-space()='Create new invitation')]",
            )
            if not create_tabs:
                raise ZiniaoWorkflowError(
                    "第 9 步验收失败：弹窗结构不完整。"
                )
        except ZiniaoWorkflowError as error:
            failure = self._failure_evidence(9)
            raise ZiniaoWorkflowError(f"{error} 诊断：{failure}") from error

        self._invitation_dialog_verified = True
        return WorkflowStepResult(
            step=9,
            action="open_other_invitation_dialog",
            success=True,
            evidence={
                **recipient,
                "dialogTitle": f"邀请 @{recipient['creatorHandle']} 合作",
                "dialogTargetMatched": True,
                "inProgressTabVisible": self._is_visible(current_tab),
                "createInvitationTabVisible": any(
                    self._is_visible(tab) for tab in create_tabs
                ),
                "invitationSent": False,
            },
        )

    def _invitation_row(
        self,
        modal: WebElement,
        invitation_name: str,
    ) -> WebElement:
        normalized_name = str(invitation_name or "").strip()
        if not normalized_name:
            raise ZiniaoWorkflowError("邀请名称不能为空。")
        matches = self._visible_exact_text_elements(
            normalized_name,
            root=modal,
        )
        rows: list[WebElement] = []
        for match in matches:
            try:
                row = match.find_element(
                    By.XPATH,
                    "ancestor::*[.//input[@type='radio']][1]",
                )
                if self._is_visible(row):
                    rows.append(row)
            except Exception:
                continue
        unique_rows = {
            row.id: row
            for row in rows
        }
        if len(unique_rows) != 1:
            raise ZiniaoWorkflowError(
                f"邀请选择失败：未找到唯一邀请“{normalized_name}”。"
            )
        return next(iter(unique_rows.values()))

    @staticmethod
    def _radio_selected(radio: WebElement) -> bool:
        return (
            radio.is_selected()
            or radio.get_attribute("checked") in {"true", "checked"}
            or radio.get_attribute("aria-checked") == "true"
        )

    def _modal_invite_button(self, modal: WebElement) -> WebElement:
        buttons = modal.find_elements(
            By.XPATH,
            ".//button[normalize-space()='邀请' "
            "or normalize-space()='Invite']",
        )
        visible = [
            button
            for button in buttons
            if self._is_visible(button)
        ]
        if len(visible) != 1:
            raise ZiniaoWorkflowError(
                "邀请弹窗未找到唯一的最终“邀请”按钮。"
            )
        return visible[0]

    def _invitation_group_ids_from_react(
        self,
        row: WebElement,
        invitation_name: str,
    ) -> set[str]:
        values = self.driver.execute_script(
            """
            const root = arguments[0];
            const expectedName = String(arguments[1] || '').trim();
            const output = [];
            const visited = new WeakSet();
            const visit = (value, depth) => {
              if (!value || typeof value !== 'object' || depth > 6) return;
              if (value instanceof Node || visited.has(value)) return;
              visited.add(value);
              const name = String(
                value.name ?? value.invitation_name
                ?? value.invitationName ?? ''
              ).trim();
              const id = value.invitation_group_id
                ?? value.invitationGroupId ?? value.id;
              if (
                name === expectedName
                && id != null
                && /^[0-9]+$/.test(String(id))
              ) output.push(String(id));
              for (const key of Object.keys(value)) {
                if (
                  ['return', 'child', 'sibling', 'stateNode', '_owner']
                    .includes(key)
                ) continue;
                let nested;
                try { nested = value[key]; } catch (_error) { continue; }
                visit(nested, depth + 1);
              }
            };
            const fiberKeys = Object.getOwnPropertyNames(root)
              .filter((key) => key.startsWith('__reactFiber$'));
            for (const key of fiberKeys) {
              let fiber = root[key];
              for (let index = 0; fiber && index < 16; index += 1) {
                visit(fiber.pendingProps, 0);
                visit(fiber.memoizedProps, 0);
                fiber = fiber.return;
              }
            }
            return Array.from(new Set(output));
            """,
            row,
            invitation_name,
        )
        return {
            str(value)
            for value in (values if isinstance(values, list) else [])
            if str(value).isdigit()
        }

    def select_invitation(
        self,
        creator: str,
        creator_id: str | None,
        invitation_name: str,
        *,
        invitation_group_id: str | None = None,
    ) -> WorkflowStepResult:
        """Step 10: select one exact invitation without submitting it."""
        recipient = self._require_verified_recipient(creator, creator_id)
        if not self._invitation_dialog_verified:
            raise ZiniaoWorkflowError(
                "第 10 步前置验收失败：邀请选择弹窗尚未验收。"
            )
        modal = self._visible_invitation_modal(creator)
        if not modal:
            raise ZiniaoWorkflowError(
                "第 10 步前置验收失败：邀请选择弹窗不可见。"
            )

        def exact_invitation_row(
            _driver: WebDriver,
        ) -> WebElement | bool:
            current_modal = self._visible_invitation_modal(creator)
            if not current_modal:
                return False
            try:
                return self._invitation_row(
                    current_modal,
                    invitation_name,
                )
            except ZiniaoWorkflowError:
                return False

        row = self._wait(
            exact_invitation_row,
            message=(
                f"第 10 步失败：邀请列表未加载唯一邀请"
                f"“{str(invitation_name).strip()}”。"
            ),
        )
        expected_group_id = str(invitation_group_id or "").strip()
        if expected_group_id and not expected_group_id.isdigit():
            raise ZiniaoWorkflowError(
                "第 10 步失败：invitationGroupId 必须是有效数字。"
            )
        react_group_ids = self._invitation_group_ids_from_react(
            row,
            str(invitation_name).strip(),
        )
        if expected_group_id and react_group_ids != {expected_group_id}:
            raise ZiniaoWorkflowError(
                "第 10 步被安全门阻止：目标邀请名称对应的 "
                "invitationGroupId 与任务快照不一致。"
            )
        radios = row.find_elements(By.CSS_SELECTOR, "input[type='radio']")
        if len(radios) != 1:
            raise ZiniaoWorkflowError(
                "第 10 步失败：目标邀请没有唯一单选框。"
            )
        radio = radios[0]
        selected_before = self._radio_selected(radio)
        if not selected_before:
            try:
                radio_label = radio.find_element(
                    By.XPATH,
                    "ancestor::label[1]",
                )
                self._click(radio_label)
            except Exception:
                self._click(row)
        self._wait(
            lambda _driver: self._radio_selected(radio),
            message="第 10 步验收失败：目标邀请未保持选中状态。",
        )
        invite_button = self._modal_invite_button(modal)
        self._wait(
            lambda _driver: (
                invite_button
                if invite_button.is_enabled()
                and invite_button.get_attribute("aria-disabled") != "true"
                else False
            ),
            message="第 10 步验收失败：选中后最终邀请按钮仍不可用。",
        )
        normalized_name = str(invitation_name).strip()
        self._selected_invitation = normalized_name
        self._selected_invitation_group_id = (
            expected_group_id
            or (
                next(iter(react_group_ids))
                if len(react_group_ids) == 1
                else None
            )
        )
        return WorkflowStepResult(
            step=10,
            action="select_invitation",
            success=True,
            evidence={
                **recipient,
                "invitationName": normalized_name,
                "invitationGroupId": (
                    self._selected_invitation_group_id or ""
                ),
                "invitationGroupIdBound": bool(expected_group_id),
                "exactInvitationMatched": True,
                "selected": True,
                "selectedBefore": selected_before,
                "inviteButtonEnabled": True,
                "invitationSent": False,
            },
        )

    def _right_panel_invitation_card(
        self,
        invitation_name: str,
        invitation_id: str | None = None,
        invitation_group_id: str | None = None,
    ) -> tuple[WebElement, str, str] | None:
        viewport_width = int(
            self.driver.execute_script("return window.innerWidth || 0;")
        )
        expected_group_id = str(invitation_group_id or "").strip()
        anchors: dict[str, WebElement] = {
            element.id: element
            for element in self._visible_exact_text_elements(invitation_name)
        }
        if expected_group_id:
            compact_id = self._xpath_literal(f"ID:{expected_group_id}")
            spaced_id = self._xpath_literal(f"ID: {expected_group_id}")
            for element in self.driver.find_elements(
                By.XPATH,
                (
                    "//*[normalize-space()="
                    f"{compact_id} or normalize-space()={spaced_id}]"
                ),
            ):
                if self._is_visible(element):
                    anchors[element.id] = element
        candidates: dict[str, tuple[WebElement, str, str]] = {}
        for element in anchors.values():
            try:
                if element.rect.get("x", 0) < viewport_width * 0.7:
                    continue
                current = element
                for _index in range(8):
                    text = str(current.get_attribute("innerText") or "")
                    id_match = re.search(r"\bID\s*:\s*(\d+)", text)
                    send_buttons = current.find_elements(
                        By.XPATH,
                        ".//button[normalize-space()='发送' "
                        "or normalize-space()='Send']",
                    )
                    if id_match and any(
                        self._is_visible(button) for button in send_buttons
                    ):
                        displayed_group_id = id_match.group(1)
                        if invitation_name not in text:
                            current = current.find_element(By.XPATH, "..")
                            continue
                        react_ids = (
                            self._invitation_ids_from_react(current)
                            or self._invitation_ids_from_react(element)
                        )
                        actual_invitation_id = react_ids.get(
                            "invitationId", ""
                        )
                        react_group_id = react_ids.get(
                            "invitationGroupId", ""
                        )
                        group_ids_match = (
                            (
                                bool(react_group_id)
                                and react_group_id == displayed_group_id
                            )
                            or (
                                not react_group_id
                                and bool(expected_group_id)
                                and displayed_group_id == expected_group_id
                            )
                        )
                        expected_invitation_id = str(
                            invitation_id or ""
                        ).strip()
                        if (
                            group_ids_match
                            and (
                                (
                                    expected_invitation_id
                                    and actual_invitation_id
                                    == expected_invitation_id
                                )
                                or (
                                    not expected_invitation_id
                                    and (
                                        not actual_invitation_id
                                        or actual_invitation_id.isdigit()
                                    )
                                )
                            )
                            and (
                                not expected_group_id
                                or displayed_group_id
                                == expected_group_id
                            )
                        ):
                            candidates[current.id] = (
                                current,
                                displayed_group_id,
                                actual_invitation_id,
                            )
                        break
                    current = current.find_element(By.XPATH, "..")
            except StaleElementReferenceException:
                continue
            except Exception:
                continue
        if len(candidates) == 1:
            return next(iter(candidates.values()))
        return None

    def _invitation_ids_from_react(
        self,
        element: WebElement,
    ) -> dict[str, str]:
        result = self.driver.execute_script(
            """
            const element = arguments[0];
            const output = [];
            const visited = new WeakSet();
            function visit(value, depth) {
              if (!value || typeof value !== 'object' || depth > 5) return;
              if (value instanceof Node || visited.has(value)) return;
              visited.add(value);
              const invitationId = (
                value.invitation_id ?? value.invitationId
              );
              const invitationGroupId = (
                value.invitation_group_id ?? value.invitationGroupId
              );
              if (
                invitationId != null
                && invitationGroupId != null
              ) {
                output.push({
                  invitationId: String(invitationId),
                  invitationGroupId: String(invitationGroupId)
                });
              }
              for (const key of Object.keys(value)) {
                if (
                  ['return', 'child', 'sibling', 'stateNode', '_owner']
                    .includes(key)
                ) continue;
                let nested;
                try { nested = value[key]; } catch (_error) { continue; }
                visit(nested, depth + 1);
              }
            }
            const fiberKeys = Object.getOwnPropertyNames(element)
              .filter((key) => key.startsWith('__reactFiber$'));
            for (const key of fiberKeys) {
              let fiber = element[key];
              for (let index = 0; fiber && index < 14; index += 1) {
                visit(fiber.pendingProps, 0);
                visit(fiber.memoizedProps, 0);
                fiber = fiber.return;
              }
            }
            const unique = new Map();
            for (const row of output) {
              unique.set(
                `${row.invitationId}:${row.invitationGroupId}`,
                row
              );
            }
            return Array.from(unique.values());
            """,
            element,
        )
        rows = result if isinstance(result, list) else []
        if len(rows) != 1 or not isinstance(rows[0], dict):
            return {}
        invitation_id = str(rows[0].get("invitationId") or "")
        group_id = str(rows[0].get("invitationGroupId") or "")
        if not invitation_id.isdigit() or not group_id.isdigit():
            return {}
        return {
            "invitationId": invitation_id,
            "invitationGroupId": group_id,
        }

    def _right_panel_invitation_visible(
        self,
        invitation_name: str,
        invitation_id: str | None = None,
        invitation_group_id: str | None = None,
    ) -> bool:
        return (
            self._right_panel_invitation_card(
                invitation_name,
                invitation_id,
                invitation_group_id,
            )
            is not None
        )

    def _target_collaboration_count(self) -> int:
        text = self._target_collaboration_tab().text.strip()
        values = re.findall(r"\d+", text)
        return int(values[-1]) if values else 0

    def _close_invitation_modal(self, modal: WebElement) -> None:
        cancel_buttons = modal.find_elements(
            By.XPATH,
            ".//button[normalize-space()='取消' "
            "or normalize-space()='Cancel']",
        )
        visible = [
            button
            for button in cancel_buttons
            if self._is_visible(button)
        ]
        if len(visible) == 1:
            self._click(visible[0])
            self._wait(
                lambda _driver: (
                    not self._visible_invitation_modal(
                        f"@{self._verified_recipient[0]}"
                    )
                ),
                message="关闭已存在邀请的选择弹窗失败。",
            )

    def _refresh_and_verify_invitation(
        self,
        creator: str,
        creator_id: str | None,
        invitation_name: str,
        *,
        verification_timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        refresh_wait_seconds = self._refresh_page()

        def verified_recipient(_driver: WebDriver) -> dict[str, Any] | bool:
            try:
                return self._recipient_evidence(creator, creator_id)
            except ZiniaoWorkflowError:
                return False

        self._wait(
            verified_recipient,
            message=(
                "第 11 步复核失败：刷新后目标聊天页未重新加载。"
            ),
            timeout_seconds=verification_timeout_seconds,
        )
        tab = self._target_collaboration_tab()
        active = (
            tab.get_attribute("aria-selected") == "true"
            or "active" in (tab.get_attribute("class") or "").lower()
        )
        if not active:
            self._click(tab)
        self._wait(
            lambda _driver: (
                self._right_panel_invitation_visible(invitation_name)
                or False
            ),
            message=(
                "第 11 步复核失败：刷新后未看到已发送的定向合作邀请。"
            ),
            timeout_seconds=verification_timeout_seconds,
        )
        collaboration_count = self._wait(
            lambda _driver: self._target_collaboration_count() or False,
            message=(
                "第 11 步复核失败：刷新后“定向合作”数量未更新。"
            ),
            timeout_seconds=verification_timeout_seconds,
        )
        card_match = self._right_panel_invitation_card(invitation_name)
        if card_match is None:
            raise ZiniaoWorkflowError(
                "第 11 步复核失败：目标邀请卡片结构不完整。"
            )
        _card, invitation_group_id, invitation_id = card_match
        return {
            "rightPanelInvitationVisible": True,
            "targetCollaborationCount": int(collaboration_count),
            "invitationId": invitation_id,
            "invitationGroupId": invitation_group_id,
            "successMessage": "",
            "verifiedAfterRefresh": True,
            "refreshWaitSeconds": refresh_wait_seconds,
        }

    def _refresh_invitation_with_retries(
        self,
        creator: str,
        creator_id: str | None,
        invitation_name: str,
        *,
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        """Retry eventual-consistency refreshes without re-clicking Invite."""
        random_wait_seconds: list[float] = []
        last_error = ""
        for attempt in range(1, max_attempts + 1):
            try:
                verified = self._refresh_and_verify_invitation(
                    creator,
                    creator_id,
                    invitation_name,
                    verification_timeout_seconds=min(
                        5,
                        self.timeout_seconds,
                    ),
                )
            except ZiniaoWorkflowError as error:
                last_error = str(error)
                continue
            wait_seconds = float(verified.get("refreshWaitSeconds") or 0)
            random_wait_seconds.append(wait_seconds)
            return {
                **verified,
                "refreshAttempts": attempt,
                "randomWaitSeconds": random_wait_seconds,
                "invitationSyncPending": False,
                "skipCreator": False,
            }
        random_wait_seconds = list(self._refresh_wait_seconds[-max_attempts:])
        return {
            "rightPanelInvitationVisible": False,
            "verifiedAfterRefresh": False,
            "refreshAttempts": max_attempts,
            "randomWaitSeconds": random_wait_seconds,
            "invitationSyncPending": True,
            "skipCreator": True,
            "skipReason": (
                "页面提示邀请添加成功，但按 5–8 秒随机间隔连续刷新 "
                f"{max_attempts} 次后仍未显示定向合作卡片；本次跳过该达人。"
            ),
            "lastRefreshError": last_error,
        }

    def send_selected_invitation(
        self,
        creator: str,
        creator_id: str | None,
        invitation_name: str,
        *,
        invitation_group_id: str | None = None,
        confirm_send: bool = False,
    ) -> WorkflowStepResult:
        """Step 11: click the exact invitation once, then hand off to phase 2."""
        recipient = self._require_verified_recipient(creator, creator_id)
        normalized_name = str(invitation_name or "").strip()
        expected_group_id = str(invitation_group_id or "").strip()
        if expected_group_id and not expected_group_id.isdigit():
            raise ZiniaoWorkflowError(
                "第 11 步失败：invitationGroupId 必须是有效数字。"
            )
        if confirm_send is not True:
            raise ZiniaoWorkflowError(
                "第 11 步被安全门阻止：必须显式确认发送邀请。"
            )
        if not (
            self._greeting_delivery_verified
            and self._target_collaboration_verified
            and self._invitation_dialog_verified
            and self._selected_invitation == normalized_name
            and (
                not expected_group_id
                or self._selected_invitation_group_id
                == expected_group_id
            )
        ):
            raise ZiniaoWorkflowError(
                "第 11 步前置验收失败：并非所有前置步骤均已通过。"
            )
        modal = self._visible_invitation_modal(creator)
        if not modal:
            raise ZiniaoWorkflowError(
                "第 11 步前置验收失败：邀请弹窗不可见。"
            )
        row = self._invitation_row(modal, normalized_name)
        radios = row.find_elements(By.CSS_SELECTOR, "input[type='radio']")
        if len(radios) != 1 or not self._radio_selected(radios[0]):
            raise ZiniaoWorkflowError(
                "第 11 步前置验收失败：目标邀请未被选中。"
            )
        invite_button = self._modal_invite_button(modal)
        if (
            not invite_button.is_enabled()
            or invite_button.get_attribute("aria-disabled") == "true"
        ):
            raise ZiniaoWorkflowError(
                "第 11 步前置验收失败：最终邀请按钮不可用。"
            )

        if self._right_panel_invitation_visible(
            normalized_name,
            invitation_group_id=expected_group_id,
        ):
            self._close_invitation_modal(modal)
            card_match = self._right_panel_invitation_card(
                normalized_name,
                invitation_group_id=expected_group_id,
            )
            invitation_group_id = card_match[1] if card_match else ""
            if (
                expected_group_id
                and invitation_group_id != expected_group_id
            ):
                raise ZiniaoWorkflowError(
                    "第 11 步被安全门阻止：右侧合作卡片的 "
                    "invitationGroupId 与任务快照不一致。"
                )
            self._created_invitation = {
                "name": normalized_name,
                "invitationGroupId": invitation_group_id,
            }
            cleanup = self.close_creator_tabs_keep_search()
            return WorkflowStepResult(
                step=11,
                action="send_selected_invitation",
                success=True,
                evidence={
                    **recipient,
                    "invitationName": normalized_name,
                    "allPreconditionsVerified": True,
                    "alreadySent": True,
                    "finalInviteButtonClicked": False,
                    "invitationButtonClicked": False,
                    "invitationSubmissionAttempted": False,
                    "invitationCompletionSource": "existing_invitation",
                    "invitationSubmissionConfirmed": True,
                    "invitationConfirmationSource": (
                        "existing_right_panel_invitation_card"
                    ),
                    "dialogClosed": True,
                    "rightPanelInvitationVisible": True,
                    "invitationGroupId": invitation_group_id,
                    "invitationCreated": True,
                    "invitationCompleted": True,
                    "cardReadyToSend": False,
                    "invitationSent": True,
                    "actionWaitSeconds": list(self._action_wait_seconds),
                    **cleanup,
                },
            )

        self._click(invite_button, fixed_wait_seconds=3.0)
        invitation_group_id = expected_group_id
        self._created_invitation = {
            "name": normalized_name,
            "invitationGroupId": invitation_group_id,
        }
        time.sleep(3.0)
        cleanup = self.close_creator_tabs_keep_search()
        return WorkflowStepResult(
            step=11,
            action="send_selected_invitation",
            success=True,
            evidence={
                **recipient,
                "invitationName": normalized_name,
                "allPreconditionsVerified": True,
                "alreadySent": False,
                "finalInviteButtonClicked": True,
                "invitationButtonClicked": True,
                "invitationSubmissionAttempted": True,
                "invitationSubmissionConfirmed": False,
                "invitationCompletionSource": "final_invite_button_click",
                "invitationGroupId": invitation_group_id,
                "invitationCreated": True,
                "invitationCompleted": True,
                "cardReadyToSend": False,
                "invitationSent": True,
                "invitationPostClickWaitSeconds": 3.0,
                "actionWaitSeconds": list(self._action_wait_seconds),
                **cleanup,
            },
        )

    def _collaboration_card_send_button(
        self,
        card: WebElement,
    ) -> WebElement:
        buttons = card.find_elements(
            By.XPATH,
            ".//button[normalize-space()='发送' "
            "or normalize-space()='Send']",
        )
        visible = [
            button
            for button in buttons
            if self._is_visible(button) and button.is_enabled()
        ]
        if len(visible) != 1:
            raise ZiniaoWorkflowError(
                "第 12 步失败：目标合作卡片未找到唯一可用“发送”按钮。"
            )
        return visible[0]

    def _chat_plan_card_evidence(
        self,
        invitation_name: str,
        invitation_id: str | None,
    ) -> dict[str, Any]:
        del invitation_name
        normalized_invitation_id = str(invitation_id or "").strip()
        if (
            normalized_invitation_id
            and not normalized_invitation_id.isdigit()
        ):
            return {
                "exactPlanCardVisible": False,
                "exactPlanCardCount": 0,
                "planCardServerIds": [],
                "planCardMessageKeys": [],
                "verifiedPlanCardMessageKeys": [],
                "targetPlanInvitationIds": [],
                "verifiedPlanCards": [],
                "targetPlanMessageVerified": False,
                "targetPlanPendingCount": 0,
                "targetPlanFailedCount": 0,
            }
        rows = self.driver.execute_script(
            """
            const scalar = (value, key) => {
              if (!value) return undefined;
              try {
                if (
                  typeof value.get === 'function'
                  && value.get(key) !== undefined
                ) return value.get(key);
              } catch (_error) {}
              return value[key];
            };
            const chain = (node) => {
              if (!node) return [];
              const key = Object.getOwnPropertyNames(node)
                .find((name) => name.startsWith('__reactFiber$'));
              const result = [];
              const visited = new Set();
              let fiber = key ? node[key] : null;
              for (
                let depth = 0;
                fiber && depth < 32 && !visited.has(fiber);
                depth += 1, fiber = fiber.return
              ) {
                visited.add(fiber);
                result.push(fiber);
              }
              return result;
            };
            let roots = Array.from(document.querySelectorAll(
              '[data-e2e="11308c62-1492-214d"]'
            ));
            if (!roots.length) {
              roots = Array.from(document.querySelectorAll(
                '.chatd-message'
              ));
            }
            const output = [];
            for (const root of roots) {
              let message = null;
              let uiMessageStatus = '';
              let position = '';
              const nodes = [
                root,
                root.querySelector
                  ? root.querySelector('.chatd-message')
                  : null
              ].filter(Boolean);
              for (const fiber of nodes.flatMap(chain)) {
                for (const props of [
                  fiber.memoizedProps,
                  fiber.pendingProps
                ]) {
                  if (!props || typeof props !== 'object') continue;
                  if (
                    !message && props.message
                    && typeof props.message === 'object'
                  ) message = props.message;
                  if (
                    !uiMessageStatus
                    && typeof props.messageStatus === 'string'
                  ) uiMessageStatus = props.messageStatus;
                  if (
                    !position && typeof props.position === 'string'
                  ) position = props.position;
                }
              }
              if (!message) continue;
              const raw = message.rawMessage || {};
              const ext = message.ext || raw.originExt || {};
              output.push({
                messageId: String(
                  message.messageId || raw.clientId || ''
                ),
                clientId: String(
                  raw.clientId || message.messageId || ''
                ),
                serverId: String(raw.serverId || ''),
                type: String(scalar(ext, 'type') || ''),
                invitationId: String(
                  scalar(ext, 'invitationId')
                  ?? scalar(ext, 'invitation_id') ?? ''
                ),
                flightStatus: Number(
                  message.flightStatus ?? raw.flightStatus ?? -999
                ),
                isFromMe: (
                  message.isFromMe ?? raw.isFromMe
                ) === true,
                createTime: Number(
                  message.createTime ?? raw.createTime ?? 0
                ),
                uiMessageStatus,
                position
              });
            }
            const unique = new Map();
            for (const row of output) {
              const key = [
                row.messageId,
                row.clientId,
                row.serverId,
                row.invitationId
              ].join(':');
              unique.set(key, row);
            }
            return Array.from(unique.values());
            """
        )
        target_rows = [
            row
            for row in (rows if isinstance(rows, list) else [])
            if (
                isinstance(row, dict)
                and str(row.get("type") or "") == "targetPlan"
                and (
                    not normalized_invitation_id
                    or str(row.get("invitationId") or "")
                    == normalized_invitation_id
                )
                and row.get("isFromMe") is True
            )
        ]
        bad_ui_statuses = {
            "pending",
            "failed",
            "error",
            "sending",
        }
        succeeded = [
            row
            for row in target_rows
            if (
                row.get("flightStatus") in {3, 4}
                and str(row.get("serverId") or "")
                and str(row.get("uiMessageStatus") or "").lower()
                not in bad_ui_statuses
            )
        ]
        pending = [
            row
            for row in target_rows
            if row.get("flightStatus") in {0, 1, 2}
        ]
        failed = [
            row
            for row in target_rows
            if row.get("flightStatus") in {-3, -2, -1}
        ]

        def message_key(row: dict[str, Any]) -> str:
            return ":".join(
                (
                    str(row.get("messageId") or ""),
                    str(row.get("clientId") or ""),
                    str(row.get("serverId") or ""),
                )
            )

        matches = {
            str(row["serverId"]): row
            for row in succeeded
        }
        verified_cards = sorted(
            (
                {
                    "messageKey": message_key(row),
                    "invitationId": str(
                        row.get("invitationId") or ""
                    ),
                    "serverId": str(row.get("serverId") or ""),
                    "flightStatus": int(
                        row.get("flightStatus") or 0
                    ),
                    "createTime": int(row.get("createTime") or 0),
                }
                for row in succeeded
            ),
            key=lambda row: (
                row["createTime"],
                row["serverId"],
            ),
        )
        return {
            "exactPlanCardVisible": bool(matches),
            "exactPlanCardCount": len(matches),
            "planCardServerIds": sorted(matches),
            "planCardMessageKeys": sorted(
                message_key(row) for row in target_rows
            ),
            "verifiedPlanCardMessageKeys": sorted(
                row["messageKey"] for row in verified_cards
            ),
            "targetPlanInvitationIds": sorted(
                {
                    row["invitationId"]
                    for row in verified_cards
                    if row["invitationId"].isdigit()
                }
            ),
            "verifiedPlanCards": verified_cards,
            "targetPlanMessageVerified": bool(matches),
            "targetPlanFlightStatus": (
                int(succeeded[0]["flightStatus"])
                if succeeded
                else None
            ),
            "targetPlanFromMe": True if matches else None,
            "targetPlanPendingCount": len(pending),
            "targetPlanFailedCount": len(failed),
            "targetPlanCreateTimes": sorted(
                int(row.get("createTime") or 0)
                for row in succeeded
            ),
        }

    def _visible_card_send_success_text(self) -> str:
        for element in self.driver.find_elements(
            By.XPATH,
            "//*[contains(@class, 'toast') "
            "or contains(@class, 'message') "
            "or @role='alert']",
        ):
            try:
                text = element.text.strip()
                lower = text.lower()
                if (
                    self._is_visible(element)
                    and text
                    and (
                        "发送成功" in text
                        or "发送计划卡片成功" in text
                        or "sent successfully" in lower
                        or "plan card sent" in lower
                    )
                ):
                    return text[:160]
            except StaleElementReferenceException:
                continue
        return ""

    def _refresh_and_verify_plan_card(
        self,
        creator: str,
        creator_id: str | None,
        invitation_name: str,
        invitation_id: str,
    ) -> dict[str, Any]:
        refresh_wait_seconds = self._refresh_page()
        self._wait(
            lambda _driver: (
                self._recipient_evidence(creator, creator_id)
                if self._visible_message_composers()
                else False
            ),
            message="第 12 步复核失败：刷新后目标聊天页未重新加载。",
        )
        evidence = self._wait(
            lambda _driver: (
                result
                if (
                    result := self._chat_plan_card_evidence(
                        invitation_name,
                        invitation_id,
                    )
                )["exactPlanCardVisible"]
                else False
            ),
            message=(
                "第 12 步复核失败：刷新后未找到目标合作计划卡片。"
            ),
        )
        return {
            **evidence,
            "verifiedAfterRefresh": True,
            "refreshWaitSeconds": refresh_wait_seconds,
        }

    def send_collaboration_card(
        self,
        creator: str,
        creator_id: str | None,
        invitation_name: str,
        invitation_id: str | None = None,
        *,
        invitation_group_id: str | None = None,
        confirm_send: bool = False,
    ) -> WorkflowStepResult:
        """Step 12: refresh, send the exact right-panel card, and verify it."""
        recipient = self._require_verified_recipient(creator, creator_id)
        normalized_name = str(invitation_name or "").strip()
        expected_group_id = str(
            invitation_group_id
            or (
                self._created_invitation.get("invitationGroupId")
                if self._created_invitation
                else ""
            )
            or ""
        ).strip()
        if expected_group_id and not expected_group_id.isdigit():
            raise ZiniaoWorkflowError(
                "第 12 步失败：invitationGroupId 必须是有效数字。"
            )
        if confirm_send is not True:
            raise ZiniaoWorkflowError(
                "第 12 步被安全门阻止：必须显式确认发送合作卡片。"
            )
        if (
            self._created_invitation is None
            or self._created_invitation.get("name") != normalized_name
        ):
            raise ZiniaoWorkflowError(
                "第 12 步前置验收失败：定向合作邀请尚未创建并验收。"
            )
        if (
            expected_group_id
            and self._created_invitation.get("invitationGroupId")
            != expected_group_id
        ):
            raise ZiniaoWorkflowError(
                "第 12 步前置验收失败：已创建邀请的 "
                "invitationGroupId 与任务快照不一致。"
            )

        refreshed = self._refresh_and_verify_invitation(
            creator,
            creator_id,
            normalized_name,
        )
        refreshed_id = str(refreshed.get("invitationId") or "")
        expected_id = str(
            invitation_id
            or self._created_invitation.get("invitationId")
            or refreshed_id
        ).strip()
        if expected_id and refreshed_id != expected_id:
            raise ZiniaoWorkflowError(
                "第 12 步前置验收失败：刷新后的邀请 ID 与任务目标不一致。"
            )
        refreshed_group_id = str(
            refreshed.get("invitationGroupId") or ""
        )
        if (
            expected_group_id
            and refreshed_group_id != expected_group_id
        ):
            raise ZiniaoWorkflowError(
                "第 12 步前置验收失败：刷新后的 "
                "invitationGroupId 与任务快照不一致。"
            )
        expected_id = refreshed_id
        self._created_invitation = {
            "name": normalized_name,
            "invitationId": expected_id,
            "invitationGroupId": str(
                refreshed.get("invitationGroupId") or ""
            ),
        }

        existing = self._chat_plan_card_evidence(
            normalized_name,
            expected_id,
        )
        if existing["exactPlanCardVisible"]:
            self._collaboration_card_delivery_verified = True
            cleanup = self.close_creator_tabs_keep_search()
            return WorkflowStepResult(
                step=12,
                action="send_collaboration_card",
                success=True,
                evidence={
                    **recipient,
                    **refreshed,
                    **existing,
                    "invitationName": normalized_name,
                    "invitationId": expected_id,
                    "alreadySent": True,
                    "cardSendButtonClicked": False,
                    "cardSent": True,
                    "finalSendVerified": True,
                    **cleanup,
                },
            )
        if existing["targetPlanPendingCount"]:
            raise ZiniaoWorkflowError(
                "第 12 步被幂等安全门阻止：该合作卡片已有发送中消息，"
                "本次不会重复点击。"
            )
        if existing["targetPlanFailedCount"]:
            raise ZiniaoWorkflowError(
                "第 12 步被幂等安全门阻止：该合作卡片存在失败或"
                "状态不明的历史消息，需要人工复核后才能重试。"
            )

        card_match = self._right_panel_invitation_card(
            normalized_name,
            expected_id,
            expected_group_id,
        )
        if card_match is None:
            raise ZiniaoWorkflowError(
                "第 12 步前置验收失败：未找到唯一目标合作卡片。"
            )
        card, invitation_group_id, actual_invitation_id = card_match
        if actual_invitation_id != expected_id:
            raise ZiniaoWorkflowError(
                "第 12 步前置验收失败：卡片实际 invitation_id 不一致。"
            )
        send_button = self._collaboration_card_send_button(card)
        baseline_keys = set(existing["planCardMessageKeys"])
        click_started_ms = int(
            self.driver.execute_script("return Date.now();")
        )
        self._click(send_button)

        def delivered(_driver: WebDriver) -> dict[str, Any] | bool:
            plan_card = self._chat_plan_card_evidence(
                normalized_name,
                expected_id,
            )
            if (
                plan_card["targetPlanFailedCount"]
                and not plan_card["targetPlanMessageVerified"]
            ):
                raise ZiniaoWorkflowError(
                    "第 12 步验收失败：合作卡片消息进入失败状态。"
                )
            current_keys = set(plan_card["planCardMessageKeys"])
            new_message = bool(current_keys - baseline_keys)
            if plan_card["targetPlanMessageVerified"] and new_message:
                create_times_ms = [
                    value if value >= 1_000_000_000_000 else value * 1000
                    for value in plan_card["targetPlanCreateTimes"]
                    if value > 0
                ]
                return {
                    **plan_card,
                    "successMessage": self._visible_card_send_success_text(),
                    "newTargetPlanMessage": True,
                    "createdNearClick": (
                        any(
                            value >= click_started_ms - 5000
                            for value in create_times_ms
                        )
                        if create_times_ms
                        else None
                    ),
                    "verifiedAfterRefresh": False,
                }
            return False

        try:
            delivery = self._wait(
                delivered,
                message=(
                    "第 12 步验收失败：点击卡片发送后未看到成功证据。"
                ),
                timeout_seconds=min(12, self.timeout_seconds),
            )
        except ZiniaoWorkflowError as error:
            try:
                delivery = self._refresh_and_verify_plan_card(
                    creator,
                    creator_id,
                    normalized_name,
                    expected_id,
                )
            except ZiniaoWorkflowError as refresh_error:
                failure = self._failure_evidence(12)
                raise ZiniaoWorkflowError(
                    f"{error} 刷新只读复核也失败：{refresh_error} "
                    f"诊断：{failure}"
                ) from refresh_error

        self._collaboration_card_delivery_verified = True
        cleanup = self.close_creator_tabs_keep_search()
        return WorkflowStepResult(
            step=12,
            action="send_collaboration_card",
            success=True,
            evidence={
                **recipient,
                **refreshed,
                **delivery,
                "invitationName": normalized_name,
                "invitationId": expected_id,
                "invitationGroupId": invitation_group_id,
                "alreadySent": False,
                "cardSendButtonClicked": True,
                "cardSent": True,
                "finalSendVerified": True,
                **cleanup,
            },
        )

    @staticmethod
    def _xpath_literal(value: str) -> str:
        if "'" not in value:
            return f"'{value}'"
        if '"' not in value:
            return f'"{value}"'
        parts = value.split("'")
        return "concat(" + ', "\'", '.join(f"'{part}'" for part in parts) + ")"
