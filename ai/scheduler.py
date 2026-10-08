"""A shared worker pool for every kind of AI task."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from typing import Generic, TypeVar
from uuid import uuid4

from ai.client import api_concurrency, call_model


ResultT = TypeVar("ResultT")
ModelCall = Callable[[str, str], str]


@dataclass(frozen=True)
class AITask(Generic[ResultT]):
    """A single unit of work for the AI scheduler."""

    id: str
    kind: str
    job_id: str
    run: Callable[[ModelCall], ResultT]


def wrap_ai_task(kind: str, job_id: str, run: Callable[[ModelCall], ResultT]) -> AITask[ResultT]:
    """Wrap a callable as a task; new task types use this same function."""
    if not kind.strip() or not job_id.strip() or not callable(run):
        raise ValueError("AI 任务需要非空类型、岗位 ID 和可调用的处理函数")
    return AITask(id=uuid4().hex, kind=kind, job_id=job_id, run=run)


class AITaskScheduler:
    """Run at most ai_api_concurrency tasks; the executor queues the rest."""

    def __init__(self, config: dict, *, model_call: ModelCall | None = None) -> None:
        self._model_call = model_call if model_call is not None else partial(call_model, config)
        self._executor = ThreadPoolExecutor(
            max_workers=api_concurrency(config), thread_name_prefix="job-helper-ai",
        )

    def submit(self, task: AITask[ResultT]) -> Future[ResultT]:
        if not isinstance(task, AITask):
            raise TypeError("调度单元必须是 AITask")
        return self._executor.submit(task.run, self._model_call)

    def shutdown(self) -> None:
        """Wait for running and queued tasks to finish."""
        self._executor.shutdown(wait=True)

    def __enter__(self) -> AITaskScheduler:
        return self

    def __exit__(self, *_: object) -> None:
        self.shutdown()
