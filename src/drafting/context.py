"""Looks up sender history and tone to provide context for draft
generation (FR5). Deeper sender-history "learning" is deferred as a
stretch goal (BRD section 12) -- this is a lookup of what's already
been logged, not a model that learns over time.

Sender history is derived from the classifications table rather than a
dedicated table (decision noted in storage/db.py's module docstring):
every classify run already logs one row per email -- sender, subject,
snippet -- which is exactly the raw material tone inference needs. A
separate table would duplicate that same log for no benefit at MVP
scope.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from storage.db import ClassificationRepository

# Recent history is what matters for tone, and keeps the drafting
# prompt small -- an unbounded history dump would cost more without
# improving tone matching.
_MAX_PRIOR_MESSAGES = 5


@dataclass
class SenderContext:
    sender: str
    prior_count: int
    prior_snippets: list[str]

    @property
    def has_history(self) -> bool:
        return self.prior_count > 0


def get_sender_context(
    conn: sqlite3.Connection, sender: str, exclude_email_id: str | None = None
) -> SenderContext:
    """Look up this sender's prior logged emails for tone context.

    A first-time sender (no prior history) comes back with
    `has_history=False` -- generator.py falls back to a neutral,
    professional default rather than guessing at a tone with nothing to
    go on.
    """
    prior = ClassificationRepository(conn).get_by_sender(
        sender, exclude_email_id=exclude_email_id, limit=_MAX_PRIOR_MESSAGES
    )
    snippets = [f"Subject: {c.subject} -- {c.snippet}" for c in prior if c.snippet]
    return SenderContext(sender=sender, prior_count=len(prior), prior_snippets=snippets)
