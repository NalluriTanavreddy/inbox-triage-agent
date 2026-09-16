"""Typer-based CLI entry point for running the agent end to end: fetch,
classify, draft/digest (BRD section 10, MVP CLI layer)."""

import sys

from dotenv import load_dotenv
import typer

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


if __name__ == "__main__":
    app()
