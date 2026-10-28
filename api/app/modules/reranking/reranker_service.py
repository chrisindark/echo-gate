import logging
import re
from typing import Any

from app.core.config import config
from app.core.logger import log_latency
from app.modules.verifier.cross_encoder_service import (
    CrossEncoderService,
    InferenceUnavailable,
)
from app.modules.verifier.instruction_service import InstructionVerifier

logger = logging.getLogger(__name__)

_EMPTY_MODIFIERS = {"", "none", "null", "n/a", "undefined"}

# `prompt_text` is decorated as `model:<name>|<role>: <content>|...`; strip the
# model prefix so the cross-encoder never sees provider noise.
_MODEL_PREFIX_RE = re.compile(r"^model:[^|\n]*\|\s*", re.IGNORECASE)

# Token normalization for short LLM-extracted phrases: possessive "'s" removal
# and punctuation stripping so "France's capital city" and "France capital"
# compare on their meaningful tokens instead of apostrophes.
_POSSESSIVE_RE = re.compile(r"'s\b")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]")

# Entity types whose *value* conflicts are strong evidence the cached answer is
# about a different object (different file, version, amount, date, URL, secret).
_CRITICAL_ENTITY_PREFIXES = {
    "NUMBER",
    "VERSION",
    "CURRENCY",
    "DATE",
    "URL",
    "EMAIL",
    "UUID",
    "ACCOUNT_ID",
    "API_KEY",
}

# Metadata mismatch sensitivity: each semantically mismatched field costs 0.05,
# capped at 0.15 so noisy LLM extraction can never erase a strong match.
_METADATA_MISMATCH_UNIT = 0.05
_METADATA_MISMATCH_CAP = 0.15
_METADATA_MISMATCH_THRESHOLD = 0.5


def _normalize_modifier(value: Any) -> str:
    """Normalize string modifier or attribute, treating None, 'null', 'none', etc. as empty."""
    if value is None:
        return ""
    val_str = str(value).strip().lower()
    if val_str in _EMPTY_MODIFIERS:
        return ""
    return val_str


def _clean_prompt(text: str | None) -> str:
    """Strip the `model:<name>|` decoration from a prepared prompt string."""
    if not text:
        return ""
    return _MODEL_PREFIX_RE.sub("", text).strip()


def _tokenize(text: str) -> set[str]:
    """Lowercase, drop possessives/punctuation, and split into a token set."""
    normalized = _POSSESSIVE_RE.sub("", text.lower())
    normalized = _NON_ALNUM_RE.sub(" ", normalized)
    return {token for token in normalized.split() if token}


