"""Review and execute company-blocklist filtering for BOSS New Greetings."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3

from api.jobs import JobActionError
from browser.boss_filter import delete_new_greeting
from data.filter_store import FilterStore


def matched_company_term(company: str, terms: list[str]) -> str:
    normalized = company.casefold()
    return next((term.strip() for term in terms
                 if term.strip() and term.strip().casefold() in normalized), "")


def delete_candidate(db_path: Path, cdp_url: str, platform: str,
                     conversation_id: str, blocked_terms: list[str], *,
                     confirm_still_present: bool = False) -> dict:
    if platform != "boss":
        raise JobActionError("当前仅支持 BOSS 新招呼过滤", 400)
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        store = FilterStore(conn)
        candidate = store.get_candidate(platform, conversation_id)
        allowed = {"pending_review", "failed", "unknown"} if confirm_still_present else {"pending_review", "failed"}
        if candidate is None or candidate["status"] not in allowed:
            raise JobActionError("当前没有可删除的过滤候选", 409)
        if not matched_company_term(candidate["company"], blocked_terms):
            raise JobActionError("公司已不再命中当前排除词，请重新扫描", 409)
        claimed = store.claim_delete(platform, conversation_id,
                                     allow_unknown=confirm_still_present)
    if claimed is None:
        raise JobActionError("删除候选已被处理，请刷新列表", 409)
    try:
        result = delete_new_greeting(cdp_url, claimed)
    except Exception as exc:
        with closing(sqlite3.connect(db_path, timeout=10)) as conn:
            FilterStore(conn).finish_delete(platform, conversation_id, outcome="unknown",
                                            error=f"删除异常：{type(exc).__name__}")
        raise JobActionError("删除结果不明，请人工核查", 409) from exc
    outcome = "deleted" if result.verified else "unknown" if result.uncertain else "failed"
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        store = FilterStore(conn)
        if not store.finish_delete(platform, conversation_id, outcome=outcome,
                                   error="" if result.verified else result.message):
            raise JobActionError("删除结果未能写入，请人工核查", 500)
        candidate = store.get_candidate(platform, conversation_id)
    if not result.verified:
        raise JobActionError(result.message, 409 if result.uncertain else 502)
    return {"candidate": candidate, "message": result.message}


def delete_filter_record(db_path: Path, platform: str, conversation_id: str) -> dict:
    if platform != "boss":
        raise JobActionError("当前仅支持 BOSS 新招呼过滤", 400)
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        result = FilterStore(conn).delete_record(platform, conversation_id)
    if result == "missing":
        raise JobActionError("本地过滤记录不存在", 404)
    if result == "busy":
        raise JobActionError("BOSS 会话正在删除，暂不能删除本地记录", 409)
    return {"deleted": True, "message": "已从本地数据库删除过滤记录"}
