"""Read daily task history using the workbench's Shanghai calendar."""

from datetime import date, datetime, timedelta, timezone

from api.jobs import JobActionError
from data.task_run_store import LOCAL_ZONE, TaskRunStore


def get_task_history(store: TaskRunStore, day_value: str = "", *,
                     now: datetime | None = None) -> dict:
    today = (now or datetime.now(timezone.utc)).astimezone(LOCAL_ZONE).date()
    latest = today - timedelta(days=1)
    try:
        day = date.fromisoformat(day_value) if day_value else latest
        if day_value and day.isoformat() != day_value:
            raise ValueError("日期格式不正确")
    except ValueError as exc:
        raise JobActionError("日期须为有效的 YYYY-MM-DD 格式", 400) from exc
    if day > latest:
        raise JobActionError("历史任务日期须早于今天，今日任务请查看上方面板", 400)
    return {**store.for_date(day), "latest_date": latest.isoformat()}
