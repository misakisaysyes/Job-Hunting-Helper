"""Edited follow-ups retain the browser's source-message and receipt checks."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from browser.boss_followup import send_boss_followup


class FollowupBrowserTests(unittest.TestCase):
    def send(self, *, changed_after_open: bool = False):
        original = "您好，想了解这个岗位。"
        edited = "您好，请问方便看一下我的简历吗？"
        source = {"message_id": "100", "sender": "self", "text": original,
                  "biz_type": "101", "delivery_status": "2"}
        after_open = ({**source, "message_id": "101", "sender": "recruiter", "text": "请发简历"}
                      if changed_after_open else source)
        sent = {**source, "message_id": "102", "text": edited, "biz_type": "1", "delivery_status": "1"}
        with patch("browser.boss_followup.ChromeBrowser") as browser, \
                patch("browser.boss_followup._normalise_message", side_effect=lambda message, _: message), \
                patch("browser.boss_followup._fetch_history", side_effect=[[source], [after_open], [source, sent]]):
            page = browser.return_value.page.return_value
            page.on.side_effect = lambda event, callback: callback(SimpleNamespace(
                url="https://www.zhipin.com/wapi/zprelation/friend/getGeekFriendList.json",
                json=lambda: {"code": 0, "zpData": {"result": [
                    {"uid": "123", "avatar": "avatar", "name": "李女士", "brandName": "示例公司"},
                ]}},
            ))
            page.locator.return_value.filter.return_value.first.get_attribute.return_value = "selected"
            page.evaluate.return_value = [0]
            page.locator.return_value.first.inner_text.return_value = edited
            page.locator.return_value.first.is_enabled.return_value = True
            result = send_boss_followup("cdp", "123", "100", edited, anchor_text=original)
            fill_count = page.locator.return_value.first.fill.call_count
            send_count = page.locator.return_value.first.click.call_count
            browser.return_value.close.assert_called_once()
        return result, fill_count, send_count

    def test_edited_text_can_be_sent_when_original_anchor_is_unchanged(self) -> None:
        result, filled, clicked = self.send()
        self.assertTrue(result.verified)
        self.assertEqual(result.message_id, "102")
        self.assertEqual((filled, clicked), (1, 1))

    def test_new_recruiter_reply_blocks_edited_followup_before_input(self) -> None:
        result, filled, clicked = self.send(changed_after_open=True)
        self.assertFalse(result.verified)
        self.assertFalse(result.uncertain)
        self.assertEqual((filled, clicked), (0, 0))


if __name__ == "__main__":
    unittest.main()
