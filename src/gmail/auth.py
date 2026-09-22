"""OAuth2 authentication against the Gmail API.

Started with readonly scope only (FR1). gmail.compose is added here in
stage 5, now that drafting logic (stage 4) is verified -- gmail.send is
never requested, and never will be (BRD section 12, "OAuth scope
creep"; FR7 is explicitly draft-only).
"""

from __future__ import annotations

import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]
TOKEN_PATH = Path("token.json")


def get_credentials() -> Credentials:
    """Return cached, valid Gmail credentials — refreshing or running the
    local OAuth2 flow as needed — so a run doesn't require re-authenticating
    every time.

    Also re-runs the OAuth flow if the cached token's granted scopes
    don't cover everything currently requested (e.g. after adding
    gmail.compose in stage 5) -- otherwise a valid-but-under-scoped
    token would be returned silently, and the first API call that
    actually needs the missing scope would fail later, at call time,
    with an unhelpful 403 rather than here.
    """
    creds: Credentials | None = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    has_required_scopes = bool(creds and creds.scopes and set(SCOPES) <= set(creds.scopes))

    if creds and creds.valid and has_required_scopes:
        return creds

    if creds and creds.expired and creds.refresh_token and has_required_scopes:
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
