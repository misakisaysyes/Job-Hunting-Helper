"""Run confirmed monitoring batches and persist their verified progress."""

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread

from api.filtering import delete_candidate
from api.followups import FollowupSkipped, send_followup
from api.jobs import JobActionError
from config import DEFAULT_CONFIG, MONITORING_CONFIG
from data.task_run_store import TASK_LABELS, TaskRunStore

BATCH_TYPES = {"followup": "monitoring_followup", "delete": "monitoring_delete"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MonitoringBatchRun:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.run_store = TaskRunStore(db_path)

    def snapshots(self) -> dict:
        return {action: self.run_store.latest(task_type) for action, task_type in BATCH_TYPES.items()}

    def get(self, run_id: int) -> dict:
        state = self.run_store.get_run(run_id)
        if state is None or state["task_type"] not in BATCH_TYPES.values():
            raise JobActionError("批量任务不存在", 404)
        return state

    def start(self, action: object, items: object) -> dict:
        if not isinstance(action, str) or action not in BATCH_TYPES:
            raise JobActionError("批量任务只支持追问或删除会话", 400)
        if not isinstance(items, list) or not 1 <= len(items) <= 100:
            raise JobActionError("批量任务须包含 1～100 条会话", 400)
        seen = set()
        for item in items:
            if (not isinstance(item, dict) or item.get("platform") != "boss"
                    or not isinstance(item.get("conversation_id"), str)
                    or not item["conversation_id"].isdigit()):
                raise JobActionError("批量任务须提供有效的 BOSS 会话 ID", 400)
            if item["conversation_id"] in seen:
                raise JobActionError("批量任务不能重复选择同一会话", 400)
            seen.add(item["conversation_id"])
            if action == "followup" and (
                    not isinstance(item.get("text"), str) or not 1 <= len(item["text"].strip()) <= 300
                    or not isinstance(item.get("anchor_id"), str) or not item["anchor_id"].isdigit()
                    or not isinstance(item.get("expected_text"), str)):
                raise JobActionError("批量追问须提供有效的追问语及草稿依据", 400)
        task_type = BATCH_TYPES[action]
        config = deepcopy(DEFAULT_CONFIG)
        config["monitoring"] = deepcopy(MONITORING_CONFIG)
        counts = {"total": len(items), "completed": 0, "processed": 0, "failed": 0, "skipped": 0}
        run_id = self.run_store.start(task_type, _now(), f"正在{TASK_LABELS[task_type]}", counts=counts)
        state = self.get(run_id)
        try:
            Thread(target=self._execute, args=(run_id, action, deepcopy(items), config),
                   name=f"monitoring-batch-{action}", daemon=True).start()
        except Exception:
            self.run_store.finish(run_id, "failed", "后台任务启动失败，请重试", counts, _now())
            raise JobActionError("后台任务启动失败，请重试", 500)
        return state

    def _execute(self, run_id: int, action: str, items: list[dict], config: dict) -> None:
        counts = {"total": len(items), "completed": 0, "processed": 0, "failed": 0, "skipped": 0}
        label = TASK_LABELS[BATCH_TYPES[action]]
        failure = ""
        skip_notes = []
        try:
            for item in items:
                if self.get(run_id)["status"] != "running":
                    return
                try:
                    if action == "followup":
                        send_followup(self.db_path, config["browser"]["cdp_url"], "boss", item["conversation_id"],
                            text=item["text"], anchor_id=item["anchor_id"], expected_text=item["expected_text"],
                            max_count=config["monitoring"]["followup_max_count"])
                    else:
                        delete_candidate(self.db_path, config["browser"]["cdp_url"], "boss", item["conversation_id"],
                            config["profile"]["blocked_companies"],
                            confirm_still_present=item.get("confirm_still_present") is True)
                except FollowupSkipped as exc:
                    counts["skipped"] += 1
                    skip_notes.append(f"跳过会话 {item['conversation_id']}：{str(exc)[:400]}")
                except Exception as exc:
                    counts["failed"] += 1
                    failure = f"会话 {item['conversation_id']}：{str(exc)[:400] or type(exc).__name__}"
                else:
                    counts["completed"] += 1
                counts["processed"] += 1
                progress = f"{label}：总数 {counts['total']}，已完成 {counts['completed']}，跳过 {counts['skipped']}"
                if skip_notes:
                    progress += "。" + "；".join(skip_notes)
                self.run_store.update_counts(run_id, counts, message=progress)
                if failure:
                    break
        except Exception as exc:
            failure = str(exc)[:400] or type(exc).__name__
        counts["skipped"] += counts["total"] - counts["processed"]
        status = ("completed_with_shortage" if counts["completed"] else "failed") if failure else "completed"
        message = f"{label}完成：成功 {counts['completed']}，跳过 {counts['skipped']}，失败 {counts['failed']}。"
        details = ([failure] if failure else []) + skip_notes
        message += "；".join(details)
        self.run_store.finish(run_id, status, message, counts, _now())
