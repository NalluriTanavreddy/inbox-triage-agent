"""Typer-based CLI entry point for running the agent end to end: fetch,
classify, draft/digest (BRD section 10, MVP CLI layer)."""

from dotenv import load_dotenv
import typer

from gmail.auth import get_credentials
from gmail.client import fetch_unread_emails

app = typer.Typer()


@app.command()
def fetch_unread() -> None:
    """Authenticate, fetch unread inbox emails, and print a quick summary.

    Stage 1 verification only — no classification yet.
    """
    load_dotenv()
    creds = get_credentials()
    emails = fetch_unread_emails(creds)

    typer.echo(f"{len(emails)} unread email(s)")
    for email in emails:
        typer.echo(f"- {email.subject}")


if __name__ == "__main__":
    app()
