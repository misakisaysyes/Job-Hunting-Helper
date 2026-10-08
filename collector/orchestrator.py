"""Coordinate BOSS job collection and callbacks."""

from __future__ import annotations

from collections.abc import Callable
from threading import Event

from collector.base import CollectorHooks
from collector.models import JobCandidate, PlatformCollectionRequest, PlatformCollectionResult
from collector.platforms.boss import BossCollector


class CollectionOrchestrator:
    def __init__(self, *, browser: object, config: dict, safety_conn: object | None = None,
                 already_collected: Callable[[str, str], bool] | None = None,
                 target_reached: Callable[[], bool] | None = None,
                 on_cycle_complete: Callable[[], None] | None = None) -> None:
        self.collector = BossCollector(browser=browser, config=config, safety_conn=safety_conn)
        self.already_collected = already_collected or (lambda platform, job_id: False)
        self.target_reached = target_reached or (lambda: False)
        self.on_cycle_complete = on_cycle_complete or (lambda: None)
        self.target_count = config.get("collection", {}).get("target_jobs", 0) if target_reached else 0

    def run(self, request: PlatformCollectionRequest, *,
            on_job: Callable[[JobCandidate], None],
            on_prefilter_pass: Callable[[JobCandidate], None] | None = None,
            on_event: Callable[[str], None] | None = None,
            stop_event: Event | None = None,
            max_jobs: int = 0) -> PlatformCollectionResult:
        if request.platform != "boss":
            raise ValueError(f"未接入采集平台：{request.platform}")
        if not 1 <= request.max_pages <= 10:
            raise ValueError("max_pages 必须在 1 到 10 之间")
        if max_jobs < 0:
            raise ValueError("max_jobs 不能为负数")

        seen_ids: set[str] = set()
        jobs: list[JobCandidate] = []
        counts = {"seen": 0, "new": 0, "duplicate": 0, "filtered": 0, "parse_failed": 0}

        def emit(message: str) -> None:
            if message and on_event is not None:
                on_event(message)

        def inspect(candidate: JobCandidate) -> bool:
            counts["seen"] += 1
            key = candidate.source_job_id
            if key in seen_ids or self.already_collected(candidate.platform, key):
                counts["duplicate"] += 1
                return False
            seen_ids.add(key)
            return True

        def save(candidate: JobCandidate) -> bool:
            jobs.append(candidate)
            counts["new"] += 1
            on_job(candidate)
            return max_jobs == 0 or len(jobs) < max_jobs

        def parse_failed(reason: str) -> None:
            counts["parse_failed"] += 1
            emit(f"解析失败：{reason}")

        def event(**values: object) -> None:
            if values.get("increment_filtered"):
                counts["filtered"] += 1
            message = values.get("message")
            if message:
                emit(str(message))

        hooks = CollectorHooks(
            stop_event=stop_event,
            on_list_candidate=inspect,
            on_candidate=save,
            on_parse_failed=parse_failed,
            on_event=event,
            on_prefilter_pass=on_prefilter_pass or (lambda job: None),
            target_reached=self.target_reached,
            on_cycle_complete=self.on_cycle_complete,
            target_count=self.target_count,
        )
        result = self.collector.collect(request, hooks)
        result.new_job_ids = [job.storage_id for job in jobs]
        result.counts = counts
        return result
