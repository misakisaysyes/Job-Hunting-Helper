"""Actions on stored conversations."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3

from api.jobs import JobActionError
from browser.browser import ChromeBrowser, ConversationLocationError
from browser.boss_filter import delete_exchanged_conversation as delete_boss_exchange
from data.conversation_store import ConversationStore
from data.exchange_store import ExchangeStore
from data.filter_store import FilterStore


def _conversation_job_id(conversation: dict, tab_label: str,
                          expected_source_job_id: str | None, *, require_job_id: bool = True) -> str:
    source_job_id = str(conversation.get("source_job_id") or "")
    if require_job_id and not source_job_id:
        raise JobActionError(f"会话缺少岗位标识，无法准确定位，请重新扫描「{tab_label}」后重试", 409)
    if expected_source_job_id is not None:
        if not isinstance(expected_source_job_id, str):
            raise JobActionError("岗位标识须为文本", 400)
        if expected_source_job_id != source_job_id:
            raise JobActionError("该会话的关联岗位已更新，请刷新监测列表后重试", 409)
    return source_job_id


def _show_boss_chat(config: dict, conversation_id: str, tab_label: str,
                    *, open_chat: bool = False, source_job_id: str | None = None) -> dict:
    browser = None
    try:
        browser = ChromeBrowser(config["browser"]["cdp_url"])
        options = {"source_job_id": source_job_id} if source_job_id is not None else {}
        if open_chat:
            located = browser.open_user_conversation(
                "https://www.zhipin.com/web/geek/chat", conversation_id, tab_label, **options)
        else:
            located = browser.locate_user_conversation(conversation_id, tab_label, **options)
        if not located:
            raise JobActionError(f"未能在 BOSS「{tab_label}」中定位目标会话，请刷新监测列表后重试", 409)
    except JobActionError:
        raise
    except ConversationLocationError as exc:
        raise JobActionError(str(exc), 409) from exc
    except Exception as exc:
        raise JobActionError("无法连接采集用 Chrome，请检查浏览器调试连接", 502) from exc
    finally:
        if browser is not None:
            browser.close()
    if open_chat:
        return {"message": f"已在采集用 Chrome 打开 BOSS「{tab_label}」中的目标会话"}
    return {"message": f"已在 BOSS「{tab_label}」列表中定位会话，请点击列表中的会话查看"}


def open_filter_candidate(db_path: Path, config: dict, platform: str,
                          conversation_id: str, *, expected_source_job_id: str | None = None) -> dict:
    if platform != "boss":
        raise JobActionError("当前仅支持 BOSS 会话", 400)
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        candidate = FilterStore(conn).get_candidate(platform, conversation_id)
    if candidate is None:
        raise JobActionError("过滤候选不存在", 404)
    if candidate["status"] in {"deleted", "stale", "deleting"}:
        raise JobActionError("该新会话已删除、已失效或正在删除，请刷新列表后重新扫描「新招呼」", 409)
    source_job_id = _conversation_job_id(candidate, "新招呼", expected_source_job_id,
                                         require_job_id=False)
    return _show_boss_chat(config, conversation_id, "新招呼", source_job_id=source_job_id)


def open_exchanged_conversation(db_path: Path, config: dict, platform: str,
                                conversation_id: str, *,
                                expected_source_job_id: str | None = None) -> dict:
    if platform != "boss":
        raise JobActionError("当前仅支持 BOSS 会话", 400)
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        conversation = ExchangeStore(conn).get_conversation(platform, conversation_id)
    if conversation is None:
        raise JobActionError("有交换会话记录不存在", 404)
    source_job_id = _conversation_job_id(conversation, "有交换", expected_source_job_id)
    return _show_boss_chat(config, conversation_id, "有交换", open_chat=True,
                           source_job_id=source_job_id)


def delete_exchanged_conversation(db_path: Path, cdp_url: str,
                                  platform: str, conversation_id: str, *,
                                  silent_background: bool = False) -> dict:
    if platform != "boss":
        raise JobActionError("当前仅支持 BOSS 会话", 400)
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        conversation = ExchangeStore(conn).get_conversation(platform, conversation_id)
    if conversation is None:
        raise JobActionError("有交换会话记录不存在", 404)
    result = delete_boss_exchange(cdp_url, conversation,
                                  silent_background=silent_background)
    if not result.verified:
        raise JobActionError(result.message, 409 if result.uncertain else 502)
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        if not ExchangeStore(conn).mark_removed(platform, conversation_id):
            raise JobActionError("BOSS 会话已删除，但本地状态未能更新，请重新扫描", 500)
    return {"deleted": True, "message": result.message}


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
                      conversation_id: str, *, expected_source_job_id: str | None = None) -> dict:
    if platform != "boss":
        raise JobActionError("当前仅支持 BOSS 会话", 400)
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        conversation = ConversationStore(conn).get_conversation(platform, conversation_id)
    if conversation is None:
        raise JobActionError("会话不存在", 404)
    source_job_id = _conversation_job_id(conversation, "仅沟通", expected_source_job_id)
    return _show_boss_chat(config, conversation_id, "仅沟通", open_chat=True,
                           source_job_id=source_job_id)
