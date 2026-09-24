from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class AIProvider(models.Model):
    _inherit = 'muk_ai.provider'

    ollama_url = fields.Char(
        string='Ollama Endpoint',
        default='http://127.0.0.1:11434',
        help='Loopback URL of the local Ollama server.',
        groups='base.group_system',
    )
    ollama_fallback_provider_id = fields.Many2one(
        comodel_name='muk_ai.provider',
        string='Failure Fallback',
        domain="[('name', '=', 'google'), ('active', '=', True)]",
        help='Google/Gemini provider used only when Ollama fails before streaming.',
        groups='base.group_system',
    )

    @api.model
    def _validate_ollama_url(self, value: str) -> str:
        """Allow only an HTTP(S) endpoint bound to the local machine."""
        value = (value or '').strip().rstrip('/')
        try:
            parsed = urlparse(value)
            port = parsed.port
        except ValueError as exc:
            raise ValidationError(
                _('The Ollama endpoint has an invalid port.')
            ) from exc
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            raise ValidationError(_('The Ollama endpoint must be an HTTP(S) URL.'))
        if parsed.username or parsed.password:
            raise ValidationError(
                _('Credentials are not allowed in the Ollama endpoint.')
            )
        if (
            parsed.path not in ('', '/')
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise ValidationError(
                _('The Ollama endpoint must not contain a path or query.')
            )
        hostname = parsed.hostname.lower()
        try:
            is_loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = hostname == 'localhost'
        if not is_loopback:
            raise ValidationError(
                _('Ollama must use localhost or a loopback IP address.')
            )
        if port is not None and not 1 <= port <= 65535:
            raise ValidationError(_('The Ollama endpoint has an invalid port.'))
        return value

    @api.constrains('name', 'ollama_url', 'ollama_fallback_provider_id')
    def _check_ollama_settings(self) -> None:
        for record in self:
            if record.name != 'ollama':
                continue
            record._validate_ollama_url(record.ollama_url)
            fallback = record.ollama_fallback_provider_id
            if fallback and (fallback == record or fallback.name != 'google'):
                raise ValidationError(_('The Ollama fallback must be Google/Gemini.'))
