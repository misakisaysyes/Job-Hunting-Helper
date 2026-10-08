"""Read recent BOSS conversations without entering or sending in a chat."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from patchright.sync_api import TimeoutError as BrowserTimeout

from browser.browser import ChromeBrowser


CHAT_URL = "https://www.zhipin.com/web/geek/chat"
RESUME_DELIVERED = "对方已同意，您的附件简历已发送给对方"


def _effective(message: dict[str, Any]) -> bool:
    """Ignore BOSS's non-conversation cards and empty/recalled messages."""
    if str(message.get("biz_type") or "") == "317":
        return False
    text = str(message.get("text") or "").strip()
    if (message.get("sender") not in {"self", "recruiter"}
            or not text or text == "你撤回了一条消息"
            or (text == RESUME_DELIVERED and str(message.get("biz_type") or "") == "14")
            or text.startswith("您正在与Boss")):
        return False
    return not (message.get("sender") == "self"
                and str(message.get("delivery_status") or "") == "3")


def _normalise_message(raw: dict[str, Any], boss_uid: str) -> dict[str, str]:
    body = raw.get("body") if isinstance(raw.get("body"), dict) else {}
    sender_info = raw.get("from") if isinstance(raw.get("from"), dict) else {}
    sender_id = str(sender_info.get("uid") or "")
    sender = "recruiter" if sender_id == boss_uid else "self" if sender_id else "system"
    text = str(body.get("text") or body.get("headTitle") or "").strip()
    timestamp = raw.get("time") or raw.get("timestamp") or ""
    return {
        "message_id": str(raw.get("mid") or ""),
        "sender": sender,
        "text": text[:4000],
        "sent_at": str(timestamp),
        "biz_type": str(raw.get("bizType") or ""),
        "delivery_status": str(raw.get("status") if raw.get("status") is not None else ""),
    }


def _fetch_history(page: Any, friend: dict[str, Any], count: int) -> list[dict[str, Any]]:
    result = page.evaluate("""async ({bossId, securityId, count}) => {
        const url = new URL('/wapi/zpchat/geek/historyMsg', location.origin);
        Object.entries({bossId, maxMsgId: 0, c: count, page: 1, src: 0, securityId})
            .forEach(([key, value]) => url.searchParams.set(key, value));
        const response = await fetch(url, {
            credentials: 'include', headers: {accept: 'application/json'},
            signal: AbortSignal.timeout(15000)
        });
        return {status: response.status, body: await response.json()};
    }""", {"bossId": friend["encryptBossId"], "securityId": friend["securityId"],
             "count": count})
    body = result.get("body") or {}
    if result.get("status") != 200 or body.get("code") != 0:
        raise RuntimeError(f"BOSS 历史消息接口失败：HTTP {result.get('status')}，code={body.get('code')}")
    raw = (body.get("zpData") or {}).get("messages")
    if not isinstance(raw, list):
        raise RuntimeError("BOSS 历史消息响应缺少 messages")
    return sorted(raw, key=lambda item: int(item.get("mid") or 0))[-count:]


def _tab_friends(page: Any, tab_label: str | None, active_days: int | None,
                 on_progress: Callable[[str], None] | None = None) -> list[dict[str, Any]]:
    """Read a whole BOSS list tab without opening a conversation."""
    friends: dict[str, dict[str, Any]] = {}

    def collect_response(response: Any) -> None:
        if "/wapi/zprelation/friend/getGeekFriendList.json" not in response.url:
            return
        try:
            body = response.json()
            if body.get("code") != 0:
                return
            for row in (body.get("zpData") or {}).get("result") or []:
                uid = str(row.get("uid") or "")
                if uid and row.get("avatar"):
                    friends[uid] = row
        except Exception:
            return

    page.on("response", collect_response)
    try:
        page.goto(CHAT_URL, wait_until="domcontentloaded", timeout=20000)
    except BrowserTimeout:
        pass
    if not page.url.startswith(CHAT_URL):
        raise RuntimeError("BOSS 会话页未正常打开，请检查登录状态")
    page.locator(".friend-content").first.wait_for(state="attached", timeout=15000)
    page.wait_for_timeout(1200)
    visible_avatars: set[str] = set()
    receipt_by_avatar: dict[str, str] = {}

    def capture_visible_avatars() -> None:
        visible = page.evaluate("""() => [...document.querySelectorAll('.friend-content')]
            .map(item => {
                const avatar = item.querySelector('img.image-circle')?.src.split('?')[0];
                const receipt = item.querySelector('.last-msg .message-status');
                const status = receipt?.classList.contains('status-read') ? 'read_no_reply'
                    : receipt?.classList.contains('status-delivery') ? 'unread' : '';
                return {avatar, status};
            }).filter(item => item.avatar)""")
        for item in visible:
            avatar = item["avatar"]
            visible_avatars.add(avatar)
            receipt_by_avatar[avatar] = item["status"]

    if tab_label:
        tab_button = page.locator("li").filter(has_text=tab_label).first
        tab_button.click(timeout=10000)
        page.wait_for_timeout(1000)
        if "selected" not in (tab_button.get_attribute("class") or "").split():
            raise RuntimeError(f"未能切换到 BOSS「{tab_label}」标签")
        capture_visible_avatars()
    cutoff_ms = (int((datetime.now(timezone.utc) - timedelta(days=active_days)).timestamp() * 1000)
                 if active_days is not None else None)
    idle_rounds = 0
    while idle_rounds < 3:
        visible_count = len(visible_avatars) if tab_label else len(friends)
        if cutoff_ms is not None:
            visible_rows = [row for row in friends.values()
                            if not tab_label or str(row.get("avatar") or "").split("?", 1)[0]
                            in visible_avatars]
            if visible_rows and min(int(row.get("lastTS") or 0) for row in visible_rows) < cutoff_ms:
                break
        page.evaluate("""() => {
            const list = document.querySelector('.user-list-content');
            if (list) list.scrollTop = list.scrollHeight;
        }""")
        page.wait_for_timeout(700)
        if tab_label:
            capture_visible_avatars()
        new_count = len(visible_avatars) if tab_label else len(friends)
        if on_progress and new_count != visible_count:
            on_progress(f"已扫描「{tab_label or '全部'}」列表 {new_count} 条")
        idle_rounds = idle_rounds + 1 if new_count == visible_count else 0
    candidates = list(friends.values())
    if tab_label:
        avatar_counts: dict[str, int] = {}
        for row in candidates:
            avatar = str(row.get("avatar") or "").split("?", 1)[0]
            avatar_counts[avatar] = avatar_counts.get(avatar, 0) + 1
        candidates = [row for row in candidates
                      if (avatar := str(row.get("avatar") or "").split("?", 1)[0])
                      in visible_avatars and avatar_counts[avatar] == 1]
    selected = sorted(candidates, key=lambda row: int(row.get("lastTS") or 0), reverse=True)
    if cutoff_ms is not None:
        selected = [row for row in selected if int(row.get("lastTS") or 0) >= cutoff_ms]
    if tab_label == "仅沟通":
        selected = [{**row, "list_receipt_status": receipt_by_avatar.get(
            str(row.get("avatar") or "").split("?", 1)[0], "")}
                    for row in selected]
    return selected


