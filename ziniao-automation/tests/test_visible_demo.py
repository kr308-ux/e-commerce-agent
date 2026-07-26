from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from ziniao_automation.actions.visible_demo import run_visible_connection_demo


class VisibleDemoTests(unittest.TestCase):
    @patch("ziniao_automation.actions.visible_demo.time.sleep")
    def test_demo_is_visible_and_reversible(self, sleep: Mock) -> None:
        driver = Mock()
        driver.execute_script.return_value = 120
        run_visible_connection_demo(driver, duration_seconds=5)
        driver.maximize_window.assert_called_once_with()
        self.assertGreaterEqual(driver.execute_script.call_count, 5)
        sleep.assert_any_call(5)

    def test_demo_duration_is_bounded(self) -> None:
        with self.assertRaises(ValueError):
            run_visible_connection_demo(Mock(), duration_seconds=301)

    @patch("ziniao_automation.actions.visible_demo.time.sleep")
    def test_cleanup_does_not_mask_closed_driver(self, sleep: Mock) -> None:
        driver = Mock()
        driver.execute_script.side_effect = [
            0,
            None,
            None,
            None,
            RuntimeError("chromedriver already closed"),
        ]
        run_visible_connection_demo(driver, duration_seconds=1)
        sleep.assert_any_call(1)


if __name__ == "__main__":
    unittest.main()
