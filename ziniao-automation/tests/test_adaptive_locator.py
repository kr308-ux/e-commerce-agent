from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from selenium.webdriver.common.by import By

from ziniao_automation.adaptive_locator import (
    AdaptiveLocatorEngine,
    AdaptiveLocatorStore,
    CandidateSelection,
    ElementCandidate,
    collect_ax_candidates,
    page_key,
    synthesize_recipes,
    target_key,
)


class AdaptiveLocatorStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = AdaptiveLocatorStore(
            Path(self.directory.name) / "locators.sqlite3"
        )

    def test_store_keeps_at_most_three_ranked_recipes(self) -> None:
        for index in range(4):
            self.store.learn(
                page="seller.test/connection/creator",
                target="target",
                strategy="css",
                value=f"button[data-index=\"{index}\"]",
                kind="stable_attribute",
                source="dom_candidate",
                confidence=0.8 + index / 100,
            )

        recipes = self.store.ranked(
            "seller.test/connection/creator",
            "target",
        )

        self.assertEqual(len(recipes), 3)
        self.assertEqual(len({recipe.value for recipe in recipes}), 3)

    def test_recipe_is_disabled_after_three_structural_failures(self) -> None:
        recipe = self.store.learn(
            page="seller.test/page",
            target="target",
            strategy="css",
            value="button[data-e2e=\"send\"]",
            kind="stable_attribute",
            source="ax_candidate",
            confidence=0.95,
        )
        self.assertIsNotNone(recipe)
        assert recipe is not None

        for _attempt in range(3):
            self.store.record_structural_failure(recipe.locator_id)

        self.assertEqual(
            self.store.ranked("seller.test/page", "target"),
            [],
        )

    def test_page_and_target_keys_are_stable(self) -> None:
        self.assertEqual(
            page_key(
                "https://Seller.Test/connection//creator/?ignored=1"
            ),
            "seller.test/connection/creator",
        )
        self.assertEqual(
            target_key("  发送   按钮 "),
            target_key("发送 按钮"),
        )

    def test_persisted_lookup_tries_next_recipe_after_structural_miss(
        self,
    ) -> None:
        page = "seller.test/chat"
        target = target_key("发送按钮")
        broken = self.store.learn(
            page=page,
            target=target,
            strategy="css",
            value="button[data-e2e=\"old-send\"]",
            kind="stable_attribute",
            source="ax_candidate",
            confidence=1.0,
        )
        working = self.store.learn(
            page=page,
            target=target,
            strategy="css",
            value="button.send",
            kind="contextual",
            source="dom_candidate",
            confidence=0.7,
        )
        self.assertIsNotNone(broken)
        self.assertIsNotNone(working)
        element = Mock()
        element.id = "send"
        element.is_displayed.return_value = True
        element.is_enabled.return_value = True
        driver = Mock()
        driver.current_url = "https://seller.test/chat"
        driver.find_elements.side_effect = (
            lambda _by, value: [element]
            if value == "button.send"
            else []
        )
        engine = AdaptiveLocatorEngine(self.store)

        result = engine.locate_persisted(
            driver,
            purpose="发送按钮",
        )

        self.assertIs(result.element, element)
        self.assertEqual(
            result.event["locatorId"],
            working.locator_id if working is not None else "",
        )
        self.assertGreaterEqual(
            len(result.event["attemptedLocatorIds"]),
            2,
        )

    def test_polling_miss_is_not_counted_until_wait_timeout(
        self,
    ) -> None:
        recipe = self.store.learn(
            page="seller.test/chat",
            target=target_key("发送按钮"),
            strategy="css",
            value="button.send",
            kind="stable_attribute",
            source="ax_candidate",
            confidence=0.95,
        )
        self.assertIsNotNone(recipe)
        driver = Mock()
        driver.current_url = "https://seller.test/chat"
        driver.find_elements.return_value = []
        engine = AdaptiveLocatorEngine(self.store)
        attempt = engine.begin_persisted_attempt(
            driver,
            purpose="发送按钮",
        )

        for _poll in range(3):
            result = engine.probe_persisted(driver, attempt)
            self.assertIsNone(result.element)

        before_timeout = self.store.ranked(
            "seller.test/chat",
            target_key("发送按钮"),
        )[0]
        self.assertEqual(before_timeout.structural_failure_count, 0)

        event = engine.finalize_persisted_timeout(attempt)
        engine.finalize_persisted_timeout(attempt)
        after_timeout = self.store.ranked(
            "seller.test/chat",
            target_key("发送按钮"),
        )[0]

        self.assertEqual(event["structuralFailureCount"], 1)
        self.assertEqual(after_timeout.structural_failure_count, 1)

    def test_recipe_appearing_during_wait_is_not_penalized(self) -> None:
        self.store.learn(
            page="seller.test/chat",
            target=target_key("发送按钮"),
            strategy="css",
            value="button.send",
            kind="stable_attribute",
            source="ax_candidate",
            confidence=0.95,
        )
        element = Mock()
        element.id = "send"
        element.is_displayed.return_value = True
        element.is_enabled.return_value = True
        driver = Mock()
        driver.current_url = "https://seller.test/chat"
        driver.find_elements.side_effect = [[], [element]]
        engine = AdaptiveLocatorEngine(self.store)
        attempt = engine.begin_persisted_attempt(
            driver,
            purpose="发送按钮",
        )

        first = engine.probe_persisted(driver, attempt)
        second = engine.probe_persisted(driver, attempt)
        event = engine.finalize_persisted_timeout(attempt)
        saved = self.store.ranked(
            "seller.test/chat",
            target_key("发送按钮"),
        )[0]

        self.assertIsNone(first.element)
        self.assertIs(second.element, element)
        self.assertEqual(event["structuralFailureCount"], 0)
        self.assertEqual(saved.structural_failure_count, 0)


