"""A chat opens only when recruiter, job and rendered message evidence agree."""

import json
import shutil
import subprocess
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit
import unittest
from unittest.mock import MagicMock

from browser.browser import (ChromeBrowser, ConversationLocationError,
                             _BOSS_CHAT_CONTENT_MATCHES, _BOSS_CHAT_HIGHLIGHT_ROW, _BOSS_CHAT_ROW_MATCHES)


CHAT_URL = "https://www.zhipin.com/web/geek/chat"
FRIEND = {"uid": "123", "encryptBossId": "boss-a", "encryptJobId": "job-a",
          "securityId": "security +/=&", "avatar": "https://example.com/shared.png",
          "name": "李女士", "brandName": "示例公司"}


def friend_response(friends):
    return SimpleNamespace(
        url="https://www.zhipin.com/wapi/zprelation/friend/getGeekFriendList.json",
        json=lambda: {"code": 0, "zpData": {"result": friends}})


def history_response(boss_id="boss-a", *, code=0, messages=None):
    return SimpleNamespace(
        url="https://www.zhipin.com/wapi/zpchat/geek/historyMsg?" + urlencode({"bossId": boss_id}),
        json=lambda: {"code": code, "zpData": {"messages": [{"mid": 9876543210123}]
                                               if messages is None else messages}})


