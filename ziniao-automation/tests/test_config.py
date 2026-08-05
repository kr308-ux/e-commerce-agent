from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from ziniao_automation.config import ZiniaoCredentials, ZiniaoSettings
from ziniao_automation.errors import ZiniaoConfigurationError


class ZiniaoConfigurationTests(unittest.TestCase):
    def test_credentials_repr_does_not_include_password(self) -> None:
        credentials = ZiniaoCredentials("company", "username", "private-password")
        self.assertNotIn("private-password", repr(credentials))

    def test_missing_credentials_lists_environment_variables(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ZiniaoConfigurationError) as captured:
                ZiniaoCredentials.from_env()
        self.assertIn("ZINIAO_COMPANY", str(captured.exception))
        self.assertIn("ZINIAO_PASSWORD", str(captured.exception))

    def test_official_minimum_request_timeout_is_enforced(self) -> None:
        settings = ZiniaoSettings(
            credentials=ZiniaoCredentials("company", "username", "password"),
            client_path=Path("/Applications/ziniao.app"),
            request_timeout_seconds=119,
        )
        with self.assertRaises(ZiniaoConfigurationError):
            settings.validate()

    def test_blank_client_path_uses_platform_default(self) -> None:
        environment = {
            "ZINIAO_COMPANY": "company",
            "ZINIAO_USERNAME": "username",
            "ZINIAO_PASSWORD": "password",
            "ZINIAO_CLIENT_PATH": "",
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = ZiniaoSettings.from_env()
        self.assertNotEqual(str(settings.client_path), ".")

    def test_privacy_mode_can_be_enabled_from_environment(self) -> None:
        environment = {
            "ZINIAO_COMPANY": "company",
            "ZINIAO_USERNAME": "username",
            "ZINIAO_PASSWORD": "password",
            "ZINIAO_PRIVACY_MODE": "true",
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = ZiniaoSettings.from_env()
        self.assertTrue(settings.privacy_mode)


if __name__ == "__main__":
    unittest.main()
