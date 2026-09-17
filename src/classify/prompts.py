"""Prompt template and response schema for the batched urgency/type
classification call (FR3). The schema is passed to the model as a
structured-output contract (see classifier.py's `output_format=`), so the
model's JSON is guaranteed to validate -- FR4's confidence/reason fields
are part of the same schema, not a separate pass."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from gmail.client import UnreadEmail


class Urgency(str, Enum):
    TODAY = "today"
    THIS_WEEK = "this_week"
    NO_RESPONSE_NEEDED = "no_response_needed"


class EmailType(str, Enum):
    SCHEDULING = "scheduling"
    INFORMATIONAL = "informational"
    REQUEST = "request"
    SPAM_NEWSLETTER = "spam_newsletter"
    AMBIGUOUS = "ambiguous"


class EmailClassification(BaseModel):
    id: str
    urgency: Urgency
    type: EmailType
    confidence: float
    reason: str | None = None


class ClassificationBatch(BaseModel):
    classifications: list[EmailClassification]


# Prompt-side guidance only. classifier.py enforces its own
# CONFIDENCE_THRESHOLD in code and doesn't trust the model to have
# followed this -- it's here so the model's own `reason` field lines up
# with the threshold that will actually be applied.
_PROMPT_CONFIDENCE_HINT = 0.7

SYSTEM_PROMPT = f"""You triage a personal email inbox. For each email in the
batch, classify:

- urgency: "today" (needs a response today), "this_week" (can wait a few
  days), or "no_response_needed" (informational, no reply expected)
- type: "scheduling" (meeting/event coordination), "informational" (FYI,
  no action needed), "request" (asks the recipient to do something),
  "spam_newsletter" (bulk/promotional/automated mail), or "ambiguous" (you
  genuinely can't tell from the subject/sender/snippet alone)
- confidence: your confidence in this classification, from 0.0 to 1.0
- reason: a one-line explanation of why the email is hard to classify --
  required (non-null) whenever type is "ambiguous" or confidence is below
  {_PROMPT_CONFIDENCE_HINT}; otherwise omit it (null)

Classify every email in the batch by its id and return exactly one
classification per email, using the same id given in the input. Respond
only with the structured JSON the response schema requires -- no
commentary."""


def build_batch_prompt(emails: list[UnreadEmail]) -> str:
    """Render the batch as the user turn: id, sender, subject, and
    snippet per email -- everything stage 1 gave us, nothing more."""
    lines = [f"Classify these {len(emails)} email(s):", ""]
    for email in emails:
        lines.append(f"id: {email.id}")
        lines.append(f"from: {email.sender}")
        lines.append(f"subject: {email.subject}")
        lines.append(f"snippet: {email.snippet}")
        lines.append("")
    return "\n".join(lines)
