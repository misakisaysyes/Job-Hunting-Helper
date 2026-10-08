"""Company exclusions, filter review deduplication, and guarded delete outcomes."""

from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from api.filtering import delete_candidate, delete_filter_record, matched_company_term
from api.jobs import JobActionError
from api.monitoring import MonitoringRun
from browser.boss_filter import FilterDeleteResult, _confirm_delete_dialog
from data.filter_store import FilterStore


GREETING = {
    "platform": "boss", "conversation_id": "123", "company": "北京万联智链科技",
    "recruiter": "贺女士", "job_title": "前端工程师", "source_job_id": "job-1",
    "avatar": "https://example.invalid/avatar.png",
}


class FilteringTests(unittest.TestCase):
    def test_in_page_delete_confirmation_clicks_expected_button(self) -> None:
        page = Mock()
        confirmation = page.locator.return_value.filter.return_value
        confirmation.count.return_value = 1
        title = Mock()
        title.inner_text.return_value = "确认删除吗?"
        body = Mock()
        body.inner_text.return_value = "将对方从你的列表中删除，同时删除聊天记录"
        button = Mock()
        button.count.return_value = 1
        button.inner_text.return_value = "确定"
        footer = Mock()
        footer.filter.return_value = button
        confirmation.locator.side_effect = [title, body, footer]

        self.assertIsNone(_confirm_delete_dialog(page))
        button.click.assert_called_once_with(timeout=5000)

    def test_in_page_delete_confirmation_rejects_other_dialog(self) -> None:
        page = Mock()
        confirmation = page.locator.return_value.filter.return_value
        confirmation.count.return_value = 1
        title = Mock()
        title.inner_text.return_value = "其他操作"
        body = Mock()
        body.inner_text.return_value = "将对方从你的列表中删除，同时删除聊天记录"
        button = Mock()
        button.count.return_value = 1
        button.inner_text.return_value = "确定"
        footer = Mock()
        footer.filter.return_value = button
        confirmation.locator.side_effect = [title, body, footer]

        self.assertIn("内容与预期不符", _confirm_delete_dialog(page))
        button.click.assert_not_called()

    def test_company_only_matches_configured_company_terms(self) -> None:
        self.assertEqual(matched_company_term("北京万联智链科技", ["法本", "万联"]), "万联")
        self.assertEqual(matched_company_term("Example TECH", ["tech"]), "tech")
        self.assertEqual(matched_company_term("北京万联智链科技", ["工程师", ""]), "")

    def test_store_deduplicates_and_preserves_review_decisions(self) -> None:
        with closing(sqlite3.connect(":memory:")) as conn:
            store = FilterStore(conn)
            self.assertTrue(store.record_match(GREETING, "万联", "scan-1"))
            self.assertFalse(store.record_match(GREETING, "万联", "scan-2"))
            rows, total = store.list_candidates(limit=20, offset=0)
            self.assertEqual((len(rows), total), (1, 1))
            self.assertEqual(rows[0]["status"], "pending_review")
            self.assertEqual(store.retire_unseen("scan-2"), 0)
            # Legacy retained decisions remain readable, but can no longer be created.
            conn.execute("""UPDATE monitored_filter_candidates SET status = 'skipped'
                WHERE platform = 'boss' AND conversation_id = '123'""")
            conn.commit()
            self.assertFalse(store.record_match(GREETING, "万联", "scan-3"))
            self.assertEqual(store.get_candidate("boss", "123")["status"], "skipped")

    def test_unseen_pending_candidate_becomes_stale(self) -> None:
        with closing(sqlite3.connect(":memory:")) as conn:
            store = FilterStore(conn)
            store.record_match(GREETING, "万联", "scan-1")
            self.assertEqual(store.retire_unseen("scan-2"), 1)
            self.assertEqual(store.get_candidate("boss", "123")["status"], "stale")
            self.assertTrue(store.record_match(GREETING, "万联", "scan-3"))

    def test_candidate_filters_apply_before_pagination(self) -> None:
        with closing(sqlite3.connect(":memory:")) as conn:
            store = FilterStore(conn)
            store.record_match(GREETING, "万联", "scan-1")
            store.record_match({**GREETING, "conversation_id": "456",
                                "company": "万联分公司"}, "万联", "scan-1")
            store.record_match({**GREETING, "conversation_id": "789",
                                "company": "其他公司"}, "其他", "scan-1")
            conn.execute("""UPDATE monitored_filter_candidates SET status = 'skipped'
                WHERE platform = 'boss' AND conversation_id = '456'""")
            conn.commit()
            rows, total = store.list_candidates(
                limit=1, offset=0, query="万联", status="pending_review")
            self.assertEqual((total, [row["conversation_id"] for row in rows]), (1, ["123"]))
            rows, total = store.list_candidates(limit=1, offset=0, query="万联")
            self.assertEqual((total, len(rows)), (2, 1))

    def test_delete_record_only_removes_local_filter_candidate(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            with closing(sqlite3.connect(path)) as conn:
                FilterStore(conn).record_match(GREETING, "万联", "scan-1")
            with patch("api.filtering.delete_new_greeting") as browser_delete:
                self.assertTrue(delete_filter_record(path, "boss", "123")["deleted"])
                browser_delete.assert_not_called()
            with closing(sqlite3.connect(path)) as conn:
                store = FilterStore(conn)
                self.assertIsNone(store.get_candidate("boss", "123"))
                self.assertTrue(store.record_match(GREETING, "万联", "scan-2"))
            with self.assertRaises(JobActionError) as error:
                delete_filter_record(path, "boss", "missing")
            self.assertEqual(error.exception.status_code, 404)

    def test_delete_record_rejects_in_flight_boss_deletion(self) -> None:
        with closing(sqlite3.connect(":memory:")) as conn:
            store = FilterStore(conn)
            store.record_match(GREETING, "万联", "scan-1")
            self.assertIsNotNone(store.claim_delete("boss", "123"))
            self.assertEqual(store.delete_record("boss", "123"), "busy")
            self.assertIsNotNone(store.get_candidate("boss", "123"))

    def test_delete_requires_match_and_records_only_verified_result(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            with closing(sqlite3.connect(path)) as conn:
                FilterStore(conn).record_match(GREETING, "万联", "scan-1")
            with patch("api.filtering.delete_new_greeting", return_value=FilterDeleteResult(
                    True, False, "已删除")) as delete:
                result = delete_candidate(path, "cdp", "boss", "123", ["万联"])
            delete.assert_called_once()
            self.assertEqual(result["candidate"]["status"], "deleted")
            with self.assertRaises(JobActionError):
                delete_candidate(path, "cdp", "boss", "123", ["万联"])

    def test_failed_delete_can_be_selected_and_retried(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            with closing(sqlite3.connect(path)) as conn:
                store = FilterStore(conn)
                store.record_match(GREETING, "万联", "scan-1")
                store.claim_delete("boss", "123")
                store.finish_delete("boss", "123", outcome="failed", error="未打开菜单")
            with patch("api.filtering.delete_new_greeting", return_value=FilterDeleteResult(
                    True, False, "已删除")) as browser_delete:
                result = delete_candidate(path, "cdp", "boss", "123", ["万联"])
            browser_delete.assert_called_once()
            self.assertEqual(result["candidate"]["status"], "deleted")

    def test_unknown_delete_stops_automatic_retry(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            with closing(sqlite3.connect(path)) as conn:
                FilterStore(conn).record_match(GREETING, "万联", "scan-1")
            with patch("api.filtering.delete_new_greeting", return_value=FilterDeleteResult(
                    False, True, "结果不明")):
                with self.assertRaises(JobActionError):
                    delete_candidate(path, "cdp", "boss", "123", ["万联"])
            with closing(sqlite3.connect(path)) as conn:
                store = FilterStore(conn)
                self.assertEqual(store.get_candidate("boss", "123")["status"], "unknown")
                self.assertIsNone(store.claim_delete("boss", "123"))

    def test_unknown_delete_requires_explicit_manual_retry(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            with closing(sqlite3.connect(path)) as conn:
                store = FilterStore(conn)
                store.record_match(GREETING, "万联", "scan-1")
                store.claim_delete("boss", "123")
                store.finish_delete("boss", "123", outcome="unknown", error="确认框未处理")
            with self.assertRaises(JobActionError):
                delete_candidate(path, "cdp", "boss", "123", ["万联"])
            with patch("api.filtering.delete_new_greeting", return_value=FilterDeleteResult(
                    True, False, "已删除")) as browser_delete:
                result = delete_candidate(path, "cdp", "boss", "123", ["万联"],
                                          confirm_still_present=True)
            browser_delete.assert_called_once()
            self.assertEqual(result["candidate"]["status"], "deleted")

    def test_monitor_creates_one_review_item_then_auto_delete_when_enabled(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            settings = {"message_limit": 10, "followup_days": 4,
                        "followup_cooldown_hours": 24, "followup_max_count": 2,
                        "followup_enabled": False, "followup_unread": True,
                        "followup_read_no_reply": True, "followup_review_required": True,
                        "filter_review_required": True}
            with patch("api.monitoring.scan_recent_conversations", return_value=[]), \
                    patch("api.monitoring.scan_new_greetings", return_value=[GREETING]), \
                    patch("api.monitoring.delete_candidate") as delete:
                first = MonitoringRun(path)
                first._execute(settings, blocked_terms=["万联"])
                second = MonitoringRun(path)
                second._execute(settings, blocked_terms=["万联"])
                delete.assert_not_called()
            self.assertEqual(first.counts["filter_matches"], 1)
            self.assertEqual(second.counts["filter_pending"], 1)
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(FilterStore(conn).list_candidates(limit=20, offset=0)[1], 1)
            with patch("api.monitoring.scan_recent_conversations", return_value=[]), \
                    patch("api.monitoring.scan_new_greetings", return_value=[GREETING]), \
                    patch("api.filtering.delete_new_greeting", return_value=FilterDeleteResult(
                        True, False, "已删除")) as browser_delete:
                third = MonitoringRun(path)
                third._execute({**settings, "filter_review_required": False},
                               blocked_terms=["万联"])
            browser_delete.assert_called_once()
            self.assertEqual(third.counts["filter_deleted"], 1)
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(FilterStore(conn).get_candidate("boss", "123")["status"], "deleted")


if __name__ == "__main__":
    unittest.main()
