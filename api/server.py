"""Serve collected jobs and the React interface on localhost."""

from __future__ import annotations

import argparse
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import sqlite3
import sys
from threading import Event, Lock, Thread
from urllib.parse import unquote, urlsplit, parse_qs
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import DEFAULT_CONFIG, MONITORING_CONFIG
from ai.client import AIRequestError, api_concurrency
from ai.scheduler import AITaskScheduler
from api.collection import CollectionRun
from api.monitoring import MonitoringRun
from api import conversations as conversation_actions
from api import followups as followup_actions
from api import filtering as filter_actions
from api import jobs as job_actions
from api.settings import (MAX_RESUME_BYTES, SettingsError, apply_full_settings,
                          read_basic_settings, read_full_settings, save_basic_settings, save_full_settings,
                          save_resume_upload)
from api.job_search import LIST_STATUSES, SearchSyntaxError, compile_search, matches_list_status
from data.job_store import JobStore
from data.conversation_store import ConversationStore
from data.filter_store import FilterStore
from data.task_run_store import TaskAlreadyRunning, TaskRunStore


def requires_service_restart(saved: dict, started_ai_concurrency: int,
                             db_path: Path, *, state_db_overridden: bool) -> bool:
    return (saved["ai"]["ai_api_concurrency"] != started_ai_concurrency
            or (not state_db_overridden
                and Path(saved["safety"]["state_db"]) != db_path))


