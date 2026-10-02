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

        # Redis
        self.REDIS_TTL = int(os.getenv("REDIS_TTL", 3600))  # Default 1 hour TTL

        # Provider & Fallback Defaults
        self.USE_FALLBACK_LLM = os.getenv("USE_FALLBACK_LLM", "true")
        self.DEFAULT_LLM_SERVICE = os.getenv("DEFAULT_LLM_SERVICE", "google-genai")
        self.USE_FALLBACK_LLM_SERVICE = os.getenv(
            "USE_FALLBACK_LLM_SERVICE", "google-genai"
        )
        self.USE_FALLBACK_LLM_MODEL = os.getenv(
            "USE_FALLBACK_LLM_MODEL", "gemini-3.1-flash-lite"
        )

        # Timeouts & Retries
        self.LLM_PROVIDER_TIMEOUT_SECONDS = float(
            os.getenv("LLM_PROVIDER_TIMEOUT_SECONDS", "15.0")
        )
        self.LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "0"))
        self.RETRY_BACKOFF_INITIAL_SECONDS = int(
            os.getenv("RETRY_BACKOFF_INITIAL_SECONDS", "1")
        )
        self.RETRY_BACKOFF_MAX_SECONDS = int(
            os.getenv("RETRY_BACKOFF_MAX_SECONDS", "10")
        )

        # Semantic Search & Cache Tuning
        self.QDRANT_SEARCH_THRESHOLD = float(
            os.getenv("QDRANT_SEARCH_THRESHOLD", "0.70")
        )
        self.QDRANT_SEARCH_HIGH_THRESHOLD = float(
            os.getenv("QDRANT_SEARCH_HIGH_THRESHOLD", "0.98")
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

        self.CROSS_ENCODER_VERIFIER_MODEL = os.getenv(
            "CROSS_ENCODER_VERIFIER_MODEL", "cross-encoder/ms-marco-MiniLM-L6-v2"
        )


config = Config()
