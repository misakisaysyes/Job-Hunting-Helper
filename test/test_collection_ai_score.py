"""Check optional scoring overlaps collection and records terminal failures."""

from __future__ import annotations

import json
import os
import sys
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collector.models import JobCandidate, PlatformCollectionResult
from collector.orchestrator import CollectionOrchestrator
from collector.platforms.boss import JS_DETECT_COLLECTION_RISK, JS_EXTRACT_DETAIL
from config import DEFAULT_CONFIG
from run import run_collection


def _job(job_id: str) -> JobCandidate:
    return JobCandidate(
        platform="boss", source_job_id=job_id, title="前端工程师",
        company="示例公司", jd="负责浏览器性能优化",
    )


class AIScoreCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = deepcopy(DEFAULT_CONFIG)
        self.config["collection"].update(
            mode="recommend", encrypt_expect_id=["expect-id"], max_jobs=0,
        )

    def run_with_runner(self, runner, on_record):
        with patch("run.open_safety_db"):
            with patch("run.ChromeBrowser"):
                with patch("run.CollectionOrchestrator", return_value=runner):
                    return run_collection(self.config, on_record=on_record)

    def test_disabled_scoring_keeps_plain_job_output(self) -> None:
        self.config["ai"]["use_ai_score"] = False
        class Runner:
            def run(self, request, *, on_job, on_event, max_jobs):
                on_job(_job("one"))
                return PlatformCollectionResult(platform="boss", status="completed", counts={"new": 1})

        records = []
        result = self.run_with_runner(Runner(), records.append)
        record = records[0]
        self.assertEqual(record["id"], "one")
        self.assertNotIn("ai_score_status", record)
        self.assertEqual(result.counts, {"new": 1})

    def test_disabled_scoring_applies_fixed_greeting_to_prefiltered_job(self) -> None:
        self.config["ai"].update(use_ai_score=False, greeting_template="您好，想了解岗位。")

        class Runner:
            def run(self, request, *, on_job, on_event, max_jobs):
                on_job(_job("one"))
                return PlatformCollectionResult(platform="boss", status="completed", counts={"new": 1})

        records = []
        self.run_with_runner(Runner(), records.append)
        self.assertEqual(records[0]["greeting"], "您好，想了解岗位。")

    def test_browser_and_state_db_close_when_collection_fails(self) -> None:
        self.config["ai"]["use_ai_score"] = False

        class Runner:
            def run(self, request, *, on_job, on_event, max_jobs):
                raise RuntimeError("collection failed")

        with patch("run.open_safety_db") as open_db:
            with patch("run.ChromeBrowser") as chrome:
                with patch("run.CollectionOrchestrator", return_value=Runner()):
                    with self.assertRaisesRegex(RuntimeError, "collection failed"):
                        run_collection(self.config, on_record=lambda record: None)
        chrome.return_value.close.assert_called_once()
        open_db.return_value.close.assert_called_once()

    def test_score_starts_while_collection_is_still_running(self) -> None:
        started = threading.Event()
        release = threading.Event()
        collection_done = threading.Event()
        run_done = threading.Event()
        self.config["ai"].update(use_ai_score=True, ai_api_concurrency=1,
                                 score_user_prompt="重点看性能经验")

        def fake_model(config, system, user):
            self.assertIn("重点看性能经验", user)
            started.set()
            self.assertTrue(release.wait(timeout=5))
            return '{"score": 88, "reason": "有相关经验"}'

        class Runner:
            def run(self, request, *, on_job, on_prefilter_pass, on_event, max_jobs):
                on_job(_job("one"))
                on_prefilter_pass(_job("one"))
                self_outer.assertTrue(started.wait(timeout=2))
                self_outer.assertFalse(release.is_set())
                on_job(_job("two"))
                on_prefilter_pass(_job("two"))
                collection_done.set()
                return PlatformCollectionResult(platform="boss", status="completed", counts={"new": 2})

        self_outer = self
        with TemporaryDirectory() as directory:
            path = Path(directory) / "resume.md"
            path.write_text("做过浏览器性能优化", encoding="utf-8")
            self.config["profile"]["resume_path"] = str(path)
            records = []
            results = []

            def run_pipeline():
                try:
                    results.append(self.run_with_runner(Runner(), records.append))
                finally:
                    run_done.set()

            with patch("ai.scheduler.call_model", side_effect=fake_model):
                thread = threading.Thread(target=run_pipeline)
                thread.start()
                try:
                    self.assertTrue(collection_done.wait(timeout=2))
                    self.assertFalse(run_done.wait(timeout=0.05))
                finally:
                    release.set()
                    thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertEqual([record["id"] for record in records], ["one", "two"])
        self.assertEqual([record["ai_score_status"] for record in records], ["scored", "scored"])
        self.assertEqual(results[0].counts["ai_scored"], 2)
        self.assertEqual(results[0].counts["ai_score_failed"], 0)

    def test_exhausted_api_retries_mark_score_failed(self) -> None:
        self.config["ai"].update(use_ai_score=True, ai_api_concurrency=1, retry_count=1)

        class Runner:
            def run(self, request, *, on_job, on_prefilter_pass, on_event, max_jobs):
                on_job(_job("one"))
                on_prefilter_pass(_job("one"))
                return PlatformCollectionResult(platform="boss", status="completed", counts={"new": 1})

        with TemporaryDirectory() as directory:
            path = Path(directory) / "resume.md"
            path.write_text("做过浏览器性能优化", encoding="utf-8")
            self.config["profile"]["resume_path"] = str(path)
            records = []
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}):
                with patch("ai.client.httpx.post", return_value=httpx.Response(503)) as post:
                    with patch("ai.client.time.sleep"):
                        result = self.run_with_runner(Runner(), records.append)
        record = records[0]
        self.assertEqual(post.call_count, 2)
        self.assertEqual(record["ai_score_status"], "score_failed")
        self.assertIsNone(record["ai_score"])
        self.assertIn("HTTP 503", record["ai_score_error"])
        self.assertEqual(result.counts["ai_score_failed"], 1)

    def test_boss_submits_only_complete_jobs_that_pass_detail_prefilter(self) -> None:
        self.config["ai"]["use_ai_score"] = True
        self.config["profile"]["jd_deal_breakers"] = ["拒绝"]

        class Browser:
            def __init__(self):
                self.current_url = ""

            def request_boss_json(self, path, method, params):
                return {"http_status": 200, "body": {"code": 0, "zpData": {
                    "jobList": [
                        {"encryptJobId": "passed", "jobName": "前端工程师", "brandName": "示例公司"},
                        {"encryptJobId": "filtered", "jobName": "前端工程师", "brandName": "示例公司"},
                    ],
                    "hasMore": False,
                }}}

            def new_tab(self, url, background=False):
                self.current_url = url
                return "detail"

            def navigate(self, tab, url):
                self.current_url = url
                return tab

            def wait_for_load(self, tab, timeout):
                return None

            def evaluate(self, tab, script):
                if script == JS_DETECT_COLLECTION_RISK:
                    return '{"risk": null}'
                if script == JS_EXTRACT_DETAIL:
                    jd = "负责浏览器性能优化" if "passed" in self.current_url else "拒绝该岗位"
                    return json.dumps({"title": "前端工程师", "company": "示例公司", "jd": jd})
                raise AssertionError("Unexpected browser evaluation")

            def close_tab(self, tab):
                return None

            def close(self):
                return None

        self.config["collection"]["city"] = "北京"
        with TemporaryDirectory() as directory:
            path = Path(directory) / "resume.md"
            path.write_text("做过浏览器性能优化", encoding="utf-8")
            self.config["profile"]["resume_path"] = str(path)
            records = []
            with patch("collector.platforms.boss.PageThrottle.wait", return_value=False):
                with patch("collector.platforms.boss._wait_or_stop", return_value=False):
                    with patch("collector.platforms.boss.BossCollector._refresh_font_digits", return_value=False):
                        with patch("ai.scheduler.call_model", return_value='{"score": 88, "reason": "相关"}') as model:
                            with patch("run.open_safety_db"):
                                with patch("run.ChromeBrowser") as chrome:
                                    chrome.return_value = Browser()
                                    with patch("run.CollectionOrchestrator", side_effect=lambda **kwargs:
                                               CollectionOrchestrator(browser=kwargs["browser"], config=kwargs["config"])):
                                        result = run_collection(self.config, on_record=records.append)
        self.assertEqual([record["id"] for record in records], ["passed"])
        self.assertEqual(records[0]["jd"], "负责浏览器性能优化")
        self.assertEqual(records[0]["ai_score_status"], "scored")
        self.assertEqual(result.counts["filtered"], 1)
        self.assertEqual(result.counts["ai_scored"], 1)
        model.assert_called_once()


if __name__ == "__main__":
    unittest.main()
