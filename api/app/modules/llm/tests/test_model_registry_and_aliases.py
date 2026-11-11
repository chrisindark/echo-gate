import unittest

from app.core.circuit_breaker import circuit_breaker_registry
from app.modules.chat.chat_schema import ChatCompletionRequest, ChatMessage
from app.modules.llm.model_registry import (
    DynamicModelRegistry,
)


class TestModelRegistryAndAliases(unittest.TestCase):
    def setUp(self):
        self.registry = DynamicModelRegistry()
        circuit_breaker_registry.reset_all()

    def test_default_models_loaded(self):
        openai_models = self.registry.get_allowed_models("openai")
        self.assertIn("gpt-4o-mini", openai_models)
        gemini_models = self.registry.get_allowed_models("google-genai")
        self.assertIn("gemini-3.1-flash-lite", gemini_models)

    def test_dynamic_register_and_unregister(self):
        self.assertFalse(self.registry.is_model_allowed("openai", "gpt-new-model"))
        self.registry.register_model("openai", "gpt-new-model")
        self.assertTrue(self.registry.is_model_allowed("openai", "gpt-new-model"))

        self.registry.unregister_model("openai", "gpt-new-model")
        self.assertFalse(self.registry.is_model_allowed("openai", "gpt-new-model"))

    def test_toggle_model_availability(self):
        self.assertTrue(self.registry.is_model_allowed("openai", "gpt-4o-mini"))
        self.registry.toggle_model("openai", "gpt-4o-mini", enabled=False)
        self.assertFalse(self.registry.is_model_allowed("openai", "gpt-4o-mini"))
        self.registry.toggle_model("openai", "gpt-4o-mini", enabled=True)
        self.assertTrue(self.registry.is_model_allowed("openai", "gpt-4o-mini"))

    def test_model_aliases(self):
        self.assertTrue(self.registry.is_alias("fast"))
        targets = self.registry.get_alias_targets("fast")
        self.assertGreater(len(targets), 1)
        self.assertEqual(targets[0].service, "groq")

        # Custom alias
        self.registry.register_alias(
            "my_alias",
            [
                {"service": "openai", "model": "gpt-4o-mini"},
                {"service": "groq", "model": "openai/gpt-oss-20b"},
            ],
        )
        self.assertTrue(self.registry.is_alias("my_alias"))
        custom_targets = self.registry.resolve_targets("openai", "my_alias")
        self.assertEqual(len(custom_targets), 2)
        self.assertEqual(custom_targets[0].model, "gpt-4o-mini")

    def test_chat_completion_request_allows_alias(self):
        # Alias "fast" does not raise validation error even though service_name is default
        req = ChatCompletionRequest(
            service_name="ollama",
            model="fast",
            messages=[ChatMessage(role="user", content="Hello")],
        )
        self.assertEqual(req.model, "fast")

    def test_reload_from_env_json(self):
        from unittest.mock import patch

        from app.core.config import config

        # Test valid JSON env vars
        with (
            patch.object(
                config,
                "ALLOWED_MODELS_JSON",
                '{"custom_svc": ["custom_m1", "custom_m2"]}',
            ),
            patch.object(
                config,
                "MODEL_ALIASES_JSON",
                '{"smart": [{"service": "openai", "model": "gpt-4o-mini"}]}',
            ),
            patch.object(config, "MODEL_CONTEXT_WINDOWS_JSON", '{"custom_m1": 16384}'),
        ):
            self.registry.reload()
            self.assertIn("custom_m1", self.registry.get_allowed_models("custom_svc"))
            self.assertTrue(self.registry.is_alias("smart"))
            self.assertEqual(self.registry.get_context_window("custom_m1"), 16384)

        # Test invalid JSON handled gracefully
        with (
            patch.object(config, "ALLOWED_MODELS_JSON", "invalid-json"),
            patch.object(config, "MODEL_ALIASES_JSON", "invalid-json"),
            patch.object(config, "MODEL_CONTEXT_WINDOWS_JSON", "invalid-json"),
        ):
            self.registry.reload()

    def test_model_registry_lookups_and_context_windows(self):
        # get_allowed_models with service=None
        all_models = self.registry.get_allowed_models(service=None)
        self.assertGreater(len(all_models), 5)

        # get_allowed_models_dict
        models_dict = self.registry.get_allowed_models_dict()
        self.assertIn("openai", models_dict)

        # is_model_allowed unknown service
        self.assertFalse(self.registry.is_model_allowed("non_existent_service", "m1"))

        # resolve_targets with None service
        targets = self.registry.resolve_targets(None, "some-unaliased-model")
        self.assertEqual(len(targets), 1)

        # is_valid_request
        self.assertTrue(self.registry.is_valid_request(None, "fast"))  # alias
        self.assertTrue(self.registry.is_valid_request("openai", "gpt-4o-mini"))
        self.assertFalse(
            self.registry.is_valid_request("openai", "definitely-not-an-openai-model")
        )

        # get_service_for_model
        self.assertEqual(self.registry.get_service_for_model("fast"), "groq")
        self.assertEqual(self.registry.get_service_for_model("gpt-4o-mini"), "openai")
        self.assertIsNone(self.registry.get_service_for_model("unknown-model-xyz"))

        # custom non-preferred service lookup
        self.registry.register_model("rare_service", "rare_model")
        self.assertEqual(
            self.registry.get_service_for_model("rare_model"), "rare_service"
        )

        # get_context_window with alias
        self.assertGreater(self.registry.get_context_window("fast"), 1000)

        # register_context_window
        self.registry.register_context_window("custom_m", 32000)
        self.assertEqual(self.registry.get_context_window("custom_m"), 32000)


if __name__ == "__main__":
    unittest.main()
