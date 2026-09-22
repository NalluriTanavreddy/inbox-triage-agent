"""Classifies unread email by urgency and type in a single batched call per
run, and flags low-confidence classifications as ambiguous with a stated
reason (FR3, FR4)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import anthropic

from classify.prompts import (
    CONFIDENCE_BAND_SCORES,
    ClassificationBatch,
    EmailClassification,
    EmailType,
    SYSTEM_PROMPT,
    build_batch_prompt,
)
from gmail.client import UnreadEmail, normalize_sender_email

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-5"

# Anything below this gets force-flagged ambiguous regardless of what the
# model itself reported -- the model's confidence self-report isn't
# trusted as the sole gate (FR4).
CONFIDENCE_THRESHOLD = 0.7

# 8192 silently truncated the JSON response (and dropped the whole
# batch to all-ambiguous, since a cut-off response fails schema
# validation) at roughly 200 emails per batch -- found while sampling
# a larger batch for stage 4 verification. Raised with headroom for a
# few hundred emails. Anthropic's non-streaming client refuses a
# max_tokens high enough to risk a >10-minute generation (hit at
# 32000), so batches too large for this should switch to streaming
# rather than raising this further.
_MAX_OUTPUT_TOKENS = 20000


@dataclass
class Classification:
    email_id: str
    subject: str
    sender: str
    snippet: str
    urgency: str
    type: str
    confidence: float
    is_ambiguous: bool
    reason: str | None


def classify_emails(
    emails: list[UnreadEmail], client: anthropic.Anthropic | None = None
) -> list[Classification]:
    """Classify a batch of emails in a single Claude API call -- never one
    call per email, which would hit the same kind of quota wall the Gmail
    per-message fetch did in stage 1, just against the Anthropic API."""
    if not emails:
        return []

    client = client or anthropic.Anthropic()
    by_id = {email.id: email for email in emails}

    try:
        response = client.messages.parse(
            model=MODEL,
            max_tokens=_MAX_OUTPUT_TOKENS,
            # Classification is a mechanical per-email judgment call, not
            # a task that benefits from extended reasoning -- disabling
            # thinking keeps this well within the BRD's negligible-cost NFR.
            thinking={"type": "disabled"},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_batch_prompt(emails)}],
            output_format=ClassificationBatch,
        )
    except Exception:
        # A call-level failure (network, rate limit, auth) leaves us with
        # no per-email data at all, so the whole batch is the unit of
        # failure here -- unlike the per-item handling below, which
        # recovers individual emails without losing the rest of the batch.
        logger.exception(
            "Classification batch call failed for %d email(s); flagging all as ambiguous",
            len(emails),
        )
        return [_failed_classification(email) for email in emails]

    by_result_id: dict[str, Classification] = {}
    for item in response.parsed_output.classifications:
        email = by_id.get(item.id)
        if email is None:
            logger.warning("Classification returned unknown email id %r; ignoring", item.id)
            continue
        by_result_id[item.id] = _to_classification(email, item)

    results = []
    for email in emails:
        if email.id in by_result_id:
            results.append(by_result_id[email.id])
        else:
            logger.warning(
                "No classification returned for %r (%s); marking ambiguous",
                email.id,
                email.subject,
            )
            results.append(_failed_classification(email))
    return results


def _to_classification(email: UnreadEmail, item: EmailClassification) -> Classification:
    confidence = CONFIDENCE_BAND_SCORES[item.confidence]
    is_ambiguous = item.type == EmailType.AMBIGUOUS or confidence < CONFIDENCE_THRESHOLD
    reason = item.reason
    if is_ambiguous and not reason:
        reason = (
            "low confidence"
            if confidence < CONFIDENCE_THRESHOLD
            else "model flagged as ambiguous"
        )
    return Classification(
        email_id=email.id,
        subject=email.subject,
        sender=normalize_sender_email(email.sender),
        snippet=email.snippet,
        urgency=item.urgency.value,
        type=item.type.value,
        confidence=confidence,
        is_ambiguous=is_ambiguous,
        reason=reason if is_ambiguous else None,
    )


def _failed_classification(email: UnreadEmail) -> Classification:
    return Classification(
        email_id=email.id,
        subject=email.subject,
        sender=normalize_sender_email(email.sender),
        snippet=email.snippet,
        urgency="unknown",
        type="ambiguous",
        confidence=0.0,
        is_ambiguous=True,
        reason="classification failed",
    )
