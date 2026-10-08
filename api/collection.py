"""Track the collection started from the local web API."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, Thread

from config import DEFAULT_CONFIG
from ai.credentials import current_api_key
from ai.scheduler import AITaskScheduler
from data.task_run_store import TaskAlreadyRunning, TaskRunStore
from run import run_collection, validate_run_config


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CollectionRun:
    def __init__(self, db_path: Path, ai_scheduler: AITaskScheduler | None = None) -> None:
        self.db_path = db_path
        self.run_store = TaskRunStore(db_path)
        self.ai_scheduler = ai_scheduler
        self.lock = Lock()
        self.status = "idle"
        self.message = ""
        self.counts: dict[str, int] = {}
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.run_id: int | None = None

    def snapshot(self) -> dict:
        with self.lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> dict:
        return {
            "status": self.status,
            "message": self.message,
            "counts": self.counts.copy(),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "run_id": self.run_id,
        }

    def start(self) -> dict | None:
        with self.lock:
            if self.status == "running":
                return None
            active_task = self.run_store.active_task_type()
            if active_task:
                raise TaskAlreadyRunning(active_task)
            config = deepcopy(DEFAULT_CONFIG)
            config["safety"]["state_db"] = str(self.db_path)
            config["collection"]["max_jobs"] = 0
            validate_run_config(config)
            if config["ai"]["use_ai_score"]:
                key_env = config["ai"]["api_key_env"]
                if not current_api_key(key_env):
                    raise ValueError(f"缺少 AI API Key：请在配置页填写并生效，或设置 {key_env} 后重启 API 服务")
            started_at = _now()
            message = "正在连接 Chrome 并采集岗位…"
            run_id = self.run_store.start("collection", started_at, message)
            self.status = "running"
            self.message = message
            self.counts = {}
            self.started_at = started_at
            self.finished_at = None
            self.run_id = run_id
            state = self._snapshot_locked()
        Thread(target=self._execute, args=(config, run_id), name="web-collection").start()
        return state

    def _event(self, message: str) -> None:
        latest = message.strip().splitlines()[-1] if message.strip() else ""
        if latest:
            with self.lock:
                self.message = latest[:500]

    def _execute(self, config: dict, run_id: int | None = None) -> None:
        try:
            result = run_collection(config, on_record=lambda record: None,
                                    on_event=self._event, ai_scheduler=self.ai_scheduler)
        except Exception as exc:
            detail = str(exc).strip()
            with self.lock:
                self.status = "failed"
                self.message = f"{type(exc).__name__}: {detail[-450:]}" if detail else type(exc).__name__
                self.finished_at = _now()
        else:
            with self.lock:
                self.status = result.status
                self.message = result.message or "采集已结束"
                self.counts = result.counts.copy()
                self.finished_at = _now()
        if run_id is not None:
            with self.lock:
                status, message = self.status, self.message
                counts, finished_at = self.counts.copy(), self.finished_at
            self.run_store.finish(run_id, status, message, counts, finished_at)
