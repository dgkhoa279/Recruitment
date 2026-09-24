from __future__ import annotations

import ast
import hashlib
import json
import re
from contextlib import contextmanager

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from .script_runner import run_isolated
from .script_schema import validate_schema, validate_value


TOOL_NAME_RE = re.compile(r'^[a-z][a-z0-9_]{2,63}$')
DOMAIN_OPERATORS = frozenset(
    {
        '=', '!=', '>', '>=', '<', '<=', 'in', 'not in', 'like', 'not like',
        'ilike', 'not ilike', '=like', '=ilike', '=?',
    }
)
SCRIPT_CONTEXT_METHODS = frozenset(
    {'search', 'read', 'search_count', 'create', 'write', 'unlink', 'generate_text'}
)
SCRIPT_READ_METHODS = frozenset({'search', 'read', 'search_count'})
SCRIPT_SAFE_CALLS = frozenset(
    {
        'all', 'any', 'bool', 'dict', 'enumerate', 'float', 'int', 'len',
        'list', 'max', 'min', 'range', 'round', 'set', 'sorted', 'str',
        'sum', 'tuple', 'zip',
    }
)
SCRIPT_SAFE_VALUE_METHODS = frozenset(
    {
        'append', 'count', 'endswith', 'extend', 'get', 'items', 'join',
        'keys', 'lower', 'replace', 'split', 'startswith', 'strip', 'upper',
        'values',
    }
)
SCRIPT_FORBIDDEN_NAMES = frozenset(
    {
        'env', 'self', 'super', 'sudo', 'open', 'eval', 'exec', 'compile',
        'globals', 'locals', 'getattr', 'setattr', 'delattr', 'hasattr',
        '__import__',
    }
)
SCRIPT_FORBIDDEN_NODES = (
    ast.AsyncFor, ast.AsyncFunctionDef, ast.Await, ast.ClassDef, ast.Delete,
    ast.FunctionDef, ast.Global, ast.Import, ast.ImportFrom, ast.Lambda,
    ast.Nonlocal, ast.Raise, ast.Try, ast.While, ast.With, ast.Yield,
    ast.YieldFrom,
)
SCRIPT_CONTEXT_SIGNATURES = {
    'search': (1, 4, ('model', 'domain', 'fields', 'limit')),
    'read': (3, 3, ('model', 'ids', 'fields')),
    'search_count': (1, 2, ('model', 'domain')),
    'create': (2, 2, ('model', 'values')),
    'write': (3, 3, ('model', 'ids', 'values')),
    'unlink': (2, 2, ('model', 'ids')),
    'generate_text': (1, 3, ('prompt', 'data', 'system')),
}


