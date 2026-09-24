from __future__ import annotations

from odoo.api import Environment

from . import providers
from . import models


def _post_init_hook(env: Environment) -> None:
    """Make Qwen the local model used by the default agent."""
    ollama = env.ref('muk_ai_ollama.provider_ollama')
    model = env.ref('muk_ai_ollama.model_qwen2_5_14b')
    agent = env.ref('muk_ai.agent_general')

    ollama.sudo().write(
        {
            'active': True,
            'default_model_id': model.id,
            'ollama_fallback_provider_id': False,
            'max_tokens': 4096,
        }
    )
    env.ref('muk_ai.provider_google').sudo().write({'active': True})
    env.ref('muk_ai.provider_openai').sudo().write({'active': False})
    env.ref('muk_ai.provider_anthropic').sudo().write({'active': False})
    agent.sudo().write(
        {
            'model_id': model.id,
            'enable_web_search': False,
            'enable_image_generation': False,
            'enable_code_interpreter': False,
        }
    )
    env['res.company'].sudo().search([]).write(
        {
            'default_ai_provider_id': ollama.id,
            'default_ai_agent_id': agent.id,
        }
    )
