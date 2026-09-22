"""Builds the daily digest listing only ambiguous emails, each with a
one-line reason for ambiguity (FR6)."""

from __future__ import annotations

import sqlite3

from storage.db import ClassificationRepository, DigestRepository


def build_digest(conn: sqlite3.Connection) -> str:
    """Read ambiguous classifications back from storage, log this digest
    generation, and return the formatted digest text."""
    ambiguous = ClassificationRepository(conn).get_ambiguous()

    DigestRepository(conn).save([c.email_id for c in ambiguous])

    if not ambiguous:
        return "No ambiguous emails — nothing needs review."

    lines = [f"{len(ambiguous)} email(s) need review:", ""]
    for c in ambiguous:
        lines.append(f"- {c.subject}")
        lines.append(f"  {c.reason}")
    return "\n".join(lines)
