"""Prepare existing job and resume data for AI tasks."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from data.text import clean_job_description


class JobRecordProvider(Protocol):
    def as_job_record(self) -> dict[str, object]: ...


JobInput = JobRecordProvider | Mapping[str, object]


def load_resume_text(config: dict) -> str:
    path = Path(str((config.get("profile") or {}).get("resume_path") or ""))
    if not path.is_file():
        raise FileNotFoundError(f"简历文件不存在：{path}")
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError(f"简历文件为空：{path}")
    return content


def job_snapshot(job: JobInput) -> dict[str, str]:
    raw = job if isinstance(job, Mapping) else job.as_job_record()
    snapshot = {
        key: str(raw.get(key) or "").strip()
        for key in ("id", "source_job_id", "title", "company", "salary", "education",
                    "experience", "jd", "score_reason")
    }
    if not snapshot["score_reason"]:
        snapshot["score_reason"] = str(raw.get("ai_score_reason") or "").strip()
    snapshot["id"] = snapshot["id"] or snapshot["source_job_id"]
    snapshot["jd"] = clean_job_description(snapshot["jd"])
    if not snapshot["id"] or not snapshot["title"] or not snapshot["jd"]:
        raise ValueError("AI 任务需要岗位 ID、职位名称和 JD")
    return snapshot


def task_input(job: dict[str, str], resume_text: str, user_prompt: str) -> str:
    return json.dumps({
        "用户补充要求": user_prompt,
        "简历": resume_text,
        "岗位": {key: value for key, value in job.items() if key != "id"},
    }, ensure_ascii=False)
