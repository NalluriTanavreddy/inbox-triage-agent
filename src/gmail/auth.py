"""OAuth2 authentication against the Gmail API.

Starts with readonly scope (FR1); gmail.compose is added later, once draft
creation logic is verified, and gmail.send is never requested (BRD section
12, "OAuth scope creep").
"""

from __future__ import annotations

import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
TOKEN_PATH = Path("token.json")


def get_credentials() -> Credentials:
    """Return cached, valid Gmail credentials — refreshing or running the
    local OAuth2 flow as needed — so a run doesn't require re-authenticating
    every time."""
    creds: Credentials | None = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        secrets_path = os.environ.get("GOOGLE_CLIENT_SECRETS_PATH")
        if not secrets_path:
            raise RuntimeError(
                "GOOGLE_CLIENT_SECRETS_PATH is not set — add it to .env "
                "(see .env.example)"
            )
        flow = InstalledAppFlow.from_client_secrets_file(secrets_path, SCOPES)
        creds = flow.run_local_server(port=0)

    TOKEN_PATH.write_text(creds.to_json())
    return creds
