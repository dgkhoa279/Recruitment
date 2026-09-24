"""Read-only planning facade. No ORM mutation hooks run during Preview."""
import hashlib
import json
import time
from psycopg2 import sql

from odoo import _
from odoo.exceptions import AccessError, UserError, ValidationError

from .generated_tool import _GeneratedScriptContext


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, allow_nan=False).encode()).hexdigest()


class PreviewContext(_GeneratedScriptContext):
    def __init__(self, tool, user, session=None, allow_ai=False):
        _source, manifest = tool._runtime_definition()
        super().__init__(tool, user, session=session, manifest=manifest)
        self.allow_ai = allow_ai
        self.checks = []
        self.effects = []
        self.mutated_models = set()
        self.started = time.monotonic()

    def _remember_read(self, method, args, kwargs):
        if args[0] in self.mutated_models:
            raise ValidationError(_('Preview requires reading a model before changing it. Read all required rows first, then plan mutations.'))
        value = getattr(super(), method)(*args, **kwargs)
        # JSON-normalize dates/tuples before crossing the worker boundary.
        value = self._tool._json_safe(value)
        self.checks.append({'method': method, 'args': args, 'kwargs': kwargs, 'fingerprint': fingerprint(value)})
        return value

    def search(self, model, domain=None, fields=None, limit=100):
        return self._remember_read('search', [model], {'domain': domain, 'fields': fields, 'limit': limit})

    def read(self, model, ids, fields):
        return self._remember_read('read', [model, ids, fields], {})

    def search_count(self, model, domain=None):
        return self._remember_read('search_count', [model], {'domain': domain})

    def _plan(self, method, model, ids=None, values=None):
        target, entry = self._entry(model, method)
        values = values or {}
        if not isinstance(values, dict):
            raise ValidationError(_('Mutation values must be an object.'))
        self._fields(target, entry, method, list(values))
        # Relational commands can mutate other models invisibly to this manifest.
        if any(target._fields[key].type in ('one2many', 'many2many') for key in values):
            raise ValidationError(_('Preview does not support relational command writes. Use explicit operations on the related model.'))
        ids = [] if method == 'create' else self._ids(ids)
        if any(rid <= 0 for rid in ids):
            raise ValidationError(_('Chained mutations on new preview IDs are not supported. Use separate tools.'))
        fields_to_read = sorted(set(values) | ({'write_date'} if 'write_date' in target._fields else set()))
        before = []
        if method != 'create':
            target.check_access_rights('read')
            target.check_field_access_rights('read', fields_to_read)
            records = target.browse(ids).exists()
            if set(records.ids) != set(ids):
                raise ValidationError(_('Some target records no longer exist.'))
            records.check_access_rule(method)
            records.check_access_rule('read')
            before = self._tool._json_safe(records.read(fields_to_read) if fields_to_read else [{'id': rid} for rid in ids])
        else:
            self._consume_new_record()
        # Repeated mutations are difficult to preview honestly without invoking
        # real ORM logic. Reject them instead of showing stale before/after data.
        if method != 'create' and any(
            effect['model'] == model and set(effect['ids']) & set(ids) for effect in self.effects
        ):
            raise ValidationError(_('Combine changes to each record into one write; repeated mutations are not previewable.'))
        self.effects.append({'method': method, 'model': model, 'ids': ids, 'values': values,
                             'before': before, 'snapshot_fields': fields_to_read})
        self.mutated_models.add(model)
        if len(self.effects) > self._tool.max_records:
            raise UserError(_('Preview operation limit exceeded.'))
        return -len(self.effects) if method == 'create' else True

    def create(self, model, values):
        return self._plan('create', model, values=values)

    def write(self, model, ids, values):
        return self._plan('write', model, ids=ids, values=values)

    def unlink(self, model, ids):
        return self._plan('unlink', model, ids=ids)

    def generate_text(self, prompt, data=None, system=None):
        if not self.allow_ai:
            raise UserError(_('This tool needs AI drafts. Enable the explicit AI-call consent before Preview; provider calls consume resources and may send data externally.'))
        return super().generate_text(prompt, data=data, system=system)

    def build(self, result):
        required_writes = {
            (entry['model'], op) for entry in self._tool._runtime_definition()[1].get('models', [])
            for op in entry['operations'] if op != 'read'
        }
        actual_writes = {(item['model'], item['method']) for item in self.effects}
        return self._tool._json_safe({
            'checks': self.checks, 'effects': self.effects, 'preview_result': result,
            'ai_calls': self._ai_calls,
            'unobserved_operations': [list(item) for item in sorted(required_writes - actual_writes)],
            'semantic_correctness_verified': False,
        })


