"""Persist BOSS conversations independently of the collected job pool."""

from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
from typing import Any


MONITORED_STATUSES = frozenset({"unread", "read_no_reply"})
_BOSS_DEFAULT_GREETING = "Boss您好，看到贵公司的招聘信息，希望能得到贵公司的垂青，谢谢"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_original_greeting(messages: list[dict[str, Any]], job: dict[str, Any] | None) -> str:
    """Prefer the custom greeting actually delivered over BOSS's opening card."""
    opening = next((message for message in messages
                    if message.get("sender") == "self"
                    and str(message.get("biz_type") or "") == "101"
                    and str(message.get("delivery_status") or "") not in {"0", "3", "4"}
                    and str(message.get("text") or "").strip()), None)
    if opening and str(opening["text"]).strip() == _BOSS_DEFAULT_GREETING:
        # BOSS can send this card before the app sends its custom greeting.
        # Only consider the first delivered plain text in that initial send sequence.
        after_opening = False
        for message in messages:
            if message is opening:
                after_opening = True
                continue
            if not after_opening or str(message.get("biz_type") or "") not in {"", "0", "1", "101"}:
                continue
            if not str(message.get("text") or "").strip():
                continue
            if message.get("sender") != "self":
                break
            if str(message.get("delivery_status") or "") not in {"1", "2"}:
                break
            try:
                start, sent = (float(str(item.get("sent_at"))) for item in (opening, message))
                start = start / 1000 if start > 1e11 else start
                sent = sent / 1000 if sent > 1e11 else sent
                if 0 <= sent - start <= 120:
                    return str(message["text"]).strip()
            except (TypeError, ValueError, OverflowError):
                pass
            break
    saved = str((job or {}).get("greeting") or "").strip()
    if saved:
        actual = next((message for message in messages
                       if message.get("sender") == "self"
                       and str(message.get("delivery_status") or "") in {"1", "2"}
                       and str(message.get("text") or "").strip() == saved), None)
        if actual:
            return str(actual["text"]).strip()
        if (job or {}).get("greeting_send_state") == "sent":
            return saved
    return str((opening or {}).get("text") or "").strip()


class ConversationStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.execute("""CREATE TABLE IF NOT EXISTS monitored_conversations (
            platform TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            source_job_id TEXT NOT NULL DEFAULT '',
            job_title TEXT NOT NULL DEFAULT '',
            company TEXT NOT NULL DEFAULT '',
            recruiter TEXT NOT NULL DEFAULT '',
            chat_url TEXT NOT NULL DEFAULT '',
            in_job_pool INTEGER NOT NULL DEFAULT 0,
            judgment TEXT NOT NULL DEFAULT 'unread',
            next_action TEXT NOT NULL DEFAULT 'none',
            evidence TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_scanned_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 0,
            monitoring_terminated INTEGER NOT NULL DEFAULT 0,
            monitoring_terminated_at TEXT NOT NULL DEFAULT '',
            scan_token TEXT NOT NULL DEFAULT '',
            followup_count INTEGER NOT NULL DEFAULT 0,
            followup_status TEXT NOT NULL DEFAULT 'none',
            followup_text TEXT NOT NULL DEFAULT '',
            followup_anchor_id TEXT NOT NULL DEFAULT '',
            followup_last_sent_at TEXT NOT NULL DEFAULT '',
            followup_error TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (platform, conversation_id)
        )""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS monitored_messages (
            platform TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            sender TEXT NOT NULL,
            text TEXT NOT NULL,
            sent_at TEXT NOT NULL DEFAULT '',
            delivery_status TEXT NOT NULL DEFAULT '',
            biz_type TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (platform, conversation_id, message_id)
        )""")
        conversation_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(monitored_conversations)")}
        followup_columns = {
            "followup_count": "INTEGER NOT NULL DEFAULT 0",
            "followup_status": "TEXT NOT NULL DEFAULT 'none'",
            "followup_text": "TEXT NOT NULL DEFAULT ''",
            "followup_anchor_id": "TEXT NOT NULL DEFAULT ''",
            "followup_last_sent_at": "TEXT NOT NULL DEFAULT ''",
            "followup_error": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in followup_columns.items():
            if name not in conversation_columns:
                self.conn.execute(f"ALTER TABLE monitored_conversations ADD COLUMN {name} {definition}")
        if "active" not in conversation_columns:
            # Previously scanned rows need a fresh list check before reappearing.
            self.conn.execute("ALTER TABLE monitored_conversations ADD COLUMN active INTEGER NOT NULL DEFAULT 0")
        if "scan_token" not in conversation_columns:
            self.conn.execute("ALTER TABLE monitored_conversations ADD COLUMN scan_token TEXT NOT NULL DEFAULT ''")
        if "monitoring_terminated" not in conversation_columns:
            self.conn.execute("ALTER TABLE monitored_conversations ADD COLUMN monitoring_terminated INTEGER NOT NULL DEFAULT 0")
        if "monitoring_terminated_at" not in conversation_columns:
            self.conn.execute("ALTER TABLE monitored_conversations ADD COLUMN monitoring_terminated_at TEXT NOT NULL DEFAULT ''")
        if "hr_reply_status" in conversation_columns:
            self.conn.execute("ALTER TABLE monitored_conversations DROP COLUMN hr_reply_status")
        message_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(monitored_messages)")}
        if "delivery_status" not in message_columns:
            self.conn.execute("ALTER TABLE monitored_messages ADD COLUMN delivery_status TEXT NOT NULL DEFAULT ''")
        if "biz_type" not in message_columns:
            self.conn.execute("ALTER TABLE monitored_messages ADD COLUMN biz_type TEXT NOT NULL DEFAULT ''")
        self.conn.execute("""CREATE INDEX IF NOT EXISTS idx_monitored_conversations_updated
            ON monitored_conversations(updated_at DESC)""")
        self.conn.execute("""CREATE INDEX IF NOT EXISTS idx_monitored_conversations_active
            ON monitored_conversations(active, updated_at DESC)""")
        self.conn.commit()

    def upsert(self, conversation: dict[str, Any], messages: list[dict[str, Any]],
               *, scan_token: str = "") -> bool:
        """Save a conversation whose outbound message is unread or read without reply."""
        status = conversation.get("judgment")
        if status not in MONITORED_STATUSES:
            raise ValueError(f"不支持的监测会话状态：{status}")
        platform = conversation["platform"]
        conversation_id = str(conversation["conversation_id"])
        now = _now()
        cursor = self.conn.execute("""INSERT INTO monitored_conversations (
            platform, conversation_id, source_job_id, job_title, company, recruiter,
            chat_url, in_job_pool, judgment, next_action, evidence,
            first_seen_at, updated_at, last_scanned_at, active, scan_token
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        ON CONFLICT(platform, conversation_id) DO UPDATE SET
            source_job_id = excluded.source_job_id,
            job_title = excluded.job_title,
            company = excluded.company,
            recruiter = excluded.recruiter,
            chat_url = excluded.chat_url,
            in_job_pool = excluded.in_job_pool,
            judgment = excluded.judgment,
            next_action = excluded.next_action,
            evidence = excluded.evidence,
            updated_at = excluded.updated_at,
            last_scanned_at = excluded.last_scanned_at,
            active = 1,
            scan_token = excluded.scan_token
        WHERE monitored_conversations.monitoring_terminated = 0""", (
            platform, conversation_id, conversation.get("source_job_id", ""),
            conversation.get("job_title", ""), conversation.get("company", ""),
            conversation.get("recruiter", ""), conversation.get("chat_url", ""),
            int(bool(conversation.get("in_job_pool"))),
            status, conversation.get("next_action", "none"),
            conversation.get("evidence", ""), now, now, now, scan_token,
        ))
        if cursor.rowcount == 0:
            self.conn.commit()
            return False
        for message in messages:
            self.conn.execute("""INSERT INTO monitored_messages
                (platform, conversation_id, message_id, sender, text, sent_at, delivery_status, biz_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, conversation_id, message_id) DO UPDATE SET
                    delivery_status = excluded.delivery_status""", (
                platform, conversation_id, str(message["message_id"]),
                message.get("sender", "unknown"), message.get("text", ""),
                message.get("sent_at", ""), message.get("delivery_status", ""),
                message.get("biz_type", ""),
            ))
        self.conn.commit()
        return True

    def terminated_ids(self, platform: str) -> set[str]:
        return {row[0] for row in self.conn.execute("""SELECT conversation_id
            FROM monitored_conversations WHERE platform = ? AND monitoring_terminated = 1""",
            (platform,))}

    def terminate_monitoring(self, platform: str, conversation_id: str) -> bool:
        """Retire an active conversation and invalidate its unsent follow-up."""
        cursor = self.conn.execute("""UPDATE monitored_conversations SET
            monitoring_terminated = 1, monitoring_terminated_at = ?, active = 0,
            followup_status = CASE WHEN followup_status = 'pending_review'
                THEN 'stale' ELSE followup_status END,
            followup_text = '', updated_at = ?
            WHERE platform = ? AND conversation_id = ? AND active = 1
              AND monitoring_terminated = 0 AND followup_status != 'sending'""",
            (_now(), _now(), platform, conversation_id))
        self.conn.commit()
        return cursor.rowcount == 1

    def delete_record(self, platform: str, conversation_id: str) -> str:
        """Remove a local conversation and its messages; never touch BOSS."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute("""SELECT followup_status FROM monitored_conversations
                WHERE platform = ? AND conversation_id = ?""",
                (platform, conversation_id)).fetchone()
            if row is None or row[0] == "sending":
                self.conn.rollback()
                return "missing" if row is None else "busy"
            self.conn.execute("""DELETE FROM monitored_messages
                WHERE platform = ? AND conversation_id = ?""", (platform, conversation_id))
            self.conn.execute("""DELETE FROM monitored_conversations
                WHERE platform = ? AND conversation_id = ?""", (platform, conversation_id))
            self.conn.commit()
            return "deleted"
        except Exception:
            self.conn.rollback()
            raise

    def deactivate_unseen(self, scan_token: str) -> int:
        """Retire rows absent from the latest successful Communication-tab scan."""
        cursor = self.conn.execute("""UPDATE monitored_conversations SET
            active = 0,
            followup_status = CASE WHEN followup_status = 'pending_review'
                THEN 'stale' ELSE followup_status END,
            followup_text = CASE WHEN followup_status = 'pending_review'
                THEN '' ELSE followup_text END,
            updated_at = ?
            WHERE active = 1 AND scan_token != ?""", (_now(), scan_token))
        self.conn.commit()
        return cursor.rowcount

    def list_conversations(self, *, limit: int, offset: int, query: str = "",
                           conversation_status: str = "",
                           followup_status: str = "") -> tuple[list[dict[str, Any]], int]:
        conditions: list[str] = ["active = 1", "monitoring_terminated = 0", "judgment IN ('unread', 'read_no_reply')"]
        params: list[str] = []
        if query.strip():
            escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            term = f"%{escaped}%"
            conditions.append("""(job_title LIKE ? ESCAPE '\\' OR company LIKE ? ESCAPE '\\'
                OR recruiter LIKE ? ESCAPE '\\' OR conversation_id LIKE ? ESCAPE '\\')""")
            params.extend([term] * 4)
        for column, value in (("judgment", conversation_status),
                              ("followup_status", followup_status)):
            if value:
                if column == "followup_status" and value == "followed_up":
                    conditions.append("followup_count > 0")
                else:
                    conditions.append(f"{column} = ?")
                    params.append(value)
        where = f" WHERE {' AND '.join(conditions)}"
        total = self.conn.execute(
            f"SELECT COUNT(*) FROM monitored_conversations{where}", params).fetchone()[0]
        columns = ("platform", "conversation_id", "source_job_id", "job_title", "company",
                   "recruiter", "chat_url", "in_job_pool", "judgment", "next_action",
                   "evidence", "first_seen_at", "updated_at", "last_scanned_at",
                   "followup_count", "followup_status", "followup_text", "followup_anchor_id",
                   "followup_last_sent_at", "followup_error")
        rows = self.conn.execute(f"""SELECT {', '.join(columns)} FROM monitored_conversations{where}
            ORDER BY updated_at DESC, platform, conversation_id LIMIT ? OFFSET ?""",
            [*params, limit, offset]).fetchall()
        conversations = [dict(zip(columns, row)) for row in rows]
        for item in conversations:
            item["in_job_pool"] = bool(item["in_job_pool"])
            item["conversation_status"] = item["judgment"]
            item["greeting_text"] = self.original_greeting(
                item["platform"], item["conversation_id"], item["source_job_id"])
        return conversations, total

    def original_greeting(self, platform: str, conversation_id: str,
                          source_job_id: str = "") -> str:
        """Resolve the original greeting from actual messages and send evidence."""
        names = ("sender", "text", "delivery_status", "biz_type", "sent_at")
        messages = [dict(zip(names, row)) for row in self.conn.execute("""SELECT
            sender, text, delivery_status, biz_type, sent_at FROM monitored_messages
            WHERE platform = ? AND conversation_id = ?
            ORDER BY CAST(message_id AS INTEGER), message_id""", (platform, conversation_id))]
        job = None
        has_jobs = self.conn.execute("""SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'collected_jobs'""").fetchone()
        if has_jobs and source_job_id:
            row = self.conn.execute("""SELECT greeting, greeting_send_state FROM collected_jobs
                WHERE platform = ? AND source_job_id = ?""",
                (platform, source_job_id)).fetchone()
            if row:
                job = dict(zip(("greeting", "greeting_send_state"), row))
        return resolve_original_greeting(messages, job)

    def get_conversation(self, platform: str, conversation_id: str) -> dict[str, Any] | None:
        columns = ("platform", "conversation_id", "source_job_id", "job_title", "company",
                   "recruiter", "chat_url", "in_job_pool", "judgment", "next_action",
                   "evidence", "first_seen_at", "updated_at", "last_scanned_at",
                   "followup_count", "followup_status", "followup_text", "followup_anchor_id",
                   "followup_last_sent_at", "followup_error", "active")
        row = self.conn.execute(f"""SELECT {', '.join(columns)} FROM monitored_conversations
            WHERE platform = ? AND conversation_id = ? AND active = 1
              AND monitoring_terminated = 0
              AND judgment IN ('unread', 'read_no_reply')""",
            (platform, conversation_id)).fetchone()
        if row is None:
            return None
        result = dict(zip(columns, row))
        result["in_job_pool"] = bool(result["in_job_pool"])
        result["active"] = bool(result["active"])
        result["conversation_status"] = result["judgment"]
        result["greeting_text"] = self.original_greeting(platform, conversation_id, result["source_job_id"])
        return result

    def prepare_followup(self, platform: str, conversation_id: str,
                         text: str, anchor_id: str) -> bool:
        """Create at most one review item for the current last outbound message."""
        now = _now()
        cursor = self.conn.execute("""UPDATE monitored_conversations
            SET followup_status = 'pending_review', followup_text = ?,
                followup_anchor_id = ?, followup_error = '', updated_at = ?
            WHERE platform = ? AND conversation_id = ?
              AND active = 1 AND monitoring_terminated = 0
              AND followup_status IN ('none', 'idle', 'stale')
              AND judgment IN ('unread', 'read_no_reply')""",
            (text, anchor_id, now, platform, conversation_id))
        self.conn.commit()
        return cursor.rowcount == 1

    def save_followup(self, platform: str, conversation_id: str, text: str,
                      anchor_id: str, *, expected_text: str | None = None) -> bool:
        """Edit a draft only while its conversation and source message still match."""
        cursor = self.conn.execute("""UPDATE monitored_conversations
            SET followup_text = ?, followup_error = '', updated_at = ?
            WHERE platform = ? AND conversation_id = ? AND active = 1
              AND monitoring_terminated = 0 AND followup_status = 'pending_review'
              AND judgment IN ('unread', 'read_no_reply') AND followup_anchor_id = ?
              AND (? IS NULL OR followup_text = ?)""",
            (text, _now(), platform, conversation_id, anchor_id, expected_text, expected_text))
        self.conn.commit()
        return cursor.rowcount == 1

    def reconcile_followup_count(self, platform: str, conversation_id: str,
                                 observed_count: int) -> None:
        if observed_count <= 0:
            return
        self.conn.execute("""UPDATE monitored_conversations
            SET followup_count = max(followup_count, ?)
            WHERE platform = ? AND conversation_id = ? AND active = 1""",
            (observed_count, platform, conversation_id))
        self.conn.commit()

    def invalidate_followup(self, platform: str, conversation_id: str) -> None:
        self.conn.execute("""UPDATE monitored_conversations
            SET followup_status = 'stale', followup_text = '',
                followup_error = '会话已变化，请重新扫描', updated_at = ?
            WHERE platform = ? AND conversation_id = ?
              AND followup_status = 'pending_review'""",
            (_now(), platform, conversation_id))
        self.conn.commit()

    def claim_followup(self, platform: str, conversation_id: str,
                       max_count: int, *, text: str | None = None,
                       anchor_id: str | None = None,
                       expected_text: str | None = None) -> dict[str, Any] | None:
        """Reserve before touching BOSS so concurrent approvals cannot double-send."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute("""SELECT coalesce(?, c.followup_text), c.followup_anchor_id,
                c.chat_url, c.followup_count, m.text FROM monitored_conversations c
                JOIN monitored_messages m ON m.platform = c.platform
                  AND m.conversation_id = c.conversation_id AND m.message_id = c.followup_anchor_id
                WHERE c.platform = ? AND c.conversation_id = ? AND m.sender = 'self'
                  AND c.active = 1 AND c.monitoring_terminated = 0
                  AND c.followup_status = 'pending_review' AND c.followup_count < ?
                  AND c.judgment IN ('unread', 'read_no_reply')
                  AND trim(coalesce(?, c.followup_text)) != ''
                  AND (? IS NULL OR c.followup_anchor_id = ?)
                  AND (? IS NULL OR c.followup_text = ?)""",
                (text, platform, conversation_id, max_count, text,
                 anchor_id, anchor_id, expected_text, expected_text)).fetchone()
            if row is None:
                self.conn.rollback()
                return None
            self.conn.execute("""UPDATE monitored_conversations
                SET followup_status = 'sending', followup_text = ?, updated_at = ?
                WHERE platform = ? AND conversation_id = ?""",
                (row[0], _now(), platform, conversation_id))
            self.conn.commit()
            return dict(zip(("text", "anchor_id", "chat_url", "count", "anchor_text"), row))
        except Exception:
            self.conn.rollback()
            raise

    def finish_followup(self, platform: str, conversation_id: str, *, outcome: str,
                        message_id: str = "", note: str = "") -> bool:
        if outcome not in {"sent", "failed", "unknown"}:
            raise ValueError("未知追问发送结果")
        status = "idle" if outcome == "sent" else outcome
        now = _now()
        if outcome == "sent" and message_id:
            # A verified edited follow-up is a real message, independent of the original greeting.
            self.conn.execute("""INSERT OR IGNORE INTO monitored_messages
                (platform, conversation_id, message_id, sender, text, sent_at, delivery_status, biz_type)
                SELECT platform, conversation_id, ?, 'self', followup_text, ?, '1', '1'
                FROM monitored_conversations WHERE platform = ? AND conversation_id = ?
                  AND followup_status = 'sending'""",
                (message_id, str(int(datetime.now(timezone.utc).timestamp() * 1000)),
                 platform, conversation_id))
        cursor = self.conn.execute("""UPDATE monitored_conversations
            SET followup_status = ?, followup_count = followup_count + ?,
                judgment = CASE WHEN ? = 'sent' THEN 'unread' ELSE judgment END,
                evidence = CASE WHEN ? = 'sent' THEN '追问已发送，等待对方阅读' ELSE evidence END,
                followup_anchor_id = CASE WHEN ? = 'sent' THEN ? ELSE followup_anchor_id END,
                followup_last_sent_at = CASE WHEN ? = 'sent' THEN ? ELSE followup_last_sent_at END,
                followup_text = CASE WHEN ? = 'sent' THEN '' ELSE followup_text END,
                followup_error = ?, updated_at = ?
            WHERE platform = ? AND conversation_id = ? AND followup_status = 'sending'""",
            (status, int(outcome == "sent"), outcome, outcome,
             outcome, message_id, outcome, now,
             outcome, note, now, platform, conversation_id))
        self.conn.commit()
        return cursor.rowcount == 1

    def skip_followup(self, platform: str, conversation_id: str) -> bool:
        cursor = self.conn.execute("""UPDATE monitored_conversations
            SET followup_status = 'skipped', followup_text = '', updated_at = ?
            WHERE platform = ? AND conversation_id = ? AND active = 1
              AND followup_status = 'pending_review'""",
            (_now(), platform, conversation_id))
        self.conn.commit()
        return cursor.rowcount == 1

    def recover_interrupted_followups(self) -> int:
        cursor = self.conn.execute("""UPDATE monitored_conversations
            SET followup_status = 'unknown', followup_error = '服务中断，发送结果未确认'
            WHERE followup_status = 'sending'""")
        self.conn.commit()
        return cursor.rowcount
