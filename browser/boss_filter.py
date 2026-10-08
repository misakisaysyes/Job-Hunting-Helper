"""Open one verified BOSS New Greetings chat and delete it from its row menu."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from browser.boss_monitor import _tab_friends
from browser.browser import ChromeBrowser


@dataclass(frozen=True)
class FilterDeleteResult:
    verified: bool
    uncertain: bool
    message: str


def _confirm_delete_dialog(page: Any) -> str | None:
    """Click only the expected BOSS in-page confirmation dialog."""
    confirmation = page.locator('.dialog-wrap.active[data-type="boss-dialog"]').filter(
        has_text="确认删除吗")
    confirmation.first.wait_for(state="visible", timeout=5000)
    if confirmation.count() != 1:
        return "删除确认框不唯一，请人工核查"
    title = confirmation.locator(".boss-dialog_title h3").inner_text().strip()
    body = confirmation.locator(".boss-dialog__body").inner_text().strip()
    confirm_button = confirmation.locator(".boss-dialog__footer .boss-dialog__button").filter(
        has_text="确定")
    if (title not in {"确认删除吗?", "确认删除吗？"}
            or body != "将对方从你的列表中删除，同时删除聊天记录"
            or confirm_button.count() != 1
            or confirm_button.inner_text().strip() != "确定"):
        return "删除确认框内容与预期不符，请人工核查"
    confirm_button.click(timeout=5000)
    return None


def delete_new_greeting(cdp_url: str, candidate: dict[str, Any]) -> FilterDeleteResult:
    browser = ChromeBrowser(cdp_url)
    attempted = False
    try:
        tab = browser.new_tab("https://www.zhipin.com/robots.txt", background=True)
        if tab is None:
            return FilterDeleteResult(False, False, "无法打开 BOSS 消息页")
        page = browser.page(tab)
        friends = _tab_friends(page, "新招呼", None)
        friend = next((row for row in friends
                       if str(row.get("uid") or "") == candidate["conversation_id"]), None)
        if friend is None:
            return FilterDeleteResult(False, False, "会话已不在「新招呼」列表，未删除")
        avatar = str(friend.get("avatar") or "").split("?", 1)[0]
        if (str(friend.get("brandName") or "").strip() != candidate["company"]
                or str(friend.get("name") or "").strip() != candidate["recruiter"]
                or avatar != candidate["avatar"]):
            return FilterDeleteResult(False, False, "公司或招聘者信息已变化，未删除")

        page.evaluate("""() => {
            const list = document.querySelector('.user-list-content');
            if (list) list.scrollTop = 0;
        }""")
        matches: list[int] = []
        idle_rounds = 0
        while idle_rounds < 3:
            matches = page.evaluate("""({avatar, name, brand}) =>
                [...document.querySelectorAll('.friend-content')].flatMap((node, index) => {
                    const text = node.innerText || '';
                    return node.querySelector('img.image-circle')?.src.split('?')[0] === avatar
                        && text.includes(name) && text.includes(brand) ? [index] : [];
                })""", {"avatar": avatar, "name": candidate["recruiter"],
                         "brand": candidate["company"]})
            if matches:
                break
            moved = page.evaluate("""() => {
                const list = document.querySelector('.user-list-content');
                if (!list) return false;
                const before = list.scrollTop;
                list.scrollTop += Math.max(list.clientHeight * 0.8, 300);
                return list.scrollTop > before;
            }""")
            page.wait_for_timeout(350)
            idle_rounds = 0 if moved else idle_rounds + 1
        if len(matches) != 1:
            return FilterDeleteResult(False, False, "无法唯一定位目标会话，未删除")
        if page.locator(".friend-content.selected").count():
            return FilterDeleteResult(False, False, "页面已选中会话，未删除")

        row = page.locator(".friend-content").nth(matches[0])
        row.click(timeout=5000)
        page.wait_for_function("""avatar => {
            const selected = [...document.querySelectorAll('.friend-content.selected')];
            return selected.length === 1
                && selected[0].querySelector('img.image-circle')?.src.split('?')[0] === avatar;
        }""", arg=avatar, timeout=5000)
        row.locator(".user-operation").first.hover(timeout=5000)
        options = page.locator(".operation-content .operation-item")
        delete_option = options.filter(has_text="删除")
        delete_option.first.wait_for(state="visible", timeout=5000)
        if (delete_option.count() != 1 or not delete_option.is_visible()
                or delete_option.inner_text().strip() != "删除"):
            return FilterDeleteResult(False, False, "未找到唯一的删除操作，未删除")
        selected_avatar = page.locator(".friend-content.selected img.image-circle").first.get_attribute("src")
        if (page.locator(".friend-content.selected").count() != 1
                or str(selected_avatar or "").split("?", 1)[0] != avatar):
            return FilterDeleteResult(False, False, "当前选中的会话已变化，未删除")
        page.on("dialog", lambda dialog: dialog.accept())
        attempted = True
        delete_option.click(timeout=5000)
        confirmation_error = _confirm_delete_dialog(page)
        if confirmation_error:
            return FilterDeleteResult(False, True, confirmation_error)
        page.wait_for_timeout(700)
        remaining = _tab_friends(page, "新招呼", None)
        if all(str(item.get("uid") or "") != candidate["conversation_id"] for item in remaining):
            return FilterDeleteResult(True, False, "已从 BOSS「新招呼」列表删除并复核")
        return FilterDeleteResult(False, True, "删除后会话仍在列表中，请人工核查")
    except Exception as exc:
        return FilterDeleteResult(False, attempted, f"删除会话时出现 {type(exc).__name__}，请人工核查")
    finally:
        browser.close()
