"""Selected search cities must persist and produce BOSS city codes."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from api.settings import SettingsError, apply_full_settings, read_full_settings, save_full_settings
from collector.platforms.boss import build_boss_request
from config import CITY_CODES, DEFAULT_CONFIG
from run import validate_run_config


class CitySelectionTests(unittest.TestCase):
    def setUp(self):
        self.saved = deepcopy(DEFAULT_CONFIG)
        DEFAULT_CONFIG["collection"]["encrypt_expect_id"] = ["test-id"]
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "user-config.json"
        self.config_patch = patch("api.settings.USER_CONFIG_PATH", self.path)
        self.config_patch.start()

    def tearDown(self):
        self.config_patch.stop()
        DEFAULT_CONFIG.clear()
        DEFAULT_CONFIG.update(self.saved)
        self.temp.cleanup()

    def test_selected_cities_persist_and_map_to_codes(self):
        result = save_full_settings({"collection": {
            "mode": "search", "encrypt_expect_id": [], "keywords": ["前端"],
            "cities": ["北京", "上海", "北京"],
        }})
        self.assertEqual(result["settings"]["collection"]["cities"], ["北京", "上海"])
        self.assertEqual(read_full_settings()["city_options"], list(CITY_CODES))
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))
                         ["collection"]["cities"], ["北京", "上海"])
        self.assertTrue(result["pending_apply"])
        self.assertNotEqual(DEFAULT_CONFIG["collection"]["cities"], ["北京", "上海"])
        self.assertFalse(apply_full_settings()["pending_apply"])
        request = build_boss_request(DEFAULT_CONFIG)
        self.assertEqual(request.cities, ["北京", "上海"])
        self.assertEqual(request.city_codes, {"北京": CITY_CODES["北京"],
                                              "上海": CITY_CODES["上海"]})

    def test_unknown_city_is_rejected(self):
        with self.assertRaisesRegex(SettingsError, "未收录城市"):
            save_full_settings({"collection": {"cities": ["不存在的城市"]}})
        self.assertFalse(self.path.exists())

    def test_legacy_custom_city_code_remains_supported(self):
        config = deepcopy(DEFAULT_CONFIG)
        config["collection"].update(city="自定义城市", city_code="123456789")
        request = build_boss_request(validate_run_config(config))
        self.assertEqual(request.cities, ["自定义城市"])
        self.assertEqual(request.city_codes, {"自定义城市": "123456789"})


if __name__ == "__main__":
    unittest.main()
