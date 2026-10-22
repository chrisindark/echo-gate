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


if __name__ == "__main__":
    unittest.main()
