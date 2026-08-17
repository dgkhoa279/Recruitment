from odoo import models, fields


class PaymentProvider(models.Model):
    _inherit = "payment.provider"

    code = fields.Selection(
        selection_add=[
            ("vnpay", "VNPAY"),
            ("momo", "MoMo"),
            ("zalopay", "ZaloPay"),
            ("vietqr", "VietQR"),
        ],
        ondelete={
            "vnpay": "set default",
            "momo": "set default",
            "zalopay": "set default",
            "vietqr": "set default",
        },
    )

    # Credential fields for custom providers
    vnpay_tmn_code = fields.Char(string="VNPAY TMN Code", help="Mã Website (TMN Code) do VNPAY cấp")
    vnpay_hash_secret = fields.Char(string="VNPAY Hash Secret", help="Chuỗi secret key cấu hình VNPAY")

    momo_partner_code = fields.Char(string="MoMo Partner Code", help="Mã đối tác do MoMo cấp")
    momo_secret_key = fields.Char(string="MoMo Secret Key", help="Khóa bí mật do MoMo cấp")

    zalopay_app_id = fields.Char(string="ZaloPay App ID", help="Mã App ID do ZaloPay cấp")
    zalopay_key1 = fields.Char(string="ZaloPay Key 1", help="Khóa Key 1 do ZaloPay cấp")
