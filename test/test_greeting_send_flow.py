"""Verified BOSS sends persist the conversation link and greeted status."""

from __future__ import annotations

from contextlib import closing
from copy import deepcopy
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import ANY, MagicMock, patch

from patchright.sync_api import TimeoutError as BrowserTimeout

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.jobs import JobActionError, confirm_greeting_not_sent, generate_greeting, open_conversation, send_greeting, start_greeting
from browser.browser import ChromeBrowser
from browser.boss_message import SendResult, send_boss_greeting
from config import DEFAULT_CONFIG
from data.job_store import JobStore


class GreetingSendFlowTests(unittest.TestCase):
    def test_low_score_requires_explicit_start_before_empty_greeting_stage(self) -> None:
        config = deepcopy(DEFAULT_CONFIG)
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "jobs.sqlite"
            with closing(sqlite3.connect(db_path)) as conn:
                JobStore(conn, score_threshold=71).save({
                    "source_platform": "boss", "source_job_id": "low",
                    "ai_score_status": "scored", "ai_score": 65,
                })
            job = start_greeting(db_path, config, "boss", "low")
            self.assertEqual(job["job_status"], "greeting_ready")
            self.assertEqual(job["greeting"], "")
            with self.assertRaisesRegex(JobActionError, "岗位状态已变化"):
                start_greeting(db_path, config, "boss", "low")

    def test_ai_generation_persists_greeting_ready_state(self) -> None:
        config = deepcopy(DEFAULT_CONFIG)
        config["ai"]["greeting_user_prompt"] = "重点介绍相关项目"
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "jobs.sqlite"
            with closing(sqlite3.connect(db_path)) as conn:
                JobStore(conn, score_threshold=71).save({
                    "source_platform": "boss", "source_job_id": "job-id",
                    "ai_score_status": "scored", "ai_score": 88,
                })
            scheduler = MagicMock()
            scheduler.submit.return_value.result.return_value = SimpleNamespace(
                text="您好，想了解岗位。")
            with patch("api.jobs.load_resume_text", return_value="简历"), \
                    patch("api.jobs.make_greeting_task", return_value="task") as make_task:
                result = generate_greeting(db_path, config, scheduler, "boss", "job-id")
            make_task.assert_called_once_with(ANY, "简历", "重点介绍相关项目")
            self.assertEqual(result["job"]["job_status"], "greeting_ready")
            self.assertEqual(result["job"]["greeting"], "您好，想了解岗位。")
            with closing(sqlite3.connect(db_path)) as conn:
                self.assertEqual(JobStore(conn).get_job("boss", "job-id")[
                    "job_status_history"], ["scored", "greeting_ready"])

    def test_view_conversation_uses_connected_chrome(self) -> None:
        config = deepcopy(DEFAULT_CONFIG)
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "jobs.sqlite"
            chat_url = "https://www.zhipin.com/web/geek/chat?jobId=job-id"
            with closing(sqlite3.connect(db_path)) as conn:
                store = JobStore(conn, score_threshold=71)
                store.save({
                    "source_platform": "boss", "source_job_id": "job-id",
                    "ai_score_status": "scored", "ai_score": 88,
                })
                store.reserve_greeting_send("boss", "job-id", "您好")
                store.finish_greeting_send(
                    "boss", "job-id", outcome="sent", chat_url=chat_url)
            with patch("api.jobs.ChromeBrowser") as browser_type:
                result = open_conversation(db_path, config, "boss", "job-id")
            self.assertIn("Chrome", result["message"])
            browser_type.assert_called_once_with(config["browser"]["cdp_url"])
            browser_type.return_value.open_user_tab.assert_called_once_with(chat_url)
            browser_type.return_value.close.assert_called_once()

    def test_view_conversation_rejects_wrong_job_link(self) -> None:
        config = deepcopy(DEFAULT_CONFIG)
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "jobs.sqlite"
            with closing(sqlite3.connect(db_path)) as conn:
                store = JobStore(conn, score_threshold=71)
                store.save({
                    "source_platform": "boss", "source_job_id": "job-id",
                    "ai_score_status": "scored", "ai_score": 88,
                })
                store.reserve_greeting_send("boss", "job-id", "您好")
                store.finish_greeting_send(
                    "boss", "job-id", outcome="sent",
                    chat_url="https://www.zhipin.com/web/geek/chat?jobId=other")
            with patch("api.jobs.ChromeBrowser") as browser_type:
                with self.assertRaisesRegex(JobActionError, "有效的 BOSS 会话链接"):
                    open_conversation(db_path, config, "boss", "job-id")
            browser_type.assert_not_called()

    def test_user_tab_uses_native_cdp_target(self) -> None:
        browser = object.__new__(ChromeBrowser)
        browser._browser = MagicMock()
        session = browser._browser.new_browser_cdp_session.return_value
        session.send.side_effect = [{"targetId": "target-id"}, {}]
        url = "https://www.zhipin.com/web/geek/chat?jobId=job-id"
        self.assertTrue(browser.open_user_tab(url))
        self.assertEqual(session.send.call_args_list[0].args,
                         ("Target.createTarget", {"url": url}))
        self.assertEqual(session.send.call_args_list[1].args,
                         ("Target.activateTarget", {"targetId": "target-id"}))
        session.detach.assert_called_once()

    def test_verified_send_stays_greeted_and_saves_conversation_url(self) -> None:
        config = deepcopy(DEFAULT_CONFIG)
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "jobs.sqlite"
            with closing(sqlite3.connect(db_path)) as conn:
                JobStore(conn, score_threshold=71).save({
                    "source_platform": "boss", "source_job_id": "job-id",
                    "title": "前端工程师", "company": "示例公司",
                    "url": "https://www.zhipin.com/job_detail/job-id.html",
                    "ai_score_status": "scored", "ai_score": 88,
                })
            chat_url = "https://www.zhipin.com/web/geek/chat?jobId=job-id&uid=recruiter"
            with patch("api.jobs.send_boss_greeting", return_value=SendResult(
                True, False, "已确认发送", chat_url,
            )):
                response = send_greeting(db_path, config, "boss", "job-id", "您好，想了解岗位。")
            job = response["job"]
            self.assertEqual(job["job_status"], "greeted")
            self.assertEqual(job["job_status_history"], ["scored", "greeting_ready", "greeted"])
            self.assertEqual(job["greeting"], "您好，想了解岗位。")
            self.assertEqual(job["chat_url"], chat_url)
            with closing(sqlite3.connect(db_path)) as conn:
                self.assertEqual(JobStore(conn).get_job("boss", "job-id")["chat_url"], chat_url)

    def test_uncertain_send_keeps_reason_and_prevents_retry(self) -> None:
        config = deepcopy(DEFAULT_CONFIG)
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "jobs.sqlite"
            with closing(sqlite3.connect(db_path)) as conn:
                JobStore(conn, score_threshold=71).save({
                    "source_platform": "boss", "source_job_id": "job-id",
                    "ai_score_status": "scored", "ai_score": 88,
                })
            with patch("api.jobs.send_boss_greeting", return_value=SendResult(
                False, True, "未能确认进入对应岗位会话",
            )):
                with self.assertRaisesRegex(JobActionError, "未能确认进入对应岗位会话"):
                    send_greeting(db_path, config, "boss", "job-id", "您好")
            with closing(sqlite3.connect(db_path)) as conn:
                store = JobStore(conn)
                job = store.get_job("boss", "job-id")
                self.assertEqual(job["greeting_send_state"], "unknown")
                self.assertEqual(job["greeting_send_note"], "未能确认进入对应岗位会话")
                self.assertEqual(job["job_status"], "greeting_ready")
                self.assertFalse(store.reserve_greeting_send("boss", "job-id", "再发一次"))
            with self.assertRaisesRegex(JobActionError, "请先确认"):
                confirm_greeting_not_sent(db_path, config, "boss", "job-id", confirmed=False)
            with patch("api.jobs.inspect_boss_greeting", return_value=SendResult(
                False, False, "对应会话未找到这条招呼语",
            )):
                recovered = confirm_greeting_not_sent(
                    db_path, config, "boss", "job-id", confirmed=True)
            self.assertEqual(recovered["greeting_send_state"], "idle")
            self.assertEqual(recovered["greeting"], "您好")
            self.assertEqual(recovered["job_status"], "greeting_ready")

    def test_confirm_not_sent_rechecks_boss_before_unlocking(self) -> None:
        config = deepcopy(DEFAULT_CONFIG)
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "jobs.sqlite"
            with closing(sqlite3.connect(db_path)) as conn:
                store = JobStore(conn, score_threshold=71)
                store.save({
                    "source_platform": "boss", "source_job_id": "job-id",
                    "ai_score_status": "scored", "ai_score": 88,
                })
                store.reserve_greeting_send("boss", "job-id", "您好")
                store.finish_greeting_send("boss", "job-id", outcome="unknown")
            chat_url = "https://www.zhipin.com/web/geek/chat?jobId=job-id"
            with patch("api.jobs.inspect_boss_greeting", return_value=SendResult(
                True, False, "已在 BOSS 会话中确认招呼语", chat_url,
            )):
                recovered = confirm_greeting_not_sent(
                    db_path, config, "boss", "job-id", confirmed=True)
            self.assertEqual(recovered["job_status"], "greeted")
            self.assertEqual(recovered["greeting_send_state"], "sent")
            self.assertEqual(recovered["chat_url"], chat_url)

    def test_inconclusive_recheck_keeps_send_locked(self) -> None:
        config = deepcopy(DEFAULT_CONFIG)
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "jobs.sqlite"
            with closing(sqlite3.connect(db_path)) as conn:
                store = JobStore(conn, score_threshold=71)
                store.save({
                    "source_platform": "boss", "source_job_id": "job-id",
                    "ai_score_status": "scored", "ai_score": 88,
                })
                store.reserve_greeting_send("boss", "job-id", "您好")
                store.finish_greeting_send("boss", "job-id", outcome="unknown")
            with patch("api.jobs.inspect_boss_greeting", return_value=SendResult(
                False, True, "会话内容尚未确认对应此岗位",
            )):
                with self.assertRaisesRegex(JobActionError, "不能解除重发锁定"):
                    confirm_greeting_not_sent(
                        db_path, config, "boss", "job-id", confirmed=True)
            with closing(sqlite3.connect(db_path)) as conn:
                self.assertEqual(JobStore(conn).get_job("boss", "job-id")[
                    "greeting_send_state"], "unknown")

    def test_verified_chat_recovers_uncertain_send_without_resending(self) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "jobs.sqlite"
            chat_url = "https://www.zhipin.com/web/geek/chat?jobId=job-id"
            with closing(sqlite3.connect(db_path)) as conn:
                store = JobStore(conn, score_threshold=71)
                store.save({
                    "source_platform": "boss", "source_job_id": "job-id",
                    "ai_score_status": "scored", "ai_score": 88,
                })
                self.assertTrue(store.reserve_greeting_send("boss", "job-id", "您好"))
                self.assertTrue(store.finish_greeting_send(
                    "boss", "job-id", outcome="unknown", note="发送结果未确认"))
                self.assertTrue(store.mark_greeting_sent_after_verification(
                    "boss", "job-id", chat_url))
                job = store.get_job("boss", "job-id")
                self.assertEqual(job["greeting_send_state"], "sent")
                self.assertEqual(job["job_status"], "greeted")
                self.assertEqual(job["chat_url"], chat_url)
                self.assertEqual(job["job_status_history"], ["scored", "greeting_ready", "greeted"])
                self.assertFalse(store.mark_greeting_sent_after_verification(
                    "boss", "job-id", chat_url))

    def test_failure_before_send_button_is_retryable(self) -> None:
        job = {"source_job_id": "job-id", "url": "https://www.zhipin.com/job_detail/job-id.html"}
        with patch("browser.boss_message.ChromeBrowser") as browser_type, \
                patch("browser.boss_message._chat_matches_job", return_value=True), \
                patch("browser.boss_message._wait_for_chat") as wait_for_chat, \
                patch("browser.boss_message._delivery_state", return_value="missing"), \
                patch("browser.boss_message.time.sleep"):
            browser = browser_type.return_value
            browser.new_tab.return_value = "tab"
            page = browser.page.return_value
            page.context.pages = [page]
            page.evaluate.return_value = False
            wait_for_chat.return_value = page
            button = MagicMock()
            button.count.return_value = 1
            button.nth.return_value.is_visible.return_value = True
            button.nth.return_value.get_attribute.return_value = "/web/geek/chat?jobId=job-id"
            input_box = MagicMock()
            input_box.first.wait_for.side_effect = BrowserTimeout("输入框未加载")
            page.locator.side_effect = [button, input_box]
            result = send_boss_greeting(job, "您好", "http://127.0.0.1:9222")
            self.assertFalse(result.verified)
            self.assertFalse(result.uncertain)
            self.assertIn("8 秒内未出现", result.message)

    def test_click_error_keeps_send_result_uncertain(self) -> None:
        job = {"source_job_id": "job-id", "url": "https://www.zhipin.com/job_detail/job-id.html"}
        with patch("browser.boss_message.ChromeBrowser") as browser_type, \
                patch("browser.boss_message._chat_matches_job", return_value=True), \
                patch("browser.boss_message._wait_for_chat") as wait_for_chat, \
                patch("browser.boss_message._delivery_state", return_value="missing"), \
                patch("browser.boss_message.time.sleep"):
            browser = browser_type.return_value
            browser.new_tab.return_value = "tab"
            page = browser.page.return_value
            page.context.pages = [page]
            page.evaluate.return_value = False
            wait_for_chat.return_value = page
            button = MagicMock()
            button.count.return_value = 1
            button.nth.return_value.is_visible.return_value = True
            button.nth.return_value.get_attribute.return_value = "/web/geek/chat?jobId=job-id"
            input_box = MagicMock()
            input_box.count.return_value = 1
            input_box.first.is_visible.return_value = True
            input_box.first.inner_text.return_value = "您好"
            send_button = MagicMock()
            send_button.count.return_value = 1
            send_button.first.is_enabled.return_value = True
            send_button.first.click.side_effect = RuntimeError("点击后连接断开")
            page.locator.side_effect = [button, input_box, send_button]
            result = send_boss_greeting(job, "您好", "http://127.0.0.1:9222")
            self.assertFalse(result.verified)
            self.assertTrue(result.uncertain)


if __name__ == "__main__":
    unittest.main()