def scan_new_greetings(cdp_url: str, *, on_progress: Callable[[str], None] | None = None
                       ) -> list[dict[str, Any]]:
    """Read company names from the New Greetings list; preserve every unread badge."""
    browser = ChromeBrowser(cdp_url)
    try:
        tab = browser.new_tab("https://www.zhipin.com/robots.txt", background=True)
        if tab is None:
            raise RuntimeError("无法连接 BOSS 页面")
        friends = _tab_friends(browser.page(tab), "新招呼", None, on_progress)
        if on_progress:
            on_progress(f"已读取「新招呼」列表 {len(friends)} 条会话")
        return [{
            "platform": "boss", "conversation_id": str(friend["uid"]),
            "company": str(friend.get("brandName") or "").strip(),
            "recruiter": str(friend.get("name") or "").strip(),
            "job_title": str(friend.get("sourceTitle") or "").strip(),
            "source_job_id": str(friend.get("encryptJobId") or ""),
            "avatar": str(friend.get("avatar") or "").split("?", 1)[0],
        } for friend in friends]
    finally:
        browser.close()


def scan_recent_conversations(cdp_url: str, *, message_limit: int,
                              active_days: int | None = None,
                              skip_conversation_ids: set[str] | None = None,
                              on_progress: Callable[[str], None] | None = None
                              ) -> list[dict[str, Any]]:
    """Read history only for unanswered outbound messages in the Communication tab."""
    browser = ChromeBrowser(cdp_url)
    try:
        tab = browser.new_tab("https://www.zhipin.com/robots.txt", background=True)
        if tab is None:
            raise RuntimeError("无法连接 BOSS 页面")
        page = browser.page(tab)
        friends = _tab_friends(page, "仅沟通", active_days, on_progress)
        selected = [friend for friend in friends
                    if friend.get("list_receipt_status") in {"unread", "read_no_reply"}
                    and str(friend.get("uid")) not in (skip_conversation_ids or set())]
        if on_progress:
            on_progress(f"「仅沟通」列表 {len(friends)} 条，其中未读或已读未回 {len(selected)} 条")
        results = []
        for index, friend in enumerate(selected, start=1):
            raw_messages = _fetch_history(page, friend, message_limit)
            uid = str(friend["uid"])
            messages = [_normalise_message(raw, uid) for raw in raw_messages]
            messages = [item for item in messages if item["message_id"]]
            messages.sort(key=lambda item: int(item["message_id"]))
            judgment = friend["list_receipt_status"]
            job_desc = next((raw.get("body", {}).get("jobDesc") for raw in raw_messages
                             if isinstance(raw.get("body"), dict)
                             and isinstance(raw["body"].get("jobDesc"), dict)), {})
            job_id = str(friend.get("encryptJobId") or "")
            numeric_job_id = str(friend.get("jobId") or "")
            results.append({
                "platform": "boss", "conversation_id": uid,
                "source_job_id": job_id,
                "job_title": str(job_desc.get("title") or friend.get("sourceTitle") or ""),
                "company": str(friend.get("brandName") or job_desc.get("company") or ""),
                "recruiter": str(friend.get("name") or ""),
                "chat_url": f"{CHAT_URL}?jobId={numeric_job_id}" if numeric_job_id else CHAT_URL,
                "judgment": judgment, "next_action": "none",
                "evidence": "「仅沟通」列表显示[送达]" if judgment == "unread"
                            else "「仅沟通」列表显示[已读]",
                "messages": messages,
            })
            if on_progress and (index == 1 or index % 10 == 0 or index == len(selected)):
                on_progress(f"已读取 {index}/{len(selected)} 条会话")
        return results
    finally:
        browser.close()
