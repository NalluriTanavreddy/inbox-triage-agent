"""FastAPI backend for the stage 7 UI.

Wraps the existing pipeline functions as REST endpoints -- fetch
(gmail.client), classify (classify.classifier), draft
(drafting.context/generator), digest (digest.builder), and persistence
(storage.db, storage.settings). Every endpoint here is a thin
orchestration layer; the actual logic is the same code the CLI (cli.py)
already calls, unchanged.

Serves the stage 7 frontend (src/ui/index.html) at "/" so the page and
the API share one origin -- no CORS configuration needed.

Run with: uvicorn api.main:app --reload  (from src/, with src on
PYTHONPATH -- see README).
"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from classify.classifier import classify_emails
from digest.builder import build_digest
from drafting.context import get_sender_context
from drafting.generator import generate_scheduling_draft
from gmail.auth import get_credentials
from gmail.client import create_draft_reply, fetch_email_body, fetch_unread_emails
from storage.db import ClassificationRepository, DraftRepository, get_connection
from storage.settings import Settings, load_settings, save_settings

load_dotenv()

app = FastAPI(title="Inbox Triage Agent")

_UI_INDEX = Path(__file__).resolve().parent.parent / "ui" / "index.html"


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_UI_INDEX)


# --- /run-triage ------------------------------------------------------


class AmbiguousItem(BaseModel):
    email_id: str
    subject: str
    sender: str
    urgency: str
    confidence: float
    reason: str | None


class DraftItem(BaseModel):
    draft_id: int
    email_id: str
    subject: str
    sender: str
    body: str


class RunTriageResponse(BaseModel):
    fetched: int
    ambiguous: list[AmbiguousItem]
    drafts: list[DraftItem]
    routine_counts: dict[str, int]


@app.post("/run-triage", response_model=RunTriageResponse)
def run_triage() -> RunTriageResponse:
    """Fetch unread mail, classify it, and generate drafts for
    confident scheduling matches -- the same three stages the CLI's
    fetch-unread/classify/draft commands run individually, in one call
    for the UI's "Run" button.

    Partitioning is mutually exclusive by design: an ambiguous email
    (FR4) is routed to "needs attention" regardless of its type, even
    if that type is "scheduling" -- a low-confidence scheduling guess
    should go to a human, not straight to draft generation. Only
    confident, non-ambiguous scheduling emails get a draft; everything
    else non-ambiguous is "routine".
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(400, "ANTHROPIC_API_KEY is not set -- add it to .env")

    settings = load_settings()
    creds = get_credentials()
    emails = fetch_unread_emails(creds, limit=settings.limit or None)

    results = classify_emails(emails, confidence_threshold=settings.confidence_threshold)
    emails_by_id = {email.id: email for email in emails}

    conn = get_connection()
    try:
        ClassificationRepository(conn).save_all(results)
        build_digest(conn)  # logs the digest generation (FR6), same as the CLI

        ambiguous = [r for r in results if r.is_ambiguous]
        scheduling = [r for r in results if r.type == "scheduling" and not r.is_ambiguous]
        routine = [r for r in results if not r.is_ambiguous and r.type != "scheduling"]

        draft_repo = DraftRepository(conn)
        draft_items: list[DraftItem] = []
        for r in scheduling:
            email = emails_by_id[r.email_id]
            context = get_sender_context(conn, r.sender, exclude_email_id=email.id)
            body_text = fetch_email_body(creds, email.id) or email.snippet
            draft_result = generate_scheduling_draft(email, body_text, context)
            if draft_result is None:
                continue
            draft_row_id = draft_repo.save(email.id, draft_result.body)
            draft_items.append(
                DraftItem(
                    draft_id=draft_row_id,
                    email_id=email.id,
                    subject=email.subject,
                    sender=email.sender,
                    body=draft_result.body,
                )
            )

        return RunTriageResponse(
            fetched=len(emails),
            ambiguous=[
                AmbiguousItem(
                    email_id=r.email_id,
                    subject=r.subject,
                    sender=r.sender,
                    urgency=r.urgency,
                    confidence=r.confidence,
                    reason=r.reason,
                )
                for r in ambiguous
            ],
            drafts=draft_items,
            routine_counts=dict(Counter(r.type for r in routine)),
        )
    finally:
        conn.close()


# --- /drafts/{id}/... ---------------------------------------------------


class CreateGmailDraftRequest(BaseModel):
    body: str


class CreateGmailDraftResponse(BaseModel):
    status: str
    gmail_draft_id: str | None = None


@app.post("/drafts/{draft_id}/create-gmail-draft", response_model=CreateGmailDraftResponse)
def create_gmail_draft_endpoint(
    draft_id: int, request: CreateGmailDraftRequest
) -> CreateGmailDraftResponse:
    """Create the real Gmail draft for a previously generated draft,
    using the (possibly user-edited) body from the request rather than
    re-reading the original from storage -- the frontend's textarea is
    editable, and what the user approved is what should be created."""
    settings = load_settings()
    conn = get_connection()
    try:
        draft_repo = DraftRepository(conn)
        email_id = draft_repo.get_email_id(draft_id)
        if email_id is None:
            raise HTTPException(404, f"No draft with id {draft_id}")

        if settings.dry_run:
            return CreateGmailDraftResponse(status="dry_run")

        creds = get_credentials()
        gmail_draft_id = create_draft_reply(creds, email_id, request.body)
        if gmail_draft_id is None:
            raise HTTPException(502, "Gmail draft creation failed -- see server logs")

        draft_repo.set_gmail_draft_id(draft_id, gmail_draft_id)
        return CreateGmailDraftResponse(status="created", gmail_draft_id=gmail_draft_id)
    finally:
        conn.close()


class SkipResponse(BaseModel):
    status: str


@app.post("/drafts/{draft_id}/skip", response_model=SkipResponse)
def skip_draft(draft_id: int) -> SkipResponse:
    """No-op acknowledgement -- skipping a draft in the UI doesn't need
    to change any stored state, it just dismisses the card."""
    return SkipResponse(status="skipped")


# --- /settings ----------------------------------------------------------


@app.get("/settings", response_model=Settings)
def get_settings() -> Settings:
    return load_settings()


@app.post("/settings", response_model=Settings)
def update_settings(settings: Settings) -> Settings:
    save_settings(settings)
    return settings
