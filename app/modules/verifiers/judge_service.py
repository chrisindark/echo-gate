import json
import logging

from app.core.config import config
from app.modules.chat.chat_schema import ChatCompletionRequest, ChatMessage
from app.modules.llm.llm_provider_service import LlmProviderService
from app.modules.verifiers.cross_encoder_service import CrossEncoderService
from app.modules.verifiers.instruction_service import InstructionVerifier

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
3. Instruction Following (1 to 5): Does the cached response follow any formatting, length, or structural constraints requested in the user query?
   - 1: Ignores all structural constraints.
   - 3: Follows some constraints but misses others.
   - 5: Strictly follows all constraints (if none were requested, default to 5).

Return ONLY valid JSON in the following format (use integer values):
{{
    "llm_relevance_score": 5,
    "llm_contradiction_score": 0,
    "llm_instruction_score": 5
}}
"""


class JudgeService:
    def __init__(
        self,
        llm_provider_service: LlmProviderService,
        cross_encoder_service: CrossEncoderService,
        instruction_service: InstructionVerifier,
    ):
        self.llm_provider_service = llm_provider_service
        self.cross_encoder_service = cross_encoder_service
        self.instruction_service = instruction_service

    async def evaluate_by_llm(self, query_text: str, response_text: str) -> dict:
        prompt = LLM_JUDGE_PROMPT.format(
            user_query=query_text, cached_response=response_text
        )

        request = ChatCompletionRequest(
            model=config.LLM_JUDGE_MODEL,
            messages=[ChatMessage(role="user", content=prompt)],
            response_format={"type": "json_object"},
            temperature=config.LLM_JUDGE_TEMPERATURE,
            max_tokens=config.LLM_JUDGE_MAX_TOKENS,
        )

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
            else:
                logger.warning(
                    f"Unsupported LLM_JUDGE_SERVICE {config.LLM_JUDGE_SERVICE}. Returning 0.0."
                )
                return {}

            if not response.choices or not response.choices[0].message.content:
                logger.warning("LLM judge returned empty content.")
                return {}

            content = response.choices[0].message.content
            scores = json.loads(content)
            return scores

        except Exception as e:
            logger.error(f"Failed to run LLM judge: {e}")
            return {}

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
            rel_scores = self.cross_encoder_service.relevance_predict(
                semantic_query_text, [semantic_response_text]
            )
            logger.debug(f"rel_scores: {rel_scores}")
            rel_score_raw = rel_scores[0] if rel_scores else 0.0
            logger.debug(f"rel_score_raw: {rel_score_raw}")
            relevance = max(1, min(5, int((rel_score_raw + 10) / 4)))
            logger.debug(f"relevance: {relevance}")

            # 2. Contradiction Score (0 or 1)
            # NLI models output probabilities for [Contradiction, Entailment, Neutral]
            # DeBERTa v3 NLI index 0 is typically contradiction.
            nli_scores = self.cross_encoder_service.nli_predict(
                semantic_query_text, [semantic_response_text]
            )
            logger.debug(f"nli_scores: {nli_scores}")
            nli_score_raw = nli_scores[0] if nli_scores else [0.0, 0.0, 0.0]
            logger.debug(f"nli_score_raw: {nli_score_raw}")

            # If the contradiction probability is higher than entailment/neutral, mark as 1
            if isinstance(nli_score_raw, list) and len(nli_score_raw) >= 3:
                contradiction = (
                    1
                    if nli_score_raw[0] > max(nli_score_raw[1], nli_score_raw[2])
                    else 0
                )
            else:
                contradiction = 0
            logger.debug(f"contradiction: {contradiction}")

            # 3. Instruction Following (1 to 5)
            instruction_scores = self.instruction_service.verify_instruction(
                query_text, [response_text]
            )
            logger.debug(f"instruction_scores: {instruction_scores}")
            pass_ratio = instruction_scores[0] if instruction_scores else 1.0
            logger.debug(f"pass_ratio: {pass_ratio}")

            if pass_ratio == 1.0:
                instruction = 5
            elif pass_ratio >= 0.75:
                instruction = 4
            elif pass_ratio >= 0.50:
                instruction = 3
            elif pass_ratio >= 0.25:
                instruction = 2
            else:
                instruction = 1
            logger.debug(f"instruction: {instruction}")

            return {
                "llm_relevance_score": relevance,
                "llm_contradiction_score": contradiction,
                "llm_instruction_score": instruction,
            }
        except Exception as e:
            logger.error(f"Failed to run local judge: {e}")
            return {}
