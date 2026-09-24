from __future__ import annotations

from collections import Counter
import unicodedata
import uuid

from odoo import _, api, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.muk_mcp.core.tool import get_tool_index, mcp_tool
from ..models.session import TOOL_AUTHORING_MARKER, TOOL_INTENT_MARKER


ACCESS_RIGHTS = ('read', 'create', 'write', 'unlink')
OBJECT_SCHEMA = {
    'type': 'object',
    'properties': {},
    'additionalProperties': False,
}
TASK_SCHEMA = {
    'type': 'object',
    'properties': {
        'summary': {'type': 'string'}, 'target': {'type': 'string'},
        'expected_result': {'type': 'string'}, 'uses_ai': {'type': 'boolean'},
        'output_language': {
            'type': 'string',
            'description': 'Required language of AI-generated business content, e.g. Vietnamese.',
        },
        'content_requirements': {
            'type': 'array', 'items': {'type': 'string'},
            'description': 'Concrete style, factual-grounding and target-format requirements.',
        },
        'models': {
            'type': 'array', 'items': {
                'type': 'object', 'properties': {
                    'model': {'type': 'string'},
                    'operations': {'type': 'array', 'items': {'type': 'string', 'enum': list(ACCESS_RIGHTS)}},
                    'read_fields': {'type': 'array', 'items': {'type': 'string'}},
                    'write_fields': {'type': 'array', 'items': {'type': 'string'}},
                },
                'required': ['model', 'operations', 'read_fields', 'write_fields'],
                'additionalProperties': False,
            },
        },
    },
    'required': ['summary', 'target', 'expected_result', 'uses_ai',
                 'output_language', 'content_requirements', 'models'],
    'additionalProperties': False,
}


