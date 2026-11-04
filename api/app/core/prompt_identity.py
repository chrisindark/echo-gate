"""Prompt identity helpers shared by usage logging and analytics.

Usage rows carry a ``prompt_hash``: a SHA-256 over the *system* prompt only.
A system prompt usually encodes the downstream application or workflow, so
grouping usage by this hash yields per-application cost attribution without
requiring callers to tag themselves. The hash is opaque, so no prompt text is
duplicated into the usage table.

This module owns both directions of the encoding used by
``LlmRouterService._prepare_prompts`` so write-time hashing and the historical
backfill cannot drift apart:

* messages are encoded as ``<role>: <content>`` segments joined by
  :data:`PROMPT_SEGMENT_SEPARATOR`;
* the whole prompt is prefixed with ``model:<model>|``.
"""

import hashlib

PROMPT_SEGMENT_SEPARATOR = "|\n"
"""Joiner between encoded messages in ``llm_usage_logs.input_text``."""

PROMPT_MODEL_PREFIX = "model:"
"""Prefix of the leading model segment in an encoded prompt."""

_KNOWN_ROLES = ("system", "user", "assistant", "tool", "function")


def compute_prompt_hash(system_prompt: str | None) -> str | None:
    """SHA-256 (hex) of the system prompt, or ``None`` when there is none.

    Whitespace is collapsed and stripped first so incidental formatting does not
    split one application into two attribution buckets.
    """
    if not system_prompt:
        return None
    normalized = " ".join(system_prompt.split())
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def system_prompt_from_prompt_text(prompt_text: str | None) -> str | None:
    """Recover the system prompt from an encoded ``input_text`` value.

    Returns ``None`` when the text has no system message, or when the encoding is
    ambiguous. Message content is free-form, so content containing
    :data:`PROMPT_SEGMENT_SEPARATOR` fabricates extra segments; a segment that
    does not begin with a known role prefix is treated as exactly that and the
    row is skipped, because a wrong attribution is worse than a missing one.
    """
    if not prompt_text:
        return None

    text = prompt_text
    if text.startswith(PROMPT_MODEL_PREFIX):
        # The model prefix is glued to the first message with a bare "|"
        # (`model:<model>|<role>: <content>`), so drop it before splitting.
        _, separator, text = text.partition("|")
        if not separator:
            return None

    segments = text.split(PROMPT_SEGMENT_SEPARATOR)
    system_parts: list[str] = []
    for segment in segments:
        role, separator, content = segment.partition(": ")
        if not separator or role not in _KNOWN_ROLES:
            return None
        if role == "system":
            system_parts.append(content)

    if not system_parts:
        return None
    return "\n".join(system_parts)