class _GeneratedScriptAnalyzer(ast.NodeVisitor):
    """Validate restricted Python and derive its Odoo capability manifest."""

    def __init__(self, source):
        self.source = source
        tree = ast.parse(source, mode='exec')
        self.capabilities = {}
        self.uses_ai = False
        self.ai_instructions = []
        self.assigned_names = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
        }
        store_counts = {}
        mutated_containers = set()
        dict_assignments = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                store_counts[node.id] = store_counts.get(node.id, 0) + 1
            if (
                isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store)
                and isinstance(node.value, ast.Name)
            ):
                mutated_containers.add(node.value.id)
            if (
                isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Dict)
            ):
                dict_assignments[node.targets[0].id] = node.value
        # Allow the common ``values = {...}; ctx.create(..., values)`` shape
        # without weakening static field analysis. Reassigned or dynamically
        # mutated containers remain invalid because their final keys are unknown.
        self.static_dict_assignments = {
            name: value for name, value in dict_assignments.items()
            if store_counts.get(name) == 1 and name not in mutated_containers
        }
        self.has_result = 'result' in self.assigned_names
        self.node_count = 0

    @staticmethod
    def _call_argument(call, name, position, default=None):
        for keyword in call.keywords:
            if keyword.arg == name:
                return keyword.value
        return call.args[position] if len(call.args) > position else default

    @staticmethod
    def _literal(node, label, default=None):
        if node is None:
            return default
        try:
            return ast.literal_eval(node)
        except (ValueError, TypeError, SyntaxError) as error:
            raise ValidationError(
                _('%(label)s must be a literal value in generated scripts.', label=label)
            ) from error

    def _model_name(self, call):
        model = self._literal(self._call_argument(call, 'model', 0), 'model')
        if not isinstance(model, str) or not model or model.startswith('_'):
            raise ValidationError(
                _('Every ctx model must be a literal technical model name.')
            )
        return model

    def _field_names(self, call, method):
        if method == 'unlink':
            return []
        if method in SCRIPT_READ_METHODS:
            if method == 'search_count':
                field_names = []
            else:
                fields_node = self._call_argument(call, 'fields', 2)
                fields_value = self._literal(fields_node, 'fields', default=[])
                if not isinstance(fields_value, (list, tuple)):
                    raise ValidationError(_('fields must be a literal list of field names.'))
                field_names = list(fields_value)
            if method in ('search', 'search_count'):
                domain_node = self._call_argument(call, 'domain', 1)
                try:
                    domain = ast.literal_eval(domain_node) if domain_node is not None else []
                except (ValueError, TypeError, SyntaxError):
                    domain = []
                for token in domain if isinstance(domain, (list, tuple)) else []:
                    if (
                        isinstance(token, (list, tuple)) and len(token) >= 3
                        and isinstance(token[0], str)
                    ):
                        field_names.append(token[0].split('.', 1)[0])
        else:
            position = 1 if method == 'create' else 2
            values_node = self._call_argument(call, 'values', position)
            if isinstance(values_node, ast.Name):
                values_node = self.static_dict_assignments.get(values_node.id)
            if not isinstance(values_node, ast.Dict):
                raise ValidationError(
                    _(
                        'ctx.create/write values must be a dictionary literal with static '
                        'field names, either inline or assigned once to a local variable.'
                    )
                )
            field_names = [self._literal(key, 'field name') for key in values_node.keys]
        if any(
            not isinstance(name, str) or not name or '.' in name or name.startswith('_')
            for name in field_names
        ):
            raise ValidationError(_('Generated scripts contain an invalid field name.'))
        return list(dict.fromkeys(field_names))

    def _record_capability(self, model, operation, field_names):
        entry = self.capabilities.setdefault(
            model,
            {
                'model': model,
                'operations': set(),
                'read_fields': set(),
                'write_fields': set(),
            },
        )
        entry['operations'].add(operation)
        target = 'read_fields' if operation == 'read' else 'write_fields'
        entry[target].update(field_names)

    def visit(self, node):
        self.node_count += 1
        if self.node_count > 500:
            raise ValidationError(_('Generated script is too complex.'))
        if isinstance(node, SCRIPT_FORBIDDEN_NODES):
            raise ValidationError(
                _('%(node)s is not allowed in generated scripts.', node=type(node).__name__)
            )
        return super().visit(node)

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Store) and node.id in ('ctx', 'params'):
            raise AccessError(
                _('Generated scripts cannot replace %(name)s.', name=node.id)
            )
        if node.id.startswith('_') or node.id in SCRIPT_FORBIDDEN_NAMES:
            raise AccessError(
                _('Name %(name)r is not allowed in generated scripts.', name=node.id)
            )
        allowed = {'ctx', 'params'} | SCRIPT_SAFE_CALLS | self.assigned_names
        if isinstance(node.ctx, ast.Load) and node.id not in allowed:
            raise ValidationError(_('Unknown script name %(name)r.', name=node.id))

    def visit_Constant(self, node):
        if isinstance(node.value, str) and len(node.value) > 20_000:
            raise ValidationError(_('A generated script string literal is too large.'))
        if isinstance(node.value, (int, float)) and abs(node.value) > 1_000_000:
            raise ValidationError(_('A generated script numeric literal is too large.'))

    def visit_BinOp(self, node):
        if isinstance(node.op, ast.Mult):
            for sequence_node, amount_node in (
                (node.left, node.right),
                (node.right, node.left),
            ):
                is_sequence = isinstance(sequence_node, (ast.List, ast.Tuple)) or (
                    isinstance(sequence_node, ast.Constant)
                    and isinstance(sequence_node.value, str)
                )
                if (
                    is_sequence
                    and isinstance(amount_node, ast.Constant)
                    and isinstance(amount_node.value, int)
                    and amount_node.value > 1000
                ):
                    raise ValidationError(
                        _('Large sequence replication is not allowed.')
                    )
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if not isinstance(node.ctx, ast.Load):
            raise AccessError(_('Generated scripts cannot assign object attributes.'))
        if node.attr.startswith('_'):
            raise AccessError(_('Private attributes are not allowed in generated scripts.'))
        if isinstance(node.value, ast.Name) and node.value.id == 'ctx':
            if node.attr not in SCRIPT_CONTEXT_METHODS:
                raise AccessError(_('ctx.%(name)s is not available.', name=node.attr))
        elif node.attr not in SCRIPT_SAFE_VALUE_METHODS:
            raise AccessError(
                _('Value method %(name)r is not allowed in generated scripts.', name=node.attr)
            )
        self.generic_visit(node)

    def visit_Call(self, node):
        if any(keyword.arg is None for keyword in node.keywords):
            raise AccessError(_('Expanded keyword arguments are not allowed.'))
        if isinstance(node.func, ast.Name):
            if node.func.id not in SCRIPT_SAFE_CALLS:
                raise AccessError(
                    _('Function %(name)r is not allowed in generated scripts.', name=node.func.id)
                )
        elif isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == 'ctx':
                method = node.func.attr
                if method not in SCRIPT_CONTEXT_METHODS:
                    raise AccessError(_('ctx.%(name)s is not available.', name=method))
                minimum, maximum, parameter_names = SCRIPT_CONTEXT_SIGNATURES[method]
                keyword_names = [keyword.arg for keyword in node.keywords]
                if any(name not in parameter_names for name in keyword_names):
                    raise ValidationError(
                        _('ctx.%(method)s received an unknown argument.', method=method)
                    )
                supplied = len(node.args) + len(keyword_names)
                if supplied < minimum or supplied > maximum:
                    raise ValidationError(
                        _('ctx.%(method)s expects %(minimum)s to %(maximum)s arguments.',
                          method=method, minimum=minimum, maximum=maximum)
                    )
                if any(
                    name in keyword_names for name in parameter_names[:len(node.args)]
                ):
                    raise ValidationError(
                        _('ctx.%(method)s received the same argument twice.', method=method)
                    )
                if method == 'generate_text':
                    self.uses_ai = True
                    prompt = self._literal(
                        self._call_argument(node, 'prompt', 0), 'generate_text prompt'
                    )
                    system = self._literal(
                        self._call_argument(node, 'system', 2),
                        'generate_text system', default=''
                    )
                    if not isinstance(prompt, str) or not isinstance(system, str):
                        raise ValidationError(
                            _('generate_text prompt and system must be literal strings.')
                        )
                    self.ai_instructions.append({'prompt': prompt, 'system': system})
                else:
                    model = self._model_name(node)
                    operation = 'read' if method in SCRIPT_READ_METHODS else method
                    self._record_capability(
                        model, operation, self._field_names(node, method)
                    )
            elif node.func.attr not in SCRIPT_SAFE_VALUE_METHODS:
                raise AccessError(_('This method call is not allowed in generated scripts.'))
        else:
            raise AccessError(_('Dynamic function calls are not allowed in generated scripts.'))
        self.generic_visit(node)

    def manifest(self):
        if not self.has_result:
            raise ValidationError(_('Generated scripts must assign their output to `result`.'))
        if not self.capabilities:
            raise ValidationError(
                _('Generated scripts must use at least one ctx Odoo operation.')
            )
        models_manifest = []
        for model in sorted(self.capabilities):
            entry = self.capabilities[model]
            models_manifest.append(
                {
                    'model': model,
                    'operations': sorted(entry['operations']),
                    'read_fields': sorted(entry['read_fields']),
                    'write_fields': sorted(entry['write_fields']),
                }
            )
        return {
            'models': models_manifest,
            'uses_ai': self.uses_ai,
            'ai_instructions': self.ai_instructions,
        }


