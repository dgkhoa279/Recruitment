from unittest.mock import patch

from odoo.tests.common import TransactionCase

from ..models.ai_provider import AIProviderFactory, ProviderError


class TestRecruitmentAIProviders(TransactionCase):

    def setUp(self):
        super().setUp()
        self.ai_service = self.env["recruitment.ai.service"]

    def test_only_ollama_and_gemini_providers_are_registered(self):
        self.assertEqual(set(AIProviderFactory.PROVIDERS), {"ollama", "gemini"})

    def test_gemini_runs_only_after_ollama_provider_error(self):
        config = {
            "gemini_fallback_enabled": True,
            "gemini_api_key": "test-key",
        }
        provider_objects = {"ollama": object(), "gemini": object()}
        attempted = []

        def get_provider(provider_name="ollama", config=None):
            return provider_objects[provider_name]

        def operation(provider_name, _provider, _config):
            attempted.append(provider_name)
            if provider_name == "ollama":
                raise ProviderError("Ollama unavailable")
            return "gemini-result"

        service_class = type(self.ai_service)
        with patch.object(
            service_class, "_get_ai_config", return_value=config
        ), patch.object(
            service_class, "_get_generation_provider", side_effect=get_provider
        ):
            result = self.ai_service._with_generation_fallback(operation)

        self.assertEqual(result, "gemini-result")
        self.assertEqual(attempted, ["ollama", "gemini"])
