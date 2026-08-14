from odoo import models, fields


class RecruitmentRefuseReason(models.Model):
    _name = "recruitment.refuse.reason"
    _description = "Recruitment Refuse Reason"
    _order = "sequence, id"

    name = fields.Char(
        string="Refuse Reason",
        required=True,
        translate=True,
    )
    sequence = fields.Integer(
        string="Sequence",
        default=10,
    )
    template_id = fields.Many2one(
        "mail.template",
        string="Email Template",
        domain="[('model', '=', 'recruitment.application')]",
    )
    active = fields.Boolean(
        string="Active",
        default=True,
    )
