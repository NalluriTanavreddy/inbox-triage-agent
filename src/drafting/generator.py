"""Generates draft replies for clearly routine categories, starting with
scheduling, using sender history/tone as context (FR5)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import anthropic

from drafting.context import SenderContext
from gmail.client import UnreadEmail

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-5"
_MAX_OUTPUT_TOKENS = 1024

SYSTEM_PROMPT = """You draft reply emails for a scheduling request in a
personal inbox. Write ONLY the reply body text -- no subject line, no
"Draft:" preamble, no commentary before or after.

Rules:
- If the sender proposes one specific time, confirm it clearly and
  positively.
- If the sender offers multiple time options and one of them clearly
  works, pick that one and confirm it directly -- optionally with a
  light "happy to adjust if that doesn't work" caveat. Don't
  mechanically re-list all their options back at them; picking one is
  the more natural reply a person would actually send.
- Only propose 2-3 concrete alternative times yourself when the ask is
  genuinely open-ended (no time or options given at all) or none of the
  sender's proposed times actually work -- and even then, never ask an
  open-ended "when works for you?"; the recipient should be able to
  reply with one word.
- Match tone (formal/casual, greeting and sign-off style) to the sender
  history you're given, if any. With no prior history, default to a
  brief, warm, professional tone.
- Sign off as "Tanav" -- don't invent a signature block or contact
  details.
- Keep it short: a few sentences, not a formal letter."""


@dataclass
class Draft:
    email_id: str
    body: str


def generate_scheduling_draft(
    email: UnreadEmail,
    body_text: str,
    context: SenderContext,
    client: anthropic.Anthropic | None = None,
) -> Draft | None:
    """Generate a draft reply for one scheduling-classified email.

    Returns None (not a placeholder) on any failure, so a broken draft
    is never silently shown as real output.
    """
    client = client or anthropic.Anthropic()

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=_MAX_OUTPUT_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_prompt(email, body_text, context)}],
        )
    except Exception:
        logger.exception("Draft generation failed for %r (%s)", email.id, email.subject)
        return None

    body = "".join(block.text for block in response.content if block.type == "text").strip()
    if not body:
        logger.warning("Draft generation returned empty text for %r (%s)", email.id, email.subject)
        return None

    return Draft(email_id=email.id, body=body)


def _build_prompt(email: UnreadEmail, body_text: str, context: SenderContext) -> str:
    lines = [
        f"From: {email.sender}",
        f"Subject: {email.subject}",
        "Message:",
        body_text.strip() or "(no body text available -- use the subject line alone)",
        "",
    ]
    if context.has_history:
        lines.append("Prior emails from this sender, most recent first (for tone only):")
        lines.extend(f"- {s}" for s in context.prior_snippets)
    else:
        lines.append("No prior emails from this sender on file.")
    lines.append("")
    lines.append("Write the reply.")
    return "\n".join(lines)
