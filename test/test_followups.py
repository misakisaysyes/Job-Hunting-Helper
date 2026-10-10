"""Follow-up candidates, duplicate prevention, and send outcomes."""

from __future__ import annotations

from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from ai import AIRequestError, AITaskScheduler
from api.followups import (FollowupSkipped, annotate_followup_availability, generate_followup, get_followup,
                           list_conversations, proposal, proposal_with_reason, save_followup, send_followup)
from api.jobs import JobActionError
from api.monitoring import MonitoringRun
from browser.boss_followup import FollowupResult, send_boss_followup
from data.conversation_store import ConversationStore, resolve_original_greeting
from data.job_store import JobStore
from config import DEFAULT_CONFIG


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

    def test_default_uses_actual_sent_greeting_instead_of_a_job_draft(self) -> None:
        conversation = {"judgment": "unread", "followup_status": "none", "followup_count": 0}
        job = {"greeting": "后来修改但没有发送的草稿", "greeting_send_state": "idle"}
        self.assertEqual(proposal(conversation, self.messages(), job, self.config, now=self.now),
                         (self.greeting, "100", 0))

    def test_followup_copies_custom_greeting_after_boss_default_opening(self) -> None:
        conversation = {"judgment": "unread", "followup_status": "none", "followup_count": 0}
        opening = {**self.messages(26)[0], "text": "Boss您好，看到贵公司的招聘信息"}
        custom = {**self.messages(25)[0], "message_id": "101", "biz_type": ""}
        for state in ("sent", "unknown"):
            with self.subTest(state=state):
                self.assertEqual(proposal(conversation, [opening, custom],
                    {"greeting": self.greeting, "greeting_send_state": state}, self.config, now=self.now),
                    (self.greeting, "101", 0))

    def test_unconfirmed_greeting_must_be_delivered_by_self_to_override_opening(self) -> None:
        job = {"greeting": "保存但未确认发送的内容", "greeting_send_state": "idle"}
        conversation = {"judgment": "unread", "followup_status": "none", "followup_count": 0}
        for sender, status in (("self", "0"), ("self", "3"), ("self", "4"), ("recruiter", "2")):
            with self.subTest(sender=sender, status=status):
                messages = [{**self.messages(26)[0], "text": job["greeting"], "biz_type": "",
                             "sender": sender, "delivery_status": status},
                            {**self.messages(25)[0], "message_id": "101"}]
                self.assertEqual(proposal(conversation, messages, job, self.config, now=self.now),
                                 (self.greeting, "101", 0))

    def test_initial_custom_send_is_used_when_job_still_records_boss_default(self) -> None:
        preset = "Boss您好，看到贵公司的招聘信息，希望能得到贵公司的垂青，谢谢"
        opening = {**self.messages(25)[0], "text": preset}
        custom = {**opening, "message_id": "102", "biz_type": "", "text": self.greeting,
                  "sent_at": str(int(opening["sent_at"]) + 35_000)}
        card = {"sender": "recruiter", "biz_type": "317", "text": ""}
        job = {"greeting": preset, "greeting_send_state": "sent"}
        conversation = {"judgment": "unread", "followup_status": "none", "followup_count": 0}
        self.assertEqual(resolve_original_greeting([opening, card, custom], job), self.greeting)
        self.assertEqual(proposal(conversation, [opening, card, custom], job, self.config, now=self.now),
                         (self.greeting, "102", 0))

    def test_later_reply_failed_send_or_unknown_time_does_not_replace_boss_opening(self) -> None:
        preset = "Boss您好，看到贵公司的招聘信息，希望能得到贵公司的垂青，谢谢"
        opening = {**self.messages(25)[0], "text": preset}
        custom = {**opening, "message_id": "102", "biz_type": "0", "text": self.greeting,
                  "sent_at": str(int(opening["sent_at"]) + 35_000)}
        recruiter = {**custom, "message_id": "101", "sender": "recruiter", "text": "请发简历"}
        sequences = [
            [opening, recruiter, custom],
            [opening, {**custom, "sent_at": str(int(opening["sent_at"]) + 121_000)}],
            [opening, {**custom, "sent_at": "unknown"}],
            [opening, {**custom, "delivery_status": "3"}],
        ]
        for messages in sequences:
            with self.subTest(messages=messages):
                self.assertEqual(resolve_original_greeting(messages, None), preset)

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

    def test_list_cooldown_deadline_uses_latest_delivered_greeting(self) -> None:
        for milliseconds in (True, False):
            with self.subTest(milliseconds=milliseconds), closing(sqlite3.connect(":memory:")) as conn:
                store = ConversationStore(conn)
                sent_at = self.now - timedelta(hours=1)
                latest = {**self.messages(1)[0], "message_id": "101", "biz_type": "1",
                          "sent_at": str(int(sent_at.timestamp() * (1000 if milliseconds else 1)))}
                card = {"message_id": "102", "sender": "recruiter", "text": "岗位卡片",
                        "biz_type": "317", "sent_at": str(int(self.now.timestamp() * 1000))}
                store.upsert({"platform": "boss", "conversation_id": "123", "judgment": "unread"},
                             self.messages(25) + [latest, card])
                rows, _ = store.list_conversations(limit=10, offset=0)
                annotate_followup_availability(conn, rows, self.config, now=self.now)
                self.assertFalse(rows[0]["followup_eligible"])
                self.assertIn("不足 24 小时", rows[0]["followup_reason"])
                self.assertEqual(rows[0]["followup_cooldown_until"],
                                 (sent_at + timedelta(hours=24)).isoformat())
                annotate_followup_availability(conn, rows, self.config,
                                               now=sent_at + timedelta(hours=24, seconds=1))
                self.assertEqual(rows[0]["followup_cooldown_until"], "")
                self.assertIn("重新运行监测", rows[0]["followup_reason"])

    def test_pending_draft_is_disabled_until_updated_cooldown_expires(self) -> None:
        with closing(sqlite3.connect(":memory:")) as conn:
            store = ConversationStore(conn)
            store.upsert({"platform": "boss", "conversation_id": "123", "judgment": "unread"},
                         self.messages(25))
            self.assertTrue(store.prepare_followup("boss", "123", self.greeting, "100"))
            rows, _ = store.list_conversations(limit=10, offset=0)
            config = {**self.config, "followup_cooldown_hours": 48}
            deadline = self.now + timedelta(hours=23)
            annotate_followup_availability(conn, rows, config, now=self.now)
            self.assertFalse(rows[0]["followup_eligible"])
            self.assertEqual(rows[0]["followup_cooldown_until"], deadline.isoformat())
            annotate_followup_availability(conn, rows, config, now=deadline + timedelta(seconds=1))
            self.assertTrue(rows[0]["followup_eligible"])
            self.assertEqual(rows[0]["followup_cooldown_until"], "")
            rows[0]["followup_count"] = 2
            annotate_followup_availability(conn, rows, config, now=self.now)
            self.assertEqual(rows[0]["followup_cooldown_until"], deadline.isoformat())
            self.assertIn("上限", rows[0]["followup_reason"])

    def test_availability_filters_before_pagination_and_excludes_blocked_rows(self) -> None:
        with closing(sqlite3.connect(":memory:")) as conn:
            store = ConversationStore(conn)
            for identity, hours, status in (
                    ("1", 25, "pending_review"), ("2", 1, "none"),
                    ("3", 25, "pending_review"), ("4", 2, "pending_review"),
                    ("5", 26, "pending_review"), ("6", 25, "none"), ("7", 25, "unknown")):
                store.upsert({"platform": "boss", "conversation_id": identity,
                              "job_title": "后端工程师" if identity == "4" else "前端工程师",
                              "judgment": "read_no_reply" if identity == "3" else "unread"},
                             self.messages(hours))
                if status == "pending_review":
                    self.assertTrue(store.prepare_followup("boss", identity, self.greeting, "100"))
                conn.execute("""UPDATE monitored_conversations
                    SET followup_status = ?, followup_count = ?, updated_at = ?
                    WHERE conversation_id = ?""",
                    (status, 2 if identity == "1" else 0,
                     f"2026-10-06T12:00:0{identity}+00:00", identity))
            rows, total = list_conversations(conn, self.config, limit=1, offset=1,
                                            followup_status="available", now=self.now)
            self.assertEqual((total, [row["conversation_id"] for row in rows]), (2, ["3"]))
            self.assertTrue(rows[0]["followup_eligible"])
            rows, total = list_conversations(conn, self.config, limit=1, offset=1,
                                            followup_status="cooling", now=self.now)
            self.assertEqual((total, [row["conversation_id"] for row in rows]), (2, ["2"]))
            self.assertTrue(rows[0]["followup_cooldown_until"])
            rows, total = list_conversations(conn, self.config, limit=10, offset=0,
                query="前端", conversation_status="read_no_reply",
                followup_status="available", now=self.now)
            self.assertEqual((total, [row["conversation_id"] for row in rows]), (1, ["3"]))
            rows, total = list_conversations(conn, self.config, limit=10, offset=0,
                query="前端", followup_status="cooling", now=self.now)
            self.assertEqual((total, [row["conversation_id"] for row in rows]), (1, ["2"]))
            rows, total = list_conversations(conn, self.config, limit=10, offset=0,
                followup_status="followed_up", now=self.now)
            self.assertEqual((total, [row["conversation_id"] for row in rows]), (1, ["1"]))
            rows, total = list_conversations(conn, {**self.config, "followup_enabled": False},
                limit=10, offset=0, followup_status="available", now=self.now)
            self.assertEqual((rows, total), ([], 0))

    def test_availability_filters_update_when_cooldown_expires(self) -> None:
        with closing(sqlite3.connect(":memory:")) as conn:
            store = ConversationStore(conn)
            store.upsert({"platform": "boss", "conversation_id": "123", "judgment": "unread"},
                         self.messages(25))
            self.assertTrue(store.prepare_followup("boss", "123", self.greeting, "100"))
            config = {**self.config, "followup_cooldown_hours": 48}
            deadline = self.now + timedelta(hours=23)
            for now, available, cooling in ((deadline, 0, 1),
                                           (deadline + timedelta(seconds=1), 1, 0)):
                with self.subTest(now=now):
                    self.assertEqual(list_conversations(conn, config, limit=10, offset=0,
                        followup_status="available", now=now)[1], available)
                    self.assertEqual(list_conversations(conn, config, limit=10, offset=0,
                        followup_status="cooling", now=now)[1], cooling)

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
            send.assert_called_once_with("cdp", "123", "100", self.greeting, anchor_text=self.greeting)
            self.assertEqual(result["conversation"]["followup_count"], 1)
            self.assertEqual(result["conversation"]["conversation_status"], "unread")
            with self.assertRaises(JobActionError):
                send_followup(path, "cdp", "boss", "123")

    def test_browser_missing_target_is_a_skip_before_any_send_attempt(self) -> None:
        browser = Mock()
        page = browser.page.return_value
        page.locator.return_value.filter.return_value.first.get_attribute.return_value = "selected"
        with patch("browser.boss_followup.ChromeBrowser", return_value=browser), \
                patch("browser.boss_followup._fetch_history") as history:
            result = send_boss_followup("cdp", "123", "100", self.greeting)
        self.assertTrue(result.skipped)
        self.assertFalse(result.verified)
        self.assertFalse(result.uncertain)
        self.assertIn("未找到目标会话", result.message)
        history.assert_not_called()
        page.locator.return_value.first.fill.assert_not_called()
        browser.close.assert_called_once()

    def test_missing_target_preserves_draft_without_counting_or_marking_failed(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            self.prepare_review(path)
            message = "「仅沟通」中未找到目标会话，已跳过，未发送"
            with patch("api.followups.send_boss_followup", return_value=FollowupResult(
                    False, False, message, skipped=True)):
                with self.assertRaisesRegex(FollowupSkipped, "未找到目标会话"):
                    send_followup(path, "cdp", "boss", "123")
            with closing(sqlite3.connect(path)) as conn:
                saved = ConversationStore(conn).get_conversation("boss", "123")
                self.assertEqual(saved["followup_status"], "pending_review")
                self.assertEqual(saved["followup_count"], 0)
                self.assertEqual(saved["followup_text"], self.greeting)
                self.assertEqual(saved["followup_anchor_id"], "100")
                self.assertEqual(saved["followup_last_sent_at"], "")
                self.assertEqual(saved["followup_error"], message)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM monitored_messages").fetchone()[0], 1)
                self.assertIsNotNone(ConversationStore(conn).claim_followup("boss", "123", 2))

    def test_other_unsent_errors_are_not_skipped_based_on_message_text(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            self.prepare_review(path)
            with patch("api.followups.send_boss_followup", return_value=FollowupResult(
                    False, False, "未找到目标会话页输入框")):
                with self.assertRaises(JobActionError) as error:
                    send_followup(path, "cdp", "boss", "123")
            self.assertNotIsInstance(error.exception, FollowupSkipped)
            self.assertEqual(error.exception.status_code, 502)
            self.assertEqual(get_followup(path, "boss", "123")["conversation"]["followup_status"], "failed")

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
        # This scan uses the real clock; keep its fixture inside the monitoring window.
        self.now = datetime.now(timezone.utc)
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
            save_followup(path, "boss", "123", "手动编辑后的追问", "100")
            with patch("api.monitoring.scan_new_greetings", return_value=[]), \
                    patch("api.monitoring.scan_recent_conversations", return_value=[observation]), \
                    patch("api.followups.send_boss_followup") as send:
                MonitoringRun(path)._execute({**settings, "followup_review_required": False})
            send.assert_not_called()
            self.assertEqual(get_followup(path, "boss", "123")["conversation"]["followup_text"], "手动编辑后的追问")
            with patch("api.monitoring.scan_new_greetings", return_value=[]), \
                    patch("api.monitoring.scan_recent_conversations", return_value=[observation]):
                MonitoringRun(path)._execute({**settings, "followup_unread": False})
            with closing(sqlite3.connect(path)) as conn:
                saved = ConversationStore(conn).get_conversation("boss", "123")
            self.assertEqual(saved["followup_status"], "stale")

    def prepare_review(self, path: Path, *, with_job: bool = False) -> dict:
        config = deepcopy(DEFAULT_CONFIG)
        config["ai"]["greeting_user_prompt"] = "招呼语和追问均突出真实的性能优化工作"
        resume = path.with_suffix(".md")
        resume.write_text("工作项目：React前端工程化、性能优化。", encoding="utf-8")
        config["profile"]["resume_path"] = str(resume)
        with closing(sqlite3.connect(path)) as conn:
            store = ConversationStore(conn)
            store.upsert({"platform": "boss", "conversation_id": "123", "judgment": "unread",
                          "source_job_id": "job" if with_job else ""}, self.messages())
            store.prepare_followup("boss", "123", self.greeting, "100")
            if with_job:
                JobStore(conn).save({"source_platform": "boss", "source_job_id": "job",
                                     "title": "前端工程师", "jd": "负责React前端工程化与性能优化"})
        return config

    def test_manual_edit_persists_and_original_greeting_is_preserved(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            self.prepare_review(path)
            result = save_followup(path, "boss", "123", "  您好，请问岗位还在招聘吗？  ", "100",
                                   expected_text=self.greeting)
            self.assertEqual(result["conversation"]["followup_text"], "您好，请问岗位还在招聘吗？")
            self.assertEqual(get_followup(path, "boss", "123")["conversation"]["greeting_text"], self.greeting)
            with self.assertRaisesRegex(JobActionError, "已变化"):
                save_followup(path, "boss", "123", "旧页面编辑", "100", expected_text=self.greeting)
            with self.assertRaises(JobActionError):
                save_followup(path, "boss", "123", "新消息", "999")
            for text in ["", "   ", "字" * 301]:
                with self.subTest(text_length=len(text)), self.assertRaises(JobActionError):
                    save_followup(path, "boss", "123", text, "100")

    def test_send_uses_unsaved_edited_text_and_checks_original_anchor_separately(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            self.prepare_review(path)
            edited = "您好，再向您了解一下岗位进展。"
            with patch("api.followups.send_boss_followup", return_value=FollowupResult(
                    True, False, "已确认", "101")) as send:
                result = send_followup(path, "cdp", "boss", "123", text=edited,
                                       anchor_id="100", expected_text=self.greeting)
            send.assert_called_once_with("cdp", "123", "100", edited, anchor_text=self.greeting)
            self.assertEqual(result["conversation"]["followup_count"], 1)
            self.assertEqual(result["conversation"]["followup_text"], "")
            self.assertEqual(result["conversation"]["last_followup_text"], edited)
            self.assertFalse(result["conversation"]["followup_eligible"])
            self.assertTrue(result["conversation"]["followup_cooldown_until"])
            reloaded = get_followup(path, "boss", "123")["conversation"]
            self.assertEqual(reloaded["last_followup_text"], edited)
            self.assertEqual(reloaded["greeting_text"], self.greeting)
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute("SELECT text FROM monitored_messages WHERE message_id = '101'").fetchone()[0], edited)
                self.assertEqual(ConversationStore(conn).original_greeting("boss", "123"), self.greeting)

    def test_conversation_details_include_only_the_linked_job_description(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            self.prepare_review(path, with_job=True)
            with closing(sqlite3.connect(path)) as conn:
                JobStore(conn).save({"source_platform": "boss", "source_job_id": "job",
                                     "title": "前端工程师", "jd": "负责React前端工程化与性能优化",
                                     "url": "https://www.zhipin.com/job_detail/job.html",
                                     "hr_name": "李女士", "recruitment_type": "experienced",
                                     "company_size": "100-499人"})
                JobStore(conn).save({"source_platform": "boss", "source_job_id": "other",
                                     "jd": "不属于当前会话的岗位描述"})
            conversation = get_followup(path, "boss", "123")["conversation"]
            self.assertEqual(conversation["job"]["jd"], "负责React前端工程化与性能优化")
            self.assertEqual(conversation["job"]["hr_name"], "李女士")
            self.assertEqual(conversation["job"]["recruitment_type"], "experienced")
            self.assertEqual(conversation["job"]["company_size"], "100-499人")
            with closing(sqlite3.connect(path)) as conn:
                conn.execute("UPDATE monitored_conversations SET source_job_id = 'missing'")
                conn.commit()
            missing = get_followup(path, "boss", "123")["conversation"]
            self.assertEqual(missing["job"]["jd"], "")
            self.assertFalse(missing["followup_ai_available"])

    def test_ai_generation_reuses_greeting_resume_jd_and_prompt(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            config = self.prepare_review(path, with_job=True)
            captured = []
            def model_call(system, user):
                captured.append(json.loads(user))
                return "您好，我有React前端工程化和性能优化经验，想向您投份简历。"
            with AITaskScheduler(config, model_call=model_call) as scheduler:
                result = generate_followup(path, config, scheduler, "boss", "123", "100",
                                           expected_text=self.greeting)
            self.assertEqual(captured[0]["用户补充要求"], config["ai"]["greeting_user_prompt"])
            self.assertIn("性能优化", captured[0]["简历"])
            self.assertIn("React", captured[0]["岗位"]["jd"])
            self.assertEqual(result["conversation"]["followup_text"], result["text"])
            self.assertEqual(result["conversation"]["greeting_text"], self.greeting)

    def test_ai_failure_or_concurrent_edit_does_not_replace_draft(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            config = self.prepare_review(path, with_job=True)
            with AITaskScheduler(config, model_call=lambda *_: "") as scheduler:
                with self.assertRaises(AIRequestError):
                    generate_followup(path, config, scheduler, "boss", "123", "100")
            self.assertEqual(get_followup(path, "boss", "123")["conversation"]["followup_text"], self.greeting)
            def model_call(*_):
                save_followup(path, "boss", "123", "其他窗口保存的草稿", "100")
                return "AI生成的新草稿"
            with AITaskScheduler(config, model_call=model_call) as scheduler:
                with self.assertRaisesRegex(JobActionError, "已变化"):
                    generate_followup(path, config, scheduler, "boss", "123", "100")
            self.assertEqual(get_followup(path, "boss", "123")["conversation"]["followup_text"], "其他窗口保存的草稿")

    def test_outside_job_pool_can_edit_but_cannot_invent_ai_context(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            config = self.prepare_review(path)
            with AITaskScheduler(config, model_call=lambda *_: self.fail("不应调用AI")) as scheduler:
                with self.assertRaisesRegex(JobActionError, "未采集"):
                    generate_followup(path, config, scheduler, "boss", "123", "100")
            save_followup(path, "boss", "123", "手动追问内容", "100")

    def test_confirmed_edited_followup_can_be_followed_up_after_cooldown(self) -> None:
        earlier = self.messages(50)
        latest = {**self.messages(25)[0], "message_id": "101", "biz_type": "1", "text": "编辑后的追问"}
        conversation = {"judgment": "read_no_reply", "followup_count": 1,
                        "followup_status": "idle", "followup_anchor_id": "101"}
        self.assertEqual(proposal(conversation, earlier + [latest], None, self.config, now=self.now),
                         (self.greeting, "101", 0))
        self.assertIsNone(proposal({**conversation, "followup_count": 2}, earlier + [latest], None,
                                   self.config, now=self.now))
        recent = {**latest, "sent_at": self.messages(1)[0]["sent_at"]}
        self.assertIsNone(proposal(conversation, earlier + [recent], None, self.config, now=self.now))

    def test_draft_cannot_be_changed_or_sent_after_claim_or_source_change(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            self.prepare_review(path)
            with patch("api.followups.send_boss_followup") as send:
                with self.assertRaises(JobActionError):
                    send_followup(path, "cdp", "boss", "123", anchor_id="999", text="新文本")
                send.assert_not_called()
            with closing(sqlite3.connect(path)) as conn:
                ConversationStore(conn).claim_followup("boss", "123", 2)
            with self.assertRaises(JobActionError):
                save_followup(path, "boss", "123", "发送中不可编辑", "100")


if __name__ == "__main__":
    unittest.main()
