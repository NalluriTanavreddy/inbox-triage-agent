"""Gmail API client for fetching unread email, handling pagination for
inboxes with more than 50 unread messages (FR2)."""

from __future__ import annotations

import base64
import logging
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.utils import parseaddr

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)


@dataclass
class UnreadEmail:
    """Minimal per-email shape — just enough for stage 2 classification to
    consume; nothing more yet."""

    id: str
    thread_id: str
    subject: str
    sender: str
    snippet: str
    received_at: str


# Retries transient failures with exponential backoff, including the
# rateLimitExceeded 403s that "Units per minute per user" quota bursts
# straight into when fetching many messages one-by-one in quick succession.
_API_RETRIES = 5

# A small proactive delay between per-message calls, so the request rate
# stays under the quota ceiling instead of bursting into it and relying on
# backoff to recover — cheaper and more predictable at scale.
_REQUEST_DELAY_SECONDS = 0.1
_PROGRESS_INTERVAL = 250


def fetch_unread_emails(creds: Credentials, limit: int | None = None) -> list[UnreadEmail]:
    """Fetch unread email, paginating through the message list rather than
    assuming it fits in one API response.

    With `limit` set, stops paginating as soon as that many message IDs are
    collected instead of walking every page — the default caller (the CLI)
    passes a small limit so a routine run doesn't fetch an entire, possibly
    huge, unread backlog.
    """
    service = build("gmail", "v1", credentials=creds)

    message_ids: list[str] = []
    page_token: str | None = None
    while True:
        response = (
            service.users()
            .messages()
            .list(userId="me", labelIds=["INBOX", "UNREAD"], pageToken=page_token)
            .execute(num_retries=_API_RETRIES)
        )
        message_ids.extend(m["id"] for m in response.get("messages", []))
        if limit is not None and len(message_ids) >= limit:
            message_ids = message_ids[:limit]
            break
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    emails: list[UnreadEmail] = []
    total = len(message_ids)
    for i, message_id in enumerate(message_ids, start=1):
        emails.append(_fetch_email_summary(service, message_id))
        if i % _PROGRESS_INTERVAL == 0 or i == total:
            print(f"...fetched {i}/{total}", file=sys.stderr)
        time.sleep(_REQUEST_DELAY_SECONDS)

    return emails


def fetch_email_by_id(creds: Credentials, message_id: str) -> UnreadEmail:
    """Fetch a single email's summary by its Gmail message id, regardless
    of unread/label status -- for targeting one specific message (e.g.
    manual verification) instead of the unread batch."""
    service = build("gmail", "v1", credentials=creds)
    return _fetch_email_summary(service, message_id)


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
        .execute(num_retries=_API_RETRIES)
    )
    headers = {h["name"]: h["value"] for h in message["payload"]["headers"]}

    return UnreadEmail(
        id=message["id"],
        thread_id=message["threadId"],
        subject=headers.get("Subject", "(no subject)"),
        sender=headers.get("From", "(unknown sender)"),
        snippet=message.get("snippet", ""),
        received_at=_internal_date_to_iso(message["internalDate"]),
    )


def _internal_date_to_iso(internal_date: str) -> str:
    """Gmail's internalDate (epoch ms) is a more reliable timestamp source
    than the From/Date header, which senders format inconsistently."""
    return datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc).isoformat()


_EMAIL_ADDRESS_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def normalize_sender_email(raw_sender: str) -> str:
    """Extract and lowercase just the email address from a raw "From"
    header (e.g. "Jane Doe <jane@example.com>" -> "jane@example.com"), so
    sender-history lookups (FR5) key on the address rather than on
    display-name formatting, which can vary per message."""
    match = _EMAIL_ADDRESS_RE.search(raw_sender)
    return (match.group(0) if match else raw_sender.strip()).lower()


def fetch_email_body(creds: Credentials, message_id: str) -> str:
    """Fetch one email's plain-text body.

    Stage 1's bulk fetch only pulls headers and Gmail's short snippet
    (~100 chars) -- enough for classification, not enough to draft a
    genuinely useful reply (FR5). Fetched lazily, one message at a time,
    only for emails actually being drafted, so the cheap bulk path stays
    cheap.
    """
    service = build("gmail", "v1", credentials=creds)
    message = (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute(num_retries=_API_RETRIES)
    )
    return _extract_plain_text(message["payload"])


def _extract_plain_text(payload: dict) -> str:
    plain = _find_body_by_mime_type(payload, "text/plain")
    if plain:
        return plain
    # No text/plain part anywhere in the tree -- fall back to text/html,
    # stripped of markup, rather than returning nothing.
    html = _find_body_by_mime_type(payload, "text/html")
    return re.sub(r"<[^>]+>", " ", html) if html else ""


def _find_body_by_mime_type(payload: dict, mime_type: str) -> str:
    if payload.get("mimeType") == mime_type and payload.get("body", {}).get("data"):
        return _decode_body(payload["body"]["data"])
    for part in payload.get("parts") or []:
        found = _find_body_by_mime_type(part, mime_type)
        if found:
            return found
    return ""


def _decode_body(data: str) -> str:
    # Gmail encodes body data as URL-safe base64 without padding.
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def create_draft_reply(creds: Credentials, message_id: str, body_text: str) -> str | None:
    """Create a Gmail draft replying to `message_id` -- never sends
    (FR7; requires gmail.compose, never gmail.send). Returns the created
    draft's id, or None if creation failed.

    For Gmail to thread this as a reply in the same conversation rather
    than a standalone new email, three things all have to hold: the
    message carries the same threadId, the Subject matches the
    original (a leading "Re: " is fine), and the In-Reply-To/References
    headers name the original's RFC 2822 Message-ID -- Gmail checks all
    three, not just threadId.
    """
    service = build("gmail", "v1", credentials=creds)

    try:
        original = (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute(num_retries=_API_RETRIES)
        )
        headers = {h["name"]: h["value"] for h in original["payload"]["headers"]}

        original_message_id = headers.get("Message-ID") or headers.get("Message-Id", "")
        original_subject = headers.get("Subject", "")
        reply_subject = (
            original_subject
            if original_subject.lower().startswith("re:")
            else f"Re: {original_subject}"
        )
        reply_to_address = parseaddr(headers.get("From", ""))[1]

        mime_message = MIMEText(body_text)
        mime_message["To"] = reply_to_address
        mime_message["Subject"] = reply_subject
        if original_message_id:
            mime_message["In-Reply-To"] = original_message_id
            references = " ".join(
                part
                for part in (headers.get("References", ""), original_message_id)
                if part
            )
            mime_message["References"] = references

        raw = base64.urlsafe_b64encode(mime_message.as_bytes()).decode("ascii")

        draft = (
            service.users()
            .drafts()
            .create(
                userId="me",
                body={"message": {"raw": raw, "threadId": original["threadId"]}},
            )
            .execute(num_retries=_API_RETRIES)
        )
    except Exception:
        logger.exception("Gmail draft creation failed for reply to %r", message_id)
        return None

    return draft["id"]
