"""OAuth2 authentication against the Gmail API.

Starts with readonly scope (FR1); gmail.compose is added later, once draft
creation logic is verified, and gmail.send is never requested (BRD section
12, "OAuth scope creep").
"""
