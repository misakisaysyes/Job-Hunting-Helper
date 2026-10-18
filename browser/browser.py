"""Direct CDP adapter for an already running, logged-in Chrome profile."""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from uuid import uuid4
from typing import Any

from patchright.sync_api import TimeoutError as BrowserTimeout
from patchright.sync_api import sync_playwright


_BOSS_CHAT_ROW_MATCHES = """({avatar, name, brand, selectedOnly = false, exactIdentity = false}) => {
    const avatarKey = value => {
        try {
            const url = new URL(value, location.href);
            return url.origin + url.pathname;
        } catch (_) { return ''; }
    };
    const key = avatarKey(avatar);
    const rows = [...document.querySelectorAll('.friend-content')];
    const matches = rows.flatMap((node, index) => {
        const text = node.innerText || '';
        const src = node.querySelector('img.image-circle')?.src;
        const identityMatches = exactIdentity
            ? node.querySelector('.name-text')?.textContent?.trim() === name
                && node.querySelector('.name-box > span:nth-child(2)')?.textContent?.trim() === brand
            : (!name || text.includes(name)) && (!brand || text.includes(brand));
        return key && src && avatarKey(src) === key
            && identityMatches
            ? [index] : [];
    });
    if (!selectedOnly) return matches;
    return matches.length === 1 && rows[matches[0]].classList.contains('selected')
        && rows.filter(node => node.classList.contains('selected')).length === 1;
}"""


_BOSS_CHAT_HIGHLIGHT_ROW = """identity => {
    const matches = (""" + _BOSS_CHAT_ROW_MATCHES + """)(identity);
    if (matches.length > 1) return 'ambiguous';
    if (matches.length !== 1) return 'missing';
    const element = document.querySelectorAll('.friend-content')[matches[0]];
    element.scrollIntoView({block: 'center', inline: 'nearest'});
    element.style.outline = '3px solid #1976e8';
    element.style.outlineOffset = '-3px';
    element.style.backgroundColor = '#edf5ff';
    return 'located';
}"""


_BOSS_CHAT_CONTENT_MATCHES = """({messageIds, jobId}) => {
    const url = new URL(location.href);
    if (url.origin !== 'https://www.zhipin.com' || url.pathname !== '/web/geek/chat')
        return false;
    const routeJob = url.searchParams.get('jobId');
    if (routeJob && routeJob !== jobId) return false;
    const root = document.querySelector('.chat-record');
    if (!root) return false;
    const vue = root.__vue__;
    const records = vue && Array.isArray(vue.list$) ? vue.list$ : [];
    const renderedIds = new Set(records.flatMap(record => record
        ? [record.mid, record.messageId, record.message_id] : [])
        .filter(value => value != null).map(String));
    root.querySelectorAll('[data-mid], [data-message-id]').forEach(node => {
        for (const key of ['data-mid', 'data-message-id']) {
            const value = node.getAttribute(key);
            if (value) renderedIds.add(value);
        }
    });
    if (!messageIds.some(id => renderedIds.has(id))) return false;
    const links = [...(document.querySelector('.chat-conversation')
        ?.querySelectorAll('a[href*="/job_detail/"]') || [])];
    const displayedJobs = links.flatMap(node => {
        try {
            const link = new URL(node.href, location.href);
            const match = link.pathname.match(/^\\/job_detail\\/([^/]+)\\.html$/);
            return link.origin === url.origin && match ? [decodeURIComponent(match[1])] : [];
        } catch (_) { return []; }
    });
    return !displayedJobs.length || displayedJobs.includes(jobId);
}"""


class ConversationLocationError(RuntimeError):
    """The requested recruiter/job could not be verified in the opened chat."""


