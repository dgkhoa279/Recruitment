from __future__ import annotations

import contextlib
import json
import logging
import uuid
from collections.abc import Callable, Iterator

import requests

from odoo.exceptions import UserError

from odoo.addons.muk_ai.providers.base import ProviderBase


_logger = logging.getLogger(__name__)


class OllamaProvider(ProviderBase):
    """Translate MuK AI's canonical conversation to Ollama's native chat API."""

    name = 'ollama'
    label = 'Ollama (Local)'
    default_model = 'qwen2.5:14b'
    default_url = 'http://127.0.0.1:11434'

    supports_web_search = False
    supports_image_generation = False
    supports_code_interpreter = False
    supports_vision = False

    @property
    def api_url(self) -> str:
        return self.provider._validate_ollama_url(
            self.provider.sudo().ollama_url or self.default_url
        )

    def headers(self) -> dict:
        return {'Content-Type': 'application/json'}

    def request(
        self,
        inputs,
        tools_schema=None,
        text_schema=None,
        on_delta=None,
        model=None,
        enable_web_search=False,
        enable_image_generation=False,
        enable_code_interpreter=False,
        extra=None,
    ) -> dict:
        """Call Ollama first and use Gemini only before any streamed output."""
        delivered = False

        def guarded_delta(kind, data):
            nonlocal delivered
            delivered = True
            return on_delta(kind, data)

        callback = guarded_delta if callable(on_delta) else None
        try:
            return self._request_ollama(
                inputs=inputs,
                tools_schema=tools_schema,
                text_schema=text_schema,
                on_delta=callback,
                model=model,
            )
        except UserError as error:
            if delivered:
                raise
            return self._request_fallback(
                error=error,
                inputs=inputs,
                tools_schema=tools_schema,
                text_schema=text_schema,
                on_delta=on_delta,
                enable_web_search=enable_web_search,
                enable_image_generation=enable_image_generation,
                enable_code_interpreter=enable_code_interpreter,
                extra=extra,
            )
        except (TypeError, ValueError) as error:
            if delivered:
                self._raise(error)
            return self._request_fallback(
                error=error,
                inputs=inputs,
                tools_schema=tools_schema,
                text_schema=text_schema,
                on_delta=on_delta,
                enable_web_search=enable_web_search,
                enable_image_generation=enable_image_generation,
                enable_code_interpreter=enable_code_interpreter,
                extra=extra,
            )

    def _request_ollama(
        self,
        inputs,
        tools_schema=None,
        text_schema=None,
        on_delta=None,
        model=None,
    ) -> dict:
        model_name = self.model_for(model)
        body = {
            'model': model_name,
            'messages': self._inputs_to_messages(inputs),
            'stream': callable(on_delta),
        }
        if self.max_tokens:
            body['options'] = {'num_predict': self.max_tokens}
        if tools := self._tools_to_ollama(tools_schema):
            body['tools'] = tools
            if (
                model_name.rsplit('/', 1)[-1].lower().startswith('gpt-oss')
                and any(
                    (item.get('function') or {}).get('name') == 'submit_routing'
                    for item in tools
                )
            ):
                body['think'] = 'low'
                body.setdefault('options', {})['temperature'] = 0
        if text_schema and text_schema.get('schema'):
            body['format'] = text_schema['schema']
            if text_schema.get('name') in (
                'muk_tool_intent', 'muk_tool_discovery', 'muk_tool_builder_step'
            ):
                # GPT-OSS can return an empty message.content when JSON schema output
                # is combined with think=False. Its lowest supported reasoning level
                # still emits the schema-constrained JSON. Other model families keep
                # thinking disabled so routing does not consume their output budget.
                model_family = model_name.rsplit('/', 1)[-1].lower()
                body['think'] = 'low' if model_family.startswith('gpt-oss') else False
                body.setdefault('options', {})['temperature'] = 0
        if callable(on_delta):
            return self._stream(body, on_delta)
        return self._parse_response(self._post_json('/api/chat', body))

    def _request_fallback(
        self,
        error,
        inputs,
        tools_schema,
        text_schema,
        on_delta,
        enable_web_search,
        enable_image_generation,
        enable_code_interpreter,
        extra,
    ) -> dict:
        fallback = self.provider.ollama_fallback_provider_id
        if not fallback or not fallback.active:
            if isinstance(error, UserError):
                raise error
            self._raise(error)
        _logger.warning(
            'Ollama failed before streaming; switching to %s: %s',
            fallback.name,
            error,
        )
        extra = extra or {}
        return fallback._request_responses(
            inputs=inputs,
            tools_schema=tools_schema,
            text_schema=text_schema,
            on_delta=on_delta,
            model=None,
            enable_web_search=(
                enable_web_search and fallback.supports_web_search
            ),
            enable_image_generation=(
                enable_image_generation and fallback.supports_image_generation
            ),
            enable_code_interpreter=(
                enable_code_interpreter and fallback.supports_code_interpreter
            ),
            cache_key=extra.get('cache_key'),
            reasoning_effort=extra.get('reasoning_effort'),
        )

    @classmethod
    def _inputs_to_messages(cls, inputs) -> list[dict]:
        call_names = {
            item.get('call_id'): item.get('name')
            for item in inputs or []
            if isinstance(item, dict) and item.get('type') == 'function_call'
        }
        messages = []
        for item in cls._strip_cache_markers(inputs):
            if not isinstance(item, dict):
                continue
            item_type = item.get('type')
            if item_type == 'function_call':
                tool_call = {
                    'type': 'function',
                    'function': {
                        'name': item.get('name') or '',
                        'arguments': cls._arguments_dict(item.get('arguments')),
                    },
                }
                if messages and messages[-1].get('role') == 'assistant':
                    messages[-1].setdefault('tool_calls', []).append(tool_call)
                else:
                    messages.append(
                        {'role': 'assistant', 'content': '', 'tool_calls': [tool_call]}
                    )
                continue
            if item_type == 'function_call_output':
                output = item.get('output')
                if not isinstance(output, str):
                    output = json.dumps(output, default=str)
                messages.append(
                    {
                        'role': 'tool',
                        'tool_name': call_names.get(item.get('call_id')) or '',
                        'content': output,
                    }
                )
                continue
            role = item.get('role')
            if role not in ('system', 'user', 'assistant'):
                continue
            content, images = cls._content_to_ollama(item.get('content'))
            message = {'role': role, 'content': content}
            if images:
                message['images'] = images
            messages.append(message)
        return messages

    @staticmethod
    def _arguments_dict(raw) -> dict:
        if isinstance(raw, dict):
            return raw
        try:
            parsed = json.loads(raw or '{}')
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @classmethod
    def _content_to_ollama(cls, content) -> tuple[str, list[str]]:
        if isinstance(content, str):
            return content, []
        text_parts = []
        images = []
        for block in content or []:
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'muk_ai_thinking':
                continue
            if block.get('type') != 'muk_ai_attachment':
                if text := block.get('text'):
                    text_parts.append(text)
                continue
            filename = block.get('filename') or 'attachment'
            strategy = block.get('strategy')
            if strategy == 'image' and block.get('data_b64'):
                images.append(block['data_b64'])
            elif strategy == 'inline_text':
                text = block.get('inline_text') or ''
                if block.get('truncated'):
                    text += '\n[truncated]'
                text_parts.append(f'--- File: {filename} ---\n{text}')
            else:
                text_parts.append(
                    f'[Attachment {filename} cannot be read by this Ollama model.]'
                )
        return '\n\n'.join(text_parts), images

    @staticmethod
    def _tools_to_ollama(tools_schema) -> list[dict]:
        tools = []
        seen = set()
        for tool in tools_schema or []:
            name = tool.get('name')
            if not name or name in seen:
                continue
            seen.add(name)
            tools.append(
                {
                    'type': 'function',
                    'function': {
                        'name': name,
                        'description': tool.get('description') or '',
                        'parameters': tool.get('parameters')
                        or {'type': 'object', 'properties': {}},
                    },
                }
            )
        return tools

    def _parse_response(self, payload: dict) -> dict:
        if error := payload.get('error'):
            self._raise(error)
        message = payload.get('message') or {}
        result = self._build_result(message, payload)
        if payload.get('done_reason') == 'length':
            self._apply_truncation(result, limit=self.max_tokens)
        return result

    def _build_result(self, message: dict, metadata: dict) -> dict:
        text = message.get('content') or ''
        thinking = message.get('thinking') or ''
        tool_calls, carry_inputs = self._consume_tool_calls(
            message.get('tool_calls') or []
        )
        content = []
        if thinking:
            content.append({'type': 'muk_ai_thinking', 'thinking': thinking})
        if text:
            content.append({'type': 'output_text', 'text': text})
        if content:
            carry_inputs.insert(0, {'role': 'assistant', 'content': content})
        return {
            'text': text.strip(),
            'tool_calls': tool_calls,
            'carry_inputs': carry_inputs,
            'usage': self._usage(
                input_tokens=metadata.get('prompt_eval_count'),
                output_tokens=metadata.get('eval_count'),
                cache_read_tokens=metadata.get('prompt_eval_cached_count'),
            ),
        }

    def _consume_tool_calls(
        self, calls: list[dict], on_delta: Callable | None = None
    ) -> tuple[list[dict], list[dict]]:
        tool_calls = []
        carry_inputs = []
        for call in calls:
            function = call.get('function') or {}
            name = function.get('name') or ''
            arguments = function.get('arguments') or {}
            if isinstance(arguments, dict):
                parse_error = None
            else:
                arguments, parse_error = self._parse_tool_arguments(arguments)
            call_id = call.get('id') or f'call_ollama_{uuid.uuid4().hex[:16]}'
            args_json = json.dumps(arguments, default=str)
            self._call_on_delta(
                on_delta, 'tool_start', {'call_id': call_id, 'name': name}
            )
            self._call_on_delta(
                on_delta,
                'tool_args',
                {'call_id': call_id, 'delta': args_json},
            )
            tool_calls.append(
                {
                    'call_id': call_id,
                    'name': name,
                    'arguments': arguments,
                    '_parse_error': parse_error,
                }
            )
            carry_inputs.append(
                {
                    'type': 'function_call',
                    'name': name,
                    'arguments': args_json,
                    'call_id': call_id,
                }
            )
        return tool_calls, carry_inputs

    def _post_ndjson(self, body: dict) -> Iterator[dict]:
        response = None
        try:
            response = self._http_session().post(
                f'{self.api_url}/api/chat',
                headers=self.headers(),
                json=body,
                timeout=(self.request_timeout, self.idle_timeout),
                stream=True,
            )
            response.raise_for_status()
            response.encoding = 'utf-8'
            for raw_line in response.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                try:
                    yield json.loads(raw_line)
                except ValueError as error:
                    self._raise(error)
        except requests.HTTPError as error:
            self._raise(getattr(error.response, 'text', '') or str(error))
        except requests.RequestException as error:
            self._raise(error)
        finally:
            if response is not None:
                with contextlib.suppress(Exception):
                    response.close()

    def _stream(self, body: dict, on_delta) -> dict:
        text_parts = []
        thinking_parts = []
        raw_calls = []
        metadata = {}
        truncated = False
        for event in self._post_ndjson(body):
            if error := event.get('error'):
                self._raise(error)
            message = event.get('message') or {}
            if text := message.get('content'):
                text_parts.append(text)
                self._call_on_delta(on_delta, 'text', {'delta': text})
            if thinking := message.get('thinking'):
                thinking_parts.append(thinking)
                self._call_on_delta(on_delta, 'reasoning', {'delta': thinking})
            raw_calls.extend(message.get('tool_calls') or [])
            metadata = event
            truncated = truncated or event.get('done_reason') == 'length'
        tool_calls, tool_carries = self._consume_tool_calls(raw_calls, on_delta)
        text = ''.join(text_parts)
        thinking = ''.join(thinking_parts)
        carry_inputs = list(tool_carries)
        content = []
        if thinking:
            content.append({'type': 'muk_ai_thinking', 'thinking': thinking})
        if text:
            content.append({'type': 'output_text', 'text': text})
        if content:
            carry_inputs.insert(0, {'role': 'assistant', 'content': content})
        result = {
            'text': text.strip(),
            'tool_calls': tool_calls,
            'carry_inputs': carry_inputs,
            'usage': self._usage(
                input_tokens=metadata.get('prompt_eval_count'),
                output_tokens=metadata.get('eval_count'),
                cache_read_tokens=metadata.get('prompt_eval_cached_count'),
            ),
        }
        if truncated:
            self._apply_truncation(result, on_delta, self.max_tokens)
        return result
