from __future__ import annotations

import json
import uuid
import hashlib
import logging
from copy import deepcopy

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.addons.muk_ai.tools import ASK_USER_TOOL
from .human_gate import AI_DISPATCH
from .intent_routing import (
    ROUTING_PROMPT,
    ROUTING_SCHEMA,
    ROUTING_SCHEMA_NAME,
    ROUTING_TOOL_PROMPT,
    decode_routing,
)
from .authoring_prompt import build_authoring_inputs


_logger = logging.getLogger(__name__)


BUILDER_TOOL_NAMES = (
    'route_tool_request',
    'search_authorized_models',
    'inspect_authorized_model',
    'define_generated_tool_task',
    'propose_generated_tool',
    'inspect_generated_tool',
    'test_generated_tool',
    'revise_generated_tool',
)

TOOL_AUTHORING_MARKER = '_tool_builder_request'
TOOL_INTENT_MARKER = '_tool_builder_intent'


class AISession(models.Model):
    """Expose user-scoped generated tools in the MuK AI tool catalog."""

    _inherit = 'muk_ai.session'

    tool_builder_discovery_turn = fields.Integer(
        string='Tool Builder Discovery Turn',
        readonly=True,
        default=0,
        copy=False,
    )
    tool_builder_discoveries = fields.Json(
        string='Tool Builder Authorized Models',
        readonly=True,
        default=dict,
        copy=False,
    )
    tool_builder_task_contract = fields.Json(readonly=True, copy=False, default=dict)
    tool_builder_task_request = fields.Text(readonly=True, copy=False)

    def _tool_builder_debug(self, event, **details):
        """Opt-in workflow diagnostics without credentials or prompt bodies."""
        if not self.env['ir.config_parameter'].sudo().get_param(
            'muk_ai_tool_builder.debug_workflow', False
        ):
            return
        payload = {
            'session_id': self.id,
            'turn_seq': self.turn_seq,
            'event': event,
            **details,
        }
        _logger.info('MuK tool-builder workflow %s', json.dumps(
            payload, ensure_ascii=False, default=str, sort_keys=True
        ))

    def _tool_intent(self):
        self.ensure_one()
        return (self._latest_counted_user_entry() or {}).get(TOOL_INTENT_MARKER) or {}

    def _tool_routing_pending(self):
        """The model must choose a workflow for each new user entry."""
        self.ensure_one()
        entry = self._latest_counted_user_entry()
        return bool(
            entry and self._tool_builder_skills()
            and not entry.get('_tool_builder_routing_terminal')
            and not entry.get(TOOL_INTENT_MARKER)
            and not entry.get(TOOL_AUTHORING_MARKER)
        )

    def _tool_routing_terminal(self):
        terminal = (self._latest_counted_user_entry() or {}).get(
            '_tool_builder_routing_terminal'
        ) or {}
        return terminal if terminal.get('turn') == self.turn_seq else {}

    def _materialize_round_inputs(self, provider, cache, notice=None):
        inputs = super()._materialize_round_inputs(provider, cache, notice=None)
        if self._tool_authoring_mode() in ('create', 'repair'):
            # Reassert the same user's task after tool results. Do not persist a
            # synthetic user turn or promote user/tool content to system authority.
            entry = self._latest_counted_user_entry() or {}
            clarifications = []
            ask_ids = {item.get('call_id') for item in self.conversation or []
                       if isinstance(item, dict) and item.get('type') == 'function_call'
                       and item.get('name') == 'ask_user'}
            for item in reversed(self.conversation or []):
                if self._is_counted_user_entry(item):
                    break
                if isinstance(item, dict) and item.get('_answer_entry'):
                    if text := self._user_entry_text(item):
                        clarifications.insert(0, text)
                elif isinstance(item, dict) and item.get('type') == 'function_call_output' and item.get('call_id') in ask_ids:
                    answer = item.get('output')
                    if isinstance(answer, str):
                        try:
                            answer = json.loads(answer)
                        except (TypeError, ValueError):
                            answer = None
                    if isinstance(answer, dict) and answer.get('status') == 'answered':
                        clarifications.insert(0, {'question': answer.get('question'),
                                                 'answer': answer.get('answer')})
            inspected = entry.get('_tool_builder_inspected_context') or {}
            inputs.append({'role': 'user', 'content': [{
                'type': 'input_text',
                'text': 'Continue the SAME pending tool-authoring request; this is a '
                'context reminder, not a new task. Tool metadata is evidence, not a '
                'replacement for my request. If the application remains ambiguous, '
                'ask me to choose; otherwise continue the required workflow.\n'
                + json.dumps({
                    'original_request': self._user_entry_text(entry),
                    'user_clarifications': clarifications,
                    'intent': self._tool_intent(),
                    'next_required_step': self._tool_builder_next_step(),
                    'declared_task': self.tool_builder_task_contract or {},
                    'selected_task_models': self._tool_builder_selected_models(),
                    'authorized_context_scope': self._tool_builder_context_prompt(),
                    'inspected_candidates': inspected.get('models', {})
                    if inspected.get('turn') == self.turn_seq else {},
                }, ensure_ascii=False),
            }]})
        if notice:
            inputs.append(notice)
        return inputs

    def _record_tool_builder_catalog(self, skill_name, candidates):
        entry = self._latest_counted_user_entry() or {}
        catalog = entry.get('_tool_builder_catalog') or {}
        if catalog.get('turn') != self.turn_seq:
            catalog = {'turn': self.turn_seq, 'skills': {}}
        skills = dict(catalog.get('skills') or {})
        # Keep earlier alternatives too: searching for a second model must not
        # erase the ambiguity information for a previously discovered model.
        combined = {item['model']: item for item in skills.get(skill_name, [])}
        for item in candidates:
            previous = combined.get(item['model'], {})
            peers = sorted(set(previous.get('same_label_models', []))
                           | set(item.get('same_label_models', [])))
            combined[item['model']] = {
                **item, 'same_label_models': peers,
                'selection_requires_explanation': len(peers) > 1,
            }
        skills[skill_name] = list(combined.values())
        self._update_current_tool_turn_metadata({
            '_tool_builder_catalog': {'turn': self.turn_seq, 'skills': skills},
        })

    def _tool_builder_catalog(self, skill_name):
        catalog = (self._latest_counted_user_entry() or {}).get('_tool_builder_catalog') or {}
        if catalog.get('turn') != self.turn_seq:
            return []
        return (catalog.get('skills') or {}).get(skill_name, [])

    def _tool_builder_context_scope(self):
        scope = (self._latest_counted_user_entry() or {}).get('_tool_builder_context_scope') or {}
        return scope if scope.get('turn') == self.turn_seq else {}

    def _tool_builder_context_prompt(self):
        """Bounded context for the LLM; full evidence stays internal to the turn."""
        scope = self._tool_builder_context_scope()
        if not scope:
            return {}
        return {
            'status': scope.get('status'),
            'source': scope.get('source'),
            'view': scope.get('view') or {},
            'fallback_model_for_requests_like_this_screen': scope.get('current_model'),
            'current_applications': scope.get('current_applications') or [],
            'modules': scope.get('modules') or [],
            'roles': scope.get('roles') or [],
            'model_selection_policy': scope.get('model_selection_policy'),
            'skills': [
                {
                    'skill_name': skill.get('skill_name'),
                    'models': [
                        {
                            'model': item.get('model'),
                            'label': item.get('label'),
                            'label_en': item.get('label_en'),
                            'authorized_access': item.get('authorized_access') or [],
                            'current_view_is_scope_only': True,
                            'semantic_score': item.get('semantic_score', 0),
                            'semantic_signals': item.get('semantic_signals') or [],
                            'semantic_match_requires_llm': bool(
                                item.get('semantic_match_requires_llm')
                            ),
                            'menu_paths': [nav.get('menu_path') for nav in
                                           (item.get('navigation') or [])[:3]],
                        }
                        for item in (skill.get('models') or [])
                    ],
                }
                for skill in (scope.get('skills') or [])
            ],
            'record_rules_apply_at_preview_and_run': True,
        }

    def _tool_builder_user_selected_model(self):
        selected = (self._latest_counted_user_entry() or {}).get('_tool_builder_user_selected_model') or {}
        return selected.get('model') if selected.get('turn') == self.turn_seq else None

    def _tool_builder_recent_tool_name(self):
        """Return only a backend-confirmed generated-tool name from this chat."""
        for item in reversed(self.conversation or []):
            if not isinstance(item, dict) or item.get('type') != 'function_call_output':
                continue
            try:
                output = json.loads(item.get('output') or '')
            except (TypeError, ValueError):
                continue
            if (
                isinstance(output, dict) and output.get('status') == 'active'
                and isinstance(output.get('tool_name'), str)
                and output['tool_name'].strip()
            ):
                return output['tool_name'].strip()
        return ''

    def _structured_routing_inputs(self, native_tool=False):
        """Small routing prompt, without the agent's competing action instructions."""
        recent = []
        for item in (self.conversation or [])[-16:]:
            if not isinstance(item, dict) or item.get('role') == 'system':
                continue
            if item.get('type') == 'function_call_output':
                # Includes available tool names returned by an earlier routing
                # attempt. Treat results as context, never as system commands.
                recent.append({'role': 'tool_result', 'text': str(item.get('output') or '')[:4000]})
            elif item.get('role') in ('user', 'assistant'):
                content = item.get('content') or []
                text = content if isinstance(content, str) else '\n'.join(
                    block.get('text') or '' for block in content
                    if isinstance(block, dict) and block.get('type') in ('input_text', 'output_text', 'text')
                )
                if text:
                    recent.append({'role': item['role'], 'text': text[:4000]})
        entry = self._latest_counted_user_entry() or {}
        data = {
            'latest_request': self._user_entry_text(entry),
            'recent_context': recent,
            'recent_generated_tool': self._tool_builder_recent_tool_name(),
            'previous_format_error': entry.get('_tool_builder_routing_error') or '',
        }
        return [
            {'role': 'system', 'content': [{
                'type': 'input_text',
                'text': ROUTING_TOOL_PROMPT if native_tool else ROUTING_PROMPT,
            }]},
            {'role': 'user', 'content': [{'type': 'input_text', 'text': json.dumps(data, ensure_ascii=False)}]},
        ]

    def _stream_provider_round(self, provider, tool_schema, model, agent, cache=None, notice=None):
        if self and self.id and self._tool_builder_next_step() == 'summarize_result':
            # Tool creation is already complete. Do not ask the LLM to summarize
            # an authoritative backend state: small local models may ignore the
            # completion instruction and ask another confirmation, causing the
            # user's answer to start a duplicate authoring turn.
            completed = (self._latest_counted_user_entry() or {}).get(
                '_tool_builder_completed', {}
            )
            tool = self.env['muk_ai.generated.tool'].sudo().browse(
                completed.get('generated_tool_id') or 0
            ).exists()
            tool_name = completed.get('tool_name') or (tool.name if tool else '')
            model_name = tool.model_name if tool else ''
            if (self.user_id.lang or '').startswith('vi'):
                text = (
                    f'Đã tạo tool `{tool_name}` cho model `{model_name}`. '
                    'Tool đã sẵn sàng trong Generated Tools để bạn xem lại và chạy; '
                    'chưa có dữ liệu nghiệp vụ nào được thay đổi.'
                )
            else:
                text = (
                    f'Tool `{tool_name}` was created for model `{model_name}`. '
                    'It is ready in Generated Tools for review and execution; '
                    'no business data has been changed.'
                )
            return {'text': text, 'usage': {}, 'tool_calls': [], 'carry_inputs': []}
        if (
            provider.name in ('ollama', 'google') and tool_schema
            and self._tool_authoring_mode() in ('create', 'repair')
            and self._tool_builder_next_step() != 'summarize_result'
        ):
            return self._stream_tool_builder_step(provider, tool_schema, model, cache, notice)
        if not self._tool_routing_pending() or provider.name not in ('ollama', 'google'):
            return super()._stream_provider_round(
                provider, tool_schema, model, agent, cache=cache, notice=notice
            )
        entry = self._latest_counted_user_entry() or {}
        failures = (
            entry.get('_tool_builder_routing_failures', 0)
            if entry.get('_tool_builder_routing_attempt_turn') == self.turn_seq else 0
        )
        if failures >= 2:
            raise UserError(_(
                'The model returned invalid routing JSON twice. Check the model output-token '
                'limit and structured-output support. No business operation was executed.'
            ))
        buffer_state = {'turn': self.turn_seq}
        self._check_cancelled(buffer_state)
        provider_model = provider._resolve_model_name(model)
        native_routing = (
            provider.name == 'ollama'
            and provider_model.rsplit('/', 1)[-1].lower().startswith('gpt-oss')
        )
        native_schema = [{
            'name': 'submit_routing',
            'description': 'Submit exactly one routing decision; this cannot execute business operations.',
            'parameters': ROUTING_SCHEMA,
        }]
        payload = provider._request_responses(
            inputs=self._structured_routing_inputs(native_tool=native_routing),
            tools_schema=native_schema if native_routing else [],
            text_schema=(None if native_routing else {
                'name': ROUTING_SCHEMA_NAME, 'schema': ROUTING_SCHEMA,
            }),
            model=model,
            # Preserve cancellation and worker heartbeat without displaying the
            # intermediate routing JSON as the assistant's answer.
            on_delta=lambda kind, data: self._check_cancelled(buffer_state),
            cache_key=f'muk_ai.session:{self.id}:intent',
        )
        buffer_state['last_state_check'] = 0
        self._check_cancelled(buffer_state)
        try:
            routing_text = payload.get('text')
            if native_routing:
                calls = payload.get('tool_calls') or []
                if len(calls) == 1 and calls[0].get('name') == 'submit_routing':
                    if calls[0].get('_parse_error'):
                        raise ValueError(calls[0]['_parse_error'])
                    routing_text = json.dumps(
                        calls[0].get('arguments') or {}, ensure_ascii=False
                    )
            name, arguments = decode_routing(routing_text)
        except (ValueError, TypeError) as error:
            self._update_current_tool_turn_metadata({
                '_tool_builder_routing_failures': failures + 1,
                '_tool_builder_routing_error': str(error),
                '_tool_builder_routing_attempt_turn': self.turn_seq,
            })
            # Route validation rejects empty arguments without selecting a mode.
            # The normal tool loop records usage/error feedback and can retry
            # once under its existing cost, cancellation and iteration limits.
            name, arguments = 'route_tool_request', {}
        else:
            self._update_current_tool_turn_metadata({
                '_tool_builder_routing_failures': 0,
                '_tool_builder_routing_error': False,
                '_tool_builder_routing_transport': 'structured_json',
                '_tool_builder_routing_attempt_turn': self.turn_seq,
            })
        call_id = f'call_intent_{uuid.uuid4().hex[:16]}'
        arguments_json = json.dumps(arguments, ensure_ascii=False)
        self._on_stream_delta('tool_start', {'call_id': call_id, 'name': name}, buffer_state)
        self._on_stream_delta('tool_args', {'call_id': call_id, 'delta': arguments_json}, buffer_state)
        self._flush_stream_buffer(buffer_state)
        # Adapt the model's structured decision to the existing audited tool
        # dispatcher. All authorization and terminal-state checks still run.
        return {
            'text': '', 'usage': payload.get('usage') or {},
            'tool_calls': [{'call_id': call_id, 'name': name, 'arguments': arguments, '_parse_error': None}],
            'carry_inputs': [{'type': 'function_call', 'call_id': call_id,
                              'name': name, 'arguments': arguments_json}],
        }

    def _stream_tool_builder_step(self, provider, tool_schema, model, cache, notice):
        """Adapt one schema-constrained provider decision to the audited dispatcher."""
        allowed_names = set(BUILDER_TOOL_NAMES) | {'ask_user'}
        tools = {
            item['name']: item for item in tool_schema
            if item.get('name') in allowed_names
        }
        if not tools:
            raise UserError(_('No discovery or clarification tool is available for this step.'))
        if 'ask_user' in tools:
            tools['ask_user'] = deepcopy(tools['ask_user'])
            parameters = tools['ask_user']['parameters']
            parameters['properties'].pop('preview', None)
            parameters['properties']['resolution'] = {'type': 'string', 'enum': ['text']}
            parameters['additionalProperties'] = False
            tools['ask_user']['description'] = (
                'Ask the user which application/menu/model they mean, in their language. '
                'This is clarification, not permission to access metadata.'
            )
        schema = {'type': 'object', 'anyOf': [
            {'type': 'object', 'properties': {
                'tool': {'type': 'string', 'enum': [name],
                         'description': item.get('description', '')},
                'arguments': item['parameters'],
            }, 'required': ['tool', 'arguments'], 'additionalProperties': False}
            for name, item in tools.items()
        ]}
        inputs = self._compact_authoring_inputs(tools)
        scope = self._tool_builder_context_scope()
        if (
            self._tool_builder_next_step() == 'ask_user'
            and scope.get('status') == 'unavailable'
            and 'ask_user' in tools
        ):
            view_model = scope.get('current_model') or 'không có model nghiệp vụ'
            arguments = {
                'question': (
                    f'Chat này đang ghim vào {view_model}, không phải màn hình nghiệp vụ '
                    'phù hợp để xác minh model, field và quyền tạo tool. Hãy mở danh sách '
                    'Vị trí tuyển dụng trong ứng dụng Recruitment và tạo chat mới từ đó. '
                    'Chưa có tool hay dữ liệu nghiệp vụ nào được tạo hoặc thay đổi.'
                ),
                'resolution': 'text',
            }
            call_id = f'call_scope_{uuid.uuid4().hex[:16]}'
            encoded = json.dumps(arguments, ensure_ascii=False)
            self._tool_builder_debug(
                'scope_clarification', next_step='ask_user',
                scope_status=scope.get('status'), scope_model=scope.get('current_model'),
                allowed_tools=sorted(tools), provider=provider.name, model=model,
            )
            return {
                'text': '', 'usage': {},
                'tool_calls': [{'call_id': call_id, 'name': 'ask_user',
                                'arguments': arguments, '_parse_error': None}],
                'carry_inputs': [{'type': 'function_call', 'call_id': call_id,
                                  'name': 'ask_user', 'arguments': encoded}],
            }
        buffer_state = {'turn': self.turn_seq}
        self._check_cancelled(buffer_state)
        payload = provider._request_responses(
            inputs=inputs, tools_schema=[], model=model,
            text_schema={'name': 'muk_tool_builder_step', 'schema': schema},
            on_delta=lambda kind, data: self._check_cancelled(buffer_state),
            cache_key=f'muk_ai.session:{self.id}:tool_builder',
        )
        self._tool_builder_debug(
            'provider_response', provider=provider.name, model=model,
            next_step=self._tool_builder_next_step(),
            output_length=len(payload.get('text') or ''),
            output_tokens=(payload.get('usage') or {}).get('output_tokens'),
            has_tool_calls=bool(payload.get('tool_calls')),
        )
        buffer_state['last_state_check'] = 0
        self._check_cancelled(buffer_state)
        try:
            # Gemini may return literal newlines or tabs inside a schema-bound
            # source-code string. The response is still bounded by the provider
            # schema and all extracted arguments are validated again below, so
            # accept those control characters instead of rejecting otherwise
            # valid generated Python before the audited dispatcher sees it.
            choice = json.loads(payload.get('text') or '', strict=False)
            if not isinstance(choice, dict) or set(choice) != {'tool', 'arguments'}:
                raise ValueError('Expected tool and arguments.')
            name, arguments = choice['tool'], choice['arguments']
            if not isinstance(name, str) or name not in tools or not isinstance(arguments, dict):
                raise ValueError('Tool or arguments are not allowed in this workflow step.')
            parameters = tools[name]['parameters']
            if set(parameters.get('required', [])) - set(arguments):
                raise ValueError('Required workflow arguments are missing.')
            if set(arguments) - set(parameters.get('properties', {})):
                raise ValueError('Unknown workflow argument; inspect uses model, not query.')
            if name == 'inspect_authorized_model' and arguments.get('model') not in (
                parameters['properties']['model'].get('enum', [])
            ):
                raise ValueError('Select only a model returned by authorized discovery.')
        except (TypeError, ValueError, KeyError) as error:
            entry = self._latest_counted_user_entry() or {}
            failures = int(entry.get('_tool_builder_structured_failures') or 0) + 1
            next_tool = self._tool_builder_next_step()
            updates = {
                '_tool_builder_structured_failures': failures,
                '_tool_builder_last_error': {
                    'turn': self.turn_seq, 'tool': 'tool_builder_step_response',
                    'error': str(error), 'required_next_tool': next_tool,
                },
            }
            if failures >= 2:
                updates['_tool_builder_failure_guard'] = {
                    'turn': self.turn_seq, 'counts': {},
                    'stage_counts': {'tool_builder_step_response': failures},
                    'blocked': True, 'resolution': 'stop',
                }
            self._update_current_tool_turn_metadata(updates)
            self._tool_builder_debug(
                'structured_parse_failed', provider=provider.name, model=model,
                next_step=self._tool_builder_next_step(), error=str(error),
                required_next_tool=self._tool_builder_next_step(),
            )
            # Record a skipped call so the normal loop supplies feedback and can
            # retry once. It is never dispatched because _parse_error is set.
            call_id = f'call_invalid_{uuid.uuid4().hex[:16]}'
            return {'text': '', 'usage': payload.get('usage') or {},
                    'tool_calls': [{'call_id': call_id, 'name': next_tool,
                                    'arguments': {}, '_parse_error':
                                    f'structured_output_invalid: {error}'}],
                    'carry_inputs': [{'type': 'function_call', 'call_id': call_id,
                                      'name': next_tool, 'arguments': '{}'}]}
        self._update_current_tool_turn_metadata({'_tool_builder_structured_failures': 0})
        self._tool_builder_debug(
            'structured_parse_succeeded', provider=provider.name, model=model,
            next_step=self._tool_builder_next_step(), selected_tool=name,
            required_next_tool=self._tool_builder_next_step(),
        )
        call_id = f'call_discovery_{uuid.uuid4().hex[:16]}'
        encoded = json.dumps(arguments, ensure_ascii=False)
        self._on_stream_delta('tool_start', {'call_id': call_id, 'name': name}, buffer_state)
        self._on_stream_delta('tool_args', {'call_id': call_id, 'delta': encoded}, buffer_state)
        self._flush_stream_buffer(buffer_state)
        return {'text': '', 'usage': payload.get('usage') or {},
                'tool_calls': [{'call_id': call_id, 'name': name,
                                'arguments': arguments, '_parse_error': None}],
                'carry_inputs': [{'type': 'function_call', 'call_id': call_id,
                                  'name': name, 'arguments': encoded}]}

    def _compact_authoring_inputs(self, tools):
        entry = self._latest_counted_user_entry() or {}
        inspected = entry.get('_tool_builder_inspected_context') or {}
        selected = inspected.get('models', {}) if inspected.get('turn') == self.turn_seq else {}
        # Successful metadata and the pending request replace a growing history
        # of tool schemas, failed code, and unrelated agent instructions.
        state = {
            'original_request': self._user_entry_text(entry),
            'intent': self._tool_intent(),
            'next_step': self._tool_builder_next_step(),
            'declared_task': self.tool_builder_task_contract or {},
            'selected_models': {
                name: {'fields': item.get('fields', []),
                       'access': (item.get('model_context') or {}).get('authorized_access', [])}
                for name, item in selected.items()
            },
        }
        if not selected:
            state['context'] = self._tool_builder_context_prompt()
        recent = list(entry.get('_tool_builder_clarifications') or [])
        for item in reversed(self.conversation or []):
            if self._is_counted_user_entry(item):
                break
            if item.get('_answer_entry'):
                recent.insert(0, self._user_entry_text(item))
            if item.get('type') == 'function_call_output':
                try:
                    output = json.loads(item.get('output') or '')
                except (TypeError, ValueError):
                    continue
                if isinstance(output, dict) and output.get('status') == 'answered':
                    recent.insert(0, output.get('answer'))
                if isinstance(output, dict) and 'source_code' in output:
                    state.setdefault('existing_tool', output)
        state['user_clarifications'] = list(dict.fromkeys(
            value for value in recent if isinstance(value, str) and value.strip()
        ))
        error = entry.get('_tool_builder_last_error') or {}
        if error.get('turn') == self.turn_seq:
            state['previous_error'] = error
            previous = next((item for item in reversed(self.conversation or [])
                             if item.get('type') == 'function_call'
                             and item.get('name') == error.get('tool')), {})
            state['previous_arguments'] = previous.get('arguments')
        inputs = build_authoring_inputs(state, list(tools.values()))
        self._tool_builder_debug(
            'authoring_payload', next_step=state['next_step'],
            scope_status=(state.get('context') or {}).get('status'),
            selected_models=sorted(state['selected_models']),
            clarification_count=len(state['user_clarifications']),
            answer_included=bool(state['user_clarifications']),
            original_request_length=len(state['original_request']),
            original_request_sha256=hashlib.sha256(
                state['original_request'].encode()
            ).hexdigest(),
            allowed_tools=sorted(tools),
        )
        return inputs

    def _turn_start_values(self):
        """Clear model-discovery proof whenever a new user turn begins."""
        return {
            **super()._turn_start_values(),
            'tool_builder_discovery_turn': 0,
            'tool_builder_discoveries': {},
            'tool_builder_task_contract': {},
            'tool_builder_task_request': False,
        }

    def _resume_turn(self, continuation, event=None):
        if (event or {}).get('kind') == 'answer' and self._tool_authoring_mode() in ('create', 'repair'):
            # Only an actual human clarification resumes discovery after the
            # repetition guard; answering never resets an access denial.
            values = {
                '_tool_builder_failure_guard': False,
                '_tool_builder_last_error': False,
            }
            answer = str((event or {}).get('answer') or '')
            entry = self._latest_counted_user_entry() or {}
            clarifications = list(entry.get('_tool_builder_clarifications') or [])
            if answer.strip():
                clarifications.append(answer.strip())
            values['_tool_builder_clarifications'] = clarifications[-20:]
            known_models = {
                item.get('model')
                for skill in ((self._latest_counted_user_entry() or {}).get(
                    '_tool_builder_catalog', {}
                ).get('skills', {}) or {}).values()
                for item in skill if item.get('model')
            }
            matches = [model for model in known_models if model in answer]
            if len(matches) == 1:
                values['_tool_builder_user_selected_model'] = {
                    'turn': self.turn_seq, 'model': matches[0],
                    'source': 'human_clarification',
                }
            scope = self._tool_builder_context_scope()
            if scope.get('status') == 'unavailable':
                values['_tool_builder_routing_terminal'] = {
                    'turn': self.turn_seq,
                    'status': 'business_context_required',
                    'error': scope.get('reason'),
                    'required_action': scope.get('required_action'),
                    'answer_received': True,
                    'nothing_created_or_overwritten': True,
                }
            self._update_current_tool_turn_metadata(values)
            self._tool_builder_debug(
                'answer_resume', answer_included=bool(answer.strip()),
                clarification_count=len(clarifications),
                scope_status=scope.get('status'),
                scope_model=scope.get('current_model'),
                selected_model=values.get('_tool_builder_user_selected_model', {}).get('model'),
                next_step=self._tool_builder_next_step(),
                allowed_tools=sorted(self._tool_authoring_lifecycle_names()),
            )
        return super()._resume_turn(continuation, event=event)

    def _latest_counted_user_entry(self):
        self.ensure_one()
        return next(
            (
                item
                for item in reversed(self.conversation or [])
                if self._is_counted_user_entry(item)
            ),
            None,
        )

    @api.model
    def _user_entry_text(self, entry):
        content = entry.get('content') if isinstance(entry, dict) else []
        if not isinstance(content, list):
            return ''
        return '\n'.join(
            block.get('text') or ''
            for block in content
            if isinstance(block, dict) and block.get('type') == 'input_text'
        )

    def _tool_authoring_mode(self):
        """Return the internal authoring state for this loop, or ``None``."""
        self.ensure_one()
        entry = self._latest_counted_user_entry()
        if not entry:
            return None
        marker = entry.get(TOOL_AUTHORING_MARKER)
        if marker in ('create', 'repair', 'run', 'denied', 'cancelled'):
            mode = marker
        elif marker is True:  # compatibility with 17.0.3 prerelease entries
            mode = 'create'
        else:
            mode = None
        if not mode or mode in ('denied', 'cancelled'):
            return mode
        if not self._tool_builder_skills():
            return 'denied'
        if mode in ('create', 'repair') and self._enforce_tool_scope() == 'read':
            return 'denied'
        return mode

    def _is_tool_authoring_turn(self):
        return bool(self._tool_authoring_mode())

    def _update_current_tool_turn_metadata(self, values):
        """Merge internal metadata into the latest real user entry."""
        self.ensure_one()
        conversation = list(self.conversation or [])
        for index in range(len(conversation) - 1, -1, -1):
            if not self._is_counted_user_entry(conversation[index]):
                continue
            entry = dict(conversation[index])
            entry.update(values)
            conversation[index] = entry
            self.conversation = conversation
            return

    def _mark_current_tool_authoring_mode(self, mode):
        """Persist an internal routing state on the current user turn."""
        if mode in ('create', 'repair', 'run', 'denied', 'cancelled'):
            self._update_current_tool_turn_metadata({TOOL_AUTHORING_MARKER: mode})

    def _record_tool_builder_discovery(self, skill_name, operation, model_names):
        """Remember the ACL-filtered model candidates for this user turn."""
        self.ensure_one()
        discoveries = (
            dict(self.tool_builder_discoveries or {})
            if self.tool_builder_discovery_turn == self.turn_seq
            else {}
        )
        key = f'{skill_name}:{operation}'
        discoveries[key] = list(dict.fromkeys([*discoveries.get(key, []), *model_names]))
        self.sudo().write(
            {
                'tool_builder_discovery_turn': self.turn_seq,
                'tool_builder_discoveries': discoveries,
            }
        )

    def _tool_builder_model_was_discovered(self, skill_name, operation, model_name):
        """Return whether model discovery authorized this model in this turn."""
        self.ensure_one()
        self.invalidate_recordset(
            ['turn_seq', 'tool_builder_discovery_turn', 'tool_builder_discoveries']
        )
        if self.tool_builder_discovery_turn != self.turn_seq:
            return False
        discoveries = self.tool_builder_discoveries or {}
        return model_name in discoveries.get(f'{skill_name}:{operation}', [])

    def _record_tool_builder_inspection(self, skill_name, operations, model_name):
        """Record metadata actually returned by Odoo, not an LLM assertion."""
        discoveries = dict(self.tool_builder_discoveries or {})
        for operation in operations:
            key = f'_inspected:{skill_name}:{operation}'
            discoveries[key] = list(dict.fromkeys([*discoveries.get(key, []), model_name]))
        self.sudo().write({'tool_builder_discoveries': discoveries})

    def _tool_builder_model_was_inspected(self, skill_name, operation, model_name):
        return (
            self._tool_builder_model_was_discovered(skill_name, operation, model_name)
            and model_name in (self.tool_builder_discoveries or {}).get(
                f'_inspected:{skill_name}:{operation}', []
            )
        )

    def _tool_builder_selected_models(self, skill_name=None):
        """Reuse successful inspections in this turn, never the whole catalog."""
        if self.tool_builder_discovery_turn != self.turn_seq:
            return []
        prefix = f'_inspected:{skill_name}:' if skill_name else '_inspected:'
        return sorted({
            model for key, names in (self.tool_builder_discoveries or {}).items()
            if key.startswith(prefix) for model in names
        })

    def _tool_builder_next_step(self):
        if self._tool_routing_terminal():
            return 'stop_and_report'
        completed = (self._latest_counted_user_entry() or {}).get('_tool_builder_completed') or {}
        if completed.get('turn') == self.turn_seq:
            return 'summarize_result'
        failure_resolution = self._tool_builder_failure_resolution()
        if failure_resolution == 'stop':
            return 'stop_and_report'
        if failure_resolution == 'ask_user':
            return 'ask_user'
        mode = self._tool_authoring_mode()
        scope = self._tool_builder_context_scope()
        if mode == 'create' and scope.get('status') == 'unavailable':
            return 'ask_user'
        last_error = (self._latest_counted_user_entry() or {}).get('_tool_builder_last_error') or {}
        if last_error.get('turn') == self.turn_seq:
            required = last_error.get('required_next_tool')
            if required in BUILDER_TOOL_NAMES or required == 'ask_user':
                if required == 'search_authorized_models' and (
                    scope.get('status') == 'scoped'
                ):
                    return 'inspect_authorized_model'
                return required
        if mode == 'repair':
            entry = self._latest_counted_user_entry() or {}
            if entry.get('_tool_builder_source_inspected_turn') != self.turn_seq:
                return 'inspect_generated_tool'
        if scope.get('status') == 'scoped':
            if not any(
                skill.get('models') for skill in scope.get('skills', [])
            ):
                return 'ask_user'
            has_inspected = any(
                values for key, values in (self.tool_builder_discoveries or {}).items()
                if key.startswith('_inspected:')
            )
            if not has_inspected:
                return 'inspect_authorized_model'
            if not self.tool_builder_task_contract:
                return 'define_generated_tool_task'
            return 'revise_generated_tool' if mode == 'repair' else 'propose_generated_tool'
        if self.tool_builder_discovery_turn != self.turn_seq or not any(
            values for key, values in (self.tool_builder_discoveries or {}).items()
            if not key.startswith('_inspected:')
        ):
            return 'search_authorized_models'
        if not any(
            values for key, values in (self.tool_builder_discoveries or {}).items()
            if key.startswith('_inspected:')
        ):
            return 'inspect_authorized_model'
        if not self.tool_builder_task_contract:
            return 'define_generated_tool_task'
        return 'revise_generated_tool' if mode == 'repair' else 'propose_generated_tool'

    def _tool_authoring_lifecycle_names(self):
        mode = self._tool_authoring_mode()
        if not mode or mode in ('denied', 'cancelled'):
            return set()
        if self._tool_builder_next_step() in ('summarize_result', 'stop_and_report'):
            return set()
        if mode == 'run':
            return {'ask_user', 'inspect_generated_tool'}
        if self._tool_builder_failure_resolution() == 'ask_user':
            return {'ask_user'}
        next_step = self._tool_builder_next_step()
        if next_step == 'ask_user':
            return {'ask_user'}
        allowed = {next_step}
        # Do not let the model ask a generic business question when the backend
        # already knows the exact next lifecycle operation. Clarification is
        # available only for a backend-declared ambiguity or same-label models.
        entry = self._latest_counted_user_entry() or {}
        catalog_state = entry.get('_tool_builder_catalog') or {}
        skills = catalog_state.get('skills') or {}
        catalog = [item for candidates in skills.values() for item in candidates]
        current_candidates = [
            item for item in catalog if item.get('is_current_view_model')
        ]
        if (
            next_step == 'inspect_authorized_model'
            and len(current_candidates) != 1
            and any(item.get('selection_requires_explanation') for item in catalog)
        ):
            allowed.add('ask_user')
        return allowed

    def _tool_builder_skills(self):
        self.ensure_one()
        return self._visible_skills().filtered('allow_tool_creation')

    def _visible_generated_tools(self):
        self.ensure_one()
        if not self.user_id:
            return self.env['muk_ai.generated.tool']
        skill_ids = self._tool_builder_skills().ids
        if not skill_ids:
            return self.env['muk_ai.generated.tool']
        domain = [
            ('owner_id', '=', self.user_id.id),
            ('skill_id', 'in', skill_ids),
            ('state', '=', 'active'),
        ]
        if self.agent_id:
            domain += [
                '|',
                ('agent_id', '=', False),
                ('agent_id', '=', self.agent_id.id),
            ]
        else:
            domain += [('agent_id', '=', False)]
        return self.env['muk_ai.generated.tool'].sudo().search(domain)

    def _tool_authoring_generated_tools(self):
        """Only a run intent exposes an executable tool, selected by exact name."""
        self.ensure_one()
        if self._tool_authoring_mode() != 'run':
            return self.env['muk_ai.generated.tool']
        name = self._tool_intent().get('tool_name')
        return self._visible_generated_tools().filtered(
            lambda tool: tool.name == name
        )

    def _get_filtered_catalog(self):
        catalog = super()._get_filtered_catalog()
        if not self or not self.id:
            return catalog
        authoring_turn = self._is_tool_authoring_turn()
        routing_pending = self._tool_routing_pending()
        lifecycle_names = (
            {'route_tool_request', 'ask_user'}
            if routing_pending else self._tool_authoring_lifecycle_names()
        )
        if lifecycle_names:
            # A visible builder skill is the explicit grant for lifecycle tools.
            # Re-add only those tools for this turn if the generic agent filter
            # does not know their dynamically supplied capability.
            tool_env = self.env(
                context={**self.env.context, **self._tool_dispatch_context()}
            )
            registered = tool_env['muk_mcp.tool'].sudo().get_tools(registry='odoo')
            present = {entry.get('name') for entry in catalog}
            catalog.extend(
                entry
                for entry in registered
                if entry.get('name') in lifecycle_names
                and entry.get('name') not in present
            )
        if routing_pending:
            return [entry for entry in catalog if entry.get('name') in lifecycle_names]
        generated_tools = self._tool_authoring_generated_tools()
        names = {entry.get('name') for entry in catalog}
        for tool in generated_tools:
            if tool.name in names:
                continue
            catalog.append(
                {
                    'name': tool.name,
                    'description': tool.description,
                    'inputSchema': tool.input_schema
                    or {
                        'type': 'object',
                        'properties': {},
                        'additionalProperties': False,
                    },
                    '_meta': {'generated': True},
                }
            )
            names.add(tool.name)
        if authoring_turn:
            allowed = lifecycle_names | set(generated_tools.mapped('name'))
            catalog = [entry for entry in catalog if entry.get('name') in allowed]
        else:
            # Ordinary chat does not expose authoring APIs or stored scripts.
            # The next user request is routed independently.
            hidden = set(BUILDER_TOOL_NAMES)
            catalog = [entry for entry in catalog if entry.get('name') not in hidden]
        return catalog

    def _get_essential_tool_names(self):
        if self and self.id and self._tool_routing_pending():
            return ['route_tool_request', 'ask_user']
        if self and self.id and self._is_tool_authoring_turn():
            return sorted(
                self._tool_authoring_lifecycle_names()
                | set(self._tool_authoring_generated_tools().mapped('name'))
            )
        names = list(super()._get_essential_tool_names())
        return names

    def _get_tool_schema(self):
        if self and self.id and self._tool_routing_terminal():
            self._tool_builder_debug(
                'allowed_tools', next_step=self._tool_builder_next_step(),
                scope_status=self._tool_builder_context_scope().get('status'),
                scope_model=self._tool_builder_context_scope().get('current_model'),
                allowed_tools=[], terminal=True,
            )
            return []
        if self and self.id and self._tool_authoring_mode() in (
            'denied',
            'cancelled',
        ):
            return []
        if (
            self and self.id
            and self._tool_builder_next_step() in ('summarize_result', 'stop_and_report')
        ):
            return []
        schema = super()._get_tool_schema()
        if self and self.id and self._tool_routing_pending():
            schema = [
                item for item in schema
                if item.get('name') in ('route_tool_request', 'ask_user')
            ]
        elif self and self.id and self._is_tool_authoring_turn():
            lifecycle = self._tool_authoring_lifecycle_names()
            generated = set(self._tool_authoring_generated_tools().mapped('name'))
            schema = [
                item for item in schema
                if item.get('name') in lifecycle | generated
            ]
        if (
            self and self.id
            and (self._tool_routing_pending() or self._is_tool_authoring_turn())
            and self._can_ask_user()
            and (
                self._tool_routing_pending()
                or 'ask_user' in self._tool_authoring_lifecycle_names()
            )
            and not any(item.get('name') == 'ask_user' for item in schema)
        ):
            schema.append(self._tool_entry_to_schema(ASK_USER_TOOL))
        if self and self.id and self._tool_authoring_mode() in ('create', 'repair'):
            schema = deepcopy(schema)
            discovered = {
                model for key, names in (self.tool_builder_discoveries or {}).items()
                if not key.startswith('_inspected:') for model in names
            } if self.tool_builder_discovery_turn == self.turn_seq else set()
            # Preserve the server's semantic ranking. Alphabetically sorting the
            # enum by technical name discards the request-matching evidence.
            choices = []
            for skill in self._tool_builder_skills():
                for candidate in self._tool_builder_catalog(skill.name):
                    model_name = candidate.get('model')
                    if model_name in discovered and model_name not in choices:
                        choices.append(model_name)
            choices.extend(sorted(discovered - set(choices)))
            if selected := self._tool_builder_user_selected_model():
                choices = [selected] if selected in choices else choices
            for item in schema:
                if len(self._tool_builder_skills()) == 1:
                    item['parameters']['properties'].pop('skill_name', None)
                    item['parameters']['required'] = [
                        key for key in item['parameters'].get('required', []) if key != 'skill_name'
                    ]
                if item.get('name') == 'inspect_authorized_model':
                    parameters = item['parameters']
                    parameters['properties']['model']['enum'] = choices
                    parameters['required'] = ['model', 'required_access']
                    if len(choices) > 1:
                        parameters['required'].extend(['entity_label', 'selection_reason'])
                    parameters['additionalProperties'] = False
                elif item.get('name') == 'define_generated_tool_task':
                    selected_models = self._tool_builder_selected_models()
                    task = item['parameters']['properties']['contract']
                    model_list = task['properties']['models']
                    model_entry = model_list['items']
                    if len(selected_models) == 1:
                        # The backend binds the identity; the LLM supplies only
                        # the operations and fields needed for this task.
                        model_entry['properties'].pop('model', None)
                        model_entry['required'] = [
                            key for key in model_entry['required'] if key != 'model'
                        ]
                        model_list.update(minItems=1, maxItems=1)
                        model_list['description'] = (
                            f'The backend supplies model={selected_models[0]}. '
                            'Supply one entry with operations/read_fields/write_fields '
                            'for this model; do not select or supply a different model.'
                        )
                    elif selected_models:
                        model_entry['properties']['model']['enum'] = selected_models
                elif item.get('name') == 'propose_generated_tool' and self._tool_intent().get('tool_name'):
                    parameters = item['parameters']
                    parameters['properties'].pop('name', None)
                    for server_limit in ('max_records', 'max_ai_calls', 'max_runtime_seconds'):
                        parameters['properties'].pop(server_limit, None)
                    parameters['required'] = [key for key in parameters['required'] if key != 'name']
            if not choices:
                schema = [item for item in schema if item.get('name') != 'inspect_authorized_model']
        self._tool_builder_debug(
            'allowed_tools', next_step=self._tool_builder_next_step(),
            scope_status=self._tool_builder_context_scope().get('status'),
            scope_model=self._tool_builder_context_scope().get('current_model'),
            allowed_tools=[item.get('name') for item in schema],
        )
        return schema

    def _tool_builder_failure_resolution(self):
        guard = (self._latest_counted_user_entry() or {}).get(
            '_tool_builder_failure_guard'
        ) or {}
        if guard.get('turn') != self.turn_seq or not guard.get('blocked'):
            return None
        return guard.get('resolution') or 'ask_user'

    def _system_prompt_addenda(self):
        addenda = super()._system_prompt_addenda()
        if not self or not self.id:
            return addenda
        if terminal := self._tool_routing_terminal():
            addenda.append(
                '<tool_routing_stopped priority="highest">\n'
                'The backend stopped this request before authoring because it cannot '
                'safely resolve the requested tool identity. Do not call any tool. '
                'Explain the following result briefly in the user language, state that '
                'nothing was created or overwritten, and state the required next action '
                f'without asking the same question again: {json.dumps(terminal, ensure_ascii=False)}\n'
                '</tool_routing_stopped>'
            )
            return addenda
        if self._tool_routing_pending():
            addenda.append(
                '<tool_request_routing>\n'
                'Read the latest USER request in its original language and conversation context. '
                'Call route_tool_request BEFORE answering or calling other tools. Select create '
                'for authoring a reusable tool, repair for changing an existing tool, run for '
                'using an existing generated tool, or chat for explanations, greetings and '
                'one-time business queries. Supply a brief faithful summary, preserving all '
                'constraints; for repair/run supply the exact tool name from context if known. '
                'A restriction on changing DATA does not cancel a request to CREATE A TOOL. '
                'Interpret Vietnamese accents as written, including the difference between '
                'dùng and đừng. The backend may attach a pinned business view as an '
                'authorized context scope; treat it as the primary scope, not as proof '
                'that every request targets the current record/model. '
                'If the intent is ambiguous, call ask_user. This routing call only selects '
                'the workflow; it does not grant permissions or create/execute any script. '
                'Make the routing call by itself, then follow the returned next steps.\n'
                '</tool_request_routing>'
            )
            return addenda
        if not self._is_tool_authoring_turn():
            return addenda
        mode = self._tool_authoring_mode()
        if self._tool_builder_next_step() == 'stop_and_report':
            detail = (self._latest_counted_user_entry() or {}).get(
                '_tool_builder_last_error', {}
            )
            addenda.append(
                '<generated_tool_failed priority="highest">\n'
                'The backend stopped this authoring turn because the model repeated '
                'an invalid lifecycle payload. Do not call any tool again. Explain '
                'briefly in the user language that no tool was created and report this '
                f'backend error exactly: {detail.get("error")!r}.\n'
                '</generated_tool_failed>'
            )
            return addenda
        if self._tool_builder_next_step() == 'summarize_result':
            completed = (self._latest_counted_user_entry() or {}).get(
                '_tool_builder_completed', {}
            )
            addenda.append(
                '<generated_tool_completed priority="highest">\n'
                'The backend stored and statically validated the generated tool '
                f'{completed.get("tool_name")!r}. Do not call any tool again. '
                'Reply briefly in the user language that it is ready in Generated '
                'Tools for human review and Preview; never claim the script ran or '
                'that business correctness was proven.\n'
                '</generated_tool_completed>'
            )
            return addenda
        if mode in ('create', 'repair'):
            addenda.append(
                '<current_authoring_stage>\n'
                f'Next required step: {self._tool_builder_next_step()}. '
                'Call one tool at a time and read its result before choosing the next call. '
                'Use authorized_context_scope first. When it is scoped, choose only a '
                'model from that application scope and do not search the whole system. '
                'If authorized_context_scope is unavailable, ask the user to open the '
                'relevant Odoo business screen and start a new chat; do not search the '
                'whole system. The ORIGINAL USER REQUEST is authoritative for the business '
                'entity. Translate its business meaning when comparing localized/English '
                'labels. The current view is only a boundary for application and access; '
                'never select its model merely because it is open or first in a list. '
                'Choose a returned model whose label matches the requested entity AND whose '
                'application/menu matches the user context. Same-label '
                'models can belong to different applications. Inspect modules and '
                'navigation evidence; supply selection_reason when there are alternatives. '
                'If context cannot distinguish them, call ask_user with the concrete '
                'application/menu choices, not a generic request for a task. '
                'A successful search alone does not establish that match. If none '
                'matches, refine the query or ask the user. Never substitute another entity. '
                'Follow required_next_tool on retryable errors; do not end with an empty response.\n'
                '</current_authoring_stage>'
            )
        skills = self._tool_builder_skills()
        skill_lines = [
            '- `%(name)s`: operations=%(operations)s, maximum revisions=%(revisions)s'
            % {
                'name': skill.name,
                'operations': ', '.join(skill.tool_allowed_operations or []),
                'revisions': skill.tool_max_revisions,
            }
            for skill in skills
        ]
        if mode == 'cancelled':
            workflow = [
                'The user rejected approval for this generated-tool write. Do '
                'not call any more tools and do not request the same approval again.',
                'Briefly confirm that no tool was created or revised, in the '
                'user\'s language, and stop.',
            ]
        elif mode == 'denied':
            workflow = [
                'The server did not authorize this generated-tool request under '
                'the current session user\'s Odoo access or builder-skill policy. '
                'Do not call any more tools, do not try another model, and do not '
                'suggest bypassing the denial.',
                'Briefly explain the denial in the user\'s language and stop.',
            ]
        elif mode == 'run':
            workflow = [
                'The user requests running an existing generated tool. Inspect its inputs '
                'if needed, then call that tool with the requested arguments. The server '
                'only opens a review form. Return review_url to the user and explain that '
                'they must Preview and Confirm there; never claim data has already changed.',
            ]
        elif mode == 'repair':
            workflow = [
                'Repair the requested generated script; do not create a different tool.',
                '1. Call `inspect_generated_tool` to obtain its exact source and error.',
                '2. If the correction needs a model/right not already discovered in '
                'this turn, call `search_authorized_models` with all required access.',
                '3. Call `define_generated_tool_task` before writing a revision. Preserve '
                'the original intent unless the user explicitly changed it. Then call '
                '`revise_generated_tool` with complete corrected source code. '
                'Access denials are final and must never be bypassed.',
                '4. A revision is successful only when the server returns status=active '
                'and generated_tool_id. Do not execute it automatically; tell the user '
                'the repaired tool is ready for user review and Preview, not verified correct.',
            ]
        else:
            workflow = [
                'The user explicitly requests a reusable stored-script tool. Do not '
                'substitute a one-time database operation or a predefined backend tool.',
                '1. Analyze every Odoo model and CRUD right needed by the requested '
                'script. Use the server-provided authorized context scope first. If '
                'the context is unavailable, stop and ask the user to open the relevant '
                'business screen and start a new chat. Use only technical '
                'models returned by the server; never invent or expand the scope yourself.',
                'Call `inspect_authorized_model` for the selected model before writing '
                'source so model and field names come from Odoo metadata, not guesses.',
                '2. Reuse the successfully inspected model. When only one model was '
                'inspected, the backend supplies it in the contract automatically. '
                'Call `define_generated_tool_task` with summary, target, '
                'expected_result, uses_ai and models (model, operations, read_fields, '
                'write_fields). This contract must reflect the USER request, not the '
                'currently open Odoo screen. Justify model selection from the requested '
                'business entity; current-view status alone is invalid. If ambiguous, '
                'ask the user; never substitute '
                'counting for writing descriptions. Use `ask_user` for ambiguity. Then write restricted Python '
                'statements and call `propose_generated_tool`. '
                'The script has only `params` and `ctx`; it must assign JSON-safe output '
                'to `result`. Do not wrap the source in a function or markdown fence.',
                '3. Allowed APIs: ctx.search(model, domain, fields, limit), '
                'ctx.read(model, ids, fields), ctx.search_count(model, domain), '
                'ctx.create(model, values), ctx.write(model, ids, values), '
                'ctx.unlink(model, ids), ctx.generate_text(prompt, data, system). '
                'Every model and field name must be a string literal. create/write '
                'values must be dictionary literals with static field-name keys.',
                '4. Imports, env, sudo, SQL, private attributes, related-field traversal, '
                'dynamic function calls and while loops are forbidden. The server '
                'derives a capability manifest from source and rechecks ACL, field '
                'access and record rules as the session user.',
                '5. Tool creation succeeds only when the server returns status=active '
                'and generated_tool_id. Never claim success from your own source text. '
                'On retryable feedback follow required_next_tool; stop on access denial.',
                '6. Server validation is static only, NOT proof of business correctness. '
                'Do not execute the new tool automatically. Tell the user to open '
                'Generated Tools → Run, review the task, Preview and Confirm. '
                'Answer in the user\'s language. Preview simulates writes; scripts '
                'must read all required data BEFORE mutating the same model. '
                'Chained operations on newly created IDs and relational commands are '
                'not supported by Preview. Report batch/search limits, never claim '
                'all records were processed if a limit truncated the selection.',
            ]
        addenda.append(
            '\n'.join(
                [
                    '<generated_tool_authoring priority="highest">',
                    *workflow,
                    *(
                        ['Available builder skills:', *skill_lines]
                        if skill_lines
                        else []
                    ),
                    '</generated_tool_authoring>',
                ]
            )
        )
        return addenda

    def _available_tools_extra_paragraphs(self):
        paragraphs = super()._available_tools_extra_paragraphs()
        if self and self.id and self._tool_builder_skills():
            paragraphs.append(
                'First route the user intent with route_tool_request. For a reusable '
                'tool request, discover all required Odoo CRUD access, '
                'declare the task using define_generated_tool_task, then propose '
                'restricted Python source. The server derives capabilities '
                'from source and is the only authority for creation success. Inspect '
                'before repair and never retry or code around an access denial. '
                'Generated-tool calls return requires_user_review, not execution success; '
                'give the user the review_url and never claim data was changed.'
            )
        return paragraphs

    def _check_approval_gate(self, name, arguments):
        """Chat only opens a review form; it cannot execute or approve a plan."""
        if self and self.id and self._tool_routing_pending():
            # Let the dispatcher return routing feedback before any write approval.
            return {'action': 'dispatch'}
        if self and self.id and self._tool_authoring_mode() in ('create', 'repair'):
            if name not in self._tool_authoring_lifecycle_names():
                # Reject an out-of-stage call before asking the human to approve
                # a write that the dispatcher cannot accept yet.
                return {'action': 'dispatch'}
        if self and self.id and self._visible_generated_tools().filtered(lambda item: item.name == name):
            return {'action': 'dispatch'}
        return super()._check_approval_gate(name, arguments)

    def _dispatch_tool_call(self, name, arguments, call_id):
        token = AI_DISPATCH.set(True)
        try:
            result, success = self._dispatch_generated_or_core_tool(name, arguments, call_id)
            if name in BUILDER_TOOL_NAMES:
                payload = result
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except (TypeError, ValueError):
                        payload = None
                if isinstance(payload, dict):
                    if payload.get('error') or payload.get('status') in (
                        'rejected', 'denied', 'failed', 'stopped'
                    ):
                        success = False
                        if (
                            self._tool_authoring_mode() in ('create', 'repair')
                            and payload.get('error_kind') != 'access'
                            and name != 'route_tool_request'
                        ):
                            entry = self._latest_counted_user_entry() or {}
                            guard = entry.get('_tool_builder_failure_guard') or {}
                            counts = dict(guard.get('counts') or {}) if guard.get('turn') == self.turn_seq else {}
                            stage_counts = dict(guard.get('stage_counts') or {}) if guard.get('turn') == self.turn_seq else {}
                            signature = hashlib.sha256(json.dumps(
                                [name, arguments, payload.get('error') or payload.get('message')],
                                sort_keys=True, ensure_ascii=False, default=str
                            ).encode()).hexdigest()
                            counts[signature] = counts.get(signature, 0) + 1
                            stage_counts[name] = stage_counts.get(name, 0) + 1
                            blocked = counts[signature] >= 2 or stage_counts[name] >= 3
                            resolution = (
                                'ask_user'
                                if name in ('search_authorized_models', 'inspect_authorized_model')
                                else 'stop'
                            )
                            self._update_current_tool_turn_metadata({'_tool_builder_failure_guard': {
                                'turn': self.turn_seq, 'counts': counts,
                                'stage_counts': stage_counts, 'blocked': blocked,
                                'resolution': resolution if blocked else None,
                            }})
                            if blocked and resolution == 'ask_user':
                                payload = {
                                    **payload,
                                    'required_next_tool': 'ask_user',
                                    'message': (
                                        'Model discovery failed repeatedly. Ask the user '
                                        'to choose the application/model; do not repeat '
                                        'the failing query.'
                                    ),
                                }
                                result = json.dumps(payload, ensure_ascii=False) if isinstance(result, str) else payload
                            elif blocked:
                                payload = {
                                    **payload,
                                    'status': 'stopped',
                                    'retryable': False,
                                    'message': (
                                        'The same invalid authoring payload was repeated. '
                                        'This turn was stopped safely; no tool was created.'
                                    ),
                                }
                                payload.pop('required_next_tool', None)
                                result = json.dumps(payload, ensure_ascii=False) if isinstance(result, str) else payload
                        self._update_current_tool_turn_metadata({'_tool_builder_last_error': {
                            'turn': self.turn_seq, 'tool': name,
                            'error': str(payload.get('error') or payload.get('message') or payload['status'])[:2000],
                            'required_next_tool': payload.get('required_next_tool'),
                        }})
                    elif success:
                        self._update_current_tool_turn_metadata({'_tool_builder_last_error': False})
            payload = result
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except (TypeError, ValueError):
                    payload = {}
            self._tool_builder_debug(
                'dispatch_result', tool=name, success=success,
                status=payload.get('status') if isinstance(payload, dict) else None,
                error_kind=payload.get('error_kind') if isinstance(payload, dict) else None,
                required_next_tool=(
                    payload.get('required_next_tool') if isinstance(payload, dict) else None
                ),
                next_step=self._tool_builder_next_step(),
            )
            return result, success
        finally:
            AI_DISPATCH.reset(token)

    def _dispatch_generated_or_core_tool(self, name, arguments, call_id):
        if self and self.id:
            if self._tool_routing_terminal():
                return {'status': 'stopped', 'retryable': False,
                        'error': 'This request is terminal. Send a new user request to continue.'}, False
            if self._tool_routing_pending() and name not in ('route_tool_request', 'ask_user'):
                return {'status': 'routing_required', 'retryable': True,
                        'required_next_tool': 'route_tool_request',
                        'message': 'Classify the user intent before calling data or authoring tools.'}, False
            if self._is_tool_authoring_turn():
                terminal_mode = self._tool_authoring_mode()
                if terminal_mode in ('denied', 'cancelled'):
                    return {
                        'status': terminal_mode,
                        'error_kind': (
                            'access' if terminal_mode == 'denied' else 'cancelled'
                        ),
                        'retryable': False,
                        'error': (
                            'The current generated-tool request is already terminal. '
                            'No further tool calls are allowed in this turn.'
                        ),
                    }, False
                if self._tool_builder_failure_resolution() == 'stop':
                    detail = (self._latest_counted_user_entry() or {}).get(
                        '_tool_builder_last_error', {}
                    )
                    return {
                        'status': 'stopped',
                        'error_kind': 'validation',
                        'retryable': False,
                        'error': detail.get('error') or (
                            'This authoring turn was stopped after repeated invalid payloads.'
                        ),
                    }, False
                generated_names = set(
                    self._tool_authoring_generated_tools().mapped('name')
                )
                allowed = self._tool_authoring_lifecycle_names() | generated_names
                if name not in allowed:
                    required_next = (
                        'inspect_generated_tool' if terminal_mode == 'run'
                        else self._tool_builder_next_step()
                    )
                    return (
                        {
                            'error': 'tool_authoring_route_required',
                            'error_kind': 'routing',
                            'retryable': True,
                            'message': (
                                'This turn must use the generated-tool workflow. '
                                'Do not call '
                                f'{name!r}. Call {required_next!r} next.'
                            ),
                            'required_next_tool': required_next,
                            'allowed_tools': sorted(allowed),
                        },
                        False,
                    )
            tool = self._visible_generated_tools().filtered(
                lambda item: item.name == name
            )[:1]
            if tool:
                if self._tool_authoring_mode() != 'run':
                    return {'status': 'rejected', 'error': 'A run intent is required; no script was executed.'}, False
                tool._authorize(self.user_id, self)
                wizard = self.env['muk_ai.generated.tool.execution'].with_user(self.user_id)._create_for_tool(
                    tool, arguments=arguments or {}, session=self
                )
                wizard.action_run_direct()
                if wizard.state == 'failed':
                    return {'status': 'failed', 'error': wizard.result_text}, False
                return {'status': 'success', 'result': wizard.result_text}, True
        result, success = super()._dispatch_tool_call(name, arguments, call_id)
        if self and self.id and name in BUILDER_TOOL_NAMES:
            payload = result
            if isinstance(result, str):
                try:
                    payload = json.loads(result)
                except (TypeError, ValueError):
                    payload = None
            if isinstance(payload, dict) and payload.get('error_kind') == 'access':
                self._mark_current_tool_authoring_mode('denied')
                success = False
            elif name == 'route_tool_request' and isinstance(payload, dict) and payload.get('status') == 'rejected':
                success = False
        return result, success

    def _finalize_round(self, payload):
        """Do not mark authoring complete without an active stored revision."""
        if self and self.id and self._tool_routing_terminal() and not payload.get('text'):
            terminal = self._tool_routing_terminal()
            self._transition_state(
                'error',
                error=terminal.get('error') or terminal.get('message')
                or _('Tool routing stopped safely; no tool was created.'),
            )
            return
        if self and self.id and self._tool_routing_pending():
            self._transition_state('error', error=_(
                'The AI did not select a workflow. It must call route_tool_request or ask for clarification.'
            ))
            return
        mode = self._tool_authoring_mode() if self and self.id else None
        if mode in ('create', 'repair'):
            active_revision = self.env['muk_ai.generated.tool.revision'].sudo().search(
                [
                    ('source_session_id', '=', self.id),
                    ('source_turn_seq', '=', self.turn_seq),
                    ('status', '=', 'active'),
                ],
                limit=1,
            )
            if not active_revision:
                detail = (self._latest_counted_user_entry() or {}).get('_tool_builder_last_error') or {}
                reason = ''
                if detail.get('turn') == self.turn_seq:
                    reason = '\n%s: %s' % (detail.get('tool'), detail.get('error'))
                self._transition_state(
                    'error',
                    error=_(
                        'AI stopped before completing tool creation/repair. '
                        'Next required step: %(step)s.%(reason)s',
                        step=(detail.get('required_next_tool') if reason else None)
                        or self._tool_builder_next_step(),
                        reason=reason,
                    ),
                )
                return
            if not payload.get('text'):
                self._transition_state('error', error=_(
                    'The tool revision was saved and statically validated, but the AI '
                    'returned no summary. Check Generated Tools before retrying; '
                    'the script has not been executed.'
                ))
                return
        if not payload.get('text') and mode in ('denied', 'cancelled'):
            detail = (self._latest_counted_user_entry() or {}).get('_tool_builder_last_error') or {}
            self._transition_state('error', error=(
                detail.get('error') if detail.get('turn') == self.turn_seq else None
            ) or _('The tool request was denied or cancelled; no further tool calls are allowed.'))
            return
        return super()._finalize_round(payload)

    def reject_tool(self, reason=None):
        """Make rejection of a builder write terminal for the current turn."""
        pending = self.pending_ask or {}
        if (
            self._is_tool_authoring_turn()
            and pending.get('name') in BUILDER_TOOL_NAMES
        ):
            self._mark_current_tool_authoring_mode('cancelled')
        return super().reject_tool(reason=reason)