def _token_overlap(a: str, b: str) -> float:
    """Jaccard similarity over normalized tokens.

    Returns 1.0 when both sides are empty, 0.0 when exactly one side is empty,
    and the token-set Jaccard otherwise. Used instead of exact string equality
    so paraphrase variability in LLM-extracted metadata does not cause a hard
    mismatch.
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    set_a, set_b = _tokenize(a), _tokenize(b)
    union = set_a | set_b
    if not union:
        return 1.0
    return len(set_a & set_b) / len(union)


def _entity_type(tag: str) -> str:
    return tag.split(":", 1)[0].strip().upper()


def _entity_value(tag: str) -> str:
    parts = tag.split(":", 1)
    return parts[1].strip() if len(parts) > 1 else ""


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


class RerankerService:
    def __init__(
        self,
        cross_encoder_service: CrossEncoderService,
        instruction_service: InstructionVerifier,
    ):
        self.cross_encoder_service = cross_encoder_service
        self.instruction_service = instruction_service

    def verify(
        self, query: str, user_query: str | None, candidates: list[dict[str, Any]]
    ) -> list[float]:
        """Score candidates with the cross-encoder.

        Pairing is kept consistent across candidates: whenever both the request
        and the candidate expose a user segment, the cross-encoder compares
        user-to-user. Otherwise it falls back to the (decorated) full prompts,
        with the `model:` prefix stripped.
        """
        cleaned_query = _clean_prompt(query)
        normalized_user_query = _clean_prompt(user_query)

        pairs: list[tuple[str, str]] = []
        for candidate in candidates:
            payload = candidate.get("payload") or {}
            candidate_user_prompt = _clean_prompt(payload.get("user_prompt"))
            candidate_full_prompt = _clean_prompt(payload.get("prompt"))

            if normalized_user_query and candidate_user_prompt:
                pairs.append((normalized_user_query, candidate_user_prompt))
            else:
                fallback = candidate_full_prompt or candidate_user_prompt
                if not fallback:
                    logger.warning(
                        f"Candidate payload missing prompt: {candidate.get('id', 'unknown')}"
                    )
                pairs.append((cleaned_query, fallback))

        try:
            return self.cross_encoder_service.rerank_predict(pairs)
        except InferenceUnavailable as exc:
            # Empty scores is the fail-open signal `rerank()` already handles:
            # the semantic path yields no candidates and the request goes to the
            # provider instead of failing.
            logger.warning(
                "reranker unavailable, skipping semantic candidates: %s", exc
            )
            return []

    def _metadata_similarity(
        self,
        request_core_operation: str | None,
        request_core_subject: str | None,
        request_subject_modifier: str | None,
        request_action_modifier: str | None,
        payload: dict[str, Any],
    ) -> tuple[float, list[str], dict[str, Any]]:
        """Return (factor, mismatched_fields, comparisons) for extracted metadata.

        A field is only judged when *both* sides carry a value: a missing request
        field means the extractor did not find it, and a missing candidate field
        may be a legacy point stored before metadata extraction existed. Neither
        is evidence of a real mismatch, so neither is penalized. Comparison is
        token-overlap based, not exact-equality, so paraphrase drift in the
        extractor does not register as a mismatch.

        `comparisons` records, per field, the two raw values and the overlap that
        drove the decision (including why an undecided field was skipped). This
        is emitted in `score_breakdown.details` so the live calibration pass can
        see exactly what a near-boundary rejection was based on; without the
        candidate-side value the decision is not reproducible from the trace.
        """
        fields = (
            ("core_operation", request_core_operation),
            ("core_subject", request_core_subject),
            ("subject_modifier", request_subject_modifier),
            ("action_modifier", request_action_modifier),
        )
        mismatches: list[str] = []
        comparisons: dict[str, Any] = {}
        for field, request_value in fields:
            normalized_request = _normalize_modifier(request_value)
            if not normalized_request:
                comparisons[field] = {"judged": False, "reason": "request_empty"}
                continue
            candidate_value = payload.get(field)
            normalized_candidate = _normalize_modifier(candidate_value)
            if not normalized_candidate:
                # Candidate predates this field (e.g. legacy point); cannot judge.
                comparisons[field] = {
                    "judged": False,
                    "reason": "candidate_empty",
                    "request": request_value,
                }
                continue
            overlap = _token_overlap(normalized_request, normalized_candidate)
            is_mismatch = overlap < _METADATA_MISMATCH_THRESHOLD
            comparisons[field] = {
                "judged": True,
                "mismatch": is_mismatch,
                "overlap": round(overlap, 4),
                "request": request_value,
                "candidate": candidate_value,
            }
            if is_mismatch:
                mismatches.append(field)

        penalty = min(_METADATA_MISMATCH_CAP, _METADATA_MISMATCH_UNIT * len(mismatches))
        return 1.0 - penalty, mismatches, comparisons

    def _entity_similarity(
        self, request_entity_set: set[str], candidate_entity_set: set[str]
    ) -> tuple[float, bool, float]:
        """Return (factor, conflict, overlap) for the entity signals."""
        if not request_entity_set and not candidate_entity_set:
            return 1.0, False, 1.0

        intersection = request_entity_set & candidate_entity_set
        union = request_entity_set | candidate_entity_set
        overlap = len(intersection) / len(union) if union else 1.0

        request_by_type: dict[str, set[str]] = {}
        for tag in request_entity_set:
            request_by_type.setdefault(_entity_type(tag), set()).add(_entity_value(tag))
        candidate_by_type: dict[str, set[str]] = {}
        for tag in candidate_entity_set:
            candidate_by_type.setdefault(_entity_type(tag), set()).add(
                _entity_value(tag)
            )

        conflict = False
        for entity_type, request_values in request_by_type.items():
            if entity_type not in _CRITICAL_ENTITY_PREFIXES:
                continue
            candidate_values = candidate_by_type.get(entity_type)
            if candidate_values is not None and not (request_values & candidate_values):
                conflict = True
                break

        if conflict:
            return 0.85, True, overlap

        # Bounded soft signal: never worse than 0.95, never better than 1.05.
        return 0.95 + 0.10 * overlap, False, overlap

    def _system_prompt_similarity(
        self, request_system_prompt: str | None, candidate_system_prompt: Any
    ) -> tuple[float, float]:
        """Return (factor, jaccard) for the system prompt signal.

        Only compared when both sides carry a system prompt. A candidate without
        one is usually a legacy point stored before the system prompt was
        persisted, so it is treated as "cannot judge" rather than a mismatch.
        """
        if request_system_prompt is None and candidate_system_prompt is None:
            return 1.0, 1.0

        request_norm = (request_system_prompt or "").strip().lower()
        candidate_norm = str(candidate_system_prompt or "").strip().lower()

        if request_norm == candidate_norm:
            return 1.0, 1.0
        if not request_norm or not candidate_norm:
            return 1.0, 0.0

        jaccard = _token_overlap(request_norm, candidate_norm)
        factor = 1.0 - min(0.10, 0.10 * (1.0 - jaccard))
        return factor, jaccard

    @log_latency()
    def rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        request_model: str | None = None,
        request_service: str | None = None,
        request_intent: str | None = None,
        request_temperature: float = 0.0,
        request_entities: list[str] | None = None,
        request_core_operation: str | None = None,
        request_core_subject: str | None = None,
        request_subject_modifier: str | None = None,
        request_action_modifier: str | None = None,
        user_query: str | None = None,
        system_query: str | None = None,
    ) -> list[dict[str, Any]]:
        logger.debug(f"request model: {request_model}")
        logger.debug(f"request service: {request_service}")
        logger.debug(f"request intent: {request_intent}")
        logger.debug(f"request temperature: {request_temperature}")
        logger.debug(f"request entities: {request_entities}")
        logger.debug(f"request core operation: {request_core_operation}")
        logger.debug(f"request core subject: {request_core_subject}")
        logger.debug(f"request subject modifier: {request_subject_modifier}")
        logger.debug(f"request action modifier: {request_action_modifier}")
        logger.debug(f"user query: {user_query}")
        logger.debug(f"system query: {system_query}")
        try:
            if not candidates:
                return []

            scoring_mode = (config.RERANKER_SCORING_MODE or "multiplicative").lower()
            request_entity_set = set(request_entities or [])
            req_temp = request_temperature if request_temperature is not None else 0.0

            scores = self.verify(query, user_query, candidates)
            logger.debug(f"reranker_scores: {scores}")
            if not scores:
                return []

            reranked = []

            for candidate, score in zip(candidates, scores, strict=False):
                try:
                    try:
                        c_text = (
                            candidate.get("payload", {})
                            .get("response", {})
                            .get("choices", [])[0]
                            .get("message", {})
                            .get("content", "")
                        )
                    except Exception:
                        logger.error(
                            f"Failed to extract response from candidate: {candidate}"
                        )
                        c_text = ""

                    # Deterministically verify JSON output only.
                    instruction_scores = self.instruction_service.verify_instruction(
                        user_query or query, [c_text], json_only=True
                    )
                    instruction_score = (
                        _clamp(float(instruction_scores[0]))
                        if instruction_scores
                        else 1.0
                    )

                    payload = candidate.get("payload") or {}
                    # sentence-transformers CrossEncoder for bge-reranker already
                    # outputs probabilities in [0, 1].
                    base_score = _clamp(float(score))

                    factors: dict[str, float] = {}

                    # Provider identity: retrieval already filters on model, but
                    # keep a soft guard in case that ever changes.
                    candidate_model = payload.get("model")
                    factors["model"] = 1.0 if candidate_model == request_model else 0.95
                    candidate_service = payload.get("service_name")
                    factors["service"] = (
                        1.0 if candidate_service == request_service else 0.98
                    )

                    # Intent: skip when the request has no classified intent so
                    # offline calibration and degraded paths are not punished.
                    if not request_intent:
                        factors["intent"] = 1.0
                    else:
                        candidate_intent = payload.get("intent")
                        factors["intent"] = (
                            1.0 if candidate_intent == request_intent else 0.92
                        )

                    (
                        metadata_factor,
                        metadata_mismatches,
                        metadata_comparisons,
                    ) = self._metadata_similarity(
                        request_core_operation,
                        request_core_subject,
                        request_subject_modifier,
                        request_action_modifier,
                        payload,
                    )
                    factors["metadata"] = metadata_factor

                    candidate_entity_set = set(payload.get("entities") or [])
                    (
                        entity_factor,
                        entity_conflict,
                        entity_overlap,
                    ) = self._entity_similarity(
                        request_entity_set, candidate_entity_set
                    )
                    factors["entities"] = entity_factor

                    system_factor, system_jaccard = self._system_prompt_similarity(
                        system_query, payload.get("system_prompt")
                    )
                    factors["system_prompt"] = system_factor

                    candidate_temperature = payload.get("temperature")
                    try:
                        candidate_temperature = float(candidate_temperature)
                    except (ValueError, TypeError):
                        candidate_temperature = 0.0
                    temperature_delta = abs(candidate_temperature - req_temp)
                    factors["temperature"] = 1.0 - min(0.10, temperature_delta * 0.05)

                    factors["instruction"] = 1.0 - min(
                        0.20, 0.20 * (1.0 - instruction_score)
                    )

                    details = {
                        "metadata_mismatches": metadata_mismatches,
                        "metadata_comparisons": metadata_comparisons,
                        "entity_conflict": entity_conflict,
                        "entity_overlap": round(entity_overlap, 6),
                        "system_prompt_jaccard": round(system_jaccard, 6),
                        "temperature_delta": round(temperature_delta, 6),
                        "instruction_score": round(instruction_score, 6),
                    }

                    if scoring_mode == "additive":
                        # Symmetric additive combine: factors are bounded around
                        # 1.0, so the base score keeps its ordering.
                        final_score = base_score + sum(
                            f - 1.0 for f in factors.values()
                        )
                    else:
                        # Multiplicative combine keeps ordering and never drives a
                        # strong base score to zero through penalty stacking.
                        final_score = base_score
                        for factor in factors.values():
                            final_score *= factor
                    final_score = _clamp(final_score)

                    result = candidate.copy()
                    result["raw_cross_encoder_score"] = float(score)
                    result["base_normalized_score"] = base_score
                    result["rerank_score"] = final_score

                    # Attached for debugging, cache_info, and the gateway trace payload.
                    result["score_breakdown"] = {
                        "scoring_mode": scoring_mode,
                        "base_score": round(base_score, 6),
                        "final_score": round(final_score, 6),
                        "factors": {k: round(v, 6) for k, v in factors.items()},
                        "details": details,
                    }
                    reranked.append(result)
                except Exception:
                    logger.exception(
                        f"Error reranking candidate {candidate.get('id', 'unknown')}"
                    )
                    continue

            reranked.sort(
                key=lambda x: x["rerank_score"],
                reverse=True,
            )

            return reranked
        except Exception:
            logger.exception("Error in reranking")
            return []
