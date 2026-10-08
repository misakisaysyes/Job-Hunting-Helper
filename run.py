"""Run a collection session and coordinate optional AI scoring."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, wait
from io import StringIO
import math
from pathlib import Path
from typing import Any
import unittest
from unittest.mock import patch

from ai import AIRequestError, AITaskScheduler
from browser import ChromeBrowser
from collector.models import JobCandidate, PlatformCollectionResult
from collector.orchestrator import CollectionOrchestrator
from collector.platforms.boss import build_boss_request
from collector.safety import open_safety_db
from config import (CITY_CODES, COMPANY_SIZE_FILTERS, DEFAULT_CONFIG,
                    EDUCATION_FILTERS, EXPERIENCE_FILTERS)
from data.job_input import load_resume_text
from data.job_store import JobStore
from message import MAX_GREETING_LENGTH, make_greeting_task
from score import make_score_task


class PrefilterTestFailure(RuntimeError):
    """The requested local prefilter check failed before live collection."""


def validate_run_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate collection and AI settings regardless of the caller."""
    collection, profile = config["collection"], config["profile"]
    if collection["platform"] != "boss":
        raise ValueError(f"当前不支持采集平台：{collection['platform']}")
    for section, defaults in DEFAULT_CONFIG.items():
        for key, default in defaults.items():
            if isinstance(default, list) and not isinstance(config[section][key], list):
                raise ValueError(f"config.py 中 {section}.{key} 必须是列表")
    expect_ids = collection["encrypt_expect_id"]
    if any(not isinstance(value, str) or not value.strip() or "," in value for value in expect_ids):
        raise ValueError("collection.encrypt_expect_id 必须是非空 ID 组成的列表，每项只写一个 ID")
    collection["encrypt_expect_id"] = list(dict.fromkeys(value.strip() for value in expect_ids))
    for key, choices in (("education", EDUCATION_FILTERS),
                         ("company_sizes", COMPANY_SIZE_FILTERS),
                         ("experience_filters", EXPERIENCE_FILTERS)):
        invalid = [value for value in profile[key] if value not in choices]
        if invalid:
            raise ValueError(f"config.py 中 profile.{key} 含无效选项：{invalid}")
    if any(value not in {"experienced", "campus", "internship"}
           for value in profile["recruitment_types"]):
        raise ValueError("profile.recruitment_types 只能包含 experienced、campus、internship")
    if collection["mode"] not in {"recommend", "search"}:
        raise ValueError("需要通过 --mode 或 config.py 的 collection.mode 指定 recommend 或 search")
    if collection["sort"] not in {"default", "newest"}:
        raise ValueError("collection.sort 只能是 default 或 newest")
    if collection["mode"] == "search" and not collection["keywords"]:
        raise ValueError("--mode search 需要至少一个 --keyword")
    if collection["mode"] == "search" and collection["encrypt_expect_id"]:
        raise ValueError("--mode search 不使用 --encrypt-expect-id")
    if collection["mode"] == "recommend" and collection["keywords"]:
        raise ValueError("--mode recommend 不接受 --keyword；请使用 --mode search")
    if collection["mode"] == "recommend" and not collection["encrypt_expect_id"]:
        raise ValueError("--mode recommend 需要非空的 --encrypt-expect-id")
    cities = collection["cities"]
    invalid_cities = [city for city in cities if not isinstance(city, str) or city not in CITY_CODES]
    if invalid_cities:
        raise ValueError(f"collection.cities 含未收录城市：{invalid_cities}")
    collection["cities"] = list(dict.fromkeys(cities))
    if collection["city_code"] and not collection["city"]:
        raise ValueError("--city-code 需要同时提供 --city")
    city_code = (collection["city_code"] or CITY_CODES.get(collection["city"])) if collection["city"] else None
    if collection["city"] and not city_code:
        raise ValueError(f"未收录城市 {collection['city']}；请提供 --city-code")
    if not 1 <= collection["max_pages"] <= 10 or collection["max_jobs"] < 0:
        raise ValueError("--max-pages 必须在 1～10，--max-jobs 不能为负")
    if (type(collection["target_jobs"]) is not int or collection["target_jobs"] < 1):
        raise ValueError("collection.target_jobs 必须是正整数")
    if profile["salary_min"] < 0 or profile["salary_max"] < 0:
        raise ValueError("--salary-min 和 --salary-max 不能为负")
    if profile["salary_max"] > 0 and profile["salary_min"] > profile["salary_max"]:
        raise ValueError("期望薪资下限不能高于上限")
    for section, keys in (("collection", ("daily_search_page_limit", "daily_detail_page_limit",
                                        "max_consecutive_page_failures", "risk_pause_min_minutes",
                                        "risk_pause_max_minutes")),
                          ("safety", ("daily_platform_page_limit", "risk_lock_minutes")),
                          ("ai", ("thinking_budget", "timeout_seconds", "ai_api_concurrency"))):
        for key in keys:
            if config[section][key] <= 0:
                raise ValueError(f"{section}.{key} 必须大于 0")
    if collection["risk_pause_max_minutes"] < collection["risk_pause_min_minutes"]:
        raise ValueError("risk_pause_max_minutes 不能小于 risk_pause_min_minutes")
    if not 1 <= collection["collection_delay_multiplier"] <= 5:
        raise ValueError("collection_delay_multiplier 必须在 1～5")
    if collection["boss_salary_decode_failure"] not in {"stop", "skip_job"}:
        raise ValueError("boss_salary_decode_failure 只能是 stop 或 skip_job")
    if config["ai"]["thinking"] not in {"auto", "enabled", "disabled"}:
        raise ValueError("ai.thinking 只能是 auto、enabled 或 disabled")
    if not isinstance(config["ai"]["use_ai_score"], bool):
        raise ValueError("ai.use_ai_score 必须是布尔值")
    if not isinstance(config["ai"]["use_ai_greeting"], bool):
        raise ValueError("ai.use_ai_greeting 必须是布尔值")
    if config["ai"]["use_ai_greeting"] and not config["ai"]["use_ai_score"]:
        raise ValueError("启用 ai.use_ai_greeting 时必须同时启用 ai.use_ai_score")
    if not isinstance(config["ai"]["score_user_prompt"], str):
        raise ValueError("ai.score_user_prompt 必须是字符串")
    if not isinstance(config["ai"]["greeting_user_prompt"], str):
        raise ValueError("ai.greeting_user_prompt 必须是字符串")
    template = config["ai"]["greeting_template"]
    if not isinstance(template, str) or len(template.strip()) > MAX_GREETING_LENGTH:
        raise ValueError(f"ai.greeting_template 必须是不超过 {MAX_GREETING_LENGTH} 字的文本")
    score_threshold = config["ai"]["score_threshold"]
    if (isinstance(score_threshold, bool) or not isinstance(score_threshold, (int, float))
            or not math.isfinite(score_threshold) or not 0 <= score_threshold <= 100):
        raise ValueError("ai.score_threshold 必须是 0～100 的数字")
    retry_count = config["ai"]["retry_count"]
    if isinstance(retry_count, bool) or not isinstance(retry_count, int) or retry_count < 0:
        raise ValueError("ai.retry_count 必须是非负整数")
    retry_min = config["ai"]["retry_delay_min_seconds"]
    retry_max = config["ai"]["retry_delay_max_seconds"]
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(value) or value < 0
           for value in (retry_min, retry_max)) or retry_max < retry_min:
        raise ValueError("AI 重试等待范围必须是非负数，且上限不能低于下限")
    return config


