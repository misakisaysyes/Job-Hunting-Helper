"""Snapshot BOSS's 有交换 list independently of follow-up conversations."""

from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExchangeStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.execute("""CREATE TABLE IF NOT EXISTS monitored_exchanges (
            platform TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            company TEXT NOT NULL DEFAULT '',
            recruiter TEXT NOT NULL DEFAULT '',
            job_title TEXT NOT NULL DEFAULT '',
            source_job_id TEXT NOT NULL DEFAULT '',
            last_ts INTEGER NOT NULL DEFAULT 0,
            in_job_pool INTEGER NOT NULL DEFAULT 0,
            resume_delivery_message_id TEXT NOT NULL DEFAULT '',
            resume_delivered_at TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            scan_token TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (platform, conversation_id)
        )""")
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(monitored_exchanges)")}
        for name in ("resume_delivery_message_id", "resume_delivered_at"):
            if name not in columns:
                self.conn.execute(f"ALTER TABLE monitored_exchanges ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
        self.conn.execute("""CREATE INDEX IF NOT EXISTS idx_monitored_exchanges_active
            ON monitored_exchanges(active, last_ts DESC)""")
        self.conn.commit()

    def record_scan(self, item: dict[str, Any], scan_token: str) -> bool:
        existing = self.conn.execute("""SELECT 1 FROM monitored_exchanges
            WHERE platform = ? AND conversation_id = ?""",
            (item["platform"], item["conversation_id"])).fetchone()
        now = _now()
        self.conn.execute("""INSERT INTO monitored_exchanges (
            platform, conversation_id, company, recruiter, job_title, source_job_id,
            last_ts, in_job_pool, resume_delivery_message_id, resume_delivered_at,
            first_seen_at, updated_at, scan_token, active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        ON CONFLICT(platform, conversation_id) DO UPDATE SET
            company = excluded.company, recruiter = excluded.recruiter,
            job_title = excluded.job_title, source_job_id = excluded.source_job_id,
            last_ts = excluded.last_ts, in_job_pool = excluded.in_job_pool,
            resume_delivery_message_id = CASE WHEN excluded.resume_delivery_message_id != ''
                THEN excluded.resume_delivery_message_id ELSE monitored_exchanges.resume_delivery_message_id END,
            resume_delivered_at = CASE WHEN excluded.resume_delivery_message_id != ''
                THEN excluded.resume_delivered_at ELSE monitored_exchanges.resume_delivered_at END,
            updated_at = excluded.updated_at, scan_token = excluded.scan_token, active = 1""",
            (item["platform"], item["conversation_id"], item["company"],
             item["recruiter"], item["job_title"], item["source_job_id"],
             item["last_ts"], int(item["in_job_pool"]),
             item.get("resume_delivery_message_id", ""), item.get("resume_delivered_at", ""),
             now, now, scan_token))
        self.conn.commit()
        return existing is None

    def deactivate_unseen(self, scan_token: str) -> int:
        cursor = self.conn.execute("""UPDATE monitored_exchanges SET active = 0, updated_at = ?
            WHERE active = 1 AND scan_token != ?""", (_now(), scan_token))
        self.conn.commit()
        return cursor.rowcount

    def list_conversations(self, *, limit: int, offset: int, query: str = "", pool_status: str = ""
                           ) -> tuple[list[dict[str, Any]], int]:
        if pool_status not in {"", "inside", "outside"}:
            raise ValueError("未知岗位池筛选状态")
        params: list[str | int] = []
        where = "WHERE active = 1"
        if pool_status:
            where += " AND in_job_pool = ?"
            params.append(1 if pool_status == "inside" else 0)
        if query.strip():
            escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where += """ AND (company LIKE ? ESCAPE '\\' OR recruiter LIKE ? ESCAPE '\\'
                OR job_title LIKE ? ESCAPE '\\' OR conversation_id LIKE ? ESCAPE '\\')"""
            params.extend([f"%{escaped}%"] * 4)
        total = self.conn.execute(f"SELECT COUNT(*) FROM monitored_exchanges {where}", params).fetchone()[0]
        columns = ("platform", "conversation_id", "company", "recruiter", "job_title",
                   "source_job_id", "last_ts", "in_job_pool", "resume_delivery_message_id",
                   "resume_delivered_at", "first_seen_at", "updated_at")
        rows = self.conn.execute(f"""SELECT {', '.join(columns)} FROM monitored_exchanges {where}
            ORDER BY last_ts DESC, platform, conversation_id LIMIT ? OFFSET ?""",
            [*params, limit, offset]).fetchall()
        return [dict(zip(columns, row)) for row in rows], total

    def get_conversation(self, platform: str, conversation_id: str) -> dict[str, Any] | None:
        columns = ("platform", "conversation_id", "company", "recruiter", "job_title",
                   "source_job_id", "last_ts", "in_job_pool", "resume_delivery_message_id",
                   "resume_delivered_at", "first_seen_at", "updated_at")
        row = self.conn.execute(f"""SELECT {', '.join(columns)} FROM monitored_exchanges
            WHERE platform = ? AND conversation_id = ? AND active = 1""",
            (platform, conversation_id)).fetchone()
        return dict(zip(columns, row)) if row else None

    def mark_removed(self, platform: str, conversation_id: str) -> bool:
        """Keep the history but remove a verified BOSS deletion from the active list."""
        cursor = self.conn.execute("""UPDATE monitored_exchanges SET active = 0, updated_at = ?
            WHERE platform = ? AND conversation_id = ? AND active = 1""",
            (_now(), platform, conversation_id))
        self.conn.commit()
        return cursor.rowcount == 1
