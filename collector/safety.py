"""Persistent BOSS page budgets and risk cooldown, isolated from bossHunter."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


class PlatformSafetyStop(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def open_safety_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS page_access (platform TEXT NOT NULL, stage TEXT NOT NULL, action TEXT NOT NULL, day TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS risk_lock (platform TEXT PRIMARY KEY, reason TEXT NOT NULL, until_utc TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS risk_event (platform TEXT NOT NULL, event_type TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL)")
    conn.commit()
    return conn


def add_risk_event(conn: sqlite3.Connection, event_type: str, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO risk_event VALUES (?, ?, ?, ?)",
        ("boss", event_type, detail, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


@dataclass
class BossAccessGuard:
    conn: sqlite3.Connection
    config: dict
    stage: str

    def ensure_unlocked(self) -> None:
        row = self.conn.execute("SELECT until_utc FROM risk_lock WHERE platform = ?", ("boss",)).fetchone()
        if row and datetime.fromisoformat(row[0]) > datetime.now(timezone.utc):
            raise PlatformSafetyStop("persistent_risk_lock")

    def reserve(self, action: str, *, daily_limit: int | None = None) -> None:
        self.ensure_unlocked()
        day = datetime.now().astimezone().date().isoformat()
        total = self.conn.execute(
            "SELECT count(*) FROM page_access WHERE platform = ? AND day = ?", ("boss", day)
        ).fetchone()[0]
        global_limit = max(1, int((self.config.get("safety") or {}).get("daily_platform_page_limit", 500)))
        if total >= global_limit:
            raise PlatformSafetyStop("daily_platform_page_limit")
        if daily_limit is not None:
            used = self.conn.execute(
                "SELECT count(*) FROM page_access WHERE platform = ? AND stage = ? AND action = ? AND day = ?",
                ("boss", self.stage, action, day),
            ).fetchone()[0]
            if used >= max(1, int(daily_limit)):
                raise PlatformSafetyStop(f"daily_{action}_limit")
        self.conn.execute("INSERT INTO page_access VALUES (?, ?, ?, ?)", ("boss", self.stage, action, day))
        self.conn.commit()

    def lock(self, reason: str, *, minutes: int | None = None) -> None:
        duration = minutes if minutes is not None else (self.config.get("safety") or {}).get("risk_lock_minutes", 10)
        until = datetime.now(timezone.utc) + timedelta(minutes=max(1, int(duration)))
        self.conn.execute(
            "INSERT OR REPLACE INTO risk_lock VALUES (?, ?, ?)", ("boss", reason, until.isoformat())
        )
        self.conn.commit()
