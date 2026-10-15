"""Count current jobs and conversations in the selected workbench time range."""

from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import json
import sqlite3

from api.jobs import JobActionError
from data.conversation_store import ConversationStore
from data.exchange_store import ExchangeStore
from data.job_store import JobStore
from data.task_run_store import LOCAL_ZONE


def _bounds(start_value: str, end_value: str) -> tuple[str, str]:
    try:
        start = date.fromisoformat(start_value)
        end = date.fromisoformat(end_value)
        if start.isoformat() != start_value or end.isoformat() != end_value:
            raise ValueError
    except ValueError as exc:
        raise JobActionError("开始和结束日期须为有效的 YYYY-MM-DD 格式", 400) from exc
    if start > end:
        raise JobActionError("结束日期不能早于开始日期", 400)
    if end > datetime.now(LOCAL_ZONE).date():
        raise JobActionError("结束日期不能晚于今天", 400)
    lower = datetime.combine(start, time.min, LOCAL_ZONE).astimezone(timezone.utc)
    upper = datetime.combine(end + timedelta(days=1), time.min, LOCAL_ZONE).astimezone(timezone.utc)
    return lower.isoformat(), upper.isoformat()


def _local_day(value: str) -> str:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(LOCAL_ZONE).date().isoformat()


def validate_conversion_range(start_date: object, end_date: object) -> None:
    if not isinstance(start_date, str) or not isinstance(end_date, str):
        raise JobActionError("请选择有效的开始日期和结束日期", 400)
    _bounds(start_date, end_date)


def get_conversion_flow(db_path: Path, start_date: str, end_date: str) -> dict:
    lower, upper = _bounds(start_date, end_date)
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        JobStore(conn)
        ConversationStore(conn)
        ExchangeStore(conn)
        greeted = conn.execute("""SELECT platform, source_job_id, updated_at FROM collected_jobs
            WHERE job_status = 'greeted' AND updated_at >= ? AND updated_at < ?""",
            (lower, upper)).fetchall()
        initiated = conn.execute("""SELECT platform, conversation_id, source_job_id, first_seen_at
            FROM monitored_exchanges
            WHERE in_job_pool = 0 AND first_seen_at >= ? AND first_seen_at < ?""",
            (lower, upper)).fetchall()
        followed = conn.execute("""SELECT platform, conversation_id, source_job_id, updated_at
            FROM monitored_conversations
            WHERE judgment IN ('unread', 'read_no_reply') AND followup_count > 0
              AND updated_at >= ? AND updated_at < ?""",
            (lower, upper)).fetchall()
        exchanged = conn.execute("""SELECT platform, conversation_id, source_job_id, first_seen_at
            FROM monitored_exchanges
            WHERE resume_delivery_message_id != ''
              AND first_seen_at >= ? AND first_seen_at < ?""",
            (lower, upper)).fetchall()

    start_day = date.fromisoformat(start_date)
    day_count = (date.fromisoformat(end_date) - start_day).days + 1
    daily_metrics = [{"date": (start_day + timedelta(days=offset)).isoformat(),
                      "greeted": 0, "hr_initiated": 0, "followups": 0, "deliveries": 0}
                     for offset in range(day_count)]
    daily_by_date = {row["date"]: row for row in daily_metrics}
    for key, rows in (("greeted", greeted), ("hr_initiated", initiated),
                      ("followups", followed), ("deliveries", exchanged)):
        for row in rows:
            daily_by_date[_local_day(row[-1])][key] += 1

    contacts: dict[tuple[str, str, str], dict] = {}
    for platform, job_id, _ in greeted:
        contacts.setdefault(("job", platform, job_id),
                            {"source": "greeted", "followed_up": False, "delivered": False})
    for platform, conversation_id, _, _ in initiated:
        contacts.setdefault(("conversation", platform, conversation_id),
                            {"source": "hr_initiated", "followed_up": False, "delivered": False})

    def contact(platform: str, conversation_id: str, job_id: str) -> dict:
        job_key = ("job", platform, job_id)
        conversation_key = ("conversation", platform, conversation_id)
        key = (conversation_key if conversation_key in contacts else
               job_key if job_id and job_key in contacts else conversation_key)
        return contacts.setdefault(key, {"source": "earlier_or_unknown",
                                        "followed_up": False, "delivered": False})

    for platform, conversation_id, job_id, _ in followed:
        contact(platform, conversation_id, job_id)["followed_up"] = True
    for platform, conversation_id, job_id, _ in exchanged:
        contact(platform, conversation_id, job_id)["delivered"] = True

    flows: dict[tuple[str, bool, bool], int] = {}
    for item in contacts.values():
        key = item["source"], item["followed_up"], item["delivered"]
        flows[key] = flows.get(key, 0) + 1
    return {
        "start_date": start_date, "end_date": end_date, "timezone": "Asia/Shanghai",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metrics": {"greeted": len(greeted), "hr_initiated": len(initiated),
                    "followups": len(followed), "deliveries": len(exchanged),
                    "contacts": len(contacts)},
        "daily_metrics": daily_metrics,
        "flows": [{"source": source, "followed_up": followed_up,
                   "delivered": delivered, "count": count}
                  for (source, followed_up, delivered), count in sorted(flows.items())],
        "basis_version": 5,
        "delivery_basis": "confirmed_attachment_resume_exchange_first_seen",
    }


def _ensure_snapshot_table(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS workbench_conversion_snapshot (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        payload_json TEXT NOT NULL
    )""")


def get_saved_conversion_flow(db_path: Path) -> dict | None:
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        _ensure_snapshot_table(conn)
        row = conn.execute("""SELECT payload_json FROM workbench_conversion_snapshot
            WHERE id = 1""").fetchone()
    return json.loads(row[0]) if row else None


def generate_and_save_conversion_flow(db_path: Path, start_date: object, end_date: object) -> dict:
    validate_conversion_range(start_date, end_date)
    result = get_conversion_flow(db_path, start_date, end_date)
    with closing(sqlite3.connect(db_path, timeout=10)) as conn, conn:
        _ensure_snapshot_table(conn)
        conn.execute("""INSERT INTO workbench_conversion_snapshot (id, payload_json)
            VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET payload_json = excluded.payload_json""",
            (json.dumps(result, ensure_ascii=False),))
    return result
