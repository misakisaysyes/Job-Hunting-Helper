"""Persistent collection failure logs shared by Web and CLI collection."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from collector.models import JobCandidate


def detail_failure_message(reason: str, candidate: JobCandidate, url: str) -> str:
    # JSON quoting keeps titles containing line breaks or separators on one line.
    details = {
        "岗位ID": candidate.source_job_id,
        "岗位名称": candidate.title,
        "链接": url,
    }
    return f"{reason} | {json.dumps(details, ensure_ascii=False)}"


def log_collection_failure(
    reason: str,
    config: dict[str, Any],
    *,
    db_path: Path,
    platform: str,
    run_id: str = "",
) -> None:
    """Append each failure to disk and publish it to the current task log."""
    message = str(reason).replace("\r", "\\r").replace("\n", "\\n")
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    context = f"platform={platform}" + (f" run_id={run_id}" if run_id else "")
    line = f"{timestamp} [{context}] {message}"
    logger = logging.getLogger(__name__)
    logger.warning("%s", line)
    callback = config.get("_workbench_log")
    path = db_path.parent / "collection-failures.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as output:
            output.write(line + "\n")
    except OSError as exc:
        # A log disk error must not abort the remaining collection queue.
        warning = f"采集失败日志写入失败：{path}（{exc}）"
        logger.warning("%s", warning)
        if callable(callback):
            callback(warning)
    if callable(callback):
        callback(message)