def make_handler(db_path: Path, scheduler: AITaskScheduler, *,
                 restart_event: Event, started_ai_concurrency: int,
                 state_db_overridden: bool, server_instance_id: str):
    static_root = (ROOT / "web" / "dist").resolve()
    collection = CollectionRun(db_path, scheduler)
    monitoring = MonitoringRun(db_path)
    task_runs = TaskRunStore(db_path)
    lifecycle_lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            if self.command == "GET" and urlsplit(self.path).path in {
                "/api/collections/current", "/api/monitoring/current",
                "/api/conversations", "/api/tasks/today",
            }:
                return
            super().log_message(format, *args)

        def send_json(self, status: int, value: object) -> None:
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def read_json(self) -> dict | None:
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
                self.send_json(415, {"error": "请使用 application/json 请求"})
                return None
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 100_000:
                    raise ValueError("请求内容长度须在 1～100000 字节")
                value = json.loads(self.rfile.read(length))
                if not isinstance(value, dict):
                    raise ValueError("请求内容须是 JSON 对象")
                return value
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
                return None

        def job_action_route(self) -> tuple[str, str, list[str]] | None:
            parts = urlsplit(self.path).path.split("/")
            if len(parts) < 5 or parts[:3] != ["", "api", "jobs"]:
                return None
            platform, job_id = unquote(parts[3]), unquote(parts[4])
            if not platform or not job_id or "/" in platform or "/" in job_id:
                return None
            return platform, job_id, parts[5:]

        def action_result(self, action) -> None:
            try:
                value = action()
            except job_actions.JobActionError as exc:
                self.send_json(exc.status_code, {"error": str(exc)})
            except AIRequestError as exc:
                self.send_json(502, {"error": str(exc)})
            except FileNotFoundError as exc:
                self.send_json(400, {"error": str(exc)})
            except (sqlite3.Error, OSError, ValueError) as exc:
                self.send_json(500, {"error": f"操作失败：{exc}"})
            except Exception:
                self.send_json(500, {"error": "操作失败，请查看服务日志"})
            else:
                self.send_json(200, value)

        def do_GET(self) -> None:
            request = urlsplit(self.path)
            if request.path == "/api/settings/basic":
                self.send_json(200, read_basic_settings())
                return
            if request.path == "/api/settings/full":
                self.send_json(200, {**read_full_settings(),
                                     "server_instance_id": server_instance_id})
                return
            if request.path == "/api/tasks/today":
                try:
                    summary = task_runs.today()
                except (sqlite3.Error, ValueError) as exc:
                    self.send_json(500, {"error": f"读取任务记录失败：{exc}"})
                    return
                summary["current"] = {
                    "collection": collection.snapshot(),
                    "monitoring": monitoring.snapshot(),
                }
                summary["active"] = {
                    "collection": summary["current"]["collection"]["status"] == "running",
                    "monitoring": summary["current"]["monitoring"]["status"] == "running",
                }
                summary["active"]["any"] = any(summary["active"].values())
                self.send_json(200, summary)
                return
            if request.path == "/api/collections/current":
                self.send_json(200, collection.snapshot())
                return
            if request.path == "/api/monitoring/current":
                self.send_json(200, monitoring.snapshot())
                return
            if request.path == "/api/conversations":
                query = parse_qs(request.query)
                try:
                    limit = int(query.get("limit", ["20"])[0])
                    offset = int(query.get("offset", ["0"])[0])
                except ValueError:
                    self.send_json(400, {"error": "limit 和 offset 必须是整数"})
                    return
                if not 1 <= limit <= 100 or offset < 0:
                    self.send_json(400, {"error": "limit 须在 1～100，offset 不能为负数"})
                    return
                keyword = query.get("query", [""])[0]
                conversation_status = query.get("conversation_status", [""])[0]
                followup_status = query.get("followup_status", [""])[0]
                try:
                    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
                        rows, total = ConversationStore(conn).list_conversations(
                            limit=limit, offset=offset, query=keyword,
                            conversation_status=conversation_status,
                            followup_status=followup_status)
                        followup_actions.annotate_followup_availability(
                            conn, rows, MONITORING_CONFIG)
                except sqlite3.Error:
                    self.send_json(500, {"error": "读取会话数据失败"})
                    return
                self.send_json(200, {"conversations": rows, "total": total,
                                     "limit": limit, "offset": offset})
                return
            if request.path == "/api/filter-candidates":
                query = parse_qs(request.query)
                try:
                    limit = int(query.get("limit", ["20"])[0])
                    offset = int(query.get("offset", ["0"])[0])
                except ValueError:
                    self.send_json(400, {"error": "limit 和 offset 必须是整数"})
                    return
                if not 1 <= limit <= 100 or offset < 0:
                    self.send_json(400, {"error": "limit 须在 1～100，offset 不能为负数"})
                    return
                keyword = query.get("query", [""])[0]
                status = query.get("status", [""])[0]
                try:
                    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
                        rows, total = FilterStore(conn).list_candidates(
                            limit=limit, offset=offset, query=keyword, status=status)
                except sqlite3.Error:
                    self.send_json(500, {"error": "读取过滤候选失败"})
                    return
                self.send_json(200, {"candidates": rows, "total": total,
                                     "limit": limit, "offset": offset})
                return
            if request.path == "/api/jobs":
                query = parse_qs(request.query)
                try:
                    limit = int(query.get("limit", ["20"])[0])
                    offset = int(query.get("offset", ["0"])[0])
                except ValueError:
                    self.send_json(400, {"error": "limit 和 offset 必须是整数"})
                    return
                if not 1 <= limit <= 100 or offset < 0:
                    self.send_json(400, {"error": "limit 须在 1～100，offset 不能为负数"})
                    return
                expression = query.get("query", [""])[0]
                status = query.get("status", [""])[0]
                if status and status not in LIST_STATUSES:
                    self.send_json(400, {"error": "未知岗位状态筛选项"})
                    return
                try:
                    predicate = compile_search(expression)
                except SearchSyntaxError as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                try:
                    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
                        store = JobStore(conn, score_threshold=DEFAULT_CONFIG["ai"]["score_threshold"])
                        if expression.strip() or status:
                            jobs, total = store.search_jobs(
                                limit=limit, offset=offset,
                                matches=lambda job: matches_list_status(job, status) and predicate(job),
                            )
                        else:
                            jobs, total = store.list_jobs(limit=limit, offset=offset)
                except (sqlite3.Error, ValueError, TypeError):
                    self.send_json(500, {"error": "读取岗位数据失败"})
                    return
                self.send_json(200, {
                    "jobs": jobs, "total": total, "limit": limit, "offset": offset,
                    "score_threshold": DEFAULT_CONFIG["ai"]["score_threshold"],
                })
                return

            route = self.job_action_route()
            if route is not None and not route[2]:
                platform, job_id, _ = route
                self.action_result(lambda: job_actions.get_job(
                    db_path, DEFAULT_CONFIG, platform, job_id))
                return

            asset = (static_root / ("index.html" if request.path == "/" else unquote(request.path).lstrip("/"))).resolve()
            if not asset.is_relative_to(static_root) or not asset.is_file():
                self.send_error(404)
                return
            body = asset.read_bytes()
            content_type = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            request = urlsplit(self.path)
            path = request.path
            if path == "/api/settings/full/apply":
                try:
                    with lifecycle_lock:
                        if restart_event.is_set():
                            self.send_json(503, {"error": "服务正在重启，请稍后重试"})
                            return
                        saved = read_full_settings()["settings"]
                        restarting = requires_service_restart(
                            saved, started_ai_concurrency, db_path,
                            state_db_overridden=state_db_overridden)
                        if restarting and (collection.snapshot()["status"] == "running"
                                           or monitoring.snapshot()["status"] == "running"):
                            self.send_json(409, {"error": "采集或监测任务正在运行，请任务结束后再生效需要重启的配置"})
                            return
                        result = apply_full_settings()
                        if restarting:
                            restart_event.set()
                    response = {**result, "restarting": restarting,
                                "server_instance_id": server_instance_id}
                    if restarting:
                        try:
                            self.send_json(200, response)
                            self.wfile.flush()
                        finally:
                            Thread(target=self.server.shutdown, name="config-restart", daemon=True).start()
                    else:
                        self.send_json(200, response)
                except SettingsError as exc:
                    self.send_json(400, {"error": str(exc)})
                except (OSError, ValueError) as exc:
                    self.send_json(500, {"error": f"生效配置失败：{exc}"})
                return
            if restart_event.is_set():
                self.send_json(503, {"error": "服务正在重启，请稍后重试"})
            if path == "/api/settings/resume":
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "text/markdown":
                    self.send_json(415, {"error": "请上传 .md 格式的简历"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                if not 0 < length <= MAX_RESUME_BYTES:
                    self.send_json(400, {"error": "简历文件须为非空且不超过 1 MB 的 .md 文件"})
                    return
                filename = parse_qs(request.query).get("filename", [""])[0]
                try:
                    result = save_resume_upload(filename, self.rfile.read(length))
                except SettingsError as exc:
                    self.send_json(400, {"error": str(exc)})
                except OSError as exc:
                    self.send_json(500, {"error": f"保存简历失败：{exc}"})
                else:
                    self.send_json(200, result)
                return
            if path == "/api/collections":
                if self.read_json() is None:
                    return
                try:
                    with lifecycle_lock:
                        if restart_event.is_set():
                            self.send_json(503, {"error": "服务正在重启，请稍后重试"})
                            return
                        state = collection.start()
                except TaskAlreadyRunning as exc:
                    self.send_json(409, {"error": str(exc)})
                    return
                except ValueError as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                if state is None:
                    self.send_json(409, {"error": "已有采集任务正在运行，请等待任务完成后再启动"})
                    return
                self.send_json(202, state)
                return
            if path == "/api/monitoring":
                if self.read_json() is None:
                    return
                try:
                    with lifecycle_lock:
                        if restart_event.is_set():
                            self.send_json(503, {"error": "服务正在重启，请稍后重试"})
                            return
                        state = monitoring.start()
                except TaskAlreadyRunning as exc:
                    self.send_json(409, {"error": str(exc)})
                    return
                except ValueError as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                if state is None:
                    self.send_json(409, {"error": "已有监测任务正在运行，请等待任务完成后再启动"})
                    return
                self.send_json(202, state)
                return
            parts = path.split("/")
            if (len(parts) == 7 and parts[:3] == ["", "api", "filter-candidates"]
                    and parts[5:] == ["delete", "confirm"]):
                body = self.read_json()
                if body is None:
                    return
                platform, conversation_id = unquote(parts[3]), unquote(parts[4])
                if not platform or not conversation_id or "/" in platform or "/" in conversation_id:
                    self.send_json(400, {"error": "缺少平台或会话 ID"})
                    return
                self.action_result(lambda: filter_actions.delete_candidate(
                    db_path, DEFAULT_CONFIG["browser"]["cdp_url"], platform,
                    conversation_id, DEFAULT_CONFIG["profile"]["blocked_companies"],
                    confirm_still_present=body.get("confirm_still_present") is True))
                return
            if (len(parts) == 7 and parts[:3] == ["", "api", "conversations"]
                    and parts[5:] == ["followup", "send"]):
                if self.read_json() is None:
                    return
                platform, conversation_id = unquote(parts[3]), unquote(parts[4])
                if not platform or not conversation_id or "/" in platform or "/" in conversation_id:
                    self.send_json(400, {"error": "缺少平台或会话 ID"})
                    return
                self.action_result(lambda: followup_actions.send_followup(
                    db_path, DEFAULT_CONFIG["browser"]["cdp_url"], platform, conversation_id))
                return
            if (len(parts) == 7 and parts[:3] == ["", "api", "conversations"]
                    and parts[5:] == ["monitoring", "terminate"]):
                if self.read_json() is None:
                    return
                platform, conversation_id = unquote(parts[3]), unquote(parts[4])
                if not platform or not conversation_id or "/" in platform or "/" in conversation_id:
                    self.send_json(400, {"error": "缺少平台或会话 ID"})
                    return
                self.action_result(lambda: conversation_actions.terminate_monitoring(
                    db_path, platform, conversation_id))
                return
            if len(parts) == 6 and parts[:3] == ["", "api", "conversations"] and parts[5] == "open":
                body = self.read_json()
                if body is None:
                    return
                platform, conversation_id = unquote(parts[3]), unquote(parts[4])
                if not platform or not conversation_id or "/" in platform or "/" in conversation_id:
                    self.send_json(400, {"error": "缺少平台或会话 ID"})
                    return
                self.action_result(lambda: conversation_actions.open_conversation(
                    db_path, DEFAULT_CONFIG, platform, conversation_id))
                return
            if len(parts) == 6 and parts[:3] == ["", "api", "filter-candidates"] and parts[5] == "open":
                if self.read_json() is None:
                    return
                platform, conversation_id = unquote(parts[3]), unquote(parts[4])
                if not platform or not conversation_id or "/" in platform or "/" in conversation_id:
                    self.send_json(400, {"error": "缺少平台或会话 ID"})
                    return
                self.action_result(lambda: conversation_actions.open_filter_candidate(
                    db_path, DEFAULT_CONFIG, platform, conversation_id))
                return
            route = self.job_action_route()
            if route is None:
                self.send_json(404, {"error": "接口不存在"})
                return
            platform, job_id, action = route
            body = self.read_json()
            if body is None:
                return
            if action == ["greeting", "generate"]:
                self.action_result(lambda: job_actions.generate_greeting(
                    db_path, DEFAULT_CONFIG, scheduler, platform, job_id))
            elif action == ["score"]:
                self.action_result(lambda: job_actions.score_job(
                    db_path, DEFAULT_CONFIG, scheduler, platform, job_id))
            elif action == ["greeting", "start"]:
                self.action_result(lambda: {"job": job_actions.start_greeting(
                    db_path, DEFAULT_CONFIG, platform, job_id)})
            elif action == ["greeting", "send"]:
                self.action_result(lambda: job_actions.send_greeting(
                    db_path, DEFAULT_CONFIG, platform, job_id, body.get("greeting", "")))
            elif action == ["greeting", "confirm-not-sent"]:
                self.action_result(lambda: {"job": job_actions.confirm_greeting_not_sent(
                    db_path, DEFAULT_CONFIG, platform, job_id,
                    confirmed=body.get("confirmed") is True)})
            elif action == ["chat", "open"]:
                self.action_result(lambda: job_actions.open_conversation(
                    db_path, DEFAULT_CONFIG, platform, job_id))
            elif action == ["force-end"]:
                self.action_result(lambda: {"job": job_actions.force_end(
                    db_path, DEFAULT_CONFIG, platform, job_id)})
            else:
                self.send_json(404, {"error": "接口不存在"})

        def do_PUT(self) -> None:
            if restart_event.is_set():
                self.send_json(503, {"error": "服务正在重启，请稍后重试"})
                return
            if urlsplit(self.path).path == "/api/settings/full":
                body = self.read_json()
                if body is None:
                    return
                try:
                    self.send_json(200, save_full_settings(body))
                except SettingsError as exc:
                    self.send_json(400, {"error": str(exc)})
                except (OSError, ValueError) as exc:
                    self.send_json(500, {"error": f"保存配置失败：{exc}"})
                return
            if urlsplit(self.path).path == "/api/settings/basic":
                body = self.read_json()
                if body is None:
                    return
                try:
                    self.send_json(200, save_basic_settings(body))
                except SettingsError as exc:
                    self.send_json(400, {"error": str(exc)})
                except (OSError, ValueError) as exc:
                    self.send_json(500, {"error": f"保存配置失败：{exc}"})
                return
            route = self.job_action_route()
            if route is None or route[2] != ["greeting"]:
                self.send_json(404, {"error": "接口不存在"})
                return
            body = self.read_json()
            if body is None:
                return
            platform, job_id, _ = route
            self.action_result(lambda: {"job": job_actions.save_greeting(
                db_path, DEFAULT_CONFIG, platform, job_id, body.get("greeting", ""))})

        def do_DELETE(self) -> None:
            if restart_event.is_set():
                self.send_json(503, {"error": "服务正在重启，请稍后重试"})
                return
            request = urlsplit(self.path)
            parts = request.path.split("/")
            if len(parts) == 5 and parts[:3] in (
                    ["", "api", "conversations"], ["", "api", "filter-candidates"]):
                platform, conversation_id = (unquote(part) for part in parts[3:])
                if not platform or not conversation_id or "/" in platform or "/" in conversation_id:
                    self.send_json(400, {"error": "缺少平台或会话 ID"})
                    return
                if parts[2] == "conversations":
                    self.action_result(lambda: conversation_actions.delete_conversation_record(
                        db_path, platform, conversation_id))
                else:
                    self.action_result(lambda: filter_actions.delete_filter_record(
                        db_path, platform, conversation_id))
                return
            if len(parts) != 5 or parts[:3] != ["", "api", "jobs"]:
                self.send_json(404, {"error": "接口不存在"})
                return
            platform, source_job_id = (unquote(part) for part in parts[3:])
            if not platform or not source_job_id:
                self.send_json(400, {"error": "缺少平台或岗位 ID"})
                return
            try:
                with closing(sqlite3.connect(db_path, timeout=10)) as conn:
                    deleted = JobStore(conn).delete(platform, source_job_id)
            except sqlite3.Error:
                self.send_json(500, {"error": "删除岗位失败"})
                return
            if not deleted:
                self.send_json(404, {"error": "岗位不存在或已删除"})
                return
            self.send_json(200, {"deleted": True})

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="本地岗位数据 API 与 React 页面")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--state-db", type=Path, default=Path(DEFAULT_CONFIG["safety"]["state_db"]))
    state_db_overridden = "--state-db" in sys.argv[1:]
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port 须在 1～65535")
    args.state_db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(args.state_db, timeout=10)) as conn:
        JobStore(conn, score_threshold=DEFAULT_CONFIG["ai"]["score_threshold"]).mark_interrupted_sends_unknown()
        ConversationStore(conn).recover_interrupted_followups()
        FilterStore(conn).recover_interrupted_deletes()
    TaskRunStore(args.state_db).mark_interrupted()
    scheduler = AITaskScheduler(DEFAULT_CONFIG)
    restart_event = Event()
    class RestartableHTTPServer(ThreadingHTTPServer):
        daemon_threads = False

    server = RestartableHTTPServer(("127.0.0.1", args.port), make_handler(
        args.state_db, scheduler, restart_event=restart_event,
        started_ai_concurrency=api_concurrency(DEFAULT_CONFIG),
        state_db_overridden=state_db_overridden,
        server_instance_id=uuid4().hex))
    print(f"岗位页面：http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        scheduler.shutdown()
    if restart_event.is_set():
        os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])


if __name__ == "__main__":
    main()
