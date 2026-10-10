"""Send an approved follow-up after checking its original chat and source message."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

from browser.boss_message import CHAT_INPUT, SEND_BUTTON
from browser.boss_monitor import CHAT_URL, _effective, _fetch_history, _normalise_message
from browser.browser import ChromeBrowser


@dataclass(frozen=True)
class FollowupResult:
    verified: bool
    uncertain: bool
    message: str
    message_id: str = ""
    skipped: bool = False


def send_boss_followup(cdp_url: str, conversation_id: str, anchor_id: str,
                       text: str, *, anchor_text: str | None = None) -> FollowupResult:
    """Recheck the exact chat and latest message before one UI send."""
    browser = ChromeBrowser(cdp_url)
    attempted = False
    expected_anchor_text = text.strip() if anchor_text is None else anchor_text.strip()
    try:
        tab = browser.new_tab("https://www.zhipin.com/robots.txt", background=True)
        if not tab:
            return FollowupResult(False, False, "无法打开 BOSS 会话页")
        page = browser.page(tab)
        friends: dict[str, dict[str, Any]] = {}

        def collect(response: Any) -> None:
            if "/wapi/zprelation/friend/getGeekFriendList.json" not in response.url:
                return
            try:
                body = response.json()
                if body.get("code") == 0:
                    for row in (body.get("zpData") or {}).get("result") or []:
                        if row.get("uid"):
                            friends[str(row["uid"])] = row
            except Exception:
                pass

        page.on("response", collect)
        page.goto(CHAT_URL, wait_until="domcontentloaded", timeout=20000)
        page.locator(".friend-content").first.wait_for(state="attached", timeout=15000)
        page.wait_for_timeout(500)
        only_tab = page.locator("li").filter(has_text="仅沟通").first
        only_tab.click(timeout=10000)
        page.wait_for_timeout(700)
        if "selected" not in (only_tab.get_attribute("class") or "").split():
            return FollowupResult(False, False, "未能切换到「仅沟通」")

        item = None
        friend = None
        for _ in range(50):
            friend = friends.get(conversation_id)
            avatar = str((friend or {}).get("avatar") or "").split("?", 1)[0]
            if avatar:
                matches = page.evaluate("""({avatar, name, brand}) => [...document.querySelectorAll('.friend-content')]
                    .flatMap((node, index) => {
                        const text = node.innerText || '';
                        return node.querySelector('img.image-circle')?.src.split('?')[0] === avatar
                            && (!name || text.includes(name)) && (!brand || text.includes(brand))
                            ? [index] : [];
                    })""", {"avatar": avatar, "name": str(friend.get("name") or ""),
                             "brand": str(friend.get("brandName") or "")})
                if len(matches) == 1:
                    item = page.locator(".friend-content").nth(matches[0])
                    break
            page.evaluate("""() => {
                const list = document.querySelector('.user-list-content');
                if (list) list.scrollTop = list.scrollHeight;
            }""")
            page.wait_for_timeout(350)
        if item is None or friend is None:
            return FollowupResult(False, False, "「仅沟通」中未找到目标会话，已跳过，未发送", skipped=True)

        history = [_normalise_message(raw, conversation_id)
                   for raw in _fetch_history(page, friend, 20)]
        latest = next((message for message in reversed(history) if _effective(message)), None)
        if (latest is None or latest["sender"] != "self"
                or latest["message_id"] != anchor_id
                or latest["text"].strip() != expected_anchor_text
                or latest["delivery_status"] not in {"1", "2"}):
            return FollowupResult(False, False, "会话最后一条消息已变化，追问未发送")

        item.click(timeout=5000)
        page.wait_for_function("""avatar => [...document.querySelectorAll('.friend-content')]
            .some(node => node.classList.contains('selected') &&
                node.querySelector('img.image-circle')?.src.split('?')[0] === avatar)""",
            arg=str(friend["avatar"]).split("?", 1)[0], timeout=5000)
        latest_after_open = next((message for message in reversed([
            _normalise_message(raw, conversation_id) for raw in _fetch_history(page, friend, 20)
        ]) if _effective(message)), None)
        if (latest_after_open is None or latest_after_open["message_id"] != anchor_id
                or latest_after_open["sender"] != "self"
                or latest_after_open["text"].strip() != expected_anchor_text
                or latest_after_open["delivery_status"] not in {"1", "2"}):
            return FollowupResult(False, False, "打开会话后发现新消息，追问未发送")
        input_box = page.locator(CHAT_INPUT).first
        input_box.wait_for(state="visible", timeout=8000)
        input_box.fill(text)
        if input_box.inner_text().strip() != text.strip():
            return FollowupResult(False, False, "追问文本未正确填入，未发送")
        button = page.locator(SEND_BUTTON).first
        button.wait_for(state="visible", timeout=8000)
        if not button.is_enabled():
            return FollowupResult(False, False, "发送按钮不可用，未发送")
        attempted = True
        button.click(timeout=5000)

        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            recent = [_normalise_message(raw, conversation_id)
                      for raw in _fetch_history(page, friend, 20)]
            sent = next((message for message in reversed(recent)
                         if message["sender"] == "self"
                         and message["message_id"].isdigit()
                         and int(message["message_id"]) > int(anchor_id)
                         and message["text"].strip() == text.strip()
                         and message["delivery_status"] in {"1", "2"}), None)
            if sent:
                return FollowupResult(True, False, "BOSS 会话中已确认追问发送", sent["message_id"])
            time.sleep(0.5)
        return FollowupResult(False, True, "追问已提交但未能确认，请人工核查")
    except Exception as exc:
        return FollowupResult(False, attempted, f"追问发送异常：{type(exc).__name__}")
    finally:
        browser.close()
