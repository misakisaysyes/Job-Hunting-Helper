"""Only unanswered outbound conversations are monitored."""

import sqlite3
import unittest
from unittest.mock import MagicMock, patch
from contextlib import closing

from browser.boss_monitor import scan_recent_conversations
from browser.browser import ChromeBrowser
from api.conversations import delete_conversation_record, open_conversation, open_filter_candidate, terminate_monitoring
from api.jobs import JobActionError
from api.monitoring import MonitoringRun
from data.conversation_store import ConversationStore
from data.filter_store import FilterStore
from data.job_store import JobStore
from pathlib import Path
from tempfile import TemporaryDirectory


class MonitoringBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.store = ConversationStore(self.conn)

    def test_conversation_and_messages_are_deduplicated(self) -> None:
        conversation = {
            "platform": "boss", "conversation_id": "123", "source_job_id": "job-a",
            "company": "甲公司", "judgment": "unread",
        }
        message = {"message_id": "m1", "sender": "self", "text": "你好"}
        with patch("data.conversation_store._now", side_effect=["2026-10-01T10:00:00+00:00",
                                                                 "2026-10-02T10:00:00+00:00"]):
            self.assertTrue(self.store.upsert(conversation, [message]))
            first, total = self.store.list_conversations(limit=10, offset=0)
            self.assertTrue(self.store.upsert({**conversation, "company": "乙公司"}, [message]))
        second, total_again = self.store.list_conversations(limit=10, offset=0)
        self.assertEqual((total, total_again), (1, 1))
        self.assertEqual(second[0]["first_seen_at"], first[0]["first_seen_at"])
        self.assertEqual(second[0]["updated_at"], "2026-10-02T10:00:00+00:00")
        self.assertEqual(second[0]["company"], "乙公司")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM monitored_messages").fetchone()[0], 1)

    def test_rescan_updates_existing_message_read_receipt(self) -> None:
        conversation = {"platform": "boss", "conversation_id": "123", "judgment": "unread"}
        message = {"message_id": "m1", "sender": "self", "text": "你好",
                   "delivery_status": "1"}
        self.store.upsert(conversation, [message])
        self.store.upsert({**conversation, "judgment": "read_no_reply"},
                          [{**message, "delivery_status": "2"}])
        self.assertEqual(self.store.get_conversation("boss", "123")["judgment"],
                         "read_no_reply")
        self.assertEqual(self.conn.execute("""SELECT delivery_status FROM monitored_messages
            WHERE conversation_id = '123' AND message_id = 'm1'""").fetchone()[0], "2")

    def test_list_uses_first_original_greeting_even_without_followup_draft(self) -> None:
        self.store.upsert({"platform": "boss", "conversation_id": "123", "judgment": "unread"}, [
            {"message_id": "100", "sender": "self", "text": "您好，想了解岗位", "biz_type": "101"},
            {"message_id": "101", "sender": "self", "text": "您好，想了解岗位", "biz_type": "1"},
        ])
        rows, _ = self.store.list_conversations(limit=10, offset=0)
        self.assertEqual(rows[0]["greeting_text"], "您好，想了解岗位")

    def test_list_prefers_delivered_custom_greeting_over_boss_opening_card(self) -> None:
        jobs = JobStore(self.conn)
        jobs.save({"source_platform": "boss", "source_job_id": "job-a"})
        custom = "您好，快6年前端，做过金融系统和性能优化。"
        self.conn.execute("""UPDATE collected_jobs SET greeting = ?, greeting_send_state = 'sent'
            WHERE platform = 'boss' AND source_job_id = 'job-a'""", (custom,))
        self.store.upsert({"platform": "boss", "conversation_id": "123",
                           "source_job_id": "job-a", "judgment": "unread"}, [
            {"message_id": "100", "sender": "self", "text": "Boss您好，看到贵公司的招聘信息",
             "biz_type": "101", "delivery_status": "1"},
            {"message_id": "101", "sender": "self", "text": custom,
             "biz_type": "", "delivery_status": "1"},
        ])
        rows, _ = self.store.list_conversations(limit=10, offset=0)
        self.assertEqual(rows[0]["greeting_text"], custom)
        self.assertEqual(self.store.get_conversation("boss", "123")["greeting_text"], custom)
        self.assertEqual(rows[0]["followup_status"], "none")

    def test_chat_delivery_can_resolve_greeting_while_job_send_is_uncertain(self) -> None:
        JobStore(self.conn).save({"source_platform": "boss", "source_job_id": "job-a"})
        custom = "您好，有前端开发经验，希望投递简历。"
        self.conn.execute("""UPDATE collected_jobs SET greeting = ?, greeting_send_state = 'unknown'
            WHERE platform = 'boss' AND source_job_id = 'job-a'""", (custom,))
        self.store.upsert({"platform": "boss", "conversation_id": "123",
                           "source_job_id": "job-a", "judgment": "read_no_reply"}, [
            {"message_id": "100", "sender": "self", "text": custom,
             "delivery_status": "2", "biz_type": "0"},
        ])
        self.assertEqual(self.store.original_greeting("boss", "123", "job-a"), custom)
        self.assertEqual(self.conn.execute("SELECT greeting_send_state FROM collected_jobs").fetchone()[0],
                         "unknown")

    def test_initial_custom_greeting_survives_default_text_in_job_record(self) -> None:
        JobStore(self.conn).save({"source_platform": "boss", "source_job_id": "job-a"})
        preset = "Boss您好，看到贵公司的招聘信息，希望能得到贵公司的垂青，谢谢"
        custom = "您好，快6年前端，希望向您投递简历。"
        self.conn.execute("""UPDATE collected_jobs SET greeting = ?, greeting_send_state = 'sent'
            WHERE platform = 'boss' AND source_job_id = 'job-a'""", (preset,))
        self.store.upsert({"platform": "boss", "conversation_id": "123",
                           "source_job_id": "job-a", "judgment": "unread"}, [
            {"message_id": "100", "sender": "self", "text": preset, "biz_type": "101",
             "delivery_status": "1", "sent_at": "1791522968541"},
            {"message_id": "101", "sender": "recruiter", "text": "", "biz_type": "317"},
            {"message_id": "102", "sender": "self", "text": custom, "biz_type": "0",
             "delivery_status": "1", "sent_at": "1791523003425"},
        ])
        self.assertEqual(self.store.get_conversation("boss", "123")["greeting_text"], custom)

    def test_incoming_greeting_and_self_replies_are_not_an_outgoing_greeting(self) -> None:
        self.store.upsert({"platform": "boss", "conversation_id": "123",
                           "judgment": "read_no_reply"}, [
            {"message_id": "100", "sender": "recruiter", "text": "您好，考虑新机会吗？",
             "biz_type": "101", "delivery_status": "2"},
            {"message_id": "101", "sender": "self", "text": "谢谢，暂不考虑。",
             "delivery_status": "2", "biz_type": "0"},
        ])
        self.assertEqual(self.store.original_greeting("boss", "123"), "")

    def test_conversation_filters_apply_before_pagination(self) -> None:
        self.store.upsert({"platform": "boss", "conversation_id": "1", "job_title": "前端工程师",
                           "company": "甲公司", "judgment": "unread"}, [])
        self.store.upsert({"platform": "boss", "conversation_id": "2", "job_title": "后端工程师",
                           "company": "乙公司", "judgment": "read_no_reply"}, [])
        self.store.upsert({"platform": "boss", "conversation_id": "3", "job_title": "前端开发",
                           "company": "丙公司", "judgment": "unread"}, [])
        rows, total = self.store.list_conversations(
            limit=1, offset=1, query="前端",
            conversation_status="unread")
        self.assertEqual((total, len(rows)), (2, 1))
        self.assertIn(rows[0]["conversation_id"], {"1", "3"})
        self.conn.execute("""UPDATE monitored_conversations SET followup_count = 1
            WHERE conversation_id = '1'""")
        rows, total = self.store.list_conversations(
            limit=10, offset=0, followup_status="followed_up")
        self.assertEqual((total, rows[0]["conversation_id"]), (1, "1"))

    def test_only_two_statuses_can_be_saved_and_unseen_rows_leave_scope(self) -> None:
        conversation = {"platform": "boss", "conversation_id": "123", "judgment": "unread"}
        with self.assertRaises(ValueError):
            self.store.upsert({**conversation, "judgment": "manual_review"}, [])
        self.store.upsert(conversation, [], scan_token="first")
        first_seen_at = self.store.get_conversation("boss", "123")["first_seen_at"]
        self.assertEqual(self.store.deactivate_unseen("second"), 1)
        self.assertEqual(self.store.list_conversations(limit=10, offset=0)[1], 0)
        self.store.upsert({**conversation, "judgment": "read_no_reply"}, [],
                          scan_token="third")
        saved = self.store.get_conversation("boss", "123")
        self.assertEqual(saved["conversation_status"], "read_no_reply")
        self.assertEqual(saved["first_seen_at"], first_seen_at)
        self.assertTrue(saved["active"])

    def test_scan_reads_history_only_for_two_list_receipts(self) -> None:
        friends = [
            {"uid": "1", "list_receipt_status": "unread", "lastTS": 3},
            {"uid": "2", "list_receipt_status": "", "lastTS": 2},
            {"uid": "3", "list_receipt_status": "read_no_reply", "lastTS": 1},
        ]
        with patch("browser.boss_monitor.ChromeBrowser") as browser, \
                patch("browser.boss_monitor._tab_friends", return_value=friends) as list_friends, \
                patch("browser.boss_monitor._fetch_history", return_value=[]) as history:
            browser.return_value.new_tab.return_value = "tab"
            rows = scan_recent_conversations("cdp", message_limit=8, active_days=4)
        list_friends.assert_called_once_with(browser.return_value.page.return_value,
                                             "仅沟通", 4, None)
        self.assertEqual([row["conversation_id"] for row in rows], ["1", "3"])
        self.assertEqual([row["judgment"] for row in rows], ["unread", "read_no_reply"])
        self.assertTrue(all("hr_reply_status" not in row for row in rows))
        self.assertEqual(history.call_count, 2)

    def test_terminated_conversation_is_not_opened_or_reactivated(self) -> None:
        conversation = {"platform": "boss", "conversation_id": "1", "judgment": "unread"}
        self.store.upsert(conversation, [{"message_id": "1", "sender": "self", "text": "你好"}])
        self.store.prepare_followup("boss", "1", "你好", "1")
        self.assertTrue(self.store.terminate_monitoring("boss", "1"))
        self.assertEqual(self.store.terminated_ids("boss"), {"1"})
        self.assertIsNone(self.store.get_conversation("boss", "1"))
        self.assertFalse(self.store.upsert(conversation, [], scan_token="next"))
        self.assertEqual(self.store.list_conversations(limit=10, offset=0)[1], 0)
        self.assertIsNone(self.store.claim_followup("boss", "1", 2))
        self.assertEqual(self.conn.execute("SELECT followup_text FROM monitored_conversations").fetchone()[0], "")
        friends = [{"uid": "1", "list_receipt_status": "unread"},
                   {"uid": "2", "list_receipt_status": "read_no_reply"}]
        with patch("browser.boss_monitor.ChromeBrowser") as browser, \
                patch("browser.boss_monitor._tab_friends", return_value=friends), \
                patch("browser.boss_monitor._fetch_history", return_value=[]) as history:
            browser.return_value.new_tab.return_value = "tab"
            rows = scan_recent_conversations("cdp", message_limit=8,
                                             skip_conversation_ids=self.store.terminated_ids("boss"))
        self.assertEqual([row["conversation_id"] for row in rows], ["2"])
        self.assertEqual(history.call_count, 1)

    def test_terminate_api_persists_across_connections(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            with closing(sqlite3.connect(path)) as conn:
                ConversationStore(conn).upsert({"platform": "boss", "conversation_id": "123",
                                                "judgment": "unread"}, [])
            self.assertIn("终止", terminate_monitoring(path, "boss", "123")["message"])
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(ConversationStore(conn).terminated_ids("boss"), {"123"})
            with self.assertRaises(JobActionError):
                terminate_monitoring(path, "boss", "123")

    def test_delete_record_removes_local_conversation_and_messages(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            conversation = {"platform": "boss", "conversation_id": "123", "judgment": "unread"}
            with closing(sqlite3.connect(path)) as conn:
                ConversationStore(conn).upsert(conversation, [
                    {"message_id": "m1", "sender": "self", "text": "您好"}])
            self.assertTrue(delete_conversation_record(path, "boss", "123")["deleted"])
            with closing(sqlite3.connect(path)) as conn:
                store = ConversationStore(conn)
                self.assertEqual(store.list_conversations(limit=10, offset=0)[1], 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM monitored_messages").fetchone()[0], 0)
                # A later scan may discover the same BOSS conversation again.
                self.assertTrue(store.upsert(conversation, [], scan_token="next"))
            with self.assertRaises(JobActionError) as error:
                delete_conversation_record(path, "boss", "missing")
            self.assertEqual(error.exception.status_code, 404)

    def test_delete_record_rejects_in_flight_followup(self) -> None:
        self.store.upsert({"platform": "boss", "conversation_id": "123", "judgment": "unread"}, [])
        self.conn.execute("""UPDATE monitored_conversations SET followup_status = 'sending'
            WHERE conversation_id = '123'""")
        self.conn.commit()
        self.assertEqual(self.store.delete_record("boss", "123"), "busy")
        self.assertIsNotNone(self.store.get_conversation("boss", "123"))

    def test_monitoring_run_passes_terminated_ids_to_list_scanner(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            with closing(sqlite3.connect(path)) as conn:
                store = ConversationStore(conn)
                store.upsert({"platform": "boss", "conversation_id": "123",
                              "judgment": "unread"}, [])
                store.terminate_monitoring("boss", "123")
            with patch("api.monitoring.scan_recent_conversations", return_value=[]) as scan, \
                    patch("api.monitoring.scan_new_greetings", return_value=[]):
                run = MonitoringRun(path)
                run._execute()
            self.assertEqual(run.snapshot()["status"], "completed")
            self.assertEqual(scan.call_args.kwargs["skip_conversation_ids"], {"123"})

    def test_monitoring_reports_each_scan_stage_separately(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            run = MonitoringRun(path)

            def scan_communicated(*args, on_progress, **kwargs):
                on_progress("已扫描「仅沟通」列表 8 条")
                state = run.snapshot()
                self.assertEqual(state["phases"]["communicated"]["status"], "scanning")
                self.assertIn("8 条", state["phases"]["communicated"]["message"])
                self.assertEqual(state["phases"]["new_greetings"]["status"], "pending")
                return []

            def scan_greetings(*args, on_progress, **kwargs):
                on_progress("已扫描「新招呼」列表 3 条")
                state = run.snapshot()
                self.assertEqual(state["phases"]["new_greetings"]["status"], "scanning")
                self.assertIn("3 条", state["phases"]["new_greetings"]["message"])
                self.assertEqual(state["phases"]["communicated"]["status"], "processing")
                return []

            with patch("api.monitoring.scan_recent_conversations", side_effect=scan_communicated), \
                    patch("api.monitoring.scan_new_greetings", side_effect=scan_greetings):
                run._execute()
            state = run.snapshot()
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["phases"]["communicated"]["status"], "completed")
            self.assertEqual(state["phases"]["new_greetings"]["status"], "completed")
            self.assertEqual(state["counts"]["new_greetings_scanned"], 0)

    def test_monitoring_marks_the_failed_scan_stage(self) -> None:
        with TemporaryDirectory() as directory:
            run = MonitoringRun(Path(directory) / "state.sqlite")
            with patch("api.monitoring.scan_recent_conversations",
                       side_effect=RuntimeError("读取会话失败")):
                run._execute()
            phases = run.snapshot()["phases"]
            self.assertEqual(phases["communicated"]["status"], "failed")
            self.assertEqual(phases["new_greetings"]["status"], "pending")

    def test_legacy_reply_column_is_removed_and_old_rows_require_rescan(self) -> None:
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.execute("""CREATE TABLE monitored_conversations (
                platform TEXT NOT NULL, conversation_id TEXT NOT NULL,
                source_job_id TEXT NOT NULL DEFAULT '', job_title TEXT NOT NULL DEFAULT '',
                company TEXT NOT NULL DEFAULT '', recruiter TEXT NOT NULL DEFAULT '',
                chat_url TEXT NOT NULL DEFAULT '', in_job_pool INTEGER NOT NULL DEFAULT 0,
                judgment TEXT NOT NULL DEFAULT 'manual_review',
                hr_reply_status TEXT NOT NULL DEFAULT 'no_reply',
                next_action TEXT NOT NULL DEFAULT 'manual_review',
                evidence TEXT NOT NULL DEFAULT '', first_seen_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, last_scanned_at TEXT NOT NULL,
                PRIMARY KEY (platform, conversation_id))""")
            conn.execute("""INSERT INTO monitored_conversations
                (platform, conversation_id, judgment, first_seen_at, updated_at, last_scanned_at)
                VALUES ('boss', 'old', 'unread', 'first', 'first', 'first')""")
            store = ConversationStore(conn)
            columns = {row[1] for row in conn.execute(
                "PRAGMA table_info(monitored_conversations)")}
            self.assertNotIn("hr_reply_status", columns)
            self.assertEqual(store.list_conversations(limit=10, offset=0)[1], 0)
            store.upsert({"platform": "boss", "conversation_id": "old",
                          "judgment": "read_no_reply"}, [], scan_token="fresh")
            rows, total = store.list_conversations(limit=10, offset=0)
            self.assertEqual((total, rows[0]["first_seen_at"]), (1, "first"))

    def test_open_conversation_selects_target_in_communication_list(self) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "state.sqlite3"
            with closing(sqlite3.connect(db_path)) as conn:
                store = ConversationStore(conn)
                store.upsert({"platform": "boss", "conversation_id": "123",
                              "judgment": "unread",
                              "chat_url": "https://www.zhipin.com/web/geek/chat?jobId=456"}, [])
            with patch("api.conversations.ChromeBrowser") as browser:
                browser.return_value.open_user_conversation.return_value = True
                response = open_conversation(db_path, {"browser": {"cdp_url": "cdp"}},
                                             "boss", "123")
                self.assertIn("仅沟通", response["message"])
                self.assertIn("打开", response["message"])
                browser.assert_called_once_with("cdp")
                browser.return_value.open_user_conversation.assert_called_once_with(
                    "https://www.zhipin.com/web/geek/chat", "123", "仅沟通")
                browser.return_value.locate_user_conversation.assert_not_called()
                browser.return_value.close.assert_called_once()
                with self.assertRaises(JobActionError):
                    open_conversation(db_path, {"browser": {"cdp_url": "cdp"}},
                                      "boss", "missing")

    def test_open_conversation_returns_error_when_target_cannot_be_verified(self) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "state.sqlite3"
            with closing(sqlite3.connect(db_path)) as conn:
                ConversationStore(conn).upsert({"platform": "boss", "conversation_id": "123",
                                               "judgment": "unread"}, [])
            with patch("api.conversations.ChromeBrowser") as browser:
                browser.return_value.open_user_conversation.return_value = False
                with self.assertRaises(JobActionError) as error:
                    open_conversation(db_path, {"browser": {"cdp_url": "cdp"}}, "boss", "123")
                self.assertEqual(error.exception.status_code, 409)
                browser.return_value.close.assert_called_once()

    def test_locator_highlights_row_without_clicking_it(self) -> None:
        adapter = object.__new__(ChromeBrowser)
        page = MagicMock()
        page.url = "https://www.zhipin.com/web/geek/chat"
        response = MagicMock()
        response.url = "https://www.zhipin.com/wapi/zprelation/friend/getGeekFriendList.json"
        response.json.return_value = {"code": 0, "zpData": {"result": [
            {"uid": "123", "avatar": "https://example.com/avatar.png"}]}}
        page.on.side_effect = lambda event, callback: callback(response)
        page.locator.return_value.filter.return_value.first.get_attribute.return_value = "selected"
        page.evaluate.return_value = [0]
        adapter._context = MagicMock()
        adapter._context.new_page.return_value = page
        self.assertTrue(adapter.locate_user_conversation("123", "仅沟通"))
        page.locator.return_value.filter.return_value.first.click.assert_called_once()
        page.locator.return_value.nth.return_value.scroll_into_view_if_needed.assert_called_once()
        page.locator.return_value.nth.return_value.click.assert_not_called()

    def test_open_filter_candidate_locates_new_greeting_without_entering_chat(self) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "state.sqlite3"
            with closing(sqlite3.connect(db_path)) as conn:
                FilterStore(conn).record_match({"platform": "boss", "conversation_id": "123",
                                                "company": "甲公司"}, "甲", "scan")
            with patch("api.conversations.ChromeBrowser") as browser:
                browser.return_value.locate_user_conversation.return_value = True
                result = open_filter_candidate(db_path, {"browser": {"cdp_url": "cdp"}},
                                               "boss", "123")
            self.assertIn("新招呼", result["message"])
            browser.return_value.locate_user_conversation.assert_called_once_with(
                "123", "新招呼")
            browser.return_value.open_user_conversation.assert_not_called()

    def test_monitoring_keeps_job_status_and_retires_conversation_outside_scope(self) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "state.sqlite3"
            with closing(sqlite3.connect(db_path)) as conn:
                jobs = JobStore(conn)
                jobs.save({"source_platform": "boss", "source_job_id": "job-a"})
                jobs.set_status("boss", "job-a", "greeted")
            observation = {"platform": "boss", "conversation_id": "123",
                           "source_job_id": "job-a", "job_title": "工程师",
                           "company": "甲公司", "recruiter": "李女士",
                           "chat_url": "https://www.zhipin.com/web/geek/chat?jobId=456",
                           "judgment": "unread", "next_action": "none",
                           "evidence": "「仅沟通」列表显示[送达]", "messages": []}
            with patch("api.monitoring.scan_new_greetings", return_value=[]), \
                    patch("api.monitoring.scan_recent_conversations", return_value=[observation]):
                run = MonitoringRun(db_path)
                run._execute()
            self.assertEqual(run.snapshot()["status"], "completed")
            with closing(sqlite3.connect(db_path)) as conn:
                self.assertEqual(JobStore(conn).get_job("boss", "job-a")["job_status"],
                                 "greeted")
                rows, total = ConversationStore(conn).list_conversations(limit=10, offset=0)
                self.assertEqual((total, rows[0]["conversation_status"]), (1, "unread"))
            with patch("api.monitoring.scan_new_greetings", return_value=[]), \
                    patch("api.monitoring.scan_recent_conversations", return_value=[]):
                run = MonitoringRun(db_path)
                run._execute()
            self.assertEqual(run.snapshot()["status"], "completed")
            self.assertEqual(run.counts["left_scope"], 1)
            with closing(sqlite3.connect(db_path)) as conn:
                self.assertEqual(JobStore(conn).get_job("boss", "job-a")["job_status"],
                                 "greeted")
                self.assertEqual(ConversationStore(conn).list_conversations(
                    limit=10, offset=0)[1], 0)


if __name__ == "__main__":
    unittest.main()