class _GeneratedScriptContext:
    """Only object through which stored scripts may reach Odoo or an LLM."""

    def __init__(self, tool, user, session=None, manifest=None):
        self._tool = tool
        self._user = user
        self._session = session
        self._env = api.Environment(tool.env.cr, user.id, dict(tool.env.context))
        self._manifest = {
            item['model']: item
            for item in (manifest or tool.capability_manifest or {}).get('models', [])
        }
        self._uses_ai = bool((manifest or tool.capability_manifest or {}).get('uses_ai'))
        self._records_touched = set()
        self._new_records_touched = 0
        self._ai_calls = 0

    def _entry(self, model, operation):
        entry = self._manifest.get(model)
        if not entry or operation not in (entry.get('operations') or []):
            raise AccessError(
                _(
                    'The script has no authorized %(operation)s capability on %(model)s.',
                    operation=operation,
                    model=model,
                )
            )
        target = self._env[model]
        target.check_access_rights(operation, raise_exception=True)
        return target, entry

    def _fields(self, target, entry, operation, field_names):
        if not isinstance(field_names, (list, tuple)):
            raise ValidationError(_('fields must be a list.'))
        field_names = list(dict.fromkeys(field_names))
        allowed_key = 'read_fields' if operation == 'read' else 'write_fields'
        if set(field_names) - set(entry.get(allowed_key) or []):
            raise AccessError(_('The script attempted to use undeclared fields.'))
        unknown = set(field_names) - set(target._fields)
        if unknown:
            raise ValidationError(
                _('Unknown fields: %(fields)s.', fields=', '.join(sorted(unknown)))
            )
        if field_names:
            target.check_field_access_rights(
                'read' if operation == 'read' else 'write', field_names
            )
        return field_names

    def _domain(self, target, domain):
        domain = [] if domain is None else domain
        field_names = list(self._tool._domain_leaves(domain))
        unknown = set(field_names) - set(target._fields)
        if unknown:
            raise ValidationError(
                _('Unknown domain fields: %(fields)s.', fields=', '.join(sorted(unknown)))
            )
        if field_names:
            target.check_field_access_rights('read', field_names)
        target._where_calc(domain)
        return domain

    def _ids(self, ids):
        if not isinstance(ids, (list, tuple)) or any(
            isinstance(value, bool) or not isinstance(value, int) for value in ids
        ):
            raise ValidationError(_('ids must be a list of integer record IDs.'))
        return list(dict.fromkeys(ids))

    def _consume_records(self, model, ids):
        self._records_touched.update((model, rid) for rid in ids)
        if len(self._records_touched) + self._new_records_touched > self._tool.max_records:
            raise UserError(_('Generated tool record limit exceeded.'))

    def _consume_new_record(self):
        self._new_records_touched += 1
        if len(self._records_touched) + self._new_records_touched > self._tool.max_records:
            raise UserError(_('Generated tool record limit exceeded.'))

    def search(self, model, domain=None, fields=None, limit=100):
        target, entry = self._entry(model, 'read')
        domain = self._domain(target, domain)
        field_names = self._fields(target, entry, 'read', fields or [])
        limit = int(limit or self._tool.max_records)
        if limit < 1 or limit > self._tool.max_records:
            raise ValidationError(_('Search limit is outside the generated tool limit.'))
        records = target.search(domain, limit=limit)
        self._consume_records(model, records.ids)
        return records.read(field_names) if field_names else [{'id': rid} for rid in records.ids]

    def read(self, model, ids, fields):
        target, entry = self._entry(model, 'read')
        field_names = self._fields(target, entry, 'read', fields)
        ids = self._ids(ids)
        self._consume_records(model, ids)
        records = target.browse(ids).exists()
        records.check_access_rule('read')
        return records.read(field_names) if field_names else [{'id': rid} for rid in records.ids]

    def search_count(self, model, domain=None):
        target, _entry = self._entry(model, 'read')
        return target.search_count(self._domain(target, domain))

    def create(self, model, values):
        target, entry = self._entry(model, 'create')
        if not isinstance(values, dict):
            raise ValidationError(_('create values must be an object.'))
        self._fields(target, entry, 'create', list(values))
        self._consume_new_record()
        return target.create(values).id

    def write(self, model, ids, values):
        target, entry = self._entry(model, 'write')
        if not isinstance(values, dict):
            raise ValidationError(_('write values must be an object.'))
        self._fields(target, entry, 'write', list(values))
        ids = self._ids(ids)
        self._consume_records(model, ids)
        records = target.browse(ids).exists()
        records.check_access_rule('write')
        return records.write(values)

    def unlink(self, model, ids):
        target, _entry = self._entry(model, 'unlink')
        ids = self._ids(ids)
        self._consume_records(model, ids)
        records = target.browse(ids).exists()
        records.check_access_rule('unlink')
        return records.unlink()

    def generate_text(self, prompt, data=None, system=None):
        if not self._uses_ai:
            raise AccessError(_('This script did not declare AI text generation.'))
        self._ai_calls += 1
        if self._ai_calls > self._tool.max_ai_calls:
            raise UserError(_('Generated tool AI-call limit exceeded.'))
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 20_000:
            raise ValidationError(
                _('generate_text requires a non-empty bounded prompt.')
            )
        agent = self._session.agent_id if self._session else self._tool.agent_id
        if not agent or not agent.model_id or not agent.model_id.provider_id:
            raise UserError(_('Generated tool has no AI model configured.'))
        provider = agent.model_id.provider_id.with_user(self._user)
        data_text = json.dumps(data or {}, ensure_ascii=False, default=str)
        if len(data_text) > 50_000:
            raise UserError(_('generate_text data is too large.'))
        inputs = [
            {
                'role': 'system',
                'content': [
                    {
                        'type': 'input_text',
                        'text': system
                        or 'Return only the requested text. Do not call tools or invent database facts.',
                    }
                ],
            },
            {
                'role': 'user',
                'content': [
                    {
                        'type': 'input_text',
                        'text': f'{prompt.strip()}\n\nINPUT DATA:\n{data_text}',
                    }
                ],
            },
        ]
        payload = provider._request_responses(
            inputs=inputs,
            tools_schema=[],
            model=agent.model_id.technical_name,
            cache_key=f'muk_ai.generated.tool:{self._tool.id}',
        )
        text = (payload or {}).get('text') or ''
        if not text.strip():
            raise UserError(_('AI returned no text for generated tool execution.'))
        return text.strip()


