from odoo import models, fields


class PaymentProvider(models.Model):
    _inherit = "payment.provider"

    code = fields.Selection(
        selection_add=[
            ("vietqr", "VietQR"),
        ],
        ondelete={
            "vietqr": "set default",
        },
    )
