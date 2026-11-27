"""Pre-routing PII Scrubber service (Stage 0).

Detects and masks sensitive entity types (Email, Phone, Credit Card with Luhn
validation, US SSN, and IBAN with ISO 13616 MOD-97 validation) prior to cache
key generation, vectorization, logging, or upstream LLM transmission.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from app.core.config import config
from app.modules.pii_scrubber.pii_scrubber_schema import PiiScrubbingResult

if TYPE_CHECKING:
    from app.modules.chat.chat_schema import ChatCompletionRequest, ChatMessage

logger = logging.getLogger(__name__)

VALID_PII_ENTITY_TYPES: frozenset[str] = frozenset(
    {"email", "phone", "credit_card", "ssn", "iban"}
)

# Order matters: more specific structured identifiers (email, credit card, SSN,
# IBAN) run before the broader phone pattern so they are never partially masked.
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# 13-19 digits allowing optional spaces or hyphens between digit groups.
_CREDIT_CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")

# US SSN (XXX-XX-XXXX or XXX XX XXXX) excluding invalid SSA area/group/serial numbers.
_SSN_RE = re.compile(r"\b(?!000|666|9\d{2})\d{3}[-\s](?!00)\d{2}[-\s](?!0000)\d{4}\b")

# IBAN: 2-letter country code + 2 check digits + 11-30 alphanumeric BBAN chars
# (with optional single spaces between 4-character blocks).
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b")

# Phone numbers: supports +country code, parenthesized area code, or separated
# 10+ digit sequences while avoiding plain dates (YYYY-MM-DD) or IP addresses.
_PHONE_RE = re.compile(
    r"(?<!\w)(?:"
    r"\+\d{1,3}[-.\s]?(?:\(\d{1,4}\)[-.\s]?)?(?:\d[-.\s]?){6,13}\d"
    r"|"
    r"\(\d{3}\)[-.\s]?\d{3}[-.\s]?\d{4}"
    r"|"
    r"(?<!\d\.)\d{3}[-.\s]\d{3}[-.\s]\d{4}(?!\.\d)"
    r")(?!\w)"
)


def is_valid_luhn(number: str) -> bool:
    """Validate a candidate payment card number using the Luhn checksum."""
    digits = [int(ch) for ch in number if ch.isdigit()]
    if not (13 <= len(digits) <= 19):
        return False
    # Reject all-zero sequences which trivially pass mod-10.
    if all(d == 0 for d in digits):
        return False

    checksum = 0
    parity = len(digits) % 2
    for idx, digit in enumerate(digits):
        if idx % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def is_valid_iban(candidate: str) -> bool:
    """Validate an IBAN candidate using ISO 13616 length and MOD-97-10 checksum."""
    cleaned = re.sub(r"\s+", "", candidate).upper()
    if not (15 <= len(cleaned) <= 34):
        return False
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", cleaned):
        return False

    rearranged = cleaned[4:] + cleaned[:4]
    numeric_parts: list[str] = []
    for ch in rearranged:
        if ch.isdigit():
            numeric_parts.append(ch)
        else:
            numeric_parts.append(str(ord(ch) - ord("A") + 10))
    return int("".join(numeric_parts)) % 97 == 1


class PiiScrubberService:
    """Sub-millisecond regex + checksum PII scrubber for Stage 0."""

    @staticmethod
    def should_scrub(request: ChatCompletionRequest) -> bool:
        """Resolve whether PII scrubbing applies to ``request``."""
        explicit = getattr(request, "redact_pii", None)
        if explicit is not None:
            return bool(explicit)
        return bool(getattr(config, "PII_SCRUBBING_ENABLED", False))

    @staticmethod
    def enabled_entities() -> frozenset[str]:
        configured = getattr(config, "PII_SCRUBBING_ENTITIES", None)
        if configured:
            return frozenset(configured)
        return VALID_PII_ENTITY_TYPES

    def scrub_text(
        self,
        text: str,
        *,
        entities: frozenset[str] | None = None,
    ) -> tuple[str, dict[str, int]]:
        """Mask detected PII entities in ``text`` and return ``(masked, counts)``."""
        if not text:
            return text, {}

        active = entities if entities is not None else self.enabled_entities()
        counts: dict[str, int] = {}
        current = text

        def _record(kind: str) -> None:
            counts[kind] = counts.get(kind, 0) + 1

        if "email" in active:

            def _replace_email(_match: re.Match[str]) -> str:
                _record("email")
                return "[EMAIL]"

            current = _EMAIL_RE.sub(_replace_email, current)

        if "credit_card" in active:

            def _replace_cc(match: re.Match[str]) -> str:
                raw = match.group(0)
                if is_valid_luhn(raw):
                    _record("credit_card")
                    return "[CREDIT_CARD]"
                return raw

            current = _CREDIT_CARD_RE.sub(_replace_cc, current)

        if "ssn" in active:

            def _replace_ssn(_match: re.Match[str]) -> str:
                _record("ssn")
                return "[SSN]"

            current = _SSN_RE.sub(_replace_ssn, current)

        if "iban" in active:

            def _replace_iban(match: re.Match[str]) -> str:
                raw = match.group(0)
                if is_valid_iban(raw):
                    _record("iban")
                    return "[IBAN]"
                return raw

            current = _IBAN_RE.sub(_replace_iban, current)

        if "phone" in active:

            def _replace_phone(match: re.Match[str]) -> str:
                raw = match.group(0)
                digits = [ch for ch in raw if ch.isdigit()]
                if 7 <= len(digits) <= 15:
                    _record("phone")
                    return "[PHONE]"
                return raw

            current = _PHONE_RE.sub(_replace_phone, current)

        return current, counts

    def scrub_request(
        self, request: ChatCompletionRequest
    ) -> tuple[ChatCompletionRequest, PiiScrubbingResult]:
        """Return a scrubbed copy of ``request`` (if enabled) and redaction summary."""
        if not self.should_scrub(request):
            return request, PiiScrubbingResult(applied=False)

        active = self.enabled_entities()
        aggregate_counts: dict[str, int] = {}
        any_changed = False

        def _merge(counts: dict[str, int]) -> None:
            for k, v in counts.items():
                aggregate_counts[k] = aggregate_counts.get(k, 0) + v

        scrubbed_messages: list[ChatMessage] = []
        for msg in request.messages:
            msg_copy = msg.model_copy(deep=True)
            if msg.content_parts is not None:
                new_parts = []
                for part in msg.content_parts:
                    part_copy = dict(part)
                    if part_copy.get("type") == "text" and isinstance(
                        part_copy.get("text"), str
                    ):
                        masked_part, part_counts = self.scrub_text(
                            part_copy["text"], entities=active
                        )
                        if part_counts:
                            any_changed = True
                            _merge(part_counts)
                        part_copy["text"] = masked_part
                    new_parts.append(part_copy)
                msg_copy.content_parts = new_parts
            if msg.cache_control is not None:
                msg_copy.cache_control = dict(msg.cache_control)

            if msg_copy.content:
                masked_content, content_counts = self.scrub_text(
                    msg_copy.content, entities=active
                )
                if content_counts:
                    any_changed = True
                    _merge(content_counts)
                msg_copy.content = masked_content

            if msg_copy.tool_calls:
                for tc in msg_copy.tool_calls:
                    if tc.function and tc.function.arguments:
                        masked_args, arg_counts = self.scrub_text(
                            tc.function.arguments, entities=active
                        )
                        if arg_counts:
                            any_changed = True
                            _merge(arg_counts)
                        tc.function.arguments = masked_args

            scrubbed_messages.append(msg_copy)

        if not any_changed:
            return request, PiiScrubbingResult(applied=False)

        scrubbed_req = request.model_copy(update={"messages": scrubbed_messages})
        # Preserve private attributes dropped or isolated during model_copy(update=...).
        scrubbed_req.tenant_id = request.tenant_id
        scrubbed_req.user_id = request.user_id
        scrubbed_req.allowed_providers = request.allowed_providers

        result = PiiScrubbingResult(applied=True, entity_counts=aggregate_counts)
        logger.info(
            "Pre-routing PII scrubber redacted %d entity(s): %s",
            result.total_redactions,
            result.to_header_value(),
        )
        return scrubbed_req, result
