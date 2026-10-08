"""Persist jobs, greetings, and workflow state."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Callable

JOB_STATUSES = frozenset({
    "scored", "greeting_ready", "greeted",
    "ended_monitoring_expired", "ended_rejected", "ended_applied", "ended_forced",
})


def _initial_job_status(record: dict[str, Any], score_threshold: float | None) -> str:
    status = record.get("job_status")
    status = {"collected": "scored", "filtered": "scored", "monitoring": "greeted"}.get(status, status)
    if status is None:
        if record.get("ai_score_status") == "scored":
            score = record.get("ai_score")
            status = ("greeting_ready" if score_threshold is not None and score is not None
                      and score >= score_threshold else "scored")
        elif record.get("ai_score_status") in (None, "not_scored"):
            status = "greeting_ready"
        else:
            status = "scored"
    if status not in JOB_STATUSES:
        raise ValueError(f"未知岗位状态：{status}")
    below_threshold = (record.get("ai_score_status") == "scored"
                       and score_threshold is not None
                       and record.get("ai_score") is not None
                       and record["ai_score"] < score_threshold)
    if status == "scored" and str(record.get("greeting") or "").strip() and not below_threshold:
        status = "greeting_ready"
    return status


class JobStore:
    def __init__(self, conn: sqlite3.Connection, *, score_threshold: float | None = None) -> None:
        self.conn = conn
        self.score_threshold = score_threshold
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS collected_jobs (
                platform TEXT NOT NULL,
                source_job_id TEXT NOT NULL,
                record_json TEXT NOT NULL,
                ai_score_status TEXT NOT NULL,
                ai_score REAL,
                ai_score_reason TEXT NOT NULL,
                ai_score_error TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                job_status TEXT NOT NULL,
                greeting TEXT NOT NULL DEFAULT '',
                greeting_send_state TEXT NOT NULL DEFAULT 'idle',
                greeting_send_note TEXT NOT NULL DEFAULT '',
                chat_url TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (platform, source_job_id)
            )
        """)
        self.conn.commit()
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(collected_jobs)")}
        if any(column not in columns for column in ("updated_at", "job_status", "greeting", "greeting_send_state", "greeting_send_note", "chat_url")):
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                columns = {row[1] for row in self.conn.execute("PRAGMA table_info(collected_jobs)")}
                if "updated_at" not in columns:
                    self.conn.execute("ALTER TABLE collected_jobs ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
                    self.conn.execute("UPDATE collected_jobs SET updated_at = collected_at")
                if "job_status" not in columns:
                    self.conn.execute("ALTER TABLE collected_jobs ADD COLUMN job_status TEXT NOT NULL DEFAULT 'collected'")
                    self.conn.execute("UPDATE collected_jobs SET job_status = 'scored' WHERE ai_score_status = 'scored'")
                if "greeting" not in columns:
                    self.conn.execute("ALTER TABLE collected_jobs ADD COLUMN greeting TEXT NOT NULL DEFAULT ''")
                    self.conn.execute("UPDATE collected_jobs SET job_status = 'scored' WHERE job_status = 'collected' AND ai_score_status = 'not_scored'")
                if "greeting_send_state" not in columns:
                    self.conn.execute("ALTER TABLE collected_jobs ADD COLUMN greeting_send_state TEXT NOT NULL DEFAULT 'idle'")
                if "greeting_send_note" not in columns:
                    self.conn.execute("ALTER TABLE collected_jobs ADD COLUMN greeting_send_note TEXT NOT NULL DEFAULT ''")
                if "chat_url" not in columns:
                    self.conn.execute("ALTER TABLE collected_jobs ADD COLUMN chat_url TEXT NOT NULL DEFAULT ''")
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        self.conn.execute("""CREATE TABLE IF NOT EXISTS job_status_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            source_job_id TEXT NOT NULL,
            status TEXT NOT NULL,
            changed_at TEXT NOT NULL
        )""")
        self.conn.execute("""CREATE INDEX IF NOT EXISTS idx_job_status_events_job
            ON job_status_events(platform, source_job_id, id)""")
        self.conn.commit()
        if (not self.conn.execute("SELECT 1 FROM job_status_events LIMIT 1").fetchone()
                and self.conn.execute("SELECT 1 FROM collected_jobs LIMIT 1").fetchone()):
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                if not self.conn.execute("SELECT 1 FROM job_status_events LIMIT 1").fetchone():
                    self.conn.execute("""INSERT INTO job_status_events(platform, source_job_id, status, changed_at)
                        SELECT platform, source_job_id, job_status, updated_at FROM collected_jobs""")
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        if self.conn.execute("""SELECT 1 FROM collected_jobs
            WHERE job_status IN ('collected', 'filtered', 'monitoring') LIMIT 1""").fetchone():
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                self.conn.execute("""UPDATE collected_jobs SET job_status = CASE job_status
                    WHEN 'monitoring' THEN 'greeted' ELSE 'scored' END
                    WHERE job_status IN ('collected', 'filtered', 'monitoring')""")
                self.conn.execute("""UPDATE job_status_events SET status = CASE status
                    WHEN 'monitoring' THEN 'greeted' ELSE 'scored' END
                    WHERE status IN ('collected', 'filtered', 'monitoring')""")
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        ready_jobs = self.conn.execute("""SELECT platform, source_job_id FROM collected_jobs
            WHERE job_status = 'scored' AND (
                ai_score_status = 'not_scored'
                OR (ai_score_status = 'scored' AND ? IS NOT NULL AND ai_score >= ?)
            )""", (score_threshold, score_threshold)).fetchall()
        if ready_jobs:
            now = datetime.now(timezone.utc).isoformat()
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                self.conn.execute("""UPDATE collected_jobs
                    SET job_status = 'greeting_ready', updated_at = ?
                    WHERE job_status = 'scored' AND (
                        ai_score_status = 'not_scored'
                        OR (ai_score_status = 'scored' AND ? IS NOT NULL AND ai_score >= ?)
                    )""", (now, score_threshold, score_threshold))
                for platform, source_job_id in ready_jobs:
                    self._record_status(platform, source_job_id, "greeting_ready", now)
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def _record_status(self, platform: str, source_job_id: str, status: str, now: str) -> None:
        previous = self.conn.execute("""SELECT status FROM job_status_events
            WHERE platform = ? AND source_job_id = ? ORDER BY id DESC LIMIT 1""",
            (platform, source_job_id),
        ).fetchone()
        if previous is None or previous[0] != status:
            self.conn.execute("""INSERT INTO job_status_events
                (platform, source_job_id, status, changed_at) VALUES (?, ?, ?, ?)""",
                (platform, source_job_id, status, now),
            )

    def contains(self, platform: str, source_job_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM collected_jobs WHERE platform = ? AND source_job_id = ?",
            (platform, source_job_id),
        ).fetchone()
        return row is not None

    def save(self, record: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        job_status = _initial_job_status(record, self.score_threshold)
        self.conn.execute(
            """INSERT INTO collected_jobs (
                platform, source_job_id, record_json, ai_score_status, ai_score,
                ai_score_reason, ai_score_error, collected_at, updated_at, job_status, greeting
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform, source_job_id) DO UPDATE SET
                record_json = excluded.record_json,
                ai_score_status = excluded.ai_score_status,
                ai_score = excluded.ai_score,
                ai_score_reason = excluded.ai_score_reason,
                ai_score_error = excluded.ai_score_error,
                updated_at = excluded.updated_at,
                greeting = CASE WHEN ? THEN excluded.greeting ELSE collected_jobs.greeting END,
                job_status = CASE
                    WHEN ? THEN excluded.job_status
                    WHEN collected_jobs.job_status IN (
                        'greeting_ready', 'greeted', 'ended_monitoring_expired',
                        'ended_rejected', 'ended_applied', 'ended_forced'
                    ) THEN collected_jobs.job_status
                    ELSE excluded.job_status
                END""",
            (
                record["source_platform"], record["source_job_id"],
                json.dumps(record, ensure_ascii=False),
                record.get("ai_score_status", "not_scored"), record.get("ai_score"),
                record.get("ai_score_reason", ""), record.get("ai_score_error", ""),
                now, now, job_status, record.get("greeting", ""),
                "greeting" in record, record.get("job_status") is not None,
            ),
        )
        platform, source_job_id = record["source_platform"], record["source_job_id"]
        current_status = self.conn.execute(
            "SELECT job_status FROM collected_jobs WHERE platform = ? AND source_job_id = ?",
            (platform, source_job_id),
        ).fetchone()[0]
        if current_status == "greeting_ready" and not self.conn.execute(
            """SELECT 1 FROM job_status_events
               WHERE platform = ? AND source_job_id = ? LIMIT 1""",
            (platform, source_job_id),
        ).fetchone():
            self._record_status(platform, source_job_id, "scored", now)
        self._record_status(platform, source_job_id, current_status, now)
        self.conn.commit()

    def _row_to_job(self, row: tuple[Any, ...]) -> dict[str, Any]:
        (record_json, collected_at, updated_at, job_status, greeting, send_state, send_note, chat_url,
         status, score, reason, error) = row
        record = json.loads(record_json)
        platform, source_job_id = record["source_platform"], record["source_job_id"]
        history = [event[0] for event in self.conn.execute("""SELECT status FROM job_status_events
            WHERE platform = ? AND source_job_id = ? ORDER BY id""",
            (platform, source_job_id),
        ).fetchall()]
        normalized_history = []
        for event_status in history:
            normalized = {"collected": "scored", "filtered": "scored", "monitoring": "greeted"}.get(event_status, event_status)
            if not normalized_history or normalized_history[-1] != normalized:
                normalized_history.append(normalized)
        history = normalized_history
        record.update(
            collected_at=collected_at,
            updated_at=updated_at,
            job_status=job_status,
            job_status_history=history,
            greeting=greeting,
            greeting_send_state=send_state,
            greeting_send_note=send_note,
            chat_url=chat_url,
            ai_score_status=status,
            ai_score=score,
            ai_score_reason=reason,
            ai_score_error=error,
        )
        return record

    def get_job(self, platform: str, source_job_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """SELECT record_json, collected_at, updated_at, job_status, greeting,
                      greeting_send_state, greeting_send_note, chat_url,
                      ai_score_status, ai_score, ai_score_reason, ai_score_error
               FROM collected_jobs WHERE platform = ? AND source_job_id = ?""",
            (platform, source_job_id),
        ).fetchone()
        return self._row_to_job(row) if row else None

    def list_jobs(self, *, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        total = self.conn.execute("SELECT COUNT(*) FROM collected_jobs").fetchone()[0]
        rows = self.conn.execute(
            """SELECT record_json, collected_at, updated_at, job_status, greeting,
                      greeting_send_state, greeting_send_note, chat_url, ai_score_status, ai_score,
                      ai_score_reason, ai_score_error
               FROM collected_jobs
               ORDER BY collected_at DESC, platform, source_job_id
               LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        return [self._row_to_job(row) for row in rows], total

    def list_monitorable_jobs(self, since: datetime) -> list[dict[str, Any]]:
        """Return greeted jobs collected after an aware timestamp."""
        if since.tzinfo is None or since.utcoffset() is None:
            raise ValueError("监测起始时间必须包含时区")
        rows = self.conn.execute(
            """SELECT platform, source_job_id, collected_at FROM collected_jobs
               WHERE job_status = 'greeted'
               ORDER BY collected_at, platform, source_job_id"""
        ).fetchall()
        selected = []
        for platform, source_job_id, collected_at in rows:
            try:
                timestamp = datetime.fromisoformat(collected_at.replace("Z", "+00:00"))
            except ValueError:
                continue
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            if timestamp > since:
                job = self.get_job(platform, source_job_id)
                if job is not None:
                    selected.append(job)
        return selected

    def search_jobs(self, *, limit: int, offset: int,
                    matches: Callable[[dict[str, Any]], bool]) -> tuple[list[dict[str, Any]], int]:
        """Apply search before pagination, then hydrate only the visible jobs."""
        keys = []
        rows = self.conn.execute("""SELECT platform, source_job_id, record_json, job_status,
                                         ai_score_status, ai_score
                                  FROM collected_jobs
                                  ORDER BY collected_at DESC, platform, source_job_id""")
        for platform, source_job_id, record_json, status, score_status, score in rows:
            record = json.loads(record_json)
            record.update(job_status=status, ai_score_status=score_status, ai_score=score)
            if matches(record):
                keys.append((platform, source_job_id))
        total = len(keys)
        jobs = [self.get_job(platform, source_job_id)
                for platform, source_job_id in keys[offset:offset + limit]]
        return [job for job in jobs if job is not None], total

    def set_status(self, platform: str, source_job_id: str, status: str) -> bool:
        if status not in JOB_STATUSES:
            raise ValueError(f"未知岗位状态：{status}")
        cursor = self.conn.execute(
            """UPDATE collected_jobs SET job_status = ?, updated_at = ?
               WHERE platform = ? AND source_job_id = ?""",
            (status, datetime.now(timezone.utc).isoformat(), platform, source_job_id),
        )
        if cursor.rowcount == 1:
            self._record_status(platform, source_job_id, status, datetime.now(timezone.utc).isoformat())
        self.conn.commit()
        return cursor.rowcount == 1

    def save_rescore(self, platform: str, source_job_id: str, *, score: float,
                     reason: str) -> bool:
        """Reset a pre-send job's draft and stage after a new AI score."""
        now = datetime.now(timezone.utc).isoformat()
        row = self.conn.execute("""SELECT record_json FROM collected_jobs
            WHERE platform = ? AND source_job_id = ?
              AND job_status IN ('scored', 'greeting_ready')
              AND greeting_send_state = 'idle'""", (platform, source_job_id)).fetchone()
        if row is None:
            return False
        record = json.loads(row[0])
        record.update(ai_score_status="scored", ai_score=score,
                      ai_score_reason=reason, ai_score_error="")
        record.pop("greeting", None)
        record.pop("ai_greeting_status", None)
        record.pop("ai_greeting_error", None)
        next_status = "scored" if self.score_threshold is not None and score < self.score_threshold else "greeting_ready"
        cursor = self.conn.execute("""UPDATE collected_jobs SET record_json = ?,
                ai_score_status = 'scored', ai_score = ?, ai_score_reason = ?, ai_score_error = '',
                greeting = '', greeting_send_note = '', job_status = ?, updated_at = ?
            WHERE platform = ? AND source_job_id = ?
              AND job_status IN ('scored', 'greeting_ready')
              AND greeting_send_state = 'idle'""",
            (json.dumps(record, ensure_ascii=False), score, reason, next_status, now,
             platform, source_job_id),
        )
        if cursor.rowcount == 1:
            self._record_status(platform, source_job_id, "scored", now)
            self._record_status(platform, source_job_id, next_status, now)
        self.conn.commit()
        return cursor.rowcount == 1

    def mark_rescore_failed(self, platform: str, source_job_id: str, error: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.conn.execute("""UPDATE collected_jobs
            SET ai_score_status = 'score_failed', ai_score = NULL,
                ai_score_reason = '', ai_score_error = ?, updated_at = ?
            WHERE platform = ? AND source_job_id = ?
              AND job_status IN ('scored', 'greeting_ready')
              AND greeting_send_state = 'idle'""",
            (error, now, platform, source_job_id),
        )
        self.conn.commit()
        return cursor.rowcount == 1

    def save_greeting(self, platform: str, source_job_id: str, greeting: str) -> bool:
        text = greeting.strip()
        if not text or len(text) > 300:
            raise ValueError("招呼语须为 1～300 字")
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.conn.execute(
            """UPDATE collected_jobs SET greeting = ?, job_status = 'greeting_ready', updated_at = ?
               WHERE platform = ? AND source_job_id = ?
                 AND job_status = 'greeting_ready'
                 AND greeting_send_state = 'idle'""",
            (text, now, platform, source_job_id),
        )
        if cursor.rowcount == 1:
            self._record_status(platform, source_job_id, "greeting_ready", now)
        self.conn.commit()
        return cursor.rowcount == 1

    def start_greeting(self, platform: str, source_job_id: str) -> bool:
        """Explicitly let a scored job enter the greeting stage without a draft."""
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.conn.execute(
            """UPDATE collected_jobs SET job_status = 'greeting_ready', updated_at = ?
               WHERE platform = ? AND source_job_id = ? AND job_status = 'scored'
                 AND greeting_send_state = 'idle'""",
            (now, platform, source_job_id),
        )
        if cursor.rowcount == 1:
            self._record_status(platform, source_job_id, "greeting_ready", now)
        self.conn.commit()
        return cursor.rowcount == 1

    def reserve_greeting_send(self, platform: str, source_job_id: str, greeting: str) -> bool:
        text = greeting.strip()
        if not text or len(text) > 300:
            raise ValueError("招呼语须为 1～300 字")
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.conn.execute(
            """UPDATE collected_jobs SET greeting = ?, greeting_send_state = 'sending',
                   greeting_send_note = '', job_status = 'greeting_ready', updated_at = ?
               WHERE platform = ? AND source_job_id = ?
                 AND job_status = 'greeting_ready'
                 AND greeting_send_state = 'idle'""",
            (text, now, platform, source_job_id),
        )
        if cursor.rowcount == 1:
            self._record_status(platform, source_job_id, "greeting_ready", now)
        self.conn.commit()
        return cursor.rowcount == 1

    def mark_interrupted_sends_unknown(self) -> int:
        """Recover sends left in progress by a prior server process without retrying."""
        cursor = self.conn.execute(
            """UPDATE collected_jobs SET greeting_send_state = 'unknown', updated_at = ?
               WHERE greeting_send_state = 'sending'""",
            (datetime.now(timezone.utc).isoformat(),),
        )
        self.conn.commit()
        return cursor.rowcount

    def finish_greeting_send(self, platform: str, source_job_id: str, *, outcome: str,
                             chat_url: str = "", note: str = "") -> bool:
        if outcome not in {"sent", "failed", "unknown"}:
            raise ValueError("未知发送结果")
        now = datetime.now(timezone.utc).isoformat()
        next_state = "idle" if outcome == "failed" else outcome
        cursor = self.conn.execute(
            """UPDATE collected_jobs
               SET greeting_send_state = ?,
                   greeting_send_note = ?,
                   job_status = CASE WHEN ? = 'sent' THEN 'greeted' ELSE job_status END,
                   chat_url = CASE WHEN ? = 'sent' THEN ? ELSE chat_url END,
                   updated_at = ?
               WHERE platform = ? AND source_job_id = ? AND greeting_send_state = 'sending'""",
            (next_state, note, outcome, outcome, chat_url, now, platform, source_job_id),
        )
        if cursor.rowcount == 1 and outcome == "sent":
            self._record_status(platform, source_job_id, "greeted", now)
        self.conn.commit()
        return cursor.rowcount == 1

    def confirm_greeting_not_sent(self, platform: str, source_job_id: str) -> bool:
        """Unlock an uncertain send only after the user checks the BOSS conversation."""
        cursor = self.conn.execute(
            """UPDATE collected_jobs SET greeting_send_state = 'idle',
                   greeting_send_note = '已人工确认未发送', updated_at = ?
               WHERE platform = ? AND source_job_id = ?
                 AND greeting_send_state = 'unknown'
                 AND job_status IN ('scored', 'greeting_ready')""",
            (datetime.now(timezone.utc).isoformat(), platform, source_job_id),
        )
        self.conn.commit()
        return cursor.rowcount == 1

    def mark_greeting_sent_after_verification(self, platform: str, source_job_id: str,
                                               chat_url: str) -> bool:
        """Recover an uncertain send after the matching BOSS chat shows the greeting."""
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.conn.execute(
            """UPDATE collected_jobs SET greeting_send_state = 'sent',
                   greeting_send_note = '已在 BOSS 会话中确认招呼语',
                   job_status = 'greeted', chat_url = ?, updated_at = ?
               WHERE platform = ? AND source_job_id = ?
                 AND greeting_send_state = 'unknown'
                 AND job_status IN ('scored', 'greeting_ready')""",
            (chat_url, now, platform, source_job_id),
        )
        if cursor.rowcount == 1:
            self._record_status(platform, source_job_id, "greeted", now)
        self.conn.commit()
        return cursor.rowcount == 1

    def force_end(self, platform: str, source_job_id: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.conn.execute(
            """UPDATE collected_jobs SET job_status = 'ended_forced', updated_at = ?
               WHERE platform = ? AND source_job_id = ?
                 AND job_status IN ('scored', 'greeting_ready', 'greeted')
                 AND greeting_send_state != 'sending'""",
            (now, platform, source_job_id),
        )
        if cursor.rowcount == 1:
            self._record_status(platform, source_job_id, "ended_forced", now)
        self.conn.commit()
        return cursor.rowcount == 1

    def delete(self, platform: str, source_job_id: str) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM collected_jobs WHERE platform = ? AND source_job_id = ?",
            (platform, source_job_id),
        )
        if cursor.rowcount == 1:
            self.conn.execute("DELETE FROM job_status_events WHERE platform = ? AND source_job_id = ?",
                              (platform, source_job_id))
        self.conn.commit()
        return cursor.rowcount == 1
