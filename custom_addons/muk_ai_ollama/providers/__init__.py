from odoo.addons.muk_ai.providers import REGISTRY

from .ollama import OllamaProvider


REGISTRY[OllamaProvider.name] = OllamaProvider
