"""Direct CDP adapter for an already running, logged-in Chrome profile."""

from __future__ import annotations

import time
from uuid import uuid4
from typing import Any

from patchright.sync_api import TimeoutError as BrowserTimeout
from patchright.sync_api import sync_playwright


_BOSS_CHAT_ROW_MATCHES = """({avatar, name, brand, selectedOnly = false}) => {
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
        return key && src && avatarKey(src) === key
            && (!name || text.includes(name)) && (!brand || text.includes(brand))
            ? [index] : [];
    });
    if (!selectedOnly) return matches;
    return matches.length === 1 && rows[matches[0]].classList.contains('selected')
        && rows.filter(node => node.classList.contains('selected')).length === 1;
}"""


class ChromeBrowser:
    def __init__(self, cdp_url: str = "http://127.0.0.1:9222") -> None:
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

    def new_tab(self, url: str, background: bool = False) -> str | None:
        # Chrome owns the profile. Only pages created by this adapter are closed.
        page = self._context.new_page()
        target_id = str(uuid4())
        self._tabs[target_id] = page
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
                               tab_label: str = "仅沟通") -> bool:
        """Open a BOSS chat tab and select the exact recruiter conversation."""
        return self._show_user_conversation(url, conversation_id, tab_label, select=True)

    def locate_user_conversation(self, conversation_id: str,
                                 tab_label: str = "仅沟通") -> bool:
        """Show and highlight a BOSS list row without entering the conversation."""
        return self._show_user_conversation("https://www.zhipin.com/web/geek/chat",
                                            conversation_id, tab_label, select=False)

    def _show_user_conversation(self, url: str, conversation_id: str,
                                tab_label: str, *, select: bool) -> bool:
        page = None
        located = False
        try:
            page = self._context.new_page()
            friends: dict[str, dict[str, Any]] = {}

            def collect_response(response: Any) -> None:
                if "/wapi/zprelation/friend/getGeekFriendList.json" not in response.url:
                    return
                try:
                    body = response.json()
                    if body.get("code") == 0:
                        for row in (body.get("zpData") or {}).get("result") or []:
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
                if friend and friend.get("avatar"):
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
            return False
        except Exception:
            return False
        finally:
            if page is not None and not located:
                try:
                    page.close()
                except Exception:
                    pass

    def request_boss_json(self, path: str, method: str, fields: dict[str, str]) -> dict[str, Any]:
        """Fetch BOSS JSON inside Chrome so its proxy can observe the request."""
        if not path.startswith("/wapi/zpgeek/") or method not in {"GET", "POST"}:
            raise ValueError("不支持的 BOSS 列表请求")
        try:
            if self._list_worker is None or self._tabs[self._list_worker].is_closed():
                # A same-origin document is needed for browser fetch. This static
                # page does not run the recommendation page's automatic request.
                self._list_worker = self.new_tab("https://www.zhipin.com/robots.txt", background=True)
            if self._list_worker is None:
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
        if target_id == self._list_worker:
            self._list_worker = None
        if page is None:
            return False
        try:
            page.close()
        except Exception:
            return False
        return True

    def close(self) -> None:
        for target_id in list(self._tabs):
            self.close_tab(target_id)
        self._playwright.stop()

    def __enter__(self) -> "ChromeBrowser":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
