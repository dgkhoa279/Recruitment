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


    # Engine 2: AI CV Scanner Settings
    ai_cv_provider = fields.Selection(
        [
            ("gemini", "Google Gemini AI (Vision Multimodal)"),
            ("openai", "OpenAI (GPT-4o Vision)"),
        ],
        string="AI Engine Provider",
        default="gemini",
        config_parameter="recruitment.ai_cv_provider",
        help="Chọn mô hình AI Engine để đọc & phân tích CV",
    )

    ai_cv_api_key = fields.Char(
        string="AI API Key",
        config_parameter="recruitment.ai_cv_api_key",
        help="Dán Google Gemini API Key hoặc OpenAI API Key tại đây",
    )

    ai_cv_model = fields.Char(
        string="AI Model Name",
        default="gemini-1.5-flash",
        config_parameter="recruitment.ai_cv_model",
        help="Tên mô hình AI (Mặc định: gemini-1.5-flash hoặc gpt-4o-mini)",
    )
