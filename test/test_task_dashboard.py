from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from api.collection import CollectionRun
from api.jobs import JobActionError
from api.server import requires_service_restart
from api.tasks import get_task_history
from api.settings import (SettingsError, apply_full_settings, read_full_settings, save_full_settings,
                          save_resume_upload)
from config import DEFAULT_CONFIG, MONITORING_CONFIG
from data.job_store import JobStore
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
    def test_legacy_scans_do_not_create_incomplete_daily_new_counts(self):
        with TemporaryDirectory() as temp:
            store = TaskRunStore(Path(temp) / "tasks.sqlite3")
            legacy = store.start("monitoring", "2026-10-10T00:00:00+00:00", "旧扫描")
            store.finish(legacy, "completed", "旧扫描完成", {"scanned": 3, "saved": 2},
                "2026-10-10T00:01:00+00:00")
            current = store.start("monitoring", "2026-10-10T00:02:00+00:00", "新扫描")
            store.finish(current, "completed", "新扫描完成",
                {"scanned": 4, "communicated_new": 1, "filter_new": 2}, "2026-10-10T00:03:00+00:00")
            summary = store.for_date(date(2026, 10, 10))["monitoring"]
            self.assertEqual(summary["totals"]["scanned"], 7)
            self.assertIsNone(summary["totals"]["communicated_new"])
            self.assertIsNone(summary["totals"]["filter_new"])
            self.assertEqual(summary["history"][0]["counts"]["communicated_new"], 1)

    def test_historical_day_totals_use_shanghai_boundaries_and_start_date(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "tasks.sqlite3"
            store = TaskRunStore(path)
            for task_type, started, finished, counts in (
                    ("collection", "2026-10-08T15:59:59+00:00", "2026-10-08T16:00:01+00:00", {"new": 99}),
                    ("collection", "2026-10-08T16:00:00+00:00", "2026-10-08T16:01:00+00:00", {"seen": 3, "new": 2}),
                    ("monitoring", "2026-10-09T01:00:00+00:00", "2026-10-09T01:01:00+00:00", {"scanned": 5, "saved": 4}),
                    ("collection", "2026-10-09T15:59:59+00:00", "2026-10-09T16:05:00+00:00", {"seen": 4, "new": 1}),
                    ("monitoring", "2026-10-09T16:06:00+00:00", "2026-10-09T16:07:00+00:00", {"scanned": 99})):
                run = store.start(task_type, started, "任务开始")
                store.finish(run, "completed", "任务完成", counts, finished)
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute("CREATE TABLE greeting_send_events (sent_at TEXT NOT NULL)")
                conn.executemany("INSERT INTO greeting_send_events (sent_at) VALUES (?)", [
                    ("2026-10-08T15:59:59+00:00",), ("2026-10-08T16:00:00+00:00",),
                    ("2026-10-09T15:59:59+00:00",), ("2026-10-09T16:00:00+00:00",),
                ])
            summary = store.for_date(date(2026, 10, 9))
            self.assertEqual(summary["date"], "2026-10-09")
            self.assertEqual(summary["timezone"], "Asia/Shanghai")
            self.assertEqual(summary["collection"]["runs"], 2)
            self.assertEqual(summary["collection"]["totals"], {"seen": 7, "new": 3, "greeted": 2})
            self.assertEqual(summary["monitoring"]["totals"],
                             {"scanned": 5, "saved": 4, "communicated_new": None, "filter_new": None})
            self.assertEqual(summary["collection"]["history"][0]["started_at"], "2026-10-09T15:59:59+00:00")
            self.assertEqual(summary["collection"]["history"][0]["finished_at"], "2026-10-09T16:05:00+00:00")

    def test_history_defaults_to_yesterday_and_reads_a_selected_or_empty_day(self):
        with TemporaryDirectory() as temp:
            store = TaskRunStore(Path(temp) / "tasks.sqlite3")
            run = store.start("collection", "2026-10-08T16:01:00+00:00", "采集中")
            store.finish(run, "completed", "完成", {"new": 2}, "2026-10-08T16:02:00+00:00")
            now = datetime(2026, 10, 9, 16, 2, tzinfo=timezone.utc)
            summary = get_task_history(store, now=now)
            self.assertEqual(summary["date"], "2026-10-09")
            self.assertEqual(summary["latest_date"], "2026-10-09")
            self.assertEqual(summary["collection"]["runs"], 1)
            empty = get_task_history(store, "2026-10-08", now=now)
            self.assertEqual(empty["collection"], {"runs": 0, "totals": {"greeted": 0}, "history": []})
            self.assertEqual(empty["monitoring"], {"runs": 0, "totals": {}, "history": []})
            self.assertEqual({key: value for key, value in summary.items() if key != "latest_date"},
                store.today(now=datetime(2026, 10, 9, 2, tzinfo=timezone.utc)))

    def test_history_rejects_invalid_dates_today_and_future_dates(self):
        with TemporaryDirectory() as temp:
            store = TaskRunStore(Path(temp) / "tasks.sqlite3")
            now = datetime(2026, 10, 9, 16, 2, tzinfo=timezone.utc)
            for value in ("invalid", "20261009", "2026-02-30", "2026-10-32", "2026-10-10", "2026-10-11"):
                with self.subTest(value=value), self.assertRaises(JobActionError) as context:
                    get_task_history(store, value, now=now)
                self.assertEqual(context.exception.status_code, 400)

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
            self.assertEqual(summary["collection"]["totals"],
                             {"new": 2, "duplicate": 1, "greeted": 0})
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

    def test_running_collection_counts_are_visible_in_both_status_and_today(self):
        with TemporaryDirectory() as temp:
            db_path = Path(temp) / "tasks.sqlite3"
            runner = CollectionRun(db_path)
            run_id = runner.run_store.start(
                "collection", "2026-10-05T01:00:00+00:00", "采集中")
            runner.status = "running"
            runner.run_id = run_id
            runner._progress({"seen": 5, "duplicate_jobs": 2, "new": 1,
                              "filtered": 2, "qualified": 0, "ai_scored": 1,
                              "ai_score_failed": 0})

            self.assertEqual(runner.snapshot()["counts"]["seen"], 5)
            summary = runner.run_store.today(now=datetime(2026, 10, 5, 2,
                                                          tzinfo=timezone.utc))
            run = summary["collection"]["history"][0]
            self.assertEqual(run["status"], "running")
            self.assertEqual(run["counts"]["duplicate_jobs"], 2)
            self.assertEqual(summary["collection"]["totals"]["ai_scored"], 1)

    def test_daily_greeted_counts_verified_sends_not_generated_drafts(self):
        with TemporaryDirectory() as temp:
            db_path = Path(temp) / "tasks.sqlite3"
            runs = TaskRunStore(db_path)
            run_id = runs.start("collection", "2026-10-05T00:00:00+00:00", "采集中")
            runs.finish(run_id, "completed", "完成",
                        {"new": 2, "ai_greeting_generated": 2},
                        "2026-10-05T00:02:00+00:00")
            with closing(sqlite3.connect(db_path)) as conn:
                jobs = JobStore(conn, score_threshold=71)
                for job_id in ("today", "yesterday", "draft"):
                    jobs.save({"source_platform": "boss", "source_job_id": job_id,
                               "ai_score_status": "scored", "ai_score": 80})
                for job_id in ("today", "yesterday"):
                    self.assertTrue(jobs.reserve_greeting_send("boss", job_id, "您好"))
                    self.assertTrue(jobs.finish_greeting_send("boss", job_id, outcome="sent"))
                conn.execute("""UPDATE greeting_send_events SET sent_at = CASE source_job_id
                    WHEN 'today' THEN '2026-10-05T02:00:00+00:00'
                    ELSE '2026-10-04T15:59:00+00:00' END""")
                conn.commit()

                summary = runs.today(now=datetime(2026, 10, 5, 3, tzinfo=timezone.utc))
                self.assertEqual(summary["collection"]["totals"], {"new": 2, "greeted": 1})
                self.assertEqual(summary["collection"]["history"][0]["counts"], {"new": 2})
                self.assertTrue(jobs.delete("boss", "today"))
            self.assertEqual(runs.today(now=datetime(2026, 10, 5, 3,
                                                    tzinfo=timezone.utc))["collection"]
                             ["totals"]["greeted"], 1)

    def test_existing_confirmed_sends_are_backfilled_for_today(self):
        with TemporaryDirectory() as temp:
            db_path = Path(temp) / "tasks.sqlite3"
            runs = TaskRunStore(db_path)
            with closing(sqlite3.connect(db_path)) as conn:
                jobs = JobStore(conn, score_threshold=71)
                jobs.save({"source_platform": "boss", "source_job_id": "old-send",
                           "ai_score_status": "scored", "ai_score": 80})
                jobs.reserve_greeting_send("boss", "old-send", "您好")
                jobs.finish_greeting_send("boss", "old-send", outcome="sent")
                conn.execute("""UPDATE job_status_events SET changed_at = ?
                    WHERE source_job_id = 'old-send' AND status = 'greeted'""",
                    ("2026-10-05T02:00:00+00:00",))
                conn.execute("DROP TABLE greeting_send_events")
                conn.commit()
                JobStore(conn, score_threshold=71)
            summary = runs.today(now=datetime(2026, 10, 5, 3, tzinfo=timezone.utc))
            self.assertEqual(summary["collection"]["totals"]["greeted"], 1)


if __name__ == "__main__":
    unittest.main()
