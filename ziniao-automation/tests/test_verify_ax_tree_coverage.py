from __future__ import annotations

import unittest

from ziniao_automation.scripts.verify_ax_tree_coverage import (
    AXNode,
    PAGE_SPECS,
    PageSnapshot,
    _aggregate_marking,
    _flatten_frame_tree,
    _page_verdict,
    _target_result,
    _url_matches,
    build_summary_reports,
    find_in_ax,
)


class VerifyAXTreeCoverageTests(unittest.TestCase):
    def test_page_specs_cover_five_shapes_and_twenty_one_targets(self) -> None:
        self.assertEqual(len(PAGE_SPECS), 5)
        target_ids = [
            target.target_id
            for page in PAGE_SPECS
            for target in page.known_targets
        ]
        self.assertEqual(len(target_ids), 21)
        self.assertEqual(
            target_ids,
            [f"T-{index:02d}" for index in range(1, 22)],
        )

    def test_flatten_frame_tree_preserves_parent_first_order(self) -> None:
        frame_ids = _flatten_frame_tree(
            {
                "frame": {"id": "root"},
                "childFrames": [
                    {
                        "frame": {"id": "child-1"},
                        "childFrames": [
                            {"frame": {"id": "grandchild"}},
                        ],
                    },
                    {"frame": {"id": "child-2"}},
                ],
            }
        )
        self.assertEqual(
            frame_ids,
            ["root", "child-1", "grandchild", "child-2"],
        )

    def test_url_markers_are_alternatives_and_exclusions_still_apply(
        self,
    ) -> None:
        store_home = PAGE_SPECS[0]
        self.assertTrue(
            _url_matches(
                store_home,
                "https://seller.us.tiktokshopglobalselling.com/homepage",
            )
        )
        self.assertFalse(
            _url_matches(
                store_home,
                "https://seller.us.tiktokshopglobalselling.com/login",
            )
        )
        self.assertFalse(
            _url_matches(
                store_home,
                "https://affiliate.tiktokshopglobalselling.com/"
                "platform/homepage",
            )
        )
        chat_tab = PAGE_SPECS[3]
        self.assertTrue(
            _url_matches(
                chat_tab,
                "https://affiliate.us.tiktokshop.com/seller/im",
            )
        )

    def test_find_in_ax_checks_role_name_context_and_state(self) -> None:
        target = PAGE_SPECS[1].known_targets[0]
        unchecked = AXNode(
            node_id="1",
            frame_id="root",
            backend_dom_node_id=10,
            role="switch",
            name="AI 搜索",
            ignored=False,
            properties={"checked": False},
        )
        checked = AXNode(
            node_id="2",
            frame_id="root",
            backend_dom_node_id=11,
            role="switch",
            name="AI 搜索",
            ignored=False,
            properties={"checked": "true"},
        )
        self.assertIs(find_in_ax([unchecked, checked], target), checked)

    def test_final_invite_requires_exact_accessible_name(self) -> None:
        target = PAGE_SPECS[4].known_targets[-1]
        other_invitation = AXNode(
            node_id="other",
            frame_id="root",
            backend_dom_node_id=12,
            role="button",
            name="+ 发送其他邀请开展合作",
            ignored=False,
        )
        final_invite = AXNode(
            node_id="final",
            frame_id="root",
            backend_dom_node_id=13,
            role="button",
            name="邀请",
            ignored=False,
        )
        self.assertIs(
            find_in_ax([other_invitation, final_invite], target),
            final_invite,
        )

    def test_send_button_does_not_match_other_invitation_action(self) -> None:
        target = PAGE_SPECS[3].known_targets[1]
        other_invitation = AXNode(
            node_id="other",
            frame_id="root",
            backend_dom_node_id=14,
            role="button",
            name="+ 发送其他邀请开展合作",
            ignored=False,
        )
        send = AXNode(
            node_id="send",
            frame_id="root",
            backend_dom_node_id=15,
            role="button",
            name="发送",
            ignored=False,
        )
        self.assertIs(
            find_in_ax([other_invitation, send], target),
            send,
        )

    def test_dynamic_target_can_hit_an_earlier_transient_phase(self) -> None:
        target = PAGE_SPECS[1].known_targets[2]
        candidate = AXNode(
            node_id="candidate",
            frame_id="root",
            backend_dom_node_id=22,
            role="menuitem",
            name="@creator_one",
            ignored=False,
        )
        snapshots = [
            self._snapshot("initial"),
            self._snapshot(
                "dynamic_candidate",
                nodes=[candidate],
                dom_target={"tag": "div", "role": "menuitem"},
                target_id="T-05",
            ),
            self._snapshot("dynamic_result"),
        ]
        result = _target_result(
            target,
            snapshots,
            creator="@creator_one",
            message="",
        )
        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["ax_phase"], "dynamic_candidate")
        self.assertEqual(result["dom_phase"], "dynamic_candidate")

    def test_page_verdict_uses_coverage_and_static_miss_thresholds(self) -> None:
        self.assertEqual(
            _page_verdict(
                coverage_rate=0.8,
                static_misses=0,
                visible_count=15,
                minimum_visible=15,
            ),
            "pass",
        )
        self.assertEqual(
            _page_verdict(
                coverage_rate=0.79,
                static_misses=0,
                visible_count=15,
                minimum_visible=15,
            ),
            "warn",
        )
        self.assertEqual(
            _page_verdict(
                coverage_rate=0.9,
                static_misses=3,
                visible_count=15,
                minimum_visible=15,
            ),
            "fail",
        )

    def test_summary_excludes_not_testable_targets_from_hit_rate(self) -> None:
        reports = [
            {
                "description": "查找达人页",
                "status": "tested",
                "dom_visible": 10,
                "covered": 8,
                "coverage_rate": 0.8,
                "verdict": "pass",
                "targets": {
                    "T-03": {"status": "hit", "in_ax": True},
                    "T-05": {
                        "status": "not_testable",
                        "in_ax": False,
                    },
                },
                "phases": [],
            }
        ]
        summary = build_summary_reports(reports)
        self.assertEqual(summary["total_targets"], 2)
        self.assertEqual(summary["evaluated_targets"], 1)
        self.assertEqual(summary["not_testable_targets"], 1)
        self.assertEqual(summary["ax_hit_rate"], 1.0)
        self.assertEqual(summary["overall_coverage"], 0.8)

    def test_marking_aggregation_counts_every_phase(self) -> None:
        reports = [
            {
                "phases": [
                    {
                        "marking": {
                            "attempted": 10,
                            "marked": 9,
                            "failed": 1,
                        }
                    },
                    {
                        "marking": {
                            "attempted": 5,
                            "marked": 5,
                            "failed": 0,
                        }
                    },
                ]
            }
        ]
        self.assertEqual(
            _aggregate_marking(reports),
            {
                "attempted": 15,
                "total_marked": 14,
                "failed": 1,
                "success_rate": 0.9333,
            },
        )

    @staticmethod
    def _snapshot(
        phase: str,
        *,
        nodes: list[AXNode] | None = None,
        dom_target: dict[str, object] | None = None,
        target_id: str = "",
    ) -> PageSnapshot:
        targets = {target_id: dom_target} if target_id else {}
        return PageSnapshot(
            phase=phase,
            url="https://example.test",
            frame_count=1,
            frame_errors=[],
            ax_nodes=nodes or [],
            dom_elements=[],
            dom_targets=targets,
            marking={
                "token": "token",
                "attempted": 0,
                "marked": 0,
                "failed": 0,
                "success_rate": 0,
            },
        )


if __name__ == "__main__":
    unittest.main()
