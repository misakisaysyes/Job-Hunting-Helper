"""Job detail actions exposed by the local web API."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
from urllib.parse import parse_qs, urlsplit

from ai.client import AIRequestError
from ai.scheduler import AITaskScheduler
from browser.browser import ChromeBrowser
from browser.boss_message import inspect_boss_greeting, send_boss_greeting
from data.job_input import load_resume_text
from data.job_store import JobStore
from message import MAX_GREETING_LENGTH, make_greeting_task
from score import make_score_task


class JobActionError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _greeting_text(value: object) -> str:
    if not isinstance(value, str):
        raise JobActionError("招呼语必须是文本")
    text = value.strip()
    if not text or len(text) > MAX_GREETING_LENGTH:
        raise JobActionError(f"招呼语须为 1～{MAX_GREETING_LENGTH} 字")
    return text


def _store(conn: sqlite3.Connection, config: dict) -> JobStore:
    return JobStore(conn, score_threshold=config["ai"]["score_threshold"])


def get_job(db_path: Path, config: dict, platform: str, job_id: str) -> dict:
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        job = _store(conn, config).get_job(platform, job_id)
    if job is None:
        raise JobActionError("岗位不存在", 404)
    return job


def score_job(db_path: Path, config: dict, scheduler: AITaskScheduler,
              platform: str, job_id: str) -> dict:
    job = get_job(db_path, config, platform, job_id)
    if job["job_status"] not in {"scored", "greeting_ready"} \
            or job["greeting_send_state"] != "idle":
        raise JobActionError("已发送或已结束的岗位不能重置评分与流程状态", 409)
    resume_text = load_resume_text(config)
    try:
        task = make_score_task(job, resume_text, config["ai"]["score_user_prompt"])
    except ValueError as exc:
        raise JobActionError(str(exc)) from exc
    try:
        result = scheduler.submit(task).result()
    except Exception as exc:
        error = str(exc) if isinstance(exc, AIRequestError) else type(exc).__name__
        with closing(sqlite3.connect(db_path, timeout=10)) as conn:
            store = _store(conn, config)
            if not store.mark_rescore_failed(platform, job_id, error):
                raise JobActionError("岗位状态已变化，评分失败未写入", 409) from exc
            return {"job": store.get_job(platform, job_id), "error": error}
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        store = _store(conn, config)
        if not store.save_rescore(platform, job_id, score=result.score, reason=result.reason):
            raise JobActionError("岗位状态已变化，新评分未写入", 409)
        return {"job": store.get_job(platform, job_id)}


def open_conversation(db_path: Path, config: dict, platform: str, job_id: str) -> dict:
    job = get_job(db_path, config, platform, job_id)
    if platform != "boss" or job["greeting_send_state"] != "sent":
        raise JobActionError("当前岗位没有已确认发送的 BOSS 会话", 409)
    chat_url = str(job.get("chat_url") or "")
    parsed = urlsplit(chat_url)
    if (parsed.scheme != "https" or parsed.netloc != "www.zhipin.com"
            or parsed.path != "/web/geek/chat"
            or parse_qs(parsed.query).get("jobId") != [job_id]):
        raise JobActionError("岗位缺少有效的 BOSS 会话链接", 409)
    browser = None
    try:
        browser = ChromeBrowser(config["browser"]["cdp_url"])
        if not browser.open_user_tab(chat_url):
            raise JobActionError("无法在采集用 Chrome 打开 BOSS 会话", 502)
    except JobActionError:
        raise
    except Exception as exc:
        raise JobActionError("无法连接采集用 Chrome，请确认远程调试浏览器正在运行", 502) from exc
    finally:
        if browser is not None:
            browser.close()
    return {"message": "已在采集用 Chrome 打开会话"}


def generate_greeting(db_path: Path, config: dict, scheduler: AITaskScheduler,
                      platform: str, job_id: str) -> dict:
    job = get_job(db_path, config, platform, job_id)
    if job["job_status"] != "greeting_ready" or job["greeting_send_state"] != "idle":
        raise JobActionError("当前岗位状态不能生成招呼语", 409)
    if job["ai_score_status"] == "not_scored":
        raise JobActionError("此岗位未启用 AI 评分，请手动编辑招呼语", 409)
    resume_text = load_resume_text(config)
    try:
        task = make_greeting_task(job, resume_text, config["ai"]["greeting_user_prompt"])
    except ValueError as exc:
        raise JobActionError(str(exc)) from exc
    result = scheduler.submit(task).result()
    text = _greeting_text(result.text)
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        store = _store(conn, config)
        if not store.save_greeting(platform, job_id, text):
            raise JobActionError("岗位状态已变化，生成的招呼语未保存", 409)
        return {"greeting": text, "job": store.get_job(platform, job_id)}


def save_greeting(db_path: Path, config: dict, platform: str,
                  job_id: str, text: str) -> dict:
    text = _greeting_text(text)
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        store = _store(conn, config)
        if store.get_job(platform, job_id) is None:
            raise JobActionError("岗位不存在", 404)
        if not store.save_greeting(platform, job_id, text):
            raise JobActionError("岗位状态已变化，招呼语未保存", 409)
        return store.get_job(platform, job_id)


def start_greeting(db_path: Path, config: dict, platform: str, job_id: str) -> dict:
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        store = _store(conn, config)
        if store.get_job(platform, job_id) is None:
            raise JobActionError("岗位不存在", 404)
        if not store.start_greeting(platform, job_id):
            raise JobActionError("岗位状态已变化，无法进入打招呼阶段", 409)
        return store.get_job(platform, job_id)


def send_greeting(db_path: Path, config: dict, platform: str,
                  job_id: str, text: str) -> dict:
    text = _greeting_text(text)
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        store = _store(conn, config)
        job = store.get_job(platform, job_id)
        if job is None:
            raise JobActionError("岗位不存在", 404)
        if not store.reserve_greeting_send(platform, job_id, text):
            raise JobActionError("岗位状态已变化或招呼语正在发送，请刷新后检查", 409)
    # Reserve before touching BOSS to prevent duplicate requests from sending twice.
    try:
        result = send_boss_greeting(job, text, config["browser"]["cdp_url"])
    except Exception as exc:
        # A browser failure can happen after submission; never allow a blind retry.
        note = f"BOSS 发送过程异常：{type(exc).__name__}"
        with closing(sqlite3.connect(db_path, timeout=10)) as conn:
            _store(conn, config).finish_greeting_send(
                platform, job_id, outcome="unknown", note=note)
        raise JobActionError(f"{note}；发送结果未知，请人工核查，系统不会自动重发", 409)
    outcome = "sent" if result.verified else "unknown" if result.uncertain else "failed"
    note = (f"{result.message}；原草稿：{text}"
            if result.actual_greeting and result.actual_greeting != text else result.message)
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        store = _store(conn, config)
        if not store.finish_greeting_send(platform, job_id, outcome=outcome,
                                          chat_url=result.chat_url, note=note,
                                          actual_greeting=result.actual_greeting):
            raise JobActionError("发送结果已返回，但岗位状态保存失败，请人工核查", 500)
        updated = store.get_job(platform, job_id)
    if result.verified and result.actual_greeting:
        raise JobActionError(result.message, 409)
    if not result.verified:
        raise JobActionError(result.message, 409 if result.uncertain else 502)
    return {"job": updated, "message": result.message}


def confirm_greeting_not_sent(db_path: Path, config: dict, platform: str,
                              job_id: str, *, confirmed: bool) -> dict:
    if confirmed is not True:
        raise JobActionError("请先确认 BOSS 会话中没有这条招呼语")
    job = get_job(db_path, config, platform, job_id)
    if job["greeting_send_state"] != "unknown":
        raise JobActionError("仅发送结果未确认的岗位可解除锁定", 409)
    inspection = inspect_boss_greeting(job, job["greeting"], config["browser"]["cdp_url"])
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        store = _store(conn, config)
        if inspection.verified:
            note = (f"{inspection.message}；原草稿：{job['greeting']}"
                    if inspection.actual_greeting
                    and inspection.actual_greeting != job["greeting"] else inspection.message)
            if not store.mark_greeting_sent_after_verification(
                    platform, job_id, inspection.chat_url,
                    actual_greeting=inspection.actual_greeting,
                    note=note):
                raise JobActionError("岗位状态已变化，请刷新后检查", 409)
            return store.get_job(platform, job_id)
        if inspection.uncertain:
            raise JobActionError(f"{inspection.message}；当前不能解除重发锁定", 409)
        if not store.confirm_greeting_not_sent(platform, job_id):
            raise JobActionError("仅发送结果未确认的岗位可解除锁定", 409)
        return store.get_job(platform, job_id)


def force_end(db_path: Path, config: dict, platform: str, job_id: str) -> dict:
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        store = _store(conn, config)
        if store.get_job(platform, job_id) is None:
            raise JobActionError("岗位不存在", 404)
        if not store.force_end(platform, job_id):
            raise JobActionError("当前状态不能强制结束，或招呼语正在发送", 409)
        return store.get_job(platform, job_id)
