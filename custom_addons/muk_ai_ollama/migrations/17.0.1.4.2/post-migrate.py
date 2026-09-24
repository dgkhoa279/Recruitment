from __future__ import annotations

from odoo import SUPERUSER_ID, api
from odoo.sql_db import Cursor


def migrate(cr: Cursor, version: str) -> None:
    """Replace the removed GPT-OSS model with the installed Qwen model."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    provider = env.ref('muk_ai_ollama.provider_ollama')
    models = env['muk_ai.model'].sudo().with_context(active_test=False)

    qwen = models.search(
        [
            ('provider_id', '=', provider.id),
            ('technical_name', '=', 'qwen2.5:14b'),
        ],
        limit=1,
    )
    legacy = models.search(
        [
            ('provider_id', '=', provider.id),
            ('technical_name', '=', 'gpt-oss:20b'),
        ],
        limit=1,
    )

    values = {
        'name': 'Qwen 2.5 14B (Local)',
        'sequence': 1,
        'provider_id': provider.id,
        'technical_name': 'qwen2.5:14b',
        'context_window': 8192,
        'input_rate': 0,
        'output_rate': 0,
        'cache_read_rate': 0,
        'cache_write_rate': 0,
        'active': True,
        'notes': 'Primary local chat model.',
    }
    if not qwen:
        qwen = legacy
        if qwen:
            qwen.write(values)
        else:
            qwen = models.create(values)

    xmlid = env['ir.model.data'].sudo().search(
        [
            ('module', '=', 'muk_ai_ollama'),
            ('name', '=', 'model_qwen2_5_14b'),
        ],
        limit=1,
    )
    xmlid_values = {
        'module': 'muk_ai_ollama',
        'name': 'model_qwen2_5_14b',
        'model': 'muk_ai.model',
        'res_id': qwen.id,
        'noupdate': True,
    }
    if xmlid:
        xmlid.write(xmlid_values)
    else:
        env['ir.model.data'].sudo().create(xmlid_values)

    provider.sudo().write(
        {
            'active': True,
            'default_model_id': qwen.id,
            'ollama_fallback_provider_id': False,
        }
    )
    agent = env.ref('muk_ai.agent_general', raise_if_not_found=False)
    if agent:
        agent.sudo().write({'model_id': qwen.id})

    if legacy and legacy != qwen:
        legacy.active = False
