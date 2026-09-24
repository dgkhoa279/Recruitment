from unittest.mock import MagicMock, patch

import requests

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.muk_ai.providers import REGISTRY

from ..providers.ollama import OllamaProvider


@tagged('post_install', '-at_install')
class TestOllamaProvider(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref('muk_ai_ollama.provider_ollama')
        cls.google = cls.env.ref('muk_ai.provider_google')
        cls.provider.write(
            {
                'active': True,
                'ollama_url': 'http://127.0.0.1:11434',
                'ollama_fallback_provider_id': cls.google.id,
            }
        )

    @staticmethod
    def _response(payload):
        response = MagicMock()
        response.json.return_value = payload
        response.raise_for_status.return_value = None
        return response

    def test_provider_is_registered_and_local(self):
        self.assertIs(REGISTRY['ollama'], OllamaProvider)
        client = self.provider._get_client()
        self.assertEqual(client.api_url, 'http://127.0.0.1:11434')
        with self.assertRaises(ValidationError):
            self.provider._validate_ollama_url('https://example.com:11434')

    def test_non_streaming_request_and_tool_call(self):
        captured = {}

        def fake_post(url, **kwargs):
            captured['url'] = url
            captured['body'] = kwargs['json']
            return self._response(
                {
                    'message': {
                        'role': 'assistant',
                        'content': 'Đã tìm thấy.',
                        'tool_calls': [
                            {
                                'function': {
                                    'name': 'search_read',
                                    'arguments': {'model': 'res.partner'},
                                }
                            }
                        ],
                    },
                    'prompt_eval_count': 12,
                    'eval_count': 5,
                    'done_reason': 'stop',
                }
            )

        with patch.object(requests.Session, 'post', side_effect=fake_post):
            result = self.provider._request_responses(
                inputs=[
                    {
                        'role': 'user',
                        'content': [{'type': 'input_text', 'text': 'Tìm'}],
                    }
                ],
                tools_schema=[
                    {
                        'name': 'search_read',
                        'description': 'Search Odoo records',
                        'parameters': {'type': 'object', 'properties': {}},
                    }
                ],
            )
        self.assertEqual(captured['url'], 'http://127.0.0.1:11434/api/chat')
        self.assertEqual(captured['body']['model'], 'qwen2.5:14b')
        self.assertEqual(result['text'], 'Đã tìm thấy.')
        self.assertEqual(result['tool_calls'][0]['name'], 'search_read')
        self.assertEqual(result['usage']['input_tokens'], 12)

    def test_tool_history_is_converted_for_ollama(self):
        messages = OllamaProvider._inputs_to_messages(
            [
                {'role': 'user', 'content': [{'type': 'input_text', 'text': 'Tìm'}]},
                {
                    'type': 'function_call',
                    'name': 'search_read',
                    'arguments': '{"model": "res.partner"}',
                    'call_id': 'call_1',
                },
                {
                    'type': 'function_call_output',
                    'call_id': 'call_1',
                    'output': '{"records": []}',
                },
            ]
        )
        self.assertEqual(
            [item['role'] for item in messages],
            ['user', 'assistant', 'tool'],
        )
        self.assertEqual(
            messages[1]['tool_calls'][0]['function']['name'],
            'search_read',
        )
        self.assertEqual(messages[2]['tool_name'], 'search_read')

    def test_builder_structured_request_uses_selected_model_and_no_thinking(self):
        captured = {}

        def fake_post(url, **kwargs):
            captured['body'] = kwargs['json']
            return self._response({
                'message': {'role': 'assistant', 'content': '{"tool":"x","arguments":{}}'},
                'prompt_eval_count': 10, 'eval_count': 8, 'done_reason': 'stop',
            })

        schema = {'type': 'object', 'properties': {
            'tool': {'type': 'string'}, 'arguments': {'type': 'object'}
        }, 'required': ['tool', 'arguments'], 'additionalProperties': False}
        with patch.object(requests.Session, 'post', side_effect=fake_post):
            result = self.provider._request_responses(
                inputs=[{'role': 'system', 'content': [{'type': 'input_text', 'text': 'author'}]},
                        {'role': 'user', 'content': [{'type': 'input_text', 'text': '{}'}]}],
                tools_schema=[], model='qwen2.5:14b',
                text_schema={'name': 'muk_tool_builder_step', 'schema': schema},
            )
        self.assertEqual(captured['body']['model'], 'qwen2.5:14b')
        self.assertEqual(captured['body']['format'], schema)
        self.assertFalse(captured['body']['think'])
        self.assertEqual(captured['body']['options']['temperature'], 0)
        self.assertEqual(result['usage']['output_tokens'], 8)

    def test_gemini_fallback_runs_before_streaming_output(self):
        fallback_result = {
            'text': 'Gemini fallback',
            'tool_calls': [],
            'carry_inputs': [],
            'usage': {},
        }
        with patch.object(
            OllamaProvider,
            '_request_ollama',
            side_effect=UserError('Ollama unavailable'),
        ), patch.object(
            type(self.google),
            '_request_responses',
            autospec=True,
            return_value=fallback_result,
        ) as fallback:
            result = self.provider._request_responses(
                inputs=[
                    {
                        'role': 'user',
                        'content': [{'type': 'input_text', 'text': 'hello'}],
                    }
                ]
            )
        self.assertEqual(result['text'], 'Gemini fallback')
        fallback.assert_called_once()
