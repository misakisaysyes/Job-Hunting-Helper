"""Guard persisted greeting sends and status transitions."""

from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import MagicMock, patch

from browser.boss_message import send_boss_greeting
from data.job_store import JobStore


class JobWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.store = JobStore(self.conn, score_threshold=71)

    def tearDown(self) -> None:
        self.conn.close()

    def save(self, job_id: str, **values) -> dict:
        self.store.save({"source_platform": "boss", "source_job_id": job_id, **values})
        return self.store.get_job("boss", job_id)

    def test_no_ai_and_low_ai_have_distinct_initial_statuses(self) -> None:
        manual = self.save("manual")
        self.assertEqual(manual["job_status"], "greeting_ready")
        self.assertEqual(manual["job_status_history"], ["scored", "greeting_ready"])
        self.assertEqual(self.save("low", ai_score_status="scored", ai_score=70)["job_status"], "scored")
        edge = self.save("edge", ai_score_status="scored", ai_score=71)
        self.assertEqual(edge["job_status"], "greeting_ready")
        self.assertEqual(edge["job_status_history"], ["scored", "greeting_ready"])

    def test_low_score_only_enters_greeting_after_explicit_choice(self) -> None:
        self.save("low", ai_score_status="scored", ai_score=70)
        self.assertFalse(self.store.save_greeting("boss", "low", "您好"))
        self.assertFalse(self.store.reserve_greeting_send("boss", "low", "您好"))
        self.assertTrue(self.store.start_greeting("boss", "low"))
        self.assertFalse(self.store.start_greeting("boss", "low"))
        job = self.store.get_job("boss", "low")
        self.assertEqual(job["job_status"], "greeting_ready")
        self.assertEqual(job["greeting"], "")
        self.assertEqual(job["job_status_history"], ["scored", "greeting_ready"])

    def test_low_score_with_prepared_draft_stays_scored_until_manual_choice(self) -> None:
        job = self.save("low-draft", ai_score_status="scored", ai_score=63,
                        greeting="您好，想了解岗位。")
        self.assertEqual(job["job_status"], "scored")
        self.assertEqual(JobStore(self.conn).get_job("boss", "low-draft")["job_status"], "scored")
        self.assertTrue(self.store.start_greeting("boss", "low-draft"))
        self.assertEqual(JobStore(self.conn, score_threshold=71).get_job(
            "boss", "low-draft")["job_status"], "greeting_ready")

    def test_saved_or_collected_greeting_is_ready_before_send(self) -> None:
        automatic = self.save("automatic", ai_score_status="scored", ai_score=88,
                              greeting="您好，想了解岗位。")
        self.assertEqual(automatic["job_status"], "greeting_ready")
        self.assertEqual(automatic["job_status_history"], ["scored", "greeting_ready"])
        self.save("handwritten", ai_score_status="scored", ai_score=88)
        self.assertTrue(self.store.save_greeting("boss", "handwritten", "您好，我有相关经验。"))
        manual = self.store.get_job("boss", "handwritten")
        self.assertEqual(manual["job_status"], "greeting_ready")
        self.assertEqual(manual["job_status_history"], ["scored", "greeting_ready"])

    def test_send_is_reserved_once_and_only_verified_send_advances(self) -> None:
        self.save("one", ai_score_status="scored", ai_score=65)
        self.assertTrue(self.store.start_greeting("boss", "one"))
        self.assertTrue(self.store.reserve_greeting_send("boss", "one", "你好，我有相关经验"))
        self.assertFalse(self.store.reserve_greeting_send("boss", "one", "重复发送"))
        self.assertTrue(self.store.finish_greeting_send("boss", "one", outcome="unknown"))
        job = self.store.get_job("boss", "one")
        self.assertEqual(job["job_status_history"], ["scored", "greeting_ready"])
        self.assertEqual(job["greeting"], "你好，我有相关经验")
        self.assertFalse(self.store.reserve_greeting_send("boss", "one", "重复发送"))

        self.save("two", ai_score_status="scored", ai_score=80)
        self.assertTrue(self.store.reserve_greeting_send("boss", "two", "你好"))
        self.assertTrue(self.store.finish_greeting_send("boss", "two", outcome="sent"))
        self.assertTrue(self.store.force_end("boss", "two"))
        self.assertEqual(self.store.get_job("boss", "two")["job_status_history"],
                         ["scored", "greeting_ready", "greeted", "ended_forced"])

    def test_legacy_jobs_get_status_and_greeting_fields(self) -> None:
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("""CREATE TABLE collected_jobs (
                platform TEXT NOT NULL, source_job_id TEXT NOT NULL, record_json TEXT NOT NULL,
                ai_score_status TEXT NOT NULL, ai_score REAL, ai_score_reason TEXT NOT NULL,
                ai_score_error TEXT NOT NULL, collected_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                job_status TEXT NOT NULL, PRIMARY KEY (platform, source_job_id))""")
            conn.execute("""INSERT INTO collected_jobs VALUES
                ('boss', 'old', '{"source_platform":"boss","source_job_id":"old"}',
                 'not_scored', NULL, '', '', '2026-01-01', '2026-01-01', 'collected')""")
            conn.commit()
            job = JobStore(conn, score_threshold=71).get_job("boss", "old")
            self.assertEqual(job["job_status"], "greeting_ready")
            self.assertEqual(job["job_status_history"], ["scored", "greeting_ready"])
            self.assertEqual(job["greeting"], "")
        finally:
            conn.close()

    def test_existing_scored_jobs_advance_but_low_scores_stay_scored(self) -> None:
        self.save("qualified", ai_score_status="scored", ai_score=88)
        self.save("low", ai_score_status="scored", ai_score=70)
        self.conn.execute("UPDATE collected_jobs SET job_status = 'scored' WHERE source_job_id = 'qualified'")
        self.conn.execute("DELETE FROM job_status_events WHERE source_job_id = 'qualified' AND status = 'greeting_ready'")
        self.conn.commit()

        JobStore(self.conn, score_threshold=71)
        self.assertEqual(self.store.get_job("boss", "qualified")["job_status_history"],
                         ["scored", "greeting_ready"])
        self.assertEqual(self.store.get_job("boss", "low")["job_status"], "scored")

    def test_legacy_filtered_and_monitoring_statuses_are_migrated(self) -> None:
        self.save("low", ai_score_status="scored", ai_score=70)
        self.save("sent", ai_score_status="scored", ai_score=88)
        self.conn.execute("UPDATE collected_jobs SET job_status = 'filtered' WHERE source_job_id = 'low'")
        self.conn.execute("UPDATE collected_jobs SET job_status = 'monitoring' WHERE source_job_id = 'sent'")
        self.conn.commit()

        migrated = JobStore(self.conn, score_threshold=71)
        self.assertEqual(migrated.get_job("boss", "low")["job_status"], "scored")
        self.assertEqual(migrated.get_job("boss", "sent")["job_status"], "greeted")

    def test_sender_rejects_other_sites_and_mismatched_chat_targets_before_click(self) -> None:
        job = {"source_job_id": "wanted", "url": "https://other.example/job_detail/wanted.html"}
        with patch("browser.boss_message.ChromeBrowser") as browser_type:
            result = send_boss_greeting(job, "你好", "http://127.0.0.1:9222")
            self.assertFalse(result.verified)
            browser_type.assert_not_called()

            job["url"] = "https://www.zhipin.com/job_detail/wanted.html"
            browser = browser_type.return_value
            browser.new_tab.return_value = "tab"
            page = browser.page.return_value
            buttons = MagicMock()
            buttons.count.return_value = 1
            button = buttons.nth.return_value
            button.is_visible.return_value = True
            button.get_attribute.return_value = "/web/geek/chat?jobId=other"
            page.locator.return_value = buttons
            result = send_boss_greeting(job, "你好", "http://127.0.0.1:9222")
            self.assertFalse(result.verified)
            button.click.assert_not_called()

    def test_sender_clicks_once_and_requires_matching_own_message(self) -> None:
        job = {"source_job_id": "wanted", "url": "https://www.zhipin.com/job_detail/wanted.html"}
        with patch("browser.boss_message.ChromeBrowser") as browser_type, patch("browser.boss_message.time.sleep"):
            browser = browser_type.return_value
            browser.new_tab.return_value = "tab"
            page = browser.page.return_value
            page.url = job["url"]
            page.context.pages = [page]
            chat_button = MagicMock()
            chat_button.count.return_value = 1
            chat_button.nth.return_value.is_visible.return_value = True
            chat_button.nth.return_value.get_attribute.return_value = "/web/geek/chat?jobId=wanted"
            input_box = MagicMock()
            input_box.count.return_value = 1
            input_box.first.is_visible.return_value = True
            input_box.first.inner_text.return_value = "你好"
            send_button = MagicMock()
            send_button.count.return_value = 1
            send_button.first.is_enabled.return_value = True
            page.locator.side_effect = [chat_button, input_box, send_button]
            def navigate(url, **_): page.url = url
            page.goto.side_effect = navigate
            page.evaluate.side_effect = [False, "missing", "delivered", "delivered"]
            browser.close.side_effect = RuntimeError("关闭浏览器连接时出错")
            result = send_boss_greeting(job, "你好", "http://127.0.0.1:9222")
            self.assertTrue(result.verified)
            self.assertEqual(result.chat_url, "https://www.zhipin.com/web/geek/chat?jobId=wanted")
            chat_button.nth.return_value.click.assert_called_once()
            input_box.first.wait_for.assert_called_once_with(state="visible", timeout=8000)
            send_button.first.wait_for.assert_called_once_with(state="visible", timeout=8000)
            send_button.first.click.assert_called_once()


if __name__ == "__main__":
    unittest.main()
