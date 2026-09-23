"""Local settings persistence for the stage 7 UI -- fetch limit,
classification confidence threshold, and the dry-run toggle for Gmail
draft creation.

A single small JSON file rather than a database table: this is
per-machine tool configuration the user edits directly, not data the
app logs or queries -- a sqlite table would be the wrong shape for it.
Reuses pydantic.BaseModel (already a dependency) so the same type
serves as the on-disk schema and the FastAPI request/response model,
with no separate schema to keep in sync.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

SETTINGS_PATH = Path("settings.json")


class Settings(BaseModel):
    limit: int = 50
    confidence_threshold: float = 0.7
    # Defaults to on: creating a real Gmail draft is opt-in behavior
    # (FR7), and a fresh install shouldn't be one click away from
    # touching the user's actual Gmail account.
    dry_run: bool = True


def load_settings() -> Settings:
    if not SETTINGS_PATH.exists():
        return Settings()
    return Settings.model_validate_json(SETTINGS_PATH.read_text())


def save_settings(settings: Settings) -> None:
    SETTINGS_PATH.write_text(settings.model_dump_json(indent=2))
