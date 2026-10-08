"""Follow-up candidates, duplicate prevention, and send outcomes."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from api.followups import annotate_followup_availability, proposal, proposal_with_reason, send_followup
from api.jobs import JobActionError
from api.monitoring import MonitoringRun
from browser.boss_followup import FollowupResult
from data.conversation_store import ConversationStore


class FollowupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
        self.config = {
            "followup_enabled": True, "followup_unread": True,
            "followup_read_no_reply": True, "followup_cooldown_hours": 24,
            "followup_max_count": 2, "followup_days": 4,
        }
        self.greeting = "您好，想了解这个岗位。"

    def messages(self, hours_ago: int = 25, status: str = "1") -> list[dict]:
        return [{"message_id": "100", "sender": "self", "text": self.greeting,
                 "biz_type": "101", "delivery_status": status,
                 "sent_at": str(int((self.now - timedelta(hours=hours_ago)).timestamp() * 1000))}]

    def test_candidate_requires_receipt_cooldown_and_original_last_message(self) -> None:
        conversation = {"judgment": "unread", "followup_count": 0,
                        "followup_status": "none"}
        self.assertEqual(proposal(conversation, self.messages(), None, self.config, now=self.now),
                         (self.greeting, "100", 0))
        self.assertIsNone(proposal(conversation, self.messages(23), None, self.config, now=self.now))
        self.assertIsNone(proposal(conversation, self.messages(5 * 24), None,
                                   self.config, now=self.now))
        self.assertIsNone(proposal({**conversation, "judgment": "read_no_reply"},
                                   self.messages(status="1"), None,
                                   {**self.config, "followup_read_no_reply": False}, now=self.now))
        self.assertIsNone(proposal(conversation, self.messages() + [
            {"message_id": "101", "sender": "self", "text": "其他问题", "delivery_status": "1"}],
            None, self.config, now=self.now))
        self.assertIsNone(proposal(conversation, self.messages() + [
            {"message_id": "101", "sender": "recruiter", "text": "请发简历"}],
            None, self.config, now=self.now))

    def test_repeat_count_and_pending_state_block_duplicates(self) -> None:
        earlier = self.messages(50)
        latest = {**self.messages(25)[0], "message_id": "101"}
        conversation = {"judgment": "read_no_reply", "followup_count": 0,
                        "followup_status": "idle"}
        self.assertEqual(proposal(conversation, earlier + [latest], None,
                                  self.config, now=self.now), (self.greeting, "101", 1))
        self.assertIsNone(proposal({**conversation, "followup_count": 2}, earlier + [latest],
                                   None, self.config, now=self.now))
        self.assertIsNone(proposal({**conversation, "followup_status": "pending_review"},
                                   earlier + [latest], None, self.config, now=self.now))

    def test_unavailable_followup_has_specific_reason(self) -> None:
        conversation = {"judgment": "unread", "followup_count": 0,
                        "followup_status": "none"}
        self.assertIn("不足 24 小时", proposal_with_reason(
            conversation, self.messages(23), None, self.config, now=self.now)[1])
        self.assertIn("不是原招呼语", proposal_with_reason(
            conversation, self.messages() + [{"message_id": "101", "sender": "self",
                                              "text": "其他消息", "delivery_status": "1"}],
            None, self.config, now=self.now)[1])
        self.assertIn("上限", proposal_with_reason(
            {**conversation, "followup_count": 2}, self.messages(), None,
            self.config, now=self.now)[1])

    def test_list_availability_requires_existing_review_draft(self) -> None:
        with closing(sqlite3.connect(":memory:")) as conn:
            store = ConversationStore(conn)
            current = datetime.now(timezone.utc)
            message = {**self.messages()[0],
                       "sent_at": str(int((current - timedelta(hours=25)).timestamp() * 1000))}
            store.upsert({"platform": "boss", "conversation_id": "123",
                          "judgment": "unread"}, [message])
            rows, _ = store.list_conversations(limit=10, offset=0)
            annotate_followup_availability(conn, rows, self.config)
            self.assertFalse(rows[0]["followup_eligible"])
            self.assertIn("重新运行监测", rows[0]["followup_reason"])
            self.assertTrue(store.prepare_followup("boss", "123", self.greeting, "100"))
            rows, _ = store.list_conversations(limit=10, offset=0)
            annotate_followup_availability(conn, rows, self.config)
            self.assertTrue(rows[0]["followup_eligible"])
            self.assertEqual(rows[0]["followup_reason"], "")

    def test_review_send_counts_only_verified_delivery(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            with closing(sqlite3.connect(path)) as conn:
                store = ConversationStore(conn)
                store.upsert({"platform": "boss", "conversation_id": "123",
                              "judgment": "read_no_reply"}, self.messages(status="2"))
                self.assertTrue(store.prepare_followup("boss", "123", self.greeting, "100"))
                self.assertFalse(store.prepare_followup("boss", "123", self.greeting, "100"))
            with patch("api.followups.send_boss_followup", return_value=FollowupResult(
                    True, False, "已确认", "101")) as send:
                result = send_followup(path, "cdp", "boss", "123")
            send.assert_called_once_with("cdp", "123", "100", self.greeting)
            self.assertEqual(result["conversation"]["followup_count"], 1)
            self.assertEqual(result["conversation"]["conversation_status"], "unread")
            with self.assertRaises(JobActionError):
                send_followup(path, "cdp", "boss", "123")

    def test_uncertain_send_blocks_retry_without_counting(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            with closing(sqlite3.connect(path)) as conn:
                store = ConversationStore(conn)
                store.upsert({"platform": "boss", "conversation_id": "123",
                              "judgment": "unread"}, self.messages())
                store.prepare_followup("boss", "123", self.greeting, "100")
            with patch("api.followups.send_boss_followup", return_value=FollowupResult(
                    False, True, "结果不明")):
                with self.assertRaises(JobActionError):
                    send_followup(path, "cdp", "boss", "123")
            with closing(sqlite3.connect(path)) as conn:
                saved = ConversationStore(conn).get_conversation("boss", "123")
            self.assertEqual(saved["followup_status"], "unknown")
            self.assertEqual(saved["followup_count"], 0)

    def test_monitor_scan_creates_one_review_item_across_repeated_scans(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            observation = {
                "platform": "boss", "conversation_id": "123", "source_job_id": "",
                "job_title": "工程师", "company": "甲公司", "recruiter": "李女士",
                "chat_url": "https://www.zhipin.com/web/geek/chat?jobId=456",
                "judgment": "unread", "next_action": "manual_review",
                "evidence": "未读",
                "messages": self.messages(),
            }
            settings = {"message_limit": 10,
                        "followup_days": 4, "followup_cooldown_hours": 24,
                        "followup_max_count": 2, "followup_enabled": True,
                        "followup_unread": True, "followup_read_no_reply": True,
                        "followup_review_required": True, "filter_review_required": True}
            with patch("api.monitoring.scan_new_greetings", return_value=[]), \
                    patch("api.monitoring.scan_recent_conversations", return_value=[observation]) as scan:
                first = MonitoringRun(path)
                first._execute(settings)
                second = MonitoringRun(path)
                second._execute(settings)
            scan.assert_called_with(
                __import__("config").DEFAULT_CONFIG["browser"]["cdp_url"],
                message_limit=10,
                active_days=4,
                skip_conversation_ids=set(),
                on_progress=second._progress)
            with closing(sqlite3.connect(path)) as conn:
                saved = ConversationStore(conn).get_conversation("boss", "123")
            self.assertEqual(saved["followup_status"], "pending_review")
            self.assertEqual(saved["followup_text"], self.greeting)
            self.assertEqual(saved["followup_count"], 0)
            self.assertEqual(first.counts["followup_pending"], 1)
            self.assertEqual(second.counts["followup_pending"], 1)
            with patch("api.monitoring.scan_new_greetings", return_value=[]), \
                    patch("api.monitoring.scan_recent_conversations", return_value=[observation]), \
                    patch("api.followups.send_boss_followup") as send:
                MonitoringRun(path)._execute({**settings, "followup_review_required": False})
            send.assert_not_called()
            with patch("api.monitoring.scan_new_greetings", return_value=[]), \
                    patch("api.monitoring.scan_recent_conversations", return_value=[observation]):
                MonitoringRun(path)._execute({**settings, "followup_unread": False})
            with closing(sqlite3.connect(path)) as conn:
                saved = ConversationStore(conn).get_conversation("boss", "123")
            self.assertEqual(saved["followup_status"], "stale")


if __name__ == "__main__":
    unittest.main()