def _check_prefilter(on_event: Callable[[str], None] | None) -> None:
    output = StringIO()
    # These regression cases expect an unfiltered starting profile. Keep the
    # user's configured filters for the collection that follows this check.
    with patch.dict(DEFAULT_CONFIG, {"profile": {}}):
        suite = unittest.defaultTestLoader.discover(
            str(Path(__file__).resolve().parent / "test"), pattern="test_prefilter.py",
        )
        result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
    report = output.getvalue().rstrip()
    if not result.wasSuccessful():
        raise PrefilterTestFailure(report or "本地预筛用例未通过")
    if on_event is not None:
        on_event(report)
        on_event("本地用例通过，开始连接 Chrome 验证真实岗位")


def run_collection(
    config: dict[str, Any],
    *,
    on_record: Callable[[dict[str, Any]], None],
    on_event: Callable[[str], None] | None = None,
    on_progress: Callable[[dict[str, int]], None] | None = None,
    ai_scheduler: AITaskScheduler | None = None,
) -> PlatformCollectionResult:
    """Run the configured platform collection session and optional AI scoring."""
    validate_run_config(config)
    if config["collection"]["test_prefilter"]:
        _check_prefilter(on_event)
    request = build_boss_request(config)
    conn = open_safety_db(Path(config["safety"]["state_db"]))
    try:
        job_store = JobStore(conn, score_threshold=config["ai"]["score_threshold"])

        def emit_record(record: dict[str, Any]) -> None:
            job_store.save(record)
            on_record(record)

        browser = ChromeBrowser(config["browser"]["cdp_url"])
        try:
            max_jobs = config["collection"]["max_jobs"]
            target_jobs = config["collection"]["target_jobs"]
            progress_counts = {"qualified": 0, "target": target_jobs}
            if config["ai"]["use_ai_score"]:
                progress_counts.update(ai_scored=0, ai_score_failed=0)

            def report_counts(counts: dict[str, int] | None = None) -> None:
                if counts is not None:
                    progress_counts.update(counts)
                if on_progress is not None:
                    on_progress(progress_counts.copy())

            if not config["ai"]["use_ai_score"]:
                qualified = 0

                def save_plain(job: JobCandidate) -> None:
                    nonlocal qualified
                    record = job.as_job_record()
                    template = config["ai"]["greeting_template"].strip()
                    if template:
                        record["greeting"] = template
                    emit_record(record)
                    qualified += 1
                    progress_counts["qualified"] = qualified
                    report_counts()
                    if on_event is not None:
                        on_event(f"本轮目标进度 {qualified}/{target_jobs}")

                runner = CollectionOrchestrator(
                    browser=browser, config=config, safety_conn=conn,
                    already_collected=job_store.contains,
                    target_reached=lambda: qualified >= target_jobs,
                    on_counts=report_counts,
                )
                result = runner.run(
                    request, on_job=save_plain,
                    on_event=on_event, max_jobs=max_jobs,
                )
                if result.reason_code:
                    result.counts.update(qualified=qualified, target=target_jobs)
                if result.reason_code == "target_not_reached":
                    result.message = f"本轮目标未达成：{qualified}/{target_jobs}；来源组合未发现新岗位"
                return result

            resume_text = load_resume_text(config)
            scheduler = ai_scheduler if ai_scheduler is not None else AITaskScheduler(config)
            score_jobs = []
            greeting_jobs = []
            scored = failed = greeting_generated = greeting_failed = qualified = 0

            def finish_score(job: JobCandidate, future: object) -> None:
                nonlocal scored, failed, greeting_failed, qualified
                record = job.as_job_record()
                try:
                    score_result = future.result()
                except Exception as exc:
                    failed += 1
                    record.update(ai_score_status="score_failed", ai_score=None,
                                  ai_score_reason="", ai_score_error=(
                                      str(exc) if isinstance(exc, AIRequestError) else type(exc).__name__
                                  ))
                else:
                    scored += 1
                    record.update(ai_score_status="scored", ai_score=score_result.score,
                                  ai_score_reason=score_result.reason, ai_score_error="")
                eligible = (record.get("ai_score_status") == "scored"
                            and record["ai_score"] >= config["ai"]["score_threshold"])
                if eligible:
                    qualified += 1
                    if on_event is not None:
                        on_event(f"本轮目标进度 {qualified}/{target_jobs}")
                progress_counts.update(qualified=qualified, ai_scored=scored,
                                       ai_score_failed=failed)
                report_counts()
                if eligible and config["ai"]["use_ai_greeting"]:
                    try:
                        task = make_greeting_task(
                            record, resume_text, config["ai"]["greeting_user_prompt"])
                        greeting_jobs.append((record, scheduler.submit(task)))
                    except Exception as exc:
                        greeting_failed += 1
                        record.update(ai_greeting_status="failed", ai_greeting_error=(
                            str(exc) if isinstance(exc, (AIRequestError, ValueError))
                            else type(exc).__name__
                        ))
                        emit_record(record)
                else:
                    template = config["ai"]["greeting_template"].strip()
                    if eligible and not config["ai"]["use_ai_greeting"] and template:
                        record["greeting"] = template
                    emit_record(record)

            def drain_scores(wait_for_all: bool = False) -> None:
                ready = [(job, future) for job, future in score_jobs
                         if wait_for_all or future.done()]
                if not ready:
                    return
                ready_futures = {future for _, future in ready}
                score_jobs[:] = [(job, future) for job, future in score_jobs
                                 if future not in ready_futures]
                for job, future in ready:
                    finish_score(job, future)

            def submit_score(job: JobCandidate) -> None:
                task = make_score_task(job, resume_text, config["ai"]["score_user_prompt"])
                future = scheduler.submit(task)
                score_jobs.append((job, future))
                drain_scores()
                pending_limit = max(4, config["ai"]["ai_api_concurrency"] * 2)
                pending_limit = min(pending_limit, max(1, target_jobs - qualified))
                if len(score_jobs) >= pending_limit:
                    wait([pending for _, pending in score_jobs], return_when=FIRST_COMPLETED)
                    drain_scores()

            def target_reached() -> bool:
                drain_scores()
                return qualified >= target_jobs

            try:
                runner = CollectionOrchestrator(
                    browser=browser, config=config, safety_conn=conn,
                    already_collected=job_store.contains,
                    target_reached=target_reached,
                    on_cycle_complete=lambda: drain_scores(wait_for_all=True),
                    on_counts=report_counts,
                )
                result = runner.run(
                    request, on_job=lambda job: None, on_prefilter_pass=submit_score,
                    on_event=on_event, max_jobs=max_jobs,
                )
                drain_scores(wait_for_all=True)

                if greeting_jobs and on_event is not None:
                    on_event(f"正在生成 {len(greeting_jobs)} 条招呼语…")
                for record, future in greeting_jobs:
                    try:
                        greeting_result = future.result()
                    except Exception as exc:
                        greeting_failed += 1
                        record.update(ai_greeting_status="failed", ai_greeting_error=(
                            str(exc) if isinstance(exc, AIRequestError) else type(exc).__name__
                        ))
                    else:
                        greeting_generated += 1
                        record.update(greeting=greeting_result.text,
                                      ai_greeting_status="generated", ai_greeting_error="")
                    emit_record(record)
                result.counts.update(ai_scored=scored, ai_score_failed=failed)
                if result.reason_code:
                    result.counts.update(qualified=qualified, target=target_jobs)
                if result.reason_code == "target_not_reached":
                    result.message = f"本轮目标未达成：{qualified}/{target_jobs}；来源组合未发现新岗位"
                if config["ai"]["use_ai_greeting"]:
                    result.counts.update(ai_greeting_generated=greeting_generated,
                                         ai_greeting_failed=greeting_failed)
                return result
            finally:
                if ai_scheduler is None:
                    scheduler.shutdown()
        finally:
            browser.close()
    finally:
        conn.close()
