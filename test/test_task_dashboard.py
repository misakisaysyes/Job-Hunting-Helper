from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from api.collection import CollectionRun
from api.server import requires_service_restart
from api.settings import (SettingsError, apply_full_settings, read_full_settings, save_full_settings,
                          save_resume_upload)
from config import DEFAULT_CONFIG, MONITORING_CONFIG
from data.task_run_store import TaskAlreadyRunning, TaskRunStore


class FullSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "user-config.json"
        self.original_default = deepcopy(DEFAULT_CONFIG)
        DEFAULT_CONFIG["collection"]["encrypt_expect_id"] = ["test-id"]
        self.saved_default = deepcopy(DEFAULT_CONFIG)
        self.saved_monitoring = deepcopy(MONITORING_CONFIG)
        self.patch = patch("api.settings.USER_CONFIG_PATH", self.path)
        self.patch.start()
        self.resume_patch = patch("api.settings.RESUME_UPLOAD_DIR", Path(self.temp.name) / "resumes")
        self.resume_patch.start()

    def tearDown(self):
        self.patch.stop()
        self.resume_patch.stop()
        DEFAULT_CONFIG.clear()
        DEFAULT_CONFIG.update(self.original_default)
        MONITORING_CONFIG.clear()
        MONITORING_CONFIG.update(self.saved_monitoring)
        self.temp.cleanup()

    def test_full_settings_expose_every_runtime_option_and_persist(self):
        settings = read_full_settings()["settings"]
        for section, fields in DEFAULT_CONFIG.items():
            self.assertEqual(settings[section], fields)
        self.assertEqual(settings["monitoring"], MONITORING_CONFIG)

        result = save_full_settings({
            "collection": {"max_jobs": 12},
            "profile": {"blocked_companies": ["示例公司"]},
            "monitoring": {"message_limit": 16},
        })
        self.assertEqual(result["settings"]["collection"]["max_jobs"], 12)
        self.assertEqual(result["settings"]["monitoring"]["message_limit"], 16)
        self.assertTrue(result["pending_apply"])
        self.assertEqual(MONITORING_CONFIG["message_limit"], self.saved_monitoring["message_limit"])
        self.assertFalse(apply_full_settings()["pending_apply"])
        self.assertEqual(MONITORING_CONFIG["message_limit"], 16)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {
            "collection": {"max_jobs": 12},
            "profile": {"blocked_companies": ["示例公司"]},
            "monitoring": {"message_limit": 16},
        })

    def test_invalid_combination_does_not_persist(self):
        with self.assertRaises(SettingsError):
            save_full_settings({"collection": {"mode": "search"}})
        self.assertFalse(self.path.exists())
        self.assertEqual(DEFAULT_CONFIG, self.saved_default)

    def test_markdown_upload_sets_resume_path_and_preserves_content(self):
        content = "# 简历\n前端开发经验\n".encode("utf-8")
        result = save_resume_upload("我的简历.MD", content)
        resume_path = Path(result["resume_path"])
        self.assertEqual(resume_path.read_bytes(), content)
        self.assertEqual(DEFAULT_CONFIG["profile"]["resume_path"], str(resume_path))
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))
                         ["profile"]["resume_path"], str(resume_path))

    def test_invalid_upload_does_not_replace_resume(self):
        for filename, content in (("resume.pdf", b"PDF"), ("resume.md", b"\xff"),
                                  ("resume.md", b" \n ")):
            with self.subTest(filename=filename, content=content):
                with self.assertRaises(SettingsError):
                    save_resume_upload(filename, content)
        self.assertFalse(self.path.exists())
        self.assertEqual(DEFAULT_CONFIG, self.saved_default)

    def test_restart_only_for_startup_configuration(self):
        saved = read_full_settings()["settings"]
        concurrency = saved["ai"]["ai_api_concurrency"]
        db_path = Path(saved["safety"]["state_db"])
        self.assertFalse(requires_service_restart(saved, concurrency, db_path,
                                                  state_db_overridden=False))
        saved["ai"]["ai_api_concurrency"] += 1
        self.assertTrue(requires_service_restart(saved, concurrency, db_path,
                                                 state_db_overridden=False))
        saved["ai"]["ai_api_concurrency"] = concurrency
        saved["safety"]["state_db"] = str(db_path.with_name("another.sqlite3"))
        self.assertTrue(requires_service_restart(saved, concurrency, db_path,
                                                 state_db_overridden=False))
        self.assertFalse(requires_service_restart(saved, concurrency, db_path,
                                                  state_db_overridden=True))


