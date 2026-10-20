"""Selected search cities must persist and produce BOSS city codes."""

from copy import deepcopy
import json
from pathlib import Path
import runpy
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from api.settings import (SettingsError, apply_full_settings, read_basic_settings,
                          read_full_settings, save_basic_settings, save_full_settings)
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

    def test_basic_settings_use_the_same_cities_configuration(self):
        self.path.write_text(json.dumps({"collection": {
            "city": "北京", "city_code": "101010100",
        }}), encoding="utf-8")
        result = save_basic_settings({"collection": {
            "mode": "search", "encrypt_expect_id": [], "keywords": ["前端"],
            "cities": ["上海", "上海"],
        }})
        self.assertEqual(result["settings"]["collection"]["cities"], ["上海"])
        self.assertEqual(read_basic_settings()["settings"]["collection"]["cities"], ["上海"])
        saved = json.loads(self.path.read_text())["collection"]
        self.assertNotIn("city", saved)
        self.assertNotIn("city_code", saved)
        self.assertEqual(build_boss_request(DEFAULT_CONFIG).city_codes, {
            "上海": CITY_CODES["上海"],
        })

    def test_only_cities_are_exposed_and_removed_fields_are_rejected(self):
        settings = read_full_settings()["settings"]["collection"]
        self.assertIn("cities", settings)
        for field in ("city", "city_code"):
            self.assertNotIn(field, settings)
            with self.subTest(field=field), self.assertRaisesRegex(SettingsError, "不支持的配置项"):
                save_full_settings({"collection": {field: ""}})
            config = deepcopy(DEFAULT_CONFIG)
            config["collection"][field] = ""
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "已移除"):
                validate_run_config(config)
        self.assertFalse(self.path.exists())

    def test_empty_cities_leave_the_boss_session_city_unchanged(self):
        config = deepcopy(DEFAULT_CONFIG)
        config["collection"].update(mode="search", encrypt_expect_id=[], keywords=["前端"], cities=[])
        request = build_boss_request(validate_run_config(config))
        self.assertEqual(request.cities, [])
        self.assertEqual(request.city_codes, {})

    def test_search_cities_do_not_override_recommendation_scope(self):
        for mode in ("recommend", "search", "both"):
            with self.subTest(mode=mode):
                config = deepcopy(DEFAULT_CONFIG)
                config["collection"].update(
                    mode=mode, cities=["北京", "上海", "北京"],
                    encrypt_expect_id=[] if mode == "search" else ["test-id"],
                    keywords=[] if mode == "recommend" else ["前端"],
                )
                request = build_boss_request(validate_run_config(config))
                self.assertEqual(request.cities, [] if mode == "recommend" else ["北京", "上海"])
                self.assertEqual(request.city_codes, {} if mode == "recommend" else {
                    "北京": CITY_CODES["北京"], "上海": CITY_CODES["上海"],
                })

    def test_saved_single_city_is_migrated_and_can_be_replaced(self):
        self.path.write_text(json.dumps({"collection": {
            "mode": "search", "encrypt_expect_id": [], "keywords": ["前端"],
            "cities": ["北京"], "city": "上海", "city_code": "101020100",
            "max_pages": 2,
        }}), encoding="utf-8")
        settings = read_full_settings()["settings"]["collection"]
        self.assertEqual(settings["cities"], ["北京", "上海"])
        self.assertNotIn("city", settings)
        self.assertNotIn("city_code", settings)

        save_full_settings({"collection": {"cities": ["深圳"]}})
        saved = json.loads(self.path.read_text())["collection"]
        self.assertEqual(saved["cities"], ["深圳"])
        self.assertEqual(saved["max_pages"], 2)
        self.assertNotIn("city", saved)
        self.assertNotIn("city_code", saved)
        apply_full_settings()
        self.assertEqual(build_boss_request(DEFAULT_CONFIG).city_codes, {
            "深圳": CITY_CODES["深圳"],
        })

    def test_startup_migrates_saved_single_city_without_duplicate_fields(self):
        isolated = Path(self.temp.name) / "startup"
        (isolated / ".state").mkdir(parents=True)
        shutil.copyfile(Path(__file__).resolve().parents[1] / "config.py", isolated / "config.py")
        (isolated / ".state" / "user-config.json").write_text(json.dumps({
            "collection": {"cities": ["北京"], "city": "北京", "city_code": "101010100"},
        }), encoding="utf-8")
        collection = runpy.run_path(str(isolated / "config.py"))["DEFAULT_CONFIG"]["collection"]
        self.assertEqual(collection["cities"], ["北京"])
        self.assertNotIn("city", collection)
        self.assertNotIn("city_code", collection)

    def test_unmapped_saved_city_requires_an_explicit_selection(self):
        self.path.write_text(json.dumps({"collection": {
            "city": "自定义城市", "city_code": "123456789",
        }}), encoding="utf-8")
        with self.assertRaisesRegex(SettingsError, "无法自动迁移"):
            read_full_settings()


if __name__ == "__main__":
    unittest.main()
