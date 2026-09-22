"""SQLite storage layer, using a repository pattern to avoid duplicated
query logic across modules (NFR: Maintainability). Logs classification
outcomes (FR8) and digest generations (FR6).

Kept minimal on purpose — schema covers what classification and digest
need today; drafting (Stage 4) extends this once it knows what it needs,
rather than guessing ahead of time.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from classify.classifier import Classification

DB_PATH = Path("inbox_triage.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS classifications (
    email_id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    urgency TEXT NOT NULL,
    type TEXT NOT NULL,
    confidence REAL NOT NULL,
    is_ambiguous INTEGER NOT NULL,
    reason TEXT,
    classified_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS digests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at TEXT NOT NULL,
    email_ids TEXT NOT NULL
);
"""


def get_connection(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    """Open the SQLite database, creating the schema if it doesn't exist
    yet."""
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    return conn


class ClassificationRepository:
    """Reads and writes classification outcomes (FR8)."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def save_all(self, classifications: list[Classification]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn:
            self._conn.executemany(
                """
                INSERT INTO classifications
                    (email_id, subject, urgency, type, confidence, is_ambiguous, reason, classified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(email_id) DO UPDATE SET
                    subject=excluded.subject,
                    urgency=excluded.urgency,
                    type=excluded.type,
                    confidence=excluded.confidence,
                    is_ambiguous=excluded.is_ambiguous,
                    reason=excluded.reason,
                    classified_at=excluded.classified_at
                """,
                [
                    (
                        c.email_id,
                        c.subject,
                        c.urgency,
                        c.type,
                        c.confidence,
                        int(c.is_ambiguous),
                        c.reason,
                        now,
                    )
                    for c in classifications
                ],
            )

    def get_ambiguous(self) -> list[Classification]:
        rows = self._conn.execute(
            "SELECT email_id, subject, urgency, type, confidence, is_ambiguous, reason "
            "FROM classifications WHERE is_ambiguous = 1 ORDER BY classified_at"
        ).fetchall()
        return [
            Classification(
                email_id=row[0],
                subject=row[1],
                urgency=row[2],
                type=row[3],
                confidence=row[4],
                is_ambiguous=bool(row[5]),
                reason=row[6],
            )
            for row in rows
        ]


class DigestRepository:
    """Logs each daily digest generation, so runs are auditable (FR6)."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def save(self, email_ids: list[str]) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO digests (generated_at, email_ids) VALUES (?, ?)",
                (now, json.dumps(email_ids)),
            )
        return cursor.lastrowid
