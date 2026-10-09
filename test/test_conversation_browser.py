"""Opening a monitored conversation must select and verify its exact list row."""

import json
import shutil
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

from patchright.sync_api import TimeoutError as BrowserTimeout

from browser.browser import ChromeBrowser, _BOSS_CHAT_ROW_MATCHES


CHAT_URL = "https://www.zhipin.com/web/geek/chat"
FRIEND = {"uid": "123", "avatar": "https://example.com/target.png?size=small",
          "name": "李女士", "brandName": "示例公司"}
IDENTITY = {"avatar": FRIEND["avatar"], "name": FRIEND["name"], "brand": FRIEND["brandName"]}


def friend_response(friends):
    return SimpleNamespace(
        url="https://www.zhipin.com/wapi/zprelation/friend/getGeekFriendList.json",
        json=lambda: {"code": 0, "zpData": {"result": friends}},
    )


class ConversationBrowserTests(unittest.TestCase):
    def adapter(self, *, friends=None, matches=None):
        browser = object.__new__(ChromeBrowser)
        browser._context = MagicMock()
        browser._playwright = MagicMock()
        browser._tabs = {}
        page = browser._context.new_page.return_value
        page.url = CHAT_URL
        page.on.side_effect = lambda event, callback: callback(friend_response(
            [FRIEND] if friends is None else friends))
        tab, rows = MagicMock(), MagicMock()
        page.locator.side_effect = lambda selector: tab if selector == "li" else rows
        pending_matches = iter([[3]] if matches is None else matches)
        last_matches = []

        def evaluate(expression, arg=None):
            nonlocal last_matches
            if expression == _BOSS_CHAT_ROW_MATCHES:
                last_matches = next(pending_matches, last_matches)
                return last_matches
            return {"top": 500, "height": 1000, "rows": 5}

        page.evaluate.side_effect = evaluate
        return browser, page, tab.filter.return_value.first, rows

    def test_open_selects_target_not_first_row_and_leaves_verified_tab_open(self):
        browser, page, tab, rows = self.adapter()
        self.assertTrue(browser.open_user_conversation(CHAT_URL, "123"))
        tab.click.assert_called_once()
        rows.nth.assert_called_once_with(3)
        item = rows.nth.return_value
        item.scroll_into_view_if_needed.assert_called_once()
        item.click.assert_called_once()
        page.evaluate.assert_any_call(_BOSS_CHAT_ROW_MATCHES, IDENTITY)
        page.wait_for_function.assert_any_call(
            _BOSS_CHAT_ROW_MATCHES, arg={**IDENTITY, "selectedOnly": True}, timeout=8000)
        page.bring_to_front.assert_called_once()
        browser.close()
        page.close.assert_not_called()

    def test_open_scrolls_until_target_row_is_loaded(self):
        browser, page, _, rows = self.adapter(matches=[[], [], [7]])
        self.assertTrue(browser.open_user_conversation(CHAT_URL, "123"))
        self.assertEqual(page.wait_for_timeout.call_count, 2)
        rows.nth.assert_called_once_with(7)
        rows.nth.return_value.click.assert_called_once()

    def test_open_waits_for_friend_identity_from_later_list_response(self):
        browser, page, _, rows = self.adapter(friends=[])
        callbacks = []
        page.on.side_effect = lambda event, listener: callbacks.append(listener)
        page.wait_for_timeout.side_effect = lambda _: callbacks[0](friend_response([FRIEND]))
        self.assertTrue(browser.open_user_conversation(CHAT_URL, "123"))
        page.wait_for_timeout.assert_called_once_with(250)
        rows.nth.return_value.click.assert_called_once()

    def test_unknown_identity_never_selects_another_conversation(self):
        browser, page, _, rows = self.adapter()
        self.assertFalse(browser.open_user_conversation(CHAT_URL, "missing"))
        rows.nth.assert_not_called()
        page.bring_to_front.assert_not_called()
        page.close.assert_called_once()

    def test_ambiguous_target_is_not_clicked(self):
        browser, page, _, rows = self.adapter(matches=[[0, 1]])
        self.assertFalse(browser.open_user_conversation(CHAT_URL, "123"))
        rows.nth.assert_not_called()
        page.close.assert_called_once()

    def test_failure_to_verify_selection_does_not_report_success(self):
        browser, page, _, rows = self.adapter()

        def wait_for_function(expression, **kwargs):
            if expression == _BOSS_CHAT_ROW_MATCHES:
                raise BrowserTimeout("target was not selected")

        page.wait_for_function.side_effect = wait_for_function
        self.assertFalse(browser.open_user_conversation(CHAT_URL, "123"))
        rows.nth.return_value.click.assert_called_once()
        page.bring_to_front.assert_not_called()
        page.close.assert_called_once()


@unittest.skipUnless(shutil.which("node"), "Node is needed to evaluate the DOM matching function")
class ConversationRowMatchingTests(unittest.TestCase):
    def match(self, rows, *, selected=False):
        # Evaluate the actual production function against a tiny DOM fixture.
        # This does not open a browser or contact BOSS.
        script = """
            const fs = require('fs');
            const {source, rows, identity} = JSON.parse(fs.readFileSync(0, 'utf8'));
            global.location = {href: 'https://www.zhipin.com/web/geek/chat'};
            global.document = {querySelectorAll: () => rows.map(row => ({
                innerText: row.text,
                querySelector: () => ({src: row.avatar}),
                classList: {contains: name => name === 'selected' && Boolean(row.selected)},
            }))};
            process.stdout.write(JSON.stringify(eval('(' + source + ')')(identity)));
        """
        result = subprocess.run([shutil.which("node"), "-e", script],
                                input=json.dumps({"source": _BOSS_CHAT_ROW_MATCHES, "rows": rows,
                                                  "identity": {**IDENTITY, "selectedOnly": selected}}),
                                capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def test_shared_avatar_is_disambiguated_by_recruiter_and_company(self):
        self.assertEqual(self.match([
            {"avatar": FRIEND["avatar"], "text": "王先生 示例公司"},
            {"avatar": FRIEND["avatar"], "text": "李女士 其他公司"},
            {"avatar": "//example.com/target.png?size=large", "text": "李女士 示例公司"},
        ]), [2])

    def test_selected_wrong_recruiter_or_company_is_not_verified(self):
        rows = [
            {"avatar": FRIEND["avatar"], "text": "王先生 示例公司", "selected": True},
            {"avatar": FRIEND["avatar"], "text": "李女士 示例公司"},
        ]
        self.assertFalse(self.match(rows, selected=True))
        rows[0]["selected"] = False
        rows[1]["selected"] = True
        self.assertTrue(self.match(rows, selected=True))

    def test_identical_rows_cannot_be_verified_even_if_one_is_selected(self):
        rows = [{"avatar": FRIEND["avatar"], "text": "李女士 示例公司", "selected": True},
                {"avatar": FRIEND["avatar"], "text": "李女士 示例公司"}]
        self.assertFalse(self.match(rows, selected=True))


if __name__ == "__main__":
    unittest.main()
