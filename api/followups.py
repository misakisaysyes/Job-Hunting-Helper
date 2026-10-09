"""Prepare, edit, generate, and send follow-ups for eligible BOSS conversations."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from api.jobs import JobActionError
from ai.scheduler import AITaskScheduler
from browser.boss_followup import send_boss_followup
from browser.boss_monitor import _effective
from config import MONITORING_CONFIG
from data.conversation_store import ConversationStore, resolve_original_greeting
from data.job_input import load_resume_text
from data.job_store import JobStore
from message import MAX_GREETING_LENGTH, make_greeting_task


def _message_time(value: object) -> datetime | None:
    try:
        stamp = float(str(value))
        if stamp > 1e11:
            stamp /= 1000
        return datetime.fromtimestamp(stamp, timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def proposal(conversation: dict[str, Any], messages: list[dict[str, Any]],
             job: dict[str, Any] | None, config: dict, *,
             now: datetime | None = None) -> tuple[str, str, int] | None:
    """Return original text, current message ID, and observed repeat count."""
    candidate, _ = proposal_with_reason(conversation, messages, job, config, now=now)
    return candidate


def proposal_with_reason(conversation: dict[str, Any], messages: list[dict[str, Any]],
                         job: dict[str, Any] | None, config: dict, *,
                         now: datetime | None = None) -> tuple[tuple[str, str, int] | None, str]:
    """Use the scan's rules to explain why no repeat greeting can be prepared."""
    if not config["followup_enabled"]:
        return None, "追问功能未开启"
    judgment = conversation.get("judgment")
    if judgment == "unread" and not config["followup_unread"]:
        return None, "未开启对未读会话的追问"
    if judgment == "read_no_reply" and not config["followup_read_no_reply"]:
        return None, "未开启对已读未回会话的追问"
    if judgment not in {"unread", "read_no_reply"}:
        return None, "当前会话状态不支持追问"
    status = conversation.get("followup_status")
    if status == "pending_review":
        return None, "追问语已生成，等待发送"
    status_reasons = {
        "sending": "追问正在发送中",
        "unknown": "上次追问发送结果未确认，请先人工核查",
        "failed": "上次追问发送失败，请先人工核查",
        "skipped": "本次追问已跳过，等待重新监测",
    }
    if status in status_reasons:
        return None, status_reasons[status]
    effective = [message for message in messages if _effective(message)]
    if not effective:
        return None, "未读取到可用于判断的会话消息"
    if effective[-1].get("sender") != "self":
        return None, "最近一条消息并非自己发送的招呼语"
    latest = effective[-1]
    if str(latest.get("delivery_status") or "") not in {"1", "2"}:
        return None, "最近一条招呼语的送达状态未确认"
    original = str(conversation.get("greeting_text") or "").strip()
    if not original:
        original = resolve_original_greeting(effective, job)
    if not original:
        return None, "未找到原招呼语，无法原文追问"
    known_followup = (status == "idle" and int(conversation.get("followup_count") or 0) > 0
                      and str(latest.get("message_id") or "") == conversation.get("followup_anchor_id"))
    if latest.get("text", "").strip() != original and not known_followup:
        return None, "最近发送的消息不是原招呼语或已确认的追问，请重新核查会话"
    matching = [message for message in effective if message["sender"] == "self"
                and message.get("text", "").strip() == original]
    observed_count = max(0, len(matching) - 1)
    if max(int(conversation.get("followup_count") or 0), observed_count) >= config["followup_max_count"]:
        return None, f"已达到最多追问 {config['followup_max_count']} 次的上限"
    sent_at = _message_time(latest.get("sent_at"))
    if sent_at is None:
        return None, "无法确认上次招呼语的发送时间"
    now = now or datetime.now(timezone.utc)
    if config.get("followup_days") is not None and now - sent_at > timedelta(days=config["followup_days"]):
        return None, f"上次招呼语已超过最近 {config['followup_days']} 天的监测范围"
    if now - sent_at <= timedelta(hours=config["followup_cooldown_hours"]):
        return None, f"距离上次招呼语或追问不足 {config['followup_cooldown_hours']} 小时"
    anchor = str(latest.get("message_id") or "")
    if not anchor.isdigit():
        return None, "无法确认上次招呼语的消息编号"
    return (original, anchor, observed_count), ""


