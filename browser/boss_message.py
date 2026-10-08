"""Send one saved greeting through the logged-in BOSS Chrome session."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
import time
from urllib.parse import parse_qs, urlsplit, urlunsplit

from patchright.sync_api import TimeoutError as BrowserTimeout

from browser.browser import ChromeBrowser
from browser.boss_monitor import _fetch_history, _normalise_message


CHAT_BUTTON = 'a.btn-startchat[redirect-url*="/web/geek/chat"], a[ka="job_detail_chat"]'
CHAT_INPUT = '#chat-input'
SEND_BUTTON = '.btn-send:not(.disabled)'


@dataclass(frozen=True)
class SendResult:
    verified: bool
    uncertain: bool
    message: str
    chat_url: str = ""
    actual_greeting: str = ""


def _delivery_state(page: object, greeting: str) -> str:
    return str(page.evaluate("""(expected) => {
        const normalize = value => String(value || '').replace(/[\\u200b-\\u200f\\ufeff]/g, '')
            .replace(/\\s+/g, ' ').trim();
        const target = normalize(expected);
        const messages = Array.from(document.querySelectorAll(
            '.chat-record .message-item.item-myself, .chat-record .item-myself, '
            + '.chat-record .message-item.item-self, .chat-record [class*="item-my"]'
        ));
        const matches = messages.filter(node => {
            const content = node.querySelector('.text-content, .message-text, '
                + '[class*="message-content"], [class*="msg-content"]');
            const raw = normalize(content ? content.innerText || content.textContent : node.innerText);
            const text = raw.replace(/^(发送中|已读|未读|送达|发送成功|重试|重新发送)\\s*/g, '')
                .replace(/\\s*(发送中|已读|未读|送达|发送成功|重试|重新发送)$/g, '').trim();
            return text === target;
        });
        const messageList = document.querySelector('.chat-record');
        const vue = messageList && messageList.__vue__;
        const records = vue && Array.isArray(vue.list$) ? vue.list$ : [];
        const matchingRecords = records.filter(record => {
            if (!record || !record.isSelf) return false;
            const text = record.text || record.lastText || record.content
                || record.message || record.body || '';
            return normalize(text) === target;
        });
        if (!matches.length && !matchingRecords.length) return 'missing';
        const states = matches.map(node => {
            const status = node.querySelector('.message-status');
            const cls = status ? String(status.className || '') : '';
            if (cls.includes('status-error')) return 'failed';
            if (cls.includes('status-loading')) return 'pending';
            return 'delivered';
        });
        states.push(...matchingRecords.map(record => {
            const status = Number(record.status);
            return status === 4 ? 'failed' : status === 0 ? 'pending' : 'delivered';
        }));
        return states.includes('delivered') ? 'delivered'
            : states.includes('pending') ? 'pending' : 'failed';
    }""", greeting))


def _chat_matches_job(page: object, job: dict) -> bool:
    expected_id = str(job["source_job_id"])
    parsed = urlsplit(page.url)
    if parsed.netloc != "www.zhipin.com" or not parsed.path.startswith("/web/geek/chat"):
        return False
    chat_job_id = parse_qs(parsed.query).get("jobId", [""])[0]
    if chat_job_id:
        return chat_job_id == expected_id
    return bool(page.evaluate("""({company, title, jobId}) => {
        const normalize = value => String(value || '').replace(/\\s+/g, '').toLowerCase();
        const roots = [document.querySelector('.chat-conversation'),
            document.querySelector('.friend-content.selected')].filter(Boolean);
        const text = normalize(roots.map(node => node.innerText || node.textContent).join(' '));
        const html = roots.map(node => node.outerHTML || '').join(' ');
        return html.includes(jobId) || (!!company && text.includes(normalize(company))
            && !!title && text.includes(normalize(title)));
    }""", {"company": job.get("company", ""), "title": job.get("title", ""),
              "jobId": expected_id}))


def _wait_for_chat(page: object, job: dict, previous_pages: set[object]) -> object | None:
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        candidates = [page, *[candidate for candidate in page.context.pages
                             if candidate not in previous_pages]]
        for candidate in candidates:
            try:
                if _chat_matches_job(candidate, job):
                    return candidate
            except Exception:
                continue
        time.sleep(0.4)
    return None


def _wait_for_delivery(page: object, greeting: str) -> SendResult:
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        state = _delivery_state(page, greeting)
        if state == "failed":
            return SendResult(False, False, "BOSS 标记招呼语发送失败")
        if state == "delivered":
            time.sleep(0.5)
            if _delivery_state(page, greeting) == "delivered":
                return SendResult(True, False, "BOSS 会话中已确认招呼语")
        time.sleep(0.4)
    return SendResult(False, True, "已提交招呼语，但未能在会话中确认；请人工核查，系统不会自动重发")


def _same_recruiter_history(page: object, recruiter_id: str, friends: list[dict],
                            greeting: str, chat_url: str, *, source_job_id: str = "",
                            preset_attempt_at: str = "") -> SendResult | None:
    """Check the selected recruiter when BOSS strips jobId and selects an older job."""
    selected_avatar = page.evaluate("""() => document.querySelector(
        '.friend-content.selected img.image-circle')?.src.split('?')[0] || ''""")
    matches = {str(friend.get("uid")): friend for friend in friends
               if friend.get("uid") and friend.get("encryptBossId") == recruiter_id
               and str(friend.get("avatar") or "").split("?", 1)[0] == selected_avatar}
    if len(matches) != 1 or not selected_avatar:
        return None
    friend = next(iter(matches.values()))
    raw_messages = _fetch_history(page, friend, 100)
    normalize = lambda value: re.sub(r"\s+", " ", str(value or "")
                                     .translate({ord(c): None for c in "\u200b\u200c\u200d\u200e\u200f\ufeff"})).strip()
    expected = normalize(greeting)
    messages = [_normalise_message(raw, str(friend["uid"])) for raw in raw_messages]
    found = [message for message in messages
             if message["sender"] == "self" and normalize(message["text"]) == expected]
    if found:
        if any(message["delivery_status"] not in {"0", "3", "4"} for message in found):
            return SendResult(True, False, "已在同一招聘者会话中确认招呼语", chat_url)
        return SendResult(False, True, "同一招聘者会话中有相同招呼语待发送或发送失败", chat_url)
    if source_job_id and friend.get("encryptJobId") == source_job_id and preset_attempt_at:
        try:
            attempted_ms = int(datetime.fromisoformat(preset_attempt_at).timestamp() * 1000)
        except ValueError:
            attempted_ms = 0
        recent = [message for message in messages
                  if message["sender"] == "self"
                  and message["delivery_status"] not in {"0", "3", "4"}
                  and normalize(message["text"])
                  and str(message["sent_at"]).isdigit()
                  and abs(int(message["sent_at"]) - attempted_ms) <= 120_000]
        if recent:
            actual = recent[-1]["text"].strip()
            return SendResult(True, False, "BOSS 已自动发送预设招呼语（非本次自定义文本）",
                              chat_url, actual)
    return SendResult(False, False, "同一招聘者会话的最近 100 条消息未找到这条招呼语", chat_url)


def inspect_boss_greeting(job: dict, greeting: str, cdp_url: str) -> SendResult:
    """Read the matching conversation without clicking either BOSS chat or send."""
    url = urlsplit(str(job.get("url") or ""))
    job_id = str(job.get("source_job_id") or "")
    if (url.scheme != "https" or url.netloc != "www.zhipin.com"
            or not url.path.startswith("/job_detail/") or not job_id):
        return SendResult(False, True, "岗位缺少有效的 BOSS 详情地址，无法核对会话")
    browser = None
    try:
        browser = ChromeBrowser(cdp_url)
        tab = browser.new_tab(urlunsplit(url), background=True)
        if not tab:
            return SendResult(False, True, "无法打开 BOSS 岗位详情页核对会话")
        page = browser.page(tab)
        buttons = page.locator(CHAT_BUTTON)
        button = next((buttons.nth(index) for index in range(buttons.count())
                       if buttons.nth(index).is_visible()), None)
        if button is None:
            return SendResult(False, True, "岗位详情页没有对应的会话入口")
        redirect = urlsplit(button.get_attribute("redirect-url") or "")
        same_origin = (not redirect.scheme and not redirect.netloc) or (
            redirect.scheme == "https" and redirect.netloc == "www.zhipin.com")
        if (not same_origin or redirect.path != "/web/geek/chat"
                or parse_qs(redirect.query).get("jobId", [""])[0] != job_id):
            return SendResult(False, True, "岗位与 BOSS 会话链接不匹配")
        chat_url = urlunsplit(("https", "www.zhipin.com", redirect.path, redirect.query, ""))
        friends: list[dict] = []

        def collect_friend_response(response: object) -> None:
            if "/wapi/zprelation/friend/getGeekFriendList.json" not in response.url:
                return
            try:
                body = response.json()
                if body.get("code") == 0:
                    friends.extend((body.get("zpData") or {}).get("result") or [])
            except Exception:
                pass

        page.on("response", collect_friend_response)
        page.goto(chat_url, wait_until="domcontentloaded", timeout=15000)
        page.locator(CHAT_INPUT).first.wait_for(state="visible", timeout=10000)
        page.locator(".chat-conversation").first.wait_for(state="visible", timeout=10000)
        recruiter_id = parse_qs(redirect.query).get("id", [""])[0]
        preset_attempt_at = (str(job.get("updated_at") or "")
                             if "预设招呼语弹窗" in str(job.get("greeting_send_note") or "") else "")
        fallback = (_same_recruiter_history(
            page, recruiter_id, friends, greeting, chat_url,
            source_job_id=job_id, preset_attempt_at=preset_attempt_at)
            if recruiter_id else None)
        if fallback is not None and fallback.verified:
            return fallback
        if not _chat_matches_job(page, job):
            if fallback is not None:
                return fallback
            return SendResult(False, True, "未能确认进入对应岗位的 BOSS 会话")
        if not job.get("company") or not job.get("title") or not page.evaluate("""({company, title}) => {
            const normalize = value => String(value || '').replace(/\\s+/g, '').toLowerCase();
            const text = normalize(document.querySelector('.chat-conversation')?.innerText);
            return text.includes(normalize(company)) && text.includes(normalize(title));
        }""", {"company": job["company"], "title": job["title"]}):
            return SendResult(False, True, "会话内容尚未确认对应此岗位")
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            state = _delivery_state(page, greeting)
            if state == "delivered":
                return SendResult(True, False, "已在 BOSS 会话中确认招呼语", chat_url)
            if state != "missing":
                return SendResult(False, True, "会话中有相同招呼语待发送或发送失败", chat_url)
            time.sleep(0.4)
        return SendResult(False, False, "对应会话未找到这条招呼语", chat_url)
    except Exception as exc:
        return SendResult(False, True, f"核对 BOSS 会话失败：{type(exc).__name__}")
    finally:
        if browser is not None:
            browser.close()


def send_boss_greeting(job: dict, greeting: str, cdp_url: str) -> SendResult:
    """Send once and mark success only after verifying the matching BOSS conversation."""
    url = urlsplit(str(job.get("url") or ""))
    job_id = str(job.get("source_job_id") or "")
    if (url.scheme != "https" or url.netloc != "www.zhipin.com"
            or not url.path.startswith("/job_detail/") or not job_id):
        return SendResult(False, False, "岗位缺少有效的 BOSS 详情地址")
    browser = None
    send_attempted = False
    phase = "打开岗位与会话"
    previous_pages = None
    page = None
    try:
        browser = ChromeBrowser(cdp_url)
        tab = browser.new_tab(urlunsplit(url), background=True)
        if not tab:
            return SendResult(False, False, "无法打开 BOSS 岗位详情页")
        page = browser.page(tab)
        buttons = page.locator(CHAT_BUTTON)
        button = next((buttons.nth(index) for index in range(buttons.count())
                       if buttons.nth(index).is_visible()), None)
        if button is None:
            return SendResult(False, False, "岗位详情页没有可用的立即沟通按钮")
        redirect = button.get_attribute("redirect-url") or ""
        target = urlsplit(redirect)
        same_origin = (not target.scheme and not target.netloc) or (
            target.scheme == "https" and target.netloc == "www.zhipin.com")
        if (not same_origin or target.path != "/web/geek/chat"
                or parse_qs(target.query).get("jobId", [""])[0] != job_id):
            return SendResult(False, False, "沟通按钮指向的岗位与当前记录不一致")
        chat_url = urlunsplit(("https", "www.zhipin.com", target.path, target.query, ""))
        previous_pages = set(page.context.pages)
        button.click(timeout=5000)
        time.sleep(0.7)
        preset = page.evaluate("""() => [...document.querySelectorAll(
            '.greet-boss-pop, .greet-pop, .dialog-wrap')].some(node => {
            const rect = node.getBoundingClientRect();
            if (!rect.width || !rect.height) return false;
            if (node.matches('.greet-boss-pop, .greet-pop')) return true;
            const text = node.innerText || node.textContent || '';
            return !node.querySelector('textarea.input-area')
                && /预设招呼语|默认招呼语|自动招呼语|打招呼语/.test(text);
        })""")
        if preset:
            check_job = {**job, "greeting_send_note": "BOSS 显示预设招呼语弹窗",
                         "updated_at": datetime.now(timezone.utc).isoformat()}
            inspection = inspect_boss_greeting(check_job, greeting, cdp_url)
            if inspection.verified:
                return inspection
            return SendResult(False, True, "BOSS 显示预设招呼语弹窗；未发送自定义文本，请人工核查")
        if not _chat_matches_job(page, job):
            page.goto(chat_url, wait_until="domcontentloaded", timeout=15000)
        chat_page = _wait_for_chat(page, job, previous_pages)
        if chat_page is None:
            return SendResult(False, False, "未能确认进入对应岗位的 BOSS 会话；自定义招呼语尚未提交")
        input_box = chat_page.locator(CHAT_INPUT)
        try:
            input_box.first.wait_for(state="visible", timeout=8000)
        except BrowserTimeout:
            return SendResult(False, False, "对应会话的输入框在 8 秒内未出现；自定义招呼语尚未提交")
        existing = _delivery_state(chat_page, greeting)
        if existing == "delivered":
            return SendResult(True, False, "会话中已有相同招呼语，未重复发送", chat_url)
        if existing != "missing":
            return SendResult(False, True, "会话中有相同招呼语待发送或发送失败，请人工核查")
        input_box.first.fill(greeting)
        if input_box.first.inner_text().strip() != greeting.strip():
            return SendResult(False, False, "招呼语未正确填入输入框；自定义招呼语尚未提交")
        send_button = chat_page.locator(SEND_BUTTON)
        try:
            send_button.first.wait_for(state="visible", timeout=8000)
        except BrowserTimeout:
            return SendResult(False, False, "BOSS 发送按钮在 8 秒内未变为可用；自定义招呼语尚未提交")
        if not send_button.first.is_enabled():
            return SendResult(False, False, "BOSS 发送按钮不可用；自定义招呼语尚未提交")
        send_attempted = True
        phase = "点击发送按钮"
        send_button.first.click(timeout=5000)
        phase = "核对会话消息"
        delivery = _wait_for_delivery(chat_page, greeting)
        if delivery.verified:
            return SendResult(True, False, delivery.message, chat_url)
        return delivery
    except Exception as exc:
        return SendResult(False, send_attempted,
                          f"BOSS 发送过程出错（{phase}）：{type(exc).__name__}")
    finally:
        if browser is not None:
            try:
                if page is not None and previous_pages is not None:
                    for opened in page.context.pages:
                        if opened not in previous_pages:
                            try:
                                opened.close()
                            except Exception:
                                pass
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
