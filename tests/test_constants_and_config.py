import os
import unittest

import app.core.constants as constants
from app.core.config import config
from app.core.constants import (
    DEFAULT_CACHE_KEY_VERSION,
    DEFAULT_CORE_QUERY_EXTRACTOR_VERSION,
    DEFAULT_EMBEDDING_VERSION,
    DEFAULT_INTENT_CLASSIFIER_VERSION,
    DEFAULT_INTENT_PROMPT_VERSION,
    DEFAULT_JUDGE_PROMPT_VERSION,
    DEFAULT_PROMPT_VERSION,
    DEFAULT_RERANKER_VERSION,
    DEFAULT_SPARSE_EMBEDDING_VERSION,
    CacheLayer,
    CacheMode,
    EchoGateHeaders,
    ErrorCode,
    ProviderName,
)


class TestConstantsAndConfig(unittest.TestCase):
    def setUp(self):
        self._saved_config = {
            "EMBEDDING_VERSION": config.EMBEDDING_VERSION,
            "CACHE_KEY_VERSION": config.CACHE_KEY_VERSION,
            "PROMPT_VERSION": config.PROMPT_VERSION,
            "INTENT_PROMPT_VERSION": config.INTENT_PROMPT_VERSION,
        }

    def tearDown(self):
        for k, v in self._saved_config.items():
            setattr(config, k, v)

    def test_headers_structure(self):
        self.assertEqual(EchoGateHeaders.API_KEY, "X-API-Key")
        self.assertEqual(EchoGateHeaders.ADMIN_KEY, "X-Admin-Key")
        self.assertEqual(EchoGateHeaders.CORRELATION_ID, "X-Correlation-ID")
        self.assertEqual(EchoGateHeaders.REQUEST_ID, "X-Request-ID")
        self.assertEqual(EchoGateHeaders.GATEWAY_DEGRADATION, "X-Gateway-Degradation")
        self.assertEqual(EchoGateHeaders.TIMEOUT_BUDGET_MS, "X-Timeout-Budget-Ms")
        self.assertEqual(EchoGateHeaders.SESSION_ID, "X-Session-ID")
        self.assertEqual(EchoGateHeaders.CONVERSATION_ID, "X-Conversation-ID")
        self.assertIn(EchoGateHeaders.CACHE, EchoGateHeaders.EXPOSED_CORS_HEADERS)

    def test_provider_names(self):
        self.assertEqual(ProviderName.GOOGLE_GENAI, "google-genai")
        self.assertEqual(ProviderName.OPENAI, "openai")
        self.assertEqual(ProviderName.CLAUDE, "claude")
        self.assertEqual(len(ProviderName.ALL), 6)

    def test_cache_layers_and_modes(self):
        self.assertEqual(CacheLayer.REDIS, "redis")
        self.assertEqual(CacheLayer.QDRANT_EXACT, "qdrant_exact")
        self.assertEqual(CacheLayer.QDRANT_SEMANTIC, "qdrant_semantic")
        self.assertIn("active", CacheMode.ALL)
        self.assertIn("shadow", CacheMode.ALL)
        self.assertIn("disabled", CacheMode.ALL)

    def test_error_codes(self):
        self.assertEqual(ErrorCode.CIRCUIT_BREAKER_OPEN, "circuit_breaker_open")
        self.assertEqual(
            ErrorCode.BULKHEAD_CAPACITY_EXCEEDED, "bulkhead_capacity_exceeded"
        )
        self.assertEqual(ErrorCode.TIMEOUT_BUDGET_EXCEEDED, "timeout_budget_exceeded")
        self.assertEqual(ErrorCode.PROVIDER_TIMEOUT, "provider_timeout")

    def test_default_versioning_values(self):
        # All pipeline versioning constants strictly follow the unified 'v1' convention
        self.assertEqual(DEFAULT_EMBEDDING_VERSION, "v1")
        self.assertEqual(DEFAULT_SPARSE_EMBEDDING_VERSION, "v1")
        self.assertEqual(DEFAULT_CACHE_KEY_VERSION, "v1")
        self.assertEqual(DEFAULT_PROMPT_VERSION, "v1")
        self.assertEqual(DEFAULT_INTENT_PROMPT_VERSION, "v1")
        self.assertEqual(DEFAULT_INTENT_CLASSIFIER_VERSION, "v1")
        self.assertEqual(DEFAULT_CORE_QUERY_EXTRACTOR_VERSION, "v1")
        self.assertEqual(DEFAULT_RERANKER_VERSION, "v1")
        self.assertEqual(DEFAULT_JUDGE_PROMPT_VERSION, "v1")

    def test_dynamic_versioning_reflection(self):
        # When config version changes, accessing via constants module returns updated value
        config.EMBEDDING_VERSION = "v2"
        self.assertEqual(constants.EMBEDDING_VERSION, "v2")

        config.CACHE_KEY_VERSION = "v3-beta"
        self.assertEqual(constants.CACHE_KEY_VERSION, "v3-beta")

        config.PROMPT_VERSION = "2.1"
        self.assertEqual(constants.PROMPT_VERSION, "2.1")

    def test_env_var_loading(self):
        backup_env = {
            "EMBEDDING_VERSION": os.environ.get("EMBEDDING_VERSION"),
            "CACHE_KEY_VERSION": os.environ.get("CACHE_KEY_VERSION"),
        }
        try:
            os.environ["EMBEDDING_VERSION"] = "v_test_model"
            os.environ["CACHE_KEY_VERSION"] = "v_test_key"
            config.load()
            self.assertEqual(config.EMBEDDING_VERSION, "v_test_model")
            self.assertEqual(config.CACHE_KEY_VERSION, "v_test_key")
            self.assertEqual(constants.EMBEDDING_VERSION, "v_test_model")
            self.assertEqual(constants.CACHE_KEY_VERSION, "v_test_key")
        finally:
            for k, v in backup_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            config.load()


if __name__ == "__main__":
    unittest.main()
