"""Scan BOSS conversations and optionally prepare or send follow-ups."""

from __future__ import annotations

from copy import deepcopy
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from threading import Lock, Thread
from uuid import uuid4

from browser.boss_monitor import scan_recent_conversations, scan_new_greetings
from browser.boss_monitor import _effective
from api.followups import proposal
from api.filtering import delete_candidate, matched_company_term
from config import DEFAULT_CONFIG, MONITORING_CONFIG
from data.conversation_store import ConversationStore
from data.filter_store import FilterStore
from data.job_store import JobStore
from data.task_run_store import TaskAlreadyRunning, TaskRunStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _initial_phases() -> dict[str, dict[str, str]]:
    return {
        "communicated": {"status": "pending", "message": "等待扫描「仅沟通」会话"},
        "new_greetings": {"status": "pending", "message": "等待扫描「新招呼」会话"},
    }


class MonitoringRun:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.run_store = TaskRunStore(db_path)
        self.lock = Lock()
        self.status = "idle"
        self.message = "尚未开始"
        self.counts: dict[str, int] = {}
        self.phases = _initial_phases()
        self._active_phase: str | None = None
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.run_id: int | None = None

    def snapshot(self) -> dict:
        with self.lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> dict:
        config = MONITORING_CONFIG
        return {
            "status": self.status,
            "message": self.message,
            "counts": self.counts.copy(),
            "phases": {key: value.copy() for key, value in self.phases.items()},
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "run_id": self.run_id,
            "message_limit": config["message_limit"],
            "followup_days": config["followup_days"],
            "followup_enabled": config["followup_enabled"],
            "filter_review_required": config["filter_review_required"],
        }

    def start(self) -> dict | None:
        with self.lock:
            if self.status == "running":
                return None
            active_task = self.run_store.active_task_type()
            if active_task:
                raise TaskAlreadyRunning(active_task)
            config = deepcopy(MONITORING_CONFIG)
            for key in ("message_limit", "followup_days",
                        "followup_cooldown_hours", "followup_max_count"):
                if type(config.get(key)) is not int or config[key] < 1:
                    raise ValueError(f"monitoring.{key} 须为正整数")
            started_at = _now()
            message = "正在连接 Chrome 并读取最近会话…"
            run_id = self.run_store.start("monitoring", started_at, message)
            self.status = "running"
            self.message = message
            self.counts = {}
            self.phases = _initial_phases()
            self._active_phase = None
            self.started_at = started_at
            self.finished_at = None
            self.run_id = run_id
            state = self._snapshot_locked()
        blocked_terms = deepcopy(DEFAULT_CONFIG["profile"]["blocked_companies"])
        Thread(target=self._execute, args=(config, run_id, blocked_terms),
               name="web-monitoring", daemon=True).start()
        return state

    def _progress(self, message: str) -> None:
        with self.lock:
            self.message = message
            if self._active_phase:
                self.phases[self._active_phase]["message"] = message

    def _phase_progress(self, phase: str, status: str, message: str,
                        counts: dict[str, int] | None = None) -> None:
        with self.lock:
            self.phases[phase] = {"status": status, "message": message}
            self._active_phase = phase if status in {"scanning", "processing"} else None
            self.message = message
            if counts is not None:
                self.counts = counts.copy()

    def _execute(self, config: dict | None = None, run_id: int | None = None,
                 blocked_terms: list[str] | None = None) -> None:
        try:
            with self.lock:
                self.phases = _initial_phases()
                self.counts = {}
            self._phase_progress("communicated", "scanning", "正在扫描「仅沟通」会话列表…")
            if config is None:
                config = deepcopy(MONITORING_CONFIG)
            if blocked_terms is None:
                blocked_terms = deepcopy(DEFAULT_CONFIG["profile"]["blocked_companies"])
            with closing(sqlite3.connect(self.db_path, timeout=10)) as conn:
                skip_conversation_ids = ConversationStore(conn).terminated_ids("boss")
            conversations = scan_recent_conversations(
                DEFAULT_CONFIG["browser"]["cdp_url"],
                message_limit=config["message_limit"],
                active_days=config["followup_days"],
                skip_conversation_ids=skip_conversation_ids,
                on_progress=self._progress,
            )
            self._phase_progress("communicated", "processing",
                                 f"已读取 {len(conversations)} 条待监测会话，等待入库")
            self._phase_progress("new_greetings", "scanning", "正在扫描「新招呼」会话列表…")
            new_greetings = scan_new_greetings(
                DEFAULT_CONFIG["browser"]["cdp_url"], on_progress=self._progress)
            counts = {"scanned": len(conversations), "saved": 0, "left_scope": 0,
                      "in_job_pool": 0, "outside_job_pool": 0,
                      "unread": 0, "read_no_reply": 0,
                      "followup_pending": 0, "followup_sent": 0,
                      "followup_failed": 0, "new_greetings_scanned": len(new_greetings),
                      "filter_matches": 0, "filter_pending": 0,
                      "filter_deleted": 0, "filter_failed": 0, "filter_stale": 0}
            self._phase_progress("new_greetings", "processing",
                                 f"已扫描 {len(new_greetings)} 条新招呼，等待匹配排除词", counts)
            auto_delete: list[str] = []
            scan_token = uuid4().hex
            with closing(sqlite3.connect(self.db_path, timeout=10)) as conn:
                conversations_store = ConversationStore(conn)
                filters = FilterStore(conn)
                jobs = JobStore(conn, score_threshold=DEFAULT_CONFIG["ai"]["score_threshold"])
                self._phase_progress("communicated", "processing",
                                     f"正在处理已沟通会话 0/{len(conversations)}", counts)
                for index, conversation in enumerate(conversations, start=1):
                    source_job_id = conversation["source_job_id"]
                    job = jobs.get_job("boss", source_job_id) if source_job_id else None
                    conversation["in_job_pool"] = job is not None
                    counts["in_job_pool" if job else "outside_job_pool"] += 1
                    if job is not None:
                        conversation["job_title"] = conversation["job_title"] or job.get("title", "")
                        conversation["company"] = job.get("company") or conversation["company"]
                    if not conversations_store.upsert(conversation, conversation["messages"],
                                                      scan_token=scan_token):
                        self._phase_progress("communicated", "processing",
                                             f"正在处理已沟通会话 {index}/{len(conversations)}", counts)
                        continue
                    counts["saved"] += 1
                    saved = conversations_store.get_conversation("boss", conversation["conversation_id"])
                    if saved is None:
                        # The user may terminate this conversation while the scan runs.
                        self._phase_progress("communicated", "processing",
                                             f"正在处理已沟通会话 {index}/{len(conversations)}", counts)
                        continue
                    if saved["followup_status"] == "pending_review":
                        latest = next((message for message in reversed(conversation["messages"])
                                       if _effective(message)), None)
                        if (saved["judgment"] not in {"unread", "read_no_reply"}
                                or not config["followup_enabled"]
                                or (saved["judgment"] == "unread" and not config["followup_unread"])
                                or (saved["judgment"] == "read_no_reply" and not config["followup_read_no_reply"])
                                or latest is None
                                or latest.get("message_id") != saved["followup_anchor_id"]):
                            conversations_store.invalidate_followup("boss", conversation["conversation_id"])
                            saved = conversations_store.get_conversation("boss", conversation["conversation_id"])
                    if saved["followup_status"] == "pending_review":
                        counts["followup_pending"] += 1
                    candidate = proposal(saved, conversation["messages"], job, config)
                    if candidate is not None:
                        text, anchor_id, observed_count = candidate
                        conversations_store.reconcile_followup_count(
                            "boss", conversation["conversation_id"], observed_count)
                        if conversations_store.prepare_followup(
                                "boss", conversation["conversation_id"], text, anchor_id):
                            counts["followup_pending"] += 1
                    judgment = saved["judgment"]
                    counts[judgment] += 1
                    self._phase_progress("communicated", "processing",
                                         f"正在处理已沟通会话 {index}/{len(conversations)}", counts)
                counts["left_scope"] = conversations_store.deactivate_unseen(scan_token)
                self._phase_progress("communicated", "completed",
                                     f"已处理 {len(conversations)} 条待监测会话，入库或更新 {counts['saved']} 条", counts)
                filter_scan_token = uuid4().hex
                self._phase_progress("new_greetings", "processing",
                                     f"正在匹配新招呼 0/{len(new_greetings)}", counts)
                for index, greeting in enumerate(new_greetings, start=1):
                    term = matched_company_term(greeting["company"], blocked_terms)
                    if not term:
                        self._phase_progress("new_greetings", "processing",
                                             f"正在匹配新招呼 {index}/{len(new_greetings)}", counts)
                        continue
                    counts["filter_matches"] += 1
                    filters.record_match(greeting, term, filter_scan_token)
                    candidate = filters.get_candidate("boss", greeting["conversation_id"])
                    if candidate["status"] == "pending_review":
                        counts["filter_pending"] += 1
                        if not config["filter_review_required"]:
                            auto_delete.append(greeting["conversation_id"])
                    self._phase_progress("new_greetings", "processing",
                                         f"正在匹配新招呼 {index}/{len(new_greetings)}", counts)
                counts["filter_stale"] = filters.retire_unseen(filter_scan_token)
                self._phase_progress("new_greetings", "processing",
                                     f"已匹配 {len(new_greetings)} 条新招呼", counts)
            for index, conversation_id in enumerate(auto_delete, start=1):
                self._progress(f"正在删除命中排除词的新招呼 {index}/{len(auto_delete)}")
                try:
                    delete_candidate(self.db_path, DEFAULT_CONFIG["browser"]["cdp_url"],
                                     "boss", conversation_id, blocked_terms)
                except Exception:
                    counts["filter_failed"] += 1
                else:
                    counts["filter_deleted"] += 1
                self._phase_progress("new_greetings", "processing",
                                     f"正在处理命中排除词的新招呼 {index}/{len(auto_delete)}", counts)
            self._phase_progress("new_greetings", "completed",
                                 f"已扫描 {len(new_greetings)} 条新招呼，命中排除词 {counts['filter_matches']} 条", counts)
        except Exception as exc:
            detail = str(exc).strip()
            with self.lock:
                self.status = "failed"
                self.message = f"{type(exc).__name__}: {detail[-450:]}" if detail else type(exc).__name__
                if self._active_phase:
                    self.phases[self._active_phase] = {
                        "status": "failed", "message": self.message,
                    }
                for phase in self.phases.values():
                    if phase["status"] in {"scanning", "processing"}:
                        phase.update(status="skipped", message="本轮未完成")
                self._active_phase = None
                self.finished_at = _now()
        else:
            with self.lock:
                self.status = "completed"
                self.message = (f"已扫描 {counts['scanned']} 条会话，记录 {counts['saved']} 条；"
                                f"追问语待发送 {counts['followup_pending']} 条；"
                                f"新招呼 {counts['new_greetings_scanned']} 条，命中排除词 {counts['filter_matches']} 条，"
                                f"待审核删除 {counts['filter_pending'] - counts['filter_deleted'] - counts['filter_failed']} 条，"
                                f"已删除 {counts['filter_deleted']} 条，未确认 {counts['filter_failed']} 条")
                self.counts = counts
                self._active_phase = None
                self.finished_at = _now()
        if run_id is not None:
            with self.lock:
                status, message = self.status, self.message
                counts, finished_at = self.counts.copy(), self.finished_at
            self.run_store.finish(run_id, status, message, counts, finished_at)