class ToolBuilderMixin(models.AbstractModel):
    """Let an LLM author, validate, test and repair stored script tools."""

    _inherit = 'muk_mcp.mixin'

    @api.model
    def _tool_builder_words(self, value):
        """Tokenize metadata without stripping meaningful Unicode accents."""
        normalized = unicodedata.normalize('NFKC', value or '').casefold()
        return [
            token for token in ''.join(
                char if char.isalnum() else ' ' for char in normalized
            ).split()
            if len(token) > 1
        ]

    @api.model
    def _rank_scoped_models(self, candidates, request):
        """Rank by request/metadata evidence; the current view is never a rank boost."""
        request_words = set(self._tool_builder_words(request))
        request_phrase = ' '.join(self._tool_builder_words(request))
        evidence = {}
        frequencies = Counter()
        for candidate in candidates:
            navigation = candidate.get('navigation') or []
            sources = {
                'localized_label': candidate.get('label') or '',
                'english_label': candidate.get('label_en') or '',
                'technical_model': candidate.get('model') or '',
                'navigation': ' '.join(
                    value
                    for item in navigation
                    for value in (
                        item.get('application') or '',
                        item.get('menu_path') or '',
                        item.get('action_name') or '',
                    )
                    if value
                ),
            }
            source_words = {
                source: set(self._tool_builder_words(value))
                for source, value in sources.items()
            }
            all_words = set().union(*source_words.values()) if source_words else set()
            evidence[candidate['model']] = (sources, source_words, all_words)
            frequencies.update(all_words)

        ranked = []
        source_weights = {
            'localized_label': 12,
            'english_label': 10,
            'technical_model': 8,
            'navigation': 6,
        }
        for candidate in candidates:
            sources, source_words, _all_words = evidence[candidate['model']]
            score = 0
            signals = []
            for source, words in source_words.items():
                matches = request_words & words
                if matches:
                    # Candidate-specific terms count more than application-wide
                    # words such as "recruitment".
                    score += sum(
                        source_weights[source] * 100 // frequencies[word]
                        for word in matches
                    )
                    signals.append({
                        'source': source,
                        'matched_terms': sorted(matches),
                    })
                phrase = ' '.join(self._tool_builder_words(sources[source]))
                if phrase and len(phrase) > 2 and phrase in request_phrase:
                    score += source_weights[source] * 200
                    signals.append({'source': source, 'exact_phrase': phrase})
            ranked_candidate = dict(candidate)
            ranked_candidate.update({
                'semantic_score': score,
                'semantic_signals': signals,
                'semantic_match_requires_llm': not bool(signals),
                'current_view_is_scope_only': True,
            })
            ranked.append(ranked_candidate)
        return sorted(
            ranked,
            key=lambda item: (
                -item['semantic_score'],
                (item.get('label') or '').casefold(),
                item['model'],
            ),
        )

    def _tool_builder_session(self, allowed_modes=('create', 'repair', 'run')):
        session_id = self.env.context.get('muk_mcp_session_id')
        if not session_id:
            raise UserError(_('AI-generated tools require an active MuK AI session.'))
        session = self.env['muk_ai.session'].sudo().browse(session_id).exists()
        if not session:
            raise UserError(_('The MuK AI session no longer exists.'))
        if self.env.uid != session.user_id.id:
            raise AccessError(_('You cannot build tools for another user session.'))
        mode = session._tool_authoring_mode()
        if allowed_modes is not None and mode not in allowed_modes:
            if mode in ('denied', 'cancelled'):
                raise AccessError(_('This tool request has been denied or cancelled.'))
            raise ValidationError(_('Call route_tool_request and select the appropriate workflow first.'))
        return session

    @api.model
    @mcp_tool(
        name='route_tool_request',
        description=(
            'Understand the user request in its original language and select the workflow. '
            'create = author a reusable tool; repair = fix an existing generated tool; '
            'run = request execution of an existing generated tool; chat = ordinary '
            'conversation, explanation or a one-time business query. Distinguish creating '
            'a TOOL from creating DATA. Call this first, by itself, then follow next steps. '
            'This call does not create a script, execute data operations or grant access.'
        ),
        input_schema={
            'type': 'object',
            'properties': {
                'intent': {'type': 'string', 'enum': ['create', 'repair', 'run', 'chat']},
                'summary': {'type': 'string', 'description': 'Brief interpretation of the user intent and constraints, in their language.'},
                'tool_name': {
                    'type': 'string',
                    'description': (
                        'Exact requested name for create, or exact existing generated '
                        'tool name for repair/run, if known.'
                    ),
                },
            },
            'required': ['intent', 'summary'], 'additionalProperties': False,
        },
        category='read', registry='odoo',
    )
    def _mcp_route_tool_request(self, intent=None, summary=None, tool_name=None):
        try:
            session = self._tool_builder_session(allowed_modes=None)
            if session._tool_authoring_mode() in ('denied', 'cancelled'):
                raise AccessError(_('This tool request has been denied or cancelled.'))
            if intent not in ('create', 'repair', 'run', 'chat'):
                raise ValidationError(_('Choose create, repair, run or chat.'))
            if not isinstance(summary, str) or not summary.strip() or len(summary) > 2000:
                raise ValidationError(_('A brief intent summary is required (maximum 2000 characters).'))
            if tool_name is not None and (not isinstance(tool_name, str) or len(tool_name) > 255):
                raise ValidationError(_('tool_name must be a short string.'))
            if not session._latest_counted_user_entry():
                raise ValidationError(_('A current user message is required.'))
            previous = session._tool_intent()
            if intent == 'create':
                request = session._user_entry_text(session._latest_counted_user_entry())
                # A name invented by the router is not a user requirement.
                # Stable identifiers are assigned once by the server.
                if previous.get('intent') == 'create':
                    tool_name = previous.get('tool_name')
                elif not tool_name or tool_name not in request:
                    tool_name = f'generated_tool_{uuid.uuid4().hex[:12]}'
            if previous and (previous.get('intent') != intent or previous.get('tool_name') != (tool_name or None)):
                raise ValidationError(_('The workflow has already been selected for this user request. Ask the user for a new request to change it.'))
            skills = session._tool_builder_skills()
            if intent != 'chat' and not skills:
                raise AccessError(_('No tool-building skill is available to this user and agent.'))
            if intent in ('create', 'repair') and session._enforce_tool_scope() == 'read':
                raise AccessError(_('This session is readonly and cannot create or revise tools.'))
            if intent == 'create' and tool_name:
                generated = self.env['muk_ai.generated.tool'].sudo().search(
                    [('owner_id', '=', session.user_id.id), ('name', '=', tool_name)],
                    limit=1,
                )
                registered = tool_name in get_tool_index(self.env, registry='odoo')
                if generated or registered:
                    error = ValidationError(_(
                        'Tool name %(name)r already exists. Send a new request either '
                        'to repair the existing generated tool or to create one with a '
                        'different name.',
                        name=tool_name,
                    ))
                    result = self._tool_builder_error_result(
                        error,
                        tool_name=tool_name,
                        retryable=False,
                        extra={
                            'name_available': False,
                            'can_repair': bool(generated),
                            'conflict_type': (
                                'generated_tool' if generated else 'registered_tool'
                            ),
                            'next_request_options': (
                                [
                                    f'Repair the existing tool {tool_name}',
                                    'Create the tool with a different name',
                                ]
                                if generated else ['Create the tool with a different name']
                            ),
                            'nothing_created_or_overwritten': True,
                        },
                    )
                    session._update_current_tool_turn_metadata({
                        '_tool_builder_routing_terminal': {
                            'turn': session.turn_seq,
                            'status': 'name_conflict',
                            'error': str(error),
                            'tool_name': tool_name,
                            'can_repair': bool(generated),
                            'next_request_options': result['next_request_options'],
                            'nothing_created_or_overwritten': True,
                        },
                    })
                    return result
            if intent in ('repair', 'run'):
                domain = [('owner_id', '=', session.user_id.id), ('skill_id', 'in', skills.ids),
                          '|', ('agent_id', '=', False), ('agent_id', '=', session.agent_id.id or False)]
                if intent == 'run':
                    domain.append(('state', '=', 'active'))
                tool_model = self.env['muk_ai.generated.tool'].sudo()
                if not tool_name:
                    # Resolve "the tool just created" from audited backend output,
                    # never from an LLM-invented display name.
                    tool_name = session._tool_builder_recent_tool_name() or None
                tool = tool_model.search(domain + [('name', '=', tool_name)], limit=1) if tool_name else tool_model
                if not tool:
                    return {'status': 'needs_tool_selection', 'required_next_tool': 'route_tool_request',
                            'retryable': True, 'intent': intent,
                            'available_tools': tool_model.search(domain, limit=30).mapped('name'),
                            'message': 'Select the exact existing tool from these names, or ask the user to clarify. Do not invent a tool name.'}
                if tool.state == 'disabled' or tool.last_error_kind == 'access':
                    raise AccessError(_('An access denial cannot be bypassed by selecting a different workflow.'))
                if intent == 'run':
                    tool._authorize(session.user_id, session)
            session._update_current_tool_turn_metadata({
                TOOL_INTENT_MARKER: {'intent': intent, 'summary': summary.strip(),
                                     'tool_name': tool_name or None,
                                     'source': (session._latest_counted_user_entry() or {}).get(
                                         '_tool_builder_routing_transport', 'model_tool_call'
                                     )},
                TOOL_AUTHORING_MARKER: intent if intent != 'chat' else False,
            })
            context_scope = (
                self._bootstrap_tool_builder_context(session, skills)
                if intent == 'create' else {}
            )
            return {'status': 'routed', 'intent': intent, 'summary': summary.strip(),
                    'tool_name': tool_name or None,
                    'context_scope': context_scope,
                    'next_step': (session._tool_builder_next_step() if intent == 'create'
                                  else {'repair': 'inspect_generated_tool',
                                        'run': 'inspect_generated_tool',
                                        'chat': 'Reply directly to the user in natural text (greetings/answers). Do NOT call any additional tools (no ask_user, no read, no write, no search).',}[intent]),
                    'authorization_granted': False}
        except (AccessError, UserError) as error:
            return self._tool_builder_error_result(error, required_next_tool='route_tool_request')

    def _tool_builder_skill(self, session, name=None):
        skills = session._tool_builder_skills()
        if len(skills) == 1:
            return skills
        if name:
            skills = skills.filtered(lambda item: item.name == name)
        if len(skills) == 1:
            return skills
        if not skills:
            raise AccessError(
                _('Tool-building skill %(skill)r is not available.', skill=name or '')
            )
        raise ValidationError(
            _('More than one tool-building skill is available; provide skill_name.')
        )

    @api.model
    def _bootstrap_tool_builder_context(self, session, skills):
        """Build an ACL-filtered module scope from MuK AI's pinned UI context."""
        view = dict(session.view_context or {})
        model_name = view.get('model')
        model_registry = self.env['ir.model'].sudo()
        current = model_registry.search([('model', '=', model_name)], limit=1) if model_name else model_registry
        if (
            not current
            or current.transient
            or model_name == 'res.config.settings'
            or model_name.startswith(('ir.', 'muk_ai.', 'muk_mcp.'))
        ):
            visible_model = model_name or None
            result = {
                'status': 'unavailable',
                'source': 'pinned_view_context',
                'view': {key: view[key] for key in (
                    'kind', 'model', 'view_type', 'action_id'
                ) if key in view},
                'current_model': visible_model,
                'reason': (
                    f'The pinned model {visible_model!r} is not an allowed persistent '
                    'business model for generated tools.'
                    if visible_model else 'No pinned business-model context is available.'
                ),
                'required_action': (
                    'Open the relevant Recruitment business screen and start a new chat. '
                    'Do not search all Odoo models.'
                ),
            }
            session._update_current_tool_turn_metadata({
                '_tool_builder_context_scope': {'turn': session.turn_seq, **result},
            })
            session._tool_builder_debug(
                'scope_bootstrap', scope_status=result['status'],
                scope_model=result.get('current_model'), reason=result['reason'],
                next_step=session._tool_builder_next_step(),
            )
            return result

        module_names = {
            value.strip() for value in (current.modules or '').split(',') if value.strip()
        }
        navigation = self._model_navigation(session, [record.model for record in model_registry.search([])])
        action_id = view.get('action_id')
        current_navigation = [
            item for item in navigation.get(model_name, [])
            if action_id and item.get('action_id') == action_id
        ]
        current_applications = {
            item.get('application') for item in current_navigation
            if item.get('application')
        }
        scoped_records = model_registry.browse()
        for record in model_registry.search([]):
            record_modules = {
                value.strip() for value in (record.modules or '').split(',') if value.strip()
            }
            record_navigation = navigation.get(record.model, [])
            in_current_application = not current_applications or any(
                item.get('application') in current_applications
                for item in record_navigation
            )
            if record.model == model_name or (
                module_names & record_modules
                and record_navigation
                and in_current_application
            ):
                scoped_records |= record

        group_xmlids = session.user_id.groups_id.sudo().get_external_id()
        roles = []
        for group in session.user_id.groups_id:
            xmlid = group_xmlids.get(group.id) or ''
            if any(xmlid.startswith(f'{module}.') for module in module_names):
                roles.append({'name': group.name, 'xmlid': xmlid})

        skill_scopes = []
        request = session._user_entry_text(session._latest_counted_user_entry() or {})
        for skill in skills:
            candidates = []
            for record in scoped_records:
                access = []
                for operation in ACCESS_RIGHTS:
                    try:
                        skill._check_tool_builder_policy(
                            user=session.user_id, session=session,
                            model_name=record.model, operation=operation,
                        )
                    except (AccessError, KeyError):
                        continue
                    access.append(operation)
                if not access:
                    continue
                candidates.append({
                    'model': record.model,
                    'label': record.name,
                    'label_en': record.with_context(lang='en_US').name,
                    'modules': [value.strip() for value in (record.modules or '').split(',') if value.strip()],
                    'navigation': navigation.get(record.model, [])[:5],
                    'authorized_access': access,
                    'is_current_view_model': record.model == model_name,
                })
            # The open view defines the safe application scope, but it must not
            # bias the requested business entity. Rank against the original
            # natural-language request and real Odoo metadata only.
            candidates = self._rank_scoped_models(candidates, request)[:30]
            label_groups = {}
            for candidate in candidates:
                label_groups.setdefault(candidate['label_en'], []).append(candidate['model'])
            for candidate in candidates:
                peers = label_groups[candidate['label_en']]
                candidate['same_label_models'] = peers
                candidate['selection_requires_explanation'] = len(peers) > 1
            for candidate in candidates:
                for operation in candidate['authorized_access']:
                    session._record_tool_builder_discovery(
                        skill.name, operation, [candidate['model']]
                    )
            session._record_tool_builder_catalog(skill.name, candidates)
            skill_scopes.append({
                'skill_name': skill.name,
                'allowed_operations': skill.tool_allowed_operations or [],
                'models': candidates,
            })

        result = {
            'status': 'scoped',
            'source': 'pinned_view_context',
            'view': {key: view[key] for key in ('kind', 'model', 'view_type', 'action_id') if key in view},
            'current_model': model_name,
            'current_applications': sorted(current_applications),
            'modules': sorted(module_names),
            'roles': roles,
            'skills': skill_scopes,
            'authorization_basis': 'current_user_groups_and_live_odoo_acl',
            'record_rules_apply_to_records_at_preview_and_run': True,
            'model_limit': 30,
            'model_selection_policy': (
                'Match the original user request to model labels and navigation. '
                'The current view limits scope only and is never evidence that it is the target.'
            ),
            'note': 'Role names are context only. Backend ACL, field access and record rules remain authoritative.',
        }
        session._update_current_tool_turn_metadata({
            '_tool_builder_context_scope': {'turn': session.turn_seq, **result},
        })
        session._tool_builder_debug(
            'scope_bootstrap', scope_status=result['status'],
            scope_model=result.get('current_model'), modules=result.get('modules'),
            candidate_models={item['skill_name']: [
                model['model'] for model in item['models']
            ] for item in skill_scopes}, next_step=session._tool_builder_next_step(),
        )
        return result

    @api.model
    def _tool_builder_error_result(
        self,
        error,
        *,
        tool_name=None,
        required_next_tool=None,
        retryable=None,
        extra=None,
    ):
        access_denied = isinstance(error, AccessError)
        can_retry = not access_denied if retryable is None else bool(retryable)
        result = {
            'status': 'denied' if access_denied else 'rejected',
            'error_kind': 'access' if access_denied else 'validation',
            'error': str(error),
            'retryable': can_retry,
        }
        if tool_name:
            result['tool_name'] = tool_name
        if required_next_tool and can_retry:
            result['required_next_tool'] = required_next_tool
        if extra:
            result.update(extra)
        return result

    @api.model
    def _activate_generated_schema(self, session, result):
        if result.get('status') != 'active':
            return
        name = result['tool_name']
        expanded = list(session.expanded_tool_names or [])
        if name not in expanded:
            session.sudo().write({'expanded_tool_names': expanded + [name]})
        session._update_current_tool_turn_metadata({
            '_tool_builder_completed': {
                'turn': session.turn_seq,
                'tool_name': name,
                'generated_tool_id': result.get('generated_tool_id'),
            }
        })

    @api.model
    def _missing_discoveries(self, session, skill, manifest):
        missing = []
        for capability in manifest.get('models', []):
            for access in capability.get('operations', []):
                if not session._tool_builder_model_was_discovered(
                    skill.name, access, capability['model']
                ):
                    missing.append(
                        {'model': capability['model'], 'access': access}
                    )
        return missing

    @api.model
    @mcp_tool(
        name='define_generated_tool_task',
        description='Declare the user-requested task BEFORE writing source. Never replace a write task with a count task. The user will review this declaration before Run.',
        input_schema={'type': 'object', 'properties': {'contract': TASK_SCHEMA, 'skill_name': {'type': 'string'}},
                      'required': ['contract'], 'additionalProperties': False},
        category='read', registry='odoo',
    )
    def _mcp_define_generated_tool_task(self, contract=None, skill_name=None, **extra):
        try:
            session = self._tool_builder_session(allowed_modes=('create', 'repair'))
            skill = self._tool_builder_skill(session, skill_name)
            tool_model = self.env['muk_ai.generated.tool']
            task_keys = set(TASK_SCHEMA['properties'])
            flat = {key: extra[key] for key in task_keys if key in extra}
            unknown = set(extra) - task_keys
            if unknown or (contract is not None and flat):
                raise ValidationError(_(
                    'Provide either contract or the five task fields, without unknown arguments.'
                ))
            if contract is None and flat:
                contract = flat
            selected_models = session._tool_builder_selected_models(skill.name)
            if not selected_models:
                return self._tool_builder_error_result(
                    ValidationError(_('Inspect the requested model before defining its task.')),
                    required_next_tool='inspect_authorized_model',
                )
            if isinstance(contract, dict) and isinstance(contract.get('models'), list):
                contract = dict(contract)
                entries = []
                for capability in contract['models']:
                    if not isinstance(capability, dict):
                        raise ValidationError(_('Invalid task model entry.'))
                    capability = dict(capability)
                    if 'model' not in capability and len(selected_models) == 1:
                        capability['model'] = selected_models[0]
                    if capability.get('model') not in selected_models:
                        return self._tool_builder_error_result(
                            ValidationError(_(
                                'The task must use the model already inspected: %(models)s. '
                                'Correct the task using its returned fields; do not search '
                                'for a different model or ask the user to supply field names.',
                                models=', '.join(selected_models),
                            )),
                            required_next_tool='define_generated_tool_task',
                            extra={'selected_models': selected_models},
                        )
                    entries.append(capability)
                contract['models'] = entries
            if isinstance(contract, dict):
                contract = dict(contract)
                contract['contract_version'] = 2
                if contract.get('uses_ai'):
                    requirements = list(contract.get('content_requirements') or [])
                    operations = {
                        operation
                        for capability in (contract.get('models') or [])
                        for operation in (capability.get('operations') or [])
                    }
                    if 'write' in operations:
                        safety_rule = (
                            'Use only facts present in the supplied Odoo record; omit missing '
                            'information and never invent salary, benefits, company policy, '
                            'qualifications or requirements.'
                        )
                        if safety_rule not in requirements:
                            requirements.append(safety_rule)
                    if 'create' in operations:
                        create_safety_rule = (
                            'Create only values requested by the user or supported by declared '
                            'inputs. Synthetic records must be clearly reported as synthetic, use '
                            'non-deliverable example.test email addresses, and must not impersonate '
                            'real people.'
                        )
                        if create_safety_rule not in requirements:
                            requirements.append(create_safety_rule)
                    contract['content_requirements'] = requirements
            contract = tool_model._validate_task_contract(contract)
            # Model identity is already selected. Recheck actual ACL and field
            # rights here instead of asking the LLM to inspect the same model
            # again just to add write access to its earlier read inspection.
            tool_model._check_script_access(session.user_id, skill, contract, session)
            for capability in contract['models']:
                for operation in capability['operations']:
                    session._record_tool_builder_discovery(skill.name, operation, [capability['model']])
                session._record_tool_builder_inspection(
                    skill.name, capability['operations'], capability['model']
                )
            request = session._user_entry_text(session._latest_counted_user_entry() or {})
            if not request:
                raise ValidationError(_('A current user request is required.'))
            session.sudo().write({'tool_builder_task_contract': contract, 'tool_builder_task_request': request})
            return {'status': 'task_defined', 'contract': contract, 'original_request': request,
                    'required_next_tool': ('revise_generated_tool' if session._tool_authoring_mode() == 'repair'
                                           else 'propose_generated_tool'),
                    'human_review_required': True, 'semantic_correctness_verified': False}
        except (AccessError, UserError, ValueError, TypeError, KeyError) as error:
            return self._tool_builder_error_result(error, required_next_tool='define_generated_tool_task')

    def _task_for_source(self, session, manifest):
        if not session.tool_builder_task_contract:
            raise ValidationError(_('Call define_generated_tool_task before submitting source.'))
        expected_models = {
            item['model'] for item in session.tool_builder_task_contract['models']
        }
        actual_models = {item['model'] for item in manifest.get('models', [])}
        if actual_models != expected_models:
            raise ValidationError(_(
                'Source must use the task models already selected: %(expected)s. '
                'It currently uses: %(actual)s. Correct the source; do not change '
                'the task model to match the incorrect source.',
                expected=', '.join(sorted(expected_models)),
                actual=', '.join(sorted(actual_models)),
            ))
        if manifest.get('uses_ai'):
            request = (session.tool_builder_task_request or '')[:1000]
            instructions = '\n'.join(
                item.get('prompt', '') + '\n' + item.get('system', '')
                for item in manifest.get('ai_instructions', [])
            )
            if request and request not in instructions:
                raise ValidationError(_(
                    'The generate_text instructions must include the original user '
                    'requirement so Preview generation cannot lose its language, style '
                    'or factual constraints.'
                ))
            contract = session.tool_builder_task_contract or {}
            required_content = [contract.get('output_language') or ''] + list(
                contract.get('content_requirements') or []
            )
            missing_content = [
                value for value in required_content
                if value and value not in instructions
            ]
            if missing_content:
                raise ValidationError(_(
                    'The generate_text instructions omit declared language or content '
                    'requirements. Copy every requirement from the task contract into '
                    'the prompt/system string.'
                ))
        return self.env['muk_ai.generated.tool']._validate_task_contract(session.tool_builder_task_contract, manifest)

    def _model_navigation(self, session, model_names):
        """Only expose navigation already visible to the current chat user."""
        result = {name: [] for name in model_names}
        if not result:
            return result
        menu_model = api.Environment(
            self.env.cr, session.user_id.id, dict(self.env.context)
        )['ir.ui.menu']
        try:
            visible_ids = menu_model._visible_menu_ids(debug=False)
        except AccessError:
            return result
        # Odoo's own menu visibility check filters groups and model read ACLs.
        # Sudo is only for metadata of those visible menu IDs, never business data.
        for menu in menu_model.sudo().browse(sorted(visible_ids)).exists():
            action = menu.action
            if not action or action._name != 'ir.actions.act_window':
                continue
            if action.res_model not in result or len(result[action.res_model]) >= 5:
                continue
            path, seen, ancestor = [], set(), menu
            while ancestor and ancestor.id in visible_ids and ancestor.id not in seen:
                seen.add(ancestor.id)
                path.append(ancestor.name)
                ancestor = ancestor.parent_id
            result[action.res_model].append({
                'application': path[-1] if path else menu.name,
                'menu_path': ' / '.join(reversed(path)),
                'menu_id': menu.id, 'action_id': action.id,
                'action_name': action.name,
            })
        return result

    @api.model
    @mcp_tool(
        name='search_authorized_models',
        description=(
            'Search Odoo models that the current chat user may access before '
            'writing a generated script. Call once with every access right the '
            'script needs. Start with the business entity label, not a guessed '
            'technical model. Compare module/menu evidence of same-label alternatives; '
            'if ambiguous, ask the user which application they mean. '
            'The server derives authorization from live Odoo ACLs.'
        ),
        input_schema={
            'type': 'object',
            'properties': {
                'query': {
                    'type': 'string',
                    'description': 'Business entity label from the request (translate if needed). Use a technical name only when the user supplied it or discovery returned it.',
                },
                'required_access': {
                    'type': 'array',
                    'items': {'type': 'string', 'enum': list(ACCESS_RIGHTS)},
                    'default': ['read'],
                },
                'skill_name': {'type': 'string'},
                'limit': {
                    'type': 'integer', 'minimum': 1, 'maximum': 50, 'default': 20,
                },
            },
            'required': ['query', 'required_access'],
            'additionalProperties': False,
        },
        category='read',
        registry='odoo',
    )
    def _mcp_search_authorized_models(
        self, query=None, required_access=None, skill_name=None, limit=20, **extra
    ):
        try:
            session = self._tool_builder_session(allowed_modes=('create', 'repair'))
            skill = self._tool_builder_skill(session, skill_name)
        except (AccessError, ValidationError) as error:
            return self._tool_builder_error_result(error)
        query = query.strip() if isinstance(query, str) else ''
        if len(query) < 2:
            return self._tool_builder_error_result(
                ValidationError(_('Enter at least two characters to search models.')),
                required_next_tool='search_authorized_models',
            )
        access_rights = list(dict.fromkeys(required_access or ['read']))
        invalid = set(access_rights) - set(ACCESS_RIGHTS)
        if invalid:
            return self._tool_builder_error_result(
                ValidationError(
                    _('Unsupported access rights: %(rights)s.', rights=', '.join(sorted(invalid)))
                ),
                required_next_tool='search_authorized_models',
            )
        try:
            limit = max(1, min(int(limit or 20), 50))
        except (TypeError, ValueError):
            limit = 20
        model_query = '.'.join(query.split())
        model_registry = self.env['ir.model'].sudo()
        query_domain = [
            '|', '|', ('name', 'ilike', query), ('model', 'ilike', query),
            ('model', 'ilike', model_query),
        ]
        seeds = model_registry.search(query_domain, order='model', limit=200)
        seeds |= model_registry.with_context(lang='en_US').search(
            query_domain, order='model', limit=200
        ).with_env(model_registry.env)
        # A technical-name query must not conceal same-label alternatives.
        # This is an exact metadata relation, not Vietnamese keyword inference.
        labels = seeds.with_context(lang='en_US').mapped('name')
        peers = model_registry.with_context(lang='en_US').search(
            [('name', 'in', labels)], order='model', limit=201
        ).with_env(model_registry.env) if labels else model_registry.browse()
        candidates = seeds | peers
        matches = []
        for candidate in candidates:
            try:
                for access in access_rights:
                    skill._check_tool_builder_policy(
                        user=session.user_id,
                        session=session,
                        model_name=candidate.model,
                        operation=access,
                    )
            except (AccessError, KeyError):
                continue
            matches.append(
                {
                    'model': candidate.model,
                    'label': candidate.name,
                    'label_en': candidate.with_context(lang='en_US').name,
                    'modules': [name.strip() for name in (candidate.modules or '').split(',') if name.strip()],
                    'authorized_access': access_rights,
                }
            )
        groups = {}
        for match in matches:
            groups.setdefault(match['label_en'], []).append(match['model'])
        total_matches = len(matches)
        # Exact label hits precede prefix hits; no preference for standard apps.
        matches.sort(key=lambda item: (
            query.casefold() not in (item['label'].casefold(), item['label_en'].casefold()),
            item['label_en'], item['model'],
        ))
        matches = matches[:limit]
        navigation = self._model_navigation(session, [match['model'] for match in matches])
        for match in matches:
            match['navigation'] = navigation[match['model']]
            match['navigation_limit'] = 5
            match['same_label_models'] = groups[match['label_en']]
            match['selection_requires_explanation'] = len(match['same_label_models']) > 1
        session._record_tool_builder_catalog(skill.name, matches)
        for access in access_rights:
            session._record_tool_builder_discovery(
                skill.name, access, [match['model'] for match in matches]
            )
        if not matches and candidates:
            return self._tool_builder_error_result(
                AccessError(
                    _(
                        'No model matching %(query)r grants the current user all '
                        'requested rights: %(rights)s.',
                        query=query,
                        rights=', '.join(access_rights),
                    )
                ),
                retryable=False,
            )
        if not matches:
            return self._tool_builder_error_result(
                ValidationError(_('No Odoo model matched query %(query)r.', query=query)),
                required_next_tool='search_authorized_models',
            )
        return {
            'status': 'authorized_models_found',
            'skill_name': skill.name,
            'models': matches,
            'count': len(matches),
            'truncated': total_matches > limit or len(seeds) >= 200 or len(peers) >= 201,
            'module_metadata_note': 'modules lists defining/extending addons, not necessarily the owning application. Use visible navigation to distinguish applications.',
            'required_access': access_rights,
            'authorization_basis': 'current_user_odoo_acl',
            'record_rules_rechecked_during_test_and_every_run': True,
            'required_next_tool': 'inspect_authorized_model',
            'next_step_instruction': (
                'Compare model labels AND modules/navigation with the original user request. '
                'Do not select the first result or prefer a model just because its name '
                'matched a technical query you guessed. For same-label alternatives, '
                'explain the selection from user context, or ask_user with application/menu '
                'choices if context is insufficient. An empty navigation list does not '
                'mean a model is forbidden; permissions were checked separately. '
                'Inspect the matching model before declaring the task or submitting code. '
                'If none matches, search again using the business entity label, not a guessed module prefix.'
            ),
        }

    @api.model
    @mcp_tool(
        name='inspect_authorized_model',
        description=(
            'Return user-visible field metadata for an Odoo model already authorized '
            'in this turn. Select the entity that best matches the original natural-language '
            'request. The current screen is scope evidence only, never an automatic target. '
            'Use it before writing source.'
        ),
        input_schema={
            'type': 'object',
            'properties': {
                'model': {'type': 'string'},
                'required_access': {
                    'type': 'array',
                    'items': {'type': 'string', 'enum': list(ACCESS_RIGHTS)},
                    'default': ['read'],
                },
                'skill_name': {'type': 'string'},
                'selection_reason': {
                    'type': 'string',
                    'description': (
                        'Explain which words or business meaning in the original request '
                        'match this model label/menu. Never justify a selection only because '
                        'it is the currently open model. If genuinely ambiguous, ask_user.'
                    ),
                },
                'entity_label': {
                    'type': 'string',
                    'description': (
                        'Translate only the business entity named by the user into a short '
                        'English/Odoo label, independently of the model you plan to select. '
                        'Examples of shape only: "candidate", "job position", "sales order". '
                        'Do not copy the current model label unless the user actually refers '
                        'to that entity.'
                    ),
                },
            },
            'required': ['model', 'required_access'],
            'additionalProperties': False,
        },
        category='read',
        registry='odoo',
    )
    def _mcp_inspect_authorized_model(
        self, model=None, required_access=None, skill_name=None, selection_reason=None,
        entity_label=None, **extra
    ):
        try:
            session = self._tool_builder_session(allowed_modes=('create', 'repair'))
            skill = self._tool_builder_skill(session, skill_name)
            access_rights = list(dict.fromkeys(required_access or ['read']))
            if not model or set(access_rights) - set(ACCESS_RIGHTS):
                raise ValidationError(_('model and valid required_access are required.'))
            missing = [
                access for access in access_rights
                if not session._tool_builder_model_was_discovered(
                    skill.name, access, model
                )
            ]
            if missing:
                return self._tool_builder_error_result(
                    ValidationError(_('Inspect only a model authorized in this turn.')),
                    required_next_tool='search_authorized_models',
                    extra={
                        'missing_authorizations': [
                            {'model': model, 'access': access} for access in missing
                        ]
                    },
                )
            for access in access_rights:
                skill._check_tool_builder_policy(
                    user=session.user_id,
                    session=session,
                    model_name=model,
                    operation=access,
                )
            catalog = session._tool_builder_catalog(skill.name)
            candidate = next((item for item in catalog if item['model'] == model), None)
            human_selected = session._tool_builder_user_selected_model() == model
            context_selected = bool(candidate and candidate.get('is_current_view_model'))
            translated_entity_resolved = False
            if len(catalog) > 1 and not human_selected and (
                not isinstance(entity_label, str) or not entity_label.strip()
            ):
                return self._tool_builder_error_result(
                    ValidationError(_(
                        'Translate the business entity from the original request into '
                        'entity_label before selecting a model.'
                    )),
                    required_next_tool='inspect_authorized_model',
                    extra={'required_argument': 'entity_label'},
                )
            if entity_label is not None and (
                not isinstance(entity_label, str) or not entity_label.strip()
                or len(entity_label) > 255
            ):
                raise ValidationError(_('entity_label must be a non-empty string of at most 255 characters.'))
            if catalog and entity_label:
                # The LLM translates the user's entity phrase; the backend, not
                # the LLM's selected index, maps that phrase to Odoo metadata.
                translated_rank = self._rank_scoped_models(catalog, entity_label.strip())
                translated_scores = {
                    item['model']: item.get('semantic_score', 0)
                    for item in translated_rank
                }
                top_translated_score = max(translated_scores.values(), default=0)
                translated_matches = [
                    item for item in translated_rank
                    if top_translated_score > 0
                    and item.get('semantic_score', 0) == top_translated_score
                ]
                translated_entity_resolved = bool(translated_matches)
                if (
                    translated_matches
                    and translated_scores.get(model, 0) < top_translated_score
                    and not human_selected
                ):
                    return self._tool_builder_error_result(
                        ValidationError(_(
                            'The selected model does not match the translated business '
                            'entity label. Inspect the returned best match instead.'
                        )),
                        required_next_tool='inspect_authorized_model',
                        extra={
                            'selection_policy': 'translated_user_entity_before_current_view',
                            'rejection_reason': 'translated_entity_mismatch',
                            'entity_label': entity_label.strip(),
                            'best_matching_models': [
                                item['model'] for item in translated_matches
                            ],
                            'best_match_evidence': {
                                item['model']: item.get('semantic_signals') or []
                                for item in translated_matches
                            },
                        },
                    )
            top_score = max(
                (item.get('semantic_score', 0) for item in catalog),
                default=0,
            )
            stronger_matches = [
                item for item in catalog
                if top_score > 0 and item.get('semantic_score', 0) == top_score
            ]
            if (
                candidate and not human_selected and stronger_matches
                and not translated_entity_resolved
                and candidate.get('semantic_score', 0) < top_score
            ):
                return self._tool_builder_error_result(
                    ValidationError(_(
                        'The selected model is contradicted by stronger label/menu '
                        'matches in the original user request. Inspect the best matching '
                        'business entity instead; do not prefer the current screen.'
                    )),
                    required_next_tool='inspect_authorized_model',
                    extra={
                        'selection_policy': 'user_request_semantics_before_current_view',
                        'rejection_reason': 'stronger_semantic_match',
                        'best_matching_models': [
                            item['model'] for item in stronger_matches
                        ],
                        'best_match_evidence': {
                            item['model']: item.get('semantic_signals') or []
                            for item in stronger_matches
                        },
                    },
                )
            if len(catalog) > 1 and not human_selected and (
                not isinstance(selection_reason, str) or not selection_reason.strip()
            ):
                return self._tool_builder_error_result(
                    ValidationError(_(
                        'Explain why the selected business entity matches the original '
                        'user request. The current screen alone is not a valid reason.'
                    )),
                    required_next_tool='inspect_authorized_model',
                    extra={
                        'selection_policy': 'user_request_semantics_before_current_view',
                        'candidate_models': [item['model'] for item in catalog],
                    },
                )
            if candidate and candidate.get('selection_requires_explanation') and (
                not human_selected
                and (not isinstance(selection_reason, str) or not selection_reason.strip())
            ):
                return self._tool_builder_error_result(
                    ValidationError(_('Several authorized models share this label. Explain which application matches the user request, or ask the user to choose.')),
                    required_next_tool='inspect_authorized_model',
                    extra={'alternatives': [item for item in catalog
                                            if item['model'] in candidate['same_label_models']],
                           'same_label_models': candidate['same_label_models'],
                           'clarification_tool': 'ask_user'},
                )
            if selection_reason is not None and (
                not isinstance(selection_reason, str) or len(selection_reason) > 2000
            ):
                raise ValidationError(_('selection_reason must be a string of at most 2000 characters.'))
            target = api.Environment(
                self.env.cr, session.user_id.id, dict(self.env.context)
            )[model]
            metadata = target.fields_get(
                attributes=['string', 'type', 'required', 'readonly', 'relation']
            )
            session._record_tool_builder_inspection(skill.name, access_rights, model)
            entry = session._latest_counted_user_entry() or {}
            inspected = entry.get('_tool_builder_inspected_context') or {}
            selected = dict(inspected.get('models') or {}) if inspected.get('turn') == session.turn_seq else {}
            selected[model] = {'model_context': candidate or {'model': model},
                               'fields': [
                                   {'name': name, 'type': info.get('type'),
                                    'label': info.get('string'), 'readonly': info.get('readonly', False)}
                                   for name, info in sorted(metadata.items())
                                   if not name.startswith('_')
                               ],
                               'selection_reason': (selection_reason or (
                                   'Selected explicitly by the user.' if human_selected
                                   else ''
                               )),
                               'entity_label': entity_label or '',
                               'selected_by_user': human_selected,
                               'selected_from_current_view': context_selected,
                               'semantic_match_verified': False}
            session._update_current_tool_turn_metadata({
                '_tool_builder_inspected_context': {'turn': session.turn_seq, 'models': selected},
            })
        except AccessError as error:
            return self._tool_builder_error_result(error, retryable=False)
        except (ValidationError, KeyError) as error:
            return self._tool_builder_error_result(
                error, required_next_tool='search_authorized_models'
            )
        return {
            'status': 'authorized_model_inspected',
            'model': model,
            'selection_reason': selection_reason or (
                'Selected explicitly by the user.' if human_selected
                else ''
            ),
            'entity_label': entity_label or '',
            'selected_by_user': human_selected,
            'selected_from_current_view': context_selected,
            'selection_semantically_verified': False,
            'model_context': candidate or {},
            'required_access': access_rights,
            'fields': [
                {'name': name, **metadata[name]}
                for name in sorted(metadata)
                if not name.startswith('_')
            ],
            'field_count': len(metadata),
            'required_next_tool': 'define_generated_tool_task',
        }

    @api.model
    @mcp_tool(
        name='propose_generated_tool',
        description=(
            'Store and statically validate a reusable restricted Python script. '
            'The script must use only ctx search/read/search_count/create/write/'
            'unlink/generate_text APIs and assign its final JSON-safe value to result.'
        ),
        input_schema={
            'type': 'object',
            'properties': {
                'name': {
                    'type': 'string',
                    'description': 'Unique lowercase name such as update_job_descriptions.',
                },
                'description': {'type': 'string'},
                'source_code': {
                    'type': 'string',
                    'description': (
                        'Restricted script-body statements using only params and ctx; '
                        'assign output to result. Never use import/from, class, def, '
                        'decorators, self or env.'
                    ),
                },
                'input_schema': {'type': 'object', 'default': OBJECT_SCHEMA},
                'default_arguments': {'type': 'object', 'default': {}},
                'skill_name': {'type': 'string'},
                'max_records': {'type': 'integer', 'minimum': 1, 'maximum': 1000},
                'max_ai_calls': {'type': 'integer', 'minimum': 0, 'maximum': 200},
                'max_runtime_seconds': {
                    'type': 'integer', 'minimum': 1, 'maximum': 300,
                },
            },
            'required': ['name', 'description', 'source_code'],
            'additionalProperties': False,
        },
        category='write',
        registry='odoo',
    )
    def _mcp_propose_generated_tool(
        self,
        name=None,
        description=None,
        source_code=None,
        input_schema=None,
        default_arguments=None,
        skill_name=None,
        max_records=200,
        max_ai_calls=25,
        max_runtime_seconds=60,
        tool_name=None,
        **extra,
    ):
        name = name or tool_name
        source_code = source_code or extra.get('script') or extra.get('code')
        try:
            session = self._tool_builder_session(allowed_modes=('create',))
            user = session.user_id
            skill = self._tool_builder_skill(session, skill_name)
            name = name or session._tool_intent().get('tool_name')
            if not session.tool_builder_task_contract:
                return self._tool_builder_error_result(
                    ValidationError(_('Declare the requested task before submitting source.')),
                    tool_name=name, required_next_tool='define_generated_tool_task',
                )
            if not name or not description or not source_code:
                raise ValidationError(_('name, description and source_code are required.'))
            tool_model = self.env['muk_ai.generated.tool']
            requested_name = session._tool_intent().get('tool_name')
            if requested_name and name != requested_name:
                return self._tool_builder_error_result(
                    ValidationError(_(
                        'The proposed name %(actual)r does not match the requested and '
                        'prechecked name %(expected)r.',
                        actual=name, expected=requested_name,
                    )),
                    tool_name=name,
                    required_next_tool='propose_generated_tool',
                )
            if name in get_tool_index(self.env, registry='odoo'):
                return self._tool_builder_error_result(
                    ValidationError(_('Tool name %(name)r is already registered.', name=name)),
                    tool_name=name,
                    required_next_tool='ask_user',
                )
            existing = self.env['muk_ai.generated.tool'].sudo().search(
                [('owner_id', '=', user.id), ('name', '=', name)], limit=1
            )
            if existing:
                can_revise = existing.state in ('active', 'failed')
                return self._tool_builder_error_result(
                    ValidationError(
                        _(
                            'Tool %(name)r already exists; %(action)s.',
                            name=name,
                            action=(
                                'ask the user for another name, or ask them to send a new request to repair this existing tool'
                                if can_revise else 'choose another name'
                            ),
                        )
                    ),
                    tool_name=name,
                    required_next_tool='ask_user' if can_revise else None,
                    retryable=can_revise,
                )
            source_code, manifest = tool_model._analyze_source_code(source_code)
            contract = self._task_for_source(session, manifest)
            input_schema = tool_model._validate_json_schema(input_schema)
            missing = self._missing_discoveries(session, skill, manifest)
            if missing:
                return self._tool_builder_error_result(
                    ValidationError(
                        _('The script uses models or rights not authorized by discovery in this turn.')
                    ),
                    tool_name=name,
                    required_next_tool='search_authorized_models',
                    extra={'missing_authorizations': missing},
                )
            model_records = tool_model._check_script_access(
                user, skill, manifest, session
            )
            primary_model = manifest['models'][0]['model']
            primary_model_record = model_records[primary_model]
        except AccessError as error:
            return self._tool_builder_error_result(error, tool_name=name)
        except (ValidationError, UserError, KeyError, TypeError, ValueError) as error:
            return self._tool_builder_error_result(
                error, tool_name=name,
                required_next_tool='propose_generated_tool',
            )
        try:
            with self.env.cr.savepoint():
                tool = self.env['muk_ai.generated.tool'].sudo().create(
                    {
                    'name': name,
                    'description': description,
                    'owner_id': user.id,
                    'skill_id': skill.id,
                    'agent_id': session.agent_id.id if session.agent_id else False,
                    'source_session_id': session.id,
                    'source_turn_seq': session.turn_seq,
                    'model_id': primary_model_record.id,
                    'operation': 'script',
                    'source_code': source_code,
                    'input_schema': input_schema,
                    'default_arguments': default_arguments or {},
                    'capability_manifest': manifest,
                    'task_contract': contract,
                    'original_request': session.tool_builder_task_request,
                    'revision_count': 1,
                    'max_revisions': skill.tool_max_revisions,
                    'max_records': max(1, min(int(max_records or 200), 1000)),
                    'max_ai_calls': max(0, min(int(25 if max_ai_calls is None else max_ai_calls), 200)),
                    'max_runtime_seconds': max(
                        1, min(int(max_runtime_seconds or 60), 300)
                    ),
                    }
                )
                self.env['muk_ai.generated.tool.revision'].sudo().create(
                    {
                    'tool_id': tool.id,
                    'revision': 1,
                    'description': description,
                    'source_code': source_code,
                    'input_schema': input_schema,
                    'default_arguments': default_arguments or {},
                    'capability_manifest': manifest,
                    'task_contract': contract,
                    'original_request': session.tool_builder_task_request,
                    'source_session_id': session.id,
                    'source_turn_seq': session.turn_seq,
                    'authored_by': user.id,
                    }
                )
        except (ValidationError, TypeError, ValueError) as error:
            return self._tool_builder_error_result(
                error, tool_name=name, required_next_tool='propose_generated_tool'
            )
        result = tool._test_for_session(session)
        result['authorization'] = {
            'user_id': user.id,
            'capability_manifest': manifest,
            'odoo_acl_rechecked_on_every_run': True,
            'record_rules_rechecked_on_every_run': True,
            'sudo_on_business_models': False,
        }
        self._activate_generated_schema(session, result)
        return result

    @api.model
    @mcp_tool(
        name='inspect_generated_tool',
        description='Read the current source, manifest and last error before repairing a tool.',
        input_schema={
            'type': 'object',
            'properties': {'tool_name': {'type': 'string'}},
            'required': ['tool_name'],
            'additionalProperties': False,
        },
        category='read',
        registry='odoo',
    )
    def _mcp_inspect_generated_tool(self, tool_name=None, **extra):
        try:
            session = self._tool_builder_session(allowed_modes=('repair', 'run'))
            selected = session._tool_intent().get('tool_name')
            if selected and tool_name != selected:
                raise ValidationError(_('Inspect the tool selected for the current request.'))
        except AccessError as error:
            return self._tool_builder_error_result(error, tool_name=tool_name)
        tool = self.env['muk_ai.generated.tool'].sudo().search(
            [('owner_id', '=', session.user_id.id), ('name', '=', tool_name)], limit=1
        )
        if not tool:
            return self._tool_builder_error_result(
                UserError(_('Generated tool %(tool)r was not found.', tool=tool_name)),
                tool_name=tool_name,
                retryable=False,
            )
        session._update_current_tool_turn_metadata({
            '_tool_builder_source_inspected_turn': session.turn_seq,
        })
        last_run = tool.run_ids[:1]
        return {
            'status': tool.state,
            'tool_name': tool.name,
            'description': tool.description,
            'source_code': tool.source_code,
            'input_schema': tool.input_schema,
            'default_arguments': tool.default_arguments,
            'capability_manifest': tool.capability_manifest,
            'task_contract': tool.task_contract,
            'original_request': tool.original_request,
            'active_revision': tool.active_revision_number,
            'revision': tool.revision_count,
            'remaining_revisions': max(
                0, min(tool.max_revisions, tool.skill_id.tool_max_revisions)
                - tool.revision_count,
            ),
            'last_error_kind': tool.last_error_kind,
            'last_error': tool.last_error,
            'last_run': {
                'status': last_run.status,
                'revision': last_run.revision,
                'input': last_run.input_data,
                'error': last_run.error_message,
            }
            if last_run
            else None,
        }

    @api.model
    @mcp_tool(
        name='test_generated_tool',
        description='Statically validate code, task contract and permissions; no script execution or AI calls.',
        input_schema={
            'type': 'object',
            'properties': {'tool_name': {'type': 'string'}},
            'required': ['tool_name'],
            'additionalProperties': False,
        },
        category='write',
        registry='odoo',
    )
    def _mcp_test_generated_tool(self, tool_name=None, **extra):
        try:
            session = self._tool_builder_session(allowed_modes=('repair',))
            selected = session._tool_intent().get('tool_name')
            if selected and tool_name != selected:
                raise ValidationError(_('Validate the tool selected for the current request.'))
        except AccessError as error:
            return self._tool_builder_error_result(error, tool_name=tool_name)
        tool = self.env['muk_ai.generated.tool'].sudo().search(
            [('owner_id', '=', session.user_id.id), ('name', '=', tool_name)], limit=1
        )
        if not tool:
            return self._tool_builder_error_result(
                UserError(_('Generated tool %(tool)r was not found.', tool=tool_name)),
                tool_name=tool_name,
                retryable=False,
            )
        result = tool._test_for_session(session)
        self._activate_generated_schema(session, result)
        return result

    @api.model
    @mcp_tool(
        name='revise_generated_tool',
        description=(
            'Create and statically validate a new source-code revision. Inspect the current '
            'tool first. Access denials cannot be repaired by changing code.'
        ),
        input_schema={
            'type': 'object',
            'properties': {
                'tool_name': {'type': 'string'},
                'description': {'type': 'string'},
                'source_code': {'type': 'string'},
                'input_schema': {'type': 'object'},
                'default_arguments': {'type': 'object'},
            },
            'required': ['tool_name', 'description', 'source_code'],
            'additionalProperties': False,
        },
        category='write',
        registry='odoo',
    )
    def _mcp_revise_generated_tool(
        self,
        tool_name=None,
        description=None,
        source_code=None,
        input_schema=None,
        default_arguments=None,
        **extra,
    ):
        source_code = source_code or extra.get('script') or extra.get('code')
        try:
            session = self._tool_builder_session(allowed_modes=('repair',))
            selected = session._tool_intent().get('tool_name')
            if selected and tool_name != selected:
                raise ValidationError(_('Revise the tool selected for the current request.'))
        except AccessError as error:
            return self._tool_builder_error_result(error, tool_name=tool_name)
        tool = self.env['muk_ai.generated.tool'].sudo().search(
            [('owner_id', '=', session.user_id.id), ('name', '=', tool_name)], limit=1
        )
        if not tool:
            return self._tool_builder_error_result(
                UserError(_('Generated tool %(tool)r was not found.', tool=tool_name)),
                tool_name=tool_name,
                retryable=False,
            )
        tool._lock_definition()
        if tool.last_error_kind == 'access' or tool.state == 'disabled':
            return self._tool_builder_error_result(
                AccessError(_('An access denial cannot be repaired by the AI.')),
                tool_name=tool_name,
                retryable=False,
            )
        revision_limit = min(tool.max_revisions, tool.skill_id.tool_max_revisions)
        if tool.revision_count >= revision_limit:
            return self._tool_builder_error_result(
                ValidationError(_('This generated tool reached its revision limit.')),
                tool_name=tool_name,
                retryable=False,
            )
        try:
            if not description or not source_code:
                raise ValidationError(_('description and source_code are required.'))
            if not session.tool_builder_task_contract:
                return self._tool_builder_error_result(
                    ValidationError(_('Declare the intended task before submitting a revision.')),
                    tool_name=tool_name, required_next_tool='define_generated_tool_task',
                )
            source_code, manifest = tool._analyze_source_code(source_code)
            contract = self._task_for_source(session, manifest)
            schema = tool._validate_json_schema(input_schema or tool.input_schema)
            missing = self._missing_discoveries(session, tool.skill_id, manifest)
            if missing:
                return self._tool_builder_error_result(
                    ValidationError(_('The revised script needs model access discovery.')),
                    tool_name=tool_name,
                    required_next_tool='search_authorized_models',
                    extra={'missing_authorizations': missing},
                )
            model_records = tool._check_script_access(
                session.user_id, tool.skill_id, manifest, session
            )
        except AccessError as error:
            return self._tool_builder_error_result(
                error, tool_name=tool_name, retryable=False
            )
        except (ValidationError, UserError, KeyError, TypeError, ValueError) as error:
            return self._tool_builder_error_result(
                error, tool_name=tool_name,
                required_next_tool='revise_generated_tool',
            )
        # Keep the previously usable definition if candidate validation fails.
        snapshot_fields = ['description', 'source_code', 'input_schema', 'default_arguments',
                           'capability_manifest', 'model_id', 'operation', 'task_contract',
                           'original_request', 'active_revision_number', 'last_test_result']
        previous = {key: (tool[key].id if key == 'model_id' else tool[key]) for key in snapshot_fields}
        previous_active = tool.state == 'active'
        revision = tool.revision_count + 1
        request_history = (tool.original_request or '') + '\n\nRevision request:\n' + (session.tool_builder_task_request or '')
        primary_model = manifest['models'][0]['model']
        tool.sudo().write(
            {
                'description': description,
                'source_code': source_code,
                'input_schema': schema,
                'default_arguments': default_arguments
                if default_arguments is not None else tool.default_arguments,
                'capability_manifest': manifest,
                'task_contract': contract,
                'original_request': request_history,
                'model_id': model_records[primary_model].id,
                'operation': 'script',
                'revision_count': revision,
                'state': 'draft',
                'last_error': False,
                'last_error_kind': False,
            }
        )
        self.env['muk_ai.generated.tool.revision'].sudo().create(
            {
                'tool_id': tool.id,
                'revision': revision,
                'description': description,
                'source_code': source_code,
                'input_schema': schema,
                'default_arguments': tool.default_arguments,
                'capability_manifest': manifest,
                'task_contract': contract,
                'original_request': request_history,
                'source_session_id': session.id,
                'source_turn_seq': session.turn_seq,
                'authored_by': session.user_id.id,
            }
        )
        result = tool._test_for_session(session)
        if result.get('status') != 'active' and previous_active:
            tool.sudo().write({**previous, 'state': 'disabled' if result.get('error_kind') == 'access' else 'active'})
            result['previous_definition_preserved'] = True
            result['active_revision'] = tool.active_revision_number
        self._activate_generated_schema(session, result)
        return result
