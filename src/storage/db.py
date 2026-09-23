"""SQLite storage layer, using a repository pattern to avoid duplicated
query logic across modules (NFR: Maintainability). Logs classification
outcomes (FR8), digest generations (FR6), and generated drafts (FR8
extension).

The classifications table doubles as the sender-history source for
drafting (FR5) -- every classify run already logs one row per email
(sender, subject, snippet), which is exactly the raw material tone
inference needs. A separate sender-history table would duplicate that
same one-row-per-email log for no benefit at MVP scope.
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
    sender TEXT NOT NULL,
    subject TEXT NOT NULL,
    snippet TEXT NOT NULL,
    urgency TEXT NOT NULL,
    type TEXT NOT NULL,
    confidence REAL NOT NULL,
    is_ambiguous INTEGER NOT NULL,
    reason TEXT,
    classified_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_classifications_sender ON classifications(sender);

CREATE TABLE IF NOT EXISTS digests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at TEXT NOT NULL,
    email_ids TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id TEXT NOT NULL,
    draft_text TEXT NOT NULL,
    gmail_draft_id TEXT,
    generated_at TEXT NOT NULL
);
"""


_CLASSIFICATION_COLUMNS = (
    "email_id, sender, subject, snippet, urgency, type, confidence, is_ambiguous, reason"
)


def _row_to_classification(row: tuple) -> Classification:
    return Classification(
        email_id=row[0],
        sender=row[1],
        subject=row[2],
        snippet=row[3],
        urgency=row[4],
        type=row[5],
        confidence=row[6],
        is_ambiguous=bool(row[7]),
        reason=row[8],
    )


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
                    (email_id, sender, subject, snippet, urgency, type, confidence, is_ambiguous, reason, classified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(email_id) DO UPDATE SET
                    sender=excluded.sender,
                    subject=excluded.subject,
                    snippet=excluded.snippet,
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
                        c.sender,
                        c.subject,
                        c.snippet,
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
            f"SELECT {_CLASSIFICATION_COLUMNS} FROM classifications "
            "WHERE is_ambiguous = 1 ORDER BY classified_at"
        ).fetchall()
        return [_row_to_classification(row) for row in rows]

    def get_by_sender(
        self, sender: str, exclude_email_id: str | None = None, limit: int = 5
    ) -> list[Classification]:
        """Prior logged emails from this sender, most recent first --
        the raw material drafting.context uses for tone (FR5)."""
        query = f"SELECT {_CLASSIFICATION_COLUMNS} FROM classifications WHERE sender = ?"
        params: list = [sender]
        if exclude_email_id:
            query += " AND email_id != ?"
            params.append(exclude_email_id)
        query += " ORDER BY classified_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [_row_to_classification(row) for row in rows]


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


class DraftRepository:
    """Logs generated draft replies -- draft text, source email,
    timestamp, and (once created) the Gmail draft id (FR8 extension,
    FR7)."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def save(self, email_id: str, draft_text: str) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO drafts (email_id, draft_text, generated_at) VALUES (?, ?, ?)",
                (email_id, draft_text, now),
            )
        return cursor.lastrowid

    def get_email_id(self, draft_id: int) -> str | None:
        """Look up the source email id for a logged draft row -- the
        stage 7 API takes the (possibly user-edited) draft body straight
        from the request, but still needs the original email id to
        thread the Gmail draft as a reply (FR7)."""
        row = self._conn.execute(
            "SELECT email_id FROM drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        return row[0] if row else None

    def set_gmail_draft_id(self, draft_id: int, gmail_draft_id: str) -> None:
        """Record the Gmail draft actually created for a logged draft
        (FR7) -- a separate write from save() because generation and
        Gmail creation are two steps that can each fail independently;
        a draft can be logged with gmail_draft_id still NULL if only
        printed, never created."""
        with self._conn:
            self._conn.execute(
                "UPDATE drafts SET gmail_draft_id = ? WHERE id = ?",
                (gmail_draft_id, draft_id),
            )
