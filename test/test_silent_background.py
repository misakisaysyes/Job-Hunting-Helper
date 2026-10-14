"""Silent runs must never create or activate a foreground Chrome page."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from api.monitoring import MonitoringRun
from api.monitoring_batch import MonitoringBatchRun
from browser.boss_message import (
    CHAT_BUTTON, CHAT_INPUT, SEND_BUTTON, SendResult, _chat_matches_job,
    send_boss_greeting,
)
from browser.browser import ChromeBrowser
from config import DEFAULT_CONFIG


class BackgroundBrowserTests(unittest.TestCase):
    def make_browser(self):
        browser = object.__new__(ChromeBrowser)
        browser._cdp_url = "http://127.0.0.1:9222"
        browser._playwright = MagicMock()
        browser._browser = MagicMock()
        browser._context = MagicMock()
        browser._context.pages = []
        browser._tabs = {}
        browser._list_worker = None
        browser._background_worker = None
        browser._background_browser = None
        browser.silent_background = True
        return browser

    def test_reuses_one_unfocused_tab_for_list_and_details(self):
        browser = self.make_browser()
        page = MagicMock()
        page.is_closed.return_value = False
        page.url = ""

        def goto(url, **_):
            page.url = url

        page.goto.side_effect = goto
        page.evaluate.return_value = {"http_status": 200, "body": {"code": 0}}
        session = browser._browser.new_browser_cdp_session.return_value
        worker_browser = browser._playwright.chromium.connect_over_cdp.return_value
        worker_context = worker_browser.contexts[0]
        worker_context.pages = []

        def send(method, params):
            if method == "Target.createTarget":
                page.url = params["url"]
                worker_context.pages = [page]
                return {"targetId": "chrome-target"}
            if method == "Target.closeTarget":
                return {}
            raise AssertionError(f"Unexpected CDP command: {method}")

        session.send.side_effect = send
        first = browser.new_tab("https://www.zhipin.com/robots.txt", background=True)
        self.assertIsNotNone(first)
        second = browser.new_tab("https://www.zhipin.com/job_detail/example.html", background=True)
        self.assertEqual(second, first)
        self.assertEqual(session.send.call_count, 1)
        browser._playwright.chromium.connect_over_cdp.assert_called_once_with(browser._cdp_url)
        self.assertEqual(browser._context.pages, [])
        self.assertEqual(session.send.call_args.args, ("Target.createTarget", {
            "url": session.send.call_args.args[1]["url"],
            "background": True, "focus": False,
        }))
        browser._context.new_page.assert_not_called()

        browser._list_worker = first
        result = browser.request_boss_json("/wapi/zpgeek/example", "GET", {"page": "1"})
        self.assertEqual(result["http_status"], 200)
        self.assertEqual(page.url, "https://www.zhipin.com/robots.txt")
        self.assertEqual(session.send.call_count, 1)

        self.assertTrue(browser.close_tab(first))
        page.close.assert_called_once()
        worker_browser.close.assert_called_once()

    def test_cdp_failure_does_not_fall_back_to_foreground(self):
        browser = self.make_browser()
        browser._browser.new_browser_cdp_session.return_value.send.side_effect = RuntimeError("unsupported")
        self.assertIsNone(browser.new_tab("https://www.zhipin.com/robots.txt", background=True))
        browser._context.new_page.assert_not_called()
        self.assertEqual(browser._tabs, {})

    def test_worker_connection_failure_closes_unusable_background_target(self):
        browser = self.make_browser()
        session = browser._browser.new_browser_cdp_session.return_value
        session.send.return_value = {"targetId": "chrome-target"}
        browser._playwright.chromium.connect_over_cdp.side_effect = RuntimeError("disconnected")
        self.assertIsNone(browser.new_tab("https://www.zhipin.com/robots.txt", background=True))
        self.assertEqual(session.send.call_args.args,
                         ("Target.closeTarget", {"targetId": "chrome-target"}))
        browser._context.new_page.assert_not_called()

    def test_chat_url_alone_does_not_prove_conversation_selected(self):
        page = MagicMock()
        page.url = "https://www.zhipin.com/web/geek/chat?jobId=job-id"
        page.evaluate.return_value = False
        self.assertFalse(_chat_matches_job(page, {
            "source_job_id": "job-id", "company": "示例公司", "title": "前端工程师",
        }))
        page.evaluate.assert_called_once()

    def test_silent_greeting_initialises_chat_in_background_without_popup(self):
        browser = MagicMock()
        browser.new_tab.return_value = "worker"
        page = browser.page.return_value
        page.context.pages = [page]
        page.evaluate.return_value = False
        chat_button = MagicMock()
        chat_button.count.return_value = 1
        chat_button.nth.return_value.is_visible.return_value = True
        chat_button.nth.return_value.get_attribute.side_effect = lambda name: {
            "redirect-url": "/web/geek/chat?jobId=job-id&uid=recruiter",
            "data-url": "/wapi/zpgeek/friend/add.json?jobId=job-id&securityId=token",
        }[name]
        input_box = MagicMock()
        input_box.first.inner_text.return_value = "您好"
        send_button = MagicMock()
        send_button.first.is_enabled.return_value = True
        page.locator.side_effect = lambda selector: {
            CHAT_BUTTON: chat_button, CHAT_INPUT: input_box, SEND_BUTTON: send_button,
        }[selector]
        job = {"url": "https://www.zhipin.com/job_detail/job-id.html",
               "source_job_id": "job-id", "company": "示例公司", "title": "前端工程师"}
        with patch("browser.boss_message.ChromeBrowser", return_value=browser), \
                patch("browser.boss_message._chat_matches_job", return_value=True), \
                patch("browser.boss_message._wait_for_chat", return_value=page), \
                patch("browser.boss_message._delivery_state", return_value="missing"), \
                patch("browser.boss_message._wait_for_delivery",
                      return_value=SendResult(True, False, "已确认")), \
                patch("browser.boss_message.time.sleep"):
            result = send_boss_greeting(job, "您好", "cdp", silent_background=True)
        self.assertTrue(result.verified)
        chat_button.nth.return_value.click.assert_not_called()
        chat_button.nth.return_value.evaluate.assert_called_once_with("element => element.click()")
        self.assertTrue(any("window.open" in str(call) for call in page.evaluate.call_args_list))
        page.goto.assert_not_called()
        send_button.first.click.assert_called_once_with(timeout=5000)

    def test_silent_greeting_stops_if_chat_does_not_open_after_entry_click(self):
        browser = MagicMock()
        browser.new_tab.return_value = "worker"
        page = browser.page.return_value
        page.context.pages = [page]
        page.evaluate.return_value = False
        button = MagicMock()
        button.count.return_value = 1
        button.nth.return_value.is_visible.return_value = True
        button.nth.return_value.get_attribute.side_effect = lambda name: {
            "redirect-url": "/web/geek/chat?jobId=job-id",
            "data-url": "/wapi/zpgeek/friend/add.json?jobId=job-id",
        }[name]
        page.locator.return_value = button
        job = {"url": "https://www.zhipin.com/job_detail/job-id.html",
               "source_job_id": "job-id", "company": "示例公司", "title": "前端工程师"}
        with patch("browser.boss_message.ChromeBrowser", return_value=browser), \
                patch("browser.boss_message._wait_for_chat", return_value=None), \
                patch("browser.boss_message.time.sleep"):
            result = send_boss_greeting(job, "您好", "cdp", silent_background=True)
        self.assertFalse(result.verified)
        self.assertTrue(result.uncertain)
        self.assertIn("自定义招呼语尚未提交", result.message)
        button.nth.return_value.evaluate.assert_called_once()
        page.goto.assert_not_called()

    def test_silent_greeting_rejects_mismatched_entry_request_without_clicking(self):
        browser = MagicMock()
        browser.new_tab.return_value = "worker"
        page = browser.page.return_value
        page.context.pages = [page]
        button = MagicMock()
        button.count.return_value = 1
        button.nth.return_value.is_visible.return_value = True
        button.nth.return_value.get_attribute.side_effect = lambda name: {
            "redirect-url": "/web/geek/chat?jobId=job-id",
            "data-url": "/wapi/zpgeek/friend/add.json?jobId=other-job",
        }[name]
        page.locator.return_value = button
        job = {"url": "https://www.zhipin.com/job_detail/job-id.html",
               "source_job_id": "job-id"}
        with patch("browser.boss_message.ChromeBrowser", return_value=browser):
            result = send_boss_greeting(job, "您好", "cdp", silent_background=True)
        self.assertFalse(result.verified)
        self.assertFalse(result.uncertain)
        button.nth.return_value.evaluate.assert_not_called()
        page.goto.assert_not_called()

    def test_silent_greeting_stops_when_boss_sent_a_preset_greeting(self):
        browser = MagicMock()
        browser.new_tab.return_value = "worker"
        page = browser.page.return_value
        page.context.pages = [page]
        button = MagicMock()
        button.count.return_value = 1
        button.nth.return_value.is_visible.return_value = True
        button.nth.return_value.get_attribute.side_effect = lambda name: {
            "redirect-url": "/web/geek/chat?jobId=job-id",
            "data-url": "/wapi/zpgeek/friend/add.json?jobId=job-id",
        }[name]
        page.locator.return_value = button
        job = {"url": "https://www.zhipin.com/job_detail/job-id.html",
               "source_job_id": "job-id", "company": "示例公司", "title": "前端工程师"}
        with patch("browser.boss_message.ChromeBrowser", return_value=browser), \
                patch("browser.boss_message._preset_greeting_visible", return_value=True), \
                patch("browser.boss_message.inspect_boss_greeting",
                      return_value=SendResult(False, True, "未能确认预设招呼语")), \
                patch("browser.boss_message.time.sleep"):
            result = send_boss_greeting(job, "自定义招呼语", "cdp", silent_background=True)
        self.assertFalse(result.verified)
        self.assertTrue(result.uncertain)
        self.assertIn("预设招呼语", result.message)
        button.nth.return_value.evaluate.assert_called_once()
        page.locator.assert_called_once_with(CHAT_BUTTON)

    def test_monitoring_phases_share_one_browser(self):
        with TemporaryDirectory() as directory, \
                patch.dict(DEFAULT_CONFIG["browser"], {"silent_background": True}), \
                patch("api.monitoring.ChromeBrowser") as browser_type, \
                patch("api.monitoring.scan_recent_conversations", return_value=[]) as communicated, \
                patch("api.monitoring.scan_new_greetings", return_value=[]) as greetings:
            MonitoringRun(Path(directory) / "monitoring.sqlite")._execute()
        browser_type.assert_called_once_with(DEFAULT_CONFIG["browser"]["cdp_url"],
                                             silent_background=True)
        self.assertIs(communicated.call_args.kwargs["browser"], browser_type.return_value)
        self.assertIs(greetings.call_args.kwargs["browser"], browser_type.return_value)
        browser_type.return_value.close.assert_called_once()

    def test_batch_items_share_one_browser(self):
        items = [{"platform": "boss", "conversation_id": str(number),
                  "text": "测试追问", "anchor_id": "100", "expected_text": "原招呼语"}
                 for number in (1, 2)]
        with TemporaryDirectory() as directory, \
                patch.dict(DEFAULT_CONFIG["browser"], {"silent_background": True}), \
                patch("api.monitoring_batch.Thread") as worker:
            runner = MonitoringBatchRun(Path(directory) / "batch.sqlite")
            runner.start("followup", items)
            args = worker.call_args.kwargs["args"]
            with patch("api.monitoring_batch.ChromeBrowser") as browser_type, \
                    patch("api.monitoring_batch.send_followup", return_value={}) as send:
                runner._execute(*args)
        browser_type.assert_called_once_with(DEFAULT_CONFIG["browser"]["cdp_url"],
                                             silent_background=True)
        self.assertEqual(send.call_count, 2)
        self.assertTrue(all(call.kwargs["browser"] is browser_type.return_value
                            for call in send.call_args_list))
        browser_type.return_value.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
