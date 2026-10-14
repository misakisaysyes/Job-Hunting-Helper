"""Human-readable provenance for jobs collected from BOSS list flows."""

from __future__ import annotations

from typing import Any


def job_source_label(job: dict[str, Any]) -> str:
    mode = str(job.get("source_mode") or "").strip()
    keyword = str(job.get("source_keyword") or "").strip()
    if not mode and job.get("source_platform") == "boss" and "source_keyword" in job:
        # Older collector records kept the search term, but not the mode or
        # recommendation expect ID. Do not guess which configured ID was used.
        mode = "search" if keyword else "recommend"
    if mode == "recommend":
        expect_id = str(job.get("source_expect_id") or "").strip()
        return f"推荐流（{expect_id[:6]}）" if expect_id else "推荐流（期望 ID 未记录）"
    if mode == "search":
        city = str(job.get("source_city") or job.get("city") or "城市未记录").strip()
        return f"搜索流（{city}+{keyword or '工种未记录'}）"
    return "来源未记录"
