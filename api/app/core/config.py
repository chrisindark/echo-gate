import json
import logging
import os

logger = logging.getLogger(__name__)

#: Valid values for ``CACHE_MODE``.
CACHE_MODES = ("disabled", "shadow", "active")

#: ``USE_*`` flag consulted by :meth:`Config.cache_layer_enabled` per layer.
_CACHE_LAYER_FLAGS = {
    "redis": "USE_REDIS_EXACT_MATCHING",
    "qdrant_exact": "USE_QDRANT_EXACT_MATCHING",
    "qdrant_semantic": "USE_QDRANT_SEMANTIC_MATCHING",
}


def _bounded_float_env(name: str, default: float, lower: float, upper: float) -> float:
    """Read a float env var, clamped to ``[lower, upper]``.

    Misconfiguration in a throttling knob must never crash startup or let the
    shadow pipeline exceed its intended budget, so invalid input falls back to
    the default and out-of-range input is clamped.
    """
    raw = os.getenv(name)
    try:
        value = float(raw) if raw is not None and raw.strip() else default
    except (TypeError, ValueError):
        logger.warning("Invalid float for %s=%r; using default %s", name, raw, default)
        value = default
    return min(upper, max(lower, value))


class Config:
    def __init__(self):
        self.load()

    def load(self):
        self.PROJECT_NAME = os.getenv("PROJECT_NAME", "")
        self.VERSION = os.getenv("VERSION", "0.1.0")

        # Telemetry & Observability
        self.OTEL_ENABLED = os.getenv("OTEL_ENABLED", "true").lower() == "true"
        self.OTEL_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "echo-gate")

        # Urls
        self.OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
        self.REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
        qdrant_key = os.getenv("QDRANT_API_KEY")
        self.QDRANT_API_KEY = (
            qdrant_key.strip() if qdrant_key and qdrant_key.strip() else None
        )
        qdrant_replication = os.getenv("QDRANT_REPLICATION_FACTOR")
        self.QDRANT_REPLICATION_FACTOR = (
            int(qdrant_replication)
            if qdrant_replication and qdrant_replication.strip().isdigit()
            else None
        )
        qdrant_consistency = os.getenv("QDRANT_WRITE_CONSISTENCY_FACTOR")
        self.QDRANT_WRITE_CONSISTENCY_FACTOR = (
            int(qdrant_consistency)
            if qdrant_consistency and qdrant_consistency.strip().isdigit()
            else None
        )
        self.DATABASE_URL = os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg://echo_gate_app:echo_gate_app@localhost:5432/echo_gate",
        )
        self.DATABASE_URL_READ = os.getenv("DATABASE_URL_READ", self.DATABASE_URL)
        # Non-superuser role the application connects as. Postgres RLS policies
        # only apply to non-owner roles, so migrations run as the owner
        # (see MIGRATION_DATABASE_URL) and the app runs as this role.
        self.DB_APP_ROLE = os.getenv("DB_APP_ROLE", "echo_gate_app")
        self.DB_APP_PASSWORD = os.getenv("DB_APP_PASSWORD", "echo_gate_app")
        self.CELERY_BROKER_URL = os.getenv(
            "CELERY_BROKER_URL", "redis://localhost:6379/1"
        )
        self.CELERY_RESULT_BACKEND = os.getenv(
            "CELERY_RESULT_BACKEND", "redis://localhost:6379/1"
        )
        self.OPENAI_BASE_URL = os.getenv(
            "OPENAI_BASE_URL", "https://api.openai.com/v1/chat/completions"
        )
        self.GROQ_BASE_URL = os.getenv(
            "GROQ_BASE_URL", "https://api.groq.com/openai/v1/chat/completions"
        )
        self.OPENROUTER_BASE_URL = os.getenv(
            "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1/chat/completions"
        )
        self.CLAUDE_BASE_URL = os.getenv(
            "CLAUDE_BASE_URL",
            os.getenv("CLAUDE_BASE_URL", "https://api.anthropic.com/v1/messages"),
        )

        # Database Connection Pool
        self.DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "20"))
        self.DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "10"))
        self.DB_POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "30"))
        self.DB_POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "1800"))
        self.DB_POOL_PRE_PING = os.getenv("DB_POOL_PRE_PING", "true").lower() == "true"

        # Provider API Keys
        openai_key = os.getenv("OPENAI_API_KEY")
        self.OPENAI_API_KEY = (
            openai_key.strip() if openai_key and openai_key.strip() else None
        )
        gemini_key = os.getenv("GEMINI_API_KEY")
        self.GEMINI_API_KEY = (
            gemini_key.strip() if gemini_key and gemini_key.strip() else None
        )
        groq_key = os.getenv("GROQ_API_KEY")
        self.GROQ_API_KEY = groq_key.strip() if groq_key and groq_key.strip() else None
        openrouter_key = os.getenv("OPENROUTER_API_KEY")
        self.OPENROUTER_API_KEY = (
            openrouter_key.strip()
            if openrouter_key and openrouter_key.strip()
            else None
        )
        claude_key = os.getenv("CLAUDE_API_KEY")
        self.CLAUDE_API_KEY = (
            claude_key.strip() if claude_key and claude_key.strip() else None
        )

        # Redis
        self.REDIS_TTL = int(os.getenv("REDIS_TTL", "3600"))  # Default 1 hour TTL
        self.USE_REDIS_EXACT_MATCHING = os.getenv(
            "USE_REDIS_EXACT_MATCHING", "false"
        ).lower()

        # Provider & Fallback Defaults
        self.USE_FALLBACK_LLM = os.getenv("USE_FALLBACK_LLM", "true")
        self.DEFAULT_LLM_SERVICE = os.getenv("DEFAULT_LLM_SERVICE", "google-genai")
        self.FALLBACK_LLM_SERVICE = os.getenv("FALLBACK_LLM_SERVICE", "google-genai")
        self.FALLBACK_LLM_MODEL = os.getenv(
            "FALLBACK_LLM_MODEL", "gemini-3.1-flash-lite"
        )

        # Timeouts & Retries
        self.LLM_PROVIDER_TIMEOUT_SECONDS = float(
            os.getenv("LLM_PROVIDER_TIMEOUT_SECONDS", "15.0")
        )
        self.HTTP_CLIENT_TIMEOUT_SECONDS = float(
            os.getenv("HTTP_CLIENT_TIMEOUT_SECONDS", "120.0")
        )
        self.LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "0"))
        self.RETRY_BACKOFF_INITIAL_SECONDS = int(
            os.getenv("RETRY_BACKOFF_INITIAL_SECONDS", "1")
        )
        self.RETRY_BACKOFF_MAX_SECONDS = int(
            os.getenv("RETRY_BACKOFF_MAX_SECONDS", "10")
        )
        # Advertised to clients as `Retry-After` when every candidate provider has
        # failed and no circuit breaker is counting down: how long to wait before
        # retrying the request.
        self.RETRY_AFTER_DEFAULT_SECONDS = int(
            os.getenv("RETRY_AFTER_DEFAULT_SECONDS", "5")
        )

        # Circuit Breaker & Bulkheads
        self.CIRCUIT_BREAKER_ENABLED = (
            os.getenv("CIRCUIT_BREAKER_ENABLED", "true").lower() == "true"
        )
        self.CIRCUIT_BREAKER_FAILURE_THRESHOLD = int(
            os.getenv("CIRCUIT_BREAKER_FAILURE_THRESHOLD", "5")
        )
        self.CIRCUIT_BREAKER_RECOVERY_TIMEOUT_SECONDS = float(
            os.getenv("CIRCUIT_BREAKER_RECOVERY_TIMEOUT_SECONDS", "30.0")
        )
        self.CIRCUIT_BREAKER_HALF_OPEN_SUCCESS_THRESHOLD = int(
            os.getenv("CIRCUIT_BREAKER_HALF_OPEN_SUCCESS_THRESHOLD", "2")
        )
        self.GLOBAL_MAX_CONCURRENCY = int(os.getenv("GLOBAL_MAX_CONCURRENCY", "100"))
        self.PROVIDER_MAX_CONCURRENCY = int(os.getenv("PROVIDER_MAX_CONCURRENCY", "25"))
        self.BULKHEAD_ACQUIRE_TIMEOUT_SECONDS = float(
            os.getenv("BULKHEAD_ACQUIRE_TIMEOUT_SECONDS", "0.0")
        )
        self.ALLOWED_MODELS_JSON = os.getenv("ALLOWED_MODELS_JSON")
        self.MODEL_ALIASES_JSON = os.getenv("MODEL_ALIASES_JSON")

        # Streaming
        self.STREAM_MAX_CONTENT_BYTES = int(
            os.getenv("STREAM_MAX_CONTENT_BYTES", str(2 * 1024 * 1024))
        )

        # Semantic Search & Cache Tuning
        self.USE_QDRANT_EXACT_MATCHING = os.getenv(
            "USE_QDRANT_EXACT_MATCHING", "false"
        ).lower()
        self.USE_QDRANT_SEMANTIC_MATCHING = os.getenv(
            "USE_QDRANT_SEMANTIC_MATCHING", "false"
        ).lower()
        self.QDRANT_DENSE_SEARCH_HIGH_THRESHOLD = float(
            os.getenv("QDRANT_DENSE_SEARCH_HIGH_THRESHOLD", "0.98")
        )
        self.QDRANT_SPARSE_SEARCH_HIGH_THRESHOLD = float(
            os.getenv("QDRANT_SPARSE_SEARCH_HIGH_THRESHOLD", "0.33")
        )
        self.QDRANT_SEARCH_LIMIT = int(os.getenv("QDRANT_SEARCH_LIMIT", "10"))
        self.DEFAULT_CACHE_TTL_SECONDS = int(
            os.getenv("DEFAULT_CACHE_TTL_SECONDS", str(1 * 24 * 60 * 60))
        )

        # Cache mode gate: `disabled` (no cache), `shadow` (serve every request
        # from the provider while the full cache pipeline runs out of band), or
        # `active` (serve from cache; the three USE_* flags above still decide
        # which layers participate).
        #
        # When CACHE_MODE is unset it is derived from the legacy USE_* flags so
        # existing deployments keep their behaviour; any explicit value wins.
        cache_mode = os.getenv("CACHE_MODE", "").strip().lower()
        if cache_mode in CACHE_MODES:
            self.CACHE_MODE = cache_mode
        else:
            if cache_mode:
                logger.warning(
                    "Unknown CACHE_MODE %r; expected one of %s. Falling back to "
                    "the legacy USE_* flags.",
                    cache_mode,
                    ", ".join(CACHE_MODES),
                )
            self.CACHE_MODE = self._legacy_cache_mode()

        # Shadow cache pipeline (CACHE_MODE=shadow). The pipeline runs out of
        # band, so these knobs throttle how much work it may do per request.
        self.SHADOW_SAMPLE_RATE = _bounded_float_env(
            "SHADOW_SAMPLE_RATE", 1.0, lower=0.0, upper=1.0
        )
        self.SHADOW_JUDGE_ENABLED = (
            os.getenv("SHADOW_JUDGE_ENABLED", "true").lower() == "true"
        )
        self.SHADOW_READINESS_MIN_EVALUATIONS = int(
            os.getenv("SHADOW_READINESS_MIN_EVALUATIONS", "200")
        )
        self.SHADOW_READINESS_MIN_HIT_RATE = _bounded_float_env(
            "SHADOW_READINESS_MIN_HIT_RATE", 0.05, lower=0.0, upper=1.0
        )
        self.SHADOW_READINESS_MIN_EQUIVALENCE_RATE = _bounded_float_env(
            "SHADOW_READINESS_MIN_EQUIVALENCE_RATE", 0.98, lower=0.0, upper=1.0
        )

        # Cache Confidence Thresholds
        default_thresholds = {
            "code_generation": {"high": 0.95, "moderate": 0.88},
            "data_extraction": {"high": 0.96, "moderate": 0.90},
            "summarization": {"high": 0.95, "moderate": 0.88},
            "general_query": {"high": 0.95, "moderate": 0.88},
            "code_explanation": {"high": 0.94, "moderate": 0.85},
            "debugging": {"high": 0.95, "moderate": 0.88},
            "error_analysis": {"high": 0.95, "moderate": 0.88},
            "refactoring": {"high": 0.94, "moderate": 0.85},
            "optimization": {"high": 0.94, "moderate": 0.85},
            "test_generation": {"high": 0.94, "moderate": 0.85},
            "chitchat": {"high": 0.85, "moderate": 0.75},
            "greeting": {"high": 0.85, "moderate": 0.75},
            "weather": {"high": 0.95, "moderate": 0.88},
            "finance_market": {"high": 0.98, "moderate": 0.92},
            "product_info": {"high": 0.95, "moderate": 0.88},
            "factual_static": {"high": 0.95, "moderate": 0.88},
        }
        thresholds_env = os.getenv("CACHE_CONFIDENCE_THRESHOLDS")
        if thresholds_env:
            try:
                self.CACHE_CONFIDENCE_THRESHOLDS = json.loads(thresholds_env)
            except (json.JSONDecodeError, TypeError):
                self.CACHE_CONFIDENCE_THRESHOLDS = default_thresholds
        else:
            self.CACHE_CONFIDENCE_THRESHOLDS = default_thresholds

        # Intent TTL Mapping
        default_intent_ttls = {
            "weather": 5 * 60,
            "product_info": 60 * 60,
            "code_generation": 24 * 60 * 60,
            "code_explanation": 24 * 60 * 60,
            "debugging": 24 * 60 * 60,
            "error_analysis": 24 * 60 * 60,
            "refactoring": 24 * 60 * 60,
            "optimization": 24 * 60 * 60,
            "test_generation": 24 * 60 * 60,
            "data_extraction": 24 * 60 * 60,
            "summarization": 24 * 60 * 60,
            "chitchat": 24 * 60 * 60,
            "greeting": 7 * 24 * 60 * 60,
            "factual_static": 30 * 24 * 60 * 60,
            "general_query": 7 * 24 * 60 * 60,
        }
        ttls_env = os.getenv("INTENT_TTL_SECONDS")
        if ttls_env:
            try:
                self.INTENT_TTL_SECONDS = json.loads(ttls_env)
            except (json.JSONDecodeError, TypeError):
                self.INTENT_TTL_SECONDS = default_intent_ttls
        else:
            self.INTENT_TTL_SECONDS = default_intent_ttls

        # Model Configuration & Pipeline Versioning
        self.EMBEDDING_MODEL_NAME = os.getenv(
            "EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2"
        )
        self.EMBEDDING_VERSION = os.getenv("EMBEDDING_VERSION", "v1")
        self.SPARSE_EMBEDDING_VERSION = os.getenv("SPARSE_EMBEDDING_VERSION", "v1")
        self.CACHE_KEY_VERSION = os.getenv("CACHE_KEY_VERSION", "v1")
        self.PROMPT_VERSION = os.getenv("PROMPT_VERSION", "v1")
        self.INTENT_PROMPT_VERSION = os.getenv("INTENT_PROMPT_VERSION", "v1")
        self.INTENT_CLASSIFIER_VERSION = os.getenv("INTENT_CLASSIFIER_VERSION", "v1")
        self.CORE_QUERY_EXTRACTOR_VERSION = os.getenv(
            "CORE_QUERY_EXTRACTOR_VERSION", "v1"
        )
        self.RERANKER_VERSION = os.getenv("RERANKER_VERSION", "v1")
        self.JUDGE_PROMPT_VERSION = os.getenv("JUDGE_PROMPT_VERSION", "v1")
        self.RERANKER_MODEL_NAME = os.getenv(
            "RERANKER_MODEL_NAME", "BAAI/bge-reranker-base"
        )
        # How reranker signals are combined: "multiplicative" (bounded factors
        # multiplied into the cross-encoder score) or "additive" (symmetric
        # bounded deltas). Multiplicative preserves candidate ordering and never
        # collapses a strong base score to zero through penalty stacking.
        self.RERANKER_SCORING_MODE = (
            os.getenv("RERANKER_SCORING_MODE", "multiplicative").strip().lower()
        )
        self.INTENT_CLASSIFIER_SERVICE = os.getenv(
            "INTENT_CLASSIFIER_SERVICE", "ollama"
        )
        self.INTENT_CLASSIFIER_MODEL = os.getenv(
            "INTENT_CLASSIFIER_MODEL", "qwen2.5-coder:3b"
        )
        self.INTENT_CLASSIFIER_TEMPERATURE = float(
            os.getenv("INTENT_CLASSIFIER_TEMPERATURE", "0.0")
        )
        self.INTENT_CLASSIFIER_TIMEOUT_SECONDS = float(
            os.getenv("INTENT_CLASSIFIER_TIMEOUT_SECONDS", "2.5")
        )
        self.INTENT_CLASSIFIER_MAX_TOKENS = int(
            os.getenv("INTENT_CLASSIFIER_MAX_TOKENS", "500")
        )
        self.INTENT_CLASSIFIER_REASONING_EFFORT = os.getenv(
            "INTENT_CLASSIFIER_REASONING_EFFORT", "low"
        )
        self.INTENT_CLASSIFIER_MAX_RETRIES = int(
            os.getenv("INTENT_CLASSIFIER_MAX_RETRIES", "0")
        )

        self.CORE_QUERY_EXTRACTOR_SERVICE = os.getenv(
            "CORE_QUERY_EXTRACTOR_SERVICE", "ollama"
        )
        self.CORE_QUERY_EXTRACTOR_MODEL = os.getenv(
            "CORE_QUERY_EXTRACTOR_MODEL", "qwen2.5-coder:1.5b"
        )
        self.CORE_QUERY_EXTRACTOR_TEMPERATURE = float(
            os.getenv("CORE_QUERY_EXTRACTOR_TEMPERATURE", "0.0")
        )
        self.CORE_QUERY_EXTRACTOR_MAX_TOKENS = int(
            os.getenv("CORE_QUERY_EXTRACTOR_MAX_TOKENS", "200")
        )

        self.CROSS_ENCODER_RELEVANCE_MODEL = os.getenv(
            "CROSS_ENCODER_RELEVANCE_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2"
        )
        self.CROSS_ENCODER_NLI_MODEL = os.getenv(
            "CROSS_ENCODER_NLI_MODEL", "cross-encoder/nli-deberta-v3-small"
        )

        # Shared Inference Platform
        #
        # When INFERENCE_BASE_URL (or any per-role URL override) is set, dense
        # embeddings, reranking and NLI are served over HTTP by `inference-platform/`
        # instead of being loaded into this process. Empty (the default) keeps the
        # in-process models, so an existing deployment is unchanged until it opts in.
        #
        # In production, services can be split across independent containers or hosts:
        #   INFERENCE_EMBED_URL -> /embeddings (e.g. http://inference-embed:7997)
        #   INFERENCE_RERANK_URL -> /rerank (e.g. http://inference-rerank:7998)
        #   INFERENCE_RELEVANCE_URL -> /rerank (defaults to INFERENCE_RERANK_URL)
        #   INFERENCE_NLI_URL / INFERENCE_CLASSIFY_URL -> /classify (e.g. http://inference-classify:7999)
        # Each per-role URL falls back to INFERENCE_BASE_URL when unset.
        #
        # Sparse BM25 is never remote: fastembed's vectors are tokenizer-coupled
        # to what is already in Qdrant, and it costs ~1ms locally.
        self.INFERENCE_BASE_URL = os.getenv("INFERENCE_BASE_URL", "").rstrip("/")
        self.INFERENCE_EMBED_URL = os.getenv("INFERENCE_EMBED_URL", "").rstrip("/")
        self.INFERENCE_RERANK_URL = os.getenv("INFERENCE_RERANK_URL", "").rstrip("/")
        self.INFERENCE_RELEVANCE_URL = os.getenv("INFERENCE_RELEVANCE_URL", "").rstrip(
            "/"
        )
        self.INFERENCE_NLI_URL = (
            os.getenv("INFERENCE_NLI_URL") or os.getenv("INFERENCE_CLASSIFY_URL") or ""
        ).rstrip("/")
        self.INFERENCE_ENABLED = bool(
            self.INFERENCE_BASE_URL
            or self.INFERENCE_EMBED_URL
            or self.INFERENCE_RERANK_URL
            or self.INFERENCE_RELEVANCE_URL
            or self.INFERENCE_NLI_URL
        )
        self.INFERENCE_TIMEOUT_SECONDS = _bounded_float_env(
            "INFERENCE_TIMEOUT_SECONDS", 5.0, 0.1, 120.0
        )
        self.INFERENCE_EMBED_TIMEOUT_SECONDS = _bounded_float_env(
            "INFERENCE_EMBED_TIMEOUT_SECONDS",
            self.INFERENCE_TIMEOUT_SECONDS,
            0.1,
            120.0,
        )
        self.INFERENCE_RERANK_TIMEOUT_SECONDS = _bounded_float_env(
            "INFERENCE_RERANK_TIMEOUT_SECONDS",
            self.INFERENCE_TIMEOUT_SECONDS,
            0.1,
            120.0,
        )
        self.INFERENCE_RELEVANCE_TIMEOUT_SECONDS = _bounded_float_env(
            "INFERENCE_RELEVANCE_TIMEOUT_SECONDS",
            self.INFERENCE_RERANK_TIMEOUT_SECONDS,
            0.1,
            120.0,
        )
        self.INFERENCE_NLI_TIMEOUT_SECONDS = _bounded_float_env(
            "INFERENCE_NLI_TIMEOUT_SECONDS", self.INFERENCE_TIMEOUT_SECONDS, 0.1, 120.0
        )
        self.INFERENCE_MAX_RETRIES = int(
            _bounded_float_env("INFERENCE_MAX_RETRIES", 1, 0, 5)
        )
        # Dimension of the dense vectors the engine serves. Locked into the
        # Qdrant collection at creation time; a mismatch is a silent cache
        # corruption, so subclasses verify it against /models at startup.
        self.INFERENCE_EMBEDDING_DIM = int(
            _bounded_float_env("INFERENCE_EMBEDDING_DIM", 384, 1, 8192)
        )
        # Optional explicit pins. Empty means "use the engine's default role",
        # which keeps laptop development working without duplicating the lock
        # file here; production should pin all three.
        self.INFERENCE_EMBED_MODEL = os.getenv("INFERENCE_EMBED_MODEL", "")
        self.INFERENCE_RERANK_MODEL = os.getenv("INFERENCE_RERANK_MODEL", "")
        self.INFERENCE_RELEVANCE_MODEL = os.getenv("INFERENCE_RELEVANCE_MODEL", "")
        self.INFERENCE_NLI_MODEL = os.getenv("INFERENCE_NLI_MODEL", "")

        # Conditional Model Loading for Deployment Separation
        self.USE_RERANKER_MODEL = (
            os.getenv("USE_RERANKER_MODEL", "true").lower() == "true"
        )
        self.USE_RELEVANCE_MODEL = (
            os.getenv("USE_RELEVANCE_MODEL", "true").lower() == "true"
        )
        self.USE_NLI_MODEL = os.getenv("USE_NLI_MODEL", "true").lower() == "true"

        # Evaluation & LLM Judge
        self.EVALUATION_BATCH_SIZE = int(os.getenv("EVALUATION_BATCH_SIZE", "50"))
        self.LLM_JUDGE_SERVICE = os.getenv("LLM_JUDGE_SERVICE", "google-genai")
        self.LLM_JUDGE_MODEL = os.getenv("LLM_JUDGE_MODEL", "gemini-3.5-flash-lite")
        self.LLM_JUDGE_TEMPERATURE = float(os.getenv("LLM_JUDGE_TEMPERATURE", "0.0"))
        self.LLM_JUDGE_MAX_TOKENS = int(os.getenv("LLM_JUDGE_MAX_TOKENS", "200"))

    @staticmethod
    def _legacy_cache_mode() -> str:
        """Derive the cache mode from the pre-``CACHE_MODE`` ``USE_*`` flags.

        Before ``CACHE_MODE`` existed, enabling any cache layer required setting
        its ``USE_*`` flag, and leaving them all off meant "no cache". Deriving
        the mode keeps that behaviour for deployments that never set
        ``CACHE_MODE``.
        """
        enabled = any(
            (os.getenv(flag, "false") or "").strip().lower() == "true"
            for flag in _CACHE_LAYER_FLAGS.values()
        )
        return "active" if enabled else "disabled"

    def cache_layer_enabled(self, layer: str) -> bool:
        """Whether ``layer`` may read from / serve out of the cache.

        The cache only ever *serves* in ``active`` mode. In ``shadow`` mode the
        pipeline runs with ``force=True`` from the background evaluator, and in
        ``disabled`` mode it does not run at all.
        """
        if self.CACHE_MODE != "active":
            return False
        flag = _CACHE_LAYER_FLAGS.get(layer)
        if flag is None:
            raise ValueError(f"Unknown cache layer: {layer!r}")
        return (getattr(self, flag, "false") or "").strip().lower() == "true"


config = Config()
