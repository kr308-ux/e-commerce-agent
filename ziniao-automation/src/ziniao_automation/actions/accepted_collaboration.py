"""Send collaboration cards from a project's accepted-creator list.

This is the second phase of creator contact.  It never creates invitations:
only creators whose invitation phase was persisted by Django are passed in.
"""

from __future__ import annotations

from typing import Any

from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement

from ..errors import ZiniaoWorkflowError
from .collaboration_sync import TargetCollaborationSync
from .creator_contact import CreatorContactWorkflow, WorkflowStepResult


class AcceptedCollaborationWorkflow(CreatorContactWorkflow):
    """Operate one exact project and one accepted creator."""

    def __init__(self, driver: WebDriver, *, timeout_seconds: int = 60):
        super().__init__(driver, timeout_seconds=timeout_seconds)
        self._project_name: str | None = None
        self._project_group_id: str | None = None
        self._accepted_creator: str | None = None

    def _visible_rows(self) -> list[WebElement]:
        return [
            row
            for row in self.driver.find_elements(By.CSS_SELECTOR, "tbody tr")
            if self._is_visible(row)
        ]

    def _unique_row_with_text(
        self,
        text: str,
        *,
        missing_message: str,
    ) -> WebElement:
        normalized = str(text or "").strip().lstrip("@").casefold()
        matches: dict[str, WebElement] = {}
        for row in self._visible_rows():
            try:
                row_tokens = {
                    token.strip().lstrip("@").casefold()
                    for token in str(
                        row.get_attribute("innerText") or ""
                    ).splitlines()
                    if token.strip()
                }
                if normalized in row_tokens:
                    matches[row.id] = row
            except StaleElementReferenceException:
                continue
        if len(matches) != 1:
            raise ZiniaoWorkflowError(missing_message)
        return next(iter(matches.values()))

    def _project_snapshot(
        self,
        sync: TargetCollaborationSync,
        invitation_name: str,
        invitation_group_id: str,
    ) -> dict[str, Any]:
        def ready_snapshot(_driver: WebDriver) -> Any:
            try:
                current = sync._read_snapshot()
            except ZiniaoWorkflowError:
                return False
            return current if current.ready and current.records else False

        snapshot = sync._wait(
            ready_snapshot,
            message="定向合作列表未加载项目数据。",
        )
        options = [
            sync.option_from_record(record).to_dict()
            for record in snapshot.records
        ]
        matches = [
            record
            for record in options
            if (
                str(record.get("name") or "").strip()
                == invitation_name
                and str(record.get("invitationGroupId") or "").strip()
                == invitation_group_id
            )
        ]
        if len(matches) != 1:
            raise ZiniaoWorkflowError(
                "定向合作列表未找到名称和 invitationGroupId 均精确匹配的唯一项目。"
            )
        return matches[0]

    @staticmethod
    def _accepted_cell(row: WebElement) -> WebElement:
        cells = row.find_elements(By.CSS_SELECTOR, "td")
        if len(cells) < 2:
            raise ZiniaoWorkflowError("目标项目行缺少“已接受的达人”列。")
        return cells[1]

    def _activate_accepted_creators_window(
        self,
        invitation_name: str,
        invitation_group_id: str,
        *,
        require_group_marker: bool = True,
    ) -> dict[str, Any] | bool:
        for handle in reversed(list(self.driver.window_handles)):
            try:
                self.driver.switch_to.window(handle)
                ready = self.driver.execute_script(
                    "return document.readyState"
                )
                if ready not in {"interactive", "complete"}:
                    continue
                name_visible = bool(
                    self._visible_exact_text_elements(invitation_name)
                )
                details_visible = any(
                    self._is_visible(element)
                    for element in self.driver.find_elements(
                        By.XPATH,
                        "//*[contains(normalize-space(.), '达人详情') "
                        "or contains(normalize-space(.), "
                        "'Creator details')]",
                    )
                )
                stored_group_id = str(
                    self.driver.execute_script(
                        "return window.sessionStorage.getItem("
                        "'ziniaoAcceptedProjectGroupId') || '';"
                    )
                    or ""
                )
                if (
                    name_visible
                    and details_visible
                    and (
                        not require_group_marker
                        or stored_group_id == invitation_group_id
                    )
                ):
                    return {
                        "handle": handle,
                        "currentUrl": self.driver.current_url,
                    }
            except Exception:
                continue
        return False

    def _creator_details_toggle(self) -> WebElement:
        for _scroll_index in range(12):
            candidates: dict[str, WebElement] = {}
            for clickable in self.driver.find_elements(
                By.XPATH,
                "//*[@role='button' and @aria-expanded and "
                "(.//*[contains(normalize-space(.), '达人详情')] "
                "or .//*[contains(normalize-space(.), "
                "'Creator details')])]",
            ):
                try:
                    if not self._is_visible(clickable):
                        continue
                    text = " ".join(clickable.text.split())
                    if len(text) > 80:
                        continue
                    candidates[clickable.id] = clickable
                except StaleElementReferenceException:
                    continue
            if len(candidates) == 1:
                return next(iter(candidates.values()))
            if len(candidates) > 1:
                raise ZiniaoWorkflowError(
                    "项目页面出现多个“达人详情”展开入口。"
                )
            moved = self.driver.execute_script(
                """
                const before = window.scrollY;
                window.scrollBy(0, Math.max(500, window.innerHeight * 0.8));
                return window.scrollY > before;
                """
            )
            if not moved:
                break
        raise ZiniaoWorkflowError(
            "向下定位后仍未找到唯一的“达人详情”展开入口。"
        )

    def _ensure_creator_details_expanded(self) -> bool:
        if self._visible_rows():
            return False
        self._click(self._creator_details_toggle())
        self._wait(
            lambda _driver: self._visible_rows() or False,
            message="点击“达人详情”后达人列表未展开。",
        )
        return True

    def open_project_accepted_creators(
        self,
        invitation_name: str,
        invitation_group_id: str,
    ) -> WorkflowStepResult:
        """Open the accepted-creator column for one exact project."""
        name = str(invitation_name or "").strip()
        group_id = str(invitation_group_id or "").strip()
        if not name or not group_id.isdigit():
            raise ZiniaoWorkflowError(
                "项目名称不能为空且 invitationGroupId 必须为数字。"
            )
        sync = TargetCollaborationSync(
            self.driver,
            timeout_seconds=self.timeout_seconds,
        )
        reused = self._activate_accepted_creators_window(name, group_id)
        if reused is not False:
            details_clicked = self._ensure_creator_details_expanded()
            accepted_count = len(self._visible_rows())
            self._project_name = name
            self._project_group_id = group_id
            return WorkflowStepResult(
                step=1,
                action="open_project_accepted_creators",
                success=True,
                evidence={
                    "invitationName": name,
                    "invitationGroupId": group_id,
                    "acceptedCreatorCount": accepted_count,
                    "acceptedCreatorsPageVisible": True,
                    "creatorDetailsClicked": details_clicked,
                    "creatorDetailsExpanded": True,
                    "currentUrl": reused["currentUrl"],
                    "projectPageReused": True,
                    "pageRefreshSkipped": True,
                    "actionWaitSeconds": list(
                        self._action_wait_seconds
                    ),
                    "refreshWaitSeconds": list(
                        self._refresh_wait_seconds
                    ),
                    "cdpClickRecoveryCount": (
                        self._cdp_click_recovery_count
                    ),
                },
            )
        if sync._affiliate_window() is None:
            self.open_find_creators()
            if sync._affiliate_window() is None:
                raise ZiniaoWorkflowError(
                    "当前浏览器没有可用的联盟页面。"
                )
        source_url = self.driver.current_url
        direct_url = sync._direct_target_url(source_url)
        opened_navigation_handle = ""
        if direct_url:
            self.driver.switch_to.new_window("tab")
            opened_navigation_handle = self.driver.current_window_handle
            self.driver.get(direct_url)
            sync._wait_for_document()
            if not sync._is_target_url(self.driver.current_url):
                raise ZiniaoWorkflowError(
                    "新标签未进入定向合作列表。"
                )
        else:
            sync.open_target_page()
        sync.activate_in_progress()
        project = self._project_snapshot(sync, name, group_id)
        row = self._unique_row_with_text(
            name,
            missing_message="未找到目标定向合作项目所在的唯一行。",
        )
        accepted_cell = self._accepted_cell(row)
        accepted_count = int(project.get("acceptedCreatorCount") or 0)
        if accepted_count <= 0:
            raise ZiniaoWorkflowError(
                "目标定向合作项目当前没有已接受的达人。"
            )
        click_targets = [
            element
            for element in accepted_cell.find_elements(
                By.CSS_SELECTOR,
                "a, button, [role='button']",
            )
            if self._is_visible(element) and element.is_enabled()
        ]
        self._click(click_targets[0] if len(click_targets) == 1 else accepted_cell)
        detail_window = self._wait(
            lambda _driver: (
                self._activate_accepted_creators_window(name, group_id)
                or self._activate_accepted_creators_window(
                    name,
                    group_id,
                    require_group_marker=False,
                )
            ),
            message="点击“已接受的达人”后未进入项目达人详情页。",
        )
        self.driver.execute_script(
            "window.sessionStorage.setItem("
            "'ziniaoAcceptedProjectGroupId', arguments[0]);",
            group_id,
        )
        detail_window = {
            "handle": self.driver.current_window_handle,
            "currentUrl": self.driver.current_url,
        }
        if (
            opened_navigation_handle
            and opened_navigation_handle != detail_window["handle"]
            and opened_navigation_handle in self.driver.window_handles
        ):
            self.driver.switch_to.window(opened_navigation_handle)
            self.driver.close()
            self.driver.switch_to.window(detail_window["handle"])
        details_clicked = self._ensure_creator_details_expanded()
        self._project_name = name
        self._project_group_id = group_id
        return WorkflowStepResult(
            step=1,
            action="open_project_accepted_creators",
            success=True,
            evidence={
                "invitationName": name,
                "invitationGroupId": group_id,
                "acceptedCreatorCount": accepted_count,
                "acceptedCreatorsPageVisible": True,
                "creatorDetailsClicked": details_clicked,
                "creatorDetailsExpanded": True,
                "currentUrl": detail_window["currentUrl"],
                "projectPageReused": False,
                "pageRefreshSkipped": True,
                "actionWaitSeconds": list(self._action_wait_seconds),
                "refreshWaitSeconds": list(self._refresh_wait_seconds),
                "cdpClickRecoveryCount": self._cdp_click_recovery_count,
            },
        )

    def _creator_chat_button(self, row: WebElement) -> WebElement:
        cells = row.find_elements(By.CSS_SELECTOR, "td")
        if not cells:
            raise ZiniaoWorkflowError("已接受达人行结构不完整。")
        operation_cell = cells[-1]
        candidates = [
            element
            for element in operation_cell.find_elements(
                By.CSS_SELECTOR,
                "button, [role='button']",
            )
            if self._is_visible(element) and element.is_enabled()
        ]
        chat_candidates: list[WebElement] = []
        for element in candidates:
            text = " ".join(element.text.split()).casefold()
            label = " ".join(
                (
                    str(element.get_attribute("aria-label") or ""),
                    str(element.get_attribute("title") or ""),
                )
            ).casefold()
            if (
                "消息" in label
                or "聊天" in label
                or "message" in label
                or "chat" in label
                or (not text and element.rect.get("width", 0) <= 48)
            ):
                chat_candidates.append(element)
        unique = {element.id: element for element in chat_candidates}
        if len(unique) != 1:
            raise ZiniaoWorkflowError(
                "目标达人行未找到唯一的聊天图标按钮。"
            )
        return next(iter(unique.values()))

    def _drawer_contact(self, handle: str) -> WebElement | bool:
        viewport_width = int(
            self.driver.execute_script("return window.innerWidth || 0;")
        )
        candidates: dict[str, WebElement] = {}
        for element in self._visible_exact_text_elements(handle):
            try:
                if element.rect.get("x", 0) < viewport_width * 0.5:
                    continue
                contact = self.driver.execute_script(
                    """
                    return arguments[0].closest(
                        '[class*="contactCard"], [role="listitem"]'
                    );
                    """,
                    element,
                )
                if (
                    contact is not None
                    and self._is_visible(contact)
                    and contact.rect.get("x", 0) >= viewport_width * 0.5
                ):
                    candidates[contact.id] = contact
            except StaleElementReferenceException:
                continue
        if len(candidates) == 1:
            return next(iter(candidates.values()))
        return False

    def verify_creator_membership(
        self,
        creator: str,
    ) -> WorkflowStepResult:
        """Verify that one exact creator is present in the opened project."""
        if not self._project_name or not self._project_group_id:
            raise ZiniaoWorkflowError("尚未验收定向合作项目达人详情页。")
        _requested, handle = self.normalize_creator_handle(creator)
        row = self._unique_row_with_text(
            handle,
            missing_message=(
                f"项目达人详情未找到唯一目标达人 @{handle}。"
            ),
        )
        row_text = " ".join(
            str(row.get_attribute("innerText") or "").split()
        )
        return WorkflowStepResult(
            step=2,
            action="verify_project_creator_membership",
            success=True,
            evidence={
                "creatorHandle": handle,
                "invitationName": self._project_name,
                "invitationGroupId": self._project_group_id,
                "acceptedCreatorsPageVisible": True,
                "creatorDetailsExpanded": True,
                "projectMembershipVerified": True,
                "rowText": row_text[:300],
                "finalInviteButtonClicked": False,
                "cardSendButtonClicked": False,
                "actionWaitSeconds": list(self._action_wait_seconds),
            },
        )

    def _drawer_conversation_ready(
        self,
        handle: str,
    ) -> dict[str, Any] | bool:
        viewport_width = int(
            self.driver.execute_script("return window.innerWidth || 0;")
        )
        contact = self._drawer_contact(handle)
        if contact is False:
            return False
        try:
            selected = any(
                token == "selected" or token.startswith("selected-")
                for token in str(
                    contact.get_attribute("class") or ""
                ).split()
            )
        except StaleElementReferenceException:
            return False
        composers = [
            composer
            for composer in self._visible_message_composers()
            if composer.rect.get("x", 0) >= viewport_width * 0.7
        ]
        if selected and composers:
            return {
                "drawerSelectedHandleMatched": True,
                "drawerComposerVisible": True,
            }
        return False

    def open_creator_chat(self, creator: str) -> WorkflowStepResult:
        """Open and verify the chat drawer for one accepted creator."""
        if not self._project_name or not self._project_group_id:
            raise ZiniaoWorkflowError("尚未验收定向合作项目达人详情页。")
        _requested, handle = self.normalize_creator_handle(creator)
        row = self._unique_row_with_text(
            handle,
            missing_message=f"已接受达人列表未找到唯一达人 @{handle}。",
        )
        self._click(self._creator_chat_button(row))
        drawer_contact = self._wait(
            lambda _driver: self._drawer_contact(handle),
            message=f"聊天抽屉最近联系人中未找到目标达人 @{handle}。",
        )
        conversation = self._drawer_conversation_ready(handle)
        if conversation is False:
            self._click(drawer_contact)
            conversation = self._wait(
                lambda _driver: self._drawer_conversation_ready(handle),
                message=(
                    f"点击最近联系人后未打开目标达人 @{handle} 的聊天会话。"
                ),
            )
        self._accepted_creator = handle.casefold()
        return WorkflowStepResult(
            step=2,
            action="open_accepted_creator_chat",
            success=True,
            evidence={
                "creatorHandle": handle,
                "chatDrawerVisible": True,
                "recipientVerified": True,
                **conversation,
                "actionWaitSeconds": list(self._action_wait_seconds),
                "cdpClickRecoveryCount": self._cdp_click_recovery_count,
            },
        )

    def send_collaboration_card(
        self,
        creator: str,
        invitation_name: str,
        invitation_group_id: str,
        *,
        confirm_send: bool = False,
    ) -> WorkflowStepResult:
        """Send one exact project card and require strong message evidence."""
        _requested, handle = self.normalize_creator_handle(creator)
        name = str(invitation_name or "").strip()
        group_id = str(invitation_group_id or "").strip()
        if confirm_send is not True:
            raise ZiniaoWorkflowError("必须显式确认发送定向合作卡片。")
        if (
            self._accepted_creator != handle.casefold()
            or self._project_name != name
            or self._project_group_id != group_id
        ):
            raise ZiniaoWorkflowError(
                "发送前置验收失败：项目或聊天达人不是任务目标。"
            )
        card_match = self._wait(
            lambda _driver: self._right_panel_invitation_card(
                name,
                invitation_group_id=group_id,
            )
            or False,
            message=(
                "目标达人聊天窗口未加载名称和 invitationGroupId "
                "均精确匹配的唯一定向合作卡片。"
            ),
        )
        card, actual_group_id, invitation_id = card_match
        if actual_group_id != group_id:
            raise ZiniaoWorkflowError(
                "聊天卡片的 invitationGroupId 与任务项目不一致。"
            )
        existing = self._chat_plan_card_evidence(name, None)
        if (
            existing["targetPlanPendingCount"]
            or existing["targetPlanFailedCount"]
        ):
            raise ZiniaoWorkflowError(
                "目标合作卡片存在发送中或失败消息，已阻止重复点击。"
            )
        send_button = self._collaboration_card_send_button(card)
        baseline_verified_keys = set(
            existing["verifiedPlanCardMessageKeys"]
        )
        self._click(send_button)

        def delivered(_driver: WebDriver) -> dict[str, Any] | bool:
            evidence = self._chat_plan_card_evidence(name, None)
            if evidence["targetPlanFailedCount"]:
                raise ZiniaoWorkflowError(
                    "合作卡片消息进入失败状态。"
                )
            new_cards = [
                row
                for row in evidence["verifiedPlanCards"]
                if row["messageKey"] not in baseline_verified_keys
            ]
            if len(new_cards) == 1:
                new_card = new_cards[0]
                actual_invitation_id = str(
                    new_card.get("invitationId") or ""
                )
                if not actual_invitation_id.isdigit():
                    return False
                return {
                    **evidence,
                    "exactPlanCardCount": 1,
                    "planCardServerIds": [new_card["serverId"]],
                    "planCardMessageKeys": [new_card["messageKey"]],
                    "verifiedPlanCardMessageKeys": [
                        new_card["messageKey"]
                    ],
                    "targetPlanInvitationIds": [
                        actual_invitation_id
                    ],
                    "targetPlanFlightStatus": new_card[
                        "flightStatus"
                    ],
                    "targetPlanCreateTimes": [
                        new_card["createTime"]
                    ],
                    "invitationId": actual_invitation_id,
                }
            return False

        delivery = self._wait(
            delivered,
            message="点击发送后未取得目标合作计划卡片的成功消息证据。",
            timeout_seconds=min(15, self.timeout_seconds),
        )
        return WorkflowStepResult(
            step=3,
            action="send_accepted_creator_collaboration_card",
            success=True,
            evidence={
                **delivery,
                "creatorHandle": handle,
                "invitationName": name,
                "invitationGroupId": group_id,
                "invitationId": delivery["invitationId"],
                "alreadySent": False,
                "cardSendButtonClicked": True,
                "cardSent": True,
                "finalSendVerified": True,
                "actionWaitSeconds": list(self._action_wait_seconds),
                "cdpClickRecoveryCount": self._cdp_click_recovery_count,
            },
        )
