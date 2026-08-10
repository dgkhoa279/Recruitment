from odoo import models, fields, api
from odoo.exceptions import ValidationError


class JobPosition(models.Model):
    _name = "recruitment.job.position"
    _description = "Job Position"

    name = fields.Char(
        string="Job Position",
        required=True,
    )

    description = fields.Text(
        string="Description",
    )

    quantity = fields.Integer(
        string="Recruitment Quantity",
        default=1,
    )

    deadline = fields.Date(
        string="Deadline",
    )

    active = fields.Boolean(
        string="Active",
        default=True,
    )

    _sql_constraints = [
        ("unique_name", "unique(name)", "Tên vị trí này đã tồn tại !"),
    ]

    @api.constrains('quantity')
    def _check_quantity(self):
        for record in self:
            if record.quantity <= 0:
                raise ValidationError("Số lượng tuyển dụng phải lớn hơn 0 !")

    @api.constrains('deadline')
    def _check_deadline(self):
        for record in self:
            if record.deadline and record.deadline < fields.Date.today():
                raise ValidationError("Ngày hết hạn phải lớn hơn ngày hiện tại !")