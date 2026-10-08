"""Automatic greetings follow the configured score threshold and persist."""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
import unittest
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai import AIRequestError, AITaskScheduler
from cli.cli import build_parser, resolve_config
from collector.models import JobCandidate, PlatformCollectionResult
from config import DEFAULT_CONFIG
from data.job_store import JobStore
from run import run_collection, validate_run_config


def _job(job_id: str) -> JobCandidate:
    return JobCandidate(
        platform="boss", source_job_id=job_id, title="前端工程师",
        company="示例公司", jd="负责浏览器性能优化",
    )


class AIGreetingCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = deepcopy(DEFAULT_CONFIG)
        self.config["collection"].update(mode="recommend", encrypt_expect_id=["expect-id"])
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        self.config["safety"]["state_db"] = str(directory / "jobs.sqlite")
        resume = directory / "resume.md"
        resume.write_text("做过浏览器性能优化", encoding="utf-8")
        self.config["profile"]["resume_path"] = str(resume)
        self.config["ai"]["score_threshold"] = 71

    def run_jobs(self, jobs, model_call, on_record=None):
        class Runner:
            def run(self, request, *, on_job, on_prefilter_pass, on_event, max_jobs):
                for job in jobs:
                    on_job(job)
                    on_prefilter_pass(job)
                return PlatformCollectionResult(
                    platform="boss", status="completed", counts={"new": len(jobs)},
                )

        with AITaskScheduler(self.config, model_call=model_call) as scheduler:
            with patch("run.ChromeBrowser"):
                with patch("run.CollectionOrchestrator", return_value=Runner()):
                    return run_collection(self.config, on_record=on_record or (lambda _: None),
                                          ai_scheduler=scheduler)

    def stored_jobs(self):
        with closing(sqlite3.connect(self.config["safety"]["state_db"])) as conn:
            jobs, _ = JobStore(conn).list_jobs(limit=20, offset=0)
        return {job["source_job_id"]: job for job in jobs}

    def test_default_off_does_not_generate_greetings(self) -> None:
        self.assertFalse(self.config["ai"]["use_ai_greeting"])
        calls = []

        def model(system, user):
            calls.append(system)
            return '{"score": 88, "reason": "匹配"}'

        result = self.run_jobs([_job("one")], model)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.stored_jobs()["one"]["greeting"], "")
        self.assertNotIn("ai_greeting_generated", result.counts)

    def test_fixed_template_only_applies_above_score_threshold(self) -> None:
        self.config["ai"]["greeting_template"] = "您好，想了解岗位。"

        def model(system, user):
            score = 71 if '"source_job_id": "at-threshold"' in user else 70
            return f'{{"score": {score}, "reason": "匹配"}}'

        self.run_jobs([_job("at-threshold"), _job("below")], model)
        jobs = self.stored_jobs()
        self.assertEqual(jobs["at-threshold"]["greeting"], "您好，想了解岗位。")
        self.assertEqual(jobs["below"]["greeting"], "")
        self.assertEqual(jobs["below"]["job_status"], "scored")

    def test_enabled_generates_at_threshold_and_persists_in_list(self) -> None:
        self.config["ai"]["use_ai_greeting"] = True
        self.config["ai"]["greeting_user_prompt"] = "语气自然，突出浏览器性能优化"
        greeted = []

        def model(system, user):
            if "岗位匹配评估员" in system:
                score = 71 if '"source_job_id": "at-threshold"' in user else 70
                return f'{{"score": {score}, "reason": "匹配"}}'
            greeted.append(user)
            return "您好，我有浏览器性能优化经验，希望聊聊这个岗位。"

        result = self.run_jobs([_job("at-threshold"), _job("below")], model)
        jobs = self.stored_jobs()
        self.assertEqual(len(greeted), 1)
        self.assertEqual(json.loads(greeted[0])["用户补充要求"],
                         "语气自然，突出浏览器性能优化")
        self.assertEqual(jobs["at-threshold"]["greeting"], "您好，我有浏览器性能优化经验，希望聊聊这个岗位。")
        self.assertEqual(jobs["at-threshold"]["ai_greeting_status"], "generated")
        self.assertEqual(jobs["at-threshold"]["job_status"], "greeting_ready")
        self.assertEqual(jobs["at-threshold"]["job_status_history"], ["scored", "greeting_ready"])
        self.assertEqual(jobs["below"]["greeting"], "")
        self.assertEqual(jobs["below"]["job_status"], "scored")
        self.assertEqual(result.counts["ai_greeting_generated"], 1)
        self.assertEqual(result.counts["ai_greeting_failed"], 0)

    def test_greeting_failure_keeps_greeting_stage_available(self) -> None:
        self.config["ai"]["use_ai_greeting"] = True

        def model(system, user):
            if "岗位匹配评估员" in system:
                return '{"score": 88, "reason": "匹配"}'
            raise AIRequestError("模型暂不可用")

        result = self.run_jobs([_job("one")], model)
        job = self.stored_jobs()["one"]
        self.assertEqual(job["ai_score"], 88)
        self.assertEqual(job["job_status"], "greeting_ready")
        self.assertEqual(job["ai_greeting_status"], "failed")
        self.assertEqual(job["ai_greeting_error"], "模型暂不可用")
        self.assertEqual(result.counts["ai_greeting_failed"], 1)

    def test_collection_waits_for_greeting(self) -> None:
        self.config["ai"]["use_ai_greeting"] = True
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def model(system, user):
            if "岗位匹配评估员" in system:
                return '{"score": 88, "reason": "匹配"}'
            started.set()
            if not release.wait(timeout=5):
                raise AssertionError("招呼语任务未被释放")
            return "您好，想了解岗位。"

        def run():
            try:
                self.run_jobs([_job("one")], model)
            finally:
                finished.set()

        thread = threading.Thread(target=run)
        thread.start()
        try:
            self.assertTrue(started.wait(timeout=2))
            self.assertFalse(finished.wait(timeout=0.05))
        finally:
            release.set()
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.stored_jobs()["one"]["greeting"], "您好，想了解岗位。")

    def test_cli_override_and_scoring_dependency(self) -> None:
        parser = build_parser()
        config = resolve_config(parser.parse_args(["--use-ai-greeting", "--encrypt-expect-id", "test-id"]), parser)
        self.assertTrue(config["ai"]["use_ai_greeting"])
        config = resolve_config(parser.parse_args(["--no-use-ai-greeting", "--encrypt-expect-id", "test-id"]), parser)
        self.assertFalse(config["ai"]["use_ai_greeting"])
        self.config["ai"].update(use_ai_score=False, use_ai_greeting=True)
        with self.assertRaisesRegex(ValueError, "必须同时启用"):
            validate_run_config(self.config)


if __name__ == "__main__":
    unittest.main()