class AdaptiveLocatorCandidateTests(unittest.TestCase):
    def test_ax_collection_locally_filters_and_assigns_snapshot_ids(
        self,
    ) -> None:
        driver = Mock()

        def cdp(command: str, _params: dict[str, object]) -> dict:
            if command == "Page.getFrameTree":
                return {"frameTree": {"frame": {"id": "main"}}}
            if command == "Accessibility.getFullAXTree":
                return {
                    "nodes": [
                        {
                            "nodeId": "1",
                            "backendDOMNodeId": 101,
                            "role": {"value": "button"},
                            "name": {"value": "Send"},
                            "properties": [],
                        },
                        {
                            "nodeId": "2",
                            "backendDOMNodeId": 102,
                            "role": {"value": "generic"},
                            "name": {"value": "Unrelated content"},
                            "properties": [],
                        },
                    ]
                }
            raise AssertionError(command)

        driver.execute_cdp_cmd.side_effect = cdp

        snapshot_id, candidates = collect_ax_candidates(
            driver,
            purpose="聊天发送按钮",
        )

        self.assertTrue(snapshot_id.startswith("ax-"))
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].candidate_id, "ax-001")
        self.assertEqual(candidates[0].backend_dom_node_id, 101)
        self.assertNotIn(
            "backend_dom_node_id",
            candidates[0].to_model_dict(),
        )

    def test_engine_falls_back_to_dom_when_ax_selection_stops(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        engine = AdaptiveLocatorEngine(
            AdaptiveLocatorStore(
                Path(directory.name) / "locators.sqlite3"
            )
        )
        driver = Mock()
        driver.current_url = "https://seller.test/connection/creator"
        element = Mock()
        ax_candidate = ElementCandidate(
            candidate_id="ax-001",
            source="ax",
            role="button",
            name="Ambiguous",
            backend_dom_node_id=1,
        )
        dom_candidate = ElementCandidate(
            candidate_id="dom-001",
            source="dom",
            role="button",
            name="Send",
            immediate_strategy="css",
            immediate_value="button.send",
        )
        selections: list[str] = []

        def select(
            _snapshot_id: str,
            _purpose: str,
            _page: str,
            candidates: list[ElementCandidate],
        ) -> CandidateSelection:
            selections.append(candidates[0].source)
            if candidates[0].source == "ax":
                return CandidateSelection(decision="stop")
            return CandidateSelection(
                decision="select",
                candidate_id="dom-001",
                confidence=0.96,
            )

        with (
            patch(
                "ziniao_automation.adaptive_locator.collect_ax_candidates",
                return_value=("ax-snapshot", [ax_candidate]),
            ),
            patch(
                "ziniao_automation.adaptive_locator.collect_dom_candidates",
                return_value=("dom-snapshot", [dom_candidate]),
            ),
            patch(
                "ziniao_automation.adaptive_locator.resolve_candidate",
                return_value=element,
            ),
            patch(
                "ziniao_automation.adaptive_locator.synthesize_recipes",
                return_value=[],
            ),
        ):
            result = engine.locate(
                driver,
                purpose="发送按钮",
                attempted_selectors=(),
                dom_snapshot=lambda: {"elements": []},
                select_candidate=select,
            )

        self.assertIs(result.element, element)
        self.assertEqual(selections, ["ax", "dom"])
        self.assertEqual(result.event["candidateSource"], "dom")
        self.assertEqual(len(result.event["attempts"]), 2)

    def test_local_recipe_synthesis_requires_unique_same_element(
        self,
    ) -> None:
        driver = Mock()
        element = Mock()
        element.id = "send-element"
        driver.execute_script.return_value = {
            "tag": "button",
            "id": "",
            "dataE2e": "send-message",
            "href": "",
            "ariaLabel": "Send",
            "name": "",
            "type": "",
            "placeholder": "",
            "role": "button",
            "text": "Send",
            "iconClasses": "arco-icon arco-icon-send",
        }
        driver.find_elements.return_value = [element]

        recipes = synthesize_recipes(
            driver,
            element,
            page="seller.test/chat",
            target="target",
            source="ax_candidate",
            confidence=0.94,
        )

        self.assertLessEqual(len(recipes), 3)
        self.assertTrue(
            any(
                recipe["value"]
                == 'button[data-e2e="send-message"]'
                for recipe in recipes
            )
        )
        for recipe in recipes:
            expected_by = (
                By.CSS_SELECTOR
                if recipe["strategy"] == "css"
                else By.XPATH
            )
            driver.find_elements.assert_any_call(
                expected_by,
                recipe["value"],
            )


if __name__ == "__main__":
    unittest.main()