def apply_plan(tool, user, plan, session=None):
    """Apply only the reviewed plan. Never regenerate AI content on Confirm."""
    _source, manifest = tool._runtime_definition()
    tool._authorize(user, session, manifest=manifest)
    if tool.task_contract:
        tool._validate_task_contract(tool.task_contract, manifest)
    context = _GeneratedScriptContext(tool, user, session=session, manifest=manifest)
    # Lock existing mutation targets before revalidation. This prevents a
    # concurrent update from slipping between snapshot validation and write.
    targets = {}
    for effect in plan['effects']:
        if effect['method'] != 'create':
            targets.setdefault(effect['model'], set()).update(effect['ids'])
    for model, ids in sorted(targets.items()):
        target = context._env[model]
        target.check_access_rights('read')
        target.browse(sorted(ids)).check_access_rule('read')
        target.flush_model()
        tool.env.cr.execute(sql.SQL('SELECT id FROM {} WHERE id = ANY(%s) ORDER BY id FOR UPDATE').format(sql.Identifier(target._table)), [sorted(ids)])
        target.invalidate_model()
    # Revalidate ALL read results and target snapshots BEFORE the first mutation.
    for check in plan['checks']:
        if check['method'] not in ('search', 'read', 'search_count'):
            raise AccessError(_('Invalid preview read operation.'))
        result = getattr(context, check['method'])(*check['args'], **check['kwargs'])
        if fingerprint(tool._json_safe(result)) != check['fingerprint']:
            raise UserError(_('Data or permissions changed since Preview. Generate a new Preview.'))
    for effect in plan['effects']:
        target, _entry = context._entry(effect['model'], effect['method'])
        if effect['method'] != 'create':
            target.check_access_rights('read')
            target.check_field_access_rights('read', effect['snapshot_fields'])
            records = target.browse(effect['ids']).exists()
            records.check_access_rule('read')
            records.check_access_rule(effect['method'])
            before = records.read(effect['snapshot_fields']) if effect['snapshot_fields'] else [{'id': rid} for rid in records.ids]
            if fingerprint(tool._json_safe(before)) != fingerprint(effect['before']):
                raise UserError(_('Target records changed since Preview. Generate a new Preview.'))
    # The application limit applies independently from the preflight read count.
    context._records_touched = set()
    context._new_records_touched = 0
    created_ids = []
    counts = {'create': 0, 'write': 0, 'unlink': 0}
    started = time.monotonic()
    for effect in plan['effects']:
        if time.monotonic() - started > tool.max_runtime_seconds:
            raise UserError(_('Generated tool runtime limit exceeded.'))
        method, model = effect['method'], effect['model']
        if method == 'create':
            rid = context.create(model, effect['values'])
            created_ids.append({'model': model, 'id': rid})
            counts[method] += 1
        elif method == 'write':
            context.write(model, effect['ids'], effect['values'])
            counts[method] += len(effect['ids'])
        elif method == 'unlink':
            context.unlink(model, effect['ids'])
            counts[method] += len(effect['ids'])
        else:
            raise AccessError(_('Invalid preview mutation.'))
    return {'applied_records': counts, 'created_records': created_ids,
            'preview_result': plan['preview_result'], 'ai_calls_during_confirm': 0,
            'note': 'Preview results may contain negative placeholder IDs for planned creates. Actual IDs are in created_records.'}