def annotate_followup_availability(conn: sqlite3.Connection, conversations: list[dict[str, Any]],
                                   config: dict, *, now: datetime | None = None) -> None:
    """Add the same follow-up decision used by scans to conversation list rows."""
    now = now or datetime.now(timezone.utc)
    cooldown_reason = f"距离上次招呼语或追问不足 {config['followup_cooldown_hours']} 小时"
    has_jobs = bool(conn.execute("""SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'collected_jobs'""").fetchone())
    for conversation in conversations:
        platform = conversation["platform"]
        conversation_id = conversation["conversation_id"]
        message_rows = conn.execute("""SELECT message_id, sender, text, sent_at,
            delivery_status, biz_type FROM monitored_messages
            WHERE platform = ? AND conversation_id = ?
            ORDER BY CAST(message_id AS INTEGER), message_id""",
            (platform, conversation_id)).fetchall()
        names = ("message_id", "sender", "text", "sent_at", "delivery_status", "biz_type")
        messages = [dict(zip(names, row)) for row in message_rows]
        job = None
        if has_jobs and conversation.get("source_job_id"):
            row = conn.execute("""SELECT greeting, greeting_send_state, record_json FROM collected_jobs
                WHERE platform = ? AND source_job_id = ?""",
                (platform, conversation["source_job_id"])).fetchone()
            if row:
                job = {**json.loads(row[2]), "greeting": row[0], "greeting_send_state": row[1]}
        conversation["followup_ai_available"] = bool(job and job.get("title") and job.get("jd"))
        conversation["followup_ai_reason"] = ("" if conversation["followup_ai_available"]
                                             else "未采集该岗位的JD，可手动编辑追问")
        conversation["job"] = {name: (job or {}).get(name) or "" for name in
                               ("title", "jd", "url", "hr_name", "recruitment_type", "company_size")}
        last_followup = next((message for message in messages
                              if message["message_id"] == conversation.get("followup_anchor_id")
                              and message["sender"] == "self"
                              and str(message.get("delivery_status") or "") in {"1", "2"}), None)
        conversation["last_followup_text"] = (
            str(last_followup.get("text") or "").strip()
            if last_followup and int(conversation.get("followup_count") or 0) > 0 else "")
        _, reason = proposal_with_reason(conversation, messages, job, config, now=now)
        cooldown_until = None
        latest = next((message for message in reversed(messages) if _effective(message)), None)
        if (latest and latest.get("sender") == "self"
                and str(latest.get("delivery_status") or "") in {"1", "2"}):
            sent_at = _message_time(latest.get("sent_at"))
            if sent_at is not None:
                deadline = sent_at + timedelta(hours=config["followup_cooldown_hours"])
                if now <= deadline:
                    cooldown_until = deadline
        pending = conversation.get("followup_status") == "pending_review"
        if pending and not str(conversation.get("followup_text") or "").strip():
            reason = "追问草稿为空，请重新运行监测"
        elif pending and not str(conversation.get("followup_anchor_id") or "").isdigit():
            reason = "追问所依据的消息编号无效，请重新运行监测"
        elif pending and int(conversation.get("followup_count") or 0) >= config["followup_max_count"]:
            reason = f"已达到最多追问 {config['followup_max_count']} 次的上限"
        elif pending and reason == "追问语已生成，等待发送":
            reason = cooldown_reason if cooldown_until else ""
        elif not pending and not reason:
            reason = "已满足追问条件，请重新运行监测生成追问语"
        conversation["followup_eligible"] = pending and not reason
        conversation["followup_reason"] = reason
        conversation["followup_cooldown_until"] = cooldown_until.isoformat() if cooldown_until else ""


def list_conversations(conn: sqlite3.Connection, config: dict, *, limit: int, offset: int,
                       query: str = "", conversation_status: str = "",
                       followup_status: str = "", now: datetime | None = None) -> tuple[list[dict], int]:
    """Filter computed availability before pagination, using the send rules."""
    availability_filter = followup_status in {"available", "cooling"}
    rows, total = ConversationStore(conn).list_conversations(
        limit=-1 if availability_filter else limit,
        offset=0 if availability_filter else offset, query=query,
        conversation_status=conversation_status,
        followup_status="" if availability_filter else followup_status)
    annotate_followup_availability(conn, rows, config, now=now)
    if availability_filter:
        rows = [row for row in rows if (
            bool(row["followup_cooldown_until"]) if followup_status == "cooling"
            else row["followup_eligible"] and not row["followup_cooldown_until"])]
        total = len(rows)
        rows = rows[offset:offset + limit]
    return rows, total


def _followup_text(value: object) -> str:
    if not isinstance(value, str):
        raise JobActionError("追问语必须是文本")
    text = value.strip()
    if not text or len(text) > MAX_GREETING_LENGTH:
        raise JobActionError(f"追问语须为 1～{MAX_GREETING_LENGTH} 字")
    return text


