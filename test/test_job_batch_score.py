"""Explicit rescoring resets only pre-send workflow state."""

from __future__ import annotations

from contextlib import closing
from copy import deepcopy
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

from ai.client import AIRequestError
from api.jobs import JobActionError, score_job
from config import DEFAULT_CONFIG
from data.job_store import JobStore


class JobBatchScoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "jobs.sqlite"
        resume = Path(self.temp.name) / "resume.md"
        resume.write_text("前端开发经验", encoding="utf-8")
        self.config = deepcopy(DEFAULT_CONFIG)
        self.config["profile"]["resume_path"] = str(resume)
        with closing(sqlite3.connect(self.db_path)) as conn:
            store = JobStore(conn, score_threshold=71)
            store.save({
                "source_platform": "boss", "source_job_id": "one", "title": "前端工程师",
                "jd": "开发 React 页面", "ai_score_status": "scored", "ai_score": 85,
                "greeting": "您好，希望聊聊岗位。",
            })

    def result(self, score: float) -> dict:
        scheduler = MagicMock()
        scheduler.submit.return_value.result.return_value = SimpleNamespace(
            score=score, reason="重新匹配后的理由")
        return score_job(self.db_path, self.config, scheduler, "boss", "one")

    def test_rescore_resets_unsent_greeting_and_status(self) -> None:
        job = self.result(60)["job"]
        self.assertEqual(job["job_status"], "scored")
        self.assertEqual(job["greeting"], "")
        self.assertEqual(job["ai_score_reason"], "重新匹配后的理由")
        job = self.result(90)["job"]
        self.assertEqual(job["job_status"], "greeting_ready")
        self.assertEqual(job["job_status_history"][-2:], ["scored", "greeting_ready"])

    def test_failed_rescore_is_recorded_without_erasing_draft(self) -> None:
        scheduler = MagicMock()
        scheduler.submit.return_value.result.side_effect = AIRequestError("服务暂不可用")
        result = score_job(self.db_path, self.config, scheduler, "boss", "one")
        self.assertEqual(result["error"], "服务暂不可用")
        self.assertEqual(result["job"]["ai_score_status"], "score_failed")
        self.assertEqual(result["job"]["greeting"], "您好，希望聊聊岗位。")

    def test_sent_job_cannot_reset_for_rescore(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            store = JobStore(conn, score_threshold=71)
            self.assertTrue(store.reserve_greeting_send("boss", "one", "您好"))
            self.assertTrue(store.finish_greeting_send("boss", "one", outcome="sent"))
        with self.assertRaisesRegex(JobActionError, "已发送或已结束"):
            self.result(90)


if __name__ == "__main__":
    unittest.main()
