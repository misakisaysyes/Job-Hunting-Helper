"""Actions on stored conversations."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3

from api.jobs import JobActionError
from browser.browser import ChromeBrowser
from data.conversation_store import ConversationStore
from data.filter_store import FilterStore


def _show_boss_chat(config: dict, conversation_id: str, tab_label: str,
                    *, open_chat: bool = False) -> dict:
    browser = None
    try:
        browser = ChromeBrowser(config["browser"]["cdp_url"])
        if open_chat:
            located = browser.open_user_conversation(
                "https://www.zhipin.com/web/geek/chat", conversation_id, tab_label)
        else:
            located = browser.locate_user_conversation(conversation_id, tab_label)
        if not located:
            raise JobActionError(f"未能在 BOSS「{tab_label}」中定位目标会话，请刷新监测列表后重试", 409)
    except JobActionError:
        raise
    except Exception as exc:
        raise JobActionError("无法连接采集用 Chrome，请检查浏览器调试连接", 502) from exc
    finally:
        if browser is not None:
            browser.close()
    if open_chat:
        return {"message": f"已在采集用 Chrome 打开 BOSS「{tab_label}」中的目标会话"}
    return {"message": f"已在 BOSS「{tab_label}」列表中定位会话，请点击列表中的会话查看"}


def open_filter_candidate(db_path: Path, config: dict, platform: str,
                          conversation_id: str) -> dict:
    if platform != "boss":
        raise JobActionError("当前仅支持 BOSS 会话", 400)
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        candidate = FilterStore(conn).get_candidate(platform, conversation_id)
    if candidate is None:
        raise JobActionError("过滤候选不存在", 404)
    return _show_boss_chat(config, conversation_id, "新招呼")


def terminate_monitoring(db_path: Path, platform: str, conversation_id: str) -> dict:
    if platform != "boss":
        raise JobActionError("当前仅支持 BOSS 会话", 400)
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        if not ConversationStore(conn).terminate_monitoring(platform, conversation_id):
            raise JobActionError("会话不存在、已终止或正在发送追问", 409)
    return {"message": "已终止监测，后续扫描将跳过该会话"}


def delete_conversation_record(db_path: Path, platform: str, conversation_id: str) -> dict:
    if platform != "boss":
        raise JobActionError("当前仅支持 BOSS 会话", 400)
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        result = ConversationStore(conn).delete_record(platform, conversation_id)
    if result == "missing":
        raise JobActionError("本地会话记录不存在", 404)
    if result == "busy":
        raise JobActionError("追问正在发送，暂不能删除记录", 409)
    return {"deleted": True, "message": "已从本地数据库删除会话及其消息"}


def open_conversation(db_path: Path, config: dict, platform: str,
                      conversation_id: str) -> dict:
    if platform != "boss":
        raise JobActionError("当前仅支持 BOSS 会话", 400)
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        conversation = ConversationStore(conn).get_conversation(platform, conversation_id)
    if conversation is None:
        raise JobActionError("会话不存在", 404)
    return _show_boss_chat(config, conversation_id, "仅沟通", open_chat=True)
