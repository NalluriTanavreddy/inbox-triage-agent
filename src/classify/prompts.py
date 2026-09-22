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


class ConfidenceBand(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EmailClassification(BaseModel):
    id: str
    urgency: Urgency
    type: EmailType
    confidence: ConfidenceBand
    reason: str | None = None


class ClassificationBatch(BaseModel):
    classifications: list[EmailClassification]


# A continuous 0.0-1.0 confidence score let the model invent an arbitrary
# decimal each call, which showed measurable boundary flakiness on
# identical input (see BRD Section 12) -- picking from three named bands
# gives it a much smaller, more stable decision to make. The bands map to
# fixed numeric values here, in code, so downstream logic (the 0.7
# threshold, storage, CLI display) is unchanged.
CONFIDENCE_BAND_SCORES = {
    ConfidenceBand.HIGH: 0.9,
    ConfidenceBand.MEDIUM: 0.6,
    ConfidenceBand.LOW: 0.3,
}

SYSTEM_PROMPT = """You triage a personal email inbox. For each email in the
batch, classify:

- urgency: "today" (needs a response today), "this_week" (can wait a few
  days), or "no_response_needed" (informational, no reply expected)
- type: "scheduling" (meeting/event coordination), "informational" (FYI,
  no action needed), "request" (asks the recipient to do something),
  "spam_newsletter" (bulk/promotional/automated mail), or "ambiguous" (you
  genuinely can't tell from the subject/sender/snippet alone)
- confidence: pick exactly one band, using this rubric --
  "high": the type and urgency are unambiguous from the subject/sender/
    snippet alone -- an obvious promotional blast, an obvious automated
    receipt, an obvious calendar invite. No competing interpretation
    exists.
  "medium": a reasonable classification follows from the available
    signals, but it took judgment -- e.g. the subject is generic or
    templated, or urgency isn't stated and has to be inferred.
  "low": at least two classifications are both plausible given what's
    provided -- e.g. the snippet is too sparse to distinguish between
    types, or the wording imitates a different category on purpose (a
    marketing email styled as a security alert, a newsletter styled as
    a personal note).
- reason: a one-line explanation of why the email is hard to classify --
  required (non-null) whenever type is "ambiguous" or confidence is
  "medium" or "low"; otherwise omit it (null)

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