class AIGeneratedTool(models.Model):
    """A user-owned restricted Python script generated by an AI agent."""

    _name = 'muk_ai.generated.tool'
    _description = 'AI Generated Script Tool'
    _order = 'create_date desc, id desc'

    name = fields.Char(required=True, readonly=True, index=True)
    description = fields.Text(required=True)
    owner_id = fields.Many2one(
        'res.users', required=True, readonly=True, index=True, ondelete='cascade'
    )
    skill_id = fields.Many2one(
        'muk_ai.skill', required=True, readonly=True, index=True, ondelete='restrict'
    )
    agent_id = fields.Many2one(
        'muk_ai.agent', readonly=True, index=True, ondelete='set null'
    )
    source_session_id = fields.Many2one(
        'muk_ai.session', readonly=True, index=True, ondelete='set null'
    )
    source_turn_seq = fields.Integer(readonly=True, copy=False)
    model_id = fields.Many2one(
        'ir.model', string='Primary Odoo Model', required=True, readonly=True,
        index=True, ondelete='cascade',
    )
    model_name = fields.Char(
        related='model_id.model', string='Primary Technical Model', store=True,
        readonly=True,
    )
    operation = fields.Selection(
        [('script', 'Restricted Python Script'), ('search_count', 'Legacy Count Records')],
        required=True, readonly=True, default='script',
    )
    domain = fields.Json(readonly=True, default=list, help='Legacy count-tool domain.')
    source_code = fields.Text(
        readonly=False,
        help='Restricted Python source. Empty only for legacy count tools.',
    )
    input_schema = fields.Json(readonly=True, default=dict)
    default_arguments = fields.Json(readonly=True, default=dict)
    capability_manifest = fields.Json(readonly=True, default=dict)
    task_contract = fields.Json(readonly=True, default=dict)
    original_request = fields.Text(readonly=True)
    active_revision_number = fields.Integer(readonly=True, default=0)
    remaining_revisions = fields.Integer(compute='_compute_remaining_revisions')
    max_records = fields.Integer(required=True, readonly=True, default=200)
    max_ai_calls = fields.Integer(required=True, readonly=True, default=25)
    max_runtime_seconds = fields.Integer(required=True, readonly=True, default=60)
    state = fields.Selection(
        [
            ('draft', 'Draft'), ('failed', 'Needs Revision'),
            ('active', 'Active'), ('disabled', 'Disabled'),
        ],
        required=True, readonly=True, default='draft', index=True,
    )
    revision_count = fields.Integer(required=True, readonly=True, default=1)
    max_revisions = fields.Integer(required=True, readonly=True, default=3)
    last_error = fields.Text(readonly=True)
    last_error_kind = fields.Selection(
        [('validation', 'Validation'), ('access', 'Access'), ('runtime', 'Runtime'), ('semantic', 'Wrong Result')],
        readonly=True,
    )
    last_test_result = fields.Json(readonly=True)
    last_tested_at = fields.Datetime(readonly=True)
    revision_ids = fields.One2many(
        'muk_ai.generated.tool.revision', 'tool_id', readonly=True
    )
    run_ids = fields.One2many('muk_ai.generated.tool.run', 'tool_id', readonly=True)

    @api.depends('revision_count', 'max_revisions', 'skill_id.tool_max_revisions')
    def _compute_remaining_revisions(self):
        for tool in self:
            tool.remaining_revisions = max(0, min(tool.max_revisions, tool.skill_id.tool_max_revisions) - tool.revision_count)

    @api.model
    def _validate_task_contract(self, contract, manifest=None):
        if not isinstance(contract, dict) or not all(
            isinstance(contract.get(key), str) and contract[key].strip()
            for key in ('summary', 'target', 'expected_result')
        ):
            raise ValidationError(_('Define the task summary, target and expected_result before writing code.'))
        entries = contract.get('models')
        if not isinstance(entries, list) or not entries or len(entries) > 10:
            raise ValidationError(_('Task contract must declare 1 to 10 models.'))
        names = set()
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get('model'), str):
                raise ValidationError(_('Invalid task model.'))
            if entry['model'] in names:
                raise ValidationError(_('Duplicate task model.'))
            names.add(entry['model'])
            for key in ('operations', 'read_fields', 'write_fields'):
                if not isinstance(entry.get(key), list) or any(not isinstance(value, str) for value in entry[key]):
                    raise ValidationError(_('Task model must declare operations, read_fields and write_fields lists.'))
            if not entry['operations'] or set(entry['operations']) - {'read', 'create', 'write', 'unlink'}:
                raise ValidationError(_('Invalid task operations.'))
        if not isinstance(contract.get('uses_ai'), bool):
            raise ValidationError(_('Task contract must explicitly declare uses_ai.'))
        if contract.get('contract_version') == 2:
            if not isinstance(contract.get('output_language'), str) or not contract['output_language'].strip():
                raise ValidationError(_('Task contract must declare the generated-content language.'))
            requirements = contract.get('content_requirements')
            if not isinstance(requirements, list) or any(
                not isinstance(value, str) or not value.strip() for value in requirements
            ):
                raise ValidationError(_('Task content_requirements must be a list of non-empty strings.'))
            if contract['uses_ai'] and not requirements:
                raise ValidationError(_('AI-generated content requires explicit content requirements.'))
        if contract['uses_ai']:
            for entry in entries:
                # Existing records must supply facts that ground generated updates.
                # A pure create has no target record to read before it exists; its
                # values are instead constrained by inputs/content requirements.
                if 'write' not in entry['operations']:
                    continue
                grounding_fields = (
                    set(entry['read_fields']) - set(entry['write_fields']) - {'id'}
                )
                if not grounding_fields:
                    raise ValidationError(_(
                        'AI-generated writes require at least one declared read field '
                        'that grounds the generated content (not only id or the target field).'
                    ))
        if manifest:
            actual = {entry['model']: entry for entry in manifest['models']}
            if names != set(actual) or contract['uses_ai'] != manifest['uses_ai']:
                raise ValidationError(_('Script models/AI usage do not match the declared task.'))
            for entry in entries:
                for key in ('operations', 'read_fields', 'write_fields'):
                    if not set(actual[entry['model']][key]).issubset(set(entry[key])):
                        raise ValidationError(_('Script %(key)s on %(model)s differ from the task contract.', key=key, model=entry['model']))
        self._json_safe(contract)
        return contract

    def _definition_fingerprint(self):
        self.ensure_one()
        source, manifest = self._runtime_definition()
        payload = [source, manifest, self.task_contract, self.input_schema,
                   self.active_revision_number, self.max_records, self.max_ai_calls,
                   self.max_runtime_seconds, self.skill_id.id, self.agent_id.id]
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

    def _lock_definition(self):
        self.ensure_one()
        self.env.cr.execute('SELECT id FROM muk_ai_generated_tool WHERE id = %s FOR UPDATE', [self.id])
        self.invalidate_recordset()

    _sql_constraints = [
        (
            'generated_tool_owner_name_unique', 'unique(owner_id, name)',
            'You already have a generated tool with this name.',
        ),
    ]

    @api.constrains('name')
    def _check_name(self):
        for record in self:
            if not TOOL_NAME_RE.match(record.name or ''):
                raise ValidationError(
                    _('Tool names must match [a-z][a-z0-9_]{2,63}.')
                )

    @api.constrains('operation', 'source_code')
    def _check_script_source_code(self):
        for record in self:
            if record.operation == 'script' and not (record.source_code or '').strip():
                raise ValidationError(_('A generated script tool requires Source Code.'))

    @api.constrains('max_records', 'max_ai_calls', 'max_runtime_seconds')
    def _check_runner_limits(self):
        for record in self:
            if not 1 <= record.max_records <= 1000:
                raise ValidationError(_('Maximum records must be between 1 and 1000.'))
            if not 0 <= record.max_ai_calls <= 200:
                raise ValidationError(_('Maximum AI calls must be between 0 and 200.'))
            if not 1 <= record.max_runtime_seconds <= 300:
                raise ValidationError(_('Maximum runtime must be between 1 and 300 seconds.'))

    @api.model
    def _normalize_source_code(self, source_code):
        source = (source_code or '').strip()
        if source.startswith('```') and source.endswith('```'):
            lines = source.splitlines()
            source = '\n'.join(lines[1:-1]).strip()
        if not source or len(source) > 30_000:
            raise ValidationError(
                _('Generated script must contain 1 to 30000 characters.')
            )
        return source

    @api.model
    def _analyze_source_code(self, source_code):
        source = self._normalize_source_code(source_code)
        try:
            tree = ast.parse(source, mode='exec')
        except SyntaxError as error:
            raise ValidationError(
                _(
                    'Generated script syntax error on line %(line)s: %(error)s.',
                    line=error.lineno,
                    error=error.msg,
                )
            ) from error
        analyzer = _GeneratedScriptAnalyzer(source)
        analyzer.visit(tree)
        return source, analyzer.manifest()

    @api.model
    def _check_script_access(self, user, skill, manifest, session=None):
        user_env = api.Environment(self.env.cr, user.id, dict(self.env.context))
        model_records = {}
        for capability in manifest.get('models', []):
            model_name = capability['model']
            for operation in capability.get('operations', []):
                model_record = skill._check_tool_builder_policy(
                    user=user, session=session, model_name=model_name,
                    operation=operation,
                )
                model_records[model_name] = model_record
            target = user_env[model_name]
            read_fields = capability.get('read_fields') or []
            write_fields = capability.get('write_fields') or []
            unknown = (set(read_fields) | set(write_fields)) - set(target._fields)
            if unknown:
                raise ValidationError(
                    _(
                        'Unknown fields on %(model)s: %(fields)s.',
                        model=model_name,
                        fields=', '.join(sorted(unknown)),
                    )
                )
            if read_fields:
                target.check_field_access_rights('read', read_fields)
            if write_fields:
                target.check_field_access_rights('write', write_fields)
        return model_records

    def _authorize(self, user, session=None, manifest=None):
        self.ensure_one()
        if not user or self.owner_id != user or not user.active:
            raise AccessError(
                _('This generated tool does not belong to the execution user.')
            )
        if session and (session.user_id != user or session._enforce_tool_scope() == 'read'):
            if session.user_id != user or any(
                set(item.get('operations', [])) - {'read'}
                for item in (manifest or self.capability_manifest or {}).get('models', [])
            ):
                raise AccessError(_('The session identity or readonly policy forbids this operation.'))
        if not session and self.agent_id.read_only and any(
            set(item.get('operations', [])) - {'read'}
            for item in (manifest or self.capability_manifest or {}).get('models', [])
        ):
            raise AccessError(_('This agent only permits read operations.'))
        if session and self.agent_id and self.agent_id != session.agent_id:
            raise AccessError(_('This generated tool belongs to another agent.'))
        if (
            not session
            and self.agent_id
            and self.skill_id.agent_ids
            and self.agent_id not in self.skill_id.agent_ids
        ):
            raise AccessError(_('This generated tool skill is no longer available to its agent.'))
        self._check_script_access(
            user, self.skill_id, manifest or self.capability_manifest or {}, session
        )
        return user

    def _runtime_definition(self):
        """Return validated source for both current and pre-script tools."""
        self.ensure_one()
        if (self.source_code or '').strip():
            return self._analyze_source_code(self.source_code)
        if self.operation != 'search_count':
            raise ValidationError(_('This generated tool has no Source Code.'))

        # Count tools created by the former declarative implementation did not
        # store Python. Translate their immutable model/domain declaration into
        # the same restricted runtime used by newly generated script tools.
        source = 'result = ctx.search_count(%r, %r)' % (
            self.model_name,
            self.domain or [],
        )
        return self._analyze_source_code(source)

    @api.model
    def _domain_leaves(self, domain):
        if not isinstance(domain, list):
            raise ValidationError(_('A generated tool domain must be a JSON list.'))
        for item in domain:
            if isinstance(item, str):
                if item not in ('&', '|', '!'):
                    raise ValidationError(
                        _('Invalid domain operator %(operator)r.', operator=item)
                    )
                continue
            if not isinstance(item, (list, tuple)) or len(item) != 3:
                raise ValidationError(
                    _('Each domain condition must contain field, operator and value.')
                )
            field_name, operator, _value = item
            if not isinstance(field_name, str) or not field_name:
                raise ValidationError(_('A domain field must be a technical field name.'))
            if '.' in field_name or field_name.startswith('_'):
                raise AccessError(_('Related or private field traversal is not allowed.'))
            if operator not in DOMAIN_OPERATORS:
                raise ValidationError(
                    _('Domain operator %(operator)r is not supported.', operator=operator)
                )
            yield field_name

    @api.model
    def _validate_json_schema(self, schema):
        schema = schema or {
            'type': 'object', 'properties': {}, 'additionalProperties': False,
        }
        if not isinstance(schema, dict) or schema.get('type') != 'object':
            raise ValidationError(
                _('Generated tool input_schema must describe an object.')
            )
        if schema.get('additionalProperties', False):
            raise ValidationError(_('Top-level inputs must be explicitly declared for the Run form.'))
        self._json_safe(schema)
        return validate_schema(schema)

    def _validate_arguments(self, arguments):
        self.ensure_one()
        arguments = {} if arguments is None else arguments
        if not isinstance(arguments, dict):
            raise ValidationError(_('Generated tool arguments must be an object.'))
        schema = self._validate_json_schema(self.input_schema)
        self._json_safe(arguments)
        return validate_value(arguments, schema)

    @contextmanager
    def _execution_limits(self):
        """Bound individual SQL waits; child has independent CPU/RAM/wall limits.

        Provider and custom ORM hooks still execute in Odoo. These are not hard
        isolated by the Python worker and must have their own network timeouts.
        """
        self.ensure_one()
        slot_acquired = False
        # Two concurrent generated-tool executions per database. Transaction
        # advisory locks release automatically on commit/rollback/crash.
        for slot in (0, 1):
            self.env.cr.execute('SELECT pg_try_advisory_xact_lock(%s, %s)', [724192, slot])
            if self.env.cr.fetchone()[0]:
                slot_acquired = True
                break
        if not slot_acquired:
            raise UserError(_('Both generated-tool execution slots are busy. Try again later.'))
        self.env.cr.execute("SELECT current_setting('statement_timeout'), setting::bigint FROM pg_settings WHERE name = 'statement_timeout'")
        previous, configured_ms = self.env.cr.fetchone()
        bounded_ms = min(configured_ms or self.max_runtime_seconds * 1000, self.max_runtime_seconds * 1000)
        self.env.cr.execute("SELECT set_config('statement_timeout', %s, true)", [str(bounded_ms)])
        try:
            with self.env.cr.savepoint():
                yield
        finally:
            self.env.cr.execute("SELECT set_config('statement_timeout', %s, true)", [previous])

    @api.model
    def _json_safe(self, value):
        encoded = json.dumps(value, ensure_ascii=False, default=str, allow_nan=False)
        if len(encoded) > 200_000:
            raise UserError(_('Generated tool output is too large.'))
        return json.loads(encoded)

    def _execute_script(self, user, arguments=None, session=None, context=None):
        self.ensure_one()
        source_code, manifest = self._runtime_definition()
        self._authorize(user, session, manifest=manifest)
        if self.task_contract:
            self._validate_task_contract(self.task_contract, manifest)
        arguments = self._validate_arguments(arguments)
        if context is None:
            context = _GeneratedScriptContext(self, user, session=session, manifest=manifest)
        with self._execution_limits():
            result = run_isolated(source_code, arguments, context, self.max_runtime_seconds, self.max_records)
        return self._json_safe(result)

    def _save_test_success(self, result):
        self.ensure_one()
        revision = (self.active_revision_number or self.revision_count) if self.state == 'active' else self.revision_count
        self.sudo().write(
            {
                'state': 'active', 'last_error': False,
                'last_error_kind': False, 'last_test_result': result,
                'last_tested_at': fields.Datetime.now(),
                'active_revision_number': revision,
            }
        )
        current = self.revision_ids.filtered(
            lambda rev: rev.revision == revision
        )
        (self.revision_ids - current).filtered(
            lambda rev: rev.status == 'active'
        ).sudo().write({'status': 'superseded'})
        current.sudo().write(
            {'status': 'active', 'error_message': False, 'test_result': result}
        )
        return {
            'status': 'active', 'generated_tool_id': self.id,
            'tool_name': self.name, 'revision': revision,
            'test_result': result, 'retryable': False,
        }

    def _save_test_failure(self, error):
        self.ensure_one()
        if isinstance(error, AccessError):
            kind, retryable, state = 'access', False, 'disabled'
        elif isinstance(
            error, (ValidationError, UserError, ValueError, TypeError, SyntaxError)
        ):
            kind, retryable, state = 'validation', True, 'failed'
        else:
            kind, retryable, state = 'runtime', True, 'failed'
        remaining = max(
            0,
            min(self.max_revisions, self.skill_id.tool_max_revisions)
            - self.revision_count,
        )
        if not remaining:
            retryable = False
        message = str(error)[:4000]
        self.sudo().write(
            {
                'state': state, 'last_error': message,
                'last_error_kind': kind, 'last_test_result': False,
                'last_tested_at': fields.Datetime.now(),
            }
        )
        self.revision_ids.filtered(
            lambda rev: rev.revision == self.revision_count
        ).sudo().write(
            {'status': 'failed', 'error_message': message, 'test_result': False}
        )
        return {
            'status': state, 'generated_tool_id': self.id,
            'tool_name': self.name, 'revision': self.revision_count,
            'error_kind': kind, 'error': message, 'retryable': retryable,
            'remaining_revisions': remaining,
        }

    def _test_for_user(self, user, session=None):
        self.ensure_one()
        # Authoring validation NEVER calls business mutations or an LLM. This
        # avoids side effects hidden in ORM overrides during rollback dry runs.
        try:
            _source, manifest = self._runtime_definition()
            self._authorize(user, session, manifest=manifest)
            if self.task_contract:
                self._validate_task_contract(self.task_contract, manifest)
            self._validate_json_schema(self.input_schema)
            return self._save_test_success({
                'validation_only': True, 'business_data_executed': False,
                'semantic_correctness_verified': False,
                'next_step': 'Open Run, review the task, enter inputs, Preview, then Confirm.',
            })
        except Exception as error:  # noqa: BLE001 - return classified repair feedback
            return self._save_test_failure(error)

    def _test_for_session(self, session):
        return self._test_for_user(session.user_id, session=session)

    def action_run(self):
        self.ensure_one()
        self._authorize(self.env.user)
        return self.env['muk_ai.generated.tool.execution']._open_for_tool(self)


