"""Dense-model identity as the gateway records it on cached vectors.

`LlmRouterService` writes `embedding_model=<identity>` into every Qdrant payload and
filters semantic search on the same value. That is what makes a model swap
quarantine old vectors instead of silently comparing two vector spaces — and it
used to read an attribute that the in-process removal deleted, so every semantic
cache write raised `AttributeError`.

`EmbeddingService.__new__` keeps the test off the model-loading path: the property
is pure config, and the BM25 artifact is irrelevant here.
"""

import unittest

from app.core.config import config
from app.modules.embedding.embedding_service import EmbeddingService


class EmbeddingModelIdentityTest(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = (config.EMBEDDING_MODEL_NAME, config.INFERENCE_EMBED_MODEL)

    def tearDown(self) -> None:
        config.EMBEDDING_MODEL_NAME, config.INFERENCE_EMBED_MODEL = self._saved

    def test_model_name_is_the_configured_engine_model(self) -> None:
        config.EMBEDDING_MODEL_NAME = "BAAI/bge-small-en-v1.5"
        config.INFERENCE_EMBED_MODEL = ""
        service = EmbeddingService.__new__(EmbeddingService)
        self.assertEqual(service.model_name, "BAAI/bge-small-en-v1.5")

    def test_explicit_engine_pin_wins_over_the_default(self) -> None:
        # What the engine actually serves is the truth; `verify()` refuses to
        # start when the two disagree, so the recorded tag must follow the pin.
        config.EMBEDDING_MODEL_NAME = "BAAI/bge-small-en-v1.5"
        config.INFERENCE_EMBED_MODEL = "BAAI/bge-base-en-v1.5"
        service = EmbeddingService.__new__(EmbeddingService)
        self.assertEqual(service.model_name, "BAAI/bge-base-en-v1.5")


if __name__ == "__main__":
    unittest.main()
