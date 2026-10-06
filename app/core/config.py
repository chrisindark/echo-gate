import json
import os


class Config:
    def __init__(self):
        self.load()

    def load(self):
        # Urls
        self.OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
        self.REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
        self.DATABASE_URL = os.getenv(
            "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/echo_gate"
        )
        self.DATABASE_URL_READ = os.getenv(
            "DATABASE_URL_READ",
            "postgresql://postgres:postgres@localhost:5432/echo_gate_read",
        )
        self.CELERY_REDIS_URL = os.getenv(
            "CELERY_REDIS_URL", "redis://localhost:6379/1"
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

        # Redis
        self.REDIS_TTL = int(os.getenv("REDIS_TTL", 3600))  # Default 1 hour TTL

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

        # Semantic Search & Cache Tuning
        self.QDRANT_DENSE_SEARCH_HIGH_THRESHOLD = float(
            os.getenv("QDRANT_DENSE_SEARCH_HIGH_THRESHOLD", "0.98")
        )
        self.QDRANT_SPARSE_SEARCH_HIGH_THRESHOLD = float(
            os.getenv("QDRANT_SPARSE_SEARCH_HIGH_THRESHOLD", "0.70")
        )
        self.QDRANT_SEARCH_LIMIT = int(os.getenv("QDRANT_SEARCH_LIMIT", "10"))
        self.DEFAULT_CACHE_TTL_SECONDS = int(
            os.getenv("DEFAULT_CACHE_TTL_SECONDS", str(1 * 24 * 60 * 60))
        )
        self.USE_REDIS_SEMANTIC_MATCHING = os.getenv(
            "USE_REDIS_SEMANTIC_MATCHING", "true"
        ).lower()
        self.USE_QDRANT_SEMANTIC_MATCHING = os.getenv(
            "USE_QDRANT_SEMANTIC_MATCHING", "true"
        ).lower()

        # Cache Confidence Thresholds
        default_thresholds = {
            "code_generation": {"high": 0.95, "moderate": 0.88},
            "casual_chat": {"high": 0.85, "moderate": 0.75},
            "general_query": {"high": 0.95, "moderate": 0.88},
            "finance_market": {"high": 0.98, "moderate": 0.92},
            "weather": {"high": 0.95, "moderate": 0.88},
            "product_info": {"high": 0.95, "moderate": 0.88},
            "debugging": {"high": 0.95, "moderate": 0.88},
            "error_analysis": {"high": 0.95, "moderate": 0.88},
            "code_explanation": {"high": 0.94, "moderate": 0.85},
            "code_review": {"high": 0.94, "moderate": 0.85},
            "data_extraction": {"high": 0.96, "moderate": 0.90},
            "research": {"high": 0.95, "moderate": 0.88},
            "refactoring": {"high": 0.94, "moderate": 0.85},
            "optimization": {"high": 0.94, "moderate": 0.85},
            "test_generation": {"high": 0.94, "moderate": 0.85},
            "code_comparison": {"high": 0.95, "moderate": 0.88},
        }
        thresholds_env = os.getenv("CACHE_CONFIDENCE_THRESHOLDS")
        if thresholds_env:
            try:
                self.CACHE_CONFIDENCE_THRESHOLDS = json.loads(thresholds_env)
            except Exception:
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
            "general_query": 7 * 24 * 60 * 60,
        }
        ttls_env = os.getenv("INTENT_TTL_SECONDS")
        if ttls_env:
            try:
                self.INTENT_TTL_SECONDS = json.loads(ttls_env)
            except Exception:
                self.INTENT_TTL_SECONDS = default_intent_ttls
        else:
            self.INTENT_TTL_SECONDS = default_intent_ttls

        # Model Configuration
        self.EMBEDDING_MODEL_NAME = os.getenv(
            "EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2"
        )
        self.RERANKER_MODEL_NAME = os.getenv(
            "RERANKER_MODEL_NAME", "BAAI/bge-reranker-base"
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


config = Config()
