from odoo import models, fields, api


class RecruitmentRefuseWizard(models.TransientModel):
    _name = "recruitment.refuse.wizard"
    _description = "Refuse Reason Wizard"

    application_ids = fields.Many2many(
        "recruitment.application",
        string="Applications",
        required=True,
    )
    refuse_reason_id = fields.Many2one(
        "recruitment.refuse.reason",
        string="Refuse Reason",
        required=True,
    )
    template_id = fields.Many2one(
        "mail.template",
        string="Email Template",
        domain="[('model', '=', 'recruitment.application')]",
    )
    send_email = fields.Boolean(
        string="Send Email",
        default=True,
    )

    @api.onchange('refuse_reason_id')
    def _onchange_refuse_reason_id(self):
        if self.refuse_reason_id and self.refuse_reason_id.template_id:
            self.template_id = self.refuse_reason_id.template_id

    def action_refuse_reason_apply(self):
        for application in self.application_ids:
            application.write({
                'refuse_reason_id': self.refuse_reason_id.id,
                'active': False,
            })
            if self.send_email and self.template_id:
                self.template_id.sudo().send_mail(
                    application.id,
                    force_send=True,
                )
        return {'type': 'ir.actions.act_window_close'}
