from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from api.settings import (SettingsError, apply_full_settings, read_basic_settings,
                          save_basic_settings, save_full_settings)
from config import DEFAULT_CONFIG, MONITORING_CONFIG


class WorkbenchSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "user-config.json"
        self.original_default = deepcopy(DEFAULT_CONFIG)
        DEFAULT_CONFIG["collection"]["encrypt_expect_id"] = ["test-id"]
        self.saved_default = deepcopy(DEFAULT_CONFIG)
        self.saved_monitoring = deepcopy(MONITORING_CONFIG)
        self.path_patch = patch("api.settings.USER_CONFIG_PATH", self.path)
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        DEFAULT_CONFIG.clear()
        DEFAULT_CONFIG.update(self.original_default)
        MONITORING_CONFIG.clear()
        MONITORING_CONFIG.update(self.saved_monitoring)
        self.temp.cleanup()

    def test_save_updates_current_values_and_persists_override(self):
        result = save_basic_settings({"monitoring": {
            "message_limit": 8, "followup_days": 5,
        }})
        self.assertEqual(result["settings"]["monitoring"]["followup_days"], 5)
        self.assertEqual(result["settings"]["monitoring"]["message_limit"], 8)
        self.assertEqual(read_basic_settings()["settings"]["monitoring"]["followup_days"], 5)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["monitoring"], {
            "message_limit": 8, "followup_days": 5,
        })

    def test_invalid_collection_settings_do_not_persist(self):
        with self.assertRaises(SettingsError):
            save_basic_settings({"collection": {"mode": "search", "keywords": [],
                                                "encrypt_expect_id": []}})
        self.assertFalse(self.path.exists())
        self.assertEqual(DEFAULT_CONFIG["collection"], self.saved_default["collection"])

    def test_basic_settings_disabling_scoring_preserves_greeting_switch(self):
        DEFAULT_CONFIG["ai"].update(use_ai_score=True, use_ai_greeting=True)
        result = save_basic_settings({"ai": {"use_ai_score": False}})
        self.assertFalse(result["settings"]["ai"]["use_ai_score"])
        self.assertTrue(result["settings"]["ai"]["use_ai_greeting"])
        self.assertEqual(json.loads(self.path.read_text())["ai"], {"use_ai_score": False})

    def test_full_settings_can_save_and_apply_greeting_only(self):
        DEFAULT_CONFIG["ai"].update(use_ai_score=True, use_ai_greeting=False)
        result = save_full_settings({"ai": {"use_ai_score": False, "use_ai_greeting": True}})
        self.assertTrue(result["pending_apply"])
        self.assertFalse(result["settings"]["ai"]["use_ai_score"])
        self.assertTrue(result["settings"]["ai"]["use_ai_greeting"])
        result = apply_full_settings()
        self.assertFalse(result["pending_apply"])
        self.assertFalse(DEFAULT_CONFIG["ai"]["use_ai_score"])
        self.assertTrue(DEFAULT_CONFIG["ai"]["use_ai_greeting"])


if __name__ == "__main__":
    unittest.main()
