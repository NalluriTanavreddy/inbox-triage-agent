"""Typer-based CLI entry point for running the agent end to end: fetch,
classify, draft/digest (BRD section 10, MVP CLI layer)."""

import os
import sys

from dotenv import load_dotenv
import typer

from classify.classifier import classify_emails
from digest.builder import build_digest
from drafting.context import get_sender_context
from drafting.generator import generate_scheduling_draft
from gmail.auth import get_credentials
from gmail.client import (
    create_draft_reply,
    fetch_email_body,
    fetch_email_by_id,
    fetch_unread_emails,
)
from storage.db import ClassificationRepository, DraftRepository, get_connection

# Windows consoles default stdout to cp1252, which can't encode most
# Unicode (e.g. emoji in email subjects) and crashes on print. Force UTF-8
# so arbitrary subject lines don't blow up the CLI.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer()


@app.command()
def fetch_unread(
    limit: int = typer.Option(
        50, "--limit", help="Max unread emails to fetch. Use 0 for no limit."
    ),
) -> None:
    """Authenticate, fetch unread inbox emails, and print a quick summary.

    Stage 1 verification only — no classification yet. Defaults to a small
    limit so a routine run stays fast regardless of inbox size; pass
    --limit 0 to fetch the entire unread backlog.
    """
    load_dotenv()
    creds = get_credentials()
    emails = fetch_unread_emails(creds, limit=limit or None)

    typer.echo(f"{len(emails)} unread email(s)")
    for email in emails:
        typer.echo(f"- {email.subject}")


@app.command()
def classify(
    limit: int = typer.Option(
        50, "--limit", help="Max unread emails to fetch and classify. Use 0 for no limit."
    ),
) -> None:
    """Authenticate, fetch unread inbox emails, classify them in a single
    batched Claude API call, and print a summary table.

    Stage 2 verification only — no digest or drafting yet.
    """
    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        typer.echo("ANTHROPIC_API_KEY is not set — add it to .env", err=True)
        raise typer.Exit(1)

    creds = get_credentials()
    emails = fetch_unread_emails(creds, limit=limit or None)
    typer.echo(f"{len(emails)} unread email(s) fetched; classifying...")

    results = classify_emails(emails)

    header = f"{'URGENCY':<18} {'TYPE':<15} {'CONF':<6} {'SUBJECT':<50} REASON"
    typer.echo(header)
    typer.echo("-" * len(header))
    for r in results:
        subject = r.subject if len(r.subject) <= 47 else r.subject[:47] + "..."
        typer.echo(
            f"{r.urgency:<18} {r.type:<15} {r.confidence:<6.2f} {subject:<50} {r.reason or ''}"
        )

    ambiguous_count = sum(1 for r in results if r.is_ambiguous)
    typer.echo("")
    typer.echo(f"{ambiguous_count}/{len(results)} flagged ambiguous")


@app.command()
def digest(
    limit: int = typer.Option(
        50, "--limit", help="Max unread emails to fetch and classify. Use 0 for no limit."
    ),
) -> None:
    """Authenticate, fetch and classify unread email, persist the outcomes,
    and print a digest of only the ambiguous ones with their reasons.

    Stage 3 verification — fetch, classify, and digest end to end.
    """
    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        typer.echo("ANTHROPIC_API_KEY is not set — add it to .env", err=True)
        raise typer.Exit(1)

    creds = get_credentials()
    emails = fetch_unread_emails(creds, limit=limit or None)
    typer.echo(f"{len(emails)} unread email(s) fetched; classifying...")

    results = classify_emails(emails)

    conn = get_connection()
    try:
        ClassificationRepository(conn).save_all(results)
        digest_text = build_digest(conn)
    finally:
        conn.close()

    typer.echo("")
    typer.echo(digest_text)


@app.command()
def draft(
    limit: int = typer.Option(
        50, "--limit", help="Max unread emails to fetch and classify. Use 0 for no limit."
    ),
    message_id: str = typer.Option(
        None,
        "--message-id",
        help="Classify and draft only this specific Gmail message id, instead of the unread batch.",
    ),
    create_gmail_draft: bool = typer.Option(
        False,
        "--create-gmail-draft",
        help="Actually create each generated draft as a real Gmail draft (never sent). "
        "Opt-in per run -- without this flag, drafts are printed only.",
    ),
) -> None:
    """Authenticate, fetch and classify unread email (or a single message
    via --message-id), and generate draft replies for scheduling-type
    emails using sender history as context. Printed for manual review by
    default; pass --create-gmail-draft to also create each draft in
    Gmail (FR7) -- draft-only, this never sends anything.

    Stage 4/5 verification -- fetch, classify, draft, and (opt-in)
    create end to end.
    """
    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        typer.echo("ANTHROPIC_API_KEY is not set — add it to .env", err=True)
        raise typer.Exit(1)

    creds = get_credentials()
    if message_id:
        emails = [fetch_email_by_id(creds, message_id)]
    else:
        emails = fetch_unread_emails(creds, limit=limit or None)
    typer.echo(f"{len(emails)} email(s) fetched; classifying...")

    results = classify_emails(emails)
    emails_by_id = {email.id: email for email in emails}

    conn = get_connection()
    try:
        ClassificationRepository(conn).save_all(results)

        scheduling = [r for r in results if r.type == "scheduling"]
        typer.echo("")
        typer.echo(f"{len(scheduling)} scheduling email(s) found")

        draft_repo = DraftRepository(conn)
        for r in scheduling:
            email = emails_by_id[r.email_id]
            context = get_sender_context(conn, r.sender, exclude_email_id=email.id)
            body_text = fetch_email_body(creds, email.id) or email.snippet

            draft_result = generate_scheduling_draft(email, body_text, context)

            typer.echo("")
            typer.echo(f"--- {email.subject}  (from {email.sender}) ---")
            if draft_result is None:
                typer.echo("(draft generation failed -- see logs)")
                continue

            draft_row_id = draft_repo.save(email.id, draft_result.body)
            typer.echo(draft_result.body)

            if create_gmail_draft:
                gmail_draft_id = create_draft_reply(creds, email.id, draft_result.body)
                if gmail_draft_id is None:
                    typer.echo("(Gmail draft creation failed -- see logs)")
                    continue
                draft_repo.set_gmail_draft_id(draft_row_id, gmail_draft_id)
                typer.echo(f"[Gmail draft created: {gmail_draft_id}]")
    finally:
        conn.close()


if __name__ == "__main__":
    app()
