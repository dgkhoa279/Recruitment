from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError


POLICY_FIELDS = frozenset(
    {
        'allow_tool_creation',
        'tool_allowed_operations',
        'tool_max_revisions',
    }
)

OPERATION_ACCESS_RIGHTS = {
    'read': 'read',
    'create': 'create',
    'write': 'write',
    'unlink': 'unlink',
}


class AISkill(models.Model):
    """Add server-enforced generated-tool policy to an AI skill."""

    _inherit = 'muk_ai.skill'

    allow_tool_creation = fields.Boolean(
        string='Allow AI Tool Creation',
        help='Allow the AI to propose restricted stored-script tools under this policy.',
        default=False,
    )
    tool_allowed_operations = fields.Json(
        string='Allowed Tool Operations',
        default=lambda self: ['read', 'create', 'write'],
        help=(
            'Allow-listed CRUD capabilities. Models and fields are authorized '
            'dynamically from the session user Odoo access rights.'
        ),
    )
    tool_max_revisions = fields.Integer(
        string='Maximum AI Revisions',
        default=3,
        help='Maximum number of versions the AI may create for one generated tool.',
    )

    @api.model
    def _tool_policy_admin(self) -> bool:
        return self.env.is_superuser() or self.env.is_admin()

    def _check_tool_builder_policy(
        self,
        *,
        user: models.BaseModel,
        session: models.BaseModel,
        model_name: str,
        operation: str,
    ) -> models.BaseModel:
        """Authorize an operation from the session user's current Odoo ACLs."""
        self.ensure_one()
        if not user or not user.active or (session and session.user_id != user):
            raise AccessError(_('The session user identity is no longer valid.'))
        visible = (
            session._visible_skills().filtered(lambda skill: skill.id == self.id)
            if session
            else self.filtered(
                lambda skill: (
                    skill.owner_id == user
                    or not skill.user_ids
                    or user in skill.user_ids
                )
            )
        )
        if not visible or not self.active or not self.allow_tool_creation:
            raise AccessError(_('This skill does not allow AI tool creation.'))
        if operation not in (self.tool_allowed_operations or []):
            raise AccessError(
                _('Operation %(operation)r is outside this skill policy.', operation=operation)
            )
        access_right = OPERATION_ACCESS_RIGHTS.get(operation)
        if not access_right:
            raise AccessError(
                _('Operation %(operation)r has no access-right mapping.', operation=operation)
            )
        model_record = self.env['ir.model'].sudo().search(
            [('model', '=', model_name)], limit=1
        )
        if not model_record:
            raise ValidationError(_('Odoo model %(model)r does not exist.', model=model_name))

        user_env = api.Environment(self.env.cr, user.id, dict(self.env.context))
        target = user_env[model_name]
        if target._transient or model_name.startswith(('ir.', 'muk_ai.', 'muk_mcp.')):
            raise AccessError(_('Generated scripts cannot access system metadata/automation, the AI control plane or transient approval forms.'))
        target.check_access_rights(access_right, raise_exception=True)
        return model_record

    @api.constrains('tool_max_revisions')
    def _check_tool_max_revisions(self) -> None:
        for record in self:
            if not 1 <= record.tool_max_revisions <= 10:
                raise ValidationError(_('Maximum AI revisions must be between 1 and 10.'))

    @api.model_create_multi
    def create(self, vals_list):
        if not self._tool_policy_admin() and any(
            POLICY_FIELDS.intersection(vals) for vals in vals_list
        ):
            raise AccessError(_('Only administrators may configure tool creation policy.'))
        return super().create(vals_list)

    def write(self, vals):
        if not self._tool_policy_admin() and POLICY_FIELDS.intersection(vals):
            raise AccessError(_('Only administrators may configure tool creation policy.'))
        return super().write(vals)