class ExactConversationNavigationTests(unittest.TestCase):
    def adapter(self, *, friends=None, history=None, rendered=True, updated_friend=None,
                highlight="located"):
        browser = object.__new__(ChromeBrowser)
        browser._context = MagicMock()
        page = browser._context.new_page.return_value
        listeners = []
        page.on.side_effect = lambda event, callback: listeners.append(callback)
        target_friends = [FRIEND] if friends is None else friends
        page.get_by_text.return_value.click.side_effect = lambda **_: listeners[0](
            friend_response(target_friends))

        def navigate(url, **_):
            page.url = url
            listeners[0](friend_response(target_friends))
            if urlsplit(url).query:
                if updated_friend:
                    listeners[0](friend_response([updated_friend]))
                listeners[0](history or history_response())

        page.goto.side_effect = navigate

        def evaluate(expression, arg=None):
            if expression == _BOSS_CHAT_HIGHLIGHT_ROW:
                return highlight
            if expression == _BOSS_CHAT_CONTENT_MATCHES:
                return rendered
            if expression == _BOSS_CHAT_ROW_MATCHES:
                raise AssertionError("Exact navigation must not choose a row by avatar/name")
            return {"top": 500, "height": 1000, "rows": 5}

        page.evaluate.side_effect = evaluate
        return browser, page

    def test_uses_exact_ids_even_when_another_recruiter_has_the_same_avatar_and_name(self):
        other = {**FRIEND, "uid": "456", "encryptBossId": "boss-b"}
        browser, page = self.adapter(friends=[other, FRIEND])
        self.assertTrue(browser.open_user_conversation(CHAT_URL, "123", source_job_id="job-a"))
        queries = [parse_qs(urlsplit(call.args[0]).query) for call in page.goto.call_args_list]
        self.assertEqual(queries, [{}, {"jobId": ["job-a"], "id": ["boss-a"],
                                       "securityId": [FRIEND["securityId"]]}])
        page.locator.return_value.nth.assert_not_called()
        page.evaluate.assert_any_call(_BOSS_CHAT_CONTENT_MATCHES,
                                      {"messageIds": ["9876543210123"], "jobId": "job-a"})
        page.bring_to_front.assert_called_once()
        page.close.assert_not_called()

    def test_native_route_does_not_depend_on_avatar_being_available(self):
        browser, _ = self.adapter(friends=[{**FRIEND, "avatar": ""}])
        self.assertTrue(browser.open_user_conversation(CHAT_URL, "123", source_job_id="job-a"))

    def test_exchange_menu_uses_exact_recruiter_and_job_even_with_identical_visual_rows(self):
        other = {**FRIEND, "uid": "456", "encryptBossId": "boss-b"}
        browser, page = self.adapter(friends=[other, FRIEND])
        self.assertTrue(browser.open_user_conversation(
            CHAT_URL, "123", "有交换", source_job_id="job-a"))
        page.locator.return_value.filter.assert_called_once_with(has_text="更多")
        page.get_by_text.assert_called_once_with("有交换", exact=True)
        query = parse_qs(urlsplit(page.goto.call_args.args[0]).query)
        self.assertEqual((query["jobId"], query["id"]), (["job-a"], ["boss-a"]))
        page.locator.return_value.nth.assert_not_called()
        page.bring_to_front.assert_called_once()
        page.close.assert_not_called()

    def test_exchange_rejects_wrong_job_recruiter_or_unrendered_history(self):
        cases = [
            ({"friends": [{**FRIEND, "encryptJobId": "other-job"}]}, "岗位已变化"),
            ({"friends": [{**FRIEND, "uid": "456"}]}, "未找到对应招聘方"),
            ({"history": history_response("boss-b")}, "聊天区"),
            ({"rendered": False}, "聊天区"),
            ({"updated_friend": {**FRIEND, "encryptJobId": "other-job"}}, "岗位发生变化"),
        ]
        for options, reason in cases:
            with self.subTest(reason=reason, options=options):
                browser, page = self.adapter(**options)
                with self.assertRaisesRegex(ConversationLocationError, reason) as error:
                    browser.open_user_conversation(CHAT_URL, "123", "有交换", source_job_id="job-a")
                self.assertIn("重新扫描「有交换」", str(error.exception))
                page.bring_to_front.assert_not_called()
                page.close.assert_called_once()

    def test_same_recruiter_for_a_different_job_is_rejected_before_opening_chat(self):
        browser, page = self.adapter(friends=[{**FRIEND, "encryptJobId": "job-b"}])
        with self.assertRaisesRegex(ConversationLocationError, "岗位已变化"):
            browser.open_user_conversation(CHAT_URL, "123", source_job_id="job-a")
        self.assertEqual(page.goto.call_count, 1)
        page.bring_to_front.assert_not_called()
        page.close.assert_called_once()

    def test_missing_recruiter_route_id_is_not_replaced_with_a_visual_match(self):
        browser, page = self.adapter(friends=[{**FRIEND, "encryptBossId": ""}])
        with self.assertRaisesRegex(ConversationLocationError, "招聘方标识"):
            browser.open_user_conversation(CHAT_URL, "123", source_job_id="job-a")
        self.assertEqual(page.goto.call_count, 1)
        page.close.assert_called_once()

    def test_a_history_response_for_another_recruiter_is_not_proof(self):
        browser, page = self.adapter(history=history_response("boss-b"))
        with self.assertRaisesRegex(ConversationLocationError, "聊天区"):
            browser.open_user_conversation(CHAT_URL, "123", source_job_id="job-a")
        page.bring_to_front.assert_not_called()
        page.close.assert_called_once()

    def test_failed_or_empty_history_is_not_proof(self):
        for response in (history_response(code=1), history_response(messages=[])):
            with self.subTest(response=response):
                browser, page = self.adapter(history=response)
                with self.assertRaisesRegex(ConversationLocationError, "聊天区"):
                    browser.open_user_conversation(CHAT_URL, "123", source_job_id="job-a")
                page.bring_to_front.assert_not_called()
                page.close.assert_called_once()

    def test_matching_history_must_actually_be_rendered(self):
        browser, page = self.adapter(rendered=False)
        with self.assertRaisesRegex(ConversationLocationError, "聊天区"):
            browser.open_user_conversation(CHAT_URL, "123", source_job_id="job-a")
        page.bring_to_front.assert_not_called()
        page.close.assert_called_once()

    def test_job_change_during_navigation_is_rejected(self):
        browser, page = self.adapter(updated_friend={**FRIEND, "encryptJobId": "job-b"})
        with self.assertRaisesRegex(ConversationLocationError, "岗位发生变化"):
            browser.open_user_conversation(CHAT_URL, "123", source_job_id="job-a")
        page.bring_to_front.assert_not_called()
        page.close.assert_called_once()

    def test_unknown_uid_is_not_replaced_with_a_same_name_recruiter(self):
        browser, page = self.adapter(friends=[{**FRIEND, "uid": "456"}])
        with self.assertRaisesRegex(ConversationLocationError, "未找到对应招聘方"):
            browser.open_user_conversation(CHAT_URL, "123", source_job_id="job-a")
        self.assertEqual(page.goto.call_count, 1)
        page.bring_to_front.assert_not_called()
        page.close.assert_called_once()

    def test_new_greeting_highlights_exact_headers_without_opening_chat_or_history(self):
        browser, page = self.adapter()
        self.assertTrue(browser.locate_user_conversation("123", "新招呼", source_job_id="job-a"))
        page.goto.assert_called_once_with(CHAT_URL, wait_until="domcontentloaded", timeout=20000)
        page.evaluate.assert_any_call(_BOSS_CHAT_HIGHLIGHT_ROW, {
            "avatar": FRIEND["avatar"], "name": FRIEND["name"],
            "brand": FRIEND["brandName"], "exactIdentity": True})
        page.locator.return_value.nth.assert_not_called()
        self.assertFalse(any(call.args[0] == _BOSS_CHAT_CONTENT_MATCHES
                             for call in page.evaluate.call_args_list))
        page.bring_to_front.assert_called_once()
        page.close.assert_not_called()

    def test_new_greeting_without_a_job_can_be_located_by_an_unambiguous_recruiter(self):
        browser, page = self.adapter(friends=[{**FRIEND, "encryptJobId": ""}])
        self.assertTrue(browser.locate_user_conversation("123", "新招呼", source_job_id=""))
        self.assertEqual(page.goto.call_count, 1)
        page.locator.return_value.nth.assert_not_called()

    def test_new_greeting_rejects_changed_job_and_incomplete_or_ambiguous_identity(self):
        cases = [
            ({"friends": [{**FRIEND, "encryptJobId": "other-job"}]}, "关联岗位已变化"),
            ({"friends": [{**FRIEND, "name": ""}]}, "身份信息不完整"),
            ({"friends": [FRIEND, {**FRIEND, "uid": "456", "avatar": FRIEND["avatar"] + "?x=1"}]},
             "身份信息相同"),
            ({"highlight": "ambiguous"}, "多个相同会话"),
            ({"highlight": "missing"}, "未找到对应招聘方"),
            ({"friends": [{**FRIEND, "uid": "456"}]}, "未找到对应招聘方"),
        ]
        for options, reason in cases:
            with self.subTest(reason=reason):
                browser, page = self.adapter(**options)
                with self.assertRaisesRegex(ConversationLocationError, reason):
                    browser.locate_user_conversation("123", "新招呼", source_job_id="job-a")
                page.locator.return_value.nth.assert_not_called()
                self.assertEqual(page.goto.call_count, 1)
                page.bring_to_front.assert_not_called()
                page.close.assert_called_once()