class TaskRunStoreTests(unittest.TestCase):
    def test_collection_and_monitoring_share_one_running_slot(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "tasks.sqlite3"
            stores = (TaskRunStore(path), TaskRunStore(path))
            barrier = Barrier(2)

            def try_start(store, task_type):
                barrier.wait()
                try:
                    return store.start(task_type, "2026-10-08T00:00:00+00:00", "运行中")
                except TaskAlreadyRunning as exc:
                    return exc

            with ThreadPoolExecutor(max_workers=2) as pool:
                collection = pool.submit(try_start, stores[0], "collection")
                monitoring = pool.submit(try_start, stores[1], "monitoring")
                results = (collection.result(), monitoring.result())
            self.assertEqual(sum(isinstance(value, int) for value in results), 1)
            self.assertEqual(sum(isinstance(value, TaskAlreadyRunning) for value in results), 1)
            winner = "collection" if isinstance(results[0], int) else "monitoring"
            self.assertEqual(stores[0].active_task_type(), winner)
            stores[0].finish(next(value for value in results if isinstance(value, int)),
                             "completed", "完成", {}, "2026-10-08T00:01:00+00:00")
            self.assertIsNone(stores[0].active_task_type())
            self.assertIsInstance(stores[0].start("monitoring", "2026-10-08T00:02:00+00:00", "运行中"), int)

    def test_daily_history_totals_and_interrupted_run(self):
        with TemporaryDirectory() as temp:
            store = TaskRunStore(Path(temp) / "tasks.sqlite3")
            yesterday = store.start("collection", "2026-10-04T15:59:00+00:00", "旧任务")
            store.finish(yesterday, "completed", "旧任务结束", {"new": 9},
                         "2026-10-04T15:59:30+00:00")
            today = store.start("collection", "2026-10-04T16:01:00+00:00", "采集中")
            store.finish(today, "completed", "完成", {"new": 2, "duplicate": 1},
                         "2026-10-04T16:02:00+00:00")
            store.start("monitoring", "2026-10-05T01:00:00+00:00", "监测中")

            summary = store.today(now=datetime(2026, 10, 5, 2, tzinfo=timezone.utc))
            self.assertEqual(summary["collection"]["runs"], 1)
            self.assertEqual(summary["collection"]["totals"], {"new": 2, "duplicate": 1})
            self.assertEqual(summary["monitoring"]["runs"], 1)
            self.assertEqual(summary["monitoring"]["history"][0]["status"], "running")

            store.mark_interrupted()
            self.assertEqual(store.today(now=datetime(2026, 10, 5, 2,
                                                   tzinfo=timezone.utc))["monitoring"]
                             ["history"][0]["status"], "interrupted")

    def test_collection_completion_is_recorded(self):
        with TemporaryDirectory() as temp:
            db_path = Path(temp) / "tasks.sqlite3"
            runner = CollectionRun(db_path)
            started = "2026-10-05T01:00:00+00:00"
            run_id = runner.run_store.start("collection", started, "采集中")
            with patch("api.collection.run_collection", return_value=SimpleNamespace(
                    status="completed", message="采集完成", counts={"seen": 4, "new": 2})):
                runner._execute({}, run_id)
            row = runner.run_store.today(now=datetime(2026, 10, 5, 2,
                                                      tzinfo=timezone.utc))["collection"]["history"][0]
            self.assertEqual(row["status"], "completed")
            self.assertEqual(row["counts"], {"seen": 4, "new": 2})


if __name__ == "__main__":
    unittest.main()