class AIGeneratedToolRevision(models.Model):
    """Immutable audit snapshot for every AI-authored script version."""

    _name = 'muk_ai.generated.tool.revision'
    _description = 'AI Generated Tool Revision'
    _order = 'tool_id, revision desc'

    tool_id = fields.Many2one(
        'muk_ai.generated.tool', required=True, readonly=True, index=True,
        ondelete='cascade',
    )
    owner_id = fields.Many2one(related='tool_id.owner_id', store=True, readonly=True)
    revision = fields.Integer(required=True, readonly=True)
    description = fields.Text(required=True, readonly=True)
    source_code = fields.Text(
        readonly=True,
        help='Restricted Python source. Empty only on legacy revision snapshots.',
    )
    input_schema = fields.Json(readonly=True, default=dict)
    default_arguments = fields.Json(readonly=True, default=dict)
    capability_manifest = fields.Json(readonly=True, default=dict)
    task_contract = fields.Json(readonly=True, default=dict)
    original_request = fields.Text(readonly=True)
    domain = fields.Json(readonly=True, help='Legacy count-tool definition.')
    source_session_id = fields.Many2one(
        'muk_ai.session', readonly=True, index=True, ondelete='set null'
    )
    source_turn_seq = fields.Integer(readonly=True, copy=False)
    status = fields.Selection(
        [
            ('draft', 'Draft'), ('active', 'Active'), ('failed', 'Failed'),
            ('superseded', 'Superseded'),
        ],
        required=True, readonly=True, default='draft',
    )
    error_message = fields.Text(readonly=True)
    test_result = fields.Json(readonly=True)
    authored_by = fields.Many2one('res.users', required=True, readonly=True)

    _sql_constraints = [
        (
            'generated_tool_revision_unique', 'unique(tool_id, revision)',
            'A generated tool revision number must be unique.',
        ),
    ]


class AIGeneratedToolRun(models.Model):
    """Audit every stored-script execution, including denied and failed calls."""

    _name = 'muk_ai.generated.tool.run'
    _description = 'AI Generated Tool Run'
    _order = 'create_date desc, id desc'

    tool_id = fields.Many2one(
        'muk_ai.generated.tool', required=True, readonly=True, index=True,
        ondelete='cascade',
    )
    user_id = fields.Many2one('res.users', required=True, readonly=True, index=True)
    session_id = fields.Many2one(
        'muk_ai.session', readonly=True, index=True, ondelete='set null'
    )
    revision = fields.Integer(readonly=True)
    input_data = fields.Json(readonly=True)
    status = fields.Selection(
        [('preview', 'Preview'), ('running', 'Running'), ('success', 'Success'), ('failed', 'Failed'), ('denied', 'Denied')],
        required=True, readonly=True,
    )
    result = fields.Json(readonly=True)
    error_message = fields.Text(readonly=True)
    duration_ms = fields.Integer(readonly=True)
    user_feedback = fields.Text(readonly=True)
