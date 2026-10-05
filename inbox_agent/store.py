"""SQLite storage: emails, the agent's analysis, drafts, follow-ups, sent mail, reply memory and settings."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
  id TEXT PRIMARY KEY,
  thread_id TEXT,
  sender TEXT NOT NULL,
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  received_at TEXT NOT NULL,
  -- new -> triaged -> (awaiting_approval | fyi) -> (sent | skipped)
  status TEXT NOT NULL DEFAULT 'new',
  category TEXT, priority TEXT, confidence REAL, reasoning TEXT,
  summary TEXT, key_points TEXT, action_items TEXT, sentiment TEXT,
  draft_subject TEXT, draft_body TEXT, draft_tone TEXT, draft_confidence REAL, draft_sources TEXT,
  processing_ms INTEGER, triaged_at TEXT, decided_at TEXT
);
CREATE TABLE IF NOT EXISTS followups (
  email_id TEXT PRIMARY KEY REFERENCES emails(id) ON DELETE CASCADE,
  due_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  completed_at TEXT
);
CREATE TABLE IF NOT EXISTS sent (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email_id TEXT REFERENCES emails(id) ON DELETE SET NULL,
  recipient TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
  sent_at TEXT NOT NULL, mock INTEGER NOT NULL DEFAULT 1
);
-- Approved replies the drafter retrieves as examples of how you answer.
CREATE TABLE IF NOT EXISTS memory (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  incoming TEXT NOT NULL, reply TEXT NOT NULL, category TEXT, subject TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL, email_id TEXT, kind TEXT NOT NULL, detail TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

DEFAULT_RULES: dict[str, Any] = {
    "approval_required": True,
    "draft_for_categories": ["urgent", "important", "general"],
    "draft_min_priority": "medium",
    "follow_up_days": 3,
}

JSON_COLUMNS = {"key_points", "action_items", "draft_sources"}


def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def query(self, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [self._row(r) for r in self._conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    @staticmethod
    def _row(r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        for k in JSON_COLUMNS & d.keys():
            d[k] = json.loads(d[k]) if d[k] else []
        return d

    # ---- emails -------------------------------------------------------------------------------

    def add_email(self, e: dict[str, Any]) -> bool:
        """Insert a fetched email; returns False if it was already stored."""
        with self.tx() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO emails (id, thread_id, sender, subject, body, received_at) VALUES (?,?,?,?,?,?)",
                (e["id"], e.get("thread_id"), e["sender"], e["subject"], e["body"], e["received_at"]),
            )
            return cur.rowcount > 0

    def email(self, email_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM emails WHERE id = ?", (email_id,))

    def emails(self, status: str | None = None, category: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM emails WHERE 1=1"
        params: list[Any] = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += """ ORDER BY CASE status WHEN 'awaiting_approval' THEN 0 WHEN 'new' THEN 1 ELSE 2 END,
                   CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, received_at DESC"""
        return self.query(sql, tuple(params))

    def update_email(self, email_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        vals = [json.dumps(v) if k in JSON_COLUMNS else v for k, v in fields.items()]
        with self.tx() as c:
            c.execute(f"UPDATE emails SET {cols} WHERE id = ?", (*vals, email_id))

    # ---- follow-ups ---------------------------------------------------------------------------

    def schedule_followup(self, email_id: str, days: float) -> str:
        due = iso(now() + timedelta(days=days))
        with self.tx() as c:
            c.execute(
                "INSERT INTO followups (email_id, due_at, status) VALUES (?,?,'pending') "
                "ON CONFLICT(email_id) DO UPDATE SET due_at = excluded.due_at, status = 'pending', completed_at = NULL",
                (email_id, due),
            )
        return due

    def followups(self) -> list[dict[str, Any]]:
        return self.query(
            "SELECT f.*, e.subject, e.sender, e.priority, e.summary FROM followups f JOIN emails e ON e.id = f.email_id "
            "ORDER BY f.status = 'done', f.due_at"
        )

    def complete_followup(self, email_id: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE followups SET status = 'done', completed_at = ? WHERE email_id = ?", (iso(now()), email_id))

    # ---- sent, memory, activity ---------------------------------------------------------------

    def record_sent(self, email_id: str, recipient: str, subject: str, body: str, mock: bool) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO sent (email_id, recipient, subject, body, sent_at, mock) VALUES (?,?,?,?,?,?)",
                (email_id, recipient, subject, body, iso(now()), int(mock)),
            )

    def sent(self) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM sent ORDER BY sent_at DESC")

    def remember(self, incoming: str, reply: str, category: str | None, subject: str | None, at: str | None = None) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO memory (incoming, reply, category, subject, created_at) VALUES (?,?,?,?,?)",
                (incoming, reply, category, subject, at or iso(now())),
            )

    def memory(self) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM memory ORDER BY id")

    def log(self, kind: str, email_id: str | None = None, detail: str = "", at: str | None = None) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO activity (at, email_id, kind, detail) VALUES (?,?,?,?)", (at or iso(now()), email_id, kind, detail))

    def activity(self, limit: int = 30) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM activity ORDER BY at DESC, id DESC LIMIT ?", (limit,))

    # ---- rules (editable in the UI) -----------------------------------------------------------

    def rules(self) -> dict[str, Any]:
        rows = {r["key"]: json.loads(r["value"]) for r in self.query("SELECT key, value FROM settings")}
        return {**DEFAULT_RULES, **rows}

    def save_rules(self, rules: dict[str, Any]) -> None:
        with self.tx() as c:
            for k, v in rules.items():
                if k in DEFAULT_RULES:
                    c.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (k, json.dumps(v)))

    def reset(self) -> None:
        with self.tx() as c:
            for t in ("followups", "sent", "memory", "activity", "emails", "settings"):
                c.execute(f"DELETE FROM {t}")
