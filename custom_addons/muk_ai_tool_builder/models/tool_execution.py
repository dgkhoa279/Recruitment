import json
import time
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .execution_plan import PreviewContext, apply_plan, fingerprint
from .generated_tool import _GeneratedScriptContext
from .human_gate import AI_DISPATCH


class GeneratedToolExecution(models.TransientModel):
    _name = 'muk_ai.generated.tool.execution'
    _description = 'Review and Run Generated Tool'
    _transient_max_hours = 1

    tool_id = fields.Many2one('muk_ai.generated.tool', required=True, readonly=True)
    session_id = fields.Many2one('muk_ai.session', readonly=True)
    state = fields.Selection([('draft', 'Inputs'), ('preview', 'Review Preview'),
                              ('done', 'Completed'), ('failed', 'Failed')], default='draft', readonly=True)
    original_request = fields.Text(related='tool_id.original_request', readonly=True)
    task_text = fields.Text(readonly=True)
    line_ids = fields.One2many('muk_ai.generated.tool.argument', 'execution_id')
    allow_ai = fields.Boolean(string='Allow AI calls to generate preview drafts')
    task_reviewed = fields.Boolean(string='The task matches my request', default=True)
    changes_reviewed = fields.Boolean(string='I approve these exact changes and any business-hook side effects')
    preview_text = fields.Text(readonly=True)
    preview_data = fields.Json(readonly=True)
    preview_at = fields.Datetime(readonly=True)
    definition_hash = fields.Char(readonly=True)
    arguments_hash = fields.Char(readonly=True)
    scope_hash = fields.Char(readonly=True)
    result_text = fields.Text(readonly=True)
    feedback = fields.Text(string='What is wrong, and what result did you expect?')
    run_id = fields.Many2one('muk_ai.generated.tool.run', readonly=True)

    def _human_only(self):
        if AI_DISPATCH.get() or self.env.context.get('muk_mcp_session_id'):
            raise AccessError(_('Only the user can review or confirm this form; AI tool calls cannot approve it.'))

    def _check_owner(self):
        self.ensure_one()
        self._human_only()
        if self.create_uid != self.env.user or self.tool_id.owner_id != self.env.user:
            raise AccessError(_('This execution belongs to another user.'))

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(_('Open an execution using the tool Run button.'))
        return super().create(vals_list)

    def write(self, vals):
        if AI_DISPATCH.get() and set(vals) & {'task_reviewed', 'changes_reviewed', 'allow_ai'}:
            raise AccessError(_('AI tool calls cannot approve executions.'))
        if not self.env.su:
            self._human_only()
            if set(vals) - {'allow_ai', 'task_reviewed', 'changes_reviewed', 'feedback', 'line_ids'}:
                raise AccessError(_('Execution plans and results are server-managed.'))
            for record in self:
                record._check_owner()
                record._lock()
            if 'allow_ai' in vals or 'line_ids' in vals:
                self._invalidate_preview()
        return super().write(vals)

    def _invalidate_preview(self):
        for record in self:
            if record.state == 'done':
                raise UserError(_('This execution is completed. Open Run again for another execution.'))
        self.sudo().write({'state': 'draft', 'preview_data': False,
                           'preview_text': False, 'changes_reviewed': False})

    @api.model
    def _create_for_tool(self, tool, arguments=None, session=None):
        tool._authorize(self.env.user, session)
        schema = tool._validate_json_schema(tool.input_schema)
        arguments = (tool.default_arguments or {}) if arguments is None else arguments
        if not isinstance(arguments, dict) or set(arguments) - set(schema.get('properties', {})):
            raise ValidationError(_('Run inputs must match the declared named parameters.'))
        lines = []
        for name, spec in schema.get('properties', {}).items():
            value = arguments.get(name, spec.get('default'))
            kind = spec['type']
            vals = {'name': name, 'label': spec.get('title', name), 'kind': kind,
                    'help_text': json.dumps(spec, ensure_ascii=False, indent=2),
                    'required_input': name in schema.get('required', []),
                    'include_value': name in arguments or 'default' in spec or name in schema.get('required', [])}
            if kind == 'boolean':
                vals['boolean_value'] = bool(value)
            else:
                vals['text_value'] = value if kind == 'string' else json.dumps(value)
            lines.append((0, 0, vals))
        contract = tool.task_contract or {'warning': 'Legacy tool: no original task contract. Review source and capabilities before running.',
                                          'description': tool.description, 'capabilities': tool._runtime_definition()[1]}
        return self.sudo().create({'tool_id': tool.id, 'session_id': session.id if session else False,
                                   'task_text': json.dumps(contract, ensure_ascii=False, indent=2), 'line_ids': lines,
                                   'definition_hash': tool._definition_fingerprint()})

    @api.model
    def _open_for_tool(self, tool):
        return self._create_for_tool(tool)._reopen()

    def _reopen(self):
        return {'type': 'ir.actions.act_window', 'name': _('Review and Run'),
                'res_model': self._name, 'res_id': self.id, 'view_mode': 'form', 'target': 'new'}

    def _arguments(self):
        values = {}
        for line in self.line_ids:
            if not line.include_value:
                continue
            if line.kind == 'boolean':
                value = line.boolean_value
            elif line.kind == 'string':
                value = line.text_value or ''
            else:
                try:
                    value = json.loads(line.text_value or 'null')
                except ValueError as error:
                    raise ValidationError(_('Input %s must contain valid JSON (numbers, arrays or objects).') % line.name) from error
            values[line.name] = value
        return self.tool_id._validate_arguments(values)

    def _scope(self):
        return fingerprint([self.env.uid, sorted(self.env.companies.ids), self.env.lang])

    def _lock(self):
        # Serialize Preview/Confirm/input edits; a double click cannot apply twice.
        self.env.cr.execute('SELECT id FROM muk_ai_generated_tool_execution WHERE id = %s FOR UPDATE', [self.id])
        self.invalidate_recordset()

    def action_run_direct(self):
        """Compatibility entry point: generate a Preview, never write directly."""
        return self.action_preview()

    def action_preview(self):
        self._check_owner()
        self._lock()
        if self.state == 'done':
            raise UserError(_('Open Run again for a new execution.'))
        tool = self.tool_id
        tool._lock_definition()
        if tool.state != 'active':
            raise UserError(_('This tool is not active.'))
        if not self.task_reviewed:
            raise UserError(_('Review and confirm that the task matches your request before Preview.'))
        arguments = self._arguments()
        started = time.monotonic()
        run = self.env['muk_ai.generated.tool.run'].sudo().create({
            'tool_id': tool.id,
            'user_id': self.env.uid,
            'session_id': self.session_id.id,
            'revision': tool.active_revision_number or tool.revision_count,
            'input_data': {'parameter_names': sorted(arguments), 'sha256': fingerprint(arguments)},
            'status': 'preview',
        })
        self.sudo().write({'run_id': run.id})
        try:
            context = PreviewContext(
                tool, self.env.user, session=self.session_id,
                allow_ai=self.allow_ai,
            )
            result = tool._execute_script(self.env.user, arguments, session=self.session_id, context=context)
            plan = context.build(result)
        except Exception as error:
            kind = 'access' if isinstance(error, AccessError) else 'runtime'
            tool.sudo().write({'last_error_kind': kind, 'last_error': str(error)[:4000]})
            run.sudo().write({'status': 'denied' if kind == 'access' else 'failed', 'error_message': str(error)[:4000],
                              'duration_ms': int((time.monotonic() - started) * 1000)})
            self.sudo().write({'state': 'failed', 'result_text': str(error)[:4000]})
            return self._reopen()

        has_effects = bool(plan.get('effects'))
        run.sudo().write({
            'status': 'preview' if has_effects else 'success',
            'result': plan,
            'duration_ms': int((time.monotonic() - started) * 1000),
        })
        self.sudo().write({
            'state': 'preview' if has_effects else 'done',
            'preview_text': json.dumps(plan, ensure_ascii=False, indent=2),
            'preview_data': plan,
            'preview_at': fields.Datetime.now(),
            'definition_hash': tool._definition_fingerprint(),
            'arguments_hash': fingerprint(arguments),
            'scope_hash': self._scope(),
            'changes_reviewed': False,
            'result_text': (
                False if has_effects
                else json.dumps(result, ensure_ascii=False, indent=2)
            ),
        })
        return self._reopen()

    def action_confirm(self):
        """Apply exactly the reviewed Preview without invoking AI again."""
        self._check_owner()
        self._lock()
        if self.state != 'preview' or not isinstance(self.preview_data, dict):
            raise UserError(_('Generate a current Preview before confirming.'))
        if not self.changes_reviewed:
            raise UserError(_('Review and approve the exact Preview changes before Confirm.'))
        tool = self.tool_id
        tool._lock_definition()
        if tool.state != 'active':
            raise UserError(_('This tool is not active.'))
        arguments = self._arguments()
        if self.definition_hash != tool._definition_fingerprint():
            raise UserError(_('The tool changed after Preview. Generate a new Preview.'))
        if self.arguments_hash != fingerprint(arguments):
            raise UserError(_('The inputs changed after Preview. Generate a new Preview.'))
        if self.scope_hash != self._scope():
            raise UserError(_('The user or company scope changed after Preview. Generate a new Preview.'))
        if not self.preview_at or self.preview_at < fields.Datetime.now() - timedelta(minutes=15):
            raise UserError(_('The Preview expired. Generate a new Preview.'))

        started = time.monotonic()
        run = self.run_id
        run.sudo().write({'status': 'running', 'error_message': False})
        try:
            result = apply_plan(
                tool, self.env.user, self.preview_data, session=self.session_id
            )
        except Exception as error:
            kind = 'access' if isinstance(error, AccessError) else 'runtime'
            tool.sudo().write({'last_error_kind': kind, 'last_error': str(error)[:4000]})
            run.sudo().write({
                'status': 'denied' if kind == 'access' else 'failed',
                'error_message': str(error)[:4000],
                'duration_ms': int((time.monotonic() - started) * 1000),
            })
            self.sudo().write({'state': 'failed', 'result_text': str(error)[:4000]})
            return self._reopen()

        run.sudo().write({
            'status': 'success', 'result': result,
            'duration_ms': int((time.monotonic() - started) * 1000),
        })
        self.sudo().write({
            'state': 'done',
            'result_text': json.dumps(result, ensure_ascii=False, indent=2),
        })
        return self._reopen()



    def action_report_issue(self):
        self._check_owner()
        self._lock()
        if not (self.feedback or '').strip():
            raise UserError(_('Describe the wrong result and the expected result.'))
        self.tool_id.sudo().write({'last_error_kind': 'semantic', 'last_error': self.feedback[:4000]})
        if self.run_id:
            self.run_id.sudo().write({'user_feedback': self.feedback[:4000]})
        return self._reopen()


class GeneratedToolArgument(models.TransientModel):
    _name = 'muk_ai.generated.tool.argument'
    _description = 'Generated Tool Input'

    execution_id = fields.Many2one('muk_ai.generated.tool.execution', required=True, ondelete='cascade')
    name = fields.Char(readonly=True, required=True)
    label = fields.Char(readonly=True)
    kind = fields.Char(readonly=True)
    help_text = fields.Text(readonly=True)
    required_input = fields.Boolean(readonly=True)
    include_value = fields.Boolean(default=True)
    text_value = fields.Text()
    boolean_value = fields.Boolean()

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(_('Input definitions are server-managed.'))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            if set(vals) - {'include_value', 'text_value', 'boolean_value'}:
                raise AccessError(_('Input definitions are server-managed.'))
            for execution in self.execution_id:
                execution._check_owner()
                execution._lock()
                execution._invalidate_preview()
        return super().write(vals)
