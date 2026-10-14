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

    def test_followup_valid_days_can_be_saved_with_bounds(self):
        result = save_full_settings({"monitoring": {"followup_valid_days": 14}})
        self.assertEqual(result["settings"]["monitoring"]["followup_valid_days"], 14)
        self.assertEqual(json.loads(self.path.read_text())["monitoring"]["followup_valid_days"], 14)
        for invalid in (0, 366):
            with self.subTest(invalid=invalid), self.assertRaises(SettingsError):
                save_full_settings({"monitoring": {"followup_valid_days": invalid}})

    def test_exchange_and_new_greeting_activity_days_are_independent(self):
        result = save_full_settings({"monitoring": {
            "exchanged_active_days": 6, "new_greetings_active_days": 12,
        }})
        self.assertEqual(result["settings"]["monitoring"]["exchanged_active_days"], 6)
        self.assertEqual(result["settings"]["monitoring"]["new_greetings_active_days"], 12)
        for field in ("exchanged_active_days", "new_greetings_active_days"):
            for invalid in (0, 31):
                with self.subTest(field=field, invalid=invalid), self.assertRaises(SettingsError):
                    save_full_settings({"monitoring": {field: invalid}})

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

    def test_silent_background_is_opt_in_and_persists(self):
        self.assertFalse(DEFAULT_CONFIG["browser"]["silent_background"])
        with self.assertRaises(SettingsError):
            save_full_settings({"browser": {"silent_background": "true"}})
        result = save_full_settings({"browser": {"silent_background": True}})
        self.assertTrue(result["settings"]["browser"]["silent_background"])
        self.assertTrue(result["pending_apply"])
        self.assertTrue(json.loads(self.path.read_text())["browser"]["silent_background"])
        apply_full_settings()
        self.assertTrue(DEFAULT_CONFIG["browser"]["silent_background"])

    def test_basic_settings_can_toggle_silent_background(self):
        result = save_basic_settings({"browser": {"silent_background": True}})
        self.assertTrue(result["settings"]["browser"]["silent_background"])
        self.assertTrue(DEFAULT_CONFIG["browser"]["silent_background"])


if __name__ == "__main__":
    unittest.main()
