"""Persist company-filter decisions for BOSS New Greetings independently of chat history."""

from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FilterStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.execute("""CREATE TABLE IF NOT EXISTS monitored_filter_candidates (
            platform TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            company TEXT NOT NULL,
            recruiter TEXT NOT NULL DEFAULT '',
            job_title TEXT NOT NULL DEFAULT '',
            source_job_id TEXT NOT NULL DEFAULT '',
            avatar TEXT NOT NULL DEFAULT '',
            matched_term TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending_review',
            error TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_scanned_at TEXT NOT NULL,
            scan_token TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (platform, conversation_id)
        )""")
        self.conn.execute("""CREATE INDEX IF NOT EXISTS idx_monitored_filter_status
            ON monitored_filter_candidates(status, updated_at DESC)""")
        self.conn.commit()

    def record_scan(self, greeting: dict[str, Any], matched_term: str, scan_token: str) -> bool:
        """Count only first inserts; updating or reviving a candidate is not an insert."""
        if not self.conn.in_transaction:
            self.conn.execute("BEGIN IMMEDIATE")
        existing = self.get_candidate(greeting["platform"], str(greeting["conversation_id"]))
        self.record_match(greeting, matched_term, scan_token)
        return existing is None

    def record_match(self, greeting: dict[str, Any], matched_term: str,
                     scan_token: str) -> bool:
        """Return True when a new review item was created; keep past decisions durable."""
        platform, conversation_id = greeting["platform"], str(greeting["conversation_id"])
        existing = self.conn.execute("""SELECT status FROM monitored_filter_candidates
            WHERE platform = ? AND conversation_id = ?""", (platform, conversation_id)).fetchone()
        now = _now()
        if existing is None:
            self.conn.execute("""INSERT INTO monitored_filter_candidates (
                platform, conversation_id, company, recruiter, job_title, source_job_id,
                avatar, matched_term, status, first_seen_at, updated_at, last_scanned_at, scan_token
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending_review', ?, ?, ?, ?)""",
                (platform, conversation_id, greeting["company"], greeting.get("recruiter", ""),
                 greeting.get("job_title", ""), greeting.get("source_job_id", ""),
                 greeting.get("avatar", ""), matched_term, now, now, now, scan_token))
            self.conn.commit()
            return True
        status = existing[0]
        new_status = "pending_review" if status in {"stale", "failed"} else status
        self.conn.execute("""UPDATE monitored_filter_candidates SET
            company = ?, recruiter = ?, job_title = ?, source_job_id = ?, avatar = ?,
            matched_term = ?, status = ?, error = CASE WHEN ? = 'pending_review' THEN '' ELSE error END,
            updated_at = ?, last_scanned_at = ?, scan_token = ?
            WHERE platform = ? AND conversation_id = ?""",
            (greeting["company"], greeting.get("recruiter", ""),
             greeting.get("job_title", ""), greeting.get("source_job_id", ""),
             greeting.get("avatar", ""), matched_term, new_status, new_status,
             now, now, scan_token, platform, conversation_id))
        self.conn.commit()
        return new_status == "pending_review" and status != "pending_review"

    def retire_unseen(self, scan_token: str) -> int:
        cursor = self.conn.execute("""UPDATE monitored_filter_candidates
            SET status = 'stale', error = '会话已不在新招呼列表或不再命中公司排除词', updated_at = ?
            WHERE status = 'pending_review' AND scan_token != ?""", (_now(), scan_token))
        self.conn.commit()
        return cursor.rowcount

    def list_candidates(self, *, limit: int, offset: int, query: str = "",
                        status: str = "") -> tuple[list[dict[str, Any]], int]:
        conditions: list[str] = []
        params: list[str] = []
        if query.strip():
            escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            term = f"%{escaped}%"
            conditions.append("""(company LIKE ? ESCAPE '\\' OR recruiter LIKE ? ESCAPE '\\'
                OR job_title LIKE ? ESCAPE '\\' OR matched_term LIKE ? ESCAPE '\\'
                OR conversation_id LIKE ? ESCAPE '\\')""")
            params.extend([term] * 5)
        if status:
            conditions.append("status = ?")
            params.append(status)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        total = self.conn.execute(
            f"SELECT COUNT(*) FROM monitored_filter_candidates{where}", params).fetchone()[0]
        columns = ("platform", "conversation_id", "company", "recruiter", "job_title",
                   "source_job_id", "avatar", "matched_term", "status", "error",
                   "first_seen_at", "updated_at", "last_scanned_at")
        rows = self.conn.execute(f"""SELECT {', '.join(columns)} FROM monitored_filter_candidates{where}
            ORDER BY CASE status WHEN 'pending_review' THEN 0 WHEN 'unknown' THEN 1 ELSE 2 END,
                     updated_at DESC, platform, conversation_id LIMIT ? OFFSET ?""",
            [*params, limit, offset]).fetchall()
        return [dict(zip(columns, row)) for row in rows], total

    def get_candidate(self, platform: str, conversation_id: str) -> dict[str, Any] | None:
        columns = ("platform", "conversation_id", "company", "recruiter", "job_title",
                   "source_job_id", "avatar", "matched_term", "status", "error",
                   "first_seen_at", "updated_at", "last_scanned_at")
        row = self.conn.execute(f"""SELECT {', '.join(columns)} FROM monitored_filter_candidates
            WHERE platform = ? AND conversation_id = ?""", (platform, conversation_id)).fetchone()
        return dict(zip(columns, row)) if row else None

    def delete_record(self, platform: str, conversation_id: str) -> str:
        """Remove only the local filter candidate, not the BOSS conversation."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute("""SELECT status FROM monitored_filter_candidates
                WHERE platform = ? AND conversation_id = ?""",
                (platform, conversation_id)).fetchone()
            if row is None or row[0] == "deleting":
                self.conn.rollback()
                return "missing" if row is None else "busy"
            self.conn.execute("""DELETE FROM monitored_filter_candidates
                WHERE platform = ? AND conversation_id = ?""", (platform, conversation_id))
            self.conn.commit()
            return "deleted"
        except Exception:
            self.conn.rollback()
            raise

    def claim_delete(self, platform: str, conversation_id: str, *,
                     allow_unknown: bool = False) -> dict[str, Any] | None:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            candidate = self.get_candidate(platform, conversation_id)
            allowed = {"pending_review", "failed", "unknown"} if allow_unknown else {"pending_review", "failed"}
            if candidate is None or candidate["status"] not in allowed:
                self.conn.rollback()
                return None
            self.conn.execute("""UPDATE monitored_filter_candidates
                SET status = 'deleting', updated_at = ?
                WHERE platform = ? AND conversation_id = ?""", (_now(), platform, conversation_id))
            self.conn.commit()
            return candidate
        except Exception:
            self.conn.rollback()
            raise

    def finish_delete(self, platform: str, conversation_id: str, *, outcome: str,
                      error: str = "") -> bool:
        if outcome not in {"deleted", "failed", "unknown"}:
            raise ValueError("未知会话删除结果")
        cursor = self.conn.execute("""UPDATE monitored_filter_candidates
            SET status = ?, error = ?, updated_at = ?
            WHERE platform = ? AND conversation_id = ? AND status = 'deleting'""",
            (outcome, error, _now(), platform, conversation_id))
        self.conn.commit()
        return cursor.rowcount == 1

    def recover_interrupted_deletes(self) -> int:
        cursor = self.conn.execute("""UPDATE monitored_filter_candidates
            SET status = 'unknown', error = '服务中断，删除结果未确认', updated_at = ?
            WHERE status = 'deleting'""", (_now(),))
        self.conn.commit()
        return cursor.rowcount
