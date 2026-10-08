"""API keys are stored separately from public settings and activated explicitly."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from ai import credentials
from ai.client import call_model
from api.settings import apply_full_settings, read_full_settings, save_full_settings
from config import DEFAULT_CONFIG


class ApiKeySettingsTests(unittest.TestCase):
    def test_save_redacts_key_until_apply(self):
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "user-config.json"
            key_path = Path(directory) / "ai-api-key"
            with patch("api.settings.USER_CONFIG_PATH", config_path), \
                    patch.object(credentials, "API_KEY_PATH", key_path), \
                    patch.object(credentials, "_active_key", ""), \
                    patch.dict(DEFAULT_CONFIG["collection"], {"encrypt_expect_id": ["test-id"]}), \
                    patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
                saved = save_full_settings({"ai": {"api_key": "sk-local-test"}})
                self.assertTrue(saved["api_key_configured"])
                self.assertTrue(saved["pending_apply"])
                self.assertNotIn("sk-local-test", json.dumps(saved))
                self.assertNotIn("sk-local-test", config_path.read_text(encoding="utf-8"))
                self.assertEqual(key_path.read_text(encoding="utf-8"), "sk-local-test")
                self.assertEqual(key_path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(credentials.current_api_key("DEEPSEEK_API_KEY"), "")

                applied = apply_full_settings()
                self.assertFalse(applied["pending_apply"])
                self.assertEqual(credentials.current_api_key("DEEPSEEK_API_KEY"), "sk-local-test")
                self.assertNotIn("sk-local-test", json.dumps(read_full_settings()))
                response = Mock(status_code=200)
                response.json.return_value = {"choices": [{"message": {"content": "完成"}}]}
                with patch("ai.client.httpx.post", return_value=response) as request:
                    self.assertEqual(call_model(DEFAULT_CONFIG, "系统要求", "岗位信息"), "完成")
                self.assertEqual(request.call_args.kwargs["headers"]["Authorization"],
                                 "Bearer sk-local-test")


if __name__ == "__main__":
    unittest.main()
