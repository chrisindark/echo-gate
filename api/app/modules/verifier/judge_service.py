import json
import logging
import math

from app.core.config import config
from app.modules.chat.chat_schema import ChatCompletionRequest, ChatMessage
from app.modules.llm.llm_provider_service import LlmProviderService
from app.modules.verifier.cross_encoder_service import (
    CrossEncoderService,
    InferenceUnavailable,
)
from app.modules.verifier.instruction_service import InstructionVerifier

logger = logging.getLogger(__name__)

LLM_JUDGE_PROMPT = """You are an objective AI evaluator. You are evaluating a response served from a Semantic Cache for a specific User Query.
Your job is to rate the cached response on three dimensions using a strict JSON format.

User Query:
{user_query}

Cached Response:
{cached_response}

Instructions:
1. Relevance (1 to 5): Does the cached response address the core intent of the user query?
   - 1: Completely irrelevant.
   - 3: Partially answers the query but misses key components.
   - 5: Perfect response, addresses the exact intent.
2. Contradiction (0 or 1): Does the cached response contain information that explicitly contradicts what the user asked for?
   - 0: No contradiction detected.
   - 1: Direct contradiction detected (e.g., user asked for 'start' but response says 'stop').
3. Entailment (1 to 5): Does the cached response logically follow from and faithfully address the user query?
   - 1: Completely ungrounded, off-topic, or contradictory.
   - 3: Partially supported or neutral.
   - 5: Strictly supported, faithful, and entails the query.

Return ONLY valid JSON in the following format (use integer values):
{{
    "llm_relevance_score": 5,
    "llm_contradiction_score": 0,
    "llm_entailment_score": 5
}}
"""

# Comparative prompt used by the shadow/audit paths: two answers to the same
# question, judged for semantic equivalence rather than scored in isolation.
LLM_EQUIVALENCE_PROMPT = """You compare two answers to the same request.

Request:
{user_query}

Answer A (from cache):
{cached_response}

Answer B (generated fresh):
{actual_response}

Question: Would Answer A satisfy the request as well as Answer B?
Two answers are equivalent when they convey the same substantive information,
even if worded differently. Differences in formatting, ordering, or style do not
matter. A different fact, a missing requirement, or a contradictory instruction
means they are NOT equivalent.

Reply with exactly one word: YES or NO."""


