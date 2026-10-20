"""Track the collection started from the local web API."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from threading import Event, Lock, Thread

from config import DEFAULT_CONFIG
from ai.credentials import current_api_key
from ai.scheduler import AITaskScheduler
from data.task_run_store import TaskAlreadyRunning, TaskRunStore
from run import run_collection, validate_run_config


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _effective_web_config(db_path: Path) -> dict:
    config = deepcopy(DEFAULT_CONFIG)
    config["safety"]["state_db"] = str(db_path)
    config["collection"]["max_jobs"] = 0
    config["collection"]["boss_salary_decode_failure"] = "skip_job"
    return config


def _display_config(config: dict) -> dict:
    """Expose the active collection choices without paths, prompts or credentials."""
    collection = config["collection"]
    profile = config["profile"]
    ai = config["ai"]
    return {
        "mode": collection["mode"],
        "expect_id_prefixes": [item[:6] for item in collection["encrypt_expect_id"]],
        "keywords": collection["keywords"].copy(),
        "cities": collection["cities"].copy(),
        "max_pages": collection["max_pages"],
        "sort": collection["sort"],
        "target_jobs": collection["target_jobs"],
        "max_jobs": collection["max_jobs"],
        "salary_decode_failure": collection["boss_salary_decode_failure"],
        "daily_search_page_limit": collection["daily_search_page_limit"],
        "daily_detail_page_limit": collection["daily_detail_page_limit"],
        "experience_filters": profile["experience_filters"].copy(),
        "education": profile["education"].copy(),
        "company_sizes": profile["company_sizes"].copy(),
        "recruitment_types": profile["recruitment_types"].copy(),
        "salary_min": profile["salary_min"],
        "salary_max": profile["salary_max"],
        "filter_unparsed_salary": profile["filter_unparsed_salary"],
        "deal_breaker_count": len(profile["deal_breakers"]),
        "jd_deal_breaker_count": len(profile["jd_deal_breakers"]),
        "blocked_company_count": len(profile["blocked_companies"]),
        "exclude_headhunter": profile["exclude_headhunter"],
        "use_ai_score": ai["use_ai_score"],
        "score_threshold": ai["score_threshold"],
        "use_ai_greeting": ai["use_ai_greeting"],
        "silent_background": config["browser"]["silent_background"],
    }


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
        self.stop_event: Event | None = None
        self.run_config_summary: dict | None = None

    def snapshot(self) -> dict:
        with self.lock:
            state = self._snapshot_locked()
        state["latest_run"] = self.run_store.latest("collection")
        return state

    def _snapshot_locked(self) -> dict:
        return {
            "status": self.status,
            "message": self.message,
            "counts": self.counts.copy(),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "run_id": self.run_id,
            "stopping": self.status == "running" and self.stop_event is not None
            and self.stop_event.is_set(),
            "config_summary": (self.run_config_summary if self.status == "running"
                               else _display_config(_effective_web_config(self.db_path))),
        }

    def start(self) -> dict | None:
        with self.lock:
            if self.status == "running":
                return None
            active_task = self.run_store.active_task_type()
            if active_task:
                raise TaskAlreadyRunning(active_task)
            config = _effective_web_config(self.db_path)
            validate_run_config(config)
            if config["ai"]["use_ai_score"] or config["ai"]["use_ai_greeting"]:
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
            self.stop_event = Event()
            self.run_config_summary = _display_config(config)
            state = self._snapshot_locked()
        Thread(target=self._execute, args=(config, run_id, self.stop_event), name="web-collection").start()
        return state

    def stop(self) -> dict | None:
        """Request a graceful stop; the worker finalizes the run and its results."""
        with self.lock:
            if self.status != "running":
                return None
            if self.stop_event is None:
                self.stop_event = Event()
            self.stop_event.set()
            self.message = "正在中断采集，整理已抓取的岗位…"
            run_id, counts = self.run_id, self.counts.copy()
            state = self._snapshot_locked()
        if run_id is not None:
            try:
                self.run_store.update_counts(run_id, counts, message=state["message"])
            except sqlite3.Error:
                pass
        return state

    def _event(self, message: str) -> None:
        latest = message.strip().splitlines()[-1] if message.strip() else ""
        if latest:
            with self.lock:
                if self.stop_event is None or not self.stop_event.is_set():
                    self.message = latest[:500]

    def _progress(self, counts: dict[str, int]) -> None:
        with self.lock:
            if self.status != "running":
                return
            self.counts = counts.copy()
            run_id = self.run_id
        if run_id is not None:
            try:
                self.run_store.update_counts(run_id, counts)
            except sqlite3.Error:
                # A transient dashboard write must not stop job collection.
                pass

    def _execute(self, config: dict, run_id: int | None = None,
                 stop_event: Event | None = None) -> None:
        try:
            result = run_collection(config, on_record=lambda record: None,
                                    on_event=self._event, on_progress=self._progress,
                                    ai_scheduler=self.ai_scheduler, stop_event=stop_event)
        except Exception as exc:
            detail = str(exc).strip()
            status = "failed"
            message = f"{type(exc).__name__}: {detail[-450:]}" if detail else type(exc).__name__
            result_counts = {}
        else:
            status = result.status
            message = result.message or "采集已结束"
            result_counts = result.counts
        with self.lock:
            if status != "failed" and stop_event is not None and stop_event.is_set():
                status = "stopped"
                message = "采集已中断；已保留本轮入库岗位"
            counts = {**self.counts, **result_counts}
            finished_at = _now()
            if run_id is not None:
                saved_counts = counts.copy()
                saved_counts.pop("ai_greeting_generated", None)
                self.run_store.finish(run_id, status, message, saved_counts, finished_at)
            self.status = status
            self.message = message
            self.counts = counts
            self.finished_at = finished_at
