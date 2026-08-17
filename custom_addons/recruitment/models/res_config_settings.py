from odoo import models, fields


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    facebook_page_id = fields.Char(
        string="Facebook Page ID",
        config_parameter="recruitment.facebook_page_id",
        help="ID của Facebook Fanpage công ty (ví dụ: 102938475610293)",
    )

    facebook_page_access_token = fields.Char(
        string="Facebook Page Access Token",
        config_parameter="recruitment.facebook_page_access_token",
        help="Long-lived Page Access Token được cấp từ Facebook Developer App",
    )