class JudgeService:
    def __init__(
        self,
        llm_provider_service: LlmProviderService,
        cross_encoder_service: CrossEncoderService,
        instruction_service: InstructionVerifier | None = None,
    ):
        self.llm_provider_service = llm_provider_service
        self.cross_encoder_service = cross_encoder_service
        self.instruction_service = instruction_service

    async def _run_judge_request(self, request: ChatCompletionRequest) -> str | None:
        """Dispatch a judge prompt to the configured judge provider."""
        try:
            if config.LLM_JUDGE_SERVICE in ["gemini", "google-genai"]:
                response = await self.llm_provider_service.generate_gemini_completion(
                    request
                )
            elif config.LLM_JUDGE_SERVICE == "openai":
                response = await self.llm_provider_service.generate_openai_completion(
                    request
                )
            elif config.LLM_JUDGE_SERVICE == "groq":
                response = await self.llm_provider_service.generate_groq_completion(
                    request
                )
            elif config.LLM_JUDGE_SERVICE in ["claude", "anthropic"]:
                response = await self.llm_provider_service.generate_claude_completion(
                    request
                )
            else:
                logger.warning(
                    f"Unsupported LLM_JUDGE_SERVICE {config.LLM_JUDGE_SERVICE}. Returning None."
                )
                return None

            if not response.choices or not response.choices[0].message.content:
                logger.warning("LLM judge returned empty content.")
                return None
            return response.choices[0].message.content

        except Exception as e:
            logger.error(f"Failed to run LLM judge: {e}")
            return None

    async def evaluate_by_llm(self, query_text: str, response_text: str) -> dict:
        prompt = LLM_JUDGE_PROMPT.format(
            user_query=query_text, cached_response=response_text
        )

        request = ChatCompletionRequest(
            # The schema validates `model` against the provider's allow-list, so
            # omitting the service falls back to "ollama" and rejects a
            # google-genai judge model before the call is ever made.
            service_name=config.LLM_JUDGE_SERVICE,
            model=config.LLM_JUDGE_MODEL,
            messages=[ChatMessage(role="user", content=prompt)],
            response_format={"type": "json_object"},
            temperature=config.LLM_JUDGE_TEMPERATURE,
            max_tokens=config.LLM_JUDGE_MAX_TOKENS,
        )

        content = await self._run_judge_request(request)
        if content is None:
            return {}

        try:
            return json.loads(content)
        except (json.JSONDecodeError, TypeError):
            logger.warning("LLM judge returned non-JSON content: %r", content)
            return {}

    async def evaluate_equivalence(
        self,
        query_text: str,
        cached_response: str,
        actual_response: str,
    ) -> bool | None:
        """Judge whether a cached answer is equivalent to a fresh one.

        Returns ``True``/``False`` when the judge answered, and ``None`` when it
        was unavailable, errored, or gave an unparseable reply — callers store
        ``None`` as "not compared" rather than inventing a verdict.
        """
        prompt = LLM_EQUIVALENCE_PROMPT.format(
            user_query=query_text,
            cached_response=cached_response,
            actual_response=actual_response,
        )

        # Deliberately tiny: the answer is one word, and this runs out of band on
        # every shadow match, so it must stay cheap.
        request = ChatCompletionRequest(
            service_name=config.LLM_JUDGE_SERVICE,
            model=config.LLM_JUDGE_MODEL,
            messages=[ChatMessage(role="user", content=prompt)],
            temperature=config.LLM_JUDGE_TEMPERATURE,
            max_tokens=16,
        )

        content = await self._run_judge_request(request)
        if not content:
            return None

        normalized = content.strip().upper()
        if normalized.startswith("YES"):
            return True
        if normalized.startswith("NO"):
            return False
        # Some providers add a sentence before the verdict; fall back to the
        # first standalone token we recognise.
        for token in normalized.replace(".", " ").replace(",", " ").split():
            if token == "YES":
                return True
            if token == "NO":
                return False
        logger.warning("Unparseable equivalence verdict: %r", content)
        return None

    async def _extract_core_query_with_llm(self, query: str) -> str:
        prompt = (
            "Extract the core semantic intent from the user query below. "
            "Remove any instructions about formatting, output structure, word counts, or constraints. "
            "Output STRICTLY the core question or statement without any introductory text, quotes, or markdown.\n\n"
            f"User Query:\n{query}"
        )

        request = ChatCompletionRequest(
            service_name=config.CORE_QUERY_EXTRACTOR_SERVICE,
            model=config.CORE_QUERY_EXTRACTOR_MODEL,
            messages=[ChatMessage(role="user", content=prompt)],
            temperature=config.CORE_QUERY_EXTRACTOR_TEMPERATURE,
            max_tokens=config.CORE_QUERY_EXTRACTOR_MAX_TOKENS,
        )

        try:
            response = await self.llm_provider_service.generate_ollama_completion(
                request
            )
            if response and response.choices and response.choices[0].message.content:
                logger.debug(
                    f"Extracted core query: {response.choices[0].message.content.strip()}"
                )
                return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Failed to extract core query with LLM: {e}")

        return query  # Fallback

    def _extract_text_from_json(self, response_text: str) -> str:
        # Cross-encoders perform poorly on JSON syntax. Strip it if possible.
        try:
            # Handle possible markdown blocks
            clean_text = response_text
            if clean_text.startswith("```json"):
                clean_text = clean_text[7:].strip()
            if clean_text.endswith("```"):
                clean_text = clean_text[:-3].strip()

            data = json.loads(clean_text)
            if isinstance(data, dict):
                # Extract all string values from the JSON
                return " ".join(
                    [
                        str(v)
                        for v in data.values()
                        if isinstance(v, str)
                        or isinstance(v, int)
                        or isinstance(v, float)
                    ]
                )
        except Exception:
            pass
        return response_text

    async def evaluate(self, query_text: str, response_text: str) -> dict:
        try:
            # Use local Qwen to extract the core semantic query for cross-encoder matching
            semantic_query_text = await self._extract_core_query_with_llm(query_text)
            # Strip JSON syntax from the response
            semantic_response_text = self._extract_text_from_json(response_text)

            # 1. Relevance Score (1 to 5)
            # Cross-encoder typically returns logits (e.g., -10 to +10).
            # We map this approximately to a 1-5 scale.
            try:
                rel_scores = await self.cross_encoder_service.relevance_predict_async(
                    semantic_query_text, [semantic_response_text]
                )
            except InferenceUnavailable as exc:
                logger.warning("relevance cross-encoder unavailable: %s", exc)
                rel_scores = []
            logger.debug(f"rel_scores: {rel_scores}")
            rel_score_raw = rel_scores[0] if rel_scores else 0.0
            logger.debug(f"rel_score_raw: {rel_score_raw}")
            relevance = max(1, min(5, int((rel_score_raw + 10) / 4)))
            logger.debug(f"relevance: {relevance}")

            # 2. Contradiction & Entailment Score from DeBERTa-v3 NLI
            # NLI models output logits for [Contradiction, Entailment, Neutral]
            try:
                nli_scores = await self.cross_encoder_service.nli_predict_async(
                    semantic_query_text, [semantic_response_text]
                )
            except InferenceUnavailable as exc:
                logger.warning("NLI cross-encoder unavailable: %s", exc)
                nli_scores = []
            logger.debug(f"nli_scores: {nli_scores}")
            nli_score_raw = nli_scores[0] if nli_scores else [0.0, 0.0, 0.0]
            logger.debug(f"nli_score_raw: {nli_score_raw}")

            contradiction = 0
            entailment = 3.0

            if isinstance(nli_score_raw, list) and len(nli_score_raw) >= 3:
                # Softmax probabilities over [contradiction, entailment, neutral]
                exp_logits = [
                    math.exp(min(max(float(s), -50.0), 50.0)) for s in nli_score_raw[:3]
                ]
                sum_exp = sum(exp_logits)
                probs = (
                    [e / sum_exp for e in exp_logits]
                    if sum_exp > 0
                    else [0.0, 0.0, 0.0]
                )
                _, p_entailment, p_neutral = probs[0], probs[1], probs[2]

                # Contradiction flag (0 or 1)
                contradiction = (
                    1
                    if nli_score_raw[0] > max(nli_score_raw[1], nli_score_raw[2])
                    else 0
                )

                # Entailment score (1 to 5 scale)
                # High entailment -> near 5.0; Neutral/low entailment -> ~3.0; Contradiction -> near 1.0
                entailment_val = 1.0 + 4.0 * (p_entailment + 0.5 * p_neutral)
                if contradiction == 1:
                    entailment_val = min(entailment_val, 2.0)
                entailment = round(max(1.0, min(5.0, entailment_val)), 2)

            logger.debug(f"contradiction: {contradiction}")
            logger.debug(f"entailment: {entailment}")

            return {
                "llm_relevance_score": relevance,
                "llm_contradiction_score": contradiction,
                "llm_entailment_score": entailment,
            }
        except Exception as e:
            logger.error(f"Failed to run local judge: {e}")
            return {}
