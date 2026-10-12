"""Workbench conversion counts preserve dated events and joined contact paths."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import MagicMock

from api.conversion import (generate_and_save_conversion_flow, get_conversion_flow,
                            get_saved_conversion_flow)
from api.jobs import JobActionError
from api.server import make_handler
from data.conversation_store import ConversationStore
from data.exchange_store import ExchangeStore
from data.job_store import JobStore
from data.task_run_store import LOCAL_ZONE


class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.sqlite3"
        self.today = datetime.now(LOCAL_ZONE).date().isoformat()
        self.now = datetime.now(timezone.utc).isoformat()

    def insert_job(self, conn, job_id, status, updated_at):
        conn.execute("""INSERT INTO collected_jobs
            (platform, source_job_id, record_json, ai_score_status, ai_score_reason,
             ai_score_error, collected_at, updated_at, job_status)
            VALUES ('boss', ?, '{}', 'not_scored', '', '', ?, ?, ?)""",
            (job_id, updated_at, updated_at, status))

    def test_date_range_joins_greetings_followups_and_exchanges(self):
        with closing(sqlite3.connect(self.path)) as conn:
            JobStore(conn)
            ConversationStore(conn)
            ExchangeStore(conn)
            self.insert_job(conn, "job-1", "greeted", self.now)
            conn.execute("""INSERT INTO greeting_send_events VALUES ('boss', 'job-1', ?)""", (self.now,))
            for conversation_id, job_id, in_pool in (("1", "job-1", 1), ("2", "", 0)):
                conn.execute("""INSERT INTO monitored_conversations
                    (platform, conversation_id, source_job_id, in_job_pool, active,
                     first_seen_at, updated_at, last_scanned_at, followup_status)
                    VALUES ('boss', ?, ?, ?, 1, ?, ?, ?, ?)""",
                    (conversation_id, job_id, in_pool, self.now, self.now, self.now,
                     "sending" if conversation_id == "1" else "none"))
            conn.execute("""INSERT INTO monitored_messages
                (platform, conversation_id, message_id, sender, text)
                VALUES ('boss', '2', '100', 'recruiter', '您好')""")
            conn.commit()
            self.assertTrue(ConversationStore(conn).finish_followup(
                "boss", "1", outcome="sent", message_id="101"))
            for conversation_id, job_id in (("1", "job-1"), ("2", ""), ("3", "")):
                ExchangeStore(conn).record_scan({
                    "platform": "boss", "conversation_id": conversation_id,
                    "company": "测试", "recruiter": "HR", "job_title": "岗位",
                    "source_job_id": job_id, "last_ts": 1, "in_job_pool": bool(job_id),
                }, "scan-1")

        result = get_conversion_flow(self.path, self.today, self.today)
        self.assertEqual(result["metrics"], {"greeted": 1, "hr_initiated": 2,
                                             "followups": 1, "deliveries": 3, "contacts": 3})
        self.assertEqual(result["flows"], [
            {"source": "greeted", "followed_up": True, "delivered": True, "count": 1},
            {"source": "hr_initiated", "followed_up": False, "delivered": True, "count": 2},
        ])
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM followup_send_events").fetchone()[0], 1)

    def test_invalid_dates_are_rejected(self):
        for start, end in (("bad", self.today), (self.today, "bad"),
                           ("2999-01-01", "2999-01-02"), (self.today, "2000-01-01")):
            with self.subTest(start=start, end=end), self.assertRaises(JobActionError):
                get_conversion_flow(self.path, start, end)

    def test_repeated_followups_count_once_per_conversation(self):
        with closing(sqlite3.connect(self.path)) as conn:
            store = ConversationStore(conn)
            conn.execute("""INSERT INTO monitored_conversations
                (platform, conversation_id, source_job_id, active, first_seen_at,
                 updated_at, last_scanned_at, followup_status)
                VALUES ('boss', '1', 'job-1', 1, ?, ?, ?, 'sending')""",
                (self.now, self.now, self.now))
            conn.commit()
            self.assertTrue(store.finish_followup("boss", "1", outcome="sent", message_id="101"))
            conn.execute("""UPDATE monitored_conversations SET followup_status = 'sending'
                WHERE platform = 'boss' AND conversation_id = '1'""")
            conn.commit()
            self.assertTrue(store.finish_followup("boss", "1", outcome="sent", message_id="102"))

        result = get_conversion_flow(self.path, self.today, self.today)
        self.assertEqual(result["metrics"]["followups"], 1)
        self.assertEqual(result["metrics"]["contacts"], 1)
        self.assertEqual(result["flows"][0]["count"], 1)
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM followup_send_events").fetchone()[0], 2)

    def test_current_statuses_and_specified_timestamps_define_each_count(self):
        yesterday = (datetime.now(LOCAL_ZONE).date() - timedelta(days=1)).isoformat()
        outside = datetime.fromisoformat(f"{yesterday}T12:00:00+08:00").astimezone(timezone.utc).isoformat()
        with closing(sqlite3.connect(self.path)) as conn:
            JobStore(conn)
            ConversationStore(conn)
            ExchangeStore(conn)
            for job_id, status, updated_at in (
                ("current", "greeted", self.now), ("old", "greeted", outside),
                ("ended", "ended_forced", self.now),
            ):
                self.insert_job(conn, job_id, status, updated_at)
            for conversation_id, in_pool, active, judgment, first_seen, updated, count in (
                ("outside", 0, 1, "unread", self.now, self.now, 0),
                ("outside-extra", 0, 1, "unread", self.now, self.now, 0),
                ("old-entry", 0, 1, "unread", outside, self.now, 0),
                ("inactive", 0, 0, "unread", self.now, self.now, 1),
                ("answered", 0, 1, "replied", self.now, self.now, 1),
                ("followed", 1, 1, "read_no_reply", outside, self.now, 1),
                ("old-followup", 1, 1, "unread", self.now, outside, 1),
            ):
                conn.execute("""INSERT INTO monitored_conversations
                    (platform, conversation_id, in_job_pool, active, judgment,
                     first_seen_at, updated_at, last_scanned_at, followup_count)
                    VALUES ('boss', ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (conversation_id, in_pool, active, judgment, first_seen,
                     updated, updated, count))
            for conversation_id, active, first_seen in (
                ("exchange-current", 1, self.now),
                ("exchange-inactive", 0, self.now),
                ("exchange-old", 1, outside),
            ):
                conn.execute("""INSERT INTO monitored_exchanges
                    (platform, conversation_id, first_seen_at, updated_at, scan_token, active)
                    VALUES ('boss', ?, ?, ?, 'scan', ?)""",
                    (conversation_id, first_seen, first_seen, active))
            conn.commit()

        result = get_conversion_flow(self.path, self.today, self.today)
        self.assertEqual(result["metrics"], {"greeted": 1, "hr_initiated": 2,
                                             "followups": 2, "deliveries": 2, "contacts": 5})

    def test_conversion_endpoint_returns_range_summary(self):
        handler_class = make_handler(self.path, MagicMock(), restart_event=Event(),
            started_ai_concurrency=1, state_db_overridden=True, server_instance_id="test")
        handler = object.__new__(handler_class)
        handler.send_json = MagicMock()
        handler.path = f"/api/workbench/conversion?start={self.today}&end={self.today}"
        handler.do_GET()
        code, body = handler.send_json.call_args.args
        self.assertEqual(code, 200)
        self.assertEqual(body["start_date"], self.today)
        self.assertEqual(body["metrics"]["deliveries"], 0)
        self.assertIsNotNone(datetime.fromisoformat(body["generated_at"]).tzinfo)

    def test_generated_chart_persists_until_next_successful_generation(self):
        self.assertIsNone(get_saved_conversion_flow(self.path))
        first = generate_and_save_conversion_flow(self.path, self.today, self.today)
        self.assertEqual(get_saved_conversion_flow(self.path), first)

        with self.assertRaises(JobActionError):
            generate_and_save_conversion_flow(self.path, "bad", self.today)
        self.assertEqual(get_saved_conversion_flow(self.path), first)

        yesterday = (datetime.now(LOCAL_ZONE).date() - timedelta(days=1)).isoformat()
        second = generate_and_save_conversion_flow(self.path, yesterday, self.today)
        self.assertEqual(get_saved_conversion_flow(self.path), second)
        self.assertEqual(second["start_date"], yesterday)

    def test_saved_chart_api_restores_and_replaces_snapshot(self):
        handler_class = make_handler(self.path, MagicMock(), restart_event=Event(),
            started_ai_concurrency=1, state_db_overridden=True, server_instance_id="test")
        handler = object.__new__(handler_class)
        handler.send_json = MagicMock()
        handler.path = "/api/workbench/conversion/latest"
        handler.do_GET()
        self.assertEqual(handler.send_json.call_args.args, (200, {"snapshot": None}))

        handler.path = "/api/workbench/conversion"
        handler.read_json = MagicMock(return_value={"start": self.today, "end": self.today})
        handler.do_POST()
        code, generated = handler.send_json.call_args.args
        self.assertEqual(code, 200)
        handler.path = "/api/workbench/conversion/latest"
        handler.do_GET()
        self.assertEqual(handler.send_json.call_args.args, (200, {"snapshot": generated}))
