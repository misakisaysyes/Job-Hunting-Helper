"""Persist web task executions for the workbench's daily overview."""

from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

LOCAL_ZONE = ZoneInfo("Asia/Shanghai")
TASK_TYPES = ("collection", "monitoring", "monitoring_followup", "monitoring_delete")
TASK_LABELS = {"collection": "采集", "monitoring": "扫描会话",
               "monitoring_followup": "批量追问", "monitoring_delete": "批量删除会话"}


class TaskAlreadyRunning(Exception):
    def __init__(self, task_type: str) -> None:
        self.task_type = task_type
        super().__init__(f"已有{TASK_LABELS.get(task_type, '其他')}任务正在运行，请等待任务完成后再启动")


class TaskRunStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        with closing(self._connect()) as conn, conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS task_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT NOT NULL DEFAULT '',
                counts_json TEXT NOT NULL DEFAULT '{}',
                started_at TEXT NOT NULL,
                finished_at TEXT
            )""")
            conn.execute("""CREATE INDEX IF NOT EXISTS idx_task_runs_type_started
                ON task_runs(task_type, started_at DESC)""")

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.db_path, timeout=10)

    def active_task_type(self) -> str | None:
        with closing(self._connect()) as conn:
            row = conn.execute("""SELECT task_type FROM task_runs
                WHERE status = 'running' ORDER BY id DESC LIMIT 1""").fetchone()
        return row[0] if row else None

    def start(self, task_type: str, started_at: str, message: str,
              *, counts: dict[str, int] | None = None) -> int:
        if task_type not in TASK_TYPES:
            raise ValueError(f"未知任务类型：{task_type}")
        with closing(self._connect()) as conn, conn:
            # SQLite serializes BEGIN IMMEDIATE writers, so concurrent requests
            # cannot both observe an empty running slot and start a task.
            conn.execute("BEGIN IMMEDIATE")
            running = conn.execute("""SELECT task_type FROM task_runs
                WHERE status = 'running' ORDER BY id DESC LIMIT 1""").fetchone()
            if running:
                raise TaskAlreadyRunning(running[0])
            cursor = conn.execute("""INSERT INTO task_runs
                (task_type, status, message, counts_json, started_at) VALUES (?, 'running', ?, ?, ?)""",
                (task_type, message, json.dumps(counts or {}, ensure_ascii=False), started_at))
            return cursor.lastrowid

    def finish(self, run_id: int, status: str, message: str,
               counts: dict[str, int], finished_at: str) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("""UPDATE task_runs SET status = ?, message = ?, counts_json = ?,
                finished_at = ? WHERE id = ? AND status = 'running'""",
                (status, message, json.dumps(counts, ensure_ascii=False), finished_at, run_id))

    def update_counts(self, run_id: int, counts: dict[str, int], *, message: str | None = None) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("""UPDATE task_runs SET counts_json = ?, message = COALESCE(?, message)
                WHERE id = ? AND status = 'running'""",
                (json.dumps(counts, ensure_ascii=False), message, run_id))

    def get_run(self, run_id: int) -> dict | None:
        with closing(self._connect()) as conn:
            row = conn.execute("""SELECT id, task_type, status, message, counts_json,
                started_at, finished_at FROM task_runs WHERE id = ?""", (run_id,)).fetchone()
        return self._run_snapshot(row)

    def latest(self, task_type: str) -> dict | None:
        with closing(self._connect()) as conn:
            row = conn.execute("""SELECT id, task_type, status, message, counts_json,
                started_at, finished_at FROM task_runs WHERE task_type = ? ORDER BY id DESC LIMIT 1""",
                (task_type,)).fetchone()
        return self._run_snapshot(row)

    @staticmethod
    def _run_snapshot(row: tuple | None) -> dict | None:
        if row is None:
            return None
        run_id, task_type, status, message, counts, started, finished = row
        return {"run_id": run_id, "task_type": task_type, "status": status, "message": message,
                "counts": json.loads(counts), "started_at": started, "finished_at": finished}

    def mark_interrupted(self) -> None:
        """A running task cannot survive a stopped API process."""
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as conn, conn:
            conn.execute("""UPDATE task_runs SET status = 'interrupted',
                message = 'API 服务重启，任务中断', finished_at = ?
                WHERE status = 'running'""", (now,))

    def today(self, *, now: datetime | None = None) -> dict:
        local_now = (now or datetime.now(timezone.utc)).astimezone(LOCAL_ZONE)
        return self.for_date(local_now.date())

    def for_date(self, day: date) -> dict:
        """Summarize runs by their start day and greetings by their sent day."""
        start = datetime.combine(day, time.min, LOCAL_ZONE)
        stop = start + timedelta(days=1)
        start_utc = start.astimezone(timezone.utc).isoformat()
        stop_utc = stop.astimezone(timezone.utc).isoformat()
        with closing(self._connect()) as conn:
            rows = conn.execute("""SELECT id, task_type, status, message, counts_json,
                started_at, finished_at FROM task_runs
                WHERE started_at >= ? AND started_at < ?
                ORDER BY started_at DESC, id DESC""",
                (start_utc, stop_utc)).fetchall()
            has_send_events = conn.execute("""SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'greeting_send_events'""").fetchone() is not None
            greeted = conn.execute("""SELECT COUNT(*) FROM greeting_send_events
                WHERE sent_at >= ? AND sent_at < ?""",
                (start_utc, stop_utc)).fetchone()[0] if has_send_events else 0
        result = {
            "date": day.isoformat(),
            "timezone": "Asia/Shanghai",
            **{task_type: {"runs": 0, "totals": {}, "history": []} for task_type in TASK_TYPES},
        }
        for run_id, task_type, status, message, raw_counts, started_at, finished_at in rows:
            if task_type not in TASK_TYPES:
                continue
            counts = json.loads(raw_counts)
            if task_type == "collection":
                counts.pop("ai_greeting_generated", None)
            item = {"id": run_id, "task_type": task_type, "status": status, "message": message,
                    "counts": counts, "started_at": started_at,
                    "finished_at": finished_at}
            bucket = result[task_type]
            bucket["runs"] += 1
            bucket["history"].append(item)
            for key, value in counts.items():
                if isinstance(value, int) and not isinstance(value, bool):
                    bucket["totals"][key] = bucket["totals"].get(key, 0) + value
        result["collection"]["totals"]["greeted"] = greeted
        for metric in ("communicated_new", "filter_new"):
            if any(metric not in run["counts"] for run in result["monitoring"]["history"]):
                result["monitoring"]["totals"][metric] = None
        return result