@unittest.skipUnless(shutil.which("node"), "Node is needed to evaluate the rendered-chat check")
class RenderedConversationEvidenceTests(unittest.TestCase):
    def matches(self, *, mids=None, attributes=None, route_job="job-a", job_links=None):
        # Run the production JS against a DOM fixture; no browser/network access.
        fixture = {
            "source": _BOSS_CHAT_CONTENT_MATCHES,
            "mids": ["9876543210123"] if mids is None else mids,
            "attributes": attributes or [], "jobLinks": job_links or [],
            "url": CHAT_URL + "?" + urlencode({"jobId": route_job}),
        }
        script = """
            const fs = require('fs');
            const f = JSON.parse(fs.readFileSync(0, 'utf8'));
            global.location = {href: f.url};
            const record = {
                __vue__: {list$: f.mids.map(mid => ({mid}))},
                querySelectorAll: () => f.attributes.map(attrs => ({
                    getAttribute: key => attrs[key] || null,
                })),
            };
            global.document = {querySelector: selector => selector === '.chat-record' ? record : {
                querySelectorAll: () => f.jobLinks.map(href => ({href})),
            }};
            process.stdout.write(JSON.stringify(eval('(' + f.source + ')')({
                messageIds: ['9876543210123'], jobId: 'job-a',
            })));
        """
        result = subprocess.run([shutil.which("node"), "-e", script],
                                input=json.dumps(fixture), capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def test_matching_url_without_the_recruiters_rendered_message_is_not_proof(self):
        self.assertFalse(self.matches(mids=[]))
        self.assertFalse(self.matches(mids=["message-from-another-chat"]))

    def test_exact_message_from_the_recruiters_history_proves_the_loaded_content(self):
        self.assertTrue(self.matches())
        self.assertTrue(self.matches(mids=[], attributes=[{"data-mid": "9876543210123"}]))

    def test_a_different_job_in_the_route_or_displayed_job_card_is_rejected(self):
        self.assertFalse(self.matches(route_job="job-b"))
        self.assertFalse(self.matches(job_links=["https://www.zhipin.com/job_detail/job-b.html"]))
        self.assertTrue(self.matches(job_links=["https://www.zhipin.com/job_detail/job-a.html"]))


if __name__ == "__main__":
    unittest.main()
