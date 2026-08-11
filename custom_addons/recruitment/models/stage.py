from odoo import models, fields


class RecruitmentStage(models.Model):
    _name = "recruitment.stage"
    _description = "Recruitment Stage"
    _order = "sequence, id"

    name = fields.Char(
        string="Stage Name",
        required=True,
    )

    sequence = fields.Integer(
        string="Sequence",
        default=10,
    )

    fold = fields.Boolean(
        string="Folded in Pipeline",
        default=False,
    )

    active = fields.Boolean(
        string="Active",
        default=True,
    )

    description = fields.Text(
        string="Description",
    )