class ChromeBrowser:
    def __init__(self, cdp_url: str = "http://127.0.0.1:9222", *,
                 silent_background: bool = False) -> None:
        self._cdp_url = cdp_url
        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.connect_over_cdp(cdp_url)
            if not self._browser.contexts:
                raise RuntimeError("Chrome 没有可用的浏览器上下文")
            self._context = self._browser.contexts[0]
        except Exception:
            self._playwright.stop()
            raise
        self._tabs: dict[str, Any] = {}
        self._list_worker: str | None = None
        self._background_worker: str | None = None
        self._background_browser: Any | None = None
        self.silent_background = silent_background

    def _new_background_page(self) -> Any:
        """Create an unfocused Chrome tab and wait until Patchright sees that target."""
        marker = f"about:blank#job-hunting-helper-{uuid4().hex}"
        session = self._browser.new_browser_cdp_session()
        chrome_target_id = None
        worker_browser = None
        try:
            chrome_target_id = session.send("Target.createTarget", {
                "url": marker, "background": True, "focus": False,
            })["targetId"]
            # Patchright does not discover targets created through an existing
            # CDP connection. A second connection attaches to the new tab
            # without activating it in Chrome.
            worker_browser = self._playwright.chromium.connect_over_cdp(self._cdp_url)
            if not worker_browser.contexts:
                raise RuntimeError("Chrome 没有可用的后台浏览器上下文")
            worker_context = worker_browser.contexts[0]
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                page = next((item for item in worker_context.pages
                             if not item.is_closed() and item.url == marker), None)
                if page is not None:
                    self._background_browser = worker_browser
                    return page
                time.sleep(0.05)
            raise RuntimeError("Chrome 未暴露后台工作标签页")
        except Exception:
            if worker_browser is not None:
                try:
                    worker_browser.close()
                except Exception:
                    pass
            if chrome_target_id is not None:
                try:
                    session.send("Target.closeTarget", {"targetId": chrome_target_id})
                except Exception:
                    pass
            raise
        finally:
            try:
                session.detach()
            except Exception:
                pass

    def new_tab(self, url: str, background: bool = False) -> str | None:
        # Chrome owns the profile. Only pages created by this adapter are closed.
        if background and self.silent_background and self._background_worker:
            if self.navigate(self._background_worker, url):
                return self._background_worker
            self.close_tab(self._background_worker)
        try:
            page = (self._new_background_page() if background and self.silent_background
                    else self._context.new_page())
        except Exception:
            # Never silently fall back to a foreground page in silent mode.
            return None
        target_id = str(uuid4())
        self._tabs[target_id] = page
        if background and self.silent_background:
            self._background_worker = target_id
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
        except BrowserTimeout:
            pass  # The collector checks readiness and risk markers afterward.
        except Exception:
            self.close_tab(target_id)
            return None
        return target_id

    def navigate(self, target_id: str, url: str) -> bool:
        page = self._tabs.get(target_id)
        if page is None or page.is_closed():
            return False
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
        except BrowserTimeout:
            return not page.is_closed()
        except Exception:
            return False
        return True

    def evaluate(self, target_id: str, expression: str, timeout: float = 30) -> Any:
        return self._tabs[target_id].evaluate(expression)

    def page(self, target_id: str) -> Any:
        """Return an owned Playwright page for platform-specific UI actions."""
        return self._tabs[target_id]

    def open_user_tab(self, url: str) -> bool:
        """Open a tab in the connected Chrome and leave it open after disconnecting."""
        session = self._browser.new_browser_cdp_session()
        target_id = None
        try:
            target_id = session.send("Target.createTarget", {"url": url})["targetId"]
            session.send("Target.activateTarget", {"targetId": target_id})
            return True
        except Exception:
            if target_id is not None:
                try:
                    session.send("Target.closeTarget", {"targetId": target_id})
                except Exception:
                    pass
            return False
        finally:
            session.detach()

    def open_user_conversation(self, url: str, conversation_id: str,
                               tab_label: str = "仅沟通", *,
                               source_job_id: str | None = None) -> bool:
        """Open a BOSS chat tab and select the exact recruiter conversation."""
        return self._show_user_conversation(url, conversation_id, tab_label,
                                            select=True, source_job_id=source_job_id)

    def locate_user_conversation(self, conversation_id: str,
                                 tab_label: str = "仅沟通", *,
                                 source_job_id: str | None = None) -> bool:
        """Show and highlight a BOSS list row without entering the conversation."""
        return self._show_user_conversation("https://www.zhipin.com/web/geek/chat",
                                            conversation_id, tab_label, select=False,
                                            source_job_id=source_job_id)

    def _show_user_conversation(self, url: str, conversation_id: str,
                                tab_label: str, *, select: bool,
                                source_job_id: str | None = None) -> bool:
        page = None
        located = False
        try:
            page = self._context.new_page()
            friends: dict[str, dict[str, Any]] = {}
            histories: dict[str, list[dict[str, Any]]] = {}

            def collect_response(response: Any) -> None:
                try:
                    parsed = urlsplit(response.url)
                    if parsed.scheme != "https" or parsed.netloc != "www.zhipin.com":
                        return
                    if parsed.path not in {"/wapi/zprelation/friend/getGeekFriendList.json",
                                           "/wapi/zpchat/geek/historyMsg"}:
                        return
                    body = response.json()
                    if body.get("code") != 0:
                        return
                    data = body.get("zpData") or {}
                    if parsed.path == "/wapi/zpchat/geek/historyMsg":
                        boss_id = parse_qs(parsed.query).get("bossId", [""])[0]
                        messages = data.get("messages")
                        if boss_id and isinstance(messages, list):
                            histories[boss_id] = messages
                        return
                    for row in data.get("result") or []:
                        uid = str(row.get("uid") or "")
                        if uid:
                            friends[uid] = row
                except Exception:
                    pass

            page.on("response", collect_response)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
            except BrowserTimeout:
                pass
            if not page.url.startswith("https://www.zhipin.com/web/geek/chat"):
                return False
            if tab_label == "有交换":
                page.locator("li").filter(has_text="更多").first.click(timeout=10000)
                friends.clear()
                page.get_by_text("有交换", exact=True).click(timeout=10000)
                page.wait_for_timeout(1000)
            else:
                tab = page.locator("li").filter(has_text=tab_label).first
                tab.click(timeout=10000)
                page.wait_for_function("""label => [...document.querySelectorAll('li')]
                    .some(node => node.textContent.includes(label)
                        && node.classList.contains('selected'))""", arg=tab_label, timeout=10000)
            page.locator(".friend-content").first.wait_for(state="attached", timeout=15000)
            page.evaluate("""() => {
                const list = document.querySelector('.user-list-content');
                if (list) list.scrollTop = 0;
            }""")

            previous_state = None
            idle_rounds = 0
            for _ in range(120):
                friend = friends.get(conversation_id)
                if select and source_job_id and friend is not None:
                    self._open_exact_boss_chat(page, friend, source_job_id, friends, histories,
                                               tab_label=tab_label)
                    page.bring_to_front()
                    located = True
                    return True
                if not select and source_job_id is not None and friend is not None:
                    if self._locate_exact_boss_row(page, friend, source_job_id, friends, tab_label):
                        page.bring_to_front()
                        located = True
                        return True
                elif friend and friend.get("avatar"):
                    identity = {"avatar": str(friend["avatar"]),
                                "name": str(friend.get("name") or ""),
                                "brand": str(friend.get("brandName") or "")}
                    matches = page.evaluate(_BOSS_CHAT_ROW_MATCHES, identity)
                    if len(matches) == 1:
                        item = page.locator(".friend-content").nth(matches[0])
                        item.scroll_into_view_if_needed(timeout=5000)
                        if select:
                            item.click(timeout=5000)
                            page.wait_for_function(_BOSS_CHAT_ROW_MATCHES,
                                arg={**identity, "selectedOnly": True}, timeout=8000)
                        else:
                            item.evaluate("""element => {
                                element.style.outline = '3px solid #1976e8';
                                element.style.outlineOffset = '-3px';
                                element.style.backgroundColor = '#edf5ff';
                            }""")
                        page.bring_to_front()
                        located = True
                        return True
                scroll_state = page.evaluate("""() => {
                    const list = document.querySelector('.user-list-content');
                    if (!list) return null;
                    list.scrollTop += Math.max(list.clientHeight * .8, 300);
                    return {top: list.scrollTop, height: list.scrollHeight,
                        rows: document.querySelectorAll('.friend-content').length};
                }""")
                state = (scroll_state, len(friends))
                idle_rounds = idle_rounds + 1 if state == previous_state else 0
                if idle_rounds >= 12:
                    break
                previous_state = state
                page.wait_for_timeout(250)
            if source_job_id is not None:
                raise ConversationLocationError(f"未找到对应招聘方的会话，请重新扫描「{tab_label}」后重试")
            return False
        except ConversationLocationError:
            raise
        except Exception:
            return False
        finally:
            if page is not None and not located:
                try:
                    page.close()
                except Exception:
                    pass

    @staticmethod
    def _locate_exact_boss_row(page: Any, friend: dict[str, Any], source_job_id: str,
                               friends: dict[str, dict[str, Any]], tab_label: str) -> bool:
        """Keep incoming greetings unread; highlight only an unambiguous live row."""
        if str(friend.get("encryptJobId") or "") != source_job_id:
            raise ConversationLocationError(
                f"该招聘方的关联岗位已变化，请重新扫描「{tab_label}」后重试")

        def identity(row: dict[str, Any]) -> tuple[str, str, str]:
            avatar = str(row.get("avatar") or "")
            avatar_url = (urlsplit(urljoin("https://www.zhipin.com/web/geek/chat", avatar))
                          ._replace(query="", fragment="").geturl()) if avatar else ""
            return (avatar_url, str(row.get("name") or "").strip(),
                    str(row.get("brandName") or "").strip())

        key = identity(friend)
        if not all(key):
            raise ConversationLocationError(f"招聘方身份信息不完整，请重新扫描「{tab_label}」后重试")
        if sum(identity(row) == key for row in friends.values()) != 1:
            raise ConversationLocationError("存在身份信息相同的多个招聘方，无法唯一定位，请在 BOSS 列表中人工核对")
        result = page.evaluate(_BOSS_CHAT_HIGHLIGHT_ROW,
                               {"avatar": key[0], "name": key[1], "brand": key[2], "exactIdentity": True})
        if result == "ambiguous":
            raise ConversationLocationError("列表中存在多个相同会话，无法唯一定位，请在 BOSS 列表中人工核对")
        return result == "located"

    @staticmethod
    def _open_exact_boss_chat(page: Any, friend: dict[str, Any], source_job_id: str,
                              friends: dict[str, dict[str, Any]],
                              histories: dict[str, list[dict[str, Any]]], *,
                              tab_label: str = "仅沟通") -> None:
        """Use BOSS's recruiter/job route and verify the rendered message history."""
        uid = str(friend["uid"])
        if str(friend.get("encryptJobId") or "") != source_job_id:
            raise ConversationLocationError(
                f"该招聘方当前会话的岗位已变化或无法确认，与所选岗位不一致，请重新扫描「{tab_label}」后重试")
        boss_id = str(friend.get("encryptBossId") or "")
        if not boss_id:
            raise ConversationLocationError(f"BOSS 未返回招聘方标识，无法准确打开会话，请重新扫描「{tab_label}」后重试")
        fields = {"jobId": source_job_id, "id": boss_id}
        if friend.get("securityId"):
            fields["securityId"] = str(friend["securityId"])
        # Old scan URLs contain only a numeric jobId and cannot select a recruiter.
        # Build the native chat route from the live record for this exact uid.
        histories.clear()
        try:
            page.goto("https://www.zhipin.com/web/geek/chat?" + urlencode(fields),
                      wait_until="domcontentloaded", timeout=20000)
        except BrowserTimeout:
            pass
        for _ in range(32):
            current = friends.get(uid, {})
            if (str(current.get("encryptJobId") or "") != source_job_id
                    or str(current.get("encryptBossId") or "") != boss_id):
                raise ConversationLocationError(
                    f"打开会话时招聘方关联岗位发生变化，请重新扫描「{tab_label}」后重试")
            messages = histories.get(boss_id, [])
            message_ids = [str(message["mid"]) for message in messages
                           if isinstance(message, dict) and message.get("mid") is not None]
            if message_ids and page.evaluate(_BOSS_CHAT_CONTENT_MATCHES,
                                             {"messageIds": message_ids, "jobId": source_job_id}):
                return
            page.wait_for_timeout(250)
        raise ConversationLocationError(
            f"未能确认聊天区已加载对应招聘方和岗位的消息，请重新扫描「{tab_label}」后重试")

    def request_boss_json(self, path: str, method: str, fields: dict[str, str]) -> dict[str, Any]:
        """Fetch BOSS JSON inside Chrome so its proxy can observe the request."""
        if not path.startswith("/wapi/zpgeek/") or method not in {"GET", "POST"}:
            raise ValueError("不支持的 BOSS 列表请求")
        try:
            worker = self._tabs.get(self._list_worker) if self._list_worker else None
            if worker is None or worker.is_closed():
                # A same-origin document is needed for browser fetch. This static
                # page does not run the recommendation page's automatic request.
                self._list_worker = self.new_tab("https://www.zhipin.com/robots.txt", background=True)
            if self._list_worker is None:
                return {"error": "worker_page_unavailable"}
            if (getattr(self, "silent_background", False)
                    and self._tabs[self._list_worker].url != "https://www.zhipin.com/robots.txt"
                    and not self.navigate(self._list_worker, "https://www.zhipin.com/robots.txt")):
                return {"error": "worker_page_unavailable"}
            return self._tabs[self._list_worker].evaluate("""async ({path, method, fields, timestamp}) => {
                const url = new URL(path, location.origin);
                const options = {
                    method,
                    credentials: 'include',
                    headers: {
                        accept: 'application/json, text/plain, */*',
                        'x-requested-with': 'XMLHttpRequest',
                    },
                    signal: AbortSignal.timeout(15000),
                };
                if (method === 'GET') {
                    const params = new URLSearchParams(fields);
                    params.set('_', timestamp);
                    url.search = params.toString();
                } else {
                    url.searchParams.set('_', timestamp);
                    options.body = new URLSearchParams(fields);
                }
                const response = await fetch(url, options);
                let body = null;
                try { body = await response.json(); } catch (_) {}
                return {http_status: response.status, body};
            }""", {"path": path, "method": method, "fields": fields,
                   "timestamp": str(int(time.time() * 1000))})
        except Exception as exc:
            return {"error": "network_error", "message": str(exc)[:160]}

    def scroll(self, target_id: str, y: int = 0, direction: str = "") -> bool:
        page = self._tabs.get(target_id)
        if page is None or page.is_closed():
            return False
        if direction == "top":
            page.evaluate("window.scrollTo(0, 0)")
        elif direction == "bottom":
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        else:
            page.evaluate("(offset) => window.scrollBy(0, offset)", y)
        return True

    def wait_for_load(self, target_id: str, timeout: float = 10.0) -> bool:
        page = self._tabs.get(target_id)
        if page is None or page.is_closed():
            return False
        try:
            page.wait_for_load_state("domcontentloaded", timeout=int(timeout * 1000))
            return True
        except BrowserTimeout:
            return False

    def close_tab(self, target_id: str) -> bool:
        page = self._tabs.pop(target_id, None)
        is_background_worker = target_id == self._background_worker
        if target_id == self._list_worker:
            self._list_worker = None
        if is_background_worker:
            self._background_worker = None
        if page is None:
            return False
        try:
            page.close()
        except Exception:
            return False
        finally:
            if is_background_worker and self._background_browser is not None:
                try:
                    self._background_browser.close()
                except Exception:
                    pass
                self._background_browser = None
        return True

    def close(self) -> None:
        for target_id in list(self._tabs):
            self.close_tab(target_id)
        if self._background_browser is not None:
            try:
                self._background_browser.close()
            except Exception:
                pass
            self._background_browser = None
        self._playwright.stop()

    def __enter__(self) -> "ChromeBrowser":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