def get_followup(db_path: Path, platform: str, conversation_id: str) -> dict:
    if platform != "boss":
        raise JobActionError("当前仅支持 BOSS 追问", 400)
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        conversation = ConversationStore(conn).get_conversation(platform, conversation_id)
        if conversation is None:
            raise JobActionError("会话不存在或已退出监测", 404)
        annotate_followup_availability(conn, [conversation], MONITORING_CONFIG)
    return {"conversation": conversation}


def save_followup(db_path: Path, platform: str, conversation_id: str,
                  text: object, anchor_id: str, *, expected_text: str | None = None) -> dict:
    text = _followup_text(text)
    if platform != "boss":
        raise JobActionError("当前仅支持 BOSS 追问", 400)
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        if not ConversationStore(conn).save_followup(
                platform, conversation_id, text, anchor_id, expected_text=expected_text):
            raise JobActionError("会话或追问草稿已变化，请刷新后再编辑", 409)
    return {"text": text, **get_followup(db_path, platform, conversation_id)}


def generate_followup(db_path: Path, config: dict, scheduler: AITaskScheduler,
                      platform: str, conversation_id: str, anchor_id: str,
                      *, expected_text: str | None = None) -> dict:
    conversation = get_followup(db_path, platform, conversation_id)["conversation"]
    if (conversation["followup_status"] != "pending_review"
            or conversation["followup_anchor_id"] != anchor_id
            or (expected_text is not None and conversation["followup_text"] != expected_text)):
        raise JobActionError("会话或追问草稿已变化，请刷新后再生成", 409)
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        job = (JobStore(conn, score_threshold=config["ai"]["score_threshold"]).get_job(
            platform, conversation["source_job_id"]) if conversation["source_job_id"] else None)
    if not job or not job.get("title") or not job.get("jd"):
        raise JobActionError("未采集该岗位的JD，暂不可AI生成追问；可手动编辑", 409)
    try:
        task = make_greeting_task(job, load_resume_text(config), config["ai"]["greeting_user_prompt"])
    except ValueError as exc:
        raise JobActionError(str(exc)) from exc
    result = scheduler.submit(task).result()
    # Generation can finish after a scan, a send, or another edit; never overwrite their result.
    return save_followup(db_path, platform, conversation_id, result.text, anchor_id,
                         expected_text=conversation["followup_text"])


def send_followup(db_path: Path, cdp_url: str, platform: str,
                  conversation_id: str, *, max_count: int | None = None,
                  text: object = None, anchor_id: str | None = None,
                  expected_text: str | None = None) -> dict:
    if platform != "boss":
        raise JobActionError("当前仅支持 BOSS 追问", 400)
    if text is not None:
        text = _followup_text(text)
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        store = ConversationStore(conn)
        claimed = store.claim_followup(platform, conversation_id,
                                      max_count if max_count is not None
                                      else MONITORING_CONFIG["followup_max_count"],
                                      text=text, anchor_id=anchor_id, expected_text=expected_text)
    if claimed is None:
        raise JobActionError("会话或追问草稿已变化，当前没有可发送的待审核追问", 409)
    try:
        result = send_boss_followup(cdp_url, conversation_id,
                                    claimed["anchor_id"], claimed["text"], anchor_text=claimed["anchor_text"])
    except Exception as exc:
        with closing(sqlite3.connect(db_path, timeout=10)) as conn:
            ConversationStore(conn).finish_followup(
                platform, conversation_id, outcome="unknown",
                note=f"发送异常：{type(exc).__name__}")
        raise JobActionError("追问发送结果不明，请人工核查", 409) from exc
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        store = ConversationStore(conn)
        outcome = "sent" if result.verified else "unknown" if result.uncertain else "failed"
        if not store.finish_followup(platform, conversation_id, outcome=outcome,
                                     message_id=result.message_id, note=result.message):
            raise JobActionError("追问结果未能写入，请人工核查", 500)
        conversation = store.get_conversation(platform, conversation_id)
        if conversation is not None:
            annotate_followup_availability(conn, [conversation], MONITORING_CONFIG)
    if not result.verified:
        raise JobActionError(result.message, 409 if result.uncertain else 502)
    return {"conversation": conversation, "message": result.message}


def skip_followup(db_path: Path, platform: str, conversation_id: str) -> dict:
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        store = ConversationStore(conn)
        if not store.skip_followup(platform, conversation_id):
            raise JobActionError("当前会话没有可跳过的追问", 409)
        return {"conversation": store.get_conversation(platform, conversation_id)}
