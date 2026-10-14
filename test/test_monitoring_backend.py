"""Only unanswered outbound conversations are monitored."""

import sqlite3
from datetime import datetime, timedelta, timezone
import unittest
from copy import deepcopy
from unittest.mock import MagicMock, patch
from contextlib import closing
from threading import Event

from browser.boss_monitor import (CHAT_URL, RESUME_DELIVERED, RESUME_VIEWED, _resume_delivery_evidence,
                                  _tab_friends, scan_exchanged_conversations,
                                  scan_new_greetings, scan_recent_conversations)
from browser.browser import ChromeBrowser
from browser.boss_filter import FilterDeleteResult
from api.conversations import (delete_conversation_record, delete_exchanged_conversation,
                               open_conversation, open_exchanged_conversation,
                               open_filter_candidate, terminate_monitoring)
from api.jobs import JobActionError
from api.monitoring import MonitoringRun
from api.server import make_handler
from data.conversation_store import ConversationStore
from data.exchange_store import ExchangeStore
from data.filter_store import FilterStore
from data.job_store import JobStore
from data.task_run_store import TaskRunStore
from config import DEFAULT_CONFIG, MONITORING_CONFIG
from pathlib import Path
from tempfile import TemporaryDirectory


class MonitoringBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        browser_config = patch.dict(DEFAULT_CONFIG["browser"], {"silent_background": False})
        browser_config.start()
        self.addCleanup(browser_config.stop)
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

    def test_exchanged_tab_opens_more_before_selecting_exchange(self) -> None:
        page = MagicMock()
        page.url = CHAT_URL
        page.evaluate.return_value = []
        clicks = []
        page.locator.return_value.filter.return_value.first.click.side_effect = lambda **_: clicks.append("更多")
        page.get_by_text.return_value.click.side_effect = lambda **_: clicks.append("有交换")
        self.assertEqual(_tab_friends(page, "有交换", None), [])
        self.assertEqual(clicks, ["更多", "有交换"])
        page.get_by_text.assert_called_once_with("有交换", exact=True)

    def test_exchange_scan_confirms_attachment_resume_from_history(self) -> None:
        friend = {"uid": "123", "brandName": "甲公司", "name": "张先生",
                  "sourceTitle": "前端工程师", "encryptJobId": "job-a", "lastTS": 1234567890}
        confirmation = {"mid": 88, "bizType": 14, "body": {"text": RESUME_DELIVERED},
                        "time": 1791454452678}
        with patch("browser.boss_monitor.ChromeBrowser") as browser, \
                patch("browser.boss_monitor._tab_friends", return_value=[friend]) as list_friends, \
                patch("browser.boss_monitor._fetch_history", return_value=[confirmation]) as history:
            browser.return_value.new_tab.return_value = "tab"
            rows = scan_exchanged_conversations("cdp", active_days=3)
        list_friends.assert_called_once_with(browser.return_value.page.return_value,
                                             "有交换", 3, None)
        history.assert_called_once_with(browser.return_value.page.return_value, friend, 100)
        self.assertEqual(rows[0]["source_job_id"], "job-a")
        self.assertEqual(rows[0]["conversation_id"], "123")
        self.assertEqual(rows[0]["resume_delivery_message_id"], "88")
        self.assertEqual(rows[0]["resume_delivered_at"],
                         datetime.fromtimestamp(1791454452.678, timezone.utc).isoformat())

    def test_resume_delivery_requires_exact_boss_confirmation_card(self) -> None:
        card = {"mid": 88, "bizType": 14, "body": {"text": RESUME_DELIVERED},
                "time": 1791454452678}
        self.assertEqual(_resume_delivery_evidence([card]), ("88",
            datetime.fromtimestamp(1791454452.678, timezone.utc).isoformat()))
        for change in ({"bizType": 1}, {"body": {"text": "我已发送简历"}}, {"mid": ""}):
            with self.subTest(change=change):
                self.assertEqual(_resume_delivery_evidence([{**card, **change}]), ("", ""))

    def test_resume_viewed_receipt_confirms_delivery_when_send_card_is_absent(self) -> None:
        viewed = {"mid": 99, "bizType": 21050035, "body": {"text": RESUME_VIEWED},
                  "time": 1791972575248}
        self.assertEqual(_resume_delivery_evidence([viewed]), ("99",
            datetime.fromtimestamp(1791972575.248, timezone.utc).isoformat()))
        for change in ({"bizType": 1}, {"body": {"text": "我已查看了您的附件简历"}},
                       {"mid": ""}):
            with self.subTest(change=change):
                self.assertEqual(_resume_delivery_evidence([{**viewed, **change}]), ("", ""))
        sent = {"mid": 88, "bizType": 14, "body": {"text": RESUME_DELIVERED},
                "time": 1791972528175}
        self.assertEqual(_resume_delivery_evidence([sent, viewed])[0], "88")

    def test_exchange_history_error_does_not_claim_resume_delivery(self) -> None:
        friend = {"uid": "123", "brandName": "甲公司", "lastTS": 1234567890}
        with patch("browser.boss_monitor.ChromeBrowser") as browser, \
                patch("browser.boss_monitor._tab_friends", return_value=[friend]), \
                patch("browser.boss_monitor._fetch_history", side_effect=RuntimeError("读取失败")):
            browser.return_value.new_tab.return_value = "tab"
            rows = scan_exchanged_conversations("cdp")
        self.assertEqual(rows[0]["resume_delivery_message_id"], "")
        self.assertEqual(rows[0]["resume_check_error"], "读取失败")

    def test_new_greeting_scan_passes_its_own_activity_window(self) -> None:
        friend = {"uid": "123", "avatar": "https://example.invalid/a.png",
                  "brandName": "甲公司", "name": "张先生"}
        with patch("browser.boss_monitor.ChromeBrowser") as browser, \
                patch("browser.boss_monitor._tab_friends", return_value=[friend]) as list_friends:
            browser.return_value.new_tab.return_value = "tab"
            rows = scan_new_greetings("cdp", active_days=9)
        list_friends.assert_called_once_with(browser.return_value.page.return_value,
                                             "新招呼", 9, None)
        self.assertEqual([row["conversation_id"] for row in rows], ["123"])

    def test_exchange_and_new_greeting_windows_filter_old_list_activity(self) -> None:
        now = datetime.now(timezone.utc)
        friends = [
            {"uid": "new", "avatar": "https://example.invalid/new.png",
             "name": "甲女士", "brandName": "甲公司",
             "lastTS": int((now - timedelta(days=2)).timestamp() * 1000)},
            {"uid": "old", "avatar": "https://example.invalid/old.png",
             "name": "乙先生", "brandName": "乙公司",
             "lastTS": int((now - timedelta(days=8)).timestamp() * 1000)},
        ]
        for tab_label in ("有交换", "新招呼"):
            with self.subTest(tab=tab_label):
                page = MagicMock()
                page.url = CHAT_URL
                page.on.side_effect = lambda _event, callback: callback(MagicMock(
                    url="https://www.zhipin.com/wapi/zprelation/friend/getGeekFriendList.json",
                    json=lambda: {"code": 0, "zpData": {"result": friends}}))
                page.evaluate.side_effect = lambda expression: (
                    [{"avatar": row["avatar"], "name": row["name"],
                      "brandName": row["brandName"], "status": ""} for row in friends]
                    if "querySelectorAll('.friend-content')" in expression else None)
                page.locator.return_value.filter.return_value.first.get_attribute.return_value = "selected"
                rows = _tab_friends(page, tab_label, 4)
                self.assertEqual([row["uid"] for row in rows], ["new"])

    def test_shared_default_avatar_does_not_hide_recent_communication_rows(self) -> None:
        now = datetime.now(timezone.utc)
        avatar = "https://example.invalid/default.png"
        friends = [
            {"uid": "today", "avatar": avatar, "name": "甲女士", "brandName": "甲公司",
             "lastTS": int(now.timestamp() * 1000)},
            {"uid": "three_days", "avatar": avatar, "name": "乙女士", "brandName": "乙公司",
             "lastTS": int((now - timedelta(days=3)).timestamp() * 1000)},
            {"uid": "stale", "avatar": avatar, "name": "丙女士", "brandName": "丙公司",
             "lastTS": int((now - timedelta(days=7)).timestamp() * 1000)},
        ]
        page = MagicMock()
        page.url = CHAT_URL
        page.on.side_effect = lambda _event, callback: callback(MagicMock(
            url="https://www.zhipin.com/wapi/zprelation/friend/getGeekFriendList.json",
            json=lambda: {"code": 0, "zpData": {"result": friends}}))
        snapshots = iter((friends[:1], friends[:2], friends))

        def evaluate(expression):
            if "querySelectorAll('.friend-content')" not in expression:
                return None
            return [{"avatar": row["avatar"], "name": row["name"],
                     "brandName": row["brandName"], "status": "unread"}
                    for row in next(snapshots, friends)]

        page.evaluate.side_effect = evaluate
        page.locator.return_value.filter.return_value.first.get_attribute.return_value = "selected"
        rows = _tab_friends(page, "仅沟通", 4)
        self.assertEqual([row["uid"] for row in rows], ["today", "three_days"])
        self.assertEqual([row["list_receipt_status"] for row in rows], ["unread", "unread"])

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

    def test_start_selects_one_monitoring_phase(self) -> None:
        with TemporaryDirectory() as directory:
            run = MonitoringRun(Path(directory) / "state.sqlite")
            with self.assertRaisesRegex(ValueError, "未知监测任务类型"):
                run.start("unknown")
            with patch("api.monitoring.Thread") as thread:
                state = run.start("new_greetings")
            self.assertEqual(state["phase"], "new_greetings")
            self.assertEqual(state["counts"]["phase"], "new_greetings")
            self.assertEqual(state["phases"]["communicated"]["status"], "not_run")
            self.assertEqual(state["phases"]["new_greetings"]["status"], "pending")
            self.assertEqual(thread.call_args.kwargs["args"][-2], "new_greetings")

    def test_phase_results_survive_other_scans_and_api_restart(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            run = MonitoringRun(path)
            with patch("api.monitoring.Thread"):
                greeting_run = run.start("new_greetings")
            with patch("api.monitoring.scan_new_greetings", return_value=[
                    {"platform": "boss", "conversation_id": "new", "company": "普通公司"}]):
                run._execute(run_id=greeting_run["run_id"], phase="new_greetings",
                             blocked_terms=[], browser_config={"cdp_url": "cdp", "silent_background": False})
            first = run.snapshot()["phase_runs"]["new_greetings"]
            self.assertEqual((first["status"], first["counts"]["new_greetings_scanned"]),
                             ("completed", 1))
            self.assertIsNotNone(first["started_at"])
            self.assertIsNotNone(first["finished_at"])

            with patch("api.monitoring.Thread"):
                communicated_run = run.start("communicated")
            during = run.snapshot()["phase_runs"]
            self.assertEqual(during["new_greetings"]["counts"]["new_greetings_scanned"], 1)
            self.assertEqual(during["new_greetings"]["run_id"], greeting_run["run_id"])
            self.assertEqual(during["communicated"]["run_id"], communicated_run["run_id"])
            self.assertIsNotNone(during["communicated"]["started_at"])
            with patch("api.monitoring.scan_recent_conversations", return_value=[]):
                run._execute(run_id=communicated_run["run_id"], phase="communicated",
                             browser_config={"cdp_url": "cdp", "silent_background": False})

            with patch("api.monitoring.Thread"):
                exchanged_run = run.start("exchanged")
            with patch("api.monitoring.scan_exchanged_conversations", return_value=[]):
                run._execute(run_id=exchanged_run["run_id"], phase="exchanged",
                             browser_config={"cdp_url": "cdp", "silent_background": False})

            restored = MonitoringRun(path).snapshot()["phase_runs"]
            self.assertEqual(restored["new_greetings"]["counts"]["new_greetings_scanned"], 1)
            self.assertEqual(restored["communicated"]["status"], "completed")
            self.assertIsNotNone(restored["communicated"]["finished_at"])
            self.assertEqual(restored["exchanged"]["status"], "completed")
            self.assertIsNotNone(restored["exchanged"]["started_at"])
            self.assertIsNotNone(restored["exchanged"]["finished_at"])

            with patch("api.monitoring.Thread"):
                rerun = run.start("new_greetings")
            current = run.snapshot()["phase_runs"]["new_greetings"]
            self.assertEqual(current["run_id"], rerun["run_id"])
            self.assertEqual(current["last_completed"]["run_id"], greeting_run["run_id"])
            self.assertEqual(current["last_completed"]["counts"]["new_greetings_scanned"], 1)
            run.run_store.mark_interrupted()
            interrupted = MonitoringRun(path).snapshot()["phase_runs"]["new_greetings"]
            self.assertEqual(interrupted["status"], "interrupted")
            self.assertIsNotNone(interrupted["finished_at"])
            self.assertEqual(interrupted["last_completed"]["counts"]["new_greetings_scanned"], 1)

    def test_today_phase_stats_are_independent_and_only_total_completed_scans(self) -> None:
        with TemporaryDirectory() as directory:
            store = TaskRunStore(Path(directory) / "state.sqlite")

            def completed(scope, counts, started):
                run_id = store.start("monitoring", started, "扫描中", counts={"phase": scope})
                store.finish(run_id, "completed", "已完成", counts,
                             started.replace("02:00:00", "02:01:00"))
                return run_id

            completed("communicated", {"phase": "communicated", "scanned": 99},
                      "2026-10-13T02:00:00+00:00")
            completed("communicated", {"phase": "communicated", "scanned": 4},
                      "2026-10-14T02:00:00+00:00")
            exchanged = completed("exchanged", {"phase": "exchanged", "exchanged_scanned": 3},
                                  "2026-10-14T02:00:00+00:00")
            completed("communicated", {"phase": "communicated", "scanned": 5},
                      "2026-10-14T02:00:00+00:00")
            both = completed("all", {"scanned": 7, "new_greetings_scanned": 8,
                                      "phase_states": {
                                          "communicated": {"status": "completed"},
                                          "new_greetings": {"status": "completed"},
                                          "exchanged": {"status": "not_run"}}},
                             "2026-10-14T02:00:00+00:00")
            pending = store.start("monitoring", "2026-10-14T03:00:00+00:00", "扫描中",
                                  counts={"phase": "new_greetings", "new_greetings_scanned": 6,
                                          "phase_states": {"new_greetings": {"status": "pending"}}})

            stats = store.today_monitoring_phases(
                now=datetime(2026, 10, 14, 12, tzinfo=timezone.utc))
            self.assertEqual(stats["communicated"],
                             {"runs": 3, "scanned_total": 16, "latest_run_id": both})
            self.assertEqual(stats["exchanged"],
                             {"runs": 1, "scanned_total": 3, "latest_run_id": exchanged})
            self.assertEqual(stats["new_greetings"],
                             {"runs": 2, "scanned_total": 8, "latest_run_id": pending})

    def test_exchanged_scan_is_independent_and_keeps_outside_job_outside(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            item = {"platform": "boss", "conversation_id": "123", "company": "甲公司",
                    "recruiter": "张先生", "job_title": "前端", "source_job_id": "outside-job",
                    "last_ts": 1234567890, "resume_delivery_message_id": "88",
                    "resume_delivered_at": "1791454452678"}
            with patch("api.monitoring.scan_recent_conversations", side_effect=AssertionError("不应扫描仅沟通")), \
                    patch("api.monitoring.scan_new_greetings", side_effect=AssertionError("不应扫描新招呼")), \
                    patch("api.monitoring.scan_exchanged_conversations", return_value=[item]) as scan:
                run = MonitoringRun(path)
                run._execute({**MONITORING_CONFIG, "exchanged_active_days": 6}, phase="exchanged")
            self.assertEqual(scan.call_args.kwargs["active_days"], 6)
            self.assertEqual(run.snapshot()["status"], "completed")
            self.assertEqual(run.snapshot()["phases"]["exchanged"]["status"], "completed")
            self.assertEqual(run.snapshot()["phases"]["communicated"]["status"], "not_run")
            self.assertEqual(run.snapshot()["phases"]["new_greetings"]["status"], "not_run")
            self.assertEqual(run.counts["exchanged_scanned"], 1)
            self.assertEqual(run.counts["exchanged_resume_confirmed"], 1)
            with closing(sqlite3.connect(path)) as conn:
                rows, total = ExchangeStore(conn).list_conversations(limit=20, offset=0)
                self.assertEqual((total, rows[0]["in_job_pool"]), (1, 0))
                self.assertEqual(rows[0]["resume_delivery_message_id"], "88")
                self.assertIsNone(JobStore(conn).get_job("boss", "outside-job"))
                self.assertEqual(ConversationStore(conn).list_conversations(limit=20, offset=0)[1], 0)

    def test_exchange_store_retires_missing_rows_and_preserves_first_seen(self) -> None:
        store = ExchangeStore(self.conn)
        item = {"platform": "boss", "conversation_id": "123", "company": "甲公司",
                "recruiter": "张先生", "job_title": "前端", "source_job_id": "job-a",
                "last_ts": 1234567890, "in_job_pool": False}
        self.assertTrue(store.record_scan({**item, "resume_delivery_message_id": "88",
                                           "resume_delivered_at": "1791454452678"}, "first"))
        first = store.list_conversations(limit=20, offset=0)[0][0]["first_seen_at"]
        self.assertFalse(store.record_scan({**item, "company": "乙公司"}, "second"))
        rows, total = store.list_conversations(limit=20, offset=0, query="乙公司")
        self.assertEqual((total, rows[0]["first_seen_at"]), (1, first))
        self.assertEqual(rows[0]["resume_delivery_message_id"], "88")
        self.assertEqual(store.deactivate_unseen("third"), 1)
        self.assertEqual(store.list_conversations(limit=20, offset=0)[1], 0)

    def test_exchange_update_time_tracks_latest_scan(self) -> None:
        store = ExchangeStore(self.conn)
        item = {"platform": "boss", "conversation_id": "123", "company": "甲公司",
                "recruiter": "张先生", "job_title": "前端", "source_job_id": "job-a",
                "last_ts": 1234567890, "in_job_pool": False}
        with patch("data.exchange_store._now", return_value="2026-10-14T10:00:00+00:00"):
            store.record_scan(item, "first")
        with patch("data.exchange_store._now", return_value="2026-10-14T11:00:00+00:00"):
            store.mark_removed("boss", "123")
        stored = self.conn.execute("""SELECT updated_at, last_scanned_at
            FROM monitored_exchanges WHERE conversation_id = '123'""").fetchone()
        self.assertEqual(stored, ("2026-10-14T11:00:00+00:00",
                                  "2026-10-14T10:00:00+00:00"))
        with patch("data.exchange_store._now", return_value="2026-10-14T12:00:00+00:00"):
            store.record_scan(item, "second")
        row = store.list_conversations(limit=20, offset=0)[0][0]
        self.assertEqual(row["first_seen_at"], "2026-10-14T10:00:00+00:00")
        self.assertEqual(row["last_scanned_at"], "2026-10-14T12:00:00+00:00")

    def test_exchange_store_migrates_existing_rows_without_claiming_delivery(self) -> None:
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.execute("""CREATE TABLE monitored_exchanges (
                platform TEXT NOT NULL, conversation_id TEXT NOT NULL,
                company TEXT NOT NULL DEFAULT '', recruiter TEXT NOT NULL DEFAULT '',
                job_title TEXT NOT NULL DEFAULT '', source_job_id TEXT NOT NULL DEFAULT '',
                last_ts INTEGER NOT NULL DEFAULT 0, in_job_pool INTEGER NOT NULL DEFAULT 0,
                first_seen_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                scan_token TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (platform, conversation_id))""")
            conn.execute("""INSERT INTO monitored_exchanges
                (platform, conversation_id, first_seen_at, updated_at, scan_token)
                VALUES ('boss', 'old', '2026-10-01', '2026-10-01', 'scan')""")
            row = ExchangeStore(conn).get_conversation("boss", "old")
            self.assertEqual(row["resume_delivery_message_id"], "")
            self.assertEqual(row["resume_delivered_at"], "")
            self.assertEqual(row["last_scanned_at"], "2026-10-01")

    def test_exchange_pool_filter_and_verified_boss_delete_keep_local_history(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            with closing(sqlite3.connect(path)) as conn:
                JobStore(conn).save({"source_platform": "boss", "source_job_id": "job-a"})
                store = ExchangeStore(conn)
                for conversation_id, pool, job_id in (("inside", True, "job-a"),
                                                      ("outside", False, "job-b")):
                    store.record_scan({"platform": "boss", "conversation_id": conversation_id,
                                       "company": "甲公司", "recruiter": "张先生", "job_title": "前端",
                                       "source_job_id": job_id, "last_ts": 1234567890,
                                       "in_job_pool": pool}, "scan")
                inside, inside_total = store.list_conversations(
                    limit=1, offset=0, pool_status="inside")
                outside, outside_total = store.list_conversations(
                    limit=1, offset=0, pool_status="outside", query="甲公司")
                self.assertEqual((inside_total, inside[0]["conversation_id"]), (1, "inside"))
                self.assertEqual((outside_total, outside[0]["conversation_id"]), (1, "outside"))
                self.assertEqual(store.list_conversations(limit=1, offset=1, pool_status="outside")[0], [])
                with self.assertRaises(ValueError):
                    store.list_conversations(limit=1, offset=0, pool_status="invalid")
            with patch("api.conversations._show_boss_chat", return_value={"message": "已打开"}) as show:
                self.assertEqual(open_exchanged_conversation(
                    path, {"browser": {"cdp_url": "cdp"}}, "boss", "outside"), {"message": "已打开"})
                show.assert_called_once_with({"browser": {"cdp_url": "cdp"}}, "outside",
                                             "有交换", open_chat=True)
            with patch("api.conversations.delete_boss_exchange", return_value=FilterDeleteResult(
                    True, False, "已从 BOSS 删除")) as browser_delete:
                self.assertTrue(delete_exchanged_conversation(
                    path, "cdp", "boss", "outside")["deleted"])
            browser_delete.assert_called_once()
            with closing(sqlite3.connect(path)) as conn:
                self.assertIsNone(ExchangeStore(conn).get_conversation("boss", "outside"))
                self.assertEqual(conn.execute("""SELECT COUNT(*) FROM monitored_exchanges
                    WHERE conversation_id = 'outside'""").fetchone()[0], 1)
                self.assertIsNotNone(JobStore(conn).get_job("boss", "job-a"))
            with self.assertRaises(JobActionError):
                open_exchanged_conversation(path, {"browser": {"cdp_url": "cdp"}}, "boss", "outside")

    def test_exchange_api_filters_opens_and_deletes_boss_conversation(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            with closing(sqlite3.connect(path)) as conn:
                ExchangeStore(conn).record_scan({
                    "platform": "boss", "conversation_id": "123", "company": "甲公司",
                    "recruiter": "张先生", "job_title": "前端", "source_job_id": "job-a",
                    "last_ts": 1234567890, "in_job_pool": False,
                }, "scan")
            handler_class = make_handler(path, MagicMock(), restart_event=Event(),
                started_ai_concurrency=1, state_db_overridden=True, server_instance_id="test")
            handler = object.__new__(handler_class)
            handler.send_json = MagicMock()
            handler.read_json = MagicMock(return_value={})
            handler.path = "/api/exchanged-conversations?limit=20&offset=0&pool_status=outside"
            handler.do_GET()
            code, body = handler.send_json.call_args.args
            self.assertEqual((code, body["total"]), (200, 1))
            handler.path = "/api/exchanged-conversations?pool_status=invalid"
            handler.do_GET()
            self.assertEqual(handler.send_json.call_args.args[0], 400)
            handler.path = "/api/exchanged-conversations/boss/123/open"
            with patch("api.conversations._show_boss_chat", return_value={"message": "已打开"}):
                handler.do_POST()
            self.assertEqual(handler.send_json.call_args.args, (200, {"message": "已打开"}))
            handler.path = "/api/exchanged-conversations/boss/123"
            with patch("api.conversations.delete_boss_exchange", return_value=FilterDeleteResult(
                    True, False, "已删除")):
                handler.do_DELETE()
            self.assertTrue(handler.send_json.call_args.args[1]["deleted"])
            handler.do_DELETE()
            self.assertEqual(handler.send_json.call_args.args[0], 404)

    def test_exchange_delete_uncertain_result_keeps_row_visible(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            with closing(sqlite3.connect(path)) as conn:
                ExchangeStore(conn).record_scan({
                    "platform": "boss", "conversation_id": "123", "company": "甲公司",
                    "recruiter": "张先生", "job_title": "前端", "source_job_id": "job-a",
                    "last_ts": 1234567890, "in_job_pool": False,
                }, "scan")
            with patch("api.conversations.delete_boss_exchange", return_value=FilterDeleteResult(
                    False, True, "删除结果不明")):
                with self.assertRaises(JobActionError) as error:
                    delete_exchanged_conversation(path, "cdp", "boss", "123")
            self.assertEqual(error.exception.status_code, 409)
            with closing(sqlite3.connect(path)) as conn:
                self.assertIsNotNone(ExchangeStore(conn).get_conversation("boss", "123"))

    def test_communicated_scan_does_not_scan_or_retire_new_greetings(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            with closing(sqlite3.connect(path)) as conn:
                FilterStore(conn).record_scan(
                    {"platform": "boss", "conversation_id": "filtered", "company": "屏蔽公司"},
                    "屏蔽", "previous")
            conversation = {"platform": "boss", "conversation_id": "communicated",
                            "source_job_id": "outside-job", "job_title": "工程师",
                            "company": "普通公司", "judgment": "unread", "messages": []}
            with patch("api.monitoring.scan_recent_conversations", return_value=[conversation]) as scan, \
                    patch("api.monitoring.scan_new_greetings", side_effect=AssertionError("不应扫描新招呼")):
                run = MonitoringRun(path)
                run._execute(phase="communicated")
            self.assertEqual(run.snapshot()["status"], "completed")
            self.assertEqual(run.snapshot()["phases"]["new_greetings"]["status"], "not_run")
            self.assertEqual(run.counts["phase"], "communicated")
            self.assertEqual(run.counts["filter_stale"], 0)
            scan.assert_called_once()
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(FilterStore(conn).get_candidate("boss", "filtered")["status"], "pending_review")
                rows, total = ConversationStore(conn).list_conversations(limit=10, offset=0)
                self.assertEqual((total, rows[0]["in_job_pool"]), (1, False))

    def test_new_greetings_scan_does_not_scan_or_retire_communicated(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            with closing(sqlite3.connect(path)) as conn:
                ConversationStore(conn).upsert({"platform": "boss", "conversation_id": "saved",
                                                "judgment": "unread"}, [], scan_token="previous")
            with patch("api.monitoring.scan_recent_conversations", side_effect=AssertionError("不应扫描仅沟通")), \
                    patch("api.monitoring.scan_new_greetings", return_value=[
                        {"platform": "boss", "conversation_id": "filtered", "company": "屏蔽公司"}]) as scan:
                run = MonitoringRun(path)
                run._execute({**MONITORING_CONFIG, "new_greetings_active_days": 9},
                             blocked_terms=["屏蔽"], phase="new_greetings")
            self.assertEqual(run.snapshot()["status"], "completed")
            self.assertEqual(run.snapshot()["phases"]["communicated"]["status"], "not_run")
            self.assertEqual(run.counts["phase"], "new_greetings")
            self.assertEqual(run.counts["left_scope"], 0)
            scan.assert_called_once()
            self.assertEqual(scan.call_args.kwargs["active_days"], 9)
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(ConversationStore(conn).list_conversations(limit=10, offset=0)[1], 1)
                self.assertIsNotNone(FilterStore(conn).get_candidate("boss", "filtered"))

    def test_scan_counts_only_first_inserts_and_persists_updates_separately(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            run = MonitoringRun(path)
            config = deepcopy(MONITORING_CONFIG)
            config.update(followup_enabled=False, filter_review_required=True)
            conversations = [{
                "platform": "boss", "conversation_id": str(i), "source_job_id": "",
                "job_title": "前端", "company": "测试公司", "judgment": judgment, "messages": [],
            } for i, judgment in ((1, "unread"), (2, "read_no_reply"))]
            greetings = [{"platform": "boss", "conversation_id": "3", "company": "屏蔽公司"},
                         {"platform": "boss", "conversation_id": "4", "company": "普通公司"}]
            with patch("api.monitoring.scan_recent_conversations", side_effect=lambda *args, **kwargs: deepcopy(conversations)), \
                    patch("api.monitoring.scan_new_greetings", return_value=greetings):
                for index, new_count in ((1, 2), (2, 0)):
                    run_id = run.run_store.start("monitoring", f"2026-10-10T00:0{index}:00+00:00", "扫描中")
                    run._execute(config, run_id, ["屏蔽"])
                    counts = run.run_store.get_run(run_id)["counts"]
                    self.assertEqual(counts["scanned"], 2)
                    self.assertEqual(counts["saved"], 2)
                    self.assertEqual(counts["communicated_new"], new_count)
                    self.assertEqual((counts["unread"], counts["read_no_reply"]), (1, 1))
                    self.assertEqual(counts["new_greetings_scanned"], 2)
                    self.assertEqual(counts["filter_new"], 1 if index == 1 else 0)
                # Restoring an existing candidate is an update, not a new database insert.
                with closing(sqlite3.connect(path)) as conn:
                    FilterStore(conn).retire_unseen("different-scan")
                run._execute(config, blocked_terms=["屏蔽"])
                self.assertEqual(run.snapshot()["counts"]["filter_new"], 0)

    def test_failed_second_scan_preserves_completed_first_scan_count(self) -> None:
        with TemporaryDirectory() as directory:
            run = MonitoringRun(Path(directory) / "state.sqlite")
            run_id = run.run_store.start("monitoring", "2026-10-10T00:01:00+00:00", "扫描中")
            with patch("api.monitoring.scan_recent_conversations", return_value=[{}] * 3), \
                    patch("api.monitoring.scan_new_greetings", side_effect=RuntimeError("列表读取失败")):
                run._execute(run_id=run_id,
                             browser_config={"cdp_url": "cdp", "silent_background": False})
            state = run.run_store.get_run(run_id)
            self.assertEqual(state["status"], "failed")
            self.assertEqual((state["counts"]["scanned"], state["counts"]["communicated_new"]), (3, 0))
            phases = MonitoringRun(run.db_path).snapshot()["phase_runs"]
            self.assertEqual(phases["communicated"]["status"], "skipped")
            self.assertEqual(phases["new_greetings"]["status"], "failed")
            self.assertIsNotNone(phases["communicated"]["finished_at"])

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

    def test_monitoring_keeps_identifiable_conversation_outside_pool(self) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "state.sqlite3"
            observation = {"platform": "boss", "conversation_id": "123",
                           "source_job_id": "job-outside", "job_title": "后端工程师",
                           "company": "甲公司", "recruiter": "李女士",
                           "chat_url": "https://www.zhipin.com/web/geek/chat?jobId=456",
                           "judgment": "unread", "messages": [{
                               "message_id": "1", "sender": "self", "text": "您好",
                               "delivery_status": "1", "biz_type": "101",
                           }]}
            with patch("api.monitoring.scan_new_greetings", return_value=[]), \
                    patch("api.monitoring.scan_recent_conversations", return_value=[observation]):
                run = MonitoringRun(db_path)
                run._execute()
                self.assertEqual(run.snapshot()["status"], "completed")
                self.assertEqual((run.counts["in_job_pool"], run.counts["outside_job_pool"]), (0, 1))
                with closing(sqlite3.connect(db_path)) as conn:
                    self.assertIsNone(JobStore(conn).get_job("boss", "job-outside"))
                    rows, total = ConversationStore(conn).list_conversations(limit=10, offset=0)
                    self.assertEqual((total, rows[0]["in_job_pool"]), (1, False))
                run._execute()
                with closing(sqlite3.connect(db_path)) as conn:
                    self.assertIsNone(JobStore(conn).get_job("boss", "job-outside"))
                    rows, total = ConversationStore(conn).list_conversations(limit=10, offset=0)
                    self.assertEqual((total, rows[0]["in_job_pool"]), (1, False))

    def test_monitoring_keeps_unidentifiable_conversation_outside_pool(self) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "state.sqlite3"
            observation = {"platform": "boss", "conversation_id": "123",
                           "source_job_id": "", "job_title": "未知岗位",
                           "company": "甲公司", "judgment": "unread", "messages": []}
            with patch("api.monitoring.scan_new_greetings", return_value=[]), \
                    patch("api.monitoring.scan_recent_conversations", return_value=[observation]):
                run = MonitoringRun(db_path)
                run._execute()
            with closing(sqlite3.connect(db_path)) as conn:
                self.assertEqual(JobStore(conn).list_jobs(limit=10, offset=0)[1], 0)
                rows, total = ConversationStore(conn).list_conversations(limit=10, offset=0)
                self.assertEqual((total, rows[0]["in_job_pool"]), (1, False))


if __name__ == "__main__":
    unittest.main()
