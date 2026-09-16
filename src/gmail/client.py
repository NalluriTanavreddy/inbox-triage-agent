"""Gmail API client for fetching unread email, handling pagination for
inboxes with more than 50 unread messages (FR2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


@dataclass
class UnreadEmail:
    """Minimal per-email shape — just enough for stage 2 classification to
    consume; nothing more yet."""

    id: str
    subject: str
    sender: str
    snippet: str
    received_at: str


def fetch_unread_emails(creds: Credentials) -> list[UnreadEmail]:
    """Fetch every unread email in the inbox, paginating through the message
    list rather than assuming it fits in one API response."""
    service = build("gmail", "v1", credentials=creds)

    message_ids: list[str] = []
    page_token: str | None = None
    while True:
        response = (
            service.users()
            .messages()
            .list(userId="me", labelIds=["INBOX", "UNREAD"], pageToken=page_token)
            .execute()
        )
        message_ids.extend(m["id"] for m in response.get("messages", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return [_fetch_email_summary(service, message_id) for message_id in message_ids]


def _fetch_email_summary(service, message_id: str) -> UnreadEmail:
    message = (
        service.users()
        .messages()
        .get(
            userId="me",
            id=message_id,
            format="metadata",
            metadataHeaders=["Subject", "From"],
        )
        .execute()
    )
    headers = {h["name"]: h["value"] for h in message["payload"]["headers"]}

    return UnreadEmail(
        id=message["id"],
        subject=headers.get("Subject", "(no subject)"),
        sender=headers.get("From", "(unknown sender)"),
        snippet=message.get("snippet", ""),
        received_at=_internal_date_to_iso(message["internalDate"]),
    )


def _internal_date_to_iso(internal_date: str) -> str:
    """Gmail's internalDate (epoch ms) is a more reliable timestamp source
    than the From/Date header, which senders format inconsistently."""
    return datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc).isoformat()
