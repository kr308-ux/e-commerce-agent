from __future__ import annotations

import unittest
from unittest.mock import Mock, call, patch

from selenium.webdriver.common.keys import Keys

from ziniao_automation.keyboard import replace_element_text


class KeyboardTests(unittest.TestCase):
    def test_replace_text_uses_platform_modifier_and_exact_key_order(
        self,
    ) -> None:
        cases = (
            ("Windows", Keys.CONTROL, Keys.COMMAND),
            ("Linux", Keys.CONTROL, Keys.COMMAND),
            ("Darwin", Keys.COMMAND, Keys.CONTROL),
        )
        for system_name, expected_modifier, wrong_modifier in cases:
            with self.subTest(system_name=system_name):
                element = Mock()

                replace_element_text(
                    element,
                    "next_creator",
                    system_name=system_name,
                )

                self.assertEqual(
                    element.send_keys.call_args_list,
                    [
                        call(expected_modifier, "a"),
                        call(Keys.BACKSPACE),
                        call("next_creator"),
                    ],
                )
                self.assertNotIn(
                    call(wrong_modifier, "a"),
                    element.send_keys.call_args_list,
                )

    def test_replace_text_detects_the_current_platform_by_default(self) -> None:
        element = Mock()
        with patch(
            "ziniao_automation.keyboard.platform.system",
            return_value="Windows",
        ):
            replace_element_text(element, "next_creator")

        self.assertEqual(
            element.send_keys.call_args_list,
            [
                call(Keys.CONTROL, "a"),
                call(Keys.BACKSPACE),
                call("next_creator"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
