"""Typer-based CLI entry point for running the agent end to end: fetch,
classify, draft/digest (BRD section 10, MVP CLI layer)."""

import os
import sys

from dotenv import load_dotenv
import typer

from classify.classifier import classify_emails
from gmail.auth import get_credentials
from gmail.client import fetch_unread_emails

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


if __name__ == "__main__":
    app()
