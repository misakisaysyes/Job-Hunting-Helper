"""Persist web task executions for the workbench's daily overview."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, time, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

LOCAL_ZONE = ZoneInfo("Asia/Shanghai")
TASK_TYPES = ("collection", "monitoring")
TASK_LABELS = {"collection": "采集", "monitoring": "监测"}


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

    def start(self, task_type: str, started_at: str, message: str) -> int:
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
                (task_type, status, message, started_at) VALUES (?, 'running', ?, ?)""",
                (task_type, message, started_at))
            return cursor.lastrowid

    def finish(self, run_id: int, status: str, message: str,
               counts: dict[str, int], finished_at: str) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("""UPDATE task_runs SET status = ?, message = ?, counts_json = ?,
                finished_at = ? WHERE id = ?""",
                (status, message, json.dumps(counts, ensure_ascii=False), finished_at, run_id))

    def mark_interrupted(self) -> None:
        """A running task cannot survive a stopped API process."""
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as conn, conn:
            conn.execute("""UPDATE task_runs SET status = 'interrupted',
                message = 'API 服务重启，任务中断', finished_at = ?
                WHERE status = 'running'""", (now,))

    def today(self, *, now: datetime | None = None) -> dict:
        local_now = (now or datetime.now(timezone.utc)).astimezone(LOCAL_ZONE)
        start = datetime.combine(local_now.date(), time.min, LOCAL_ZONE)
        stop = start + timedelta(days=1)
        with closing(self._connect()) as conn:
            rows = conn.execute("""SELECT id, task_type, status, message, counts_json,
                started_at, finished_at FROM task_runs
                WHERE started_at >= ? AND started_at < ?
                ORDER BY started_at DESC, id DESC""",
                (start.astimezone(timezone.utc).isoformat(),
                 stop.astimezone(timezone.utc).isoformat())).fetchall()
        result = {
            "date": local_now.date().isoformat(),
            "timezone": "Asia/Shanghai",
            "collection": {"runs": 0, "totals": {}, "history": []},
            "monitoring": {"runs": 0, "totals": {}, "history": []},
        }
        for run_id, task_type, status, message, raw_counts, started_at, finished_at in rows:
            if task_type not in TASK_TYPES:
                continue
            counts = json.loads(raw_counts)
            item = {"id": run_id, "status": status, "message": message,
                    "counts": counts, "started_at": started_at,
                    "finished_at": finished_at}
            bucket = result[task_type]
            bucket["runs"] += 1
            bucket["history"].append(item)
            for key, value in counts.items():
                if isinstance(value, int) and not isinstance(value, bool):
                    bucket["totals"][key] = bucket["totals"].get(key, 0) + value
        return result